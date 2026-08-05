#!/usr/bin/env python3
"""Sond: hur representeras foto-/karusellinlägg i IQ:s profilrutnät?

Öppnar profilen i din inloggade Chrome-profil, scrollar en stund och skriver ut
vilka länkmönster som finns – så vi ser varför /photo/-inläggen inte fångas.
Läser och skriver INGET i datamappen. Kör: python3 probe_profile.py
"""

import collections
import os
import re
import time

from playwright.sync_api import sync_playwright

PROFILE_URL = "https://www.tiktok.com/@iqinitiativet"
USER_DATA_DIR = os.path.expanduser("~/iq_tiktok_chrome_profil")
KNOWN = ["7655325406882647299", "7577754708085984534"]  # karusell + enkelfoto


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            USER_DATA_DIR, headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(PROFILE_URL, wait_until="domcontentloaded")
        input("Logga in i fönstret om det behövs, tryck sedan ENTER här ...")

        # Scrolla en stund så rutnätet hinner ladda (de nyaste ligger överst).
        for _ in range(20):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.2)

        hrefs = page.eval_on_selector_all("a", "els => els.map(e => e.href)")
        pat = collections.Counter()
        for h in hrefs:
            if "/video/" in h:
                pat["/video/"] += 1
            elif "/photo/" in h:
                pat["/photo/"] += 1
        print(f"\nAntal <a>-taggar på sidan: {len(hrefs)}")
        print(f"Länkmönster: {dict(pat)}")

        # Andra länkar som pekar på ett inlägg (siffer-id) men varken video/photo.
        others = [h for h in hrefs
                  if re.search(r"/\d{6,}", h) and "/video/" not in h and "/photo/" not in h]
        print(f"Andra id-länkar (ev. annat mönster), exempel: {others[:10]}")

        # Renderas de kända foto-inläggen alls på sidan?
        html = page.content()
        for k in KNOWN:
            as_link = [h for h in hrefs if k in h]
            print(f"KÄND {k}: i HTML={k in html} | som <a>-länk={as_link[:2]}")

        # Klickbara element som INTE är <a> men bär ett inläggs-id (t.ex. <div>).
        divlike = page.evaluate(
            "() => Array.from(document.querySelectorAll('[href],[data-video-id],"
            "[data-item-id]')).slice(0,5).map(e => ({tag:e.tagName, "
            "href:e.getAttribute('href'), vid:e.getAttribute('data-video-id'), "
            "iid:e.getAttribute('data-item-id')}))")
        print(f"Exempel på element med href/data-id: {divlike}")

        ctx.close()


if __name__ == "__main__":
    main()
