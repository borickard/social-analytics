#!/usr/bin/env python3
"""Sond: hur är en foto-/karusell-DETALJSIDA uppbyggd i den inbäddade JSON:en?

Öppnar ETT känt karusellinlägg i din inloggade Chrome-profil och skriver ut var
itemStruct ligger + hur bilderna är strukturerade – så vi kan laga extract_item
och image_urls. Läser/skriver inget i datamappen. Kör: python3 probe_photo.py
"""

import json
import os

from playwright.sync_api import sync_playwright

USER_DATA_DIR = os.path.expanduser("~/iq_tiktok_chrome_profil")
URL = "https://www.tiktok.com/@iqinitiativet/photo/7655325406882647299"  # karusell


def dig(d, *path, default=None):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def main():
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            USER_DATA_DIR, headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(URL, wait_until="domcontentloaded")
        input("Vänta tills inlägget laddat i fönstret, tryck sedan ENTER här ...")

        page.wait_for_selector("#__UNIVERSAL_DATA_FOR_REHYDRATION__",
                               state="attached", timeout=15000)
        raw = page.eval_on_selector(
            "#__UNIVERSAL_DATA_FOR_REHYDRATION__", "el => el.textContent")
        data = json.loads(raw)
        scope = data.get("__DEFAULT_SCOPE__", {})
        print("\nScope-nycklar:", list(scope.keys()))

        # Var finns itemInfo.itemStruct?
        with_struct = [k for k, v in scope.items()
                       if isinstance(v, dict) and dig(v, "itemInfo", "itemStruct")]
        print("Scope-nycklar med itemInfo.itemStruct:", with_struct)

        item = dig(scope, "webapp.video-detail", "itemInfo", "itemStruct", default={})
        print("Via webapp.video-detail: itemStruct hittad =", bool(item))
        if not item and with_struct:
            item = dig(scope[with_struct[0]], "itemInfo", "itemStruct", default={})
            print("Använder itemStruct från:", with_struct[0])

        if not item:
            print("!! Ingen itemStruct hittad. Övriga scope-nycklar visas ovan.")
            # Sök brett efter 'imagePost' var som helst i JSON:en.
            found = []

            def walk(o, path=""):
                if isinstance(o, dict):
                    for k, v in o.items():
                        if k == "imagePost":
                            found.append(path + "/imagePost")
                        walk(v, path + "/" + k)
                elif isinstance(o, list) and o:
                    walk(o[0], path + "[0]")
            walk(data)
            print("Hittade 'imagePost' på:", found[:5])
            ctx.close()
            return

        print("\nitem id:", item.get("id"))
        print("har 'video':", bool(item.get("video")),
              "| har 'imagePost':", bool(item.get("imagePost")))
        ip = item.get("imagePost") or {}
        imgs = ip.get("images", []) if isinstance(ip, dict) else []
        print("imagePost-nycklar:", list(ip.keys()) if isinstance(ip, dict) else None)
        print("antal bilder:", len(imgs))
        if imgs:
            first = imgs[0]
            print("nycklar i images[0]:", list(first.keys()))
            iu = first.get("imageURL")
            print("imageURL:", list(iu.keys()) if isinstance(iu, dict) else type(iu).__name__)
            ul = dig(first, "imageURL", "urlList", default=[])
            print("urlList längd:", len(ul))
            if ul:
                print("första URL:", ul[0][:90])
        # stats + desc funkar likadant?
        print("stats finns:", bool(item.get("stats")),
              "| desc:", (item.get("desc") or "")[:60])
        ctx.close()


if __name__ == "__main__":
    main()
