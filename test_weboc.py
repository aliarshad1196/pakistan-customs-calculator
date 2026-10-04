"""Pre-deployment tests for the WeBOC income-tax parser.

Run with: python test_weboc.py
This test uses the archived Tariff & SROs HTML supplied for development and
also attempts a live GET to WeBOC. It never invents or hard-codes tax rates.
"""
import plistlib, re, sys
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT.parent / "files" / "Tariff & SROs.webarchive"

# Standalone copy of the production parser's extraction logic for an offline test.
def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()

def extract_category_rate(text, category):
    t = clean_text(text)
    patterns = ([
        r"commercial\s+importer.{0,500}?@?\s*(\d+(?:\.\d+)?)\s*%",
        r"commercial.{0,500}?import.{0,300}?@?\s*(\d+(?:\.\d+)?)\s*%",
    ] if category == "commercial" else [
        r"industrial\s+undertaking.{0,500}?@?\s*(\d+(?:\.\d+)?)\s*%",
        r"industrial\s+concern.{0,500}?@?\s*(\d+(?:\.\d+)?)\s*%",
    ])
    for pat in patterns:
        m = re.search(pat, t, re.I)
        if m:
            return float(m.group(1)) / 100
    return None

def parse(html):
    soup = BeautifulSoup(html, "html.parser")
    out = {"commercial": None, "industrial": None, "commercial_detail": None, "industrial_detail": None}
    for tr in soup.find_all("tr"):
        cells = [clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if not cells: continue
        row = " | ".join(cells); low = row.lower()
        if "income tax" not in low: continue
        if "commercial importer" in low or ("commercial" in low and "import" in low):
            rate = extract_category_rate(row, "commercial")
            if rate is not None: out["commercial"] = rate; out["commercial_detail"] = row
        if "industrial undertaking" in low or "industrial concern" in low:
            rate = extract_category_rate(row, "industrial")
            if rate is not None: out["industrial"] = rate; out["industrial_detail"] = row
    return out

def main():
    archive = Path(sys.argv[1]) if len(sys.argv) > 1 else ARCHIVE
    assert archive.exists(), f"Archive not found: {archive}"
    d = plistlib.load(open(archive, "rb"))
    html = d["WebMainResource"]["WebResourceData"]
    result = parse(html)
    print("OFFLINE ARCHIVE TEST")
    print("Commercial:", result["commercial"])
    print("Industrial:", result["industrial"])
    assert abs(result["commercial"] - 0.035) < 1e-12, result
    assert abs(result["industrial"] - 0.02) < 1e-12, result
    print("PASS: archived WeBOC rows parsed as 3.5% commercial and 2% industrial.")

    print("\nLIVE WEBOC CONNECTIVITY TEST")
    try:
        import requests
        r = requests.get("https://www.weboc.gov.pk/Shared/TariffList.aspx", timeout=(8, 20), verify=False, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 Version/26.5.2 Mobile/15E148 Safari/604.1"})
        print("HTTP:", r.status_code)
        print("Final URL:", r.url)
        print("Bytes:", len(r.content))
        print("Form present:", bool(BeautifulSoup(r.text, "html.parser").find("form", id="form1") or BeautifulSoup(r.text, "html.parser").find("form")))
    except Exception as e:
        print("LIVE TEST COULD NOT CONNECT:", repr(e))
        print("This is a connectivity result, not a parser failure.")

if __name__ == "__main__":
    main()
