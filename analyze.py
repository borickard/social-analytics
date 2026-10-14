#!/usr/bin/env python3
"""
Analys: kopplar innehåll till engagemang för IQ:s TikTok-inlägg.

Läser iq_tiktok_data/iq_tiktok_enriched.csv och skriver:
  - en terminalsammanfattning
  - en INTERAKTIV, fristående HTML-rapport (iq_tiktok_data/iq_analys.html):
      * segment-väljare Alla / Organiskt / Boostat (filtrerar allt)
      * sorterbara kolumner (median-ER, medel-ER, visningar, n)
      * klickbara kategorier som fälls ut och visar inläggen (thumbnail +
        caption + ER + visningar), varje inlägg länkar till TikTok
      * topp/botten separat för organiskt och boostat

ENGAGEMANG (så IQ mäter): viktad engagemangsgrad, visad som procent
    ER = (likes + kommentarer*5 + delningar*10 + favoriter*5) / visningar
Riktmärken: <0,5 % svagt · 0,5–2 % normalt · 2 %+ starkt. Exakta siffror
(*_exakt) används där de finns. Median är huvudmått (robust mot virala
extremvärden). Organiskt och boostat (is_ad) skiljer sig kraftigt och bör
jämföras var för sig – därav segment-väljaren.

Kör:  python analyze.py            (använder standard-CSV:n)
      python analyze.py fil.csv    (annan CSV, t.ex. för test)
"""

import csv
import datetime
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(PROJECT_DIR, "iq_tiktok_data", "iq_tiktok_enriched.csv")
CSV_PATH = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
OUT_HTML = os.path.join(os.path.dirname(CSV_PATH) or ".", "iq_analys.html")
# Manuella rättelser (video_id,field,value). Ligger separat från enriched.csv
# och läggs ÖVERST – så modellkörningar (reclassify m.fl.) aldrig skriver över
# dem. Skapas/uppdateras via "Ladda ner overrides.csv" i dashboarden.
OVERRIDES_PATH = os.path.join(os.path.dirname(CSV_PATH) or ".", "overrides.csv")
AVAIL_PATH = os.path.join(os.path.dirname(CSV_PATH) or ".", "availability.csv")

# Fält som går att ändra manuellt i dashboarden, med tillåtna värden. Nycklarna
# måste matcha kolumnnamnen i enriched.csv (och POSTS-fälten i JS).
EDITABLE = {
    "strategi": ["always on", "kampanj"],
    "typ": ["", "video", "bild"],
    "kategori": ["fakta", "humor", "POV", "frågor på stan", "quiz/lek",
                 "dramatiserat", "övrigt"],
    "format": ["filmat", "animerat", "skärmavbildning", "voiceover",
               "talking head", "sketch", "bildinlägg", "övrigt"],
    "budskapston": ["", "budskap", "lattsamt", "blandat"],
    "hogtid": ["", "Nyår", "Jul", "Midsommar", "Valborg", "Studenten",
               "Halloween", "Sommarlov", "Födelsedag", "Påsk", "Kräftskiva"],
    "har_hook": ["", "ja", "nej"],
}


# ----------------------------------------------------------------- inläsning ---
def num(x):
    if x is None:
        return None
    s = str(x).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pick(row, exact, scraped):
    return num(row.get(exact)) if num(row.get(exact)) is not None else num(row.get(scraped))


def weighted_er(row):
    views = pick(row, "visningar_exakt", "visningar")
    if not views:
        return None
    likes = pick(row, "likes_exakt", "likes") or 0
    comments = pick(row, "kommentarer_exakt", "kommentarer") or 0
    shares = pick(row, "delningar_exakt", "delningar") or 0
    saves = pick(row, "sparade_exakt", "sparade") or 0
    return (likes + comments * 5 + shares * 10 + saves * 5) / views


def is_organic(r):
    return str(r.get("is_ad", "")).strip().lower() not in ("true", "1", "ja")


def levels(vals):
    """Datadrivna nivågränser (kvartiler): (q1, median, q3). Under q1 = lågt,
    över q3 = högt, däremellan = medel. Robust för små n."""
    vals = [v for v in vals if v is not None]
    if not vals:
        return (0, 0, 0)
    if len(vals) < 4:
        m = statistics.median(vals)
        return (m, m, m)
    q1, q2, q3 = statistics.quantiles(vals, n=4)
    return (q1, q2, q3)


def pct(er):
    return f"{er * 100:.2f} %".replace(".", ",")


def parse_list(v):
    v = v or ""
    try:
        p = json.loads(v)
        return [str(x) for x in p] if isinstance(p, list) else []
    except Exception:
        return []


