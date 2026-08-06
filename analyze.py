#!/usr/bin/env python3
"""
Analys: kopplar innehåll till engagemang för IQ:s TikTok-inlägg.

Läser iq_tiktok_data/iq_tiktok_enriched.csv och skriver:
  - en terminalsammanfattning
  - en fristående HTML-rapport (iq_tiktok_data/iq_analys.html) med diagram

ENGAGEMANG (så IQ mäter): viktad engagemangsgrad, visad som procent
    ER = (likes + kommentarer*5 + delningar*10 + favoriter*5) / visningar
Riktmärken: <0,5 % svagt · 0,5–2 % normalt · 2 %+ starkt. Delningar väger
tyngst, allt i relation till visningar. Exakta siffror (*_exakt) används där de
finns, annars de skrapade. Median används som huvudmått (robust mot virala
extremvärden); medel visas bredvid.

Kör:  python analyze.py            (använder standard-CSV:n)
      python analyze.py fil.csv    (annan CSV, t.ex. för test)
"""

import csv
import html
import os
import statistics
import sys
from collections import Counter, defaultdict

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(PROJECT_DIR, "iq_tiktok_data", "iq_tiktok_enriched.csv")
CSV_PATH = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CSV
OUT_HTML = os.path.join(os.path.dirname(CSV_PATH) or ".", "iq_analys.html")

# Minsta antal inlägg för att en grupp ska visas i en benchmark (undviker att
# enstaka inlägg ser ut som en "kategori").
MIN_N = 4
# Minsta visningar för att räknas med i topp/botten-listor (undviker att inlägg
# med extremt få visningar ger missvisande höga/låga procenttal).
MIN_VIEWS_RANK = 3000


# ----------------------------------------------------------------- inläsning ---
def num(x):
    """Tolka ett tal ur en cell (tar bort mellanslag/hårda mellanslag)."""
    if x is None:
        return None
    s = str(x).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pick(row, exact, scraped):
    """Exakt siffra om den finns, annars den skrapade."""
    return num(row.get(exact)) if num(row.get(exact)) is not None else num(row.get(scraped))


def weighted_er(row):
    """IQ:s viktade engagemangsgrad (andel, ej procent). None om visningar saknas."""
    views = pick(row, "visningar_exakt", "visningar")
    if not views:
        return None
    likes = pick(row, "likes_exakt", "likes") or 0
    comments = pick(row, "kommentarer_exakt", "kommentarer") or 0
    shares = pick(row, "delningar_exakt", "delningar") or 0
    saves = pick(row, "sparade_exakt", "sparade") or 0
    return (likes + comments * 5 + shares * 10 + saves * 5) / views


def band(er):
    """IQ:s riktmärkesband för en viktad ER (andel)."""
    p = er * 100
    if p >= 2:
        return "starkt (2 %+)"
    if p >= 0.5:
        return "normalt (0,5–2 %)"
    return "svagt (<0,5 %)"


