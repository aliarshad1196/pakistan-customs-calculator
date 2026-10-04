export default {
  async fetch(request) {
    const target = request.headers.get('X-Target-URL');
    if (!target) return new Response('Missing X-Target-URL', {status: 400});

    let u;
    try { u = new URL(target); } catch { return new Response('Invalid target URL', {status: 400}); }
    if (!['www.weboc.gov.pk','weboc.gov.pk','www.psw.gov.pk','psw.gov.pk'].includes(u.hostname.toLowerCase())) {
      return new Response('Target host not allowed', {status: 403});
    }

    const h = new Headers(request.headers);
    h.delete('Host');
    h.delete('Content-Length');
    h.delete('X-Target-URL');
    // Let the target see a normal browser-like referer when absent.
    if (!h.has('Referer')) h.set('Referer', 'https://www.psw.gov.pk/');

    const init = {
      method: request.method,
      headers: h,
      redirect: 'follow'
    };
    if (request.method !== 'GET' && request.method !== 'HEAD') init.body = request.body;

    const upstream = await fetch(u.toString(), init);
    const out = new Headers(upstream.headers);
    // Keep Set-Cookie so requests.Session can retain the WebOC/PSW cookie.
    out.set('Access-Control-Allow-Origin', '*');
    return new Response(upstream.body, {status: upstream.status, headers: out});
  }
};
