#!/usr/bin/env python3
"""
Kontrollerar vilka TikTok-inlägg som fortfarande finns kvar (inte borttagna)
genom att läsa sidans inbäddade statusCode (samma data som scrapern använder).

  statusCode 0 + itemStruct  → "live"
  statusCode 10204           → "gone" ("item doesn't exist" = borttagen)
  okänt tillstånd            → "live" (flaggas inte i onödan)
  ingen data / blockerad sida→ tillfälligt, tas om vid nästa körning

Obs: oEmbed-endpointen (som användes förut) är opålitlig – den kan svara fel
åt BÅDA håll (fel för levande men ej inbäddningsbara videor, och 200 för redan
borttagna). Därför läser vi sidans statusCode i stället. /photo/-URL:er
normaliseras till /video/ (där datan server-renderas).

Resultatet skrivs till iq_tiktok_data/availability.csv (video_id,status,checked)
och ligger – precis som overrides.csv – SEPARAT från enriched.csv så att
content-pipelinen aldrig skriver över det. analyze.py läser filen och märker
borttagna inlägg i dashboarden.

Återupptagbart: redan kontrollerade rader hoppas över om de inte är äldre än
IQ_AVAIL_RECHECK_DAYS dagar (default 30). IQ_AVAIL_ALL=1 kontrollerar om alla.
IQ_LIMIT=N kontrollerar bara N stycken (bra för test).

    python check_availability.py
"""

import csv
import json
import os
import random
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT_DIR, "iq_tiktok_data")
CSV_PATH = os.path.join(DATA_DIR, "iq_tiktok_enriched.csv")
AVAIL_PATH = os.path.join(DATA_DIR, "availability.csv")

LIMIT = int(os.environ.get("IQ_LIMIT", "0") or 0)
RECHECK_DAYS = int(os.environ.get("IQ_AVAIL_RECHECK_DAYS", "30") or 30)
RECHECK_ALL = os.environ.get("IQ_AVAIL_ALL", "") not in ("", "0", "false")
MAX_RETRIES = int(os.environ.get("IQ_AVAIL_RETRIES", "4") or 4)
PAUSE = float(os.environ.get("IQ_AVAIL_PAUSE", "0.4") or 0.4)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# SSL via den förkonfigurerade agent-proxyns CA-bundle + proxy ur miljön.
_ca = os.environ.get("REQUESTS_CA_BUNDLE") or "/root/.ccr/ca-bundle.crt"
_ctx = ssl.create_default_context()
if os.path.exists(_ca):
    try:
        _ctx.load_verify_locations(_ca)
    except Exception:
        pass
_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler(urllib.request.getproxies()),
    urllib.request.HTTPSHandler(context=_ctx),
)


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def days_since(d):
    try:
        then = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - then).days
    except Exception:
        return 10 ** 6


RE_REHY = re.compile(
    r'__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.S)


def check_one(url):
    """Returnerar 'live' eller 'gone', eller kastar vid tillfälligt/oklart fel.

    Läser sidans server-renderade statusCode (webapp.video-detail) – pålitligt,
    till skillnad från oEmbed. /photo/ normaliseras till /video/ (SSR:as där)."""
    url = url.replace("/photo/", "/video/")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8"})
    with _opener.open(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")
    m = RE_REHY.search(html)
    if not m:
        # Blockerad/ofullständig sida (t.ex. rate-limit) – inte ett svar vi kan
        # lita på. Kasta så det blir ett återförsök i stället för falskt "gone".
        raise RuntimeError("ingen data på sidan (blockerad/ofullständig)")
    vd = (json.loads(m.group(1)).get("__DEFAULT_SCOPE__", {})
          .get("webapp.video-detail", {}))
    status = vd.get("statusCode")
    has_item = bool(vd.get("itemInfo", {}).get("itemStruct"))
    if status == 0 and has_item:
        return "live"
    if status == 10204:                 # "item doesn't exist" = borttagen/raderad
        return "gone"
    # Okänt tillstånd (privat, regionblock, udda kod) → flagga INTE som borttaget.
    return "live"


def check(url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return check_one(url)
        except Exception as e:
            if attempt < MAX_RETRIES:
                wait = min(2 ** attempt, 20) + random.uniform(0, 1.0)
                print(f"    … tillfälligt fel (försök {attempt}/{MAX_RETRIES}), "
                      f"väntar {wait:.0f}s: {e}")
                time.sleep(wait)
                continue
            raise


def load_avail():
    out = {}
    if os.path.exists(AVAIL_PATH):
        with open(AVAIL_PATH, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                vid = (r.get("video_id") or "").strip()
                if vid:
                    out[vid] = {"status": r.get("status", ""),
                                "checked": r.get("checked", "")}
    return out


def save_avail(avail):
    tmp = AVAIL_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["video_id", "status", "checked"])
        for vid in sorted(avail):
            w.writerow([vid, avail[vid]["status"], avail[vid]["checked"]])
    os.replace(tmp, AVAIL_PATH)


def main():
    if not os.path.exists(CSV_PATH):
        sys.exit(f"Hittar inte {CSV_PATH} – kör content-pipelinen först.")
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    posts = [(r.get("video_id", "").strip(), r.get("url", "").strip())
             for r in rows if r.get("video_id") and r.get("url")]

    avail = load_avail()

    def needs_check(vid):
        if RECHECK_ALL or vid not in avail:
            return True
        return days_since(avail[vid].get("checked", "")) >= RECHECK_DAYS

    todo = [(vid, url) for vid, url in posts if needs_check(vid)]
    if LIMIT:
        todo = todo[:LIMIT]

    print(f"{len(posts)} inlägg totalt, {len(avail)} redan kontrollerade. "
          f"Kontrollerar {len(todo)} nu (recheck efter {RECHECK_DAYS} dagar).")

    fails = 0
    for i, (vid, url) in enumerate(todo, 1):
        try:
            status = check(url)
            avail[vid] = {"status": status, "checked": today()}
            mark = "✓ live" if status == "live" else "✗ BORTTAGEN"
            print(f"[{i}/{len(todo)}] {vid}  {mark}")
            fails = 0
            if i % 20 == 0:
                save_avail(avail)
            time.sleep(PAUSE + random.uniform(0, 0.2))
        except KeyboardInterrupt:
            print("\nAvbrutet – sparar det som hunnits.")
            break
        except Exception as e:
            print(f"  ! kunde inte kontrollera {vid}: {e}")
            fails += 1
            if fails >= 6:
                print("\nAvbryter: 6 fel i rad (troligen rate-limit). Inget "
                      "förlorat – kör igen senare, klara rader hoppas över.")
                break

    save_avail(avail)
    live = sum(1 for v in avail.values() if v["status"] == "live")
    gone = sum(1 for v in avail.values() if v["status"] == "gone")
    print(f"\nKlart. {live} live, {gone} borttagna, {len(avail)} kontrollerade "
          f"totalt. Sparat i {AVAIL_PATH}.")


if __name__ == "__main__":
    main()
