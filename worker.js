const PSW_URL = "https://www.psw.gov.pk/public/utilities?service=tariff_sros";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:157.0) Gecko/20100101 Firefox/157.0";

function cookieJarFromResponse(resp, jar) {
  let lines = [];
  try {
    if (typeof resp.headers.getSetCookie === "function") lines = resp.headers.getSetCookie();
  } catch {}
  if (!lines.length) {
    try {
      const raw = resp.headers.get("Set-Cookie");
      if (raw) lines = raw.split(/,(?=\s*[^;,=\s]+\s*=)/);
    } catch {}
  }
  for (const line of lines) {
    const first = line.split(";", 1)[0].trim();
    const i = first.indexOf("=");
    if (i > 0) jar[first.slice(0, i)] = first.slice(i + 1);
  }
}
function cookieHeader(jar) {
  return Object.entries(jar).map(([k,v]) => `${k}=${v}`).join("; ");
}
function absoluteUrl(base, value) {
  try { return new URL(value, base).toString(); } catch { return null; }
}
function findWebocUrl(html) {
  const decoded = html
    .replaceAll("\\/", "/")
    .replaceAll("\\u002F", "/")
    .replaceAll("\\u003A", ":")
    .replaceAll("\\u0026", "&");
  const patterns = [
    /https?:\/\/(?:www\.)?weboc\.gov\.pk\/[^"'<>\s]*/ig,
    /\/(?:S\([^)]*\))\/Shared\/TariffList\.aspx[^"'<>\s]*/ig,
    /(?:src|href|data-src|data-url|data-href|value)\s*=\s*["']([^"']*TariffList\.aspx[^"']*)["']/ig
  ];
  for (const re of patterns) {
    const m = re.exec(decoded);
    if (!m) continue;
    const candidate = m[1] || m[0];
    const u = absoluteUrl(PSW_URL, candidate);
    if (u && /weboc\.gov\.pk/i.test(u) && /TariffList\.aspx/i.test(u)) return u;
  }
  return null;
}
function hidden(html, name) {
  const re = new RegExp(`<input[^>]+name=["']${name.replace(/[$()*+.?[\\\]^{|}]/g,"\\$&")}[^>]+value=["']([^"']*)["']`, "i");
  const m = html.match(re);
  return m ? m[1] : "";
}
function textOnly(html) {
  return html.replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ").replace(/&amp;/gi,"&").replace(/&quot;/gi,'"')
    .replace(/\s+/g, " ").trim();
}
function extractPercent(s) {
  const m = s.match(/@\s*(\d+(?:\.\d+)?)\s*%/i) || s.match(/(?:^|\s)(\d+(?:\.\d+)?)\s*%/i);
  return m ? Number(m[1]) / 100 : null;
}
function parseRates(html) {
  // WeBOC renders the tariff as an HTML table. The ordinary tariff row can
  // contain "Income Tax @ 12%", so searching for the first Income Tax rate is
  // wrong. We must identify the specific First Schedule rows for Commercial
  // Importer and Industrial Undertaking.
  let commercial = null;
  let industrial = null;
  const matchedRows = [];

  const rowRe = /<tr\b[^>]*>[\s\S]*?<\/tr>/gi;
  let m;
  while ((m = rowRe.exec(html)) !== null) {
    const rowHtml = m[0];
    const rowText = textOnly(rowHtml);
    const lower = rowText.toLowerCase();
    if (!lower.includes("income tax")) continue;

    const rate = extractPercent(rowText);
    if (rate === null) continue;

    // Match the actual First Schedule classification, not generic tariff
    // "Income Tax @ 12%" rows.
    if (/commercial\s*importer/i.test(rowText) && /part[-\s]*ii/i.test(rowText)) {
      commercial = rate;
      matchedRows.push({type:"commercial", text:rowText.slice(0,500), rate});
    }
    if (/industrial\s*undertaking/i.test(rowText) && /part[-\s]*ii/i.test(rowText)) {
      industrial = rate;
      matchedRows.push({type:"industrial", text:rowText.slice(0,500), rate});
    }
  }

  // Fallback for layouts where the row markup is not a normal <tr>. Search
  // specifically around the First Schedule wording and never use the generic
  // Income Tax tariff row.
  const t = textOnly(html);
  const incomePositions = [];
  const re = /income tax/ig;
  let x;
  while ((x = re.exec(t)) !== null) incomePositions.push(x.index);

  for (const pos of incomePositions) {
    const chunk = t.slice(pos, pos + 900);
    const lower = chunk.toLowerCase();
    if (!lower.includes("first schedule") && !lower.includes("twelfth schedule") && !lower.includes("commercial importer") && !lower.includes("industrial undertaking")) continue;
    const rate = extractPercent(chunk);
    if (rate === null) continue;
    if (commercial === null && /commercial\s*importer/i.test(chunk) && /part[-\s]*ii/i.test(chunk)) {
      commercial = rate;
      matchedRows.push({type:"commercial-fallback", text:chunk.slice(0,500), rate});
    }
    if (industrial === null && /industrial\s*undertaking/i.test(chunk) && /part[-\s]*ii/i.test(chunk)) {
      industrial = rate;
      matchedRows.push({type:"industrial-fallback", text:chunk.slice(0,500), rate});
    }
  }

  const incomeIndex = t.toLowerCase().indexOf("income tax");
  return {
    commercial,
    industrial,
    matched_rows: matchedRows,
    text_preview: incomeIndex >= 0 ? t.slice(incomeIndex, incomeIndex + 2200) : t.slice(0,2200)
  };
}
async function get(url, jar, referer) {
  const headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language":"en-US,en;q=0.9"};
  if (referer) headers.Referer = referer;
  const c = cookieHeader(jar); if (c) headers.Cookie = c;
  const r = await fetch(url, {method:"GET", headers, redirect:"manual"});
  cookieJarFromResponse(r, jar);
  return r;
}
async function post(url, body, jar, referer) {
  const headers = {"User-Agent": UA, "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language":"en-US,en;q=0.9", "Content-Type":"application/x-www-form-urlencoded", "Origin":"https://www.weboc.gov.pk"};
  if (referer) headers.Referer = referer;
  const c = cookieHeader(jar); if (c) headers.Cookie = c;
  const r = await fetch(url, {method:"POST", headers, body, redirect:"manual"});
  cookieJarFromResponse(r, jar);
  return r;
}

export default {
  async fetch(request) {
    const u = new URL(request.url);
    if (u.pathname !== "/weboc") return new Response("WeBOC proxy OK. Use /weboc?hs=9503.0090", {status:200});
    const hs = u.searchParams.get("hs") || "";
    if (!hs) return Response.json({error:"Missing hs"},{status:400});

    const jar = {};
    try {
      // 1) Establish the PSW browser session and discover the exact WeBOC iframe URL.
      const psw = await get(PSW_URL, jar, "https://www.psw.gov.pk/");
      const pswHtml = await psw.text();
      let webocUrl = findWebocUrl(pswHtml);
      if (!webocUrl) return Response.json({error:"PSW did not expose a session-specific WeBOC TariffList URL", psw_status:psw.status},{status:502});

      // 2) GET the exact session URL and capture ASP.NET/session cookies + hidden fields.
      let page = await get(webocUrl, jar, "https://www.psw.gov.pk/");
      let pageHtml = await page.text();
      if (page.status >= 300 && page.status < 400) {
        const loc = page.headers.get("Location");
        if (loc) { webocUrl = absoluteUrl(webocUrl, loc); page = await get(webocUrl, jar, "https://www.psw.gov.pk/"); pageHtml = await page.text(); }
      }

      const vs = hidden(pageHtml, "__VIEWSTATE");
      const vg = hidden(pageHtml, "__VIEWSTATEGENERATOR");
      const ve = hidden(pageHtml, "__EVENTVALIDATION");
      const body = new URLSearchParams();
      body.set("__VIEWSTATE", vs); body.set("__VIEWSTATEGENERATOR", vg); body.set("__VIEWSTATEENCRYPTED", ""); body.set("__EVENTVALIDATION", ve);
      body.set("TariffList$txtSearch", hs); body.set("TariffList$btnSearch", "Search");

      // 3) POST exactly like Firefox, retaining the cookie jar.
      let result = await post(webocUrl, body.toString(), jar, webocUrl);
      let resultHtml = await result.text();
      let hops = 0;
      // 4) WeBOC can rotate the (S(...)) session on 302. Follow it manually while
      // retaining all cookies, instead of allowing fetch to lose the session state.
      while (result.status >= 300 && result.status < 400 && hops < 4) {
        const loc = result.headers.get("Location");
        if (!loc) break;
        webocUrl = absoluteUrl(webocUrl, loc);
        result = await get(webocUrl, jar, webocUrl);
        resultHtml = await result.text();
        hops++;
      }

      const rates = parseRates(resultHtml);
      return Response.json({
        commercial: rates.commercial,
        industrial: rates.industrial,
        weboc_url: webocUrl,
        post_status: result.status,
        redirects_followed: hops,
        cookie_names: Object.keys(jar),
        cookie_debug: jar,
        preview: rates.text_preview
      });
    } catch (e) {
      return Response.json({error:String(e?.message || e)}, {status:500});
    }
  }
};
