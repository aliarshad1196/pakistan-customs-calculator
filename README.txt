Pakistan Customs Duty Calculator

Deployment
1. Upload all files/folders in this package to the GitHub repository root.
2. Keep the data folder and cpfta_rates.csv in the same repository.
3. Render build command: pip install -r requirements.txt
4. Render start command: gunicorn --timeout 120 app:app

CPFTA
The local data/cpfta_rates.csv file was converted from the supplied
Tariff-Elimiantion-Schedule-of-Pakistan-final.xlsx. The app uses the
calendar-year mapping Year 1 = 2020, so 2026 = Year 7. CPFTA rates are
looked up by exact 8-digit PCT code; no PDF is downloaded or parsed.

Do not upload __pycache__.

Password protection
The entire website and all /api/* tariff endpoints are protected by one shared password.
Set these Render environment variables before deploying:
- SITE_PASSWORD = your chosen access password
- FLASK_SECRET_KEY = a long random secret (recommended)

Do not put the password in index.html. If SITE_PASSWORD is empty, users cannot authenticate.
After login, the browser receives a secure HttpOnly session cookie. Use the Logout button to end the session.
