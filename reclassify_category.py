#!/usr/bin/env python3
"""
Omkategorisering av `kategori` med en förbättrad, IQ-godkänd taxonomi.

Den ursprungliga vision-taggningen använde 'forstahjalpen' som en slaskkategori.
Det här skriptet skriver om `kategori` i iq_tiktok_enriched.csv utifrån
omslagsbild + caption + transkript + text-i-bild (en billig pass – ingen Whisper,
ingen ffmpeg). Återupptagbart: rader som redan är omkategoriserade
(kategori_reclassad = "ja") hoppas över. Sätt IQ_RECLASS_FORCE=1 för att köra om
alla (t.ex. om du ändrar taxonomin).

Kör efter iq_tiktok_content_pipeline.py:
    python reclassify_category.py
"""

import base64
import csv
import json
import os
import random
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJECT_DIR, "iq_tiktok_data")
CSV_PATH = os.path.join(DATA, "iq_tiktok_enriched.csv")

MODEL = os.environ.get("IQ_CATEGORY_MODEL",
                       os.environ.get("IQ_VISION_MODEL", "claude-sonnet-5"))
MAX_RETRIES = int(os.environ.get("IQ_CATEGORY_RETRIES", "6") or 6)
LIMIT = int(os.environ.get("IQ_LIMIT", "0") or 0)
FORCE = os.environ.get("IQ_RECLASS_FORCE", "").strip() not in ("", "0", "false")
MAX_CONSECUTIVE_FAILS = 5

PRICING = {"claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (3.0, 15.0)}
USAGE = {"in": 0, "out": 0}


def est_cost():
    pin, pout = PRICING.get(MODEL, PRICING["claude-sonnet-5"])
    return USAGE["in"] / 1e6 * pin + USAGE["out"] / 1e6 * pout


# Läsbara svenska värden – blir samtidigt etiketter i rapporten.
CATEGORIES = [
    "frågor på stan",
    "faktatips",
    "myt vs fakta",
    "personlig berättelse",
    "quiz/lek",
    "memes relaterbart",
    "dramatiserat",
    "övrigt",
]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kategori": {
            "type": "string",
            "enum": CATEGORIES,
            "description": "Innehållstyp/format. Välj den som passar BÄST.",
        },
    },
    "required": ["kategori"],
}

PROMPT = (
    "Du kategoriserar ett TikTok-inlägg från IQ (iqinitiativet), en svensk "
    "organisation för en smartare attityd till alkohol. Välj EN innehållstyp "
    "(format), inte budskapet. Definitioner:\n"
    "- frågor på stan: intervjuer/frågor med människor ute på stan.\n"
    "- faktatips: fakta, tips eller råd (t.ex. 'tänk på detta', 'så här gör du').\n"
    "- myt vs fakta: ställer en myt mot fakta.\n"
    "- personlig berättelse: någon berättar en personlig historia/erfarenhet.\n"
    "- quiz/lek: quiz, lek eller uppmaning att svara/gissa.\n"
    "- memes relaterbart: memes, skämt, relaterbara vardagssituationer.\n"
    "- dramatiserat: skådespelat/scenariobaserat (t.ex. en dramatiserad scen).\n"
    "- övrigt: använd BARA om inget av ovanstående rimligen passar. "
    "Undvik övrigt i det längsta – välj hellre den närmaste kategorin.\n"
    "Utgå från omslagsbilden (om den finns), captionen, transkriptet och texten "
    "i bild. Svara enligt strukturen."
)


def _is_transient(e):
    code = getattr(e, "status_code", None)
    if code in (408, 409, 425, 429, 500, 502, 503, 529):
        return True
    s = str(e).lower()
    return any(m in s for m in (
        "overloaded", "rate limit", "rate_limit", "timeout", "timed out",
        "connection", "temporarily", "429", "500", "502", "503", "529"))


def _text_for(row):
    tib = row.get("text_i_bild", "") or ""
    try:
        p = json.loads(tib)
        if isinstance(p, list):
            tib = " | ".join(str(x) for x in p)
    except Exception:
        pass
    return (f"Caption:\n{row.get('caption','') or '(ingen)'}\n\n"
            f"Transkript:\n{row.get('transkript','') or '(inget tal)'}\n\n"
            f"Text i bild:\n{tib or '(ingen)'}")


def _thumb_block(row):
    """base64-bild av omslaget om det finns, annars None."""
    rel = row.get("thumbnail", "") or ""
    path = os.path.join(DATA, rel) if rel else ""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode("utf-8")
        return {"type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}}
    except Exception:
        return None


def classify(row):
    import anthropic
    client = anthropic.Anthropic()
    content = [{"type": "text", "text": PROMPT}]
    img = _thumb_block(row)
    if img:
        content.append(img)
    content.append({"type": "text", "text": _text_for(row)})
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=200,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            )
            text = next((b.text for b in resp.content if b.type == "text"), None)
            if not text:
                raise RuntimeError("tomt svar")
            kat = json.loads(text).get("kategori", "")
            USAGE["in"] += getattr(resp.usage, "input_tokens", 0) or 0
            USAGE["out"] += getattr(resp.usage, "output_tokens", 0) or 0
            return kat
        except Exception as e:
            retry = _is_transient(e) or isinstance(e, (json.JSONDecodeError, RuntimeError))
            if attempt < MAX_RETRIES and retry:
                wait = min(2 ** attempt, 30) + random.uniform(0, 1.5)
                print(f"  … instabil (försök {attempt}/{MAX_RETRIES}), väntar {wait:.0f}s: {e}")
                time.sleep(wait)
                continue
            raise


def main():
    if not os.path.exists(CSV_PATH):
        sys.exit(f"Hittar inte {CSV_PATH} – kör iq_tiktok_content_pipeline.py först.")
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("Enriched-CSV:n är tom.")
    fields = list(rows[0].keys())
    if "kategori_reclassad" not in fields:
        fields.append("kategori_reclassad")

    todo = [r for r in rows if r.get("format")
            and (FORCE or r.get("kategori_reclassad") != "ja")]
    if LIMIT:
        todo = todo[:LIMIT]
    done = sum(1 for r in rows if r.get("kategori_reclassad") == "ja")
    print(f"{len(rows)} rader, {done} redan omkategoriserade. "
          f"Kör {len(todo)} nu (modell: {MODEL}).")

    fails = 0
    for i, row in enumerate(todo, 1):
        vid = row.get("video_id", "")
        try:
            kat = classify(row)
            gammal = row.get("kategori", "")
            row["kategori"] = kat
            row["kategori_reclassad"] = "ja"
            fails = 0
            tmp = CSV_PATH + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
            os.replace(tmp, CSV_PATH)
            flag = "" if kat == gammal else f"  (var: {gammal})"
            print(f"[{i}/{len(todo)}] {vid}: {kat}{flag}  ~${est_cost():.2f}")
        except Exception as e:
            print(f"  ! fel på {vid}: {e}")
            fails += 1
            if fails >= MAX_CONSECUTIVE_FAILS:
                print("\nAvbryter: 5 fel i rad (troligen API-problem). Inget "
                      "förlorat – kör igen senare, klara rader hoppas över.")
                break
    print(f"\nKlart. Uppskattad kostnad: ~${est_cost():.2f}")


if __name__ == "__main__":
    main()
