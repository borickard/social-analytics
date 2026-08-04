#!/usr/bin/env python3
"""
IQ TikTok-scraper  –  Ström A (resultatdata) + nedladdning av videofiler.

Vad skriptet gör
----------------
1. Öppnar er profil i din INLOGGADE Chrome-profil (mindre bot-skydd, ingen headless).
2. Scrollar profilen tills alla klipp laddats och samlar in video-URL:erna.
3. För varje video läser den siffror + caption ur sidans inbäddade JSON
   (#__UNIVERSAL_DATA_FOR_REHYDRATION__) – ingen OCR, inga skärmdumpar.
4. Laddar ner själva videofilen med yt-dlp (för innehållsanalysen sen).
5. Skriver en CSV (en rad per video) + en rå JSONL som säkerhetskopia.

RÄCKVIDD (reach) ingår INTE med flit – det kräver TikTok Studio och är den
enda knöliga biten. Vi utgår från visningar (playCount). Reach kan joinas in
på video-id senare via en manuell CSV-export.

Körning är återupptagbar: redan hämtade videor hoppas över, så du kan avbryta
och köra igen.

Förberedelser (engångs)
-----------------------
    pip install playwright
    playwright install chromium
    # yt-dlp: pip install yt-dlp   (eller: brew install yt-dlp)

Ställ in USER_DATA_DIR nedan till en egen mapp. Första gången skriptet kör
öppnas ett Chrome-fönster – logga in på TikTok där om du inte redan är det,
och låt det stå kvar. Nästa körning minns inloggningen.

Kör:
    python iq_tiktok_scraper.py
"""

import csv
import json
import os
import random
import re
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

# ------------------------------------------------------------------ CONFIG ---
PROFILE_URL = "https://www.tiktok.com/@iqinitiativet"

# En egen mapp för Chrome-profilen som skriptet använder (håller inloggningen).
USER_DATA_DIR = os.path.expanduser("~/iq_tiktok_chrome_profil")

# Datan hamnar i en undermapp i projektet (bredvid skriptet), oavsett var du
# står i Terminal. Mappen är gitignore:ad så den laddas aldrig upp.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(PROJECT_DIR, "iq_tiktok_data")
VIDEO_DIR = os.path.join(OUT_DIR, "videos")
CSV_PATH = os.path.join(OUT_DIR, "iq_tiktok_metrics.csv")
RAW_PATH = os.path.join(OUT_DIR, "iq_tiktok_raw.jsonl")

DOWNLOAD_VIDEOS = False         # sätt True för att även ladda ner videofilerna
USE_CHROME_COOKIES = True       # låter yt-dlp använda din Chrome-inloggning

# Snäll, mänsklig takt – minskar risk för strypning. Öka vid problem.
MIN_DELAY, MAX_DELAY = 2.5, 5.0

# Begränsa antal videor (nyast först). Bra för att testa. Sätt 0 för alla.
MAX_VIDEOS = 20

CSV_FIELDS = [
    "video_id", "url", "publiceringsdatum", "langd_sek",
    "visningar", "likes", "kommentarer", "delningar", "sparade",
    "engagement_rate",
    "caption", "caption_langd", "antal_hashtags", "hashtags",
    "musik", "boostad", "is_ad", "ad_metadata", "nedladdad",
]

# Nyckel-ledtrådar för annons-/betalrelaterade fält i den inbäddade JSON:en.
AD_KEY_HINTS = ("isad", "paid", "promot", "commerce", "sponsor", "brand",
                "adauthor", "adlabel", "advert")
# -----------------------------------------------------------------------------


def sleep_a_bit():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def already_done():
    """Video-id:n som redan finns i CSV:n, för återupptagning."""
    done = set()
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                done.add(row["video_id"])
    return done


