#!/usr/bin/env python3
"""
Ström B – innehållspipeline. Läser videos/<id>.mp4 + iq_tiktok_metrics.csv,
producerar iq_tiktok_enriched.csv. Kör efter iq_tiktok_scraper.py.

Beroenden:
    pip install faster-whisper anthropic
    # ffmpeg + ffprobe måste finnas i PATH (t.ex. brew install ffmpeg)

VISION: call_vision() nedan är kopplad mot Claude (Messages API, structured
outputs). Claude har stark svensk OCR/vision, vilket krävs för text i bild.
Sätt din nyckel i miljövariabeln ANTHROPIC_API_KEY (eller kör `ant auth login`).
Modell kan bytas via IQ_VISION_MODEL (default claude-opus-5). För kostnads-
känsliga körningar över ~150 videor är claude-sonnet-5 ett billigare val.
"""

import base64
import csv
import glob
import json
import os
import subprocess
import sys
import tempfile

# Samma datamapp som scrapern skriver till: en undermapp i projektet.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJECT_DIR, "iq_tiktok_data")
VIDEO_DIR = os.path.join(DATA, "videos")
IN_CSV = os.path.join(DATA, "iq_tiktok_metrics.csv")
OUT_CSV = os.path.join(DATA, "iq_tiktok_enriched.csv")

# Vision-modell. claude-opus-5 = bäst kvalitet; claude-sonnet-5 = billigare.
VISION_MODEL = os.environ.get("IQ_VISION_MODEL", "claude-opus-5")

# Whisper-modell för transkribering. large-v3 = bäst men långsam på CPU (Mac).
# Sätt t.ex. IQ_WHISPER_MODEL=medium eller small för snabbare körning.
WHISPER_MODEL = os.environ.get("IQ_WHISPER_MODEL", "large-v3")

# Testläge: analysera bara de N första (0 = alla). T.ex. IQ_LIMIT=3 för ett test.
LIMIT = int(os.environ.get("IQ_LIMIT", "0") or 0)

# Verbala alkohol-nyckelord (korsas mot transkriptet i main()).
ALKOHOL_ORD = ["alkohol", "öl", "vin", "sprit", "drink", "bärs", "cider",
               "champagne", "bubbel", "shot", "systembolag", "fylla", "berusad"]

# Fälten Ström B lägger till (utöver Ström A-fälten):
CONTENT_FIELDS = [
    "langd_verifierad", "upplosning", "bildformat",
    "transkript", "hook_text", "format", "tema",
    "text_i_bild", "grafik_beskrivning", "personer_i_bild", "cta",
    "alkohol_i_bild", "alkohol_marke", "alkohol_omnamns_verbalt", "alkohol_kontext",
]

# Fälten vision-modellen ska returnera per video (allt utom
# alkohol_omnamns_verbalt, som härleds ur transkriptet i main()).
VISION_KEYS = [
    "format", "tema", "text_i_bild", "grafik_beskrivning",
    "personer_i_bild", "cta", "alkohol_i_bild", "alkohol_marke",
    "alkohol_kontext",
]

# JSON Schema som styr modellens utdata (structured outputs).
VISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "format": {
            "type": "string",
            "enum": ["talking_head", "voiceover_broll", "skarminspelning",
                     "animerat", "ovrigt"],
            "description": "Övergripande videoformat.",
        },
        "tema": {"type": "string", "description": "Kort ämnesetikett på svenska."},
        "text_i_bild": {
            "type": "string",
            "description": "All synlig text/overlays i klippet (OCR), svensk text ordagrant.",
        },
        "grafik_beskrivning": {
            "type": "string",
            "description": "Grafiska element, stil och klippning.",
        },
        "personer_i_bild": {
            "type": "string",
            "description": "Antal och typ av personer som syns.",
        },
        "cta": {"type": "string", "description": "Ev. uppmaning (call to action), annars tom sträng."},
        "alkohol_i_bild": {
            "type": "string",
            "enum": ["ja", "nej"],
            "description": "Syns dryck/flaska/glas/burk som är alkohol?",
        },
        "alkohol_marke": {
            "type": "string",
            "description": "Identifierbara alkoholvarumärken, annars tom sträng.",
        },
        "alkohol_kontext": {
            "type": "string",
            "enum": ["fest", "maltid", "negativt", "neutralt", "forebyggande", "ingen"],
            "description": "I vilket sammanhang förekommer alkohol (eller 'ingen').",
        },
    },
    "required": VISION_KEYS,
}

