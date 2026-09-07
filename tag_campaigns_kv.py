#!/usr/bin/env python3
"""
Förtagga kampanjinlägg som strategi=kampanj – rakt in i din hostade dashboards
KV-databas (via /api/overrides). Hittar kampanjer via namn/hashtags i
caption/transkript/bildtext. Slår ihop med befintliga overrides (skriver inte
över annat).

Kör (på din Mac):
    IQ_SITE_URL=https://iq-analytics.vercel.app \\
    IQ_SITE_USER=iq IQ_SITE_PASSWORD=ditt-lösenord \\
    python tag_campaigns_kv.py --dry      # visa träffar, skriv inget
    # ... och utan --dry för att skriva till KV.

Kräver inget utöver dina sidlösenordsuppgifter. LiqLab nämns sällan i text –
lägg ev. dess video_id i kampanj_overrides.csv (video_id,kampanj) för att få med
dem, eller tagga dem för hand i dashboarden efteråt.
"""

import base64
import csv
import json
import os
import re
import sys
import urllib.request
from collections import Counter

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(PROJECT_DIR, "iq_tiktok_data", "iq_tiktok_enriched.csv")
OVR_FILE = os.path.join(PROJECT_DIR, "iq_tiktok_data", "kampanj_overrides.csv")

SITE_URL = os.environ.get("IQ_SITE_URL", "").rstrip("/")
SITE_USER = os.environ.get("IQ_SITE_USER", "iq")
SITE_PASSWORD = os.environ.get("IQ_SITE_PASSWORD", "")

CAMPAIGNS = {
    "Scener ur en fylla": {
        "phrases": ["scener ur en fylla"],
        "hashtags": ["scenerurenfylla", "scenerurenfyllan", "scenerurfylla"]},
    "Ruset": {"phrases": ["ruset"], "hashtags": ["ruset", "ruset2"]},
    "LiqLab": {"phrases": ["liqlab", "liq lab", "liq-lab", "dejtingexperiment",
                           "dejting-experiment", "dejting experiment", "felicia"],
               "hashtags": ["liqlab", "liqlabb"]},
    "Skickat": {"phrases": ["skickat"], "hashtags": ["skickat"]},
}


def _listtext(v):
    v = v or ""
    try:
        p = json.loads(v)
        if isinstance(p, list):
            return " ".join(str(x) for x in p)
    except Exception:
        pass
    return v


def match_campaign(row):
    caption = (row.get("caption", "") or "").lower()
    hay = " ".join([caption,
                    (row.get("transkript", "") or "").lower(),
                    _listtext(row.get("text_i_bild", "")).lower()])
    tags = set((row.get("hashtags", "") or "").lower().split())
    tags |= set(re.findall(r"#(\w+)", caption))
    for name, cfg in CAMPAIGNS.items():
        if any(h.lower() in tags for h in cfg.get("hashtags", [])):
            return name
        for p in cfg.get("phrases", []):
            if re.search(r"\b" + re.escape(p.lower()) + r"\b", hay):
                return name
    return ""


def manual_ids():
    """Ev. manuellt angivna kampanj-id (kampanj_overrides.csv: video_id,kampanj)."""
    out = {}
    if os.path.isfile(OVR_FILE):
        with open(OVR_FILE, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                vid = (r.get("video_id") or "").strip()
                name = (r.get("kampanj") or "").strip()
                if vid and name:
                    out[vid] = name
    return out


def api(method, body=None):
    req = urllib.request.Request(SITE_URL + "/api/overrides", method=method)
    tok = base64.b64encode(f"{SITE_USER}:{SITE_PASSWORD}".encode()).decode()
    req.add_header("Authorization", "Basic " + tok)
    if body is not None:
        req.add_header("Content-Type", "application/json")
        body = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data=body, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode(errors="replace")
        except Exception:
            pass
        hint = ""
        if e.code == 401:
            hint = "  (fel användarnamn/lösenord – kolla IQ_SITE_USER/PASSWORD)"
        elif e.code == 500:
            hint = ("  (KV svarar inte – vanligast: koppla KV-storen och gör en "
                    "REDEPLOY i Vercel efteråt, annars saknar funktionen nycklarna)")
        sys.exit(f"HTTP {e.code} från /api/overrides{hint}\n  Svar: {detail}")


def main():
    dry = "--dry" in sys.argv[1:]
    if not os.path.isfile(CSV_PATH):
        sys.exit(f"Hittar inte {CSV_PATH} – kör analyze.py först.")
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    hits, per = {}, Counter()
    for r in rows:
        name = match_campaign(r)
        if name:
            hits[r.get("video_id", "")] = name
            per[name] += 1
    for vid, name in manual_ids().items():
        hits[vid] = name
        per[name] += 0

    print(f"{len(rows)} inlägg. Kampanjträffar: {len(hits)}")
    for name in CAMPAIGNS:
        print(f"  {name}: {per.get(name, 0)}")

    if not hits:
        sys.exit("Inga träffar – inget att tagga.")
    if dry:
        print("\n(--dry: skrev inget. Ta bort --dry för att skriva till KV.)")
        return
    if not SITE_URL or not SITE_PASSWORD:
        sys.exit("Sätt IQ_SITE_URL och IQ_SITE_PASSWORD (miljövariabler).")

    print(f"\nHämtar befintliga overrides från {SITE_URL}/api/overrides ...")
    ov = api("GET") or {}
    if not isinstance(ov, dict):
        ov = {}
    for vid in hits:
        ov.setdefault(vid, {})["strategi"] = "kampanj"
    print(f"Skriver {len(hits)} kampanj-taggar (totalt {len(ov)} inlägg med "
          f"overrides) till KV ...")
    res = api("POST", ov)
    print("Klart:", res)
    print("Ladda om dashboarden – kampanjinläggen ligger nu under strategi=kampanj.")


if __name__ == "__main__":
    main()
