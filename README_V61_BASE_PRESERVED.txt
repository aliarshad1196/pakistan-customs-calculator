V61 is based directly on the V55 calculator UI/application/data.
The full calculator UI, Duty Difference Calculator, GST, FTA, TIPP mapping,
CPFTA data, password protection and tariff functionality are preserved.
Only the live Income Tax API path was patched to use the verified Cloudflare
Worker /weboc endpoint first, with the existing V55 direct WeBOC path retained
as a secondary fallback. No Income Tax percentage is hard-coded.
Set Render environment variable:
WEBOC_PROXY_URL=https://webo-proxy.aliarshad1196.workers.dev