def load_overrides():
    """Läs overrides.csv → {video_id: {field: value}}. Tom om filen saknas."""
    ov = {}
    if os.path.exists(OVERRIDES_PATH):
        with open(OVERRIDES_PATH, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                vid = (r.get("video_id") or "").strip()
                fld = (r.get("field") or "").strip()
                if vid and fld in EDITABLE:
                    ov.setdefault(vid, {})[fld] = r.get("value", "")
    return ov


def load_availability():
    """Läs availability.csv → {video_id: status}. Skrivs av check_availability.py.
    Tom om filen saknas (då markeras inga inlägg som borttagna)."""
    av = {}
    if os.path.exists(AVAIL_PATH):
        with open(AVAIL_PATH, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                vid = (r.get("video_id") or "").strip()
                if vid:
                    av[vid] = (r.get("status") or "").strip()
    return av


def load(path, ov):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ana = []
    for r in rows:
        o = ov.get(r.get("video_id", ""))
        if o:                                  # lägg manuella rättelser överst
            for fld, val in o.items():
                r[fld] = val
        if not r.get("format"):
            continue
        er = weighted_er(r)
        if er is None:
            continue
        r["_er"] = er
        r["_views"] = pick(r, "visningar_exakt", "visningar")
        ana.append(r)
    return ana


def _pubdate(r):
    s = (r.get("publiceringsdatum", "") or "")[:10]
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return None


def attach_benchmarks(ana, half_window_days=45):
    """Per inlägg: jämför dess ER mot MEDIANEN för samma segment (organiskt vs
    boostat) i ett rullande fönster ±1,5 mån runt publiceringen. Sparar kvoten
    (ER/median) och periodens gränser för hover-texten i dashboarden."""
    pts = [(_pubdate(r), r["_er"], is_organic(r), r) for r in ana]
    delta = datetime.timedelta(days=half_window_days)
    for d, er, seg, r in pts:
        if d is None:
            r["_bench"] = None
            continue
        lo, hi = d - delta, d + delta
        vals = [e for (dd, e, s, _) in pts if s == seg and dd is not None and lo <= dd <= hi]
        med = statistics.median(vals) if vals else None
        r["_bench"] = (er / med) if med else None
        r["_bench_lo"] = lo.isoformat()
        r["_bench_hi"] = hi.isoformat()


# --------------------------------------------------------- server-side charts ---
def months(rows):
    m = defaultdict(list)
    for r in rows:
        d = (r.get("publiceringsdatum", "") or "")[:7]
        if len(d) == 7:
            m[d].append(r["_er"])
    return [(k, statistics.median(m[k]), len(m[k])) for k in sorted(m)]


def trend(series):
    pts = [(i, v) for i, (_, v, _) in enumerate(series)]
    n = len(pts)
    if n < 3:
        return 0.0, "för få månader för trend"
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    denom = sum((p[0] - mx) ** 2 for p in pts)
    if denom == 0:
        return 0.0, "stabilt →"
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / denom * 100
    return slope, ("uppåt ↗" if slope > 0.03 else "nedåt ↘" if slope < -0.03 else "stabilt →")


# ---------------------------------------------------------- data för JS-appen ---
def post_json(r):
    vid = r.get("video_id", "")
    return {
        "id": vid,
        "url": r.get("url", ""),
        "gone": bool(r.get("_gone")),          # borttagen/privat på TikTok
        "thumb": r.get("thumbnail", "") or f"thumbnails/{vid}.jpg",
        "caption": (r.get("caption", "") or "")[:180],
        # hela beskrivningen (gemener) för fritextsök, kapad för att hålla nere storleken
        "sok": (r.get("caption", "") or "").lower()[:1000],
        "typ": r.get("typ", "") or "?",
        "format": r.get("format", "") or "?",
        "kategori": r.get("kategori", "") or "okänt",
        "hook_typ": r.get("hook_typ", ""),
        "har_hook": r.get("har_hook", ""),
        "har_cta": r.get("har_cta", ""),
        "budskapston": r.get("budskapston", "") or "okänt",
        "teman": parse_list(r.get("budskap_teman", "")),
        "alk": r.get("alkohol_i_bild", "") or "?",
        "alkkontext": r.get("alkohol_kontext", "") or "ingen",
        "hogtid": r.get("hogtid", ""),
        "strategi": r.get("strategi", "") or "always on",
        "organic": is_organic(r),
        "er": round(r["_er"], 6),
        "views": int(r["_views"]),
        "likes": int(pick(r, "likes_exakt", "likes") or 0),
        "kommentarer": int(pick(r, "kommentarer_exakt", "kommentarer") or 0),
        "delningar": int(pick(r, "delningar_exakt", "delningar") or 0),
        "sparade": int(pick(r, "sparade_exakt", "sparade") or 0),
        "date": (r.get("publiceringsdatum", "") or "")[:10],
        "bench": round(r["_bench"], 4) if r.get("_bench") else None,
        "bench_lo": r.get("_bench_lo", ""),
        "bench_hi": r.get("_bench_hi", ""),
    }


APP_JS = r"""
const fmtPct = e => (e*100).toFixed(2).replace('.',',')+' %';
const fmtNum = n => Math.round(n).toLocaleString('sv-SE');
// Kortform för väldigt stora tal: 276 860 300 → "276,9 m". Exakt tal visas
// vid hover (title). Under en miljon visas talet oavkortat.
function compactNum(n){
  n=Math.round(n);const a=Math.abs(n);
  const dec=x=>x.toLocaleString('sv-SE',{maximumFractionDigits:1});
  if(a>=1e9)return dec(n/1e9)+' md';
  if(a>=1e6)return dec(n/1e6)+' m';
  return fmtNum(n);
}
const esc = s => (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const median = a => {if(!a.length)return 0;const s=[...a].sort((x,y)=>x-y);const m=s.length>>1;return s.length%2?s[m]:(s[m-1]+s[m])/2;};
const mean = a => a.length?a.reduce((x,y)=>x+y,0)/a.length:0;
const MIN_N = 3, MIN_VIEWS = 3000;
let segment = 'boost';   // boostat är default (störst volym)

// Datadrivna nivåer (kvartiler). Varje inläggs nivå bedöms mot SITT eget
// segment (organiskt mot organiskt, boostat mot boostat) eftersom de skiljer
// sig kraftigt. lågt < q1, högt > q3, annars medel.
function quantile(s,p){if(!s.length)return 0;const i=(s.length-1)*p,lo=Math.floor(i),hi=Math.ceil(i);return lo===hi?s[lo]:s[lo]+(s[hi]-s[lo])*(i-lo);}
function levelsOf(posts){const s=posts.map(p=>p.er).sort((a,b)=>a-b);return{q1:quantile(s,.25),med:quantile(s,.5),q3:quantile(s,.75)};}
const ORG_L=levelsOf(POSTS.filter(p=>p.organic)),BOOST_L=levelsOf(POSTS.filter(p=>!p.organic));
POSTS.forEach(p=>{const L=p.organic?ORG_L:BOOST_L;p.band=p.er>L.q3?'hög':(p.er<L.q1?'låg':'medel');});
const BY_ID={};POSTS.forEach(p=>BY_ID[p.id]=p);   // snabb uppslagning per video_id

// Tidsfilter: periodFrom/periodTo (ISO 'YYYY-MM-DD' eller null). Allt nedanför
// (översikt, graf, dimensioner, topplistor, sök) filtreras på publiceringsdatum.
let periodFrom=null, periodTo=null;
function inPeriod(p){const d=p.date||'';
  if(periodFrom&&(!d||d<periodFrom))return false;
  if(periodTo&&(!d||d>periodTo))return false;return true;}
function timePosts(){return POSTS.filter(inPeriod);}                 // bara tidsfilter
function segPosts(){return timePosts().filter(p=>segment==='alla'?true:segment==='org'?p.organic:!p.organic);}
function hookKey(p){if(p.har_hook)return p.har_hook==='ja'?'hook':'ingen hook';
  if(!p.hook_typ)return '';return p.hook_typ==='ovrigt'?'ingen/oklar hook':'hook: '+p.hook_typ;}

const DIMS = [
  {id:'strategi',label:'Strategi (kampanj / always on)',key:p=>[p.strategi],field:'strategi'},
  {id:'kategori',label:'Kategori',key:p=>[p.kategori],field:'kategori'},
  {id:'format',label:'Format',key:p=>[p.format],field:'format'},
  {id:'typ',label:'Typ (video/bild)',key:p=>[p.typ],field:'typ'},
  {id:'hook',label:'Hook',key:p=>[hookKey(p)],field:'har_hook'},
  {id:'cta',label:'Call to action',key:p=>[p.har_cta==='ja'?'CTA':'ingen CTA']},
  {id:'alk',label:'Alkohol i bild',key:p=>['alkohol: '+p.alk]},
  {id:'alkkontext',label:'Alkohol-kontext',key:p=>[p.alkkontext]},
];
if(window.HAS_TONE){
  DIMS.splice(5,0,{id:'ton',label:'Budskapston (budskap vs lättsamt)',key:p=>[p.budskapston],field:'budskapston'},
                 {id:'teman',label:'Budskap-teman',key:p=>p.teman});
}
if(window.HAS_OCCASION){
  DIMS.push({id:'hogtid',label:'Högtid / tillfälle',key:p=>p.hogtid?[p.hogtid]:[],field:'hogtid'});
}

const sortState = {};   // dimId -> {col, dir}
const openState = {};   // dimId -> Set av öppna grupper
const SEL = new Set();  // markerade inläggs-id för bulkändring
const EXP = new Set();  // inlägg med utfälld ("läs mer") beskrivning

function groups(posts, keyfn){
  const g={};
  posts.forEach(p=>keyfn(p).forEach(k=>{if(k){(g[k]=g[k]||[]).push(p);}}));
  return Object.entries(g).map(([k,ps])=>({k,n:ps.length,
      med:median(ps.map(p=>p.er)),avg:mean(ps.map(p=>p.er)),
      views:median(ps.map(p=>p.views)),
      delningar:median(ps.map(p=>p.delningar)),
      kommentarer:median(ps.map(p=>p.kommentarer)),
      likes:median(ps.map(p=>p.likes)),
      sparade:median(ps.map(p=>p.sparade)),
      posts:ps})).filter(x=>x.n>=MIN_N);
}

const COLS=[{k:'k',t:'Grupp',num:false,fmt:esc},{k:'n',t:'n',num:true,fmt:fmtNum,cls:'n'},
  {k:'med',t:'Median ER',num:true,fmt:fmtPct},{k:'avg',t:'Medel ER',num:true,fmt:fmtPct,muted:true},
  {k:'views',t:'Visn.',num:true,fmt:fmtNum,muted:true},
  {k:'delningar',t:'Deln.',num:true,fmt:fmtNum,muted:true},
  {k:'kommentarer',t:'Komm.',num:true,fmt:fmtNum,muted:true},
  {k:'likes',t:'Likes',num:true,fmt:fmtNum,muted:true},
  {k:'sparade',t:'Spar.',num:true,fmt:fmtNum,muted:true}];

function renderDim(dim){
  const st = sortState[dim.id] || (sortState[dim.id]={col:'med',dir:-1});
  const open = openState[dim.id] || (openState[dim.id]=new Set());
  let rows = groups(segPosts(), dim.key);
  rows.sort((a,b)=>{const c=st.col;const va=a[c],vb=b[c];
    return (va<vb?-1:va>vb?1:0)*st.dir;});
  let h = '<table class="bt"><thead><tr>';
  COLS.forEach(c=>{const arrow=st.col===c.k?(st.dir<0?' ▾':' ▴'):'';
    h+=`<th class="${c.num?'v':''}${c.cls?' '+c.cls:''} sortable" data-dim="${dim.id}" data-col="${c.k}">${c.t}${arrow}</th>`;});
  h+='</tr></thead><tbody>';
  rows.forEach(r=>{
    const isopen=open.has(r.k);
    let cells='';
    COLS.forEach((c,i)=>{cells+= i===0
      ? `<td>${isopen?'▾ ':'▸ '}${esc(r.k)}</td>`
      : `<td class="v${c.cls?' '+c.cls:''}${c.muted?' muted':''}">${c.fmt(r[c.k])}</td>`;});
    h+=`<tr class="grow" data-dim="${dim.id}" data-k="${esc(r.k)}">${cells}</tr>`;
    if(isopen){
      const ps=[...r.posts].sort((a,b)=>b.er-a.er);
      const ids=ps.map(p=>p.id);
      const allsel=ids.every(id=>SEL.has(id));
      const sall=`<div class="selall"><label><input type="checkbox" class="selallbox" `+
        `data-ids="${esc(ids.join(','))}"${allsel?' checked':''}> Markera alla ${ps.length} i "${esc(r.k)}"</label></div>`;
      h+=`<tr class="drow"><td colspan="${COLS.length}">${sall}<div class="cards">${ps.map(p=>card(p)).join('')}</div></td></tr>`;
    }
  });
  h+='</tbody></table>';
  if(!rows.length) h='<p class="muted">För få inlägg i detta segment.</p>';
  document.getElementById('dim-'+dim.id).innerHTML=h;
}

const ICON={
 like:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z"/></svg>',
 comment:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.9 9.9 0 0 1-4-.8L3 20l1-3.8A8.4 8.4 0 1 1 21 11.5z"/></svg>',
 share:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/></svg>',
 save:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
};
const ER_FORMULA='(likes + kommentarer×5 + delningar×10 + favoriter×5) / visningar';
function erInner(p){return `<b>${fmtPct(p.er)}</b><span class="tip tip-l">Viktad ER<i>${ER_FORMULA}</i></span>`;}
function benchText(p){const r=p.bench;
  if(r==null)return null;
  if(r>=1.05){let m=(Math.round(r*10)/10).toFixed(1).replace('.',',').replace(/,0$/,'');return {t:m+'× median',c:'pos'};}
  if(r>0.95)return {t:'≈ median',c:'mid'};
  return {t:'under median',c:'neg'};}
function benchHTML(p){const b=benchText(p);if(!b)return '';
  const seg=p.organic?'organiskt':'boostat';
  const tip=`<span class="tip tip-r">Mot medianen för ${seg}<i>${esc(p.bench_lo)} – ${esc(p.bench_hi)} (±1,5 mån)</i></span>`;
  return `<span class="bench ${b.c}">${b.t}${tip}</span>`;}
function metricsHTML(p){return [['like',p.likes],['comment',p.kommentarer],['share',p.delningar],['save',p.sparade]]
  .map(([k,v])=>`<span class="m"><span class="mi">${ICON[k]}</span>${fmtNum(v)}</span>`).join('');}
function card(p,compact){
  const sel=SEL.has(p.id),exp=EXP.has(p.id),ovd=OV[p.id];
  const seg=p.organic?'organiskt':'boostat';
  const check=`<input type="checkbox" class="selbox" data-id="${esc(p.id)}"${sel?' checked':''} title="Markera för bulkändring">`;
  const img=`<img loading="lazy" src="${esc(p.thumb)}" alt="">`;
  // Borttagna/privata videor: visa märke, dämpa miniatyren, ingen play-knapp.
  const play=p.gone?'':'<span class="playbtn" aria-hidden="true"></span>';
  const flag=p.gone?'<span class="goneflag" title="Finns inte längre på TikTok (borttagen eller privat)">borttagen</span>':'';
  const cap=`<div class="pcc">${esc(p.caption)||'<span class="muted">(ingen beskrivning)</span>'}</div>`+
    `<button class="morebtn" data-id="${esc(p.id)}" hidden>${exp?'visa mindre':'läs mer'}</button>`;
  const foot=`<div class="foot"><div class="metaw"><span class="l1">${esc(p.typ)} · ${esc(p.kategori)}</span>`+
    `<span class="l2">${esc(p.date)} · ${seg}</span></div>`+
    `<button class="editbtn" data-id="${esc(p.id)}" title="Ändra taggar">✎ ändra</button></div>`;
  if(compact){
    return `<div class="pc compact${sel?' sel':''}${exp?' expanded':''}">${check}`+
      `<a class="pcimg${p.gone?' gone':''}" href="${esc(p.url)}" data-play="${esc(p.id)}" target="_blank" rel="noopener">${img}${play}${flag}</a>`+
      `<div class="pcm"><div class="erline"><span class="er sm">${erInner(p)}</span>${benchHTML(p)}`+
      `${ovd?'<span class="ovmark">ändrad</span>':''}</div>`+
      `<div class="metrics">${metricsHTML(p)}</div>${cap}${foot}</div></div>`;
  }
  return `<div class="pc${sel?' sel':''}${exp?' expanded':''}">${check}`+
    `<a class="pcimg${p.gone?' gone':''}" href="${esc(p.url)}" data-play="${esc(p.id)}" target="_blank" rel="noopener">${img}${play}${flag}`+
      `<div class="pcoverlay"><span class="er">${erInner(p)}</span>${benchHTML(p)}</div>`+
      `${ovd?'<span class="ovchip">ändrad</span>':''}</a>`+
    `<div class="pcm"><div class="metrics">${metricsHTML(p)}</div>${cap}${foot}</div></div>`;
}

const METRICS={
  er:{label:'Viktad ER',get:p=>p.er,fmt:fmtPct,minv:true,both:true},
  delningar:{label:'Delningar',get:p=>p.delningar,fmt:fmtNum},
  kommentarer:{label:'Kommentarer',get:p=>p.kommentarer,fmt:fmtNum},
  likes:{label:'Likes',get:p=>p.likes,fmt:fmtNum},
  sparade:{label:'Sparningar',get:p=>p.sparade,fmt:fmtNum},
  views:{label:'Visningar',get:p=>p.views,fmt:fmtNum},
};
let rankMetric='er';
let rankDir='top';    // 'top' = Mest, 'bottom' = Svagast (bara för viktad ER)
function rankCard(p,M){
  return `<div class="rankitem"><div class="rankval">${M.fmt(M.get(p))} `+
    `<span class="muted">${M.label.toLowerCase()}</span></div>${card(p,true)}</div>`;
}
function renderRank(){
  const M=METRICS[rankMetric];
  const best=M.both?(rankDir==='top'):true;
  const dirLbl=M.both?(best?'Högst engagemang':'Lägst engagemang'):'Mest';
  const mk=(posts)=>{let c=posts.slice();
    if(M.minv)c=c.filter(p=>p.views>=MIN_VIEWS);
    c=c.sort((a,b)=>best?M.get(b)-M.get(a):M.get(a)-M.get(b)).slice(0,10);
    return c.length?`<div class="ranklist">${c.map(p=>rankCard(p,M)).join('')}</div>`:'<p class="muted">Inga inlägg.</p>';};
  const org=timePosts().filter(p=>p.organic), bo=timePosts().filter(p=>!p.organic);
  const cols=[];                              // följer segment-valet
  if(segment!=='boost')cols.push(['organiskt',org]);
  if(segment!=='org')cols.push(['boostat',bo]);
  const pills=Object.keys(METRICS).map(k=>`<button class="pill mini${k===rankMetric?' active':''}" data-metric="${k}">${METRICS[k].label}</button>`).join('');
  const dirToggle=M.both?('<span class="lbl" style="margin-left:18px">Visa</span>'+
    `<button class="pill mini${rankDir==='top'?' active':''}" data-rankdir="top">Högst engagemang</button>`+
    `<button class="pill mini${rankDir==='bottom'?' active':''}" data-rankdir="bottom">Lägst engagemang</button>`):'';
  const h=`<div class="metricbar"><span class="lbl">Sortera efter</span>${pills}${dirToggle}</div>`+
    cols.map(c=>`<div class="ranksec"><div class="rankhead">${dirLbl} – ${c[0]}</div>${mk(c[1])}</div>`).join('');
  document.getElementById('rank').innerHTML=h;
}

// Tidsupplösning i grafen beror på vald period: ~1 mån → dag, ~3 mån → vecka,
// ~1–1,5 år → månad, längre → kvartal.
function isoWeek(ws){const d=new Date(ws+'T00:00:00Z'),dn=(d.getUTCDay()+6)%7;
  d.setUTCDate(d.getUTCDate()-dn+3);const ft=new Date(Date.UTC(d.getUTCFullYear(),0,4));
  const fn=(ft.getUTCDay()+6)%7;ft.setUTCDate(ft.getUTCDate()-fn+3);
  return 1+Math.round((d-ft)/6048e5);}
function bucketKey(d,gran){
  if(gran==='day')return [d,(+d.slice(8,10))+'/'+(+d.slice(5,7))];
  if(gran==='week'){const dt=new Date(d+'T00:00:00Z'),dow=(dt.getUTCDay()+6)%7;
    dt.setUTCDate(dt.getUTCDate()-dow);const ws=dt.toISOString().slice(0,10);
    return [ws,'v'+isoWeek(ws)];}
  if(gran==='month'){const MN=['jan','feb','mar','apr','maj','jun','jul','aug','sep','okt','nov','dec'];
    return [d.slice(0,7),MN[(+d.slice(5,7))-1]+' -'+d.slice(2,4)];}
  const y=+d.slice(0,4),q=(((+d.slice(5,7))-1)/3|0)+1;
  return [y+'-Q'+q,'Q'+q+'-'+String(y).slice(2)];
}
function timeAgg(posts,gran){
  gran=gran||'quarter';const m={};
  posts.forEach(p=>{const d=(p.date||'').slice(0,10);if(d.length<7)return;
    const kl=bucketKey(d,gran);(m[kl[0]]=m[kl[0]]||{lbl:kl[1],ers:[]}).ers.push(p.er);});
  return Object.keys(m).sort().map(k=>[m[k].lbl,median(m[k].ers),m[k].ers.length]);
}
function chartGran(){
  let from=periodFrom,to=periodTo;
  if(!from||!to){const ds=segPosts().map(p=>p.date).filter(Boolean).sort();
    from=from||ds[0];to=to||ds[ds.length-1];}
  if(!from||!to)return 'quarter';
  const span=(new Date(to)-new Date(from))/864e5;
  return span<=45?'day':span<=130?'week':span<=600?'month':'quarter';
}
const GRAN_LBL={day:'per dag',week:'per vecka',month:'per månad',quarter:'per kvartal'};
function quartersAgg(posts){return timeAgg(posts,chartGran());}
function trendOf(series){
  // Linjär regression på kvartalens median-ER (i %). Domen är RELATIV: total
  // förändring över perioden jämförs med nivån (medianen), så samma tröskel
  // funkar för både organiskt (~3-5%) och boostat (~0,2%).
  if(series.length<3)return{sl:0,total:0,verdict:'för få kvartal'};
  const pts=series.map((s,i)=>[i,s[1]*100]),n=pts.length;
  const mx=pts.reduce((a,p)=>a+p[0],0)/n,my=pts.reduce((a,p)=>a+p[1],0)/n;
  let nu=0,de=0;pts.forEach(p=>{nu+=(p[0]-mx)*(p[1]-my);de+=(p[0]-mx)**2;});
  const sl=de?nu/de:0, total=sl*(n-1), lvl=median(pts.map(p=>p[1]))||1;
  const rel=total/lvl;
  return{sl,total,verdict:rel>0.15?'uppåt ↗':rel<-0.15?'nedåt ↘':'stabilt →'};
}
function renderOverview(){
  const ps=segPosts(),n=ps.length;
  const sum=f=>ps.reduce((a,p)=>a+(p[f]||0),0);
  const sumV=sum('views'),sumL=sum('likes'),sumK=sum('kommentarer'),sumD=sum('delningar'),sumS=sum('sparade');
  const erMed=n?median(ps.map(p=>p.er)):0;
  const wSum=ps.reduce((a,p)=>a+(p.likes+p.kommentarer*5+p.delningar*10+p.sparade*5),0);
  const erSnitt=sumV?wSum/sumV:0;
  const tr=trendOf(quartersAgg(ps));
  // Stora tal i kortform; exakt tal vid hover (title).
  const bigv=x=>`<span title="${fmtNum(x)} exakt">${compactNum(x)}</span>`;
  const tile=(v,l,ac)=>`<div class="c${ac?' '+ac:''}"><div class="big">${v}</div><div class="muted">${l}</div></div>`;
  const sr=document.getElementById('statrow');
  if(sr)sr.innerHTML=tile(bigv(n),'inlägg')+tile(bigv(sumV),'visningar')+
    tile(n?fmtPct(erMed):'–','eng.rate median','ac-pink')+
    tile(n?fmtPct(erSnitt):'–','eng.rate snitt','ac-blue')+
    `<div class="c wide trendcard"><div class="trendhead"><span class="big">${tr.verdict}</span>`+
      `<span class="muted">eng.rate-trend över tid</span></div><div class="trendmini" id="trendmini"></div></div>`;
  const ir=document.getElementById('introw');
  if(ir)ir.innerHTML=
    [['like',sumL,'likes'],['comment',sumK,'kommentarer'],['share',sumD,'delningar'],['save',sumS,'sparade']]
     .map(([k,v,t])=>`<div class="imc"><span class="imi">${ICON[k]}</span><div class="imv" title="${fmtNum(v)} exakt">${compactNum(v)}</div><div class="imt">${t}</div></div>`).join('');
  renderMiniChart();
}
// Förenklad trendgraf i dashboarden: samma typ av area-/linjegraf som den stora,
// men avskalad (inga axlar/etiketter), plus en rät trendlinje (linjär regression)
// som visar riktningen över tid. Följer segment- och periodvalet.
function renderMiniChart(){
  const host=document.getElementById('trendmini');if(!host)return;
  const series=timeAgg(segPosts(),chartGran());
  if(series.length<2){host.innerHTML='<div class="trendempty">för få datapunkter</div>';return;}
  const W=320,H=72,pad=5;
  const ys=series.map(s=>s[1]*100),ymax=Math.max(...ys)*1.18||1;
  const n=series.length;
  const X=i=>pad+i*(W-2*pad)/(n-1), Y=v=>H-pad-(Math.max(0,Math.min(ymax,v))/ymax)*(H-2*pad);
  const P=series.map((s,i)=>[X(i),Y(ys[i])]);
  const base=H-pad;
  const line=P.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join(' ');
  const area='M '+P[0][0].toFixed(1)+' '+base.toFixed(1)+' '+
    P.map(p=>'L '+p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' ')+
    ' L '+P[n-1][0].toFixed(1)+' '+base.toFixed(1)+' Z';
  // Linjär regression → rät trendlinje från första till sista punkten.
  const mx=(n-1)/2, my=ys.reduce((a,b)=>a+b,0)/n;
  let nu=0,de=0;for(let i=0;i<n;i++){nu+=(i-mx)*(ys[i]-my);de+=(i-mx)**2;}
  const sl=de?nu/de:0, a0=my-sl*mx;
  const trend=X(0).toFixed(1)+','+Y(a0).toFixed(1)+' '+X(n-1).toFixed(1)+','+Y(a0+sl*(n-1)).toFixed(1);
  host.innerHTML='<svg viewBox="0 0 '+W+' '+H+'" width="100%" height="100%" preserveAspectRatio="none" style="display:block">'
    +'<defs><linearGradient id="miniarea" x1="0" y1="0" x2="0" y2="1">'
    +'<stop offset="0" stop-color="#fff" stop-opacity=".18"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>'
    +'<path d="'+area+'" fill="url(#miniarea)"/>'
    +'<polyline points="'+line+'" fill="none" stroke="rgba(255,255,255,.55)" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>'
    +'<polyline points="'+trend+'" fill="none" stroke="#ffaac7" stroke-width="2.4" stroke-linecap="round"/>'
    +'</svg>';
}
function renderChart(){
  const gran=chartGran(),series=timeAgg(segPosts(),gran);
  const lbl={alla:'alla',org:'organiskt',boost:'boostat'}[segment];
  document.getElementById('chart-title').textContent='Engagemang över tid ('+lbl+', '+GRAN_LBL[gran]+')';
  const L=levelsOf(segPosts());
  const lv=document.getElementById('levels');
  if(lv)lv.innerHTML=`Nivåer för <b>${lbl}</b> (datadrivet, kvartiler): lågt &lt; ${fmtPct(L.q1)} · medel · högt &gt; ${fmtPct(L.q3)} — median ${fmtPct(L.med)}.`;
  const note=document.getElementById('chart-note'),host=document.getElementById('chart');
  if(series.length<2){host.innerHTML='<p class="muted" style="padding:12px">För få datapunkter i perioden/segmentet.</p>';note.textContent='';return;}
  const W=960,H=340,pl=48,pr=58,pt=26,pb=44;
  const ys=series.map(s=>s[1]*100),ymax=Math.max(...ys)*1.18||1;
  const X=i=>pl+i*(W-pl-pr)/(series.length-1),Y=v=>H-pb-(v/ymax)*(H-pt-pb),base=Y(0);
  const pctLbl=v=>v.toFixed(2).replace('.',',')+' %';
  let grid='';const ystep=ymax<=1?0.2:ymax<=2.5?0.5:ymax<=6?1:ymax<=14?2:5,ydec=ystep<1?1:0;
  for(let i=0;i*ystep<=ymax+1e-9;i++){const t=i*ystep,y=Y(t);
    grid+=`<line x1="${pl}" y1="${y.toFixed(1)}" x2="${W-pr}" y2="${y.toFixed(1)}" stroke="#e2ddd3"/>`+
      `<text x="${pl-9}" y="${(y+3.5).toFixed(1)}" text-anchor="end" font-size="10.5" fill="#8b857a">${t.toFixed(ydec).replace('.',',')} %</text>`;}
  let band='';
  if(L.q1>0&&L.q3>L.q1){const y3=Y(Math.min(L.q3*100,ymax)),y1=Y(L.q1*100);
    band=`<rect x="${pl}" y="${y3.toFixed(1)}" width="${(W-pl-pr).toFixed(1)}" height="${(y1-y3).toFixed(1)}" fill="#242f550d"/>`;}
  let medline='';
  if(L.med*100<=ymax){const ym=Y(L.med*100);
    medline=`<line x1="${pl}" y1="${ym.toFixed(1)}" x2="${W-pr}" y2="${ym.toFixed(1)}" stroke="#242f55" stroke-width="1.2" stroke-dasharray="5 4" opacity=".5"/>`+
      `<text x="${pl+6}" y="${(ym-5).toFixed(1)}" font-size="10" font-weight="600" fill="#242f55" opacity=".72">median</text>`;}
  const P=series.map((s,i)=>[X(i),Y(ys[i])]);
  const line=P.map(p=>`${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const area=`M ${P[0][0].toFixed(1)} ${base.toFixed(1)} `+P.map(p=>`L ${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join(' ')+` L ${P[P.length-1][0].toFixed(1)} ${base.toFixed(1)} Z`;
  const dots=P.map((p,i)=>`<circle cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="4" fill="#1359c5" stroke="#fff" stroke-width="2" style="pointer-events:none"/>`).join('');
  const hits=P.map((p,i)=>`<circle class="cdot" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="13" fill="transparent" data-q="${series[i][0]}" data-er="${pctLbl(ys[i])}" data-n="${series[i][2]}"/>`).join('');
  const vlab=series.length<=16?P.map((p,i)=>`<text x="${p[0].toFixed(1)}" y="${(p[1]-11).toFixed(1)}" text-anchor="middle" font-size="9.5" font-weight="700" fill="#242f55">${pctLbl(ys[i])}</text>`).join(''):'';
  const st=Math.max(1,Math.ceil(series.length/16));let xl='';
  for(let i=0;i<series.length;i+=st)xl+=`<text x="${X(i).toFixed(1)}" y="${H-pb+20}" text-anchor="middle" font-size="10.5" fill="#8b857a">${series[i][0]}</text>`;
  host.innerHTML=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="width:100%">`+
    `<defs><linearGradient id="iqarea" x1="0" y1="0" x2="0" y2="1">`+
    `<stop offset="0" stop-color="#1359c5" stop-opacity=".22"/><stop offset="1" stop-color="#1359c5" stop-opacity="0"/></linearGradient></defs>`+
    `${grid}${band}${medline}<path d="${area}" fill="url(#iqarea)"/>`+
    `<polyline points="${line}" fill="none" stroke="#1359c5" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>`+
    `${dots}${vlab}${xl}${hits}</svg><div id="charttip" class="charttip" hidden></div>`;
  const tr=trendOf(series);
  const k=Math.min(3,series.length);
  const early=median(series.slice(0,k).map(s=>s[1]*100)),recent=median(series.slice(-k).map(s=>s[1]*100));
  const chg=early?Math.round((recent-early)/early*100):0;
  const dir=chg<0?`ungefär ${Math.abs(chg)} % lägre`:chg>0?`ungefär ${chg} % högre`:'på ungefär samma nivå';
  note.innerHTML=`<strong>Trend: ${tr.verdict}</strong> Engagemanget har gått från ${pctLbl(early)} i början av perioden till ${pctLbl(recent)} i slutet – ${dir}. Håll muspekaren på en punkt för eng.rate och antal inlägg.`;
}
function renderOverridden(){
  const el=document.getElementById('overridden');if(!el)return;
  // Visa bara riktiga taggrättelser – hoppa över rena strategi-ändringar
  // (kampanj-taggning) så listan inte blir jättelång.
  const items=Object.keys(OV).map(id=>({p:POSTS.find(x=>x.id===id),
      ch:Object.entries(OV[id]).filter(e=>e[0]!=='strategi')}))
    .filter(o=>o.p&&o.ch.length);
  const head=document.getElementById('ovhead');
  if(head)head.textContent='Manuellt ändrade ('+items.length+')';
  if(!items.length){el.innerHTML='<p class="muted">Inga manuella taggrättelser än (kampanj-taggning räknas inte här).</p>';return;}
  el.innerHTML='<div class="cards col">'+items.map(o=>{
    const ch=o.ch.map(e=>`${esc(e[0])} → <b>${esc(e[1]||'(tom)')}</b>`).join(' · ');
    return `<div class="ovitem">${card(o.p,true)}<div class="ovchg">Ändrat: ${ch}</div></div>`;
  }).join('')+'</div>';
}
function renderSearch(){
  const box=document.getElementById('searchbox');if(!box)return;
  const q=(box.value||'').trim().toLowerCase();
  const info=document.getElementById('searchinfo'),res=document.getElementById('searchres');
  if(!q){info.textContent='';res.innerHTML='';return;}
  if(q.length<2){info.textContent='Skriv minst två tecken.';res.innerHTML='';return;}
  const hits=timePosts().filter(p=>(p.sok||'').includes(q));
  info.innerHTML=`<b>${hits.length}</b> inlägg innehåller ”${esc(q)}” i beskrivningen.`;
  if(!hits.length){res.innerHTML='';return;}
  const ids=hits.map(p=>p.id),allsel=ids.every(id=>SEL.has(id));
  const sall=`<div class="selall"><label><input type="checkbox" class="selallbox" `+
    `data-ids="${esc(ids.join(','))}"${allsel?' checked':''}> Markera alla ${hits.length} träffarna</label></div>`;
  res.innerHTML=sall+`<div class="cards">${hits.map(p=>card(p)).join('')}</div>`;
  requestAnimationFrame(refreshMore);
}
// Visa "läs mer" bara på kort vars beskrivning faktiskt är klippt (>3 rader).
// Kräver layout, så körs efter att korten ritats (och när bredd/kolumner ändras).
function refreshMore(){
  document.querySelectorAll('.pc').forEach(pc=>{
    const btn=pc.querySelector('.morebtn'),cc=pc.querySelector('.pcc');
    if(!btn||!cc)return;
    const id=btn.dataset.id;
    if(EXP.has(id)){btn.hidden=false;btn.textContent='visa mindre';return;}
    const clipped=cc.scrollHeight-cc.clientHeight>2;   // 0 utan layout (t.ex. i test)
    btn.hidden=!clipped;if(clipped)btn.textContent='läs mer';
  });
}
function renderAll(){renderOverview();renderChart();DIMS.forEach(renderDim);renderRank();renderSearch();
  requestAnimationFrame(refreshMore);}

/* ---- Manuella rättelser (overrides) -------------------------------------
   OV = {video_id:{field:value}}. Seedas från overrides.csv (bakat i POSTS av
   analyze + speglat i window.OVERRIDES) samt localStorage (osparade ändringar).
   enriched.csv rörs aldrig. "Ladda ner overrides.csv" exporterar hela OV. */
const AUTOSAVE=location.protocol.indexOf('http')===0;  // serverad → spara direkt
const LS_KEY='iq_overrides_'+POSTS.length;
function loadLS(){try{return JSON.parse(localStorage.getItem(LS_KEY)||'{}')||{};}catch(e){return{};}}
function saveLS(){try{localStorage.setItem(LS_KEY,JSON.stringify(OV));}catch(e){}}
let OV={};
(function(){const disk=window.OVERRIDES||{},ls=loadLS();
  for(const v in disk)OV[v]=Object.assign({},disk[v]);
  for(const v in ls)OV[v]=Object.assign(OV[v]||{},ls[v]);})();
function applyOv(){POSTS.forEach(p=>{const o=OV[p.id];if(o)for(const f in o)p[f]=o[f];});}
applyOv();

/* ---- Bulkmarkering & bulkändring ---------------------------------------
   SEL = markerade inläggs-id. Kryssrutor på varje kort + "markera alla" per
   grupp. När minst ett är markerat visas bulkfältet (döljer ovbar): välj fält
   + värde och applicera på alla markerade på en gång. Sparas som vanliga
   overrides (KV i molnet). */
let bulkField='har_hook';
let bulkVal=null;        // förvalt värde (den tagg man hoppat in i)
let bulkManual=false;    // true när man själv ändrat fält/värde → sluta förvälja
// Vilket redigerbart fält hör en kryssruta till? (dvs vilken dimension man står
// i.) Används för att förvälja rätt fält i bulkraden när man börjar markera.
function ctxField(el){
  const host=el.closest&&el.closest('[id^="dim-"]');
  if(!host)return null;
  const d=DIMS.find(x=>'dim-'+x.id===host.id);
  return d&&d.field&&window.EDITABLE[d.field]?d.field:null;
}
// Postens värde för fältet, om det är ett giltigt alternativ (annars null).
function postFieldVal(id,field){const p=POSTS.find(x=>x.id===id);
  if(!p)return null;const v=p[field];
  return (window.EDITABLE[field]||[]).includes(v)?v:null;}
// Förvälj fält + värde efter den dimension/tagg man markerar i (om inte man
// själv redan ändrat bulkraden).
function autoPick(ctx,id){if(!ctx||bulkManual)return;bulkField=ctx;bulkVal=postFieldVal(id,ctx);}
function reflectSel(){                       // spegla SEL till alla kryssrutor + kort utan full omritning
  document.querySelectorAll('.selbox').forEach(cb=>{
    const on=SEL.has(cb.dataset.id);cb.checked=on;
    const pc=cb.closest('.pc');if(pc)pc.classList.toggle('sel',on);});
  document.querySelectorAll('.selallbox').forEach(cb=>{
    const ids=(cb.dataset.ids||'').split(',').filter(Boolean);
    cb.checked=ids.length>0&&ids.every(id=>SEL.has(id));});
}
function toggleSel(id,on,ctx){
  on?SEL.add(id):SEL.delete(id);
  if(on)autoPick(ctx,id);
  reflectSel();updateBulkBar();}
function toggleSelAll(ids,on,ctx){
  ids.forEach(id=>on?SEL.add(id):SEL.delete(id));
  if(on&&ids.length)autoPick(ctx,ids[0]);
  reflectSel();updateBulkBar();}
function bulkValueOptions(){
  return (window.EDITABLE[bulkField]||[]).map(v=>`<option value="${esc(v)}"${v===bulkVal?' selected':''}>${esc(v||'(tom)')}</option>`).join('');}
function updateBulkBar(){
  const bar=document.getElementById('bulkbar'),ov=document.getElementById('ovbar');
  if(!bar)return;
  if(!SEL.size){bar.hidden=true;if(ov)ov.hidden=false;bulkManual=false;bulkVal=null;return;}
  if(ov)ov.hidden=true;bar.hidden=false;
  const fopts=Object.keys(window.EDITABLE).map(f=>`<option value="${esc(f)}"${f===bulkField?' selected':''}>${esc(f)}</option>`).join('');
  bar.innerHTML=`<span><b>${SEL.size}</b> markerade</span><span class="sep">·</span>`+
    `<span>sätt</span><select id="bulkField">${fopts}</select>`+
    `<span>till</span><select id="bulkValue">${bulkValueOptions()}</select>`+
    `<button class="pill dark" data-act="bulkapply">Applicera på ${SEL.size}</button>`+
    `<button class="pill" data-act="bulkclear">Avmarkera</button>`;
}
function applyBulk(){
  const vsel=document.getElementById('bulkValue');if(!vsel)return;
  const v=vsel.value;let n=0;const changes=[];
  SEL.forEach(id=>{const p=POSTS.find(x=>x.id===id);if(!p)return;
    if(v!==(p[bulkField]||'')){OV[id]=OV[id]||{};OV[id][bulkField]=v;changes.push({id,field:bulkField,value:v});n++;}});
  saveLS();applyOv();pushDeltas(changes);
  SEL.clear();updateOvBar();renderAll();updateBulkBar();
  if(!AUTOSAVE&&n)toast(`${n} inlägg ändrade – ladda ner för att spara`);
}
function clearSel(){SEL.clear();reflectSel();updateBulkBar();}

let editId=null;
function openEditor(id){editId=id;const p=POSTS.find(x=>x.id===id);if(!p)return;
  document.getElementById('edCap').textContent=(p.caption||id).slice(0,90);
  const box=document.getElementById('edFields');box.innerHTML='';
  for(const f in window.EDITABLE){const cur=p[f]||'';
    const opts=window.EDITABLE[f].map(v=>`<option value="${esc(v)}"${v===cur?' selected':''}>${esc(v||'(tom)')}</option>`).join('');
    box.insertAdjacentHTML('beforeend',`<div><label>${f}</label><select data-f="${f}">${opts}</select></div>`);}
  document.getElementById('editor').hidden=false;}
function closeEditor(){document.getElementById('editor').hidden=true;editId=null;}
function saveEditor(){const p=POSTS.find(x=>x.id===editId);if(!p){closeEditor();return;}
  const changes=[];
  document.querySelectorAll('#edFields select').forEach(sel=>{const f=sel.dataset.f,v=sel.value;
    if(v!==(p[f]||'')){OV[editId]=OV[editId]||{};OV[editId][f]=v;changes.push({id:editId,field:f,value:v});}});
  saveLS();applyOv();pushDeltas(changes);updateOvBar();closeEditor();renderAll();}

/* ---- Justeringsflöde: gå igenom inläggen ett i taget och rätta taggar ---- */
const REV_KEY='iq_reviewed_'+POSTS.length;
function loadRev(){try{return new Set(JSON.parse(localStorage.getItem(REV_KEY)||'[]'));}catch(e){return new Set();}}
let reviewed=loadRev();
function saveRev(){try{localStorage.setItem(REV_KEY,JSON.stringify([...reviewed]));}catch(e){}}
let flowStatus='all', flowField='', flowVal='', flowQuery='', flowHide=false, flowList=[], flowIdx=0;
const HOOK_HINT='Hook (vår definition): en direkt fråga i början ("Har du testat att…?"), eller något som sticker ut och får en att reagera – ibland ett kort klipp från längre fram i videon som lyfts först för att man ska vilja se mer.';
function buildFlowList(){
  let ps=POSTS.slice();
  if(flowStatus==='adj')ps=ps.filter(p=>OV[p.id]);
  else if(flowStatus==='unadj')ps=ps.filter(p=>!OV[p.id]);
  if(flowField&&flowVal!=='')ps=ps.filter(p=>(p[flowField]||'')===flowVal);
  const q=flowQuery.trim().toLowerCase();
  if(q)ps=ps.filter(p=>(p.sok||'').includes(q));
  if(flowHide)ps=ps.filter(p=>!reviewed.has(p.id));
  ps.sort((a,b)=>(b.date||'').localeCompare(a.date||''));
  flowList=ps.map(p=>p.id);
  if(flowIdx>=flowList.length)flowIdx=Math.max(0,flowList.length-1);
}
function flowFieldsHTML(p){
  return Object.keys(window.EDITABLE).map(f=>{const cur=p[f]||'';
    const opts=window.EDITABLE[f].map(v=>`<option value="${esc(v)}"${v===cur?' selected':''}>${esc(v||'(tom)')}</option>`).join('');
    const hint=f==='har_hook'?` <span class="fhint" title="${esc(HOOK_HINT)}">?</span>`:'';
    return `<div class="ffield"><label>${f}${hint}</label><select class="flowsel" data-f="${f}" data-id="${esc(p.id)}">${opts}</select></div>`;
  }).join('');
}
function renderFlow(){
  const host=document.getElementById('flow');if(!host)return;
  buildFlowList();const total=flowList.length;
  if(!total){host.innerHTML='<p class="muted" style="padding:16px 0">Inga inlägg matchar filtret. 🎉</p>';return;}
  const p=POSTS.find(x=>x.id===flowList[flowIdx]);if(!p){host.innerHTML='';return;}
  const adj=OV[p.id],rev=reviewed.has(p.id),seg=p.organic?'organiskt':'boostat',bench=benchText(p);
  host.innerHTML=
    `<div class="flowhead"><span>Inlägg <b>${flowIdx+1}</b> av <b>${total}</b></span>`+
    `<span class="dot">·</span><span><b>${reviewed.size}</b> granskade totalt</span>`+
    (adj?'<span class="flowbadge adj">justerad</span>':'')+
    (rev?'<span class="flowbadge rev">granskad ✓</span>':'')+'</div>'+
    `<div class="flowcard">`+
      `<a class="flowimg${p.gone?' gone':''}" href="${esc(p.url)}" data-play="${esc(p.id)}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(p.thumb)}" alt="">`+
        (p.gone?'<span class="goneflag" title="Finns inte längre på TikTok">borttagen</span>':'<span class="playbtn" aria-hidden="true"></span>')+
        `<span class="er"><b>${fmtPct(p.er)}</b></span>`+(bench?`<span class="bench ${bench.c}">${bench.t}</span>`:'')+`</a>`+
      `<div class="flowbody">`+
        `<div class="flowmeta">${esc(p.typ)} · ${esc(p.kategori)} · ${esc(p.date)} · ${seg} · `+
          `<a href="${esc(p.url)}" target="_blank" rel="noopener">öppna på TikTok ↗</a></div>`+
        `<div class="flowmetrics metrics">${metricsHTML(p)}</div>`+
        `<div class="flowcap">${esc(p.caption)||'<span class="muted">(ingen beskrivning)</span>'}</div>`+
        `<div class="flowfields">${flowFieldsHTML(p)}</div>`+
        `<p class="flowhook">${esc(HOOK_HINT)}</p>`+
      `</div></div>`+
    `<div class="flowactions">`+
      `<button class="pill" data-flow="prev"${flowIdx<=0?' disabled':''}>← Föregående</button>`+
      `<button class="pill" data-flow="skip">Hoppa över →</button>`+
      `<button class="pill dark" data-flow="done">Granskad ✓ → nästa</button></div>`;
}
function flowSet(id,field,value){const p=POSTS.find(x=>x.id===id);if(!p||value===(p[field]||''))return;
  OV[id]=OV[id]||{};OV[id][field]=value;saveLS();applyOv();pushDeltas([{id,field,value}]);
  updateOvBar();renderAll();renderFlow();}
function flowAct(act){
  if(!flowList.length)return;const id=flowList[flowIdx];
  if(act==='prev'){if(flowIdx>0)flowIdx--;renderFlow();return;}
  if(act==='done'&&id){reviewed.add(id);saveRev();
    if(flowHide){renderFlow();return;}}            // rebuild drops den → nästa glider in
  if(flowIdx<flowList.length-1)flowIdx++;
  renderFlow();}
function populateFlowFilters(){
  const fs=document.getElementById('ffield');if(fs)fs.innerHTML='<option value="">(alla fält)</option>'+
    Object.keys(window.EDITABLE).map(f=>`<option value="${f}">${f}</option>`).join('');
  updateFlowValFilter();}
function updateFlowValFilter(){const vs=document.getElementById('fval');if(!vs)return;
  if(!flowField){vs.innerHTML='<option value="">–</option>';vs.disabled=true;return;}
  vs.disabled=false;vs.innerHTML='<option value="">(alla värden)</option>'+
    (window.EDITABLE[flowField]||[]).map(v=>`<option value="${esc(v)}">${esc(v||'(tom)')}</option>`).join('');}
function showTab(name){
  document.querySelectorAll('.tabpanel').forEach(p=>{p.hidden=(p.dataset.tab!==name);});
  document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));
  try{localStorage.setItem('iq_tab',name);}catch(e){}
  if(name==='justering')renderFlow();
  if(name==='sok'){const sb=document.getElementById('searchbox');if(sb)setTimeout(()=>sb.focus(),30);}
  requestAnimationFrame(refreshMore);}
const OV_ENDPOINT='/api/overrides';
// Spara bara de ÄNDRADE fälten (deltas) till den delade databasen – aldrig hela
// blobben. Då kan två personer spara samtidigt utan att skriva över varandra.
function pushDeltas(changes,quiet){
  if(!AUTOSAVE||!changes||!changes.length)return;
  fetch(OV_ENDPOINT,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({deltas:changes})})
    .then(r=>r.ok?r.json():Promise.reject())
    .then(()=>{if(!quiet)toast('sparat ✓ – synkat till alla');})
    .catch(()=>{if(!quiet)toast('kunde inte spara – kontrollera anslutningen');});}
function exportCsv(){let rows=[['video_id','field','value']];
  for(const v in OV)for(const f in OV[v])rows.push([v,f,OV[v][f]]);
  const csv=rows.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\n');
  const blob=new Blob(['﻿'+csv],{type:'text/csv'}),url=URL.createObjectURL(blob),a=document.createElement('a');
  a.href=url;a.download='overrides.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function clearLocal(){if(!confirm('Rensa lokala (osparade) ändringar? Redan sparade i overrides.csv finns kvar efter att du kört analyze.py igen.'))return;
  try{localStorage.removeItem(LS_KEY);}catch(e){}location.reload();}
// Liten självslocknande notis (i stället för en permanent bar) – ändringar
// sparas ändå automatiskt till den delade databasen.
function toast(msg){const t=document.getElementById('toast');if(!t)return;
  t.textContent=msg;t.classList.add('show');
  clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),1900);}
function updateOvBar(){const b=document.getElementById('ovbar');if(!b)return;const n=Object.keys(OV).length;
  if(AUTOSAVE){
    // I molnet sparas allt automatiskt till databasen – ingen permanent bar.
    b.hidden=true;b.innerHTML='';
  }else{
    b.hidden=false;
    b.innerHTML=`<span><b>${n}</b> inlägg med manuella ändringar</span>`+
      `<button class="pill dark" data-act="dl">⬇ Ladda ner overrides.csv</button>`+
      `<button class="pill" data-act="clr">Rensa lokala</button>`+
      `<span class="muted">Spara filen i iq_tiktok_data/ och kör <b>analyze.py</b> igen. `+
      `(Tips: kör <b>serve.py</b> så sparas ändringar automatiskt.)</span>`;}}

// Spela upp videon direkt på sidan i en lightbox via TikToks officiella
// inbäddade spelare. Inget API-nyckel behövs – spelaren laddas i en iframe
// i tittarens egen webbläsare. Öppna-på-TikTok finns alltid som reserv.
let _lbId=null;   // vilken video som visas i lightboxen just nu
function lbUnavailable(p){
  const thumb=(p&&p.thumb)?'<img src="'+esc(p.thumb)+'" alt="">':'';
  document.getElementById('lbFrame').innerHTML=
    '<div class="lb-gone">'+thumb+
    '<div class="lb-gone-msg"><strong>Videon är inte tillgänglig</strong>'+
    '<p>Den har tagits bort eller gjorts privat på TikTok. Siffrorna och '+
    'miniatyren finns kvar i dashboarden.</p></div></div>';
}
function openVideo(id,url){
  if(!id)return;
  const p=BY_ID[id];_lbId=id;
  const op=document.getElementById('lbOpen');if(op)op.href=url||(p&&p.url)||'#';
  if(p&&p.gone){
    lbUnavailable(p);                                 // känd borttagen → ingen iframe
  }else{
    const src='https://www.tiktok.com/player/v1/'+encodeURIComponent(id)+
      '?autoplay=1&loop=1&rel=0&music_info=0&description=0&native_context_menu=0';
    document.getElementById('lbFrame').innerHTML=
      '<iframe src="'+src+'" allow="autoplay; fullscreen; encrypted-media; picture-in-picture" '+
      'allowfullscreen scrolling="no" frameborder="0"></iframe>';
  }
  document.getElementById('lightbox').hidden=false;
  document.body.classList.add('noscroll');
}
function closeVideo(){
  const lb=document.getElementById('lightbox');if(!lb||lb.hidden)return;
  lb.hidden=true;_lbId=null;
  document.getElementById('lbFrame').innerHTML='';   // stoppar uppspelningen
  document.body.classList.remove('noscroll');
}
// Spelaren postar meddelanden till föräldrafönstret. Rapporterar den ett fel
// (videon togs bort efter senaste tillgänglighetskontrollen) byter vi till
// "inte tillgänglig"-vyn i stället för en svart ruta.
window.addEventListener('message',e=>{
  let host='';try{host=new URL(e.origin).hostname;}catch(_){return;}
  if(!/(^|\.)tiktok\.com$/.test(host))return;
  if(!_lbId||document.getElementById('lightbox').hidden)return;
  let d=e.data;if(typeof d==='string'){try{d=JSON.parse(d);}catch(_){return;}}
  const kind=((d&&(d.type||d.event||d.name))||'')+'';
  if(/error/i.test(kind))lbUnavailable(BY_ID[_lbId]);
});
document.addEventListener('click',e=>{
  // Klick på en miniatyr → spela upp i lightbox. Cmd/Ctrl/mittenklick samt
  // högerklick lämnas till webbläsaren så att man kan öppna på TikTok i ny flik.
  const pv=e.target.closest('[data-play]');
  if(pv&&e.button===0&&!e.metaKey&&!e.ctrlKey&&!e.shiftKey&&!e.altKey){
    e.preventDefault();openVideo(pv.dataset.play,pv.getAttribute('href'));return;}
  if(e.target.closest('#lbClose')||e.target.id==='lightbox'||e.target.classList.contains('lb-backdrop')){
    closeVideo();return;}
  const eb=e.target.closest('.editbtn');
  if(eb){openEditor(eb.dataset.id);return;}
  const mb=e.target.closest('.morebtn');
  if(mb){const id=mb.dataset.id;EXP.has(id)?EXP.delete(id):EXP.add(id);
    const pc=mb.closest('.pc');if(pc)pc.classList.toggle('expanded',EXP.has(id));
    refreshMore();return;}
  const mp=e.target.closest('.pill[data-metric]');
  if(mp){rankMetric=mp.dataset.metric;renderRank();return;}
  const rd=e.target.closest('.pill[data-rankdir]');
  if(rd){rankDir=rd.dataset.rankdir;renderRank();return;}
  const cp=e.target.closest('.pill[data-cols]');
  if(cp){setCols(+cp.dataset.cols);return;}
  const tb=e.target.closest('.tab');
  if(tb){showTab(tb.dataset.tab);return;}
  const fst=e.target.closest('.pill[data-fstatus]');
  if(fst){flowStatus=fst.dataset.fstatus;flowIdx=0;
    document.querySelectorAll('.pill[data-fstatus]').forEach(x=>x.classList.toggle('active',x===fst));renderFlow();return;}
  const fa=e.target.closest('.pill[data-flow]');
  if(fa){flowAct(fa.dataset.flow);return;}
  const act=e.target.closest('[data-act]');
  if(act){const a=act.dataset.act;
    if(a==='dl')exportCsv();else if(a==='clr')clearLocal();
    else if(a==='bulkapply')applyBulk();else if(a==='bulkclear')clearSel();return;}
  if(e.target.closest('#edSave')){saveEditor();return;}
  if(e.target.closest('#edClose')||e.target.closest('#edCancel')||e.target.id==='editor'){closeEditor();return;}
  const th=e.target.closest('th.sortable');
  if(th){const d=th.dataset.dim,c=th.dataset.col;const st=sortState[d];
    if(st.col===c)st.dir*=-1;else{st.col=c;st.dir=(c==='k')?1:-1;}renderDim(DIMS.find(x=>x.id===d));return;}
  const gr=e.target.closest('tr.grow');
  if(gr){const d=gr.dataset.dim,k=gr.dataset.k;const s=openState[d];
    s.has(k)?s.delete(k):s.add(k);renderDim(DIMS.find(x=>x.id===d));return;}
});
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){closeVideo();if(typeof closeEditor==='function')closeEditor();}
});
document.addEventListener('change',e=>{
  const cb=e.target.closest('.selbox');
  if(cb){toggleSel(cb.dataset.id,cb.checked,ctxField(cb));return;}
  const ca=e.target.closest('.selallbox');
  if(ca){toggleSelAll((ca.dataset.ids||'').split(',').filter(Boolean),ca.checked,ctxField(ca));return;}
  if(e.target.id==='bulkField'){bulkField=e.target.value;bulkVal=null;bulkManual=true;
    const vs=document.getElementById('bulkValue');if(vs)vs.innerHTML=bulkValueOptions();return;}
  if(e.target.id==='bulkValue'){bulkVal=e.target.value;bulkManual=true;return;}
  const fsel=e.target.closest('.flowsel');
  if(fsel){flowSet(fsel.dataset.id,fsel.dataset.f,fsel.value);return;}
  if(e.target.id==='ffield'){flowField=e.target.value;flowVal='';updateFlowValFilter();flowIdx=0;renderFlow();return;}
  if(e.target.id==='fval'){flowVal=e.target.value;flowIdx=0;renderFlow();return;}
  if(e.target.id==='fhide'){flowHide=e.target.checked;flowIdx=0;renderFlow();return;}
});
document.querySelectorAll('.pill[data-seg]').forEach(b=>b.addEventListener('click',e=>{
  segment=e.currentTarget.dataset.seg;
  document.querySelectorAll('.pill[data-seg]').forEach(x=>x.classList.toggle('active',x===e.currentTarget));
  renderAll();}));
const _sb=document.getElementById('searchbox');
if(_sb)_sb.addEventListener('input',renderSearch);
// Chart-tooltip (snabb, egen – visar ER + antal inlägg för kvartalet).
(function(){const chart=document.getElementById('chart');if(!chart)return;
  chart.addEventListener('mouseover',e=>{const c=e.target.closest('.cdot');if(!c)return;
    const tip=document.getElementById('charttip');if(!tip)return;
    tip.innerHTML=`<b>${c.dataset.q}</b><span>Antal inlägg: ${c.dataset.n} st</span><span>Eng.rate: ${c.dataset.er}</span>`;
    const r=c.getBoundingClientRect(),cr=chart.getBoundingClientRect();
    tip.style.left=(r.left-cr.left+r.width/2)+'px';
    tip.style.top=(r.top-cr.top-10)+'px';tip.hidden=false;});
  chart.addEventListener('mouseout',e=>{if(e.target.closest('.cdot')){const t=document.getElementById('charttip');if(t)t.hidden=true;}});
})();
// Tidsperiod: datumfält + snabbval. Referens = senaste inläggsdatum i datan.
const _dates=POSTS.map(p=>p.date).filter(Boolean).sort();
const DMIN=_dates[0]||'', DMAX=_dates[_dates.length-1]||'';
function daysAgo(nd){if(!DMAX)return '';const d=new Date(DMAX);d.setDate(d.getDate()-nd);return d.toISOString().slice(0,10);}
function setPeriod(from,to){periodFrom=from||null;periodTo=to||null;
  const pf=document.getElementById('pfrom'),pt=document.getElementById('pto');
  if(pf)pf.value=periodFrom||'';if(pt)pt.value=periodTo||'';
  document.querySelectorAll('.pill[data-period]').forEach(b=>{
    const act=(b.dataset.period==='all'&&!periodFrom&&!periodTo);
    b.classList.toggle('active',act);});
  renderAll();}
(function(){const pf=document.getElementById('pfrom'),pt=document.getElementById('pto');
  if(pf){pf.min=pt.min=DMIN;pf.max=pt.max=DMAX;
    pf.addEventListener('change',()=>setPeriod(pf.value,pt.value));
    pt.addEventListener('change',()=>setPeriod(pf.value,pt.value));}
  document.querySelectorAll('.pill[data-period]').forEach(b=>b.addEventListener('click',()=>{
    const v=b.dataset.period;
    if(v==='all')setPeriod('','');
    else setPeriod(daysAgo(+v),DMAX);}));})();
// "Logga ut" visas bara när sajten serveras (dvs bakom inloggning på Vercel).
if(AUTOSAVE){const _ll=document.getElementById('logoutlink');if(_ll)_ll.hidden=false;}
function setCols(n){n=Math.min(5,Math.max(1,n|0))||3;
  document.documentElement.style.setProperty('--cols',n);
  document.documentElement.dataset.cols=n;
  try{localStorage.setItem('iq_cols',n);}catch(e){}
  document.querySelectorAll('.pill[data-cols]').forEach(b=>b.classList.toggle('active',+b.dataset.cols===n));
  requestAnimationFrame(refreshMore);}
let _initCols=3;try{_initCols=+localStorage.getItem('iq_cols')||3;}catch(e){}
setCols(_initCols);

// Bygg dimensions-sektionerna och rendera.
const host=document.getElementById('dims');
DIMS.forEach(d=>{host.insertAdjacentHTML('beforeend',
  `<section><h2>${d.label}</h2><div id="dim-${d.id}"></div></section>`);});
updateOvBar();
renderAll();
// Flikar + justeringsflöde.
populateFlowFilters();
const _fsearch=document.getElementById('fsearch');
if(_fsearch)_fsearch.addEventListener('input',()=>{flowQuery=_fsearch.value;flowIdx=0;renderFlow();});
let _initTab='insikter';try{_initTab=localStorage.getItem('iq_tab')||'insikter';}catch(e){}
showTab(_initTab);
// När sajten serveras (serve.py lokalt eller Vercel): håll sidan i synk med den
// delade databasen. Hämtas vid start och sedan med jämna mellanrum, så en
// ändring som någon annan gör dyker upp automatiskt – utan att man laddar om.
function syncOverrides(initial){
  if(!AUTOSAVE)return;
  fetch(OV_ENDPOINT).then(r=>r.ok?r.json():{}).then(c=>{
    if(!c||typeof c!=='object')return;
    let changed=false;
    for(const v in c){const cur=OV[v]||{};
      for(const f in c[v]){if(cur[f]!==c[v][f])changed=true;}
      OV[v]=Object.assign(OV[v]||{},c[v]);}
    saveLS();
    if(initial)seedBaseline(c);
    if(!changed&&!initial)return;
    // Stör inte pågående redigering – nästa synk tar det när rutan stängts.
    const busy=!document.getElementById('editor').hidden||(typeof SEL!=='undefined'&&SEL.size>0);
    if(busy&&!initial)return;
    applyOv();updateOvBar();renderAll();
  }).catch(()=>{});
}
// Engångs-sådd: lägg den inbakade baslinjen i databasen om den inte redan finns
// där, så databasen blir den enda sanningen. Skriver bara fält som SAKNAS i KV
// (rör aldrig något som redan ändrats där), så det körs i praktiken bara en gång.
function seedBaseline(kv){
  const disk=window.OVERRIDES||{};const seed=[];
  for(const id in disk){const kf=(kv&&kv[id])||{};
    for(const f in disk[id]){if(!(f in kf))seed.push({id,field:f,value:disk[id][f]});}}
  if(seed.length)pushDeltas(seed,true);
}
syncOverrides(true);
setInterval(()=>{if(document.visibilityState==='visible')syncOverrides(false);},25000);
"""


def main():
    if not os.path.exists(CSV_PATH):
        sys.exit(f"Hittar inte {CSV_PATH}.")
    ov = load_overrides()
    ana = load(CSV_PATH, ov)
    if not ana:
        sys.exit("Inga analyserade rader med visningar hittades.")
    avail = load_availability()
    n_gone = 0
    for r in ana:
        if avail.get(r.get("video_id", "")) == "gone":
            r["_gone"] = True
            n_gone += 1
    if avail:
        print(f"Tillgänglighet: {n_gone} inlägg borttagna/privata på TikTok "
              f"(av {len(avail)} kontrollerade i availability.csv).")
    attach_benchmarks(ana)
    if ov:
        print(f"Tillämpar {sum(len(v) for v in ov.values())} manuella rättelser "
              f"på {len(ov)} inlägg (overrides.csv).")

    org = [r for r in ana if is_organic(r)]
    boost = [r for r in ana if not is_organic(r)]
    has_tone = any(r.get("budskapston") for r in ana)
    has_occasion = any(r.get("hogtid") for r in ana)

    ser_org = months(org)
    slope, verdict = trend(ser_org)
    med_org = statistics.median([r["_er"] for r in org]) if org else 0
    med_boost = statistics.median([r["_er"] for r in boost]) if boost else 0
    lo_org, md_org, hi_org = levels([r["_er"] for r in org])
    lo_bo, md_bo, hi_bo = levels([r["_er"] for r in boost])

    # ---- terminal ----
    print(f"\nAnalyserade inlägg: {len(ana)}  (organiska: {len(org)}, "
          f"boostade: {len(boost)})")
    print("\nNivåer (datadrivet ur den faktiska datan, kvartiler per segment):")
    print(f"  ORGANISKT (n={len(org)}):  lågt < {pct(lo_org)}   "
          f"medel {pct(lo_org)}–{pct(hi_org)} (median {pct(md_org)})   högt > {pct(hi_org)}")
    print(f"  BOOSTAT   (n={len(boost)}):  lågt < {pct(lo_bo)}   "
          f"medel {pct(lo_bo)}–{pct(hi_bo)} (median {pct(md_bo)})   högt > {pct(hi_bo)}")
    print(f"\nTrend (organiskt): {verdict} ({slope:+.3f} pe/månad)")
    if not has_tone:
        print("(Obs: budskapston saknas – kör classify_tone.py för ton/hook.)")
    print(f"\nInteraktiv rapport: {OUT_HTML}")

    # ---- HTML ----
    data = [post_json(r) for r in ana]
    data_js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    css = """
      /* Stil inspirerad av playchipless.com: varmt cream, svart/rust, piller,
         versala spärrade etiketter, mjukt rundade kort. */
      :root{--bg:#efece6;--panel:#f7f5f1;--card:#fbfaf7;--ink:#18140f;
        --muted:#8b857a;--line:#e2ddd3;--accent:#c0562f;--taupe:#8f8275;--cols:3;
        --iq-blue:#1359c5;--iq-pink:#ffaac7;--iq-navy:#242f55;
        --disp:"Onest","Helvetica Neue",Helvetica,Arial,system-ui,sans-serif}
      *{box-sizing:border-box}
      body{font:15px/1.55 "Helvetica Neue",Helvetica,Arial,-apple-system,system-ui,sans-serif;
        margin:0;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased}
      .wrap{max-width:1340px;margin:0 auto;padding:28px 20px 100px}
      .layout{display:grid;grid-template-columns:1fr;gap:0}
      @media(min-width:1080px){
        .layout{grid-template-columns:420px minmax(0,1fr);gap:26px;align-items:start}
        .sidebar{position:sticky;top:20px}
        .sidebar .hero{margin-bottom:0}
        .sidebar .hero h1{font-size:26px;margin-bottom:14px}
        .sidebar .stat{display:grid;grid-template-columns:1fr 1fr;gap:10px}
        .sidebar .stat .c.wide{grid-column:1/-1}
        .sidebar .introw{grid-template-columns:1fr 1fr}
      }
      /* hero / header-band i IQ-marinblått */
      .hero{position:relative;overflow:hidden;background:var(--iq-navy);color:#fff;
        border-radius:24px;padding:26px 30px 22px;margin-bottom:26px}
      .hero::before{content:"";position:absolute;top:-45%;right:-8%;width:440px;height:440px;
        background:radial-gradient(circle,rgba(19,89,197,.75),transparent 68%);pointer-events:none}
      .hero::after{content:"";position:absolute;bottom:-70%;left:28%;width:380px;height:380px;
        background:radial-gradient(circle,rgba(255,170,199,.5),transparent 70%);pointer-events:none}
      .hero>*{position:relative;z-index:1}
      .eyebrow{font-size:12px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;
        color:var(--iq-pink);margin:0 0 8px}
      .hero h1{color:#fff;margin:0 0 18px}
      /* logga ut: diskret länk i sidfoten – ingen behöver den normalt */
      .logout{color:inherit;text-decoration:none;opacity:.8}
      .logout:hover{text-decoration:underline;opacity:1}
      h1{font-family:var(--disp);font-size:40px;line-height:1.02;letter-spacing:-.025em;font-weight:800;margin:0 0 8px}
      @media(max-width:560px){h1{font-size:30px}}
      h2{font-family:var(--disp);font-size:22px;letter-spacing:-.015em;font-weight:800;color:var(--iq-navy);margin:32px 0 12px}
      h3{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
        font-weight:700;margin:16px 0 8px}
      .muted{color:var(--muted)} a{color:inherit}
      /* nyckeltal-kort (i hero) */
      .stat{display:flex;gap:12px;flex-wrap:wrap;margin:0}
      .stat .c{flex:1 1 150px;min-width:0;background:rgba(255,255,255,.10);
        border:1px solid rgba(255,255,255,.20);border-radius:16px;padding:14px 16px;overflow:hidden}
      .stat .c.ac-pink{border-top:3px solid var(--iq-pink)}
      .stat .c.ac-blue{border-top:3px solid var(--iq-blue)}
      .stat .big{font-family:var(--disp);font-size:24px;font-weight:800;letter-spacing:-.02em;
        margin-bottom:2px;color:#fff;line-height:1.1;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
      .stat .muted{font-size:12.5px;color:rgba(255,255,255,.7)}
      /* förenklad trendgraf i dashboarden */
      .trendcard .trendhead{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:8px}
      .trendcard .trendhead .big{font-size:18px;margin:0}
      .trendcard .trendhead .muted{font-size:11px;text-transform:uppercase;letter-spacing:.06em}
      .trendmini{height:64px}
      .trendmini svg{height:64px}
      .trendempty{font-size:11.5px;color:rgba(255,255,255,.5);padding:20px 0;text-align:center}
      /* interaktioner (i hero) – egna rutor, lyft fram */
      .introw-lbl{font-size:11px;text-transform:uppercase;letter-spacing:.11em;font-weight:700;
        color:var(--iq-pink);margin:16px 0 8px}
      .introw{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
      .imc{background:rgba(255,255,255,.10);border:1px solid rgba(255,255,255,.18);
        border-radius:14px;padding:11px 14px;min-width:0;overflow:hidden}
      .imc .imi{width:15px;height:15px;color:rgba(255,255,255,.58);display:inline-flex}.imc .imi svg{width:15px;height:15px}
      .imc .imv{font-family:var(--disp);font-size:19px;font-weight:800;color:#fff;line-height:1.12;
        margin-top:5px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
      .imc .imt{font-size:11.5px;color:rgba(255,255,255,.72);margin-top:1px}
      .hero-foot{font-size:11.5px;color:rgba(255,255,255,.55);margin:16px 0 0;line-height:1.5}
      /* förklaringsruta (hopfällbar) */
      .explain{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:2px 16px;margin:0 0 20px}
      .explain summary{font-family:var(--disp);font-weight:700;color:var(--iq-navy);
        font-size:14.5px;cursor:pointer;padding:12px 0;list-style:none}
      .explain summary::-webkit-details-marker{display:none}
      .explain summary::before{content:"▸ ";color:var(--iq-blue)}
      .explain[open] summary::before{content:"▾ "}
      .explain p{margin:0 0 14px;font-size:13.5px;color:var(--muted);line-height:1.6;max-width:72ch}
      .explain p b{color:var(--ink);font-weight:650}
      /* periodrad */
      .period{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:2px 0 10px}
      .period .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);font-weight:700;margin-right:2px}
      .period .dates{display:inline-flex;align-items:center;gap:7px;font-size:13px;color:var(--muted);margin-left:6px}
      .period input[type=date]{border:1px solid var(--line);border-radius:10px;padding:6px 10px;
        font:inherit;font-size:13px;color:var(--ink);background:var(--panel)}
      .period input[type=date]:focus{outline:none;border-color:var(--iq-blue)}
      /* chart-tooltip */
      .charttip{position:absolute;z-index:10;transform:translate(-50%,-100%);pointer-events:none;
        background:var(--iq-navy);color:#fff;padding:7px 10px;border-radius:9px;font-size:11.5px;
        line-height:1.35;box-shadow:0 8px 20px rgba(0,0,0,.3);white-space:nowrap;
        display:flex;flex-direction:column;gap:1px}
      .charttip b{font-size:12.5px}
      .charttip span{opacity:.82;font-weight:500}
      /* segment-piller */
      .seg{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:8px;
        flex-wrap:wrap;padding:12px 0;
        background:linear-gradient(var(--bg),var(--bg) 72%,transparent)}
      .seg .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.09em;
        color:var(--muted);font-weight:700;margin-right:2px}
      .pill{border:1px solid var(--line);background:transparent;color:var(--ink);
        border-radius:999px;padding:8px 16px;font:inherit;font-size:14px;font-weight:600;
        cursor:pointer;transition:all .15s ease}
      .pill:hover{border-color:var(--iq-blue)}
      .pill.active{background:var(--iq-navy);color:#fff;border-color:var(--iq-navy)}
      .pill.mini{padding:6px 12px;font-size:13px}
      .metricbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 18px}
      .metricbar .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.09em;
        color:var(--muted);font-weight:700;margin-right:2px}
      .rankitem{position:relative}
      .rankval{font-size:12px;font-weight:700;color:var(--ink);margin:0 0 4px 2px}
      .rankval .muted{color:var(--muted);font-weight:600}
      /* tabeller */
      section{overflow-x:auto}
      /* tidsgraf */
      #chart{position:relative;background:var(--card);border:1px solid var(--line);border-radius:18px;
        padding:14px 14px 6px;margin-top:2px}
      #chart svg{display:block;overflow:visible}
      #chart-note{margin-top:14px;background:var(--card);border:1px solid var(--line);
        border-left:3px solid var(--iq-blue);border-radius:12px;padding:11px 15px;
        font-size:13.5px;line-height:1.5}
      #chart-note strong{color:var(--iq-navy)}
      table.bt{border-collapse:collapse;width:100%;font-size:14px;background:var(--card);
        border:1px solid var(--line);border-radius:16px;overflow:hidden;table-layout:fixed}
      table.bt th,table.bt td{padding:11px 12px;text-align:left;
        border-bottom:1px solid var(--line)}
      /* fasta kolumnbredder – annars hoppar kolumnerna när man sorterar om */
      table.bt th.v,table.bt td.v{width:82px;padding-left:8px;padding-right:12px}
      table.bt th.n,table.bt td.n{width:52px}
      table.bt tr.grow td:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      table.bt tbody tr:last-child td{border-bottom:none}
      table.bt th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;
        color:var(--muted);font-weight:700;background:var(--panel)}
      th.v,td.v{text-align:right;white-space:nowrap}
      th.sortable{cursor:pointer;user-select:none} th.sortable:hover{color:var(--iq-navy)}
      tr.grow{cursor:pointer;transition:background .12s} tr.grow:hover{background:#0000000a}
      td.muted{color:var(--muted)}
      .drow td{background:#00000006;padding:6px 12px 14px}
      /* ---- inläggskort ---- */
      .cards{display:grid;grid-template-columns:repeat(var(--cols),minmax(0,1fr));gap:14px;margin:10px 0}
      .cards.col{grid-template-columns:1fr}
      @media(max-width:640px){.cards:not(.col){grid-template-columns:1fr}}
      .pc{position:relative;display:flex;background:var(--card);overflow:hidden;
        border:1px solid var(--line);border-radius:16px;text-decoration:none;
        transition:transform .12s,box-shadow .12s,border-color .12s}
      .pc:hover{transform:translateY(-1px);box-shadow:0 8px 20px #0000000f}
      .pc.sel{border-color:var(--iq-blue);box-shadow:0 0 0 1px var(--iq-blue) inset}
      .selbox{position:absolute;top:9px;left:9px;z-index:3;width:18px;height:18px;
        accent-color:var(--iq-blue);cursor:pointer;border-radius:4px;box-shadow:0 0 0 3px #00000026}
      .selall{margin:2px 0 10px;font-size:12.5px}
      .selall label{display:inline-flex;align-items:center;gap:7px;cursor:pointer;color:var(--muted);font-weight:600}
      .selall input{width:16px;height:16px;accent-color:var(--iq-blue);cursor:pointer}
      .pcimg{flex:0 0 auto;line-height:0;display:block;position:relative}
      .pcimg img{object-fit:cover;background:var(--line);display:block}
      .pcm{min-width:0;flex:1;display:flex;flex-direction:column}
      /* play-knapp över miniatyrerna (signalerar att videon spelas upp på sidan) */
      .playbtn{position:absolute;top:50%;left:50%;width:46px;height:46px;transform:translate(-50%,-50%);
        border-radius:999px;background:rgba(18,20,26,.52);backdrop-filter:blur(2px);pointer-events:none;
        display:flex;align-items:center;justify-content:center;opacity:.92;transition:transform .12s,background .12s;z-index:3}
      .playbtn::before{content:"";border-style:solid;border-width:9px 0 9px 15px;
        border-color:transparent transparent transparent #fff;margin-left:3px}
      .pcimg:hover .playbtn,.flowimg:hover .playbtn{background:var(--iq-blue);transform:translate(-50%,-50%) scale(1.08);opacity:1}
      .pc.compact .playbtn{width:34px;height:34px}
      .pc.compact .playbtn::before{border-width:7px 0 7px 11px}
      /* borttagna/privata videor: dämpad miniatyr + litet märke */
      .pcimg.gone img,.flowimg.gone img{filter:grayscale(1) brightness(.62)}
      .goneflag{position:absolute;top:8px;left:8px;z-index:3;background:rgba(18,20,26,.82);color:#fff;
        font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;
        padding:3px 7px;border-radius:7px;line-height:1;pointer-events:none}
      /* lightbox-spelare */
      body.noscroll{overflow:hidden}
      .lb{position:fixed;inset:0;z-index:60;display:flex;align-items:center;justify-content:center;padding:24px}
      .lb[hidden]{display:none}
      .lb-backdrop{position:absolute;inset:0;background:rgba(10,12,18,.82);backdrop-filter:blur(3px)}
      .lb-inner{position:relative;z-index:1;display:flex;flex-direction:column;align-items:center;gap:12px;
        width:min(92vw,340px)}
      .lb-frame{width:100%;aspect-ratio:9/16;max-height:78vh;border-radius:16px;overflow:hidden;
        background:#000;box-shadow:0 30px 80px rgba(0,0,0,.5)}
      .lb-frame iframe{width:100%;height:100%;border:0;display:block}
      .lb-gone{width:100%;height:100%;position:relative;display:flex;align-items:flex-end}
      .lb-gone img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;
        filter:grayscale(1) brightness(.4)}
      .lb-gone-msg{position:relative;z-index:1;padding:20px 18px;color:#fff;
        background:linear-gradient(transparent,rgba(10,12,18,.9))}
      .lb-gone-msg strong{display:block;font-family:var(--disp);font-size:16px;margin-bottom:5px}
      .lb-gone-msg p{margin:0;font-size:12.5px;color:rgba(255,255,255,.8);line-height:1.5}
      .lb-x{position:absolute;top:-44px;right:-4px;width:36px;height:36px;border-radius:999px;border:none;
        background:rgba(255,255,255,.14);color:#fff;font-size:17px;cursor:pointer;line-height:1}
      .lb-x:hover{background:rgba(255,255,255,.28)}
      .lb-open{color:#fff;font-size:13.5px;font-weight:700;text-decoration:none;opacity:.85;
        border:1px solid rgba(255,255,255,.3);border-radius:999px;padding:7px 15px}
      .lb-open:hover{opacity:1;background:rgba(255,255,255,.12)}
      @media(max-width:560px){.lb-x{top:-42px}}
      /* ER-badge (bara siffran; "viktad ER" vid hover) + median-chip */
      .er{position:absolute;background:var(--iq-blue);color:#fff;border-radius:12px;
        padding:7px 11px;box-shadow:0 6px 16px rgba(19,89,197,.42);line-height:1}
      .er b{font-family:var(--disp);font-weight:800;font-size:21px;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
      .bench{position:absolute;font-size:11px;font-weight:750;padding:5px 11px;border-radius:999px;
        line-height:1.5;white-space:nowrap;font-variant-numeric:tabular-nums}
      .bench.pos{background:var(--iq-pink);color:var(--iq-navy)}
      .bench.mid{background:#ffffffe6;color:var(--iq-navy)}
      .bench.neg{background:rgba(36,47,85,.78);color:#fff}
      .er,.bench{cursor:default}
      .tip{position:absolute;bottom:calc(100% + 8px);z-index:6;width:max-content;max-width:210px;
        background:var(--iq-navy);color:#fff;font-weight:600;font-size:11.5px;line-height:1.35;
        padding:8px 10px;border-radius:9px;box-shadow:0 8px 20px rgba(0,0,0,.3);opacity:0;
        transform:translateY(3px);transition:opacity .13s,transform .13s;pointer-events:none;text-align:left;white-space:normal}
      .tip i{display:block;font-style:normal;font-weight:450;opacity:.82;margin-top:3px;font-size:10.5px}
      .tip-l{left:0} .tip-r{right:0}
      .er:hover .tip,.bench:hover .tip{opacity:1;transform:translateY(0)}
      /* mått med symboler */
      .metrics{display:flex;flex-wrap:wrap;gap:12px;color:var(--ink)}
      .m{display:inline-flex;align-items:center;gap:5px;font-size:13px;font-weight:650;font-variant-numeric:tabular-nums}
      .mi{width:15px;height:15px;color:var(--muted);display:inline-flex} .mi svg{width:15px;height:15px}
      .pcc{font-size:12.5px;color:var(--muted);line-height:1.45;margin-top:8px;white-space:normal;
        overflow:hidden;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;
        min-height:calc(1.45em * 2)}
      .pc.expanded .pcc{-webkit-line-clamp:unset;overflow:visible;min-height:0}
      .morebtn{align-self:flex-start;margin-top:2px;border:none;background:none;color:var(--iq-blue);
        font:inherit;font-size:12px;font-weight:700;cursor:pointer;padding:2px 0}
      .morebtn:hover{text-decoration:underline}
      .foot{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-top:auto;padding-top:9px}
      .metaw{display:flex;flex-direction:column;gap:1px;min-width:0}
      .metaw .l1{font-size:11.5px;color:var(--muted);font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
      .metaw .l2{font-size:11px;color:var(--muted);font-variant-numeric:tabular-nums}
      .editbtn{flex:0 0 auto;border:1px solid var(--line);background:transparent;color:var(--iq-blue);
        font:inherit;font-size:11.5px;font-weight:700;padding:6px 12px;border-radius:999px;cursor:pointer;white-space:nowrap;margin:0}
      .editbtn:hover{border-color:var(--iq-blue)}
      .ovchip{position:absolute;top:9px;right:9px;z-index:3;background:var(--iq-pink);color:var(--iq-navy);
        font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;padding:3px 8px;border-radius:999px}
      .ovmark{font-size:10px;color:var(--iq-blue);font-weight:800;text-transform:uppercase;letter-spacing:.04em}
      /* rutnät (dims + sök): bild överst, ER/median överlagrade på tumnageln */
      .cards:not(.col) .pc{flex-direction:column;height:100%}
      .cards:not(.col) .pcimg{width:100%}
      .cards:not(.col) .pcimg img{width:100%;aspect-ratio:3/4;max-height:360px}
      .cards:not(.col) .pcoverlay{position:absolute;left:0;right:0;bottom:0;display:flex;
        align-items:flex-end;justify-content:space-between;gap:8px;padding:10px}
      .cards:not(.col) .er,.cards:not(.col) .bench{position:relative;bottom:auto;left:auto;right:auto}
      .cards:not(.col) .bench{max-width:calc(100% - 92px)}
      .cards:not(.col) .pcm{padding:11px 13px 12px}
      :root[data-cols="1"] .cards:not(.col) .pcimg img{max-height:460px}
      /* kompakta listor (topplistor + manuellt ändrade): bild vänster, ER inline */
      .pc.compact{flex-direction:row;gap:12px;padding:10px;align-items:stretch}
      .pc.compact .pcimg{align-self:stretch}
      .pc.compact .pcimg img{width:92px;height:100%;min-height:122px;border-radius:10px;object-fit:cover}
      .pc.compact .pcm{padding:0}
      .erline{display:flex;align-items:center;flex-wrap:wrap;gap:8px}
      .pc.compact .er{position:relative;box-shadow:none;padding:4px 9px;border-radius:9px}
      .pc.compact .er b{font-size:16px}
      .pc.compact .bench{position:relative}
      .pc.compact .tip{bottom:auto;top:calc(100% + 8px)}
      .pc.compact .pcc{-webkit-line-clamp:3;min-height:calc(1.45em * 3);margin-top:7px}
      .two{display:flex;gap:20px;flex-wrap:wrap} .two>div{flex:1;min-width:300px}
      /* topplistorna: egen responsiv rutnät som fyller hela bredden oavsett
         kolumnvalet – kompaktkorten (bild + text) behöver ~270px för att se bra ut */
      .ranklist{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(270px,100%),1fr));
        gap:12px;margin:10px 0 22px}
      .rankitem{display:flex;flex-direction:column;height:100%}
      .rankitem>.pc{flex:1}
      .ranksec{margin-bottom:10px}
      .rankhead{font-family:var(--disp);font-size:19px;font-weight:800;letter-spacing:-.01em;
        color:var(--iq-navy);margin:20px 0 4px}
      /* flikar + panel som tydligt hänger ihop */
      .tabs{display:flex;gap:5px;margin:4px 0 0;padding:0 4px}
      .tab{border:1px solid transparent;border-bottom:none;background:none;font-family:var(--disp);
        font-size:16px;font-weight:700;color:var(--muted);padding:12px 20px 13px;cursor:pointer;
        border-radius:13px 13px 0 0}
      .tab:hover{color:var(--ink)}
      .tab.active{color:var(--iq-navy);background:var(--panel);border-color:var(--line)}
      .tabpanel{background:var(--panel);border:1px solid var(--line);border-radius:16px;
        padding:22px 24px;margin-bottom:24px}
      .tabpanel[hidden]{display:none}
      .tabpanel>section:first-child>h2:first-child,.tabpanel>h2:first-child,
      .tabpanel>:first-child{margin-top:0}
      .tabpanel>:last-child{margin-bottom:0}
      /* justeringsflöde */
      .flowbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 12px}
      .flowbar .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);font-weight:700}
      .flowbar select,.flowbar input[type=search]{border:1px solid var(--line);border-radius:10px;
        padding:7px 10px;font:inherit;font-size:13px;background:var(--panel);color:var(--ink)}
      .flowbar input[type=search]{min-width:200px}
      .flowbar select:focus,.flowbar input:focus{outline:none;border-color:var(--iq-blue)}
      .fhide{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--muted);cursor:pointer;margin-left:8px}
      .fhide input{width:16px;height:16px;accent-color:var(--iq-blue);cursor:pointer}
      .flowhead{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:14px;color:var(--muted);margin:4px 0 12px}
      .flowhead b{color:var(--ink)} .flowhead .dot{color:var(--line)}
      .flowbadge{font-size:10px;font-weight:800;text-transform:uppercase;letter-spacing:.04em;padding:3px 9px;border-radius:999px}
      .flowbadge.adj{background:var(--iq-pink);color:var(--iq-navy)}
      .flowbadge.rev{background:#5f7d5c22;color:#4a6647}
      .flowcard{display:flex;gap:18px;align-items:flex-start;background:var(--card);
        border:1px solid var(--line);border-radius:18px;padding:16px}
      .flowimg{position:relative;flex:0 0 auto;line-height:0;display:block}
      .flowimg img{width:190px;aspect-ratio:3/4;object-fit:cover;border-radius:14px;background:var(--line)}
      .flowimg .er{position:absolute;bottom:8px;left:8px;background:var(--iq-blue);color:#fff;
        border-radius:11px;padding:6px 10px;line-height:1}
      .flowimg .er b{font-family:var(--disp);font-size:18px;font-weight:800;font-variant-numeric:tabular-nums}
      .flowimg .bench{position:absolute;bottom:8px;right:8px}
      .flowbody{flex:1;min-width:0;display:flex;flex-direction:column;gap:10px}
      .flowmeta{font-size:12.5px;color:var(--muted)} .flowmeta a{color:var(--iq-blue);font-weight:700}
      .flowcap{font-size:14px;color:var(--ink);line-height:1.5}
      .flowfields{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px 16px;margin-top:2px}
      .ffield label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.07em;
        color:var(--muted);font-weight:700;margin-bottom:5px}
      .ffield select{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:10px;
        background:var(--panel);font:inherit;font-size:14px;color:var(--ink)}
      .ffield select:focus{outline:none;border-color:var(--iq-blue)}
      .fhint{display:inline-flex;align-items:center;justify-content:center;width:15px;height:15px;
        border-radius:50%;background:var(--line);color:var(--muted);font-size:10px;font-weight:800;cursor:help}
      .flowhook{font-size:12px;color:var(--muted);background:var(--panel);border-left:3px solid var(--iq-pink);
        border-radius:8px;padding:9px 12px;margin:4px 0 0;line-height:1.5}
      .flowactions{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
      .flowactions .pill[disabled]{opacity:.4;cursor:default}
      @media(max-width:560px){.flowcard{flex-direction:column}.flowimg img{width:150px}}
      .ovitem{margin-bottom:10px}
      .ovchg{font-size:12px;color:var(--iq-blue);margin:4px 0 0 114px;font-weight:600}
      .ovdetails{margin-top:34px;border-top:1px solid var(--line);padding-top:14px}
      .ovdetails summary{font-family:var(--disp);font-size:22px;font-weight:800;letter-spacing:-.015em;
        cursor:pointer;list-style:none;margin-bottom:10px}
      .ovdetails summary::-webkit-details-marker{display:none}
      .ovdetails summary::before{content:'▸ ';color:var(--muted);font-weight:400}
      .ovdetails[open] summary::before{content:'▾ '}
      /* sök i beskrivningar */
      #searchbox{width:100%;max-width:520px;padding:11px 14px;border:1px solid var(--line);
        border-radius:12px;background:var(--panel);font:inherit;font-size:15px;color:var(--ink)}
      #searchbox:focus{outline:none;border-color:var(--iq-blue)}
      #searchinfo{font-size:13px;color:var(--muted);margin:10px 0 2px}
      /* redigering av taggar: redigeraren (modal) */
      .modal{position:fixed;inset:0;background:#00000066;display:flex;
        align-items:center;justify-content:center;z-index:50;padding:16px}
      .modal[hidden]{display:none}
      .sheet{background:var(--panel);border:1px solid var(--line);border-radius:20px;
        padding:20px;max-width:420px;width:100%;box-shadow:0 24px 60px #00000030}
      .mh{display:flex;justify-content:space-between;align-items:center}
      .mh b{font-size:17px} .x{border:none;background:none;font-size:18px;
        cursor:pointer;color:var(--muted)}
      #edCap{font-size:13px;color:var(--muted);margin:6px 0 4px}
      #edFields{display:flex;flex-direction:column;gap:12px;margin:14px 0}
      #edFields label{font-size:11px;text-transform:uppercase;letter-spacing:.08em;
        color:var(--muted);font-weight:700;display:block;margin-bottom:4px}
      #edFields select{width:100%;padding:9px 10px;border:1px solid var(--line);
        border-radius:10px;background:var(--card);font:inherit;font-size:14px;color:var(--ink)}
      .mfoot{display:flex;justify-content:flex-end;gap:8px}
      .pill.dark{background:var(--ink);color:#fff;border-color:var(--ink)}
      .ovbar{position:fixed;left:0;right:0;bottom:0;background:var(--card);
        border-top:1px solid var(--line);padding:9px 16px;display:flex;
        align-items:center;gap:10px;flex-wrap:wrap;justify-content:center;
        font-size:13px;z-index:20}
      .ovbar[hidden]{display:none}
      .ovbar .pill{padding:6px 12px;font-size:13px}
      .ovbar select{padding:6px 9px;border:1px solid var(--line);border-radius:9px;
        background:var(--card);font:inherit;font-size:13px;color:var(--ink);cursor:pointer}
      .ovbar .sep{color:var(--line)}
      /* liten självslocknande spar-notis */
      .toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(8px);
        background:var(--iq-navy);color:#fff;padding:10px 18px;border-radius:999px;
        font-size:13px;font-weight:600;letter-spacing:.01em;box-shadow:0 12px 30px rgba(0,0,0,.25);
        opacity:0;pointer-events:none;transition:opacity .2s,transform .2s;z-index:50}
      .toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
    """
    def stat(v, l, ac=""):
        cls = "c" + (" " + ac if ac else "")
        return f'<div class="{cls}"><div class="big">{v}</div><div class="muted">{l}</div></div>'

    header = (f'<header class="hero">'
              f'<p class="eyebrow">Innehåll × engagemang</p>'
              f'<h1>IQ × TikTok Dashboard</h1>'
              f'<div class="stat" id="statrow"></div>'
              f'<p class="introw-lbl">Interaktioner</p>'
              f'<div class="introw" id="introw"></div>'
              f'<p class="hero-foot">{len(ana)} analyserade inlägg · '
              f'översikten och allt nedanför följer segment- och periodvalet'
              f'<a href="/logout" id="logoutlink" class="logout" hidden> · logga ut</a></p>'
              f'</header>')

    seg = ('<div class="seg"><span class="lbl">Segment</span>'
           '<button class="pill active" data-seg="boost">Boostat</button>'
           '<button class="pill" data-seg="org">Organiskt</button>'
           '<button class="pill" data-seg="alla">Alla</button>'
           '<span class="lbl" style="margin-left:18px">Kolumner</span>'
           + "".join(f'<button class="pill mini" data-cols="{i}">{i}</button>'
                     for i in range(1, 6))
           + '</div>'
           '<div class="period"><span class="lbl">Period</span>'
           '<button class="pill mini active" data-period="all">Allt</button>'
           '<button class="pill mini" data-period="90">Senaste 90 dagar</button>'
           '<button class="pill mini" data-period="365">Senaste 12 mån</button>'
           '<span class="dates">Från <input type="date" id="pfrom"> till '
           '<input type="date" id="pto"></span></div>')

    tabs = ('<div class="tabs" role="tablist">'
            '<button class="tab active" data-tab="insikter">Insikter</button>'
            '<button class="tab" data-tab="topp">Topplistor</button>'
            '<button class="tab" data-tab="sok">Sök</button>'
            '<button class="tab" data-tab="justering">Justering</button></div>')

    # Tidsgrafen ritas av JS-appen (uppdateras med segment-/periodvalet).
    charts = ('<p id="levels" class="muted"></p>'
              '<section><h2 id="chart-title">Engagemang över tid</h2>'
              '<div id="chart"></div><p id="chart-note" class="muted"></p></section>')

    explain = ('<details class="explain"><summary>Vad är viktad engagement rate?</summary>'
               '<p><b>Viktad ER</b> = (likes + kommentarer×5 + delningar×10 + favoriter×5) '
               '/ visningar. Kommentarer, delningar och sparningar väger tyngre än likes '
               'eftersom de kräver mer av tittaren. Siffran visas som <b>median per grupp</b> '
               '(mindre känslig för enstaka virala toppar än ett snitt).</p></details>')

    insikter_panel = f'<div class="tabpanel" data-tab="insikter">{explain}{charts}<div id="dims"></div></div>'

    topp_panel = (f'<div class="tabpanel" data-tab="topp" hidden>'
                  f'<section><h2>Topplistor</h2>'
                  f'<p class="muted">Välj vad som ska rangordnas. För viktad ER kan du växla '
                  f'mellan <b>Högst</b> och <b>Lägst engagemang</b> (filtrerat till ≥ {3000} visningar för '
                  f'stabilare siffror); för delningar, kommentarer m.m. visas flest. '
                  f'Klicka för att öppna på TikTok.</p><div id="rank"></div></section></div>')

    search = ('<section><h2>Sök i beskrivningar</h2>'
              '<p class="muted">Skriv ett nyckelord – listar alla inlägg vars beskrivning '
              'innehåller det (följer segment- och periodvalet).</p>'
              '<input id="searchbox" type="search" autocomplete="off" '
              'placeholder="t.ex. valborg, promille, tips …">'
              '<div id="searchinfo" class="muted"></div>'
              '<div id="searchres"></div></section>')

    sok_panel = f'<div class="tabpanel" data-tab="sok" hidden>{search}</div>'

    justering = ('<section><h2>Justering</h2>'
                 '<p class="muted">Gå igenom inläggen ett i taget och rätta taggar som blivit fel. '
                 'Ändringar sparas automatiskt. Filtrera på om de justerats manuellt eller inte, '
                 'bocka av dem du gått igenom, och hoppa vidare till nästa.</p>'
                 '<div class="flowbar"><span class="lbl">Visa</span>'
                 '<button class="pill mini active" data-fstatus="all">Alla</button>'
                 '<button class="pill mini" data-fstatus="unadj">Ej justerade</button>'
                 '<button class="pill mini" data-fstatus="adj">Justerade</button>'
                 '<label class="fhide"><input type="checkbox" id="fhide"> Dölj granskade</label></div>'
                 '<div class="flowbar"><span class="lbl">Fält</span>'
                 '<select id="ffield"></select><select id="fval" disabled></select>'
                 '<input id="fsearch" type="search" autocomplete="off" placeholder="Sök i beskrivning"></div>'
                 '<div id="flow"></div></section>')

    justering_panel = f'<div class="tabpanel" data-tab="justering" hidden>{justering}</div>'

    editor = ('<div id="editor" class="modal" hidden><div class="sheet">'
              '<div class="mh"><b>Ändra taggar</b>'
              '<button class="x" id="edClose">✕</button></div>'
              '<div id="edCap"></div><div id="edFields"></div>'
              '<div class="mfoot"><button class="pill" id="edCancel">Avbryt</button>'
              '<button class="pill dark" id="edSave">Spara</button></div>'
              '</div></div>')
    lightbox = ('<div id="lightbox" class="lb" hidden>'
                '<div class="lb-backdrop"></div>'
                '<div class="lb-inner">'
                '<button class="lb-x" id="lbClose" title="Stäng (Esc)">✕</button>'
                '<div class="lb-frame" id="lbFrame"></div>'
                '<a class="lb-open" id="lbOpen" target="_blank" rel="noopener">Öppna på TikTok ↗</a>'
                '</div></div>')
    ovbar = ('<div class="ovbar" id="bulkbar" hidden></div>'
             '<div class="ovbar" id="ovbar" hidden></div>'
             '<div class="toast" id="toast"></div>')

    ov_js = json.dumps(ov, ensure_ascii=False).replace("</", "<\\/")
    edit_js = json.dumps(EDITABLE, ensure_ascii=False).replace("</", "<\\/")

    doc = (f'<!doctype html><html lang="sv"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<meta name="robots" content="noindex, nofollow, noarchive, nosnippet">'
           f'<title>IQ × TikTok Dashboard</title>'
           f'<link rel="preconnect" href="https://fonts.googleapis.com">'
           f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
           f'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
           f'family=Onest:wght@400;500;600;700;800&display=swap">'
           f'<style>{css}</style>'
           f'</head><body><div class="wrap"><div class="layout">'
           f'<aside class="sidebar">{header}</aside>'
           f'<main class="main">{seg}{tabs}{insikter_panel}{topp_panel}{sok_panel}{justering_panel}</main>'
           f'</div></div>{editor}{lightbox}{ovbar}'
           f'<script>window.POSTS={data_js};window.HAS_TONE={str(has_tone).lower()};'
           f'window.HAS_OCCASION={str(has_occasion).lower()};'
           f'window.OVERRIDES={ov_js};window.EDITABLE={edit_js};</script>'
           f'<script>{APP_JS}</script></body></html>')
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(doc)


if __name__ == "__main__":
    main()
