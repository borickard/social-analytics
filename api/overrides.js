// Serverless-funktion: den DELADE databasen för manuella taggrättelser.
// Lagras i en Redis-store (Vercel → Storage) som en HASH – ett fält per
// (video_id, fält), så två personer som sparar samtidigt aldrig skriver över
// varandra. Fungerar med vilken Redis som helst (Redis Cloud, Upstash m.fl.)
// via en vanlig redis://- eller rediss://-URL i en miljövariabel.
//
//   GET   → hela databasen som {video_id:{fält:värde}}
//   POST  {deltas:[{id,field,value}]} → skriver bara de ändrade fälten
//           (value===null tar bort fältet). En plain {id:{fält:värde}} tolkas
//           också som deltas och VÄVS IN (skriver aldrig över hela blobben).
const { createClient } = require('redis');

const HKEY = 'iq_overrides_h';     // Redis-HASH, fält = "<video_id>::<fält>"
const LEGACY = 'iq_overrides';     // äldre helblob (sträng) – läses in om den finns
const SEP = '::';

// Hitta anslutnings-URL:en oavsett vad miljövariabeln råkar heta.
function redisUrl() {
  const named = ['REDIS_URL', 'KV_REDIS_URL', 'KV_URL', 'REDIS_URI',
    'STORAGE_REDIS_URL', 'UPSTASH_REDIS_URL'];
  for (const n of named) {
    const v = process.env[n];
    if (v && /^rediss?:\/\//.test(v)) return v;
  }
  for (const k of Object.keys(process.env)) {
    const v = process.env[k];
    if (v && /^rediss?:\/\//.test(v)) return v;
  }
  return undefined;
}

// Återanvänd klienten mellan (varma) anrop i stället för att koppla upp varje gång.
let _clientPromise = null;
function getClient() {
  if (_clientPromise) return _clientPromise;
  const url = redisUrl();
  if (!url) return Promise.reject(new Error('no-redis-url'));
  // Failar snabbt om databasen inte går att nå (annars skulle anropet hänga tills
  // Vercels timeout). Några korta återförsök tål tillfälliga avbrott.
  const client = createClient({ url, socket: {
    connectTimeout: 4000,
    reconnectStrategy: (retries) => (retries > 3 ? false : Math.min(retries * 200, 1000)),
  } });
  client.on('error', () => {});   // svälj fel så en trasig anslutning inte kraschar processen
  client.on('end', () => { _clientPromise = null; });   // koppla upp på nytt vid nästa anrop
  _clientPromise = client.connect().then(() => client).catch((e) => {
    _clientPromise = null;        // tillåt nytt försök vid nästa anrop
    throw e;
  });
  return _clientPromise;
}

module.exports = async (req, res) => {
  let client;
  try {
    client = await getClient();
  } catch (e) {
    res.status(500).json({ error: 'KV-store saknas eller går inte att nå – koppla en Redis i Vercel (Storage), sätt dess URL som miljövariabel och gör en redeploy.' });
    return;
  }

  try {
    if (req.method === 'GET') {
      const nested = {};
      // Äldre helblob (om den finns) läggs som grund så inget historiskt tappas.
      try {
        const legacy = await client.get(LEGACY);
        if (legacy) {
          const o = JSON.parse(legacy);
          for (const id in o) if (o[id] && typeof o[id] === 'object') nested[id] = Object.assign({}, o[id]);
        }
      } catch (e) { /* ignorera trasig/legacy-data */ }
      // Hashen (den aktuella delade databasen) läggs överst.
      const h = await client.hGetAll(HKEY);   // {"<id>::<fält>": "<värde>"}
      for (const k in h) {
        const ix = k.indexOf(SEP);
        if (ix < 0) continue;
        const id = k.slice(0, ix), f = k.slice(ix + SEP.length);
        (nested[id] = nested[id] || {})[f] = h[k];
      }
      res.status(200).json(nested);
      return;
    }

    if (req.method === 'POST') {
      let body = req.body;
      if (typeof body === 'string') { try { body = JSON.parse(body); } catch (e) { body = {}; } }
      if (!body || typeof body !== 'object') body = {};

      // Normalisera till en lista deltas.
      let deltas = [];
      if (Array.isArray(body.deltas)) {
        deltas = body.deltas;
      } else {
        for (const id in body) {
          const fields = body[id];
          if (fields && typeof fields === 'object')
            for (const f in fields) deltas.push({ id, field: f, value: fields[f] });
        }
      }

      let written = 0;
      const m = client.multi();
      for (const d of deltas) {
        if (!d || !d.id || !d.field) continue;
        const field = String(d.id) + SEP + String(d.field);
        if (d.value === null || d.value === undefined) m.hDel(HKEY, field);
        else m.hSet(HKEY, field, String(d.value));
        written++;
      }
      if (written) await m.exec();
      res.status(200).json({ ok: true, written });
      return;
    }

    res.status(405).end();
  } catch (e) {
    res.status(500).json({ error: String(e) });
  }
};
