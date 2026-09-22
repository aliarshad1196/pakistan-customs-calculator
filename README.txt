Pakistan Customs Duty Calculator — Final TIPP Lookup Build

Files are intended for the ROOT of the GitHub repository:
- app.py
- index.html
- requirements.txt
- render.yaml
- README.txt

Render:
- Root Directory: leave BLANK
- Build Command: pip install -r requirements.txt
- Start Command: gunicorn app:app
- Plan: Free

Test:
- Open the site and enter 9503.0090
- TIPP should resolve to 950300900000 / record 23226
- The detail page is fetched LIVE from TIPP, so tariff rates are not hard-coded.
- Current verified TIPP tariff record has CD 20%, ACD 2%, RD 16%, ST 18%, IT 12%, with no AST tariff row, so AST is 0%.

Important:
- Concessions, exemptions and provincial cess are displayed separately and are NOT automatically included in the core calculation.
- Core calculation formulas are unchanged.
