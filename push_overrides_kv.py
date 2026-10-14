#!/usr/bin/env python3
"""
Ladda upp taggrättelser från iq_tiktok_data/overrides.csv till den DELADE
databasen (Upstash/Redis via /api/overrides). Skriver varje fält som en delta,
så inget skrivs över – befintliga värden i databasen behålls, dina läggs till.

Använd t.ex. för att "sätta" den gemensamma baslinjen i själva databasen
(utöver att den redan är inbakad i bygget).

Kör (på din dator – lösenordet lämnar aldrig din maskin):
    IQ_SITE_URL=https://iq-analytics.vercel.app \\
    IQ_SITE_USER=iq IQ_SITE_PASSWORD=ditt-lösenord \\
    python push_overrides_kv.py --dry     # visa vad som skulle skickas
    # ... utan --dry för att faktiskt skriva till databasen.

Läser en annan fil med:  python push_overrides_kv.py sökväg/till/overrides.csv
"""

import base64
import csv
import json
import os
import sys
import urllib.error
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CSV = os.path.join(PROJECT_DIR, "iq_tiktok_data", "overrides.csv")

SITE_URL = os.environ.get("IQ_SITE_URL", "").rstrip("/")
SITE_USER = os.environ.get("IQ_SITE_USER", "iq")
SITE_PASSWORD = os.environ.get("IQ_SITE_PASSWORD", "")
BATCH = int(os.environ.get("IQ_BATCH", "200") or 200)

# Fält som dashboarden känner igen (matchar EDITABLE i analyze.py).
EDITABLE = {"strategi", "typ", "kategori", "format", "budskapston", "hogtid",
            "har_hook"}


def read_deltas(path):
    deltas = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            vid = (r.get("video_id") or "").strip()
            fld = (r.get("field") or "").strip()
            if vid and fld in EDITABLE:
                deltas.append({"id": vid, "field": fld, "value": r.get("value", "")})
    return deltas


def post(deltas):
    url = SITE_URL + "/api/overrides"
    body = json.dumps({"deltas": deltas}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    tok = base64.b64encode(f"{SITE_USER}:{SITE_PASSWORD}".encode()).decode()
    req.add_header("Authorization", "Basic " + tok)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
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
            hint = "  (databasen svarar inte – kolla att REDIS_URL finns i Vercel)"
        sys.exit(f"HTTP {e.code} från /api/overrides{hint}\n  Svar: {detail}")


def main():
    args = [a for a in sys.argv[1:] if a != "--dry"]
    dry = "--dry" in sys.argv[1:]
    path = args[0] if args else DEFAULT_CSV
    if not os.path.isfile(path):
        sys.exit(f"Hittar inte {path}")

    deltas = read_deltas(path)
    posts = len({d["id"] for d in deltas})
    print(f"{len(deltas)} fält-ändringar på {posts} inlägg i {os.path.basename(path)}.")
    if not deltas:
        sys.exit("Inget att skicka.")
    if dry:
        print("(--dry: skrev inget. Ta bort --dry för att skriva till databasen.)")
        return
    if not SITE_URL or not SITE_PASSWORD:
        sys.exit("Sätt IQ_SITE_URL och IQ_SITE_PASSWORD (miljövariabler).")

    sent = 0
    for i in range(0, len(deltas), BATCH):
        chunk = deltas[i:i + BATCH]
        res = post(chunk)
        sent += res.get("written", len(chunk)) if isinstance(res, dict) else len(chunk)
        print(f"  skrev {sent}/{len(deltas)} …")
    print(f"Klart. {sent} fält i databasen. Ladda om dashboarden – alla ser dem nu.")


if __name__ == "__main__":
    main()
