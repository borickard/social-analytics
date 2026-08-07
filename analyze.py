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


def band(er):
    p = er * 100
    if p >= 2:
        return "starkt (2 %+)"
    if p >= 0.5:
        return "normalt (0,5–2 %)"
    return "svagt (<0,5 %)"


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


def svg_line(series, title):
    if len(series) < 2:
        return f"<p><em>{title}: för få månader.</em></p>"
    W, H = 760, 260
    padl, padr, padt, padb = 52, 54, 14, 34
    ys = [v * 100 for _, v, _ in series]
    ymax = max(ys) * 1.15 or 1
    def X(i): return padl + i * (W - padl - padr) / (len(series) - 1)
    def Y(v): return H - padb - (v / ymax) * (H - padt - padb)

    # Y-axel: rutnätslinjer + %-etiketter på jämna steg (så värdena går att läsa).
    ystep = 1 if ymax <= 6 else 2 if ymax <= 14 else 5
    grid = ""
    t = 0.0
    while t <= ymax:
        yy = Y(t)
        grid += (f'<line x1="{padl}" y1="{yy:.1f}" x2="{W-padr}" y2="{yy:.1f}" '
                 f'stroke="#8883" stroke-width="1"/>'
                 f'<text x="{padl-8}" y="{yy+4:.1f}" text-anchor="end" '
                 f'font-size="10" fill="#999">{str(t).rstrip("0").rstrip(".").replace(".", ",")} %</text>')
        t += ystep

    # Riktmärkeslinjer 0,5 % (svagt) och 2 % (starkt), tydligt markerade.
    guides = ""
    for gy, gl, col in [(0.5, "0,5 %", "#c9622e"), (2.0, "2 %", "#2e7d5b")]:
        if gy <= ymax:
            yy = Y(gy)
            guides += (f'<line x1="{padl}" y1="{yy:.1f}" x2="{W-padr}" y2="{yy:.1f}" '
                       f'stroke="{col}" stroke-dasharray="5 3" stroke-width="1.2"/>'
                       f'<text x="{W-padr+3}" y="{yy+4:.1f}" font-size="10" fill="{col}">{gl}</text>')

    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(ys))
    step = max(1, len(series) // 8)
    xlab = "".join(f'<text x="{X(i):.1f}" y="{H-padb+16}" text-anchor="middle" '
                   f'font-size="10" fill="currentColor">{series[i][0]}</text>'
                   for i in range(0, len(series), step))
    dots = "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.6" fill="#3b6fb0">'
                   f'<title>{series[i][0]}: {v:.2f} %</title></circle>'
                   for i, v in enumerate(ys))
    return (f'<h3>{title}</h3><svg viewBox="0 0 {W} {H}" width="100%" '
            f'style="max-width:{W}px">{grid}{guides}<polyline points="{pts}" '
            f'fill="none" stroke="#3b6fb0" stroke-width="2"/>{dots}{xlab}</svg>')


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
let segment = 'alla';

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
    `<div class="pcm"><div class="pcer">${fmtPct(p.er)} `+
    `<span class="badge ${p.organic?'o':'b'}">${p.organic?'org':'boost'}</span></div>`+
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

function renderAll(){DIMS.forEach(renderDim);renderRank();}

