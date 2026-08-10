// Lösenordsskydd för hela sajten (Basic Auth med ett delat lösenord).
// Sätt miljövariabeln SITE_PASSWORD i Vercel (och ev. SITE_USER, default "iq").
// Utan SITE_PASSWORD är sajten öppen – så glöm inte att sätta den.
export const config = { matcher: '/((?!favicon.ico).*)' };

export default function middleware(request) {
  const user = process.env.SITE_USER || 'iq';
  const pass = process.env.SITE_PASSWORD;
  if (!pass) return; // inget lösenord satt → släpp igenom
  const auth = request.headers.get('authorization') || '';
  const expected = 'Basic ' + btoa(user + ':' + pass);
  if (auth !== expected) {
    return new Response('Autentisering krävs.', {
      status: 401,
      headers: { 'WWW-Authenticate': 'Basic realm="IQ Analytics", charset="UTF-8"' },
    });
  }
}
