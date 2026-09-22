from flask import Flask, request, jsonify, send_from_directory
import re, requests, urllib3
from bs4 import BeautifulSoup
from urllib.parse import quote

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app=Flask(__name__, static_folder='.')

TIPP_SEARCH="https://www.tipp.gov.pk/index.php?r=tradeInfo/index"

def pct(v):
    if v is None: return 0.0
    m=re.search(r'(\d+(?:\.\d+)?)\s*%', str(v))
    return float(m.group(1))/100 if m else 0.0


def _parse_rate(text):
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", text or "")
    return float(m.group(1)) / 100 if m else 0.0


def _numeric_id_from_href(href):
    if not href:
        return None
    h = href.replace("%2F", "/").replace("%3D", "=").replace("&amp;", "&")
    m = re.search(r"(?:^|[?&])id=(\d+)(?:&|$)", h, re.I)
    return m.group(1) if m else None


def _record_id_from_row(row):
    for attr in ("data-key", "data-id", "data-record-id", "data-product-id"):
        val = row.get(attr)
        if val and re.fullmatch(r"\d+", str(val).strip()):
            return str(val).strip()
    for a in row.find_all("a", href=True):
        rid = _numeric_id_from_href(a.get("href"))
        if rid:
            return rid
    return None


def _find_record_id(search_html, exact):
    soup = BeautifulSoup(search_html, "html.parser")
    compact = re.sub(r"[^0-9]", "", exact)

    # TIPP uses Yii-style result rows in which the model key may be in data-key.
    for row in soup.find_all("tr"):
        txt = re.sub(r"[^0-9A-Za-z]", "", row.get_text(" ", strip=True))
        if compact in txt or (len(compact) >= 8 and compact[:8] in txt):
            rid = _record_id_from_row(row)
            if rid:
                return rid

    # Fallback: valid numeric view link only. Never accept id= with no value.
    for a in soup.find_all("a", href=True):
        rid = _numeric_id_from_href(a.get("href"))
        if rid:
            txt = re.sub(r"[^0-9A-Za-z]", "", a.get_text(" ", strip=True))
            if compact in txt or (len(compact) >= 8 and compact[:8] in txt):
                return rid
    return None


def _search_tipp_for_record(session, exact):
    candidates = [
        {"Product[hscode]": exact}, {"Product[hs_code]": exact},
        {"Product[hsCode]": exact}, {"SearchProduct[hscode]": exact},
        {"SearchProduct[hs_code]": exact}, {"hscode": exact},
        {"hs_code": exact}, {"HSCode": exact}
    ]
    last_status = "unknown"
    for params in candidates:
        params = dict(params)
        params["r"] = "tradeInfo/searchProduct"
        try:
            rr = session.get(TIPP_BASE, params=params, timeout=30, verify=False)
            last_status = rr.status_code
            if rr.ok and exact in rr.text.replace(" ", ""):
                rid = _find_record_id(rr.text, exact)
                if rid:
                    return rid
        except requests.RequestException:
            continue
    raise RuntimeError(
        f"TIPP search did not expose a valid numeric commodity record ID for {exact}. "
        f"Last HTTP status: {last_status}."
    )


def lookup(hs):
    raw = str(hs or "").strip()
    digits = re.sub(r"[^0-9]", "", raw)

    if len(digits) == 8:
        hs8, exact = digits, digits + "0000"
    elif len(digits) == 12:
        hs8, exact = digits[:8], digits
    elif len(digits) in (2, 4, 6):
        hs8, exact = digits, digits
    else:
        raise ValueError(
            "Enter the HS/PCT code in the format 9503.0090 (8 digits), "
            "or a 12-digit TIPP code."
        )

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/140.0 Safari/537.36"
    })

    record_id = _search_tipp_for_record(session, exact)

    # Critical fix: construct the commodity URL from the verified numeric ID.
    view_url = f"{TIPP_BASE}?r=tradeInfo/view&id={record_id}"
    rr = session.get(view_url, timeout=30, verify=False)
    rr.raise_for_status()

    soup = BeautifulSoup(rr.text, "html.parser")
    rates = {"cd":0.0, "acd":0.0, "rd":0.0, "st":0.0, "ast":0.0, "it":0.0}
    non_core = {"concessions":[], "exemptions":[], "provincial_cess":[]}

    for tr in soup.find_all("tr"):
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip()
                 for c in tr.find_all(["th","td"])]
        row = " | ".join(cells)
        low = row.lower()

        if "additional customs duty" in low or re.search(r"\bacd\b", low):
            rates["acd"] = _parse_rate(row)
        elif "customs duty" in low and "additional" not in low:
            rates["cd"] = _parse_rate(row)
        elif "regulatory duty" in low or re.search(r"\brd\b", low):
            rates["rd"] = _parse_rate(row)
        elif "additional sales tax" in low or re.search(r"\bast\b", low):
            rates["ast"] = _parse_rate(row)
        elif "sales tax" in low and "additional" not in low:
            rates["st"] = _parse_rate(row)
        elif "income tax" in low or re.search(r"\bit\b", low):
            rates["it"] = _parse_rate(row)

        if "concession" in low:
            non_core["concessions"].append(row)
        elif "exemption" in low or "exempt" in low:
            non_core["exemptions"].append(row)
        elif "provincial cess" in low or ("cess" in low and "provincial" in low):
            non_core["provincial_cess"].append(row)

    for k, vals in non_core.items():
        seen = set()
        non_core[k] = [x for x in vals if x and not (x in seen or seen.add(x))]

    return {
        "hs_code": hs8, "tipp_code": exact, "record_id": record_id,
        "rates": rates, "non_core": non_core, "source_url": view_url
    }