document.addEventListener('click',e=>{
  const th=e.target.closest('th.sortable');
  if(th){const d=th.dataset.dim,c=th.dataset.col;const st=sortState[d];
    if(st.col===c)st.dir*=-1;else{st.col=c;st.dir=(c==='k')?1:-1;}renderDim(DIMS.find(x=>x.id===d));return;}
  const gr=e.target.closest('tr.grow');
  if(gr){const d=gr.dataset.dim,k=gr.dataset.k;const s=openState[d];
    s.has(k)?s.delete(k):s.add(k);renderDim(DIMS.find(x=>x.id===d));return;}
});
document.querySelectorAll('input[name=seg]').forEach(r=>r.addEventListener('change',e=>{
  segment=e.target.value;renderAll();}));

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
    med_all = statistics.median([r["_er"] for r in ana])
    med_org = statistics.median([r["_er"] for r in org]) if org else 0
    med_boost = statistics.median([r["_er"] for r in boost]) if boost else 0
    bands = Counter(band(r["_er"]) for r in ana)

    # ---- terminal ----
    print(f"\nAnalyserade inlägg: {len(ana)}  (organiska: {len(org)}, "
          f"boostade: {len(boost)})")
    print(f"Median viktad ER – alla: {pct(med_all)} | organiskt: {pct(med_org)} "
          f"| boostat: {pct(med_boost)}")
    for b in ("starkt (2 %+)", "normalt (0,5–2 %)", "svagt (<0,5 %)"):
        print(f"   {b}: {bands.get(b,0)}")
    print(f"Trend (organiskt): {verdict} ({slope:+.3f} pe/månad)")
    if not has_tone:
        print("(Obs: budskapston saknas – kör classify_tone.py för ton/hook.)")
    print(f"\nInteraktiv rapport: {OUT_HTML}")

    # ---- HTML ----
    data = [post_json(r) for r in ana]
    data_js = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")

    css = """
      :root{color-scheme:light dark}
      body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;
        background:#fafafa;color:#1a1a1a}
      @media(prefers-color-scheme:dark){body{background:#161616;color:#eaeaea}}
      .wrap{max-width:920px;margin:0 auto;padding:26px 18px 90px}
      h1{font-size:24px;margin:0 0 4px} h2{font-size:18px;margin:26px 0 8px}
      h3{font-size:13px;color:#888;margin:14px 0 6px}
      .muted{color:#888} a{color:inherit}
      .cards{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}
      .cards.col{flex-direction:column}
      .stat{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
      .stat .c{background:#fff;border:1px solid #8882;border-radius:10px;padding:12px 14px}
      @media(prefers-color-scheme:dark){.stat .c{background:#222}}
      .stat .big{font-size:22px;font-weight:700}
      .seg{position:sticky;top:0;background:#fafafaee;padding:10px 0;z-index:5;
        backdrop-filter:blur(4px)}
      @media(prefers-color-scheme:dark){.seg{background:#161616ee}}
      .seg label{margin-right:14px;cursor:pointer}
      table.bt{border-collapse:collapse;width:100%;font-size:13px}
      table.bt th,table.bt td{padding:6px 9px;border-bottom:1px solid #8882;text-align:left}
      table.bt th.v,table.bt td.v{text-align:right;white-space:nowrap}
      th.sortable{cursor:pointer;user-select:none;color:#888;font-weight:600}
      th.sortable:hover{color:inherit}
      tr.grow{cursor:pointer} tr.grow:hover{background:#8881}
      .drow td{background:#8880;padding:4px 9px 12px}
      .pc{display:flex;gap:9px;width:290px;text-decoration:none;border:1px solid #8882;
        border-radius:10px;padding:8px;background:#fff}
      @media(prefers-color-scheme:dark){.pc{background:#1e1e1e}}
      .cards.col .pc{width:100%}
      .pc img{width:70px;height:92px;object-fit:cover;border-radius:6px;background:#8882;flex:0 0 auto}
      .pcm{min-width:0} .pcer{font-weight:700} .pcc{font-size:12px;color:#888;margin-top:3px;
        overflow:hidden;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical}
      .badge{font-size:10px;padding:1px 6px;border-radius:8px;vertical-align:middle}
      .badge.o{background:#4a9d7f33;color:#2e7d5b} .badge.b{background:#e0823d33;color:#b0632a}
      .two{display:flex;gap:18px;flex-wrap:wrap} .two>div{flex:1;min-width:300px}
      section{overflow-x:auto}
    """
    def stat(v, l):
        return f'<div class="c"><div class="big">{v}</div><div class="muted">{l}</div></div>'

    header = (f'<h1>IQ TikTok – innehåll vs engagemang</h1>'
              f'<p class="muted">{len(ana)} analyserade inlägg. Viktad ER = '
              f'(likes + kommentarer×5 + delningar×10 + favoriter×5) / visningar. '
              f'Median som huvudmått. Klicka en kategori för att se inläggen, '
              f'klicka en kolumnrubrik för att sortera.</p>'
              f'<div class="stat">{stat(pct(med_org),"median ER organiskt")}'
              f'{stat(pct(med_boost),"median ER boostat")}'
              f'{stat(verdict,"trend organiskt")}'
              f'{stat(bands.get("starkt (2 %+)",0),"starka inlägg (2 %+)")}</div>')

    seg = ('<div class="seg"><strong>Segment:</strong> '
           '<label><input type="radio" name="seg" value="alla" checked> Alla</label>'
           '<label><input type="radio" name="seg" value="org"> Organiskt</label>'
           '<label><input type="radio" name="seg" value="boost"> Boostat</label>'
           '<span class="muted">(filtrerar tabellerna nedan)</span></div>')

    charts = (f'<section><h2>Engagemang över tid (organiskt)</h2>'
              f'{svg_line(ser_org, "Median viktad ER per månad")}'
              f'<p class="muted">Trend: <strong>{verdict}</strong> '
              f'({slope:+.3f} procentenheter/månad).</p></section>')

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