def collect_video_urls(page):
    """Scrolla profilen tills antalet länkar slutar växa; returnera URL:erna."""
    print("Scrollar profilen för att ladda alla klipp ...")
    seen, stagnant = set(), 0
    # Ta bara IQ:s EGNA videor. Profilsidan innehåller även rekommenderade
    # klipp från andra konton – utan filtret slinker de med.
    own_prefix = PROFILE_URL.rstrip("/") + "/video/"
    while stagnant < 5:
        hrefs = page.eval_on_selector_all(
            'a[href*="/video/"]', "els => els.map(e => e.href)")
        before = len(seen)
        seen.update(h.split("?")[0] for h in hrefs
                    if h.split("?")[0].startswith(own_prefix))
        # Nyaste ligger överst, så vid en begränsad testkörning kan vi sluta
        # så fort vi har tillräckligt – slipper scrolla hela profilen.
        if MAX_VIDEOS and len(seen) >= MAX_VIDEOS:
            break
        stagnant = stagnant + 1 if len(seen) == before else 0
        # Robust scroll: rulla dokumentet hela vägen ner + putta sista kortet
        # i vy. page.mouse.wheel kräver rätt muspekarläge och missar ofta.
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.evaluate(
            "() => { const a = document.querySelectorAll('a[href*=\"/video/\"]');"
            " if (a.length) a[a.length - 1].scrollIntoView(); }")
        try:
            page.keyboard.press("End")
        except Exception:
            pass
        time.sleep(random.uniform(1.5, 3.0))
    print(f"Hittade {len(seen)} videor.")

    # Sortera nyast först (TikToks video-id växer med tiden), begränsa ev.
    def _vid_id(u):
        tail = u.rstrip("/").split("/")[-1]
        return int(tail) if tail.isdigit() else 0
    ordered = sorted(seen, key=_vid_id, reverse=True)
    if MAX_VIDEOS:
        ordered = ordered[:MAX_VIDEOS]
        print(f"Begränsar till {len(ordered)} nyaste (MAX_VIDEOS={MAX_VIDEOS}).")
    return ordered


def dig(d, *path, default=None):
    """Säker nästlad uppslagning i dict:ar."""
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def extract_ad_fields(item):
    """
    Samla alla annons-/betalrelaterade fält som TikTok exponerar för videon.
    Skannar toppnivån + kända nästlade behållare (commerce/ad-info) och tar med
    allt vars nyckel matchar AD_KEY_HINTS. Så ser vi vad som FAKTISKT finns i
    datan i stället för att gissa – och missar inget om TikTok byter namn.
    """
    found = {}

    def scan(d, prefix=""):
        if not isinstance(d, dict):
            return
        for k, v in d.items():
            if isinstance(v, (dict, list)):
                continue
            if any(h in str(k).lower() for h in AD_KEY_HINTS):
                found[prefix + str(k)] = v

    scan(item)
    for container in ("commerceInfo", "commerce_info", "adInfo", "BAInfo",
                      "adInfoV2", "item_control"):
        scan(item.get(container), prefix=f"{container}.")
    return found


def extract_item(page):
    """Plocka ut itemStruct ur den inbäddade rehydration-JSON:en."""
    raw = page.eval_on_selector(
        "#__UNIVERSAL_DATA_FOR_REHYDRATION__", "el => el.textContent")
    data = json.loads(raw)
    scope = data.get("__DEFAULT_SCOPE__", {})
    item = dig(scope, "webapp.video-detail", "itemInfo", "itemStruct", default={})
    return item


