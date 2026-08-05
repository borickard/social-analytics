#!/usr/bin/env python3
"""
Ström B – innehållspipeline. Läser videos/<id>.mp4 (och foto-/karusellinläggens
bilder i images/<id>/) + iq_tiktok_metrics.csv, producerar
iq_tiktok_enriched.csv. Kör efter iq_tiktok_scraper.py.

Videor transkriberas (Whisper) och nyckelbilder klipps ut (ffmpeg) innan
vision-analysen. Foto-/karusellinlägg har inget ljud och ingen film: deras
nedladdade bilder skickas direkt till vision-modellen (ingen Whisper, ingen
ffmpeg-utklippning), och transkript/hook_text lämnas tomma.

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
import random
import subprocess
import sys
import tempfile
import time

# Samma datamapp som scrapern skriver till: en undermapp i projektet.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(PROJECT_DIR, "iq_tiktok_data")
VIDEO_DIR = os.path.join(DATA, "videos")
# Foto-/karusellinlägg saknar videofil; deras bilder ligger i images/<id>/ och
# används som "frames" direkt (ingen ffmpeg-utklippning, ingen transkribering).
IMAGE_DIR = os.path.join(DATA, "images")
IN_CSV = os.path.join(DATA, "iq_tiktok_metrics.csv")
OUT_CSV = os.path.join(DATA, "iq_tiktok_enriched.csv")

# Vision-modell. claude-opus-5 = bäst kvalitet; claude-sonnet-5 = billigare.
VISION_MODEL = os.environ.get("IQ_VISION_MODEL", "claude-opus-5")

# Whisper-modell för transkribering. large-v3 = bäst men långsam på CPU (Mac).
# Sätt t.ex. IQ_WHISPER_MODEL=medium eller small för snabbare körning.
WHISPER_MODEL = os.environ.get("IQ_WHISPER_MODEL", "large-v3")

# Testläge: analysera bara de N första (0 = alla). T.ex. IQ_LIMIT=3 för ett test.
LIMIT = int(os.environ.get("IQ_LIMIT", "0") or 0)

# Antal försök för vision-anropet vid övergående fel (överbelastning/timeout).
VISION_MAX_RETRIES = int(os.environ.get("IQ_VISION_RETRIES", "6") or 6)

# Säkring: avbryt hela körningen om så här många inlägg i rad misslyckas (tyder
# på en pågående API-outage). Slösar då inte Whisper-tid på resten – kör igen
# senare, redan klara hoppas över. 0 = stäng av säkringen.
MAX_CONSECUTIVE_FAILS = int(os.environ.get("IQ_MAX_CONSECUTIVE_FAILS", "5") or 5)

# Ungefärligt pris (USD per miljon tokens) för den löpande kostnadsräknaren.
# Uppskattning – Sonnet 5 har intropris (~$2/$10) t.o.m. 2026-08-31.
PRICING = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
}
USAGE = {"in": 0, "out": 0}   # ackumuleras i call_vision()


def est_cost():
    pin, pout = PRICING.get(VISION_MODEL, PRICING["claude-opus-5"])
    return USAGE["in"] / 1e6 * pin + USAGE["out"] / 1e6 * pout

# Verbala alkohol-nyckelord (korsas mot transkriptet i main()).
ALKOHOL_ORD = ["alkohol", "öl", "vin", "sprit", "drink", "bärs", "cider",
               "champagne", "bubbel", "shot", "systembolag", "fylla", "berusad"]

# Fälten Ström B lägger till (utöver Ström A-fälten):
CONTENT_FIELDS = [
    "langd_verifierad", "upplosning", "bildformat",
    "transkript", "hook_text", "hook_typ",
    "format", "kategori", "tema",
    "text_i_bild", "grafik_beskrivning", "personer_i_bild", "medverkande",
    "cta", "har_cta",
    "alkohol_i_bild", "alkohol_marke", "alkohol_omnamns_verbalt", "alkohol_kontext",
    "save_rate", "share_rate", "likes_per_view",
]
# OBS: rackvidd ligger i Ström A (metrics-CSV:n) och följer med hit automatiskt.

# Fälten vision-modellen ska returnera per video (härledda fält som
# alkohol_omnamns_verbalt, har_cta och *_rate sätts i main(), inte här).
VISION_KEYS = [
    "format", "kategori", "tema", "hook_typ",
    "text_i_bild", "grafik_beskrivning", "personer_i_bild", "medverkande",
    "cta", "alkohol_i_bild", "alkohol_marke", "alkohol_kontext",
]

# JSON Schema som styr modellens utdata (structured outputs).
VISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "format": {
            "type": "string",
            "enum": ["talking_head", "voiceover_broll", "skarminspelning",
                     "animerat", "bildinlagg", "ovrigt"],
            "description": "Övergripande format. Använd 'bildinlagg' för "
                           "foto-/karusellinlägg (stillbilder, ingen film).",
        },
        "kategori": {
            "type": "string",
            "enum": ["gatuintervju", "faktatips", "forstahjalpen", "myt_vs_fakta",
                     "personlig_berattelse", "quiz_lek", "ovrigt"],
            "description": "Innehållstyp/kategori. Välj 'ovrigt' om inget passar.",
        },
        "tema": {"type": "string", "description": "Kort ämnesetikett på svenska (fri text)."},
        "hook_typ": {
            "type": "string",
            "enum": ["fraga", "pastaende", "chock", "humor", "statistik", "ovrigt"],
            "description": "Typ av hook i de första sekunderna (utifrån tal + första bilden).",
        },
        "text_i_bild": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista med all synlig text/overlays i klippet (OCR) – "
                           "ett element per textblock, svensk text ordagrant.",
        },
        "grafik_beskrivning": {
            "type": "string",
            "description": "Grafiska element, stil och klippning.",
        },
        "personer_i_bild": {
            "type": "string",
            "description": "Antal och typ av personer som syns (fri text).",
        },
        "medverkande": {
            "type": "string",
            "enum": ["ingen", "en_person", "flera_personer"],
            "description": "Antal synliga medverkande personer (kategori).",
        },
        "cta": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista med uppmaningar (call to action) – talade, i bild "
                           "ELLER länk i caption. Tom lista om ingen finns.",
        },
        "alkohol_i_bild": {
            "type": "string",
            "enum": ["ja", "nej"],
            "description": "Syns dryck/flaska/glas/burk som är alkohol?",
        },
        "alkohol_marke": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Lista med identifierbara alkoholvarumärken, tom lista om inga.",
        },
        "alkohol_kontext": {
            "type": "string",
            "enum": ["fest", "maltid", "negativt", "neutralt", "forebyggande", "ingen"],
            "description": "I vilket sammanhang förekommer alkohol (eller 'ingen').",
        },
    },
    "required": VISION_KEYS,
}

# Intro-meningarna skiljer sig mellan video och foto/karusell; resten delas.
VISION_INTRO_VIDEO = (
    "Du analyserar en TikTok-video från IQ (iqinitiativet), en svensk "
    "organisation som arbetar för en smartare attityd till alkohol.\n\n"
    "Du får ett antal nyckelbilder ur videon (i ordning), ett transkript av "
    "det som sägs samt videons caption. Beskriv INNEHÅLLET och fyll i "
    "strukturen.\n\n"
)
VISION_INTRO_PHOTO = (
    "Du analyserar ett TikTok-BILDINLÄGG (ett foto eller en bildkarusell) från "
    "IQ (iqinitiativet), en svensk organisation som arbetar för en smartare "
    "attityd till alkohol.\n\n"
    "Du får inläggets samtliga bilder (i ordning) samt captionen. Det finns "
    "inget tal och inget ljud, alltså inget transkript. Behandla första bilden "
    "som hook och sätt format='bildinlagg'. Beskriv INNEHÅLLET och fyll i "
    "strukturen.\n\n"
)
VISION_BODY = (
    "Instruktioner:\n"
    "- Läs av (OCR) all text och grafik som syns i bild – återge svensk text "
    "ordagrant.\n"
    "- Bedöm format och innehållskategori. Klassa hook_typ utifrån början "
    "(tal + första bilden, eller enbart första bilden för bildinlägg).\n"
    "- medverkande: ingen / en_person / flera_personer.\n"
    "- CTA (lista): fånga varje uppmaning oavsett om den är talad, syns i bild "
    "eller är en länk i captionen (t.ex. en webbadress). Tom lista om ingen finns.\n"
    "- Detektionstaxonomi (alkohol): avgör om alkohol SYNS i bild "
    "(dryck/flaska/glas/burk), lista ev. varumärken, och klassa "
    "sammanhanget. Räkna INTE alkoholfri dryck som alkohol.\n"
    "- Om något inte går att avgöra: använd tom sträng för fritextfält, tom "
    "lista för listfält och 'ingen'/'nej' för alkoholfälten.\n"
    "- Svara endast på svenska i fritextfälten.\n"
)


def vision_prompt(is_photo):
    return (VISION_INTRO_PHOTO if is_photo else VISION_INTRO_VIDEO) + VISION_BODY


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
        print(f"Läser in Whisper-modell '{WHISPER_MODEL}' "
              "(laddas ner bara om den saknas i cachen; annars läses den bara "
              "in i minnet – kan ta en stund)...")
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


def find_images(video_id):
    """Hitta ett foto-/karusellinläggs bilder (images/<id>/NN.jpg), i ordning."""
    d = os.path.join(IMAGE_DIR, video_id)
    return sorted(glob.glob(os.path.join(d, "*.jpg"))
                  + glob.glob(os.path.join(d, "*.png"))
                  + glob.glob(os.path.join(d, "*.jpeg")))


def _is_transient(e):
    """Övergående fel som är värt att försöka igen (överbelastning/timeout/nät)."""
    code = getattr(e, "status_code", None)
    if code in (408, 409, 425, 429, 500, 502, 503, 529):
        return True
    s = str(e).lower()
    return any(m in s for m in (
        "overloaded", "rate limit", "rate_limit", "timeout", "timed out",
        "connection", "temporarily", "429", "500", "502", "503", "529"))


def call_vision(frames, transcript, caption="", is_photo=False):
    """
    Skicka nyckelbilderna + transkriptet + captionen till Claude och be om JSON
    enligt VISION_SCHEMA. Returnerar ett dict med nycklarna i VISION_KEYS.

    Vid övergående fel (t.ex. 529 Overloaded) görs flera försök med växande
    väntetid. Om det ändå misslyckas KASTAS felet vidare – då skrivs INTE raden
    som analyserad, utan tas om automatiskt vid nästa körning (i stället för att
    tyst sparas med tomma värden och hoppas över för alltid).
    """
    empty = {k: ("nej" if k == "alkohol_i_bild"
                 else "ingen" if k == "alkohol_kontext"
                 else "") for k in VISION_KEYS}
    if not frames:
        return empty

    import anthropic

    # Bygg innehållsblocken: en text + bilderna + transkript/caption.
    content = [{"type": "text", "text": vision_prompt(is_photo)}]
    for fp in frames:
        with open(fp, "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
    # Bildinlägg har inget tal – då utelämnar vi transkript-raden helt.
    tail = (f"Caption:\n{caption or '(ingen)'}" if is_photo else
            f"Transkript av det som sägs:\n{transcript or '(inget tal)'}\n\n"
            f"Caption:\n{caption or '(ingen)'}")
    content.append({"type": "text", "text": tail})

    client = anthropic.Anthropic()
    response = None
    for attempt in range(1, VISION_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=VISION_MODEL,
                max_tokens=2000,
                messages=[{"role": "user", "content": content}],
                output_config={"format": {"type": "json_schema", "schema": VISION_SCHEMA}},
            )
            break
        except Exception as e:
            # Övergående fel (t.ex. 529 Overloaded): vänta och försök igen.
            if attempt < VISION_MAX_RETRIES and _is_transient(e):
                wait = min(2 ** attempt, 30) + random.uniform(0, 1.5)
                print(f"  … vision överbelastad/instabil (försök {attempt}/"
                      f"{VISION_MAX_RETRIES}), väntar {wait:.0f}s: {e}")
                time.sleep(wait)
                continue
            # Slut på försök, eller icke-övergående fel: kasta vidare så raden
            # INTE sparas som analyserad (den tas om vid nästa körning).
            raise
    # Räkna tokens för den löpande kostnadsuppskattningen.
    USAGE["in"] += getattr(response.usage, "input_tokens", 0) or 0
    USAGE["out"] += getattr(response.usage, "output_tokens", 0) or 0
    # Med structured outputs ligger giltig JSON i första text-blocket.
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("vision: inget text-svar")
    data = json.loads(text)
    # Se till att alla nycklar finns. List-fält (t.ex. text_i_bild) sparas
    # som JSON-sträng i cellen så de går att bryta ut exakt senare med
    # json.loads – oberoende av vilka tecken texten själv innehåller.
    out = {}
    for k in VISION_KEYS:
        v = data.get(k, empty[k])
        out[k] = json.dumps(v, ensure_ascii=False) if isinstance(v, list) else v
    return out


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


def _rate(num, den):
    """Andel (num/den) avrundad; tom sträng om det inte går att räkna."""
    try:
        n, d = float(num), float(den)
        return round(n / d, 6) if d else ""
    except (TypeError, ValueError):
        return ""


def _has_content(val):
    """True om cellen har innehåll – hanterar både JSON-listor (t.ex. "[]") och text."""
    s = str(val or "").strip()
    if s.startswith("["):
        try:
            return len(json.loads(s)) > 0
        except Exception:
            return s not in ("", "[]")
    return bool(s)


def is_analyzed(row):
    """Har raden redan analyserats KLART i Ström B?

    Kräver att vision faktiskt gav ett resultat (fältet 'format' är satt). Rader
    där vision misslyckades – t.ex. under ett tillfälligt API-fel – saknar
    'format' och tas därför om vid nästa körning i stället för att hoppas över
    för alltid med tomma innehållsfält."""
    return bool(row.get("format"))


def add_derived(row):
    """Härledda nyckeltal ur Ström A-siffrorna + har_cta ur cta-fältet."""
    row["save_rate"] = _rate(row.get("sparade"), row.get("visningar"))
    row["share_rate"] = _rate(row.get("delningar"), row.get("visningar"))
    row["likes_per_view"] = _rate(row.get("likes"), row.get("visningar"))
    row["har_cta"] = "ja" if _has_content(row.get("cta")) else "nej"
    return row


def main():
    if not os.path.exists(IN_CSV):
        sys.exit(f"Hittar inte {IN_CSV} – kör iq_tiktok_scraper.py först.")

    rows_a = list(csv.DictReader(open(IN_CSV, newline="", encoding="utf-8-sig")))
    if not rows_a:
        sys.exit(f"{IN_CSV} är tom.")
    out_fields = list(rows_a[0].keys()) + CONTENT_FIELDS

    enriched = load_enriched()
    done = sum(1 for r in enriched.values() if is_analyzed(r))
    if done:
        print(f"Återupptar – {done} inlägg redan analyserade, hoppar över dem.")

    todo = rows_a[:LIMIT] if LIMIT else rows_a
    consecutive_fails = 0
    for i, row in enumerate(todo, 1):
        vid = row["video_id"]
        # Hoppa över redan analyserade.
        if is_analyzed(enriched.get(vid, {})):
            print(f"[{i}/{len(todo)}] {vid} – redan analyserad, hoppar över")
            continue
        print(f"[{i}/{len(todo)}] {vid}")
        mp4 = find_video(vid)
        images = find_images(vid)
        # Bevara ev. redan inklistrad reach (enriched först, annars metrics-värdet).
        prev_rackvidd = enriched.get(vid, {}).get("rackvidd", "") or row.get("rackvidd", "")
        if not mp4 and not images:
            print("  ! varken videofil eller bilder – skriver bara ström A-raden")
            arow = add_derived(dict(row))
            arow["rackvidd"] = prev_rackvidd
            enriched[vid] = arow
            write_all(enriched, out_fields)
            consecutive_fails = 0
            continue
        try:
            if mp4:
                meta = ffprobe(mp4)
                transcript, hook = transcribe(mp4)
                with tempfile.TemporaryDirectory() as tmp:
                    frames = extract_keyframes(mp4, tmp)
                    vision = call_vision(frames, transcript,
                                         row.get("caption", ""), is_photo=False)
            else:
                # Foto-/karusellinlägg: bilderna ÄR nyckelbilderna. Ingen film,
                # inget ljud → hoppa över ffmpeg-utklippning och Whisper.
                # ffprobe på första bilden ger ändå upplösning/bildformat.
                meta = ffprobe(images[0])
                meta["langd_verifierad"] = ""      # stillbilder har ingen speltid
                transcript, hook = "", ""
                frames = images[:12]
                vision = call_vision(frames, "",
                                     row.get("caption", ""), is_photo=True)
            row.update(meta)
            row["transkript"] = transcript
            row["hook_text"] = hook
            # Verbalt omnämnande kräver tal; bildinlägg (tomt transkript) → nej.
            row["alkohol_omnamns_verbalt"] = (
                "ja" if any(k in (transcript or "").lower() for k in ALKOHOL_ORD)
                else "nej")
            for k in VISION_KEYS:
                row[k] = vision.get(k, "")
            add_derived(row)
            row["rackvidd"] = prev_rackvidd   # bevara ev. manuellt inklistrad reach
            enriched[vid] = row
            write_all(enriched, out_fields)   # spara progress efter varje inlägg
            consecutive_fails = 0
            print(f"    hittills ~${est_cost():.2f}  "
                  f"({USAGE['in']:,}/{USAGE['out']:,} tokens in/ut)")
        except Exception as e:
            print(f"  ! fel på {vid}: {e}")
            consecutive_fails += 1
            if MAX_CONSECUTIVE_FAILS and consecutive_fails >= MAX_CONSECUTIVE_FAILS:
                print(f"\nAvbryter: {consecutive_fails} inlägg i rad misslyckades "
                      "– troligen ett pågående API-fel. Inget är förlorat (raderna "
                      "sparades inte som klara). Kör skriptet igen senare så tas de "
                      "kvarvarande inläggen om; redan klara hoppas över.")
                break
    print(f"\nKlart: {OUT_CSV}\nUppskattad vision-kostnad: ~${est_cost():.2f}")


if __name__ == "__main__":
    main()
