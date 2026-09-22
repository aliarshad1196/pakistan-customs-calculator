Pakistan Customs Duty Calculator — dynamic TIPP search

Enter dotted 8-digit HS/PCT, e.g. 9503.0090.

The backend:
- normalizes 9503.0090 -> 95030090 -> 950300900000
- opens TIPP's actual Search Commodity form
- discovers the HSCODE field instead of guessing its Yii parameter name
- submits the form and extracts a real numeric commodity record ID
- constructs the commodity URL from that ID
- keeps concessions/exemptions/provincial cess separate from CD/ACD/RD/ST/AST/IT

Render:
Build: pip install -r requirements.txt
Start: gunicorn app:app
Root Directory: blank
