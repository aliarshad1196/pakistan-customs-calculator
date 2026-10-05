export default {
  async fetch(request) {
    const u = new URL(request.url);
    if (u.pathname !== '/weboc') return new Response('Not found', { status: 404 });

    const hsRaw = (u.searchParams.get('hs') || '').trim();
    if (!hsRaw) return Response.json({ error: 'Missing ?hs= parameter' }, { status: 400 });
    const digits = hsRaw.replace(/[^0-9]/g, '');
    const hs = digits.length === 8 ? `${digits.slice(0, 4)}.${digits.slice(4)}` : hsRaw;

    const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:157.0) Gecko/20100101 Firefox/157.0';
    const PSW = 'https://www.psw.gov.pk/public/utilities?service=tariff_sros';
    const ROOT = 'https://www.weboc.gov.pk/';
    const jar = {};

    const cookieHeader = () => Object.entries(jar).map(([k, v]) => `${k}=${v}`).join('; ');
    const captureCookies = (resp) => {
      let lines = [];
      try {
        if (typeof resp.headers.getSetCookie === 'function') lines = resp.headers.getSetCookie() || [];
      } catch (_) {}
      if (!lines.length) {
        try {
          const raw = resp.headers.get('Set-Cookie');
          if (raw) lines = raw.split(/,(?=\s*[^;,=\s]+\s*=)/);
        } catch (_) {}
      }
      for (const line of lines) {
        const first = line.split(';', 1)[0].trim();
        const i = first.indexOf('=');
        if (i > 0) jar[first.slice(0, i)] = first.slice(i + 1);
      }
    };

    const req = async (url, options = {}) => {
      const headers = new Headers(options.headers || {});
      headers.set('User-Agent', UA);
      if (!headers.has('Accept')) headers.set('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8');
      if (Object.keys(jar).length) headers.set('Cookie', cookieHeader());
      const resp = await fetch(url, { ...options, headers, redirect: 'manual' });
      captureCookies(resp);
      return resp;
    };

    const absolute = (base, location) => new URL(location, base).toString();
    const textOf = async (resp) => await resp.text();

    const hidden = (html, name) => {
      const esc = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const re = new RegExp(`<input[^>]+name=["']${esc}["'][^>]*value=["']([^"']*)["']`, 'i');
      const m = html.match(re);
      return m ? m[1] : '';
    };

    const sessionUrlFromHtml = (html) => {
      const m = html.match(/(?:href|action)=["']([^"']*TariffList\.aspx[^"']*)["']/i);
      if (m) return absolute(ROOT, m[1]);
      const s = html.match(/\/(?:\(S\([^)]*\)\/)?Shared\/TariffList\.aspx/i);
      return s ? absolute(ROOT, s[0]) : null;
    };

    const plainText = (html) => html
      .replace(/<script[\s\S]*?<\/script>/gi, ' ')
      .replace(/<style[\s\S]*?<\/style>/gi, ' ')
      .replace(/<[^>]+>/g, ' ')
      .replace(/&nbsp;/gi, ' ')
      .replace(/&amp;/gi, '&')
      .replace(/\s+/g, ' ')
      .trim();

    const rowRate = (row, category) => {
      const t = row.replace(/\s+/g, ' ').trim();
      const patterns = category === 'commercial'
        ? [/commercial\s+importer[\s\S]{0,500}?@\s*([0-9]+(?:\.[0-9]+)?)\s*%/i, /commercial\s+importer[\s\S]{0,500}?([0-9]+(?:\.[0-9]+)?)\s*%/i]
        : [/industrial\s+undertaking[\s\S]{0,500}?@\s*([0-9]+(?:\.[0-9]+)?)\s*%/i, /industrial\s+(?:undertaking|concern)[\s\S]{0,500}?([0-9]+(?:\.[0-9]+)?)\s*%/i];
      for (const p of patterns) {
        const m = t.match(p);
        if (m) return Number(m[1]);
      }
      return null;
    };

    const parseRates = (html) => {
      let commercial = null, industrial = null;
      let commercial_detail = null, industrial_detail = null;
      const rowRe = /<tr\b[\s\S]*?<\/tr>/gi;
      const rows = html.match(rowRe) || [];
      for (const rowHtml of rows) {
        const row = plainText(rowHtml);
        const low = row.toLowerCase();
        if (!low.includes('income tax')) continue;
        if (low.includes('commercial importer') || (low.includes('commercial') && low.includes('import'))) {
          const r = rowRate(row, 'commercial');
          if (r !== null) { commercial = r; commercial_detail = row; }
        }
        if (low.includes('industrial undertaking') || low.includes('industrial concern')) {
          const r = rowRate(row, 'industrial');
          if (r !== null) { industrial = r; industrial_detail = row; }
        }
      }

      // Fallback for markup where the SRO text is not inside <tr> elements.
      const text = plainText(html);
      if (commercial === null) {
        const i = text.toLowerCase().indexOf('commercial importer');
        if (i >= 0) commercial = rowRate(text.slice(Math.max(0, i - 100), i + 900), 'commercial');
      }
      if (industrial === null) {
        const i = text.toLowerCase().indexOf('industrial undertaking');
        if (i >= 0) industrial = rowRate(text.slice(Math.max(0, i - 100), i + 900), 'industrial');
      }
      return { commercial, industrial, commercial_detail, industrial_detail };
    };

    try {
      // 1. Open PSW and discover the current cookieless WeBOC session URL.
      const psw = await req(PSW, { headers: { Referer: 'https://www.google.com/' } });
      const pswHtml = await textOf(psw);
      let url = sessionUrlFromHtml(pswHtml);
      if (!url) {
        return Response.json({ error: 'Could not discover WeBOC TariffList.aspx session URL', cookie_names: Object.keys(jar) }, { status: 502 });
      }

      // 2. WeBOC can rotate /(S(...))/ on POST. Each redirect is therefore
      // treated as a new live form: GET the new session URL, read fresh
      // ViewState/EventValidation, then POST the search again.
      for (let attempt = 0; attempt < 8; attempt++) {
        const page = await req(url, { headers: { Referer: PSW } });
        const html = await textOf(page);
        const formAction = sessionUrlFromHtml(html) || url;
        const vs = hidden(html, '__VIEWSTATE');
        const vg = hidden(html, '__VIEWSTATEGENERATOR');
        const ev = hidden(html, '__EVENTVALIDATION');
        const ve = hidden(html, '__VIEWSTATEENCRYPTED');

        if (!vs || !ev) {
          return Response.json({ error: 'WeBOC form fields could not be parsed', weboc_url: url, preview: html.slice(0, 1200), cookie_names: Object.keys(jar) }, { status: 502 });
        }

        const body = new URLSearchParams();
        body.set('__VIEWSTATE', vs);
        if (vg) body.set('__VIEWSTATEGENERATOR', vg);
        if (ve) body.set('__VIEWSTATEENCRYPTED', ve);
        body.set('__EVENTVALIDATION', ev);
        body.set('TariffList$txtSearch', hs);
        body.set('TariffList$btnSearch', 'Search');

        const post = await req(formAction, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            Referer: url,
            Origin: 'https://www.weboc.gov.pk',
          },
          body: body.toString(),
        });
        const postHtml = await textOf(post);

        if (post.status >= 300 && post.status < 400) {
          const loc = post.headers.get('Location');
          if (!loc) break;
          url = absolute(formAction, loc);
          continue;
        }

        const rates = parseRates(postHtml);
        if (rates.commercial !== null || rates.industrial !== null) {
          return Response.json({ ok: true, weboc_url: url, ...rates, cookie_names: Object.keys(jar) });
        }

        // Some successful responses immediately contain a fresh session URL
        // but still show the form. Retry that URL with a fresh ViewState.
        const next = sessionUrlFromHtml(postHtml);
        if (next && next !== url) {
          url = next;
          continue;
        }

        return Response.json({
          error: 'WeBOC tariff page returned but the Income Tax rows could not be parsed',
          weboc_url: url,
          post_status: post.status,
          commercial: rates.commercial,
          industrial: rates.industrial,
          preview: postHtml.slice(0, 3000),
          cookie_names: Object.keys(jar),
        }, { status: 502 });
      }

      return Response.json({ error: 'WeBOC kept rotating the session URL after search POST', weboc_url: url, cookie_names: Object.keys(jar), cookie_debug: jar }, { status: 502 });
    } catch (e) {
      return Response.json({ error: String(e?.message || e), cookie_names: Object.keys(jar), cookie_debug: jar }, { status: 500 });
    }
  }
};
