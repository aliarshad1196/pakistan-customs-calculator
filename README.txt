Pakistan Customs Duty Calculator v48

Income Tax source:
- Commercial Income Tax is fetched dynamically from the live WeBOC Tariff & SROs page for the entered HS/PCT code.
- Industrial concern rate is fetched dynamically from the same WeBOC result.
- The parser reads the actual "Exemptions / SROs" Income Tax rows containing "commercial importer" and "industrial undertaking".
- No Commercial or Industrial Income Tax percentage is hard-coded.
- If WeBOC cannot be queried or does not return a category rate, the calculator does NOT substitute TIPP's generic 12% rate. It reports that the WeBOC category rate is unavailable.

Other functionality from the previous build is retained, including password protection, TIPP tariff lookup, CPFTA, AST and GST.

Keep Render environment variables SITE_PASSWORD and FLASK_SECRET_KEY unchanged.
