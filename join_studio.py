#!/usr/bin/env python3
"""
Joina in EXAKTA siffror från en TikTok Business Manager / Studio-export i
metrics- och enriched-CSV:erna, matchat på video_id.

De publika (skrapade) siffrorna är avrundade; exporten har exakta tal. Detta
skript LÄGGER TILL exakta kolumner bredvid de skrapade (skriver inte över):

    visningar_exakt, likes_exakt, kommentarer_exakt, delningar_exakt, sparade_exakt

Innehåller exporten dessutom en reach-kolumn fylls den befintliga 'rackvidd'.
Videor som saknas i exporten (t.ex. utanför dess datumintervall) lämnas tomma.

Klarar komma/semikolon/tab, UTF-8(-BOM) och ev. titelrad före rubrikerna, och
plockar video_id ur en video-URL om exporten saknar en ren id-kolumn.

Användning:
    python join_studio.py                    # iq_tiktok_data/studio_export.csv
    python join_studio.py min_export.csv
"""

import csv
import os
import re
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJECT_DIR, "iq_tiktok_data")
METRICS = os.path.join(DATA, "iq_tiktok_metrics.csv")
ENRICHED = os.path.join(DATA, "iq_tiktok_enriched.csv")
DEFAULT_EXPORT = os.path.join(DATA, "studio_export.csv")

ID_HINTS = ["video_id", "videoid", "video id", "post id", "post_id", "id"]
URL_HINTS = ["url", "länk", "lank", "link", "video url", "video-länk", "video link"]

# (ledtrådar för källkolumnen i exporten, målkolumn i vår CSV)
FIELD_MAP = [
    (["videovisningar", "visningar", "video views", "views"], "visningar_exakt"),
    (["gilla-markeringar", "gillamarkeringar", "gilla", "likes"], "likes_exakt"),
    (["kommentarer", "comments"], "kommentarer_exakt"),
    (["delningar", "shares"], "delningar_exakt"),
    (["lägg till i favoriter", "favoriter", "favorites", "saves"], "sparade_exakt"),
    (["rackvidd", "räckvidd", "reach", "nådda konton", "nådda tittare",
      "unika tittare", "unique viewers", "accounts reached"], "rackvidd"),
]


def sniff_read(path):
    """Läs CSV/TSV oavsett avgränsare (tab/semikolon/komma) och UTF-8(-BOM).
    Hoppar över ev. titel-/metadatarader före den riktiga rubrikraden."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        head = [ln for ln in (next(f, "") for _ in range(20)) if ln.strip()]
    if not head:
        return []
    best = max(["\t", ";", ","], key=lambda d: max(ln.count(d) for ln in head))
    width = max(ln.count(best) for ln in head)
    if width == 0:
        best = ","
    with open(path, newline="", encoding="utf-8-sig") as f:
        alllines = list(f)
    start = 0
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
        cl = c.lower()
        if any(len(h) >= 4 and h in cl for h in hints):
            return c
    return None


def vid_from(value):
    """Plocka ut det numeriska video-id:t ur ett värde (rent id eller URL)."""
    m = re.search(r"(\d{6,})", str(value))
    return m.group(1) if m else ""


def clean_number(value):
    """Ta bort tusentalsavgränsare (mellanslag / hårt mellanslag)."""
    return (value or "").strip().replace(" ", "").replace(" ", "")


def fill_file(path, values_by_id, targets):
    """Lägg in exakta värden i en CSV; lägg till målkolumner som saknas."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    name = os.path.basename(path)
    if not rows:
        print(f"  {name}: tom – hoppar över.")
        return
    fields = list(rows[0].keys())
    for t in targets:
        if t not in fields:
            fields.append(t)
    filled = 0
    for row in rows:
        vals = values_by_id.get(row.get("video_id", ""))
        if vals:
            for t in targets:
                if vals.get(t, "") != "":
                    row[t] = vals[t]
            filled += 1
        for t in targets:                 # se till att kolumnen alltid finns
            row.setdefault(t, "")
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    print(f"  {name}: fyllde exakta siffror på {filled} av {len(rows)} videor.")


def main():
    export_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXPORT
    if not os.path.exists(export_path):
        sys.exit(f"Hittar inte exportfilen: {export_path}")

    export_rows = sniff_read(export_path)
    if not export_rows:
        sys.exit("Exportfilen är tom.")
    cols = list(export_rows[0].keys())

    id_col = find_col(cols, ID_HINTS) or find_col(cols, URL_HINTS)
    if not id_col:
        print("Kunde inte hitta någon video-id/URL-kolumn.")
        print("Kolumner i exporten:", cols)
        sys.exit("Säg vilken kolumn som är video-id/URL, så justerar vi.")

    # Vilka målkolumner kan vi fylla från den här exporten?
    mapping = {}                          # källkolumn -> målkolumn
    for hints, target in FIELD_MAP:
        src = find_col(cols, hints)
        if src:
            mapping[src] = target
    if not mapping:
        print("Hittade inga kända siffror att joina in.")
        print("Kolumner i exporten:", cols)
        sys.exit("Säg vilka kolumner som ska in, så justerar vi FIELD_MAP.")

    print(f"Matchar på '{id_col}'. Joinar in:")
    for src, target in mapping.items():
        print(f"  '{src}'  ->  {target}")

    values_by_id = {}
    for r in export_rows:
        vid = vid_from(r.get(id_col, ""))
        if not vid:
            continue
        values_by_id[vid] = {t: clean_number(r.get(src, "")) for src, t in mapping.items()}

    targets = sorted(set(mapping.values()))
    files = [p for p in (METRICS, ENRICHED) if os.path.exists(p)]
    if not files:
        sys.exit("Hittar varken metrics- eller enriched-CSV:n att fylla i.")
    for path in files:
        fill_file(path, values_by_id, targets)

    seen = set()
    for path in files:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                seen.add(r.get("video_id", ""))
    unmatched = [v for v in values_by_id if v not in seen]
    if unmatched:
        print(f"OBS: {len(unmatched)} rader i exporten matchade ingen video i CSV:erna.")


if __name__ == "__main__":
    main()
