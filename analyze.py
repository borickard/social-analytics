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
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(PROJECT_DIR, "iq_tiktok_data", "iq_tiktok_enriched.csv")
CSV_PATH = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
OUT_HTML = os.path.join(os.path.dirname(CSV_PATH) or ".", "iq_analys.html")


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


def load(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ana = []
    for r in rows:
        if not r.get("format"):
            continue
        er = weighted_er(r)
        if er is None:
            continue
        r["_er"] = er
        r["_views"] = pick(r, "visningar_exakt", "visningar")
        ana.append(r)
    return ana


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
        "thumb": r.get("thumbnail", "") or f"thumbnails/{vid}.jpg",
        "caption": (r.get("caption", "") or "")[:180],
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
        "organic": is_organic(r),
        "er": round(r["_er"], 6),
        "views": int(r["_views"]),
        "date": (r.get("publiceringsdatum", "") or "")[:10],
    }


APP_JS = r"""
const fmtPct = e => (e*100).toFixed(2).replace('.',',')+' %';
const fmtNum = n => Math.round(n).toLocaleString('sv-SE');
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

function segPosts(){return POSTS.filter(p=>segment==='alla'?true:segment==='org'?p.organic:!p.organic);}
function hookKey(p){if(p.har_hook)return p.har_hook==='ja'?'hook':'ingen hook';
  if(!p.hook_typ)return '';return p.hook_typ==='ovrigt'?'ingen/oklar hook':'hook: '+p.hook_typ;}

const DIMS = [
  {id:'kategori',label:'Kategori',key:p=>[p.kategori]},
  {id:'format',label:'Format',key:p=>[p.format]},
  {id:'typ',label:'Typ (video/bild)',key:p=>[p.typ]},
  {id:'hook',label:'Hook',key:p=>[hookKey(p)]},
  {id:'cta',label:'Call to action',key:p=>[p.har_cta==='ja'?'CTA':'ingen CTA']},
  {id:'alk',label:'Alkohol i bild',key:p=>['alkohol: '+p.alk]},
  {id:'alkkontext',label:'Alkohol-kontext',key:p=>[p.alkkontext]},
];
if(window.HAS_TONE){
  DIMS.splice(5,0,{id:'ton',label:'Budskapston (budskap vs lättsamt)',key:p=>[p.budskapston]},
                 {id:'teman',label:'Budskap-teman',key:p=>p.teman});
}
if(window.HAS_OCCASION){
  DIMS.push({id:'hogtid',label:'Högtid / tillfälle',key:p=>p.hogtid?[p.hogtid]:[]});
}

const sortState = {};   // dimId -> {col, dir}
const openState = {};   // dimId -> Set av öppna grupper

function groups(posts, keyfn){
  const g={};
  posts.forEach(p=>keyfn(p).forEach(k=>{if(k){(g[k]=g[k]||[]).push(p);}}));
  return Object.entries(g).map(([k,ps])=>({k,n:ps.length,
      med:median(ps.map(p=>p.er)),avg:mean(ps.map(p=>p.er)),
      views:median(ps.map(p=>p.views)),posts:ps})).filter(x=>x.n>=MIN_N);
}

const COLS=[{k:'k',t:'Grupp',num:false},{k:'n',t:'n',num:true},
  {k:'med',t:'Median ER',num:true},{k:'avg',t:'Medel ER',num:true},
  {k:'views',t:'Median visn.',num:true}];

function renderDim(dim){
  const st = sortState[dim.id] || (sortState[dim.id]={col:'med',dir:-1});
  const open = openState[dim.id] || (openState[dim.id]=new Set());
  let rows = groups(segPosts(), dim.key);
  rows.sort((a,b)=>{const c=st.col;const va=a[c],vb=b[c];
    return (va<vb?-1:va>vb?1:0)*st.dir;});
  let h = '<table class="bt"><thead><tr>';
  COLS.forEach(c=>{const arrow=st.col===c.k?(st.dir<0?' ▾':' ▴'):'';
    h+=`<th class="${c.num?'v':''} sortable" data-dim="${dim.id}" data-col="${c.k}">${c.t}${arrow}</th>`;});
  h+='</tr></thead><tbody>';
  rows.forEach(r=>{
    const isopen=open.has(r.k);
    h+=`<tr class="grow" data-dim="${dim.id}" data-k="${esc(r.k)}">`+
       `<td>${isopen?'▾ ':'▸ '}${esc(r.k)}</td>`+
       `<td class="v">${r.n}</td><td class="v">${fmtPct(r.med)}</td>`+
       `<td class="v muted">${fmtPct(r.avg)}</td>`+
       `<td class="v muted">${fmtNum(r.views)}</td></tr>`;
    if(isopen){
      const ps=[...r.posts].sort((a,b)=>b.er-a.er);
      h+=`<tr class="drow"><td colspan="5"><div class="cards">${ps.map(card).join('')}</div></td></tr>`;
    }
  });
  h+='</tbody></table>';
  if(!rows.length) h='<p class="muted">För få inlägg i detta segment.</p>';
  document.getElementById('dim-'+dim.id).innerHTML=h;
}

function card(p){
  return `<a class="pc" href="${esc(p.url)}" target="_blank" rel="noopener">`+
    `<img loading="lazy" src="${esc(p.thumb)}" alt="">`+
    `<div class="pcm"><div class="pcer ${p.band==='hög'?'er-hi':p.band==='låg'?'er-lo':''}">${fmtPct(p.er)} `+
    `<span class="badge ${p.organic?'o':'b'}">${p.organic?'org':'boost'}</span> `+
    `<span class="lvl">${p.band}</span></div>`+
    `<div class="muted">${fmtNum(p.views)} visn. · ${esc(p.typ)}/${esc(p.kategori)} · ${esc(p.date)}</div>`+
    `<div class="pcc">${esc(p.caption)}</div></div></a>`;
}

function renderRank(){
  const mk=(posts,best)=>{const c=posts.filter(p=>p.views>=MIN_VIEWS)
      .sort((a,b)=>best?b.er-a.er:a.er-b.er).slice(0,10);
    return c.length?`<div class="cards col">${c.map(card).join('')}</div>`:'<p class="muted">Inga inlägg.</p>';};
  const org=POSTS.filter(p=>p.organic), bo=POSTS.filter(p=>!p.organic);
  document.getElementById('rank').innerHTML=
    `<div class="two"><div><h3>Starkast – organiskt (${org.length})</h3>${mk(org,true)}</div>`+
    `<div><h3>Starkast – boostat (${bo.length})</h3>${mk(bo,true)}</div></div>`+
    `<div class="two"><div><h3>Svagast – organiskt</h3>${mk(org,false)}</div>`+
    `<div><h3>Svagast – boostat</h3>${mk(bo,false)}</div></div>`;
}

function quartersAgg(posts){
  const m={};
  posts.forEach(p=>{const d=p.date||'';if(d.length>=7){const y=+d.slice(0,4),mo=+d.slice(5,7);
    if(y&&mo){const q=((mo-1)/3|0)+1,key=y*10+q;
      (m[key]=m[key]||{lbl:'Q'+q+'-'+String(y).slice(2),ers:[]}).ers.push(p.er);}}});
  return Object.keys(m).map(Number).sort((a,b)=>a-b).map(k=>[m[k].lbl,median(m[k].ers),m[k].ers.length]);
}
function trendOf(series){
  if(series.length<3)return[0,'för få kvartal'];
  const pts=series.map((s,i)=>[i,s[1]*100]),n=pts.length;
  const mx=pts.reduce((a,p)=>a+p[0],0)/n,my=pts.reduce((a,p)=>a+p[1],0)/n;
  let nu=0,de=0;pts.forEach(p=>{nu+=(p[0]-mx)*(p[1]-my);de+=(p[0]-mx)**2;});
  const sl=de?nu/de:0;
  return[sl,sl>0.05?'uppåt ↗':sl<-0.05?'nedåt ↘':'stabilt →'];
}
function renderChart(){
  const series=quartersAgg(segPosts());
  const lbl={alla:'alla',org:'organiskt',boost:'boostat'}[segment];
  document.getElementById('chart-title').textContent='Engagemang över tid ('+lbl+', per kvartal)';
  const L=levelsOf(segPosts());
  const lv=document.getElementById('levels');
  if(lv)lv.innerHTML=`Nivåer för <b>${lbl}</b> (datadrivet, kvartiler): lågt &lt; ${fmtPct(L.q1)} · medel · högt &gt; ${fmtPct(L.q3)} — median ${fmtPct(L.med)}.`;
  const note=document.getElementById('chart-note');
  if(series.length<2){document.getElementById('chart').innerHTML='<p class="muted">För få kvartal i detta segment.</p>';note.textContent='';return;}
  const W=920,H=300,pl=54,pr=60,pt=24,pb=38;
  const ys=series.map(s=>s[1]*100),ymax=Math.max(...ys)*1.18||1;
  const X=i=>pl+i*(W-pl-pr)/(series.length-1),Y=v=>H-pb-(v/ymax)*(H-pt-pb);
  const pctLbl=v=>v.toFixed(2).replace('.',',')+' %';
  // horisontellt rutnät + y-etiketter
  let grid='',ystep=ymax<=6?1:ymax<=14?2:5;
  for(let t=0;t<=ymax;t+=ystep){const y=Y(t);
    grid+=`<line x1="${pl}" y1="${y}" x2="${W-pr}" y2="${y}" stroke="#e2ddd3"/>`+
      `<text x="${pl-8}" y="${y+4}" text-anchor="end" font-size="10" fill="#8b857a">${(''+t).replace('.',',')} %</text>`;}
  // vertikala linjer per kvartal (punkt → baslinje) + värde-etikett per punkt
  let vl='',vlab='';
  series.forEach((s,i)=>{const x=X(i),y=Y(ys[i]);
    vl+=`<line x1="${x.toFixed(1)}" y1="${y.toFixed(1)}" x2="${x.toFixed(1)}" y2="${H-pb}" stroke="#d8d2c7" stroke-width="1"/>`;
    vlab+=`<text x="${x.toFixed(1)}" y="${(y-9).toFixed(1)}" text-anchor="middle" font-size="9.5" fill="#18140f">${pctLbl(ys[i])}</text>`;});
  // nivålinjer (lågt/median/högt)
  let gu='';[[L.q1*100,'lågt','#c0562f'],[L.med*100,'median','#8f8275'],[L.q3*100,'högt','#5f7d5c']].forEach(g=>{if(g[0]>0&&g[0]<=ymax){const y=Y(g[0]);
    gu+=`<line x1="${pl}" y1="${y}" x2="${W-pr}" y2="${y}" stroke="${g[2]}" stroke-dasharray="5 3" stroke-width="1.2"/>`+
      `<text x="${W-pr+4}" y="${y+4}" font-size="10" fill="${g[2]}">${g[1]}</text>`;}});
  const line=series.map((s,i)=>`${X(i).toFixed(1)},${Y(ys[i]).toFixed(1)}`).join(' ');
  const st=Math.max(1,Math.ceil(series.length/20));let xl='';
  for(let i=0;i<series.length;i+=st)xl+=`<text x="${X(i).toFixed(1)}" y="${H-pb+18}" text-anchor="middle" font-size="10" fill="currentColor">${series[i][0]}</text>`;
  const dots=series.map((s,i)=>`<circle cx="${X(i).toFixed(1)}" cy="${Y(ys[i]).toFixed(1)}" r="2.8" fill="#c0562f"><title>${s[0]}: ${ys[i].toFixed(2)} % (n=${s[2]})</title></circle>`).join('');
  document.getElementById('chart').innerHTML=`<svg viewBox="0 0 ${W} ${H}" width="100%" style="width:100%">${grid}${vl}${gu}<polyline points="${line}" fill="none" stroke="#18140f" stroke-width="2"/>${dots}${vlab}${xl}</svg>`;
  const tr=trendOf(series);
  note.innerHTML=`Trend: <strong>${tr[1]}</strong> (${tr[0]>=0?'+':''}${tr[0].toFixed(3)} procentenheter/kvartal). Median-ER per kvartal, n per punkt vid hover.`;
}
function renderAll(){renderChart();DIMS.forEach(renderDim);renderRank();}

document.addEventListener('click',e=>{
  const th=e.target.closest('th.sortable');
  if(th){const d=th.dataset.dim,c=th.dataset.col;const st=sortState[d];
    if(st.col===c)st.dir*=-1;else{st.col=c;st.dir=(c==='k')?1:-1;}renderDim(DIMS.find(x=>x.id===d));return;}
  const gr=e.target.closest('tr.grow');
  if(gr){const d=gr.dataset.dim,k=gr.dataset.k;const s=openState[d];
    s.has(k)?s.delete(k):s.add(k);renderDim(DIMS.find(x=>x.id===d));return;}
});
document.querySelectorAll('.pill[data-seg]').forEach(b=>b.addEventListener('click',e=>{
  segment=e.currentTarget.dataset.seg;
  document.querySelectorAll('.pill[data-seg]').forEach(x=>x.classList.toggle('active',x===e.currentTarget));
  renderAll();}));

// Bygg dimensions-sektionerna och rendera.
const host=document.getElementById('dims');
DIMS.forEach(d=>{host.insertAdjacentHTML('beforeend',
  `<section><h2>${d.label}</h2><div id="dim-${d.id}"></div></section>`);});
renderAll();
"""


