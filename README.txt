Pakistan Customs Duty Calculator — corrected TIPP parser

Use:
- Enter 8-digit dotted HS/PCT code, e.g. 9503.0090.
- The app normalizes it to 95030090 and TIPP 950300900000.
- The parser identifies the real numeric TIPP record ID from the search result,
  including Yii GridView data-key/data-id values.
- It then constructs the commodity URL itself, so malformed links ending in
  id= are not followed.
- Concessions, exemptions, and provincial cess remain separate informational
  data and are NOT included in the core CD/ACD/RD/ST/AST/IT calculation.
- Flask routes /, /health and /api/tariff are included.

Render:
Build: pip install -r requirements.txt
Start: gunicorn app:app
Root Directory: blank (files are in repository root)
