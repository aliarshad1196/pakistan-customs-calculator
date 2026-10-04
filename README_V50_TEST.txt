v50 — WeBOC LIVE DIAGNOSTIC TEST BUILD

Purpose
-------
This is a PRE-DEPLOYMENT diagnostic build. It is not the production release.

What was added
---------------
1. /api/weboc-debug?hs=9503.0090
   - Protected by the existing login.
   - Creates a fresh WeBOC session.
   - Reports GET status/final URL, form detection, search field, POST status,
     result-page markers, and the parsed Commercial/Industrial IT rows.
   - Does not contain hard-coded tax rates.

2. test_weboc.py
   - Offline-tests the parser against the supplied WeBOC Tariff & SROs
     webarchive.
   - Also attempts a live WeBOC connectivity test.
   - Pass an archive path explicitly if needed:
       python test_weboc.py "Tariff & SROs.webarchive"

Pre-deployment test result
--------------------------
The supplied archived WeBOC page parsed successfully:
- Commercial Importer: 3.5%
- Industrial Undertaking: 2%

Those values are extracted from the archived HTML; they are NOT embedded as
fallback values in the application.

The live test from this development environment could not resolve
www.weboc.gov.pk (DNS/network failure). Therefore live WeBOC success has NOT
been claimed or assumed.

Deployment test
---------------
After uploading this test build to a test deployment, log in and open:
  /api/weboc-debug?hs=9503.0090
The JSON response should show the actual WeBOC session/POST diagnostics.
Do not deploy this diagnostic endpoint as the final public production API
without reviewing/removing it after testing.
