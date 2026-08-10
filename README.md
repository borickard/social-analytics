# IQ – automatiserad analys av TikTok-innehåll vs resultat

Kopplar innehåll till resultat för [@iqinitiativet](https://www.tiktok.com/@iqinitiativet)
på TikTok: en rad per inlägg med både innehållsvariabler (vad som visas/sägs,
format, hook, alkoholflaggor) och resultatsiffror (visningar, engagemang).
Både vanliga videor och foto-/karusellinlägg (`/photo/`) tas med. Körs manuellt
då och då – inte som driftsatt tjänst.

## Arkitektur

Två dataströmmar som joinas på `video_id`:

```
  TikTok-profil
       │
       ▼
 [ Ström A: iq_tiktok_scraper.py ]        ← resultatdata + media
   • scrolla profil → URL:er (/video/ + /photo/)
   • läs metrics + caption ur inbäddad JSON
   • ladda ner videofil (yt-dlp) ELLER karusellens bilder
       │
       ├── iq_tiktok_data/iq_tiktok_metrics.csv   (siffror + caption)
       ├── iq_tiktok_data/videos/<id>.mp4         (video, för ström B)
       ├── iq_tiktok_data/images/<id>/NN.jpg      (foto/karusell, för ström B)
       ├── iq_tiktok_data/thumbnails/<id>.jpg     (för dashboard)
       └── iq_tiktok_data/iq_tiktok_raw.jsonl     (rå fallback)
       │
       ▼
 [ Ström B: iq_tiktok_content_pipeline.py ]   ← innehållsanalys
   • ffprobe  → längd, upplösning, bildformat
   • ffmpeg   → nyckelbilder (scendetektering)   [bara video]
   • Whisper  → transkript (vad sägs) + hook_text [bara video]
   • Claude   → vad visas, text i bild (OCR), format, alkoholflaggor
       │
       ▼
   iq_tiktok_data/iq_tiktok_enriched.csv   ← berikad CSV för analys
```

**Foto-/karusellinlägg** hanteras genom hela kedjan. De har ingen video och
inget ljud: Ström A laddar ner de enskilda bilderna till `images/<id>/`, och
Ström B skickar dem direkt till vision-modellen (ingen Whisper, ingen
ffmpeg-utklippning). `transkript`, `hook_text` och `langd_sek`/`langd_verifierad`
lämnas tomma; kolumnen `typ` skiljer `video` från `bild` och `antal_bilder`
anger bildantalet. Siffror och alkoholanalys fungerar likadant som för videor.

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
| `DOWNLOAD_VIDEOS` | `False` = bara siffror (snabbt). `True` = ladda även ner mediet som Ström B behöver (videofiler resp. karusellbilder). |
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
# standardmodell är claude-sonnet-5; för högsta kvalitet:
IQ_VISION_MODEL=claude-opus-5 python iq_tiktok_content_pipeline.py
# valfritt: bara foto-/karusellinlägg (kräver ingen Whisper – bra när
# API:et strular eller för att komplettera datasetet)
IQ_ONLY_PHOTOS=1 python iq_tiktok_content_pipeline.py
```

Läser `iq_tiktok_metrics.csv` + `videos/<id>.mp4` (och foto-/karusellinläggens
`images/<id>/`) och skriver `iq_tiktok_enriched.csv`. Verifiera vision-utdatan på
ett par inlägg och stickprovsvalidera alkoholflaggorna mot råmaterialet innan analys.

**Återupptagbart & feltåligt.** Progress sparas efter varje inlägg. Vid
tillfälliga API-fel (t.ex. `529 Overloaded`) OCH dåliga svar (trunkerat/tomt
JSON) görs flera försök med växande väntetid (`IQ_VISION_RETRIES`, default 6).
`max_tokens` för svaret är 4096 (`IQ_VISION_MAX_TOKENS`) så text-tunga karuseller
inte klipps av. Lyckas det ändå inte markeras raden **inte** som klar – kör bara
skriptet igen senare så tas de kvarvarande inläggen om (redan analyserade hoppas
över). En rad räknas som klar först när vision faktiskt gav ett resultat
(`format` är satt).

**Exakta siffror + reach från TikTok Business Manager / Studio.** De skrapade
publika siffrorna är avrundade; en Studio-/Business Manager-export har exakta
tal. Exportera den, spara som CSV (t.ex. `iq_tiktok_data/studio_export.csv`) och
joina in de exakta värdena på `video_id`:

```bash
python join_studio.py                    # använder iq_tiktok_data/studio_export.csv
python join_studio.py min_export.csv     # eller ange filen
```

Skriptet känner igen kolumnerna automatiskt (video-id/URL + siffror), klarar
komma/semikolon/tab och ev. titelrad, och **lägger till** exakta kolumner
bredvid de skrapade (skriver inte över):
`visningar_exakt`, `likes_exakt`, `kommentarer_exakt`, `delningar_exakt`,
`sparade_exakt`. Innehåller exporten även en reach-kolumn fylls `rackvidd`.
Fyller både metrics- och enriched-CSV:n; videor som saknas i exporten (t.ex.
före 2024-08-05 för reach) lämnas tomma. Kan köras när som helst.

## Kalkylark-schema (berikad CSV)

| Grupp | Kolumner |
|---|---|
| Identifiering | `video_id`, `url`, `typ` (`video`/`bild`), `antal_bilder` (foto/karusell), `publiceringsdatum`, `thumbnail` (`thumbnails/<id>.jpg`) |
| Resultat (ström A) | `visningar`, `likes`, `kommentarer`, `delningar`, `sparade`, `engagement_rate`, `rackvidd` (reach – fylls i efterhand), `is_ad` (True = boostad) |
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

## Analys (innehåll → engagemang)

När datasetet är berikat kopplar två skript innehåll till resultat.

**Engagemang** mäts som viktad engagemangsgrad, visad som procent:

```
ER = (likes + kommentarer×5 + delningar×10 + favoriter×5) / visningar
```

Riktmärken: **<0,5 % svagt · 0,5–2 % normalt · 2 %+ starkt**. Delningar väger
tyngst, allt i relation till visningar. Exakta siffror (`*_exakt`) används där de
finns. Median är huvudmått (robust mot virala extremvärden).

**1. Ton & budskap (valfritt men rekommenderat)** – en billig text-baserad
LLM-pass som lägger till `budskapston` (budskap vs lättsamt vs blandat),
`budskap_teman` och en ren `har_hook`-flagga:

```bash
python classify_tone.py        # bara text, inga bilder – snabbt/billigt
```

**Omkategorisering (vid behov):** om `kategori` ser fel ut (den ursprungliga
vision-taggningen kunde missa) skriver detta om `kategori` med en förbättrad
taxonomi utifrån omslagsbild + caption + transkript – billigt, ingen Whisper:

```bash
python reclassify_category.py          # återupptagbar; hoppar redan omgjorda
IQ_RECLASS_FORCE=1 python reclassify_category.py   # kör om alla (ändrad taxonomi)
```

Skriver om både `kategori` och `format`. Kategori: fakta · humor · POV ·
frågor på stan · quiz/lek · dramatiserat (endast regisserat TV-drama) · övrigt.
Format: filmat · animerat · skärmavbildning · voiceover · talking head ·
sketch · bildinlägg. En ändrad taxonomi triggar omkörning automatiskt. (Nya
inlägg via Ström B får samma taxonomi direkt.)

**Högtid/tillfälle (valfritt):** märk upp inlägg som rör en högtid (nyår, jul,
midsommar, valborg, studenten, halloween, sommarlov, födelsedag, påsk,
kräftskiva) via deterministisk nyckelordsmatchning:

```bash
python tag_occasions.py --dry   # visa träffar utan att skriva
python tag_occasions.py         # lägg till kolumnen hogtid
```

`analyze.py` visar då "Högtid / tillfälle" som en egen benchmark-dimension.

**2. Validera taggningen (rekommenderat före slutsatser):**

```bash
python validate_sample.py      # skriver iq_tiktok_data/iq_validering.html
```

Visar ett slumpurval inlägg med miniatyrbild + alla taggar + caption sida vid
sida, så du snabbt kan ögna om kategori/budskapston/hook/alkoholflaggor stämmer.
Styr med `IQ_VALIDATE_N`, `IQ_VALIDATE_SEED` och `IQ_VALIDATE_FILTER`
(`alkohol`/`bild`/`budskap`).

**3. Analys & rapport:**

```bash
python analyze.py              # skriver iq_tiktok_data/iq_analys.html + terminalsammanfattning
```

Rapporten är **interaktiv** (fristående HTML, inga beroenden):

- **Segment-väljare** Alla / Organiskt / Boostat som filtrerar alla tabeller
  (organiskt och boostat skiljer sig kraftigt i ER – jämför dem var för sig).
- **Sorterbara kolumner** – klicka en rubrik för att sortera på median-ER,
  medel-ER, visningar eller n.
- **Klickbara kategorier** – klicka en rad för att fälla ut inläggen i den
  gruppen (thumbnail + caption + ER + visningar); varje inlägg länkar till
  originalet på TikTok.
- Benchmarks per kategori, format/typ, hook, CTA, budskapston, budskap-tema och
  alkohol; engagemang **över tid** med trend (organiskt); samt **starkast/svagast
  inlägg separat för organiskt och boostat**.

Kör `classify_tone.py` först så får du även ton- och hook-benchmarks.

**Manuella rättelser i dashboarden.** Feltaggat innehåll kan rättas direkt i
rapporten: klicka **✎ ändra** på ett inläggskort, välj rätt kategori/format/
ton/högtid/hook och spara. Ändringarna slår igenom direkt i tabeller och graf.
Rättelserna ligger i `iq_tiktok_data/overrides.csv`, separat från
`iq_tiktok_enriched.csv`, och läggs alltid överst – så modellkörningar
(`reclassify_category.py` m.fl.) kan aldrig skriva över dem.

Två sätt att spara ändringarna:

```bash
# A) Automatiskt (rekommenderas): kör dashboarden via en lokal server
python analyze.py        # bygg rapporten
python serve.py          # öppna sedan http://localhost:8000
# varje ändring skrivs direkt till overrides.csv

# B) Utan server: öppna filen direkt, klicka "Ladda ner overrides.csv",
#    lägg den i iq_tiktok_data/ och kör analyze.py igen.
```

Kör `analyze.py` igen när du vill bygga om rapporten (t.ex. efter nytt
skrap/analys) – overrides tillämpas alltid.

## Förbehåll

- Videor äldre än 365 dagar slutar uppdatera statistik hos TikTok (frysta siffror).
- Sparade (`collectCount`) är ibland publikt, ibland inte – behandla som bonus.
- Scraping bryter mot TikToks villkor. Kör i egen inloggad profil, mänsklig takt.
- TikTok ändrar DOM/JSON-nycklar då och då – rådata sparas därför alltid till JSONL.
- Vision-modellens taggning bör stickprovsvalideras. Med ~150 rader ser man
  riktningsgivande mönster, inte statistiska bevis (korrelation ≠ kausalitet,
  litet urval, störfaktorer).
