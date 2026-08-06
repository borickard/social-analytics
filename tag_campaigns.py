#!/usr/bin/env python3
"""
Märk upp kampanjinnehåll vs "always on"-innehåll.

IQ:s större kampanjer (produceras av reklambyrå, mer polerat, kända namn) är
något annat än det löpande "always on"-innehållet som teamet gör med enklare
medel. De här kampanjerna känns igen på sina namn i caption/hashtags/bildtext.

Skriptet lägger till två kolumner i iq_tiktok_enriched.csv:
  kampanj          – kampanjnamnet om något matchar, annars tomt
  produktionsniva  – "kampanj" om en kampanj matchade, annars "always_on"

Matchningen är deterministisk (nyckelord) och reproducerbar. Distinkta namn
matchas både som fras (i caption/bildtext) och som hashtag; vanliga ord
(t.ex. "ruset", "skickat") matchas BARA som hashtag för att undvika falska
träffar. Justera CAMPAIGNS nedan efter behov.

Kör:
    python tag_campaigns.py            # visar träffar + skriver kolumnerna
    python tag_campaigns.py --dry      # visar bara träffar, skriver inget
"""

import csv
import json
import os
import re
import sys
from collections import Counter

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(PROJECT_DIR, "iq_tiktok_data", "iq_tiktok_enriched.csv")

# Kampanjkonfiguration. Per kampanj:
#   phrases  – matchas som delsträng i caption + text i bild (gemener). Använd
#              BARA för distinkta namn (annars falska träffar).
#   hashtags – matchas exakt mot inläggets hashtags (utan #, gemener). Säkert
#              även för namn som är vanliga ord.
CAMPAIGNS = {
    "Scener ur en fylla": {
        "phrases": ["scener ur en fylla"],
        "hashtags": ["scenerurenfylla", "scenerurenfyllan", "scenerurfylla"],
    },
    "Ruset": {
        # Bestämd form "ruset" är distinkt nog i IQ:s captions. Matchas som
        # helt ord (ordgräns) så "bruset"/"kruset" o.d. inte råkar träffa.
        "phrases": ["ruset"],
        "hashtags": ["ruset"],
    },
    "LiqLab": {
        "phrases": ["liqlab", "liq lab", "liq-lab"],
        "hashtags": ["liqlab", "liqlabb"],
    },
    "Skickat": {
        "phrases": ["skickat"],
        "hashtags": ["skickat"],
    },
}


def _listtext(v):
    """Platta ut ett JSON-listfält (t.ex. text_i_bild) till en sträng."""
    v = v or ""
    try:
        parsed = json.loads(v)
        if isinstance(parsed, list):
            return " ".join(str(x) for x in parsed)
    except Exception:
        pass
    return v


def match_campaign(row):
    """Returnera kampanjnamn om raden matchar, annars ''."""
    caption = (row.get("caption", "") or "").lower()
    bildtext = _listtext(row.get("text_i_bild", "")).lower()
    haystack = caption + " " + bildtext
    # Hashtags: både den egna kolumnen och ev. #taggar i captionen.
    tags = set((row.get("hashtags", "") or "").lower().split())
    tags |= set(re.findall(r"#(\w+)", caption))
    for name, cfg in CAMPAIGNS.items():
        if any(h.lower() in tags for h in cfg.get("hashtags", [])):
            return name
        # Fraser matchas som hela ord (ordgräns) så delsträngar inte träffar
        # (t.ex. "ruset" i "bruset").
        for p in cfg.get("phrases", []):
            if re.search(r"\b" + re.escape(p.lower()) + r"\b", haystack):
                return name
    return ""


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
        name = match_campaign(r)
        r["kampanj"] = name
        r["produktionsniva"] = "kampanj" if name else "always_on"
        if name:
            hits[name] += 1
            examples.setdefault(name, []).append(
                (r.get("video_id", ""), (r.get("caption", "") or "")[:70]))

    total_kampanj = sum(hits.values())
    print(f"{len(rows)} inlägg. Kampanjträffar: {total_kampanj} "
          f"({len(rows)-total_kampanj} always_on)")
    for name in CAMPAIGNS:
        print(f"\n  {name}: {hits.get(name,0)} träffar")
        for vid, cap in examples.get(name, [])[:5]:
            print(f"     {vid}  {cap}")
    if not total_kampanj:
        print("\nInga träffar. Justera CAMPAIGNS (t.ex. fler hashtag-varianter) "
              "eller kontrollera hur kampanjerna taggas i captionen.")

    if dry:
        print("\n(--dry: skrev inget. Kör utan --dry för att spara kolumnerna.)")
        return

    fields = list(rows[0].keys())
    for c in ("kampanj", "produktionsniva"):
        if c not in fields:
            fields.append(c)
    tmp = CSV_PATH + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, CSV_PATH)
    print(f"\nSkrev kolumnerna 'kampanj' och 'produktionsniva' till "
          f"{os.path.basename(CSV_PATH)}.")


if __name__ == "__main__":
    main()
