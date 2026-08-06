#!/usr/bin/env python3
"""
Text-baserad klassificering av ton och budskap – ett komplement till Ström B.

Lägger till tre kolumner i iq_tiktok_enriched.csv:
  budskapston  – "budskap" (tydligt förebyggande/informativt om alkohol),
                 "lattsamt" (meme/relaterbart/underhållande utan tungt budskap)
                 eller "blandat".
  budskap_teman – JSON-lista med vilka alkohol-budskap som förekommer.
  har_hook     – "ja"/"nej": finns en tydlig hook i början (ren binär flagga,
                 komplement till hook_typ som saknar ett "ingen"-värde).

Använder BARA text (caption + transkript + text i bild + tema/kategori) – inga
bilder – så det är snabbt och billigt. Återupptagbart: rader som redan har
budskapston hoppas över. Kör efter iq_tiktok_content_pipeline.py:

    python classify_tone.py
"""

import csv
import json
import os
import random
import sys
import time

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(PROJECT_DIR, "iq_tiktok_data", "iq_tiktok_enriched.csv")

MODEL = os.environ.get("IQ_TONE_MODEL",
                       os.environ.get("IQ_VISION_MODEL", "claude-sonnet-5"))
MAX_RETRIES = int(os.environ.get("IQ_TONE_RETRIES", "6") or 6)
LIMIT = int(os.environ.get("IQ_LIMIT", "0") or 0)

NEW_FIELDS = ["budskapston", "budskap_teman", "har_hook"]

PRICING = {"claude-opus-5": (5.0, 25.0), "claude-sonnet-5": (3.0, 15.0)}
USAGE = {"in": 0, "out": 0}


def est_cost():
    pin, pout = PRICING.get(MODEL, PRICING["claude-sonnet-5"])
    return USAGE["in"] / 1e6 * pin + USAGE["out"] / 1e6 * pout


BUDSKAP_TEMAN = [
    "effekter_av_alkohol", "minska_eller_avsta", "du_behover_inte_dricka",
    "grupptryck", "stotta_narstaende", "fakta_upplysning",
    "personlig_berattelse", "ovrigt",
]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "budskapston": {
            "type": "string",
            "enum": ["budskap", "lattsamt", "blandat"],
            "description": "budskap = tydligt förebyggande/informativt budskap "
                           "om alkohol; lattsamt = meme/relaterbart/underhållande "
                           "utan tungt budskap; blandat = tydligt inslag av båda.",
        },
        "budskap_teman": {
            "type": "array",
            "items": {"type": "string", "enum": BUDSKAP_TEMAN},
            "description": "Vilka alkohol-relaterade budskap förekommer. Tom lista "
                           "om inlägget är rent lättsamt utan budskap.",
        },
        "har_hook": {
            "type": "string",
            "enum": ["ja", "nej"],
            "description": "Finns en tydlig hook i de första sekunderna (fråga, "
                           "påstående, chock, statistik e.d.) som fångar tittaren?",
        },
    },
    "required": ["budskapston", "budskap_teman", "har_hook"],
}

PROMPT = (
    "Du klassificerar ett TikTok-inlägg från IQ (iqinitiativet), en svensk "
    "organisation för en smartare attityd till alkohol.\n\n"
    "Utifrån captionen, transkriptet och texten i bild – avgör:\n"
    "1) budskapston: 'budskap' om inlägget tydligt förmedlar ett förebyggande "
    "eller informativt alkoholbudskap (t.ex. effekter av alkohol, att man inte "
    "behöver dricka, grupptryck, stötta någon, fakta). 'lattsamt' om det är "
    "meme/relaterbart/underhållande utan tungt budskap. 'blandat' om båda "
    "tydligt finns.\n"
    "2) budskap_teman: vilka alkohol-budskap som förekommer (tom lista om inga).\n"
    "3) har_hook: om det finns en tydlig hook i början.\n"
    "Svara endast enligt strukturen."
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
    """Bygg textunderlaget (caption + transkript + text i bild + tema/kategori)."""
    tib = row.get("text_i_bild", "") or ""
    try:                       # text_i_bild sparas som JSON-lista
        parsed = json.loads(tib)
        if isinstance(parsed, list):
            tib = " | ".join(str(x) for x in parsed)
    except Exception:
        pass
    return (
        f"Caption:\n{row.get('caption','') or '(ingen)'}\n\n"
        f"Transkript:\n{row.get('transkript','') or '(inget tal)'}\n\n"
        f"Text i bild:\n{tib or '(ingen)'}\n\n"
        f"Tema: {row.get('tema','')} | Kategori: {row.get('kategori','')} | "
        f"hook_typ: {row.get('hook_typ','')}"
    )


def classify(row):
    """Klassificera en rad. Returnerar dict med NEW_FIELDS eller kastar vid fel."""
    import anthropic
    client = anthropic.Anthropic()
    content = [{"type": "text", "text": PROMPT},
               {"type": "text", "text": _text_for(row)}]
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=600,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            )
            text = next((b.text for b in resp.content if b.type == "text"), None)
            if not text:
                raise RuntimeError("tomt svar")
            data = json.loads(text)
            USAGE["in"] += getattr(resp.usage, "input_tokens", 0) or 0
            USAGE["out"] += getattr(resp.usage, "output_tokens", 0) or 0
            return {
                "budskapston": data.get("budskapston", ""),
                "budskap_teman": json.dumps(data.get("budskap_teman", []),
                                            ensure_ascii=False),
                "har_hook": data.get("har_hook", ""),
            }
        except Exception as e:
            retry = _is_transient(e) or isinstance(e, (json.JSONDecodeError, RuntimeError))
            if attempt < MAX_RETRIES and retry:
                wait = min(2 ** attempt, 30) + random.uniform(0, 1.5)
                print(f"  … instabil (försök {attempt}/{MAX_RETRIES}), "
                      f"väntar {wait:.0f}s: {e}")
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
    for c in NEW_FIELDS:
        if c not in fields:
            fields.append(c)

    # Bara analyserade rader (vision klar = 'format' satt) utan ton ännu.
    todo = [r for r in rows if r.get("format") and not r.get("budskapston")]
    if LIMIT:
        todo = todo[:LIMIT]
    done = sum(1 for r in rows if r.get("budskapston"))
    print(f"{len(rows)} rader totalt, {done} redan klassade. Klassar {len(todo)} nu.")

    fails = 0
    for i, row in enumerate(todo, 1):
        vid = row.get("video_id", "")
        print(f"[{i}/{len(todo)}] {vid}")
        try:
            row.update(classify(row))
            fails = 0
            # Skriv hela filen atomiskt efter varje rad (återupptagbart).
            tmp = CSV_PATH + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for r in rows:
                    w.writerow(r)
            os.replace(tmp, CSV_PATH)
            print(f"    {row['budskapston']} | hook={row['har_hook']} "
                  f"| ~${est_cost():.2f}")
        except Exception as e:
            print(f"  ! fel på {vid}: {e}")
            fails += 1
            if fails >= 5:
                print("\nAvbryter: 5 fel i rad (troligen API-problem). Inget "
                      "förlorat – kör igen senare, klara rader hoppas över.")
                break
    print(f"\nKlart. Uppskattad kostnad: ~${est_cost():.2f}")


if __name__ == "__main__":
    main()
