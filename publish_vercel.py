#!/usr/bin/env python3
"""
Förbered rapporten för Vercel: kopiera den byggda dashboarden + thumbnails till
public/ (som Vercel serverar). Kör efter analyze.py:

    python analyze.py
    python publish_vercel.py
    git add public && git commit -m "Uppdatera dashboard" && git push

Vercel bygger om automatiskt vid push. Overrides (taggrättelser) sparas i
molnet via /api/overrides och ligger kvar över deployer.
"""

import os
import shutil

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJECT_DIR, "iq_tiktok_data")
PUB = os.path.join(PROJECT_DIR, "public")


def main():
    report = os.path.join(DATA, "iq_analys.html")
    if not os.path.isfile(report):
        raise SystemExit("Hittar inte iq_tiktok_data/iq_analys.html – kör analyze.py först.")
    os.makedirs(PUB, exist_ok=True)
    shutil.copyfile(report, os.path.join(PUB, "index.html"))

    src = os.path.join(DATA, "thumbnails")
    dst = os.path.join(PUB, "thumbnails")
    n = 0
    if os.path.isdir(src):
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        n = len([f for f in os.listdir(dst) if not f.startswith(".")])

    size_mb = sum(os.path.getsize(os.path.join(dst, f)) for f in os.listdir(dst)) / 1e6 \
        if os.path.isdir(dst) else 0
    print(f"public/index.html uppdaterad. {n} thumbnails ({size_mb:.1f} MB).")
    print("Nästa: git add public && git commit && git push  → Vercel deployar.")


if __name__ == "__main__":
    main()