VISION_PROMPT = (
    "Du analyserar en TikTok-video från IQ (iqinitiativet), en svensk "
    "organisation som arbetar för en smartare attityd till alkohol.\n\n"
    "Du får ett antal nyckelbilder ur videon (i ordning) samt ett transkript "
    "av det som sägs. Beskriva INNEHÅLLET och fyll i den begärda strukturen.\n\n"
    "Instruktioner:\n"
    "- Läs av (OCR) all text och grafik som syns i bild – återge svensk text "
    "ordagrant.\n"
    "- Bedöm videoformat, tema, personer i bild och ev. uppmaning (CTA).\n"
    "- Detektionstaxonomi (alkohol): avgör om alkohol SYNS i bild "
    "(dryck/flaska/glas/burk), identifiera ev. varumärken, och klassa "
    "sammanhanget. Räkna INTE alkoholfri dryck som alkohol.\n"
    "- Om något inte går att avgöra: använd tom sträng för fritextfält och "
    "'ingen'/'nej' för alkoholfälten.\n"
    "- Svara endast på svenska i fritextfälten.\n"
)


def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", path], capture_output=True, text=True).stdout
    streams = json.loads(out).get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "langd_verifierad": v.get("duration", ""),
        "upplosning": f"{v.get('width','')}x{v.get('height','')}",
        "bildformat": "staende" if int(v.get("height", 0) or 0) >= int(v.get("width", 0) or 0) else "liggande",
    }


