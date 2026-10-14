// Serverless-funktion: den DELADE databasen för manuella taggrättelser.
// Lagras i Vercels KV (Upstash Redis) som en HASH – ett fält per
// (video_id, fält), så två personer som sparar samtidigt aldrig skriver över
// varandra. Miljövariablerna injiceras automatiskt när du kopplar en KV-store
// till projektet (Storage → Connect) och gör en redeploy.
//
//   GET   → hela databasen som {video_id:{fält:värde}}
//   POST  {deltas:[{id,field,value}]} → skriver bara de ändrade fälten
//           (value===null tar bort fältet). En plain {id:{fält:värde}} tolkas
//           också som deltas och VÄVS IN (skriver aldrig över hela blobben).
const HKEY = 'iq_overrides_h';     // Redis-HASH, fält = "<video_id>::<fält>"
const LEGACY = 'iq_overrides';     // äldre helblob (sträng) – läses in en gång
const SEP = '::';

function findEnv(suffix) {
  for (const k of Object.keys(process.env)) {
    if (k.endsWith(suffix)) return process.env[k];
  }
  return undefined;
}
const REST_URL = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL
  || findEnv('_REST_API_URL');
const REST_TOKEN = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN
  || findEnv('_REST_API_TOKEN');

module.exports = async (req, res) => {
  if (!REST_URL || !REST_TOKEN) {
    res.status(500).json({ error: 'KV-store saknas – koppla en i Vercel (Storage) och gör en redeploy.' });
    return;
  }
  const headers = { Authorization: 'Bearer ' + REST_TOKEN, 'Content-Type': 'application/json' };
  const cmd = async (args) => {
    const r = await fetch(REST_URL, { method: 'POST', headers, body: JSON.stringify(args) });
    return r.json();
  };
  const pipeline = async (cmds) => {
    const r = await fetch(REST_URL + '/pipeline', { method: 'POST', headers, body: JSON.stringify(cmds) });
    return r.json();
  };

  try {
    if (req.method === 'GET') {
      const nested = {};
      // Äldre helblob (om den finns) läggs som grund så inget historiskt tappas.
      try {
        const l = await cmd(['GET', LEGACY]);
        if (l && l.result) {
          const o = JSON.parse(l.result);
          for (const id in o) if (o[id] && typeof o[id] === 'object') nested[id] = Object.assign({}, o[id]);
        }
      } catch (e) { /* ignorera trasig/legacy-data */ }
      // Hashen (den aktuella delade databasen) läggs överst.
      const h = await cmd(['HGETALL', HKEY]);
      const arr = (h && h.result) || [];
      for (let i = 0; i + 1 < arr.length; i += 2) {
        const k = String(arr[i]); const ix = k.indexOf(SEP);
        if (ix < 0) continue;
        const id = k.slice(0, ix), f = k.slice(ix + SEP.length);
        (nested[id] = nested[id] || {})[f] = arr[i + 1];
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

      const cmds = [];
      for (const d of deltas) {
        if (!d || !d.id || !d.field) continue;
        const field = String(d.id) + SEP + String(d.field);
        if (d.value === null || d.value === undefined) cmds.push(['HDEL', HKEY, field]);
        else cmds.push(['HSET', HKEY, field, String(d.value)]);
      }
      if (cmds.length) await pipeline(cmds);
      res.status(200).json({ ok: true, written: cmds.length });
      return;
    }

    res.status(405).end();
  } catch (e) {
    res.status(500).json({ error: String(e) });
  }
};
