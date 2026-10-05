WEBOC LIVE INCOME TAX PROXY

This package preserves the v50 application UI, password protection, data files,
and Render configuration. Only the WeBOC lookup path was changed.

Cloudflare Worker:
- Deploy worker.js to a Cloudflare Worker.
- The worker endpoint should be /weboc?hs=9503.0090
- The default endpoint in app.py is:
  https://webo-proxy.aliarshad1196.workers.dev/weboc

If your Worker URL is different, set this Render environment variable:
  WEBOC_PROXY_URL=https://YOUR-WORKER.workers.dev/weboc

The Worker handles WeBOC's ASP.NET cookieless /(S(...))/ session rotation by
GETting the redirected session page, extracting fresh ViewState/EventValidation,
and POSTing the search again. No Commercial or Industrial income-tax rate is
hard-coded into the Worker.

The calculator keeps the existing v50 UI and all original project/data files.