def extract_keyframes(path, outdir):
    os.makedirs(outdir, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-i", path, "-vf", "select='gt(scene,0.3)'",
         "-vsync", "vfr", os.path.join(outdir, "f%03d.jpg")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    frames = sorted(glob.glob(os.path.join(outdir, "*.jpg")))
    if len(frames) < 3:  # fallback: en bild var 2:e sekund
        subprocess.run(
            ["ffmpeg", "-i", path, "-vf", "fps=0.5",
             "-vsync", "vfr", os.path.join(outdir, "g%03d.jpg")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        frames = sorted(glob.glob(os.path.join(outdir, "*.jpg")))
    return frames[:12]


_WHISPER = None


def get_whisper():
    """Ladda Whisper-modellen EN gång (inte per video) och återanvänd den."""
    global _WHISPER
    if _WHISPER is None:
        from faster_whisper import WhisperModel
        print(f"Laddar Whisper-modell '{WHISPER_MODEL}' "
              "(första gången laddas den ner – kan ta ett tag)...")
        _WHISPER = WhisperModel(WHISPER_MODEL, device="auto", compute_type="int8")
    return _WHISPER


def transcribe(path):
    model = get_whisper()
    segments, _ = model.transcribe(path, language="sv")
    segs = list(segments)
    full = " ".join(s.text.strip() for s in segs)
    hook = " ".join(s.text.strip() for s in segs if s.start < 3.0)
    return full, hook


def find_video(video_id):
    """Hitta videofilen oavsett filändelse (.mp4/.webm ...)."""
    hits = glob.glob(os.path.join(VIDEO_DIR, f"{video_id}.*"))
    return hits[0] if hits else None


def call_vision(frames, transcript):
    """
    Skicka nyckelbilderna + transkriptet till Claude och be om JSON enligt
    VISION_SCHEMA. Returnerar ett dict med nycklarna i VISION_KEYS.

    Robust: vid fel loggas det och tomma värden returneras så att pipelinen
    kan fortsätta (rådata finns kvar i CSV:n som ström A skrev).
    """
    import anthropic

    empty = {k: ("nej" if k == "alkohol_i_bild"
                 else "ingen" if k == "alkohol_kontext"
                 else "") for k in VISION_KEYS}
    if not frames:
        return empty

    # Bygg innehållsblocken: en text + bilderna + transkriptet.
    content = [{"type": "text", "text": VISION_PROMPT}]
    for fp in frames:
        with open(fp, "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    content.append({
        "type": "text",
        "text": f"Transkript av det som sägs:\n{transcript or '(inget tal)'}",
    })

    client = anthropic.Anthropic()
    try:
        response = client.messages.create(
            model=VISION_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": VISION_SCHEMA}},
        )
        # Med structured outputs ligger giltig JSON i första text-blocket.
        text = next((b.text for b in response.content if b.type == "text"), None)
        if not text:
            print("  ! vision: inget text-svar")
            return empty
        data = json.loads(text)
        # Se till att alla förväntade nycklar finns.
        return {k: data.get(k, empty[k]) for k in VISION_KEYS}
    except Exception as e:
        print(f"  ! vision misslyckades: {e}")
        return empty


def load_enriched():
    """Läs befintlig enriched-CSV till {video_id: rad} – för återupptagning."""
    rows = {}
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rows[r["video_id"]] = r
    return rows


def write_all(rows, out_fields):
    """Skriv hela enriched-CSV:n atomiskt – ett avbrott lämnar inte en trasig fil."""
    tmp = OUT_CSV + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader()
        for r in rows.values():
            w.writerow(r)
    os.replace(tmp, OUT_CSV)


def main():
    if not os.path.exists(IN_CSV):
        sys.exit(f"Hittar inte {IN_CSV} – kör iq_tiktok_scraper.py först.")

    rows_a = list(csv.DictReader(open(IN_CSV, newline="", encoding="utf-8-sig")))
    if not rows_a:
        sys.exit(f"{IN_CSV} är tom.")
    out_fields = list(rows_a[0].keys()) + CONTENT_FIELDS

    enriched = load_enriched()
    done = sum(1 for r in enriched.values() if r.get("langd_verifierad"))
    if done:
        print(f"Återupptar – {done} videor redan analyserade, hoppar över dem.")

    todo = rows_a[:LIMIT] if LIMIT else rows_a
    for i, row in enumerate(todo, 1):
        vid = row["video_id"]
        # Hoppa över redan analyserade (langd_verifierad satt = klar).
        if enriched.get(vid, {}).get("langd_verifierad"):
            print(f"[{i}/{len(todo)}] {vid} – redan analyserad, hoppar över")
            continue
        print(f"[{i}/{len(todo)}] {vid}")
        mp4 = find_video(vid)
        if not mp4:
            print("  ! ingen videofil – skriver bara ström A-raden")
            enriched[vid] = row
            write_all(enriched, out_fields)
            continue
        try:
            meta = ffprobe(mp4)
            transcript, hook = transcribe(mp4)
            with tempfile.TemporaryDirectory() as tmp:
                frames = extract_keyframes(mp4, tmp)
                vision = call_vision(frames, transcript)
            row.update(meta)
            row["transkript"] = transcript
            row["hook_text"] = hook
            row["alkohol_omnamns_verbalt"] = (
                "ja" if any(k in (transcript or "").lower() for k in ALKOHOL_ORD)
                else "nej")
            for k in VISION_KEYS:
                row[k] = vision.get(k, "")
            enriched[vid] = row
            write_all(enriched, out_fields)   # spara progress efter varje video
        except Exception as e:
            print(f"  ! fel på {vid}: {e}")
    print("Klart:", OUT_CSV)


if __name__ == "__main__":
    main()
