// Ladda upp de nedladdade videofilerna (iq_tiktok_data/videos/<id>.mp4) till
// Vercel Blob och skriv iq_tiktok_data/video_urls.csv (video_id,url). analyze.py
// bakar in den filen så dashboarden spelar videon från Blob i stället för att
// bädda in TikTok (ingen rate-limit, spelar direkt). Saknas en video i CSV:n
// faller dashboarden tillbaka på TikToks inbäddning.
//
// Körs på din dator (filerna ligger lokalt – de är gitignorade):
//   1) npm install @vercel/blob
//   2) Hämta token: Vercel → ditt projekt → Storage → (skapa/öppna en Blob-store,
//      access = public) → .env.local-fliken visar BLOB_READ_WRITE_TOKEN.
//        export BLOB_READ_WRITE_TOKEN=vercel_blob_rw_xxx
//      (eller: vercel env pull och läs ur .env.local)
//   3) node upload_videos_blob.mjs
//
// Idempotent/återupptagbar: redan uppladdade (finns i video_urls.csv) hoppas
// över, så avbryt och kör igen när du vill. Committa video_urls.csv + pusha
// efteråt så bakas URL:erna in i molnbygget.

import { put } from '@vercel/blob';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DATA_DIR = path.join(HERE, 'iq_tiktok_data');
const VIDEO_DIR = path.join(DATA_DIR, 'videos');
const MANIFEST = path.join(DATA_DIR, 'video_urls.csv');

const token = process.env.BLOB_READ_WRITE_TOKEN;
if (!token) {
  console.error('Sätt BLOB_READ_WRITE_TOKEN först (Vercel → Storage → din Blob-store → .env.local).');
  process.exit(1);
}
if (!fs.existsSync(VIDEO_DIR)) {
  console.error(`Hittar inte ${VIDEO_DIR} – kör scrapern först (den laddar ner videorna).`);
  process.exit(1);
}

// Läs ev. befintligt manifest (för att hoppa över redan uppladdade).
const done = new Map(); // id -> url
if (fs.existsSync(MANIFEST)) {
  const lines = fs.readFileSync(MANIFEST, 'utf8').split(/\r?\n/).slice(1);
  for (const line of lines) {
    const i = line.indexOf(',');
    if (i < 0) continue;
    const id = line.slice(0, i).trim();
    const url = line.slice(i + 1).trim();
    if (id && url) done.set(id, url);
  }
}

function writeManifest() {
  const rows = [...done.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1));
  const out = ['video_id,url', ...rows.map(([id, url]) => `${id},${url}`)].join('\n') + '\n';
  fs.writeFileSync(MANIFEST, out);
}

const EXT = { '.mp4': 'video/mp4', '.webm': 'video/webm', '.mov': 'video/quicktime' };
const files = fs.readdirSync(VIDEO_DIR).filter(f => EXT[path.extname(f).toLowerCase()]);
const todo = files.filter(f => !done.has(path.basename(f, path.extname(f))));

console.log(`${files.length} videofiler lokalt, ${done.size} redan uppladdade, ${todo.length} att ladda upp.`);
if (!todo.length) { writeManifest(); console.log('Inget nytt att göra.'); process.exit(0); }

let n = 0, failed = 0;
for (const f of todo) {
  const id = path.basename(f, path.extname(f));
  const ext = path.extname(f).toLowerCase();
  const full = path.join(VIDEO_DIR, f);
  try {
    const body = fs.readFileSync(full);
    const { url } = await put(`videos/${id}${ext}`, body, {
      access: 'public',
      addRandomSuffix: false,  // stabil, förutsägbar URL
      allowOverwrite: true,    // får köras om utan att faila
      contentType: EXT[ext],
      token,
    });
    done.set(id, url);
    n++;
    if (n % 10 === 0) writeManifest();   // spara löpande så avbrott inte tappar framsteg
    const mb = (body.length / 1048576).toFixed(1);
    console.log(`[${n}/${todo.length}] ${id} (${mb} MB) → ${url}`);
  } catch (e) {
    failed++;
    console.error(`  ! misslyckades för ${id}: ${e && e.message ? e.message : e}`);
  }
}
writeManifest();
console.log(`\nKlart. ${done.size} videor i video_urls.csv${failed ? ` (${failed} misslyckades – kör igen för att ta dem)` : ''}.`);
console.log('Nästa steg: git add iq_tiktok_data/video_urls.csv && commit && push → URL:erna bakas in i bygget.');
