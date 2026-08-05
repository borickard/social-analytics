#!/usr/bin/env python3
"""
Joina in reach (räckvidd) från en manuell TikTok Studio-export i den berikade
CSV:n (iq_tiktok_enriched.csv), matchat på video_id. Videor utan reach-data
(t.ex. äldre än 2024-08-05) lämnas tomma.

Användning:
    1. Exportera reach från TikTok Studio och spara som CSV, t.ex.
       iq_tiktok_data/reach.csv
    2. python join_reach.py                  # använder iq_tiktok_data/reach.csv
       python join_reach.py min_reach.csv    # eller ange filen

Skriptet försöker hitta rätt kolumner själv (video-id/URL + reach). Klarar
komma/semikolon och UTF-8. Hittar det inte kolumnerna listar det dem så vi kan
justera ID_HINTS / URL_HINTS / REACH_HINTS nedan.

Skriver tillbaka atomiskt och rör bara kolumnen 'rackvidd' – övrig data lämnas
orörd.
"""

import csv
import os
import re
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJECT_DIR, "iq_tiktok_data")
METRICS = os.path.join(DATA, "iq_tiktok_metrics.csv")
ENRICHED = os.path.join(DATA, "iq_tiktok_enriched.csv")
DEFAULT_REACH = os.path.join(DATA, "reach.csv")

# Möjliga kolumnrubriker (gemener) – utöka vid behov när vi sett din export.
ID_HINTS = ["video_id", "videoid", "video id", "post id", "post_id", "id"]
URL_HINTS = ["url", "länk", "lank", "link", "video url", "video-länk", "video link"]
REACH_HINTS = ["rackvidd", "räckvidd", "reach", "nådda konton", "nadda konton",
               "nådda tittare", "unika tittare", "unika visningar",
               "unique viewers", "accounts reached", "reached audience"]


def sniff_read(path):
    """Läs en CSV/TSV oavsett avgränsare (tab/komma/semikolon) och UTF-8(-BOM).
    Hoppar även över ev. titel-/metadatarader före den riktiga rubrikraden
    (TikTok Studio lägger t.ex. en 'Video(datum-datum)'-rad överst)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        head = [ln for ln in (next(f, "") for _ in range(20)) if ln.strip()]
    if not head:
        return []
    # Välj den avgränsare som ger flest kolumner.
    best = max(["\t", ";", ","], key=lambda d: max(ln.count(d) for ln in head))
    width = max(ln.count(best) for ln in head)
    if width == 0:                      # bara en kolumn – strunta i avgränsare
        best = ","
    # Rubrikraden = första raden med full bredd (hoppar över titelrader).
    start = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        alllines = list(f)
    for i, ln in enumerate(alllines):
        if ln.strip() and ln.count(best) >= width:
            start = i
            break
    return list(csv.DictReader(alllines[start:], delimiter=best))


def find_col(fieldnames, hints):
    low = {c.lower().strip(): c for c in fieldnames}
    for h in hints:                       # exakt match först
        if h in low:
            return low[h]
    for c in fieldnames:                  # sen delmatchning – bara specifika (>=4 tecken)
        cl = c.lower()                     # så korta "id"/"url" inte råkar träffa "videotitel"
        if any(len(h) >= 4 and h in cl for h in hints):
            return c
    return None


def vid_from(value):
    """Plocka ut det numeriska video-id:t ur ett värde (rent id eller URL)."""
    m = re.search(r"(\d{6,})", str(value))
    return m.group(1) if m else ""


def clean_number(value):
    """Ta bort tusentalsavgränsare (mellanslag/hårda mellanslag)."""
    return (value or "").strip().replace(" ", "").replace(" ", "")


def fill_file(path, reach_by_id):
    """Fyll rackvidd i en CSV (metrics eller enriched); lägg till kolumnen om den
    saknas. Returnerar mängden video_id som fanns i filen."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    name = os.path.basename(path)
    if not rows:
        print(f"  {name}: tom – hoppar över.")
        return set()
    fields = list(rows[0].keys())
    if "rackvidd" not in fields:
        fields.append("rackvidd")
    ids, filled = set(), 0
    for row in rows:
        vid = row.get("video_id", "")
        ids.add(vid)
        row.setdefault("rackvidd", "")
        if reach_by_id.get(vid, "") != "":
            row["rackvidd"] = reach_by_id[vid]
            filled += 1
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    print(f"  {name}: fyllde reach på {filled} av {len(rows)} videor.")
    return ids


def main():
    reach_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REACH
    if not os.path.exists(ENRICHED):
        sys.exit(f"Hittar inte {ENRICHED} – kör pipelinen först.")
    if not os.path.exists(reach_path):
        sys.exit(f"Hittar inte reach-filen: {reach_path}")

    reach_rows = sniff_read(reach_path)
    if not reach_rows:
        sys.exit("Reach-filen är tom.")
    cols = list(reach_rows[0].keys())

    id_col = find_col(cols, ID_HINTS) or find_col(cols, URL_HINTS)
    reach_col = find_col(cols, REACH_HINTS)
    if not id_col or not reach_col:
        print("Kunde inte hitta rätt kolumner automatiskt.")
        print("Kolumner i reach-filen:", cols)
        sys.exit("Säg vilka kolumner som är video-id/URL och reach, så justerar vi.")
    print(f"Matchar på '{id_col}' (video-id/URL) och läser reach ur '{reach_col}'.")

    reach_by_id = {}
    for r in reach_rows:
        vid = vid_from(r.get(id_col, ""))
        if vid:
            reach_by_id[vid] = clean_number(r.get(reach_col, ""))

    targets = [p for p in (METRICS, ENRICHED) if os.path.exists(p)]
    if not targets:
        sys.exit("Hittar varken metrics- eller enriched-CSV:n att fylla i.")

    seen_ids = set()
    for path in targets:
        seen_ids |= fill_file(path, reach_by_id)

    unmatched = [vid for vid in reach_by_id if vid not in seen_ids]
    if unmatched:
        print(f"OBS: {len(unmatched)} rader i reach-filen matchade ingen video "
              "i CSV:erna (kollades på video_id).")


if __name__ == "__main__":
    main()
