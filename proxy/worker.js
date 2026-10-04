const ALLOWED = new Set([
  "www.psw.gov.pk",
  "psw.gov.pk",
  "www.weboc.gov.pk",
  "weboc.gov.pk",
]);

const PSW_TARIFF = "https://www.psw.gov.pk/public/utilities?service=tariff_sros";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36";

function cookieHeader(response) {
  // Workers exposes Set-Cookie values via getAll on the Headers implementation.
  try {
    const all = response.headers.getAll("Set-Cookie");
    if (all && all.length) return all.map(v => v.split(";", 1)[0]).join("; ");
  } catch (_) {}
  const one = response.headers.get("Set-Cookie");
  return one ? one.split(";", 1)[0] : "";
}

function decodeEscaped(s) {
  return String(s || "")
    .replace(/\\\//g, "/")
    .replace(/\\u0026/gi, "&")
    .replace(/\\u003A/gi, ":")
    .replace(/\\u003D/gi, "=")
    .replace(/&amp;/gi, "&")
    .replace(/&quot;/gi, '"');
}

function extractWebocUrl(html) {
  const text = decodeEscaped(html);
  const patterns = [
    /https?:\/\/www\.weboc\.gov\.pk\/\(S\([^)]*\)\)\/Shared\/TariffList\.aspx[^\"'<>\s]*/ig,
    /https?:\/\/weboc\.gov\.pk\/\(S\([^)]*\)\)\/Shared\/TariffList\.aspx[^\"'<>\s]*/ig,
    /\/(?:S)\([^)]*\)\/Shared\/TariffList\.aspx[^\"'<>\s]*/ig,
  ];
  for (const re of patterns) {
    const m = re.exec(text);
    if (m) {
      let u = m[0];
      if (u.startsWith("/")) u = "https://www.weboc.gov.pk" + u;
      return u.replace(/[),;]+$/, "");
    }
  }
  return null;
}

function hiddenFields(html) {
  const out = {};
  const re = /<input\b[^>]*\btype=["']hidden["'][^>]*>/ig;
  let m;
  while ((m = re.exec(html))) {
    const tag = m[0];
    const nm = /\bname=["']([^"']+)["']/i.exec(tag);
    const val = /\bvalue=["']([^"']*)["']/i.exec(tag);
    if (nm) out[nm[1]] = val ? decodeEscaped(val[1]) : "";
  }
  return out;
}

function extractRates(html) {
  const soupText = html
    .replace(/<script[\s\S]*?<\/script>/ig, " ")
    .replace(/<style[\s\S]*?<\/style>/ig, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/\s+/g, " ");

  function rateFor(labelRe) {
    const patterns = [
      new RegExp(labelRe.source + "[\\s\\S]{0,900}?(\\d+(?:\\.\\d+)?)\\s*%", "i"),
      new RegExp("(\\d+(?:\\.\\d+)?)\\s*%[\\s\\S]{0,900}?" + labelRe.source, "i"),
    ];
    for (const re of patterns) {
      const m = re.exec(soupText);
      if (m) return Number(m[1]);
    }
    return null;
  }

  // Prefer the exact descriptions seen in the real WeBOC tariff table.
  const commercial = rateFor(/Commercial\s+Importer[^%]{0,500}?/i);
  const industrial = rateFor(/Industrial\s+Undertaking[^%]{0,500}?/i);

  // More targeted fallback around the Income Tax section.
  let itSection = soupText;
  const idx = soupText.search(/Income\s+Tax/i);
  if (idx >= 0) itSection = soupText.slice(idx, idx + 6000);

  function sectionRate(re) {
    const m = re.exec(itSection);
    return m ? Number(m[1]) : null;
  }

  const commercial2 = sectionRate(/Commercial\s+Importer[\s\S]{0,1000}?(\d+(?:\.\d+)?)\s*%/i);
  const industrial2 = sectionRate(/Industrial\s+Undertaking[\s\S]{0,1000}?(\d+(?:\.\d+)?)\s*%/i);

  return {
    commercial: commercial2 ?? commercial,
    industrial: industrial2 ?? industrial,
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
  });
}

export default {
  async fetch(request) {
    const u = new URL(request.url);
    if (request.method !== "GET" || u.pathname !== "/weboc") {
      return json({ ok: true, service: "weboс-session-proxy", usage: "/weboc?hs=9503.0090" });
    }

    const hs = (u.searchParams.get("hs") || "").trim();
    if (!hs) return json({ error: "Missing hs" }, 400);

    try {
      // 1) Load PSW first. This is what creates/discovers the session-specific WeBOC iframe URL.
      const psw = await fetch(PSW_TARIFF, {
        method: "GET",
        redirect: "manual",
        headers: {
          "User-Agent": UA,
          "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          "Accept-Language": "en-US,en;q=0.9",
        },
      });

      const pswHtml = await psw.text();
      let webocUrl = extractWebocUrl(pswHtml);

      // Some PSW responses can redirect before exposing the iframe.
      if (!webocUrl) {
        const loc = psw.headers.get("Location");
        if (loc && /weboc\.gov\.pk/i.test(loc)) webocUrl = new URL(loc, PSW_TARIFF).toString();
      }
      if (!webocUrl) {
        return json({ error: "PSW did not expose a session-specific WeBOC TariffList URL", psw_status: psw.status }, 502);
      }

      let cookies = cookieHeader(psw);
      const baseHeaders = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.psw.gov.pk/",
      };
      if (cookies) baseHeaders["Cookie"] = cookies;

      // 2) GET the exact session-specific TariffList.aspx URL.
      const page = await fetch(webocUrl, { method: "GET", redirect: "manual", headers: baseHeaders });
      const pageHtml = await page.text();
      const pageCookies = cookieHeader(page);
      if (pageCookies) cookies = cookies ? `${cookies}; ${pageCookies}` : pageCookies;

      // 3) Reproduce Firefox's ASP.NET POST with the hidden state fields.
      const fields = hiddenFields(pageHtml);
      fields["TariffList$txtSearch"] = hs;
      fields["TariffList$btnSearch"] = "Search";

      const body = new URLSearchParams(fields).toString();
      const postHeaders = {
        ...baseHeaders,
        "Referer": webocUrl,
        "Origin": "https://www.weboc.gov.pk",
        "Content-Type": "application/x-www-form-urlencoded",
      };
      if (cookies) postHeaders["Cookie"] = cookies;

      const result = await fetch(webocUrl, {
        method: "POST",
        redirect: "manual",
        headers: postHeaders,
        body,
      });
      const resultHtml = await result.text();
      const rates = extractRates(resultHtml);

      if (rates.commercial === null || rates.industrial === null) {
        return json({
          error: "WeBOC tariff page returned but the Income Tax rows could not be parsed",
          weboc_url: webocUrl,
          post_status: result.status,
          commercial: rates.commercial,
          industrial: rates.industrial,
          preview: resultHtml.replace(/\s+/g, " ").slice(0, 1200),
        }, 502);
      }

      return json({
        ok: true,
        hs,
        weboc_url: webocUrl,
        commercial: rates.commercial,
        industrial: rates.industrial,
        post_status: result.status,
      });
    } catch (e) {
      return json({ error: String(e && e.message ? e.message : e) }, 502);
    }
  },
};