def load(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    ana = []
    for r in rows:
        if not r.get("format"):          # bara analyserade inlägg
            continue
        r["_er"] = weighted_er(r)
        r["_views"] = pick(r, "visningar_exakt", "visningar")
        if r["_er"] is None:
            continue
        ana.append(r)
    return rows, ana


# --------------------------------------------------------------- aggregering ---
def is_organic(r):
    return str(r.get("is_ad", "")).strip().lower() not in ("true", "1", "ja")


def pct(er):
    return f"{er * 100:.2f} %".replace(".", ",")


def thousands(n):
    """Heltal med mellanslag som tusentalsavgränsare (svensk stil)."""
    return f"{int(n):,}".replace(",", " ")


def benchmark(rows, keyfn, min_n=MIN_N):
    """Gruppera på keyfn (kan returnera flera nycklar per rad) och sammanfatta."""
    groups = defaultdict(list)
    for r in rows:
        for k in keyfn(r):
            if k not in (None, "", "[]"):
                groups[k].append(r)
    out = []
    for k, rs in groups.items():
        ers = [x["_er"] for x in rs]
        views = [x["_views"] for x in rs if x["_views"]]
        out.append({
            "grupp": k, "n": len(rs),
            "median_er": statistics.median(ers),
            "mean_er": statistics.mean(ers),
            "median_views": statistics.median(views) if views else 0,
        })
    out = [g for g in out if g["n"] >= min_n]
    out.sort(key=lambda d: d["median_er"], reverse=True)
    return out


def single(field):
    return lambda r: [r.get(field, "")]


def listfield(field):
    """Nyckelfunktion för fält lagrade som JSON-listor (t.ex. budskap_teman)."""
    import json

    def fn(r):
        v = r.get(field, "") or ""
        try:
            parsed = json.loads(v)
            if isinstance(parsed, list):
                return [str(x) for x in parsed] or [""]
        except Exception:
            pass
        return [v] if v else [""]
    return fn


def hook_key(r):
    """har_hook om den finns (från classify_tone), annars härlett ur hook_typ."""
    if r.get("har_hook"):
        return ["hook" if r["har_hook"] == "ja" else "ingen hook"]
    ht = r.get("hook_typ", "")
    if not ht:
        return [""]
    return ["ingen/oklar hook" if ht == "ovrigt" else f"hook: {ht}"]


def months(rows):
    """Median-ER och antal per månad (YYYY-MM), kronologiskt."""
    m = defaultdict(list)
    for r in rows:
        d = (r.get("publiceringsdatum", "") or "")[:7]
        if len(d) == 7:
            m[d].append(r["_er"])
    return [(k, statistics.median(m[k]), len(m[k])) for k in sorted(m)]


def trend(series):
    """Enkel linjär regression på månads-median-ER → lutning + omdöme."""
    pts = [(i, v) for i, (_, v, _) in enumerate(series)]
    n = len(pts)
    if n < 3:
        return 0.0, "för få månader för trend"
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    denom = sum((p[0] - mx) ** 2 for p in pts)
    if denom == 0:
        return 0.0, "stabilt"
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / denom
    # Lutning per månad i procentenheter.
    per_month = slope * 100
    if per_month > 0.03:
        verdict = "uppåt ↗"
    elif per_month < -0.03:
        verdict = "nedåt ↘"
    else:
        verdict = "stabilt →"
    return per_month, verdict


def mix_by_month(rows, field, top=5):
    """Andel av varje kategori (field) per månad – för innehållsmix över tid."""
    per = defaultdict(Counter)
    totals = Counter()
    for r in rows:
        d = (r.get("publiceringsdatum", "") or "")[:7]
        k = r.get(field, "") or "okänt"
        if len(d) == 7:
            per[d][k] += 1
            totals[k] += 1
    keys = [k for k, _ in totals.most_common(top)]
    ms = sorted(per)
    return ms, keys, per


# --------------------------------------------------------------------- SVG -----
PALETTE = ["#3b6fb0", "#e0823d", "#4a9d7f", "#b0503b", "#8a6bb0",
           "#c9a13b", "#6b8fb0", "#9d4a6b"]


def _esc(s):
    return html.escape(str(s))


def svg_bars(rows, title, maxbars=12):
    """Horisontellt stapeldiagram över median-ER (%) per grupp."""
    rows = rows[:maxbars]
    if not rows:
        return f"<p><em>{_esc(title)}: för lite data.</em></p>"
    W, rowh, pad, lblw = 640, 26, 8, 210
    H = pad * 2 + rowh * len(rows) + 20
    mx = max(r["median_er"] for r in rows) or 1
    bars = []
    for i, r in enumerate(rows):
        y = pad + i * rowh
        bw = (r["median_er"] / mx) * (W - lblw - 90)
        label = f'{r["grupp"]}  (n={r["n"]})'
        bars.append(
            f'<text x="{lblw-6}" y="{y+rowh/2+4}" text-anchor="end" '
            f'font-size="12" fill="currentColor">{_esc(label)}</text>'
            f'<rect x="{lblw}" y="{y+3}" width="{max(bw,1):.1f}" height="{rowh-8}" '
            f'rx="3" fill="{PALETTE[0]}"/>'
            f'<text x="{lblw+max(bw,1)+6:.1f}" y="{y+rowh/2+4}" font-size="12" '
            f'fill="currentColor">{_esc(pct(r["median_er"]))}</text>')
    return (f'<h3>{_esc(title)}</h3>'
            f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
            f'style="max-width:{W}px">{"".join(bars)}</svg>')


def svg_line(series, title):
    """Linjediagram: median-ER (%) per månad."""
    if len(series) < 2:
        return f"<p><em>{_esc(title)}: för få månader.</em></p>"
    W, H, pad = 720, 240, 40
    xs = list(range(len(series)))
    ys = [v * 100 for _, v, _ in series]
    ymax = max(ys) * 1.15 or 1
    def X(i): return pad + i * (W - pad * 2) / (len(series) - 1)
    def Y(v): return H - pad - (v / ymax) * (H - pad * 2)
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in zip(xs, ys))
    # riktmärkeslinjer 0,5 % och 2 %
    guides = ""
    for gy, gl in [(0.5, "0,5 %"), (2.0, "2 %")]:
        if gy <= ymax:
            yy = Y(gy)
            guides += (f'<line x1="{pad}" y1="{yy:.1f}" x2="{W-pad}" y2="{yy:.1f}" '
                       f'stroke="#aaa" stroke-dasharray="4 3" stroke-width="1"/>'
                       f'<text x="{W-pad+2}" y="{yy+4:.1f}" font-size="10" '
                       f'fill="#999">{gl}</text>')
    # x-etiketter (gles)
    step = max(1, len(series) // 8)
    xlab = "".join(
        f'<text x="{X(i):.1f}" y="{H-pad+16}" text-anchor="middle" font-size="10" '
        f'fill="currentColor">{_esc(series[i][0])}</text>'
        for i in range(0, len(series), step))
    dots = "".join(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="2.5" '
                   f'fill="{PALETTE[0]}"/>' for i, v in zip(xs, ys))
    return (f'<h3>{_esc(title)}</h3>'
            f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
            f'style="max-width:{W}px">{guides}'
            f'<polyline points="{pts}" fill="none" stroke="{PALETTE[0]}" '
            f'stroke-width="2"/>{dots}{xlab}</svg>')


def svg_stacked(ms, keys, per, title):
    """Staplade staplar: innehållsmix (andel per kategori) per månad."""
    if len(ms) < 2:
        return f"<p><em>{_esc(title)}: för få månader.</em></p>"
    W, H, pad = 720, 260, 40
    bw = (W - pad * 2) / len(ms) * 0.8
    bars = ""
    for i, mo in enumerate(ms):
        x = pad + i * (W - pad * 2) / len(ms)
        tot = sum(per[mo].get(k, 0) for k in keys) or 1
        # inkludera "övrigt" som resten
        other = sum(per[mo].values()) - sum(per[mo].get(k, 0) for k in keys)
        yacc = H - pad
        segs = [(k, per[mo].get(k, 0)) for k in keys] + [("övrigt", other)]
        denom = sum(v for _, v in segs) or 1
        for j, (k, v) in enumerate(segs):
            h = (v / denom) * (H - pad * 2)
            if h <= 0:
                continue
            col = PALETTE[j % len(PALETTE)] if k != "övrigt" else "#cfcfcf"
            bars += (f'<rect x="{x:.1f}" y="{yacc-h:.1f}" width="{bw:.1f}" '
                     f'height="{h:.1f}" fill="{col}"/>')
            yacc -= h
    step = max(1, len(ms) // 8)
    xlab = "".join(
        f'<text x="{pad + i*(W-pad*2)/len(ms) + bw/2:.1f}" y="{H-pad+16}" '
        f'text-anchor="middle" font-size="10" fill="currentColor">'
        f'{_esc(ms[i])}</text>' for i in range(0, len(ms), step))
    legend = " ".join(
        f'<span style="white-space:nowrap"><span style="display:inline-block;'
        f'width:11px;height:11px;background:{PALETTE[j % len(PALETTE)]};'
        f'border-radius:2px"></span> {_esc(k)}</span>'
        for j, k in enumerate(keys))
    return (f'<h3>{_esc(title)}</h3>'
            f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" '
            f'style="max-width:{W}px">{bars}{xlab}</svg>'
            f'<div class="legend">{legend} '
            f'<span style="white-space:nowrap"><span style="display:inline-block;'
            f'width:11px;height:11px;background:#cfcfcf;border-radius:2px">'
            f'</span> övrigt</span></div>')


# ------------------------------------------------------------------- tabeller ---
def bench_table(rows):
    if not rows:
        return "<p><em>För lite data.</em></p>"
    trs = "".join(
        f'<tr><td>{_esc(r["grupp"])}</td><td class="n">{r["n"]}</td>'
        f'<td class="v">{_esc(pct(r["median_er"]))}</td>'
        f'<td class="v muted">{_esc(pct(r["mean_er"]))}</td>'
        f'<td class="v muted">{thousands(r["median_views"])}</td></tr>'
        for r in rows)
    return ('<table><thead><tr><th>Grupp</th><th class="n">n</th>'
            '<th class="v">Median ER</th><th class="v">Medel ER</th>'
            '<th class="v">Median visn.</th></tr></thead>'
            f'<tbody>{trs}</tbody></table>')


def rank_table(rows, best=True, k=10):
    cand = [r for r in rows if (r["_views"] or 0) >= MIN_VIEWS_RANK]
    cand.sort(key=lambda r: r["_er"], reverse=best)
    cand = cand[:k]
    trs = ""
    for r in cand:
        cap = (r.get("caption", "") or "")[:70]
        trs += (f'<tr><td class="v">{_esc(pct(r["_er"]))}</td>'
                f'<td>{_esc(r.get("typ",""))}/{_esc(r.get("kategori",""))}</td>'
                f'<td>{_esc(r.get("budskapston",""))}</td>'
                f'<td class="v">{thousands(r["_views"])}</td>'
                f'<td class="cap">{_esc(cap)}</td></tr>')
    return ('<table><thead><tr><th class="v">ER</th><th>typ/kategori</th>'
            '<th>ton</th><th class="v">visningar</th><th>caption</th></tr></thead>'
            f'<tbody>{trs}</tbody></table>')


# --------------------------------------------------------------------- main ----
def main():
    if not os.path.exists(CSV_PATH):
        sys.exit(f"Hittar inte {CSV_PATH}.")
    allrows, ana = load(CSV_PATH)
    if not ana:
        sys.exit("Inga analyserade rader med visningar hittades.")

    org = [r for r in ana if is_organic(r)]
    has_tone = any(r.get("budskapston") for r in ana)

    overall = statistics.median([r["_er"] for r in ana])
    bands = Counter(band(r["_er"]) for r in ana)
    ser = months(org)
    slope, verdict = trend(ser)

    # ---- terminalsammanfattning ----
    print(f"\nAnalyserade inlägg: {len(ana)}  (varav organiska: {len(org)}, "
          f"boostade: {len(ana)-len(org)})")
    print(f"Median viktad ER (alla): {pct(overall)}")
    for b in ("starkt (2 %+)", "normalt (0,5–2 %)", "svagt (<0,5 %)"):
        print(f"   {b}: {bands.get(b,0)}")
    print(f"Trend (organiskt, median-ER/månad): {verdict}  "
          f"({slope:+.3f} procentenheter/månad)")
    print("\nBenchmark per kategori (organiskt, median ER):")
    for r in benchmark(org, single("kategori")):
        print(f"   {r['grupp']:<22} n={r['n']:<4} {pct(r['median_er'])}")
    if not has_tone:
        print("\n(Obs: budskapston/har_hook saknas – kör classify_tone.py för "
              "budskap-vs-lättsamt och ren hook-analys.)")
    print(f"\nHTML-rapport: {OUT_HTML}")

    # ---- HTML ----
    def sect(title, body):
        return f'<section><h2>{_esc(title)}</h2>{body}</section>'

    blocks = []
    blocks.append(sect("Översikt", (
        f'<div class="cards">'
        f'<div class="card"><div class="big">{len(ana)}</div>'
        f'<div>analyserade inlägg</div></div>'
        f'<div class="card"><div class="big">{_esc(pct(overall))}</div>'
        f'<div>median viktad ER</div></div>'
        f'<div class="card"><div class="big">{verdict}</div>'
        f'<div>trend över tid (organiskt)</div></div>'
        f'<div class="card"><div class="big">{bands.get("starkt (2 %+)",0)}</div>'
        f'<div>starka inlägg (2 %+)</div></div>'
        f'</div>'
        f'<p class="muted">Viktad ER = (likes + kommentarer×5 + delningar×10 '
        f'+ favoriter×5) / visningar. Riktmärken: &lt;0,5 % svagt · 0,5–2 % '
        f'normalt · 2 %+ starkt. Median används som huvudmått. Boostade inlägg '
        f'(is_ad) särredovisas i tidsanalysen (organiskt) men ingår i '
        f'kategoribenchmarks.</p>')))

    blocks.append(sect("Engagemang över tid (organiskt)",
                       svg_line(ser, "Median viktad ER per månad") +
                       f'<p>Trend: <strong>{_esc(verdict)}</strong> '
                       f'({slope:+.3f} procentenheter/månad).</p>'))

    blocks.append(sect("Innehållsmix över tid",
                       svg_stacked(*mix_by_month(ana, "kategori"),
                                   title="Andel per kategori och månad")))

    blocks.append(sect("Benchmark: kategori",
                       svg_bars(benchmark(org, single("kategori")),
                                "Median ER per kategori (organiskt)") +
                       bench_table(benchmark(ana, single("kategori")))))

    blocks.append(sect("Benchmark: format & typ",
                       svg_bars(benchmark(ana, single("format")),
                                "Median ER per format") +
                       bench_table(benchmark(ana, single("typ")))))

    blocks.append(sect("Benchmark: hook",
                       bench_table(benchmark(ana, hook_key))))

    blocks.append(sect("Benchmark: call to action",
                       bench_table(benchmark(
                           ana, lambda r: ["CTA" if r.get("har_cta") == "ja"
                                           else "ingen CTA"]))))

    if has_tone:
        blocks.append(sect("Benchmark: budskapston (budskap vs lättsamt)",
                           svg_bars(benchmark(ana, single("budskapston")),
                                    "Median ER per ton") +
                           bench_table(benchmark(ana, single("budskapston")))))
        blocks.append(sect("Benchmark: budskap (teman)",
                           bench_table(benchmark(ana, listfield("budskap_teman")))))

    blocks.append(sect("Benchmark: alkohol i bild",
                       bench_table(benchmark(
                           ana, lambda r: [f'alkohol: {r.get("alkohol_i_bild","")}'])) +
                       bench_table(benchmark(ana, single("alkohol_kontext")))))

    blocks.append(sect("Boostat vs organiskt",
                       bench_table(benchmark(
                           ana, lambda r: ["organiskt" if is_organic(r)
                                           else "boostat"], min_n=1))))

    blocks.append(sect("Starkast inlägg", rank_table(ana, best=True)))
    blocks.append(sect("Svagast inlägg", rank_table(ana, best=False)))

    css = """
      :root{color-scheme:light dark}
      body{font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;
        background:#fafafa;color:#1a1a1a}
      @media(prefers-color-scheme:dark){body{background:#161616;color:#eaeaea}}
      .wrap{max-width:860px;margin:0 auto;padding:28px 20px 80px}
      h1{font-size:26px;margin:0 0 4px} h2{font-size:19px;margin:34px 0 12px;
        border-bottom:1px solid #8883;padding-bottom:6px} h3{font-size:14px;
        margin:16px 0 6px;color:#888;font-weight:600}
      .cards{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
      .card{flex:1;min-width:150px;background:#fff;border:1px solid #8882;
        border-radius:10px;padding:14px}
      @media(prefers-color-scheme:dark){.card{background:#222}}
      .big{font-size:24px;font-weight:700;margin-bottom:2px}
      table{border-collapse:collapse;width:100%;margin:8px 0;font-size:13px}
      th,td{text-align:left;padding:5px 8px;border-bottom:1px solid #8882}
      th.v,td.v,th.n,td.n{text-align:right;white-space:nowrap}
      td.cap{color:#888;font-size:12px} .muted{color:#888}
      .legend{font-size:12px;color:#888;display:flex;gap:14px;flex-wrap:wrap;
        margin-top:6px}
      section{overflow-x:auto}
    """
    doc = (f'<!doctype html><html lang="sv"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>IQ TikTok – innehåll vs engagemang</title>'
           f'<style>{css}</style></head><body><div class="wrap">'
           f'<h1>IQ TikTok – innehåll vs engagemang</h1>'
           f'<p class="muted">{len(ana)} analyserade inlägg. '
           f'Genererad av analyze.py.</p>'
           f'{"".join(blocks)}</div></body></html>')
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(doc)


if __name__ == "__main__":
    main()
