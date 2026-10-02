Pakistan Customs Duty Calculator — v46

This build keeps the existing TIPP/CPFTA/GST/AST/password features and adds a robust WeBOC Tariff & SROs Income Tax lookup.

Income Tax:
- Commercial Importer is the default and uses the Commercial Importer rate from WeBOC Exemptions/SROs when available.
- Industrial concern is a checkbox and switches to the Industrial Undertaking rate from WeBOC.
- The generic 12% tariff-line Income Tax remains only as fallback when WeBOC cannot be queried.

WeBOC access is best-effort and bounded by short timeouts so a WeBOC outage cannot turn /api/tariff into an HTML/500 response.

Keep SITE_PASSWORD and FLASK_SECRET_KEY in Render environment variables. Do not put secrets in GitHub.
