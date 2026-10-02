// Lösenordsskydd för hela sajten.
//
// Två sätt att autentisera:
//   1. Inloggningssida (formulär) för webbläsare – INGEN popup. När man loggat
//      in sätts en cookie. Kryssar man i "Håll mig inloggad" blir cookien
//      kvar i 30 dagar, annars bara tills webbläsaren stängs. Formuläret låter
//      också webbläsarens lösenordshanterare erbjuda att spara uppgifterna.
//   2. Basic Auth-header – så att skript (t.ex. tag_campaigns_kv.py) kan nå
//      /api/overrides programmatiskt precis som förut.
//
// Sätt miljövariabeln SITE_PASSWORD i Vercel (och ev. SITE_USER, default "iq").
// Utan SITE_PASSWORD är sajten öppen – så glöm inte att sätta den.
export const config = { matcher: '/((?!favicon.ico).*)' };

const COOKIE = 'iq_auth';
const REMEMBER_MAXAGE = 60 * 60 * 24 * 30; // 30 dagar

async function token(user, pass) {
  // Cookie-värdet är en hash av user:pass – kan inte räknas tillbaka till
  // lösenordet, och kan bara skapas av den som kan lösenordet.
  const data = new TextEncoder().encode('iqv1:' + user + ':' + pass);
  const buf = await crypto.subtle.digest('SHA-256', data);
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function loginPage(user, msg) {
  const err = msg
    ? `<p class="err">${msg}</p>`
    : '<p class="sub">Ange lösenordet för att se analysen.</p>';
  return `<!doctype html><html lang="sv"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IQ TikTok – logga in</title><style>
  :root{color-scheme:light}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:#efece6;color:#18140f;padding:24px;
    font:15px/1.5 "Helvetica Neue",Helvetica,Arial,-apple-system,system-ui,sans-serif;
    -webkit-font-smoothing:antialiased}
  .box{background:#f7f5f1;border:1px solid #e2ddd3;border-radius:20px;
    padding:30px 28px;width:100%;max-width:380px;box-shadow:0 24px 60px #00000012}
  h1{font-size:25px;letter-spacing:-.02em;font-weight:800;margin:0 0 4px}
  .sub,.err{font-size:13.5px;margin:0 0 20px}
  .sub{color:#8b857a}
  .err{color:#a2481f;font-weight:600}
  label{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.08em;
    color:#8b857a;font-weight:700;margin:0 0 5px}
  input[type=text],input[type=password]{width:100%;padding:11px 13px;margin-bottom:16px;
    border:1px solid #e2ddd3;border-radius:11px;background:#fbfaf7;font:inherit;
    font-size:15px;color:#18140f}
  input:focus{outline:none;border-color:#c0562f}
  .rem{display:flex;align-items:center;gap:8px;margin-bottom:20px;font-size:13.5px;
    color:#18140f;cursor:pointer;text-transform:none;letter-spacing:normal;font-weight:500}
  .rem input{width:17px;height:17px;accent-color:#c0562f;cursor:pointer}
  button{width:100%;padding:12px;border:none;border-radius:999px;background:#18140f;
    color:#fff;font:inherit;font-size:15px;font-weight:700;cursor:pointer}
  button:hover{background:#c0562f}
</style></head><body>
  <form class="box" method="post" action="/login">
    <h1>IQ × TikTok</h1>
    ${err}
    <label for="u">Användarnamn</label>
    <input id="u" type="text" name="username" value="${user}" autocomplete="username" autocapitalize="none" autocorrect="off">
    <label for="p">Lösenord</label>
    <input id="p" type="password" name="password" autocomplete="current-password" autofocus>
    <label class="rem"><input type="checkbox" name="remember" value="1" checked> Håll mig inloggad på den här enheten</label>
    <button type="submit">Logga in</button>
  </form>
</body></html>`;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

export default async function middleware(request) {
  const user = process.env.SITE_USER || 'iq';
  const pass = process.env.SITE_PASSWORD;
  if (!pass) return; // inget lösenord satt → öppen sajt

  const url = new URL(request.url);
  const expected = await token(user, pass);

  // 1) Basic Auth (skript) – släpp igenom direkt.
  const auth = request.headers.get('authorization') || '';
  if (auth === 'Basic ' + btoa(user + ':' + pass)) return;

  // Giltig inloggnings-cookie?
  const cookies = request.headers.get('cookie') || '';
  const m = cookies.match(/(?:^|;\s*)iq_auth=([a-f0-9]+)/);
  const authed = m && m[1] === expected;

  // Logga ut: nollställ cookien och visa inloggningssidan.
  if (url.pathname === '/logout') {
    return new Response(null, {
      status: 303,
      headers: {
        Location: '/',
        'Set-Cookie': `${COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax`,
      },
    });
  }

  // Hantera inloggningsformuläret.
  if (request.method === 'POST' && url.pathname === '/login') {
    const form = await request.formData();
    const u = (form.get('username') || '').toString();
    const p = (form.get('password') || '').toString();
    const remember = form.get('remember');
    if (u === user && p === pass) {
      const maxAge = remember ? `; Max-Age=${REMEMBER_MAXAGE}` : '';
      return new Response(null, {
        status: 303,
        headers: {
          Location: '/',
          'Set-Cookie': `${COOKIE}=${expected}; Path=/; HttpOnly; Secure; SameSite=Lax${maxAge}`,
        },
      });
    }
    return new Response(loginPage(esc(u) || user, 'Fel användarnamn eller lösenord.'), {
      status: 401,
      headers: { 'content-type': 'text/html; charset=utf-8' },
    });
  }

  if (authed) return; // insläppt

  // Alla andra obehöriga förfrågningar → visa inloggningssidan (ingen popup).
  return new Response(loginPage(user, ''), {
    status: 200,
    headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' },
  });
}
