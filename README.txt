PAKISTAN CUSTOMS DUTY CALCULATOR — ONLINE

Files:
- index.html
- app.py
- requirements.txt
- render.yaml
- README.txt

GitHub + Render deployment:
1. Create/open a GitHub repository.
2. Upload all files above to the repository root.
3. In Render, choose New > Web Service and connect this GitHub repository.
4. Render can use render.yaml automatically. If it asks manually:
   Build Command: pip install -r requirements.txt
   Start Command: gunicorn app:app
   Plan: Free
5. Deploy and open the generated HTTPS URL on your phone.

No installation is needed on the phone.

Calculation logic:
CV = Quantity × Unit Price × Exchange Rate
CD = CV × CD Rate
ACD = CV × ACD Rate
RD = CV × RD Rate
ST = (CV + CD + RD) × ST Rate
AST = (CV + CD + RD) × AST Rate
IT = (CV + CD + RD + ACD + AST + ST) × IT Rate
Total Duties & Taxes = CD + ACD + RD + ST + AST + IT
Total Landed Value = CV + Total Duties & Taxes
