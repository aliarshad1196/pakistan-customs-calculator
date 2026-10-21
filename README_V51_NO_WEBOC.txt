Pakistan Customs Duty Calculator — v51 (WeBOC paused)

This package restores the last pre-WeBOC version (v43). It preserves the existing calculator UI, calculations, CPFTA CSV, TIPP lookup/mapping, password protection, and other features from that version. The experimental live WeBOC integration and diagnostic code are not included.

Deploy the files in this ZIP to the repository root, preserving the data/ folder and data/cpfta_rates.csv. Keep Render environment variables SITE_PASSWORD and FLASK_SECRET_KEY unchanged.

Repair notes: WeBOC is not called by this version. Home route now serves index.html using an absolute path, and the final TIPP detail request has a bounded timeout to reduce worker hangs. UI and calculation logic are otherwise unchanged.
