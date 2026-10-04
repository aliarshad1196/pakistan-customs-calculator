V55 - WeBOC proxy-ready build

The calculator still retrieves Commercial Importer and Industrial Undertaking Income Tax directly from live WeBOC. No tax rate is hard-coded and no generic 12% fallback is used.

Why the proxy option exists:
Render's outbound connection to www.weboc.gov.pk has repeatedly timed out, while Firefox reaches the same WeBOC POST successfully. V55 can tunnel only PSW/WeBOC traffic through a proxy.

To enable it on Render:
1. Deploy proxy/worker.js as a Cloudflare Worker.
2. Add Render environment variable:
   WEBOC_PROXY_URL=https://YOUR-WORKER.workers.dev/
3. Redeploy the calculator.

The Worker is restricted to psw.gov.pk and weboc.gov.pk. It forwards GET/POST, cookies, headers and response bodies, which preserves the ASP.NET session/ViewState flow.

If WEBOC_PROXY_URL is not set, the calculator keeps the existing direct PSW -> WeBOC flow.
