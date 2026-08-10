// Serverless-funktion: läser/skriver manuella taggrättelser till en
// Vercel KV (Redis)-store via dess REST-API. Miljövariablerna injiceras
// automatiskt när du kopplar en KV/Upstash-store till projektet i Vercel.
const KEY = 'iq_overrides';
const REST_URL = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
const REST_TOKEN = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;

module.exports = async (req, res) => {
  if (!REST_URL || !REST_TOKEN) {
    res.status(500).json({ error: 'KV-store saknas – koppla en i Vercel (Storage).' });
    return;
  }
  const headers = { Authorization: 'Bearer ' + REST_TOKEN };
  try {
    if (req.method === 'GET') {
      const r = await fetch(REST_URL + '/get/' + KEY, { headers });
      const j = await r.json();
      let data = {};
      if (j && j.result) { try { data = JSON.parse(j.result) || {}; } catch (e) { data = {}; } }
      res.status(200).json(data);
      return;
    }
    if (req.method === 'POST') {
      let body = req.body;
      if (typeof body === 'string') { try { body = JSON.parse(body); } catch (e) { body = {}; } }
      if (!body || typeof body !== 'object') body = {};
      await fetch(REST_URL + '/set/' + KEY, {
        method: 'POST', headers, body: JSON.stringify(body),
      });
      res.status(200).json({ ok: true });
      return;
    }
    res.status(405).end();
  } catch (e) {
    res.status(500).json({ error: String(e) });
  }
};