def parse_row(item):
    stats = item.get("stats", {}) or item.get("statsV2", {})
    desc = item.get("desc", "") or ""
    hashtags = re.findall(r"#(\w+)", desc)
    ad_fields = extract_ad_fields(item)
    views = int(stats.get("playCount", 0) or 0)
    likes = int(stats.get("diggCount", 0) or 0)
    comments = int(stats.get("commentCount", 0) or 0)
    shares = int(stats.get("shareCount", 0) or 0)
    saves = int(stats.get("collectCount", 0) or 0)
    eng = round((likes + comments + shares + saves) / views, 4) if views else ""
    created = item.get("createTime")
    date = ""
    if created:
        date = time.strftime("%Y-%m-%d", time.gmtime(int(created)))
    return {
        "video_id": item.get("id", ""),
        "url": f"{PROFILE_URL}/video/{item.get('id','')}",
        "publiceringsdatum": date,
        "langd_sek": dig(item, "video", "duration", default=""),
        "visningar": views,
        "likes": likes,
        "kommentarer": comments,
        "delningar": shares,
        "sparade": saves,
        "engagement_rate": eng,
        "caption": desc,
        "caption_langd": len(desc),
        "antal_hashtags": len(hashtags),
        "hashtags": " ".join(hashtags),
        "musik": dig(item, "music", "title", default=""),
        # isAd speglar boost-status för IQ:s konto (verifierat mot känd data).
        "boostad": ("ja" if item.get("isAd") is True
                    else "nej" if item.get("isAd") is False else ""),
        "is_ad": item.get("isAd", ""),
        "ad_metadata": json.dumps(ad_fields, ensure_ascii=False) if ad_fields else "",
        "nedladdad": "",
    }


def download_video(url, video_id):
    out = os.path.join(VIDEO_DIR, f"{video_id}.%(ext)s")
    cmd = ["yt-dlp", "--no-warnings", "-o", out, url]
    if USE_CHROME_COOKIES:
        cmd += ["--cookies-from-browser", "chrome"]
    try:
        subprocess.run(cmd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "ja"
    except Exception as e:
        print(f"  ! nedladdning misslyckades ({video_id}): {e}")
        return "nej"


def append_csv(row):
    new = not os.path.exists(CSV_PATH)
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def main():
    os.makedirs(VIDEO_DIR, exist_ok=True)
    done = already_done()
    if done:
        print(f"Återupptar – {len(done)} videor redan klara, hoppar över dem.")

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto(PROFILE_URL, wait_until="domcontentloaded")
        # Ge dig chans att logga in / passera ev. captcha vid första körningen.
        input("Logga in i fönstret om det behövs, tryck sedan ENTER här ...")

        urls = collect_video_urls(page)

        for i, url in enumerate(urls, 1):
            vid = url.rstrip("/").split("/")[-1]
            if vid in done:
                continue
            print(f"[{i}/{len(urls)}] {url}")
            try:
                page.goto(url, wait_until="domcontentloaded")
                # Vänta bara tills JSON-taggen med siffrorna finns – snabbare
                # och stabilare än att vänta på att hela nätverket blir tyst.
                # state="attached": <script>-taggen finns i DOM men ritas
                # aldrig ut, så vi får INTE vänta på att den blir "synlig".
                page.wait_for_selector(
                    "#__UNIVERSAL_DATA_FOR_REHYDRATION__",
                    state="attached", timeout=15000)
                item = extract_item(page)
                if not item:
                    print("  ! ingen data hittad – hoppar över")
                    continue
                # Skyddsnät: skriv bara om laddad video matchar URL:ens id.
                # Skyddar mot att en råkad scroll byter klipp mitt i läsningen –
                # en felläsning skrivs aldrig, den plockas upp vid nästa körning.
                if str(item.get("id", "")) != str(vid):
                    print(f"  ! fel video laddad (fick id {item.get('id')}), "
                          "hoppar över – körs igen nästa gång")
                    continue
                with open(RAW_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                row = parse_row(item)
                if DOWNLOAD_VIDEOS:
                    row["nedladdad"] = download_video(url, row["video_id"])
                append_csv(row)
            except Exception as e:
                print(f"  ! fel på {url}: {e}")
            sleep_a_bit()

        ctx.close()

    print(f"\nKlart. CSV: {CSV_PATH}\nVideor: {VIDEO_DIR}")


if __name__ == "__main__":
    sys.exit(main())
