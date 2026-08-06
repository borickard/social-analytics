#!/usr/bin/env python3
"""
Stickprovsvalidering av taggningen innan analys.

Väljer ett slumpmässigt (reproducerbart) urval analyserade inlägg och skriver
en HTML-sida (iq_tiktok_data/iq_validering.html) där varje inlägg visas med
miniatyrbild + alla taggar + caption + transkript-utdrag – så du snabbt kan
ögna om kategori/budskapston/hook/alkoholflaggor stämmer mot verkligheten.

Kör:
    python validate_sample.py            # 20 slumpade inlägg
    IQ_VALIDATE_N=30 python validate_sample.py
    IQ_VALIDATE_FILTER=alkohol python validate_sample.py   # bara alkohol_i_bild=ja
    IQ_VALIDATE_FILTER=bild python validate_sample.py       # bara foto/karusell
"""

import csv
import html
import json
import os
import random
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJECT_DIR, "iq_tiktok_data")
CSV_PATH = os.path.join(DATA, "iq_tiktok_enriched.csv")
OUT_HTML = os.path.join(DATA, "iq_validering.html")

N = int(os.environ.get("IQ_VALIDATE_N", "20") or 20)
SEED = int(os.environ.get("IQ_VALIDATE_SEED", "0") or 0)
FILTER = os.environ.get("IQ_VALIDATE_FILTER", "").strip().lower()

# Taggar som visas för granskning (i denna ordning).
TAGS = ["typ", "format", "kategori", "tema", "hook_typ", "har_hook",
        "budskapston", "budskap_teman", "har_cta", "cta",
        "alkohol_i_bild", "alkohol_marke", "alkohol_kontext",
        "alkohol_omnamns_verbalt"]


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def _listcell(v):
    """Visa JSON-listfält snyggt (t.ex. text_i_bild, cta, budskap_teman)."""
    v = v or ""
    try:
        parsed = json.loads(v)
        if isinstance(parsed, list):
            return " · ".join(str(x) for x in parsed) if parsed else "—"
    except Exception:
        pass
    return v or "—"


def main():
    if not os.path.exists(CSV_PATH):
        sys.exit(f"Hittar inte {CSV_PATH}.")
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r.get("format")]
    if not rows:
        sys.exit("Inga analyserade rader att validera.")

    if FILTER == "alkohol":
        rows = [r for r in rows if r.get("alkohol_i_bild") == "ja"]
    elif FILTER in ("bild", "foto", "karusell"):
        rows = [r for r in rows if r.get("typ") == "bild"]
    elif FILTER == "budskap":
        rows = [r for r in rows if r.get("budskapston") == "budskap"]
    if not rows:
        sys.exit(f"Inga inlägg matchade filtret '{FILTER}'.")

    rng = random.Random(SEED)
    sample = rng.sample(rows, min(N, len(rows)))

    cards = []
    for r in sample:
        vid = r.get("video_id", "")
        thumb = r.get("thumbnail", "") or f"thumbnails/{vid}.jpg"
        url = r.get("url", "")
        tagrows = "".join(
            f'<tr><th>{_esc(t)}</th><td>{_esc(_listcell(r.get(t)))}</td></tr>'
            for t in TAGS)
        transcript = (r.get("transkript", "") or "")[:400]
        cards.append(
            f'<div class="card">'
            f'<div class="thumb"><a href="{_esc(url)}" target="_blank">'
            f'<img src="{_esc(thumb)}" loading="lazy" alt="thumbnail"></a>'
            f'<div class="vid"><a href="{_esc(url)}" target="_blank">{_esc(vid)}</a></div>'
            f'</div>'
            f'<div class="meta">'
            f'<table>{tagrows}</table>'
            f'<div class="cap"><strong>Caption:</strong> {_esc(r.get("caption",""))}</div>'
            f'<div class="tib"><strong>Text i bild:</strong> '
            f'{_esc(_listcell(r.get("text_i_bild")))}</div>'
            + (f'<div class="tr"><strong>Transkript:</strong> {_esc(transcript)}'
               f'{"…" if len(r.get("transkript","") or "")>400 else ""}</div>'
               if transcript else "")
            + f'</div></div>')

    css = """
      :root{color-scheme:light dark}
      body{font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif;margin:0;
        background:#fafafa;color:#1a1a1a}
      @media(prefers-color-scheme:dark){body{background:#161616;color:#eaeaea}}
      .wrap{max-width:1000px;margin:0 auto;padding:24px 18px 80px}
      h1{font-size:22px} .lead{color:#888;margin-bottom:20px}
      .card{display:flex;gap:16px;background:#fff;border:1px solid #8882;
        border-radius:12px;padding:14px;margin:14px 0}
      @media(prefers-color-scheme:dark){.card{background:#222}}
      .thumb{flex:0 0 160px;text-align:center}
      .thumb img{width:160px;border-radius:8px;background:#0001}
      .vid{font-size:11px;margin-top:6px;word-break:break-all}
      .meta{flex:1;min-width:0}
      table{border-collapse:collapse;font-size:13px;margin-bottom:8px}
      th{text-align:left;color:#888;font-weight:600;padding:2px 10px 2px 0;
        vertical-align:top;white-space:nowrap}
      td{padding:2px 0}
      .cap,.tib,.tr{font-size:13px;margin-top:6px}
      .tr{color:#888}
      @media(max-width:640px){.card{flex-direction:column}}
    """
    doc = (f'<!doctype html><html lang="sv"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>IQ – validering av taggning</title><style>{css}</style></head>'
           f'<body><div class="wrap"><h1>Validering av taggning</h1>'
           f'<p class="lead">{len(sample)} slumpade inlägg (av {len(rows)} '
           f'{"filtrerade " if FILTER else ""}analyserade). Jämför miniatyrbild '
           f'+ caption mot taggarna. Klicka på bilden för att öppna på TikTok. '
           f'Seed={SEED} (sätt IQ_VALIDATE_SEED för ett annat urval).</p>'
           f'{"".join(cards)}</div></body></html>')
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"Skrev {OUT_HTML} med {len(sample)} inlägg. Öppna med:  "
          f"open {os.path.relpath(OUT_HTML, PROJECT_DIR)}")


if __name__ == "__main__":
    main()
