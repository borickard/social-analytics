#!/usr/bin/env python3
"""Mät auto-taggarens träffsäkerhet mot dina manuella granskningar.

Eftersom ALLA inlägg är granskade är facit = det auto-värde du lät stå + dina
rättelser (overrides.csv) ovanpå. Då kan vi mäta per fält hur ofta auto-taggaren
höll med dig – och, efter en prompt-ändring, köra om modellen på ett urval och se
om träffsäkerheten gått upp.

Lägen:
  python eval_tagger.py
      BASLINJE – jämför de SPARADE auto-taggarna (iq_tiktok_enriched.csv) mot
      facit. Körs var som helst, ingen modell, ingen kostnad.

  python eval_tagger.py --rerun-tone [--limit N]
      Kör om classify_tone (har_hook, budskapston) på ett urval och mäter mot
      facit. Text-only → billigt. Kräver ANTHROPIC_API_KEY.

  python eval_tagger.py --rerun-vision [--limit N]
      Kör om vision (format, kategori) på ett urval. Kräver videofiler lokalt +
      ANTHROPIC_API_KEY. Tyngre.

Skriver eval_disagreements.csv (rad per fält-miss) för felanalys. Med --rerun-*
skrivs eval_rerun_<läge>.csv med modellens nya gissningar vs facit.
"""

import argparse
import csv
import os
import random
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "iq_tiktok_data")
ENRICHED = os.path.join(DATA, "iq_tiktok_enriched.csv")
OVERRIDES = os.path.join(DATA, "overrides.csv")

# Redigerbara fält i dashboarden + vilken omkörning som återskapar dem.
EDITABLE = ["strategi", "typ", "kategori", "format", "budskapston", "hogtid", "har_hook"]
TONE_FIELDS = ["har_hook", "budskapston"]      # classify_tone.py (text)
VISION_FIELDS = ["format", "kategori", "har_hook"]   # iq_tiktok_content_pipeline.py (video)


def norm(v):
    s = str(v if v is not None else "").strip()
    return s.lower() if s.lower() in ("ja", "nej") else s


