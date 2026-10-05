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
      const baseHeaders = new Headers(options.headers || {});
      baseHeaders.set('User-Agent', UA);
      if (!baseHeaders.has('Accept')) baseHeaders.set('Accept', 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8');
      if (Object.keys(jar).length) baseHeaders.set('Cookie', cookieHeader());

      // WeBOC occasionally returns Cloudflare 522 from one hostname/edge.
      // Retry the same request against the bare host before giving up.
      const targets = [url];
      try {
        const parsed = new URL(url);
        if (parsed.hostname === 'www.weboc.gov.pk') {
          const alt = new URL(url);
          alt.hostname = 'weboc.gov.pk';
          targets.push(alt.toString());
        } else if (parsed.hostname === 'weboc.gov.pk') {
          const alt = new URL(url);
          alt.hostname = 'www.weboc.gov.pk';
          targets.push(alt.toString());
        }
      } catch (_) {}

      let lastResp = null;
      let lastError = null;
      for (let n = 0; n < targets.length; n++) {
        const target = targets[n];
        const headers = new Headers(baseHeaders);
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 10000);
        try {
          const resp = await fetch(target, { ...options, headers, redirect: 'manual', signal: controller.signal });
          captureCookies(resp);
          lastResp = resp;
          if (resp.status !== 522 || n === targets.length - 1) return resp;
          // 522: give the alternate hostname a chance.
        } catch (e) {
          lastError = e;
          if (n === targets.length - 1) throw e;
        } finally {
          clearTimeout(timer);
        }
      }
      if (lastResp) return lastResp;
      throw lastError || new Error('WeBOC request failed');
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
      // WeBOC can intermittently stall for a particular PCT. Do not let one
      // stalled session poison the whole request: retry once with a completely
      // fresh PSW/WeBOC session and fresh cookies/ViewState.
      let lastTimeout = null;

      for (let cycle = 0; cycle < 2; cycle++) {
        // Fresh session for every cycle.
        for (const k of Object.keys(jar)) delete jar[k];

        try {
          // 1. Open PSW and discover the current cookieless WeBOC session URL.
          const psw = await req(PSW, { headers: { Referer: 'https://www.google.com/' } });
          const pswHtml = await textOf(psw);
          let url = sessionUrlFromHtml(pswHtml);
          if (!url) {
            return Response.json({ error: 'Could not discover WeBOC TariffList.aspx session URL', cookie_names: Object.keys(jar), retry_cycle: cycle + 1 }, { status: 502 });
          }

          // 2. WeBOC can rotate /(S(...))/ on POST. Each redirect is therefore
          // treated as a new live form: GET the new session URL, read fresh
          // ViewState/EventValidation, then POST the search again.
          for (let attempt = 0; attempt < 4; attempt++) {
            const page = await req(url, { headers: { Referer: PSW } });
            const html = await textOf(page);

            // WeBOC can rotate the /(S(...))/ session URL even on GET.
            // Because redirects are manual, a 302 must be followed before
            // attempting to parse ViewState/EventValidation. Otherwise the
            // body is only the "Object moved" page and form fields appear missing.
            if (page.status >= 300 && page.status < 400) {
              const loc = page.headers.get('Location');
              if (loc) {
                url = absolute(url, loc);
                continue;
              }
            }

            if (page.status === 522) {
              // A 522 is retryable: abandon this session and start fresh.
              throw Object.assign(new Error('WeBOC origin returned HTTP 522 while opening TariffList.aspx'), { code: 'WEBOC_522', weboc_url: url, preview: html.slice(0, 500), attempt });
            }
            const formAction = sessionUrlFromHtml(html) || url;
            const vs = hidden(html, '__VIEWSTATE');
            const vg = hidden(html, '__VIEWSTATEGENERATOR');
            const ev = hidden(html, '__EVENTVALIDATION');
            const ve = hidden(html, '__VIEWSTATEENCRYPTED');

            if (!vs || !ev) {
              return Response.json({ error: 'WeBOC form fields could not be parsed', weboc_url: url, preview: html.slice(0, 1200), cookie_names: Object.keys(jar), retry_cycle: cycle + 1 }, { status: 502 });
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

            if (post.status === 522) {
              throw Object.assign(new Error('WeBOC origin returned HTTP 522 after search POST'), { code: 'WEBOC_522', weboc_url: formAction, preview: postHtml.slice(0, 500), attempt });
            }

            if (post.status >= 300 && post.status < 400) {
              const loc = post.headers.get('Location');
              if (!loc) break;
              url = absolute(formAction, loc);
              continue;
            }

            const rates = parseRates(postHtml);
            if (rates.commercial !== null || rates.industrial !== null) {
              return Response.json({ ok: true, weboc_url: url, ...rates, cookie_names: Object.keys(jar), retry_cycle: cycle + 1 });
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
              retry_cycle: cycle + 1,
            }, { status: 502 });
          }

          // Session kept rotating without yielding a tariff page. Start a fresh cycle.
          lastTimeout = { code: 'SESSION_ROTATION', weboc_url: url };
        } catch (e) {
          if (e?.name === 'AbortError') {
            lastTimeout = { code: 'TIMEOUT', message: 'WeBOC request timed out after 10 seconds' };
            continue;
          }
          if (e?.code === 'WEBOC_522') {
            lastTimeout = { code: '522', message: e.message, weboc_url: e.weboc_url, preview: e.preview };
            continue;
          }
          throw e;
        }
      }

      return Response.json({
        error: lastTimeout?.message || 'WeBOC lookup failed after retrying with fresh sessions',
        hs,
        retries: 2,
        retry_reason: lastTimeout?.code || 'UNKNOWN',
        weboc_url: lastTimeout?.weboc_url || null,
        preview: lastTimeout?.preview || null,
        cookie_names: Object.keys(jar),
      }, { status: 504 });
    } catch (e) {
      return Response.json({ error: String(e?.name === 'AbortError' ? 'WeBOC request timed out after 10 seconds' : (e?.message || e)), cookie_names: Object.keys(jar), cookie_debug: jar }, { status: e?.name === 'AbortError' ? 504 : 500 });
    }
  }
};
