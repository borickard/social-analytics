// Serverless-funktion: läser/skriver manuella taggrättelser till Supabase.
// Kräver miljövariablerna SUPABASE_URL och SUPABASE_SERVICE_ROLE_KEY i Vercel.
// Rättelserna sparas som en rad i tabellen app_state (key='iq_overrides').
//
// Skapa tabellen en gång i Supabase (SQL Editor):
//   create table if not exists app_state (
//     key text primary key,
//     value jsonb not null default '{}'::jsonb,
//     updated_at timestamptz default now()
//   );
const TABLE = 'app_state';
const OKEY = 'iq_overrides';
const SB_URL = process.env.SUPABASE_URL;
const SB_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_KEY;

module.exports = async (req, res) => {
  if (!SB_URL || !SB_KEY) {
    res.status(500).json({ error: 'Saknar SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY.' });
    return;
  }
  const base = SB_URL.replace(/\/$/, '') + '/rest/v1/' + TABLE;
  const headers = {
    apikey: SB_KEY,
    Authorization: 'Bearer ' + SB_KEY,
    'Content-Type': 'application/json',
  };
  try {
    if (req.method === 'GET') {
      const r = await fetch(base + '?key=eq.' + OKEY + '&select=value', { headers });
      const rows = await r.json();
      const data = Array.isArray(rows) && rows[0] && rows[0].value ? rows[0].value : {};
      res.status(200).json(data);
      return;
    }
    if (req.method === 'POST') {
      let body = req.body;
      if (typeof body === 'string') { try { body = JSON.parse(body); } catch (e) { body = {}; } }
      if (!body || typeof body !== 'object') body = {};
      const r = await fetch(base, {
        method: 'POST',
        headers: Object.assign({}, headers, { Prefer: 'resolution=merge-duplicates' }),
        body: JSON.stringify({ key: OKEY, value: body }),
      });
      if (!r.ok) {
        const t = await r.text();
        res.status(500).json({ error: 'Supabase: ' + t });
        return;
      }
      res.status(200).json({ ok: true });
      return;
    }
    res.status(405).end();
  } catch (e) {
    res.status(500).json({ error: String(e) });
  }
};
