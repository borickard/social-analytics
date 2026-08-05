#!/usr/bin/env python3
"""Sond: var hämtas foto-/karuselldatan ifrån?

Foto-detaljsidor bäddar INTE in itemStruct i sid-JSON:en (till skillnad från
videor). Datan laddas via ett API-anrop (XHR). Den här sonden lyssnar på
nätverkstrafiken när ett känt karusellinlägg öppnas och visar vilket anrop som
bär datan + i vilket format – så vi kan bygga rätt hämtning.

Läser/skriver inget i datamappen. Kör: python3 probe_photo.py
"""

import json
import os

from playwright.sync_api import sync_playwright

USER_DATA_DIR = os.path.expanduser("~/iq_tiktok_chrome_profil")
URL = "https://www.tiktok.com/@iqinitiativet/photo/7655325406882647299"  # karusell
MARKERS = ("imagePost", "image_post_info", "itemStruct", "aweme_detail", "diggCount")


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            USER_DATA_DIR, headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        captured = []

        def on_response(resp):
            try:
                url = resp.url
                if "/api/" not in url:
                    return
                ct = resp.headers.get("content-type", "")
                if "json" not in ct and "text" not in ct:
                    return
                body = resp.text()
                if any(m in body for m in MARKERS):
                    captured.append((url, body))
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(URL, wait_until="domcontentloaded")
        input("Vänta tills inlägget syns i fönstret, tryck sedan ENTER här ...")
        page.wait_for_timeout(3000)

        print(f"\nAntal API-svar med item-data: {len(captured)}")
        for url, body in captured[:8]:
            print("\n--- URL:", url[:150])
            try:
                j = json.loads(body)
            except Exception:
                print("    (ej JSON)")
                continue
            print("    top-nycklar:", list(j.keys())[:12])
            it = j.get("itemInfo", {}).get("itemStruct") if isinstance(j.get("itemInfo"), dict) else None
            if it:
                print("    FORMAT: web (itemInfo.itemStruct)")
                print("    id:", it.get("id"), "| imagePost:", bool(it.get("imagePost")))
                imgs = (it.get("imagePost") or {}).get("images", [])
                print("    antal bilder:", len(imgs))
                if imgs:
                    ul = imgs[0].get("imageURL", {}).get("urlList", [])
                    print("    urlList längd:", len(ul), "| ex:", (ul[0][:90] if ul else None))
            elif isinstance(j.get("aweme_detail"), dict):
                ad = j["aweme_detail"]
                print("    FORMAT: app (aweme_detail)")
                print("    aweme_id:", ad.get("aweme_id"),
                      "| image_post_info:", bool(ad.get("image_post_info")))
                imgs = (ad.get("image_post_info") or {}).get("images", [])
                print("    antal bilder:", len(imgs))
                if imgs:
                    du = imgs[0].get("display_image", {}).get("url_list", [])
                    print("    url_list längd:", len(du), "| ex:", (du[0][:90] if du else None))
            else:
                print("    Okänt format – top-nycklar ovan.")
        ctx.close()


if __name__ == "__main__":
    main()
