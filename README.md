# IQ – automatiserad analys av TikTok-innehåll vs resultat

Kopplar innehåll till resultat för [@iqinitiativet](https://www.tiktok.com/@iqinitiativet)
på TikTok: en rad per video med både innehållsvariabler (vad som visas/sägs,
format, hook, alkoholflaggor) och resultatsiffror (visningar, engagemang).
Körs manuellt då och då – inte som driftsatt tjänst.

## Arkitektur

Två dataströmmar som joinas på `video_id`:

```
  TikTok-profil
       │
       ▼
 [ Ström A: iq_tiktok_scraper.py ]        ← resultatdata + videofiler
   • scrolla profil → video-URL:er
   • läs metrics + caption ur inbäddad JSON
   • ladda ner videofil (yt-dlp)
       │
       ├── iq_tiktok_data/iq_tiktok_metrics.csv   (siffror + caption)
       ├── iq_tiktok_data/videos/<id>.mp4         (för ström B)
       └── iq_tiktok_data/iq_tiktok_raw.jsonl     (rå fallback)
       │
       ▼
 [ Ström B: iq_tiktok_content_pipeline.py ]   ← innehållsanalys
   • ffprobe  → längd, upplösning, bildformat
   • ffmpeg   → nyckelbilder (scendetektering)
   • Whisper  → transkript (vad sägs) + hook_text
   • Claude   → vad visas, text i bild (OCR), format, alkoholflaggor
       │
       ▼
   iq_tiktok_data/iq_tiktok_enriched.csv   ← berikad CSV för analys
```

**Räckvidd (reach) ingår medvetet inte** – det kräver TikTok Studio. Vi utgår
från visningar (`playCount`). Reach kan joinas in på `video_id` senare via en
manuell CSV-export.

## Installation

```bash
pip install -r requirements.txt
playwright install chromium
# ffmpeg + ffprobe måste finnas i PATH (t.ex. brew install ffmpeg)
```

Vision-steget använder Claude – sätt en nyckel:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."   # eller: ant auth login
```

## Körning

**1. Ström A – scraper**

```bash
python iq_tiktok_scraper.py
```

Första gången öppnas ett Chrome-fönster: logga in på TikTok och tryck ENTER i
terminalen. Standard är `DOWNLOAD_VIDEOS = False` (bara siffror – snabbt att
verifiera). Sätt `True` när du vill ladda ner videofilerna som Ström B behöver.
`MAX_VIDEOS` begränsar antalet (nyast först); sätt `0` för alla. Körningen är
återupptagbar, och datan hamnar i `iq_tiktok_data/` i projektmappen.

**2. Ström B – innehållspipeline**

```bash
python iq_tiktok_content_pipeline.py
# valfritt: billigare modell för stora körningar
IQ_VISION_MODEL=claude-sonnet-5 python iq_tiktok_content_pipeline.py
```

Läser `iq_tiktok_metrics.csv` + `videos/<id>.mp4` och skriver
`iq_tiktok_enriched.csv`. Verifiera vision-utdatan på ett par videor och
stickprovsvalidera alkoholflaggorna mot råmaterialet innan analys.

## Kalkylark-schema (berikad CSV)

| Grupp | Kolumner |
|---|---|
| Identifiering | `video_id`, `url`, `publiceringsdatum` |
| Resultat (ström A) | `visningar`, `likes`, `kommentarer`, `delningar`, `sparade`, `engagement_rate` |
| Caption | `caption`, `caption_langd`, `antal_hashtags`, `hashtags`, `musik` |
| Innehåll (ström B) | `langd_verifierad`, `upplosning`, `bildformat`, `transkript`, `hook_text`, `format`, `tema`, `text_i_bild`, `grafik_beskrivning`, `personer_i_bild`, `cta` |
| Detektionsflaggor | `alkohol_i_bild`, `alkohol_marke`, `alkohol_omnamns_verbalt`, `alkohol_kontext` |

Detektionstaxonomin (alkoholflaggorna) är lätt att utöka: lägg till fält i
`VISION_SCHEMA` / `VISION_KEYS` i `iq_tiktok_content_pipeline.py`.

## Förbehåll

- Videor äldre än 365 dagar slutar uppdatera statistik hos TikTok (frysta siffror).
- Sparade (`collectCount`) är ibland publikt, ibland inte – behandla som bonus.
- Scraping bryter mot TikToks villkor. Kör i egen inloggad profil, mänsklig takt.
- TikTok ändrar DOM/JSON-nycklar då och då – rådata sparas därför alltid till JSONL.
- Vision-modellens taggning bör stickprovsvalideras. Med ~150 rader ser man
  riktningsgivande mönster, inte statistiska bevis (korrelation ≠ kausalitet,
  litet urval, störfaktorer).
