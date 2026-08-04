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
       ├── iq_tiktok_data/thumbnails/<id>.jpg     (för dashboard)
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
terminalen. Datan hamnar i `iq_tiktok_data/` i projektmappen.

Inställningar (överst i skriptet):

| Inställning | Betydelse |
|---|---|
| `DOWNLOAD_VIDEOS` | `False` = bara siffror (snabbt). `True` = ladda även ner videofilerna som Ström B behöver. |
| `MAX_VIDEOS` | Begränsa antal (nyast först). `0` = alla. Bra för att testa. |
| `SKIP_SCRAPED` | `True` = återuppta: hoppa över videor som redan har metadata (men ladda ändå ner ev. saknade videofiler). `False` = hämta om och **uppdatera siffrorna** (visningar/likes ändras över tid). |

**Återupptagbart & idempotent.** Progress sparas efter *varje* video, så ett
avbrott (Ctrl+C, nätverksfel) förlorar inget – kör bara igen. En redan
nedladdad videofil laddas **aldrig** ner på nytt.

**Stor körning (~600 videor) – två sätt, båda fungerar:**
- *Allt i ett:* sätt `DOWNLOAD_VIDEOS = True` och `MAX_VIDEOS = 0`, kör en gång. Enklast.
- *Två steg:* kör först med `DOWNLOAD_VIDEOS = False` (alla siffror, går fortare
  att få en komplett CSV att ögna på), sätt sedan `DOWNLOAD_VIDEOS = True` och
  kör igen – då hämtas bara videofilerna, metadatan skrapas inte om.

**Uppdatera siffror senare:** sätt `SKIP_SCRAPED = False` och kör igen. Siffrorna
uppdateras, men videofiler som redan finns laddas inte ner på nytt.

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
| Identifiering | `video_id`, `url`, `publiceringsdatum`, `thumbnail` (`thumbnails/<id>.jpg`) |
| Resultat (ström A) | `visningar`, `likes`, `kommentarer`, `delningar`, `sparade`, `engagement_rate`, `is_ad` (True = boostad) |
| Caption | `caption`, `caption_langd`, `antal_hashtags`, `hashtags`, `musik`, `musik_original` (ja/nej) |
| Innehåll (ström B) | `langd_verifierad`, `upplosning`, `bildformat`, `transkript`, `hook_text`, `hook_typ`, `format`, `kategori`, `tema`, `text_i_bild`, `grafik_beskrivning`, `personer_i_bild`, `medverkande`, `cta`, `har_cta` |
| Detektionsflaggor | `alkohol_i_bild`, `alkohol_marke`, `alkohol_omnamns_verbalt`, `alkohol_kontext` |
| Nyckeltal (uträknade) | `save_rate`, `share_rate`, `likes_per_view` |

Kategoriska fält (`hook_typ`, `format`, `kategori`, `medverkande`, `har_cta`,
alkoholflaggorna) har fasta värden och är gjorda för att gruppera/aggregera på i
analysen. Flervärdesfält (`text_i_bild`, `cta`, `alkohol_marke`) sparas som
**JSON-listor** så de går att bryta ut exakt med `json.loads(cell)`, oberoende
av tecken i texten. (`hashtags` från Ström A är mellanslagsseparerad – redan
säkert splittbar.) Fri text (`tema`, `grafik_beskrivning`, `personer_i_bild`,
`transkript`) finns kvar som komplement.

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
