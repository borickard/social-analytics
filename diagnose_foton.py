#!/usr/bin/env python3
"""Engångs-diagnos: kom foton/karuseller med i insamlingen, och laddades
bilderna ner? Kör: python3 diagnose_foton.py  (från projektmappen)."""

import collections
import csv
import glob
import json
import os

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJECT_DIR, "iq_tiktok_data")
CSV = os.path.join(DATA, "iq_tiktok_metrics.csv")
RAW = os.path.join(DATA, "iq_tiktok_raw.jsonl")
IMG = os.path.join(DATA, "images")
KNOWN = ["7655325406882647299", "7577754708085984534"]  # karusell + enkelfoto

# 1) Metrics-CSV:n
rows = []
if os.path.exists(CSV):
    with open(CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
typ = collections.Counter(r.get("typ", "") for r in rows)
photo_url = sum(1 for r in rows if "/photo/" in (r.get("url", "") or ""))
print(f"CSV: {len(rows)} rader | typ-fördelning: {dict(typ)}")
print(f"CSV: rader med /photo/ i url: {photo_url}")

byid = {r.get("video_id", ""): r for r in rows}
for k in KNOWN:
    r = byid.get(k)
    if r:
        print(f"  KÄND {k}: typ={r.get('typ')!r} url={r.get('url')!r} "
              f"nedladdad={r.get('nedladdad')!r} antal_bilder={r.get('antal_bilder')!r}")
    else:
        print(f"  KÄND {k}: FINNS INTE i CSV:n")

# 2) Råloggen – hur många scrapade items var foto-inlägg?
n = nimg = 0
known_in_raw = {}
if os.path.exists(RAW):
    with open(RAW, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n += 1
            try:
                item = json.loads(line)
            except Exception:
                continue
            if item.get("imagePost"):
                nimg += 1
            if str(item.get("id", "")) in KNOWN:
                known_in_raw[str(item.get("id"))] = bool(item.get("imagePost"))
print(f"RAW jsonl: {n} items, varav {nimg} med imagePost (foto/karusell)")
for k in KNOWN:
    if k in known_in_raw:
        print(f"  KÄND {k} i råloggen: imagePost={known_in_raw[k]}")
    else:
        print(f"  KÄND {k}: inte scrapad (finns inte i råloggen)")

# 3) Bilder på disk
if os.path.isdir(IMG):
    dirs = [d for d in os.listdir(IMG) if os.path.isdir(os.path.join(IMG, d))]
    files = glob.glob(os.path.join(IMG, "*", "*"))
    print(f"images/: {len(dirs)} mappar, {len(files)} bildfiler")
else:
    print("images/: mappen finns inte")
