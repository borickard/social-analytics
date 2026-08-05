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
import glob
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
THUMB_DIR = os.path.join(OUT_DIR, "thumbnails")
CSV_PATH = os.path.join(OUT_DIR, "iq_tiktok_metrics.csv")
RAW_PATH = os.path.join(OUT_DIR, "iq_tiktok_raw.jsonl")

DOWNLOAD_VIDEOS = False         # sätt True för att även ladda ner videofilerna
USE_CHROME_COOKIES = True       # låter yt-dlp använda din Chrome-inloggning

# Återupptagning:
#   True  = hoppa över videor som redan har metadata (ladda ändå ner ev.
#           saknade videofiler). Bra för att fortsätta en avbruten körning
#           eller för att köra steg 2 (nedladdning) efter steg 1 (metadata).
#   False = hämta om metadatan och UPPDATERA siffrorna (visningar/likes ändras
#           ju över tid). Videofiler som redan finns laddas aldrig ner på nytt.
SKIP_SCRAPED = True

# Snäll, mänsklig takt – minskar risk för strypning. Öka vid problem.
MIN_DELAY, MAX_DELAY = 2.5, 5.0

# Begränsa antal videor (nyast först). Bra för att testa. Sätt 0 för alla.
MAX_VIDEOS = 20

CSV_FIELDS = [
    "video_id", "url", "publiceringsdatum", "langd_sek",
    "visningar", "likes", "kommentarer", "delningar", "sparade",
    "engagement_rate", "rackvidd",
    "caption", "caption_langd", "antal_hashtags", "hashtags",
    "musik", "musik_original", "is_ad", "nedladdad", "thumbnail",
]
# -----------------------------------------------------------------------------


def sleep_a_bit():
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))


def load_existing():
    """Läs befintlig CSV till {video_id: rad} – för återupptagning/uppdatering."""
    rows = {}
    if os.path.exists(CSV_PATH):
        with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows[row["video_id"]] = row
    return rows


def write_all(rows):
    """Skriv hela CSV:n atomiskt (via en temp-fil) från {video_id: rad}.
    Atomiskt = ett avbrott mitt i skrivningen lämnar aldrig en trasig CSV."""
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for row in rows.values():
            w.writerow(row)
    os.replace(tmp, CSV_PATH)


def video_exists(video_id):
    """Finns videofilen redan nedladdad? (valfri filändelse)"""
    return bool(glob.glob(os.path.join(VIDEO_DIR, f"{video_id}.*")))


def thumb_exists(video_id):
    """Finns thumbnailen redan nedladdad?"""
    return bool(glob.glob(os.path.join(THUMB_DIR, f"{video_id}.*")))


def thumb_rel(video_id):
    """Relativ sökväg (t.ex. thumbnails/<id>.jpg) om thumbnailen finns, annars tom."""
    hits = glob.glob(os.path.join(THUMB_DIR, f"{video_id}.*"))
    return os.path.relpath(hits[0], OUT_DIR) if hits else ""


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
        "rackvidd": "",   # fylls i i efterhand från TikTok Studio (join_reach.py)
        "caption": desc,
        "caption_langd": len(desc),
        "antal_hashtags": len(hashtags),
        "hashtags": " ".join(hashtags),
        "musik": dig(item, "music", "title", default=""),
        # music.title är lokal-beroende ("originalljud"/"original sound") – den
        # rena signalen är originalflaggan (eget ljud vs licensierad musik).
        "musik_original": ("ja" if dig(item, "music", "original") is True
                           else "nej" if dig(item, "music", "original") is False
                           else ""),
        # isAd = boostad (verifierat mot känd data): True = boostad, False = organisk.
        "is_ad": item.get("isAd", ""),
        "nedladdad": "",
        "thumbnail": "",   # sätts till thumbnails/<id>.jpg om filen finns
    }


def download_video(url, video_id):
    # Idempotent: hämta bara det som saknas (video och/eller thumbnail).
    have_video = video_exists(video_id)
    have_thumb = thumb_exists(video_id)
    if have_video and have_thumb:
        return "ja (fanns redan)"
    cmd = ["yt-dlp", "--no-warnings"]
    if have_video:
        cmd += ["--skip-download"]      # videon finns – hämta bara thumbnailen
    cmd += [
        # Thumbnail till egen mapp, alltid som .jpg (kräver ffmpeg) så
        # filnamnen blir förutsägbara: thumbnails/<id>.jpg (bra för dashboard).
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "-o", os.path.join(VIDEO_DIR, f"{video_id}.%(ext)s"),
        "-o", f"thumbnail:{os.path.join(THUMB_DIR, video_id)}.%(ext)s",
        url,
    ]
    if USE_CHROME_COOKIES:
        cmd += ["--cookies-from-browser", "chrome"]
    try:
        subprocess.run(cmd, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return "ja"
    except Exception as e:
        print(f"  ! nedladdning misslyckades ({video_id}): {e}")
        return "nej"


def main():
    os.makedirs(VIDEO_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    rows = load_existing()
    if rows:
        lage = "hoppar över dem" if SKIP_SCRAPED else "uppdaterar deras siffror"
        print(f"{len(rows)} videor finns redan i CSV:n ({lage}).")

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

            # Återupptagningsläge: metadatan finns redan.
            if SKIP_SCRAPED and vid in rows:
                # Ladda ändå ner video/thumbnail om något saknas (t.ex. steg 2
                # efter att steg 1 hämtat bara siffror). Finns allt görs inget.
                if DOWNLOAD_VIDEOS and not (video_exists(vid) and thumb_exists(vid)):
                    print(f"[{i}/{len(urls)}] {vid} – metadata finns, hämtar video/thumbnail")
                    rows[vid]["nedladdad"] = download_video(url, vid)
                    rows[vid]["thumbnail"] = thumb_rel(vid)
                    write_all(rows)
                else:
                    print(f"[{i}/{len(urls)}] {vid} – redan klar, hoppar över")
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
                row["thumbnail"] = thumb_rel(row["video_id"])
                rows[row["video_id"]] = row     # infoga/ersätt
                write_all(rows)                 # spara progress efter varje video
            except Exception as e:
                print(f"  ! fel på {url}: {e}")
            sleep_a_bit()

        ctx.close()

    print(f"\nKlart. CSV: {CSV_PATH}\nVideor: {VIDEO_DIR}")


if __name__ == "__main__":
    sys.exit(main())
