#!/usr/bin/env python3
"""
Märk upp högtider/tillfällen som en egen dimension.

Lägger till kolumnen `hogtid` i iq_tiktok_enriched.csv (nyår, jul, midsommar,
valborg, studenten, halloween, sommarlov, födelsedag, påsk, kräftskiva …).
Matchningen är deterministisk (nyckelord i caption + transkript + text i bild +
hashtags), med ordgränser så delsträngar inte träffar (t.ex. "jul" i "juli").

Kör:
    python tag_occasions.py --dry     # visa träffar, skriv inget
    python tag_occasions.py           # skriv kolumnen hogtid
"""

import csv
import json
import os
import re
import sys
from collections import Counter

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(PROJECT_DIR, "iq_tiktok_data", "iq_tiktok_enriched.csv")

# Per högtid: phrases (matchas som hela ord i caption/transkript/bildtext) och
# hashtags (matchas exakt mot inläggets hashtags). Justera fritt.
OCCASIONS = {
    "Nyår": {"phrases": ["nyår", "nyårs", "nyårsafton", "gott nytt år",
                         "nyårslöfte", "nyårslöften"],
             "hashtags": ["nyår", "nyar", "newyear", "nyårsafton"]},
    "Jul": {"phrases": ["jul", "julen", "julafton", "julbord", "lucia",
                        "advent", "god jul", "mellandagarna"],
            "hashtags": ["jul", "christmas", "julbord"]},
    "Midsommar": {"phrases": ["midsommar", "midsommarafton"],
                  "hashtags": ["midsommar"]},
    "Valborg": {"phrases": ["valborg", "valborgsmässoafton", "sista april"],
                "hashtags": ["valborg"]},
    "Studenten": {"phrases": ["studenten", "studentflak", "studentmössa",
                             "utspring", "mössans dag", "studentfest"],
                  "hashtags": ["studenten", "student2024", "student2025", "student2026"]},
    "Halloween": {"phrases": ["halloween"], "hashtags": ["halloween"]},
    "Sommarlov": {"phrases": ["sommarlov", "skolavslutning", "skolavslut",
                             "sista skoldagen"],
                  "hashtags": ["sommarlov"]},
    "Födelsedag": {"phrases": ["födelsedag", "födelsedagsfest", "födelsedagen"],
                   "hashtags": ["födelsedag", "bday", "birthday"]},
    "Påsk": {"phrases": ["påsk", "påskafton", "påskhelgen"], "hashtags": ["påsk"]},
    "Kräftskiva": {"phrases": ["kräftskiva", "kräftskivor", "kräftor"],
                   "hashtags": ["kräftskiva", "kräftor"]},
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


def match_occasion(row):
    """Returnera (högtid, anledning) för första träffen, annars ('','')."""
    caption = (row.get("caption", "") or "").lower()
    haystack = " ".join([
        caption,
        (row.get("transkript", "") or "").lower(),
        _listtext(row.get("text_i_bild", "")).lower(),
    ])
    tags = set((row.get("hashtags", "") or "").lower().split())
    tags |= set(re.findall(r"#(\w+)", caption))
    for name, cfg in OCCASIONS.items():
        for h in cfg.get("hashtags", []):
            if h.lower() in tags:
                return name, f"#{h.lower()}"
        for p in cfg.get("phrases", []):
            if re.search(r"\b" + re.escape(p.lower()) + r"\b", haystack):
                return name, f"fras:{p}"
    return "", ""


def main():
    dry = "--dry" in sys.argv[1:]
    if not os.path.exists(CSV_PATH):
        sys.exit(f"Hittar inte {CSV_PATH} – kör iq_tiktok_content_pipeline.py först.")
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("Enriched-CSV:n är tom.")

    hits = Counter()
    examples = {}
    for r in rows:
        name, reason = match_occasion(r)
        r["hogtid"] = name
        if name:
            hits[name] += 1
            examples.setdefault(name, []).append(
                (r.get("video_id", ""), reason, (r.get("caption", "") or "")[:60]))

    total = sum(hits.values())
    print(f"{len(rows)} inlägg. Högtidsträffar: {total} "
          f"({len(rows)-total} utan högtid)")
    for name in OCCASIONS:
        if hits.get(name):
            print(f"\n  {name}: {hits[name]}")
            for vid, reason, cap in examples.get(name, [])[:6]:
                print(f"     {vid}  [{reason}]  {cap}")

    if dry:
        print("\n(--dry: skrev inget.)")
        return
    fields = list(rows[0].keys())
    if "hogtid" not in fields:
        fields.append("hogtid")
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, CSV_PATH)
    print(f"\nSkrev kolumnen 'hogtid' till {os.path.basename(CSV_PATH)}.")


if __name__ == "__main__":
    main()