def load_rows():
    rows = {}
    with open(ENRICHED, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            vid = (r.get("video_id") or "").strip()
            if vid:
                rows[vid] = r
    return rows


def load_overrides():
    ov = {}
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                vid = (r.get("video_id") or "").strip()
                fld = (r.get("field") or "").strip()
                if vid and fld:
                    ov.setdefault(vid, {})[fld] = r.get("value", "")
    return ov


def truth_of(vid, field, rows, ov):
    """Facit: din rättelse om den finns, annars auto-värdet (du lät det stå)."""
    if vid in ov and field in ov[vid]:
        return ov[vid][field]
    return rows.get(vid, {}).get(field, "")


def score(pairs):
    """pairs: lista av (pred, truth). Returnerar (n, korrekta, confusion-Counter)."""
    n = ok = 0
    conf = Counter()
    for pred, t in pairs:
        p, tt = norm(pred), norm(t)
        if tt == "" and p == "":
            continue
        n += 1
        if p == tt:
            ok += 1
        else:
            conf[(tt or "(tom)", p or "(tom)")] += 1   # facit -> modellens gissning
    return n, ok, conf


# Varifrån fältet kommer – så 0% på separat-taggade fält inte förvirrar.
SOURCE = {"format": "vision", "kategori": "vision", "tema": "vision",
          "har_hook": "tone", "budskapston": "tone",
          "strategi": "separat (kampanjskript)", "hogtid": "separat (tag_occasions)",
          "typ": "scraper"}


def report(title, per_field):
    print(f"\n=== {title} ===")
    print(f"{'fält':14} {'träff':>8}  {'n':>5}  {'källa':22} vanligaste miss (facit → gissning)")
    for fld, (n, ok, conf) in per_field.items():
        acc = f"{100*ok/n:.1f}%" if n else "—"
        top = ", ".join(f"{t}→{p} ×{c}" for (t, p), c in conf.most_common(3))
        print(f"{fld:14} {acc:>8}  {n:>5}  {SOURCE.get(fld,''):22} {top}")


def baseline(rows, ov):
    per = {}
    dis = []
    for fld in EDITABLE:
        pairs = []
        for vid, r in rows.items():
            pred = r.get(fld, "")
            t = truth_of(vid, fld, rows, ov)
            pairs.append((pred, t))
            if norm(pred) != norm(t):
                dis.append({"video_id": vid, "field": fld, "auto": pred, "facit": t})
        per[fld] = score(pairs)
    report("BASLINJE – sparade auto-taggar vs facit", per)
    out = os.path.join(HERE, "eval_disagreements.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["video_id", "field", "auto", "facit"])
        w.writeheader()
        w.writerows(dis)
    print(f"\n{len(dis)} missar skrivna till {os.path.basename(out)}.")


def pick_sample(rows, need_fields, limit, seed=0):
    vids = [v for v, r in rows.items()
            if any((r.get(f, "") or "").strip() for f in need_fields)]
    vids.sort()
    if limit and limit < len(vids):
        random.Random(seed).shuffle(vids)
        vids = sorted(vids[:limit])
    return vids


def rerun_tone(rows, ov, limit):
    try:
        import classify_tone
    except Exception as e:
        sys.exit(f"Kan inte importera classify_tone: {e}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Sätt ANTHROPIC_API_KEY.")
    vids = pick_sample(rows, ["caption", "transkript"], limit)
    print(f"Kör om tone-klassificeraren på {len(vids)} inlägg …")
    per = {f: [] for f in TONE_FIELDS}
    out_rows = []
    for i, vid in enumerate(vids, 1):
        r = rows[vid]
        try:
            pred = classify_tone.classify(r)
        except Exception as e:
            print(f"  ! {vid}: {e}")
            continue
        row_out = {"video_id": vid}
        for f in TONE_FIELDS:
            t = truth_of(vid, f, rows, ov)
            per[f].append((pred.get(f, ""), t))
            row_out[f"{f}_gissning"] = pred.get(f, "")
            row_out[f"{f}_facit"] = t
        out_rows.append(row_out)
        if i % 20 == 0:
            print(f"  … {i}/{len(vids)}")
    report("OMKÖRNING tone (har_hook, budskapston) vs facit", {f: score(p) for f, p in per.items()})
    _write_rerun("tone", out_rows)


def rerun_vision(rows, ov, limit):
    try:
        import iq_tiktok_content_pipeline as P
    except Exception as e:
        sys.exit(f"Kan inte importera iq_tiktok_content_pipeline: {e}")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Sätt ANTHROPIC_API_KEY.")
    import tempfile
    vids = pick_sample(rows, ["format", "kategori"], limit)
    print(f"Kör om vision på {len(vids)} inlägg (kräver videofiler lokalt) …")
    per = {f: [] for f in VISION_FIELDS}
    out_rows = []
    for i, vid in enumerate(vids, 1):
        r = rows[vid]
        is_photo = (r.get("typ", "") or "").lower() == "bild"
        try:
            with tempfile.TemporaryDirectory() as td:
                if is_photo:
                    imgs = P.find_images(vid)
                    if not imgs:
                        continue
                    frames = imgs[:P.MAX_FRAMES] if hasattr(P, "MAX_FRAMES") else imgs
                else:
                    mp4 = P.find_video(vid)
                    if not mp4:
                        continue
                    frames = P.extract_keyframes(mp4, td)
                pred = P.call_vision(frames, r.get("transkript", ""),
                                     r.get("caption", ""), is_photo)
        except Exception as e:
            print(f"  ! {vid}: {e}")
            continue
        row_out = {"video_id": vid}
        for f in VISION_FIELDS:
            t = truth_of(vid, f, rows, ov)
            per[f].append((pred.get(f, ""), t))
            row_out[f"{f}_gissning"] = pred.get(f, "")
            row_out[f"{f}_facit"] = t
        out_rows.append(row_out)
        print(f"  [{i}/{len(vids)}] {vid}")
    report("OMKÖRNING vision (format, kategori) vs facit", {f: score(p) for f, p in per.items()})
    _write_rerun("vision", out_rows)


def _write_rerun(tag, out_rows):
    if not out_rows:
        print("Inget att skriva (inga lyckade omkörningar).")
        return
    out = os.path.join(HERE, f"eval_rerun_{tag}.csv")
    cols = list(out_rows[0].keys())
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)
    print(f"Skrev {len(out_rows)} rader till {os.path.basename(out)}.")


def main():
    ap = argparse.ArgumentParser(description="Mät auto-taggarens träffsäkerhet mot facit.")
    ap.add_argument("--rerun-tone", action="store_true", help="kör om har_hook/budskapston")
    ap.add_argument("--rerun-vision", action="store_true", help="kör om format/kategori")
    ap.add_argument("--limit", type=int, default=0, help="urvalsstorlek vid omkörning (0=alla)")
    args = ap.parse_args()

    if not os.path.exists(ENRICHED):
        sys.exit(f"Hittar inte {ENRICHED}")
    rows, ov = load_rows(), load_overrides()
    print(f"{len(rows)} inlägg i enriched, {len(ov)} med minst en rättelse.")

    if args.rerun_tone:
        rerun_tone(rows, ov, args.limit)
    elif args.rerun_vision:
        rerun_vision(rows, ov, args.limit)
    else:
        baseline(rows, ov)


if __name__ == "__main__":
    main()