def main():
    if not os.path.exists(CSV_PATH):
        sys.exit(f"Hittar inte {CSV_PATH}.")
    ana = load(CSV_PATH)
    if not ana:
        sys.exit("Inga analyserade rader med visningar hittades.")

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
        --muted:#8b857a;--line:#e2ddd3;--accent:#c0562f;--taupe:#8f8275}
      *{box-sizing:border-box}
      body{font:15px/1.55 "Helvetica Neue",Helvetica,Arial,-apple-system,system-ui,sans-serif;
        margin:0;background:var(--bg);color:var(--ink);-webkit-font-smoothing:antialiased}
      .wrap{max-width:940px;margin:0 auto;padding:36px 20px 100px}
      h1{font-size:38px;line-height:1.03;letter-spacing:-.025em;font-weight:800;margin:0 0 8px}
      @media(max-width:560px){h1{font-size:29px}}
      h2{font-size:21px;letter-spacing:-.01em;font-weight:800;margin:32px 0 12px}
      h3{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--muted);
        font-weight:700;margin:16px 0 8px}
      .muted{color:var(--muted)} a{color:inherit}
      /* nyckeltal-kort */
      .stat{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0 6px}
      .stat .c{flex:1;min-width:150px;background:var(--panel);border:1px solid var(--line);
        border-radius:18px;padding:16px 18px}
      .stat .big{font-size:26px;font-weight:800;letter-spacing:-.02em;margin-bottom:2px}
      .stat .muted{font-size:13px}
      /* segment-piller */
      .seg{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:8px;
        flex-wrap:wrap;padding:12px 0;
        background:linear-gradient(var(--bg),var(--bg) 72%,transparent)}
      .seg .lbl{font-size:11px;text-transform:uppercase;letter-spacing:.09em;
        color:var(--muted);font-weight:700;margin-right:2px}
      .pill{border:1px solid var(--line);background:transparent;color:var(--ink);
        border-radius:999px;padding:8px 16px;font:inherit;font-size:14px;font-weight:600;
        cursor:pointer;transition:all .15s ease}
      .pill:hover{border-color:var(--muted)}
      .pill.active{background:var(--ink);color:#fff;border-color:var(--ink)}
      /* tabeller */
      section{overflow-x:auto}
      table.bt{border-collapse:collapse;width:100%;font-size:14px;background:var(--panel);
        border:1px solid var(--line);border-radius:16px;overflow:hidden}
      table.bt th,table.bt td{padding:11px 14px;text-align:left;
        border-bottom:1px solid var(--line)}
      table.bt tbody tr:last-child td{border-bottom:none}
      table.bt th{font-size:11px;text-transform:uppercase;letter-spacing:.07em;
        color:var(--muted);font-weight:700;background:var(--card)}
      th.v,td.v{text-align:right;white-space:nowrap}
      th.sortable{cursor:pointer;user-select:none} th.sortable:hover{color:var(--ink)}
      tr.grow{cursor:pointer;transition:background .12s} tr.grow:hover{background:#0000000a}
      td.muted{color:var(--muted)}
      .drow td{background:#00000006;padding:6px 12px 14px}
      /* inläggskort */
      .cards{display:flex;gap:12px;flex-wrap:wrap;margin:10px 0}
      .cards.col{flex-direction:column}
      .pc{display:flex;gap:12px;width:300px;text-decoration:none;background:var(--card);
        border:1px solid var(--line);border-radius:16px;padding:10px;
        transition:transform .12s,box-shadow .12s}
      .pc:hover{transform:translateY(-1px);box-shadow:0 6px 18px #0000000f}
      .cards.col .pc{width:100%}
      .pc img{width:66px;height:88px;object-fit:cover;border-radius:10px;
        background:var(--line);flex:0 0 auto}
      .pcm{min-width:0} .pcer{font-weight:800;font-size:15px;letter-spacing:-.01em}
      .er-hi{color:#4a6647} .er-lo{color:#a2481f}
      .lvl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
      .pcc{font-size:12.5px;color:var(--muted);margin-top:3px;overflow:hidden;
        display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
      .badge{font-size:10px;font-weight:700;padding:2px 8px;border-radius:999px;
        vertical-align:middle;text-transform:uppercase;letter-spacing:.04em}
      .badge.o{background:#5f7d5c22;color:#4a6647} .badge.b{background:#c0562f22;color:#a2481f}
      .two{display:flex;gap:20px;flex-wrap:wrap} .two>div{flex:1;min-width:300px}
    """
    def stat(v, l):
        return f'<div class="c"><div class="big">{v}</div><div class="muted">{l}</div></div>'

    header = (f'<h1>IQ TikTok – innehåll vs engagemang</h1>'
              f'<p class="muted">{len(ana)} analyserade inlägg. Viktad ER = '
              f'(likes + kommentarer×5 + delningar×10 + favoriter×5) / visningar. '
              f'Nivåerna lågt/medel/högt beräknas datadrivet ur er faktiska data '
              f'(kvartiler), separat för organiskt och boostat. Klicka en kategori '
              f'för att se inläggen, en kolumnrubrik för att sortera.</p>'
              f'<div class="stat">{stat(pct(med_org),"median ER organiskt")}'
              f'{stat(pct(med_boost),"median ER boostat")}'
              f'{stat(verdict,"trend organiskt")}'
              f'{stat(str(len(org))+" / "+str(len(boost)),"organiska / boostade")}</div>')

    seg = ('<div class="seg"><span class="lbl">Segment</span>'
           '<button class="pill active" data-seg="boost">Boostat</button>'
           '<button class="pill" data-seg="org">Organiskt</button>'
           '<button class="pill" data-seg="alla">Alla</button></div>'
           '<p id="levels" class="muted"></p>')

    # Tidsgrafen ritas av JS-appen (uppdateras med segment-väljaren).
    charts = ('<section><h2 id="chart-title">Engagemang över tid</h2>'
              '<div id="chart"></div><p id="chart-note" class="muted"></p></section>')

    doc = (f'<!doctype html><html lang="sv"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>IQ TikTok – innehåll vs engagemang</title><style>{css}</style>'
           f'</head><body><div class="wrap">{header}{seg}{charts}'
           f'<div id="dims"></div>'
           f'<section><h2>Starkast &amp; svagast (organiskt vs boostat)</h2>'
           f'<p class="muted">Filtrerat till ≥ {3000} visningar. Klicka för att '
           f'öppna på TikTok.</p><div id="rank"></div></section>'
           f'</div>'
           f'<script>window.POSTS={data_js};window.HAS_TONE={str(has_tone).lower()};'
           f'window.HAS_OCCASION={str(has_occasion).lower()};</script>'
           f'<script>{APP_JS}</script></body></html>')
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(doc)


if __name__ == "__main__":
    main()
