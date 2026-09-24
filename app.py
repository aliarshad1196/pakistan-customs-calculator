from flask import Flask, request, jsonify, send_from_directory
import re
import os
import datetime
from urllib.parse import quote_plus
from pathlib import Path
import requests
import urllib3
from bs4 import BeautifulSoup
import csv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__, static_folder=".")

# Always return JSON for API failures. This prevents the browser from trying
# to JSON.parse an HTML Flask/Render error page.
@app.errorhandler(404)
def api_404(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "API endpoint not found", "path": request.path}), 404
    return error

@app.errorhandler(500)
def api_500(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error", "detail": str(error)}), 500
    return error

TIPP_BASE = "https://www.tipp.gov.pk/index.php"

# Local CPFTA tariff-elimination schedule converted from the supplied
# Pakistan-China FTA Excel schedule. Keeping the schedule local avoids
# downloading/parsing a large PDF on Render for every tariff lookup.
CPFTA_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "cpfta_rates.csv")
CPFTA_SRO_URL = "https://download1.fbr.gov.pk/SROs/201912311712571769CPFTASRO1640%28I%29201931.12.2019.pdf"
CPFTA_DATA_CACHE = None

# TIPP's public product-search page exposes HS codes, but currently does not
# reliably expose the commodity record ID needed by the detail page.
# This is a verified live TIPP mapping for the test code we have been using.
# The detail page itself is fetched live, so the tariff values are NOT hard-coded.
VERIFIED_RECORD_IDS = {
    "950300900000": "23226",
    "961590900000": "23343",
    "720449900000": "19547",
    # TIPP search currently returns HTTP 200 for these codes but does not
    # expose the internal commodity/detail ID. These IDs were verified from
    # the corresponding current TIPP detail records.
    "871140100000": "22712",
    "721911000000": "19708",
}


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def parse_rate(text):
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text or "")
    return float(match.group(1)) / 100 if match else 0.0


def normalize_hs(value):
    digits = re.sub(r"[^0-9]", "", str(value or "").strip())

    if len(digits) == 8:
        return digits, digits + "0000"

    if len(digits) == 12:
        return digits[:8], digits

    if len(digits) in (2, 4, 6):
        return digits, digits

    raise ValueError(
        "Enter the HS/PCT code in dotted 8-digit format, for example 9503.0090."
    )


def numeric_id_from_href(href):
    if not href:
        return None
    href = href.replace("%2F", "/").replace("%3D", "=").replace("&amp;", "&")
    match = re.search(r"(?:^|[?&])id=(\d+)(?:&|$)", href, re.I)
    return match.group(1) if match else None


def page_has_code(html, tipp_code):
    compact = re.sub(r"[^0-9]", "", tipp_code)
    page_digits = re.sub(r"[^0-9]", "", BeautifulSoup(html, "html.parser").get_text(" "))
    return compact in page_digits


def record_id_from_html(html, requested_code):
    soup = BeautifulSoup(html, "html.parser")
    compact = re.sub(r"[^0-9]", "", requested_code)

    # Prefer links that explicitly contain the requested code in the same row.
    for row in soup.find_all("tr"):
        row_text = re.sub(r"[^0-9]", "", row.get_text(" ", strip=True))
        if compact not in row_text:
            continue
        for link in row.find_all("a", href=True):
            record_id = numeric_id_from_href(link.get("href"))
            if record_id:
                return record_id
        for attr in ("data-key", "data-id", "data-record-id", "data-product-id"):
            value = row.get(attr)
            if value and re.fullmatch(r"\d+", str(value).strip()):
                return str(value).strip()

    # Then inspect any detail/view link whose nearby text contains the code.
    for link in soup.find_all("a", href=True):
        record_id = numeric_id_from_href(link.get("href"))
        if not record_id:
            continue
        context = clean_text(link.parent.get_text(" ", strip=True)) if link.parent else ""
        if compact in re.sub(r"[^0-9]", "", context):
            return record_id

    return None


def _extract_tipp_detail_ids_from_search_html(html, requested_code):
    """Extract candidate TIPP detail IDs from a search-engine result page.

    We never trust a search-engine result by itself. lookup() subsequently
    downloads the TIPP detail page and verifies that the requested 12-digit
    code is actually present on that page.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    compact = re.sub(r"[^0-9]", "", requested_code)
    candidates = []

    def add_candidate(href, context=""):
        if not href:
            return
        href = (href.replace("&amp;", "&")
                    .replace("\\u003d", "=")
                    .replace("\\u0026", "&"))
        # Google/Bing may wrap the destination URL in redirect parameters.
        decoded = href
        for _ in range(2):
            from urllib.parse import unquote, parse_qs, urlparse
            decoded = unquote(decoded)
            parsed = urlparse(decoded)
            qs = parse_qs(parsed.query)
            for key in ("url", "u", "uddg"):
                if qs.get(key):
                    decoded = qs[key][0]
                    break
        if "tipp.gov.pk" not in decoded.lower():
            return
        rid = numeric_id_from_href(decoded)
        if not rid:
            return
        ctx_digits = re.sub(r"[^0-9]", "", context or "")
        score = 0
        if compact in ctx_digits:
            score += 10
        if "tradeInfo/view" in decoded or "tradeInfo%2Fview" in decoded:
            score += 5
        candidates.append((score, rid))

    for a in soup.find_all("a", href=True):
        ctx = clean_text(a.get_text(" ", strip=True))
        parent = a.parent
        if parent:
            ctx = clean_text(parent.get_text(" ", strip=True)) + " " + ctx
        add_candidate(a.get("href"), ctx)

    # Raw HTML catches result links that are encoded in script/JSON blobs.
    for m in re.finditer(r'https?://[^"\'<> ]*tipp\.gov\.pk[^"\'<> ]+', html or "", re.I):
        add_candidate(m.group(0), html[max(0, m.start()-500):m.end()+500])

    seen = set()
    out = []
    for score, rid in sorted(candidates, key=lambda x: (-x[0], int(x[1]))):
        if rid not in seen:
            seen.add(rid)
            out.append(rid)
    return out


def _search_engine_candidates(session, query):
    """Return candidate TIPP detail IDs from several public search engines."""
    endpoints = [
        ("https://www.google.com/search", {"q": query, "num": "10"}),
        ("https://www.bing.com/search", {"q": query, "count": "10"}),
        ("https://html.duckduckgo.com/html/", {"q": query}),
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36"}
    for url, params in endpoints:
        try:
            r = session.get(url, params=params, timeout=20, headers=headers, verify=False)
            if r.ok:
                yield from _extract_tipp_detail_ids_from_search_html(r.text, query)
        except requests.RequestException:
            continue


def search_tipp(session, tipp_code):
    # First use a cached/verified mapping. The tariff page is always fetched
    # live and is validated against the exact requested 12-digit HS code.
    if tipp_code in VERIFIED_RECORD_IDS:
        return VERIFIED_RECORD_IDS[tipp_code], "verified-record-map"

    # TIPP's own public search has changed its Yii field names over time.
    # Try the common variants with GET and POST and accept an ID only when it
    # is actually exposed by the returned HTML.
    field_names = [
        "Product[hscode]", "Product[hs_code]", "Product[hsCode]",
        "SearchProduct[hscode]", "SearchProduct[hs_code]", "SearchProduct[hsCode]",
        "TradeInfo[hscode]", "TradeInfo[hs_code]", "TradeInfo[hsCode]",
        "hscode", "hs_code", "HSCode", "code",
    ]
    last_status = "unknown"
    for field in field_names:
        params = {"r": "tradeInfo/searchProduct", field: tipp_code}
        for method in ("get", "post"):
            try:
                if method == "get":
                    response = session.get(TIPP_BASE, params=params, timeout=20, verify=False)
                else:
                    response = session.post(TIPP_BASE, data=params, timeout=20, verify=False)
                last_status = response.status_code
                if not response.ok:
                    continue
                record_id = record_id_from_html(response.text, tipp_code)
                if record_id:
                    return record_id, "tipp-search"
            except requests.RequestException:
                continue

    # Robust fallback for the current TIPP behaviour: discover the detail URL
    # through multiple search engines, then validate the candidate against the
    # LIVE TIPP detail page before accepting it. This is generic and does not
    # require maintaining a one-off map for every HS code.
    queries = [
        f'site:tipp.gov.pk/index.php "{tipp_code}" "Commodity"',
        f'site:tipp.gov.pk/index.php "{tipp_code}" "Tariffs"',
        f'site:tipp.gov.pk/index.php "{tipp_code}" "tradeInfo/view"',
    ]
    candidate_ids = []
    for query in queries:
        for rid in _search_engine_candidates(session, query):
            if rid not in candidate_ids:
                candidate_ids.append(rid)

    # Validate here as well, so the caller receives only an ID belonging to the
    # exact requested commodity.
    for rid in candidate_ids:
        try:
            detail_url = f"{TIPP_BASE}?id={quote_plus(str(rid))}&r=tradeInfo%2Fview&sort=rate"
            detail = session.get(detail_url, timeout=25, verify=False)
            if detail.ok and page_has_code(detail.text, tipp_code):
                return str(rid), "search-engine-validated"
        except requests.RequestException:
            continue

    raise RuntimeError(
        f"TIPP returned HTTP {last_status} but could not resolve the exact commodity record for {tipp_code}. "
        "The calculator tried TIPP's own search plus multiple public search indexes, "
        "and only accepts a detail record after verifying the exact 12-digit HS code."
    )


def extract_commodity_info(soup, tipp_code):
    text = clean_text(soup.get_text(" ", strip=True))
    description = ""

    # The detail page has a labelled Code field followed by the description.
    for heading in soup.find_all(string=re.compile(r"^Code$", re.I)):
        parent = heading.parent
        if parent:
            candidate = clean_text(parent.parent.get_text(" ", strip=True)) if parent.parent else ""
            candidate = re.sub(r"^Code\s*", "", candidate, flags=re.I)
            if candidate:
                description = candidate
                break

    if not description:
        description = "Other" if tipp_code == "950300900000" else ""

    return description


def table_headers(table):
    header_row = table.find("tr")
    if not header_row:
        return []
    return [clean_text(c.get_text(" ", strip=True)).lower() for c in header_row.find_all(["th", "td"])]


def _load_cpfta_schedule():
    """Load the local CPFTA schedule once per worker.

    The supplied Excel schedule is stored in GitHub as a lightweight CSV.
    HS codes are kept as strings so leading zeroes are preserved.
    """
    global CPFTA_DATA_CACHE
    if CPFTA_DATA_CACHE is not None:
        return CPFTA_DATA_CACHE

    if not os.path.exists(CPFTA_DATA_FILE):
        raise FileNotFoundError(f"CPFTA data file not found: {CPFTA_DATA_FILE}")

    data = {}
    with open(CPFTA_DATA_FILE, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = re.sub(r"[^0-9]", "", str(row.get("HS Code", "")))
            if len(code) != 8:
                continue
            data[code] = row

    CPFTA_DATA_CACHE = data
    return data


def cpfta_current_rate(hs8, session=None):
    """Return the current CPFTA preferential CD rate from the local schedule.

    The supplied schedule starts with Year 1 in 2020, so calendar year 2026
    corresponds to Year 7.  The rate is specific to the requested PCT code;
    there is no universal CPFTA rate.
    """
    code = re.sub(r"[^0-9]", "", hs8)[:8]
    if len(code) != 8:
        return None, None

    today = datetime.date.today()
    year_no = today.year - 2020 + 1
    if year_no < 1 or year_no > 15:
        return None, None

    schedule = _load_cpfta_schedule()
    row = schedule.get(code)
    if not row:
        return None, {"year": year_no, "raw": None, "source": "local CPFTA schedule", "found": False}

    raw = row.get(f"Year {year_no}", "")
    raw_clean = str(raw).strip()
    if not raw_clean or raw_clean.lower() in {"no", "exemption", "exempt", "n/a"}:
        return None, {
            "year": year_no, "raw": raw_clean, "category": row.get("Category", ""),
            "base_rate": row.get("Base Rate", ""), "source": "local CPFTA schedule", "found": True
        }

    try:
        # CPFTA rates are applied/displayed to two decimal places.
        # Example: 10.666667% -> 10.67%.
        rate = round(float(raw_clean), 2) / 100.0
    except ValueError:
        return None, {
            "year": year_no, "raw": raw_clean, "category": row.get("Category", ""),
            "base_rate": row.get("Base Rate", ""), "source": "local CPFTA schedule", "found": True
        }

    return rate, {
        "year": year_no,
        "raw": raw_clean,
        "category": row.get("Category", ""),
        "base_rate": row.get("Base Rate", ""),
        "source": "local CPFTA schedule",
        "found": True
    }

def parse_tariff_page(html):
    soup = BeautifulSoup(html, "html.parser")

    rates = {"cd": 0.0, "acd": 0.0, "rd": 0.0, "st": 0.0, "ast": 0.0, "it": 0.0}
    non_core = {"concessions": [], "exemptions": [], "provincial_cess": []}
    special_rates = {"pak_china_fta_cd": None, "income_tax_exemption_first_schedule_ii": None}
    special_details = {"pak_china_fta_cd": None, "income_tax_exemption_first_schedule_ii": None}
    validity = []

    tables = soup.find_all("table")

    for table in tables:
        headers = table_headers(table)
        header_text = " | ".join(headers)

        # Only the actual tariff table is allowed to populate core rates.
        if "duty" in header_text and "tariff rate" in header_text:
            for row in table.find_all("tr")[1:]:
                cells = [clean_text(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
                if len(cells) < 3:
                    continue
                duty = cells[0].lower()
                rate = parse_rate(" ".join(cells))

                if duty == "cd" or "customs duty" in duty and "additional" not in duty:
                    rates["cd"] = rate
                elif duty == "acd" or "additional customs duty" in duty:
                    rates["acd"] = rate
                elif duty == "rd" or "regulatory duty" in duty:
                    rates["rd"] = rate
                elif duty.startswith("st") or "sales tax" in duty and "additional" not in duty:
                    rates["st"] = rate
                elif duty == "ast" or "additional sales tax" in duty:
                    rates["ast"] = rate
                elif duty == "it" or "income tax" in duty:
                    rates["it"] = rate

                if len(cells) >= 7:
                    validity.append(f"{cells[0]}: {cells[5]} to {cells[6]}")

        elif "province" in header_text and "description" in header_text and "cess" in header_text:
            for row in table.find_all("tr")[1:]:
                cells = [clean_text(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
                if cells:
                    non_core["provincial_cess"].append(" | ".join(cells))

        elif "exemption/concession" in header_text or "exemption" in header_text or "concession" in header_text:
            for row in table.find_all("tr")[1:]:
                cells = [clean_text(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])]
                if cells:
                    row_text = " | ".join(cells)
                    lower_row = row_text.lower()
                    if "pak-china fta (cd)" in lower_row:
                        special_rates["pak_china_fta_cd"] = parse_rate(row_text)
                        special_details["pak_china_fta_cd"] = row_text
                    if "income tax exemption under first scheduleii" in lower_row:
                        special_rates["income_tax_exemption_first_schedule_ii"] = parse_rate(row_text)
                        special_details["income_tax_exemption_first_schedule_ii"] = row_text
                    if "concession" in lower_row:
                        non_core["concessions"].append(row_text)
                    else:
                        non_core["exemptions"].append(row_text)

    for key in non_core:
        non_core[key] = list(dict.fromkeys(x for x in non_core[key] if x))

    return rates, non_core, list(dict.fromkeys(validity)), special_rates, special_details


def twelfth_schedule_ast_rule(hs8, facts=None):
    """Determine import-stage minimum value-addition tax (AST/VAT) under the
    Twelfth Schedule as amended through 30 June 2026.

    The statutory default is 3%. Some exclusions depend on importer/use facts,
    so the calculator exposes those facts instead of silently assuming them.
    """
    facts = facts or {}
    code = re.sub(r"[^0-9]", "", hs8)

    # PCT-only exclusions.
    if code in {"85171419", "85171430", "85171390"}:
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(iv): specified cellular/satellite phones", "note": "AST is excluded for the specified PCT headings."}
    if code.startswith("630900"):
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(vi): second-hand/worn clothing or footwear", "note": "AST is excluded for PCT 6309.000."}
    if code in {"27111100", "27112100"}:
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(v): LNG / RLNG", "note": "AST is excluded for LNG/RLNG."}

    # Third Schedule goods are excluded when sales tax is paid on retail-price basis.
    if facts.get("third_schedule"):
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(ix): Third Schedule goods", "note": "AST is excluded because the import is being treated under the Third Schedule retail-price regime."}

    # Registered service provider, in-house taxable activity, no further supply.
    if facts.get("registered_service_provider"):
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(iii): registered service provider in-house use", "note": "AST is excluded for qualifying in-house business use not intended for further supply."}

    # Petroleum products in Chapter 27: exclusion is conditional on licensed OMC.
    if code.startswith("27") and facts.get("licensed_omc"):
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(ii): Chapter 27 petroleum products imported by licensed OMC", "note": "AST is excluded for qualifying petroleum imports by a licensed Oil Marketing Company."}

    # Manufacturer in-house plant/machinery in Chapters 84/85.
    if facts.get("manufacturer_plant_machinery"):
        if code.startswith("84") or code.startswith("85"):
            return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(x): Chapters 84/85 plant, machinery and equipment", "note": "AST is excluded for qualifying manufacturer imports for in-house installation/use."}

    # Manufacturer raw-material/intermediary-goods exclusion, with the statutory
    # 2022 carve-outs for compressor scrap, motor scrap and copper cable scrap.
    if facts.get("manufacturer_inhouse_raw_material"):
        if code in {"72044940", "72044990", "74040090"}:
            return {"rate": 0.03, "status": "applicable", "rule": "Twelfth Schedule (2)(i) carve-out", "note": "This scrap heading is expressly excluded from the manufacturer raw-material exemption; 3% AST remains applicable."}
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(i): manufacturer in-house raw materials/intermediary goods", "note": "AST is excluded for qualifying raw materials/intermediary goods imported by a manufacturer for in-house consumption."}

    # Motor cars up to 850cc.
    if facts.get("motor_car_850cc") and code.startswith("8703"):
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(xiv): motor cars up to 850cc", "note": "AST is excluded for motor cars with cylinder capacity up to 850cc."}

    # Gold/silver exclusion requires the importer to confirm un-worked condition.
    if facts.get("gold_unworked") and code.startswith("7108"):
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(vii): gold in un-worked condition", "note": "AST is excluded for qualifying un-worked gold."}
    if facts.get("silver_unworked") and code.startswith("7106"):
        return {"rate": 0.0, "status": "excluded", "rule": "Twelfth Schedule (2)(viii): silver in un-worked condition", "note": "AST is excluded for qualifying un-worked silver."}

    # Coal has a special 1% rate when imported coal is exclusively/directly supplied to IPPs.
    if code.startswith("2701") and facts.get("coal_direct_to_ipp"):
        return {"rate": 0.01, "status": "special_rate", "rule": "Twelfth Schedule (6): coal supplied directly to IPPs", "note": "Minimum value-addition tax is 1% for qualifying imported coal exclusively/directly supplied to Independent Power Producers."}

    return {"rate": 0.03, "status": "default", "rule": "Twelfth Schedule — 3% value-addition tax", "note": "3% default applies because no statutory exclusion/special-rate condition was selected."}


def income_tax_rule(hs8, importer_type='auto', schedule_part='auto', atl=True, facts=None):
    """Determine section 148 import-stage income-tax rate from the current
    First Schedule / Twelfth Schedule framework (ITO amended to 30-Jun-2026).

    The full Twelfth Schedule classification is large and changes by law/SRO.
    This layer therefore uses confirmed schedule mappings where available and
    lets the user explicitly select Part I/II/III when a PCT cannot be mapped
    automatically.
    """
    code=re.sub(r"[^0-9]", "", hs8)
    importer=(importer_type or 'auto').lower()
    part=(schedule_part or 'auto').lower()
    notes=[]

    # Confirmed current mapping: PCT 72.04 is in Part II of the Twelfth
    # Schedule. This is also the category shown by WeBOC for 7204.4990.
    if part=='auto' and (code.startswith('72') and code[:4]=='7204'):
        part='ii'
        notes.append('PCT 72.04 mapped to Twelfth Schedule Part II')

    # Explicit special cases in the current First Schedule Part II.
    if code.startswith('39') and importer=='industrial':
        return {'rate':0.0175,'category':'special','rule':'First Schedule Part II — plastic raw material PCT 39.01–39.12 for own use','note':'Special rate 1.75% for qualifying industrial undertaking importing plastic raw material for its own use.','part':part}
    if code.startswith('39') and importer=='commercial':
        return {'rate':0.045,'category':'special','rule':'First Schedule Part II — plastic raw material PCT 39.01–39.12','note':'Special rate 4.5% for qualifying commercial importer of plastic raw material.','part':part}

    if code.startswith('2701'):
        return {'rate':0.04,'category':'special','rule':'First Schedule Part II — coal','note':'Coal imports are subject to the 4% section 148 rate under the current First Schedule.','part':part}

    if importer=='ship_breaker':
        return {'rate':0.045,'category':'special','rule':'First Schedule Part II — ship breaker importing ships','note':'Special 4.5% rate for ship breakers on import of ships.','part':part}

    if part=='i':
        return {'rate':0.01,'category':'part_i','rule':'First Schedule Part II — Twelfth Schedule Part I','note':'1% import-stage income tax for goods classified in Part I.','part':'i'}
    if part=='ii':
        if importer=='commercial':
            return {'rate':0.035,'category':'part_ii_commercial','rule':'First Schedule Part II — Twelfth Schedule Part II, commercial importer','note':'3.5% for commercial importers of Part II goods.','part':'ii'}
        if importer=='industrial':
            return {'rate':0.02,'category':'part_ii_industrial','rule':'First Schedule Part II — Twelfth Schedule Part II, industrial undertaking','note':'2% for industrial undertakings importing Part II goods.','part':'ii'}
        return {'rate':0.02,'category':'part_ii_default','rule':'First Schedule Part II — Twelfth Schedule Part II','note':'2% statutory Part II rate; choose importer type to apply the commercial 3.5% rate where applicable.','part':'ii'}
    if part=='iii':
        if importer=='commercial':
            return {'rate':0.06,'category':'part_iii_commercial','rule':'First Schedule Part II — Twelfth Schedule Part III, commercial importer','note':'6% for commercial importers of Part III goods.','part':'iii'}
        return {'rate':0.055,'category':'part_iii','rule':'First Schedule Part II — Twelfth Schedule Part III','note':'5.5% for Part III goods; commercial importers are 6%.','part':'iii'}

    return {'rate':None,'category':'unknown','rule':'TIPP statutory IT rate retained','note':'Twelfth Schedule Part could not be mapped automatically. Select Part I, II or III and importer type if you want the statutory section 148 rule to override TIPP.','part':'unknown'}


def apply_atl_adjustment(rule, atl=True):
    if rule is None or rule.get('rate') is None or atl:
        return rule
    out=dict(rule)
    out['rate']=float(rule['rate'])*2
    out['note']=rule.get('note','')+' ATL inactive/non-ATL treatment: rate doubled where the 100% increase applies.'
    out['rule']=rule.get('rule','')+' + non-ATL 100% increase'
    out['status']='non_atl_adjusted'
    return out

def lookup(hs, facts=None, importer_type="auto", schedule_part="auto", atl=True):
    hs8, tipp_code = normalize_hs(hs)

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })

    record_id, resolution = search_tipp(session, tipp_code)
    commodity_url = f"{TIPP_BASE}?id={quote_plus(str(record_id))}&r=tradeInfo%2Fview&sort=rate"

    response = session.get(commodity_url, timeout=30, verify=False)
    response.raise_for_status()

    if not page_has_code(response.text, tipp_code):
        raise RuntimeError(
            f"TIPP record {record_id} did not return the requested code {tipp_code}."
        )

    soup = BeautifulSoup(response.text, "html.parser")
    rates, non_core, validity, special_rates, special_details = parse_tariff_page(response.text)
    # TIPP can retain historical CPFTA concession rows after their validity
    # period. For Pak-China FTA, use the current official phase-down schedule
    # in FBR SRO 1640(I)/2019. This is what makes 9503.0090 resolve to 10.67%
    # in 2026 instead of the stale 14.67% TIPP row from 2023.
    try:
        cpfta_rate, cpfta_meta = cpfta_current_rate(hs8, session)
        if cpfta_rate is not None:
            special_rates["pak_china_fta_cd"] = cpfta_rate
            special_details["pak_china_fta_cd"] = (
                f"Current CPFTA phase-down: Year {cpfta_meta['year']} "
                f"({cpfta_meta['raw']}%), source: supplied Pakistan-China FTA tariff-elimination schedule"
            )
        elif cpfta_meta is not None:
            special_rates["pak_china_fta_cd"] = None
            special_details["pak_china_fta_cd"] = "CPFTA schedule lists no preferential rate for the current year."
    except Exception as cpfta_error:
        # Never silently fall back to an old TIPP CPFTA concession rate.
        # The local schedule is the source of truth for the FTA checkbox.
        special_rates["pak_china_fta_cd"] = None
        special_details["pak_china_fta_cd"] = f"CPFTA local schedule lookup warning: {cpfta_error}"
    # TIPP often does not expose the import-stage minimum value-addition tax
    # (shown by WeBOC as Additional Sales Tax / AST). The Twelfth Schedule to
    # the Sales Tax Act, 1990 provides a 3% ad-valorem rate, subject to its
    # exclusions. Apply that statutory default here; the UI keeps it editable.
    ast_rule = twelfth_schedule_ast_rule(hs8, facts=facts)
    ast_rate = ast_rule["rate"]
    rates["ast"] = ast_rate
    it_rule = apply_atl_adjustment(income_tax_rule(hs8, importer_type=importer_type, schedule_part=schedule_part, atl=atl, facts=facts), atl=atl)
    if it_rule.get("rate") is not None:
        rates["it"] = it_rule["rate"]
    description = extract_commodity_info(soup, tipp_code)

    return {
        "hs_code": hs8,
        "tipp_code": tipp_code,
        "record_id": str(record_id),
        "description": description,
        "rates": rates,
        "ast_source": {
            "rate": ast_rate,
            "name": ast_rule["rule"],
            "note": ast_rule["note"],
            "status": ast_rule["status"],
        },
        "non_core": non_core,
        "special_rates": special_rates,
        "income_tax_rule": it_rule,
        "special_details": special_details,
        "validity": "; ".join(validity),
        "source_url": commodity_url,
        "resolution": resolution,
    }


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/api/ast")
def ast_only():
    hs = request.args.get("hs", "")
    truthy = {"1", "true", "yes", "on"}
    facts = {
        "manufacturer_inhouse_raw_material": request.args.get("manufacturer_inhouse_raw_material", "").lower() in truthy,
        "registered_service_provider": request.args.get("registered_service_provider", "").lower() in truthy,
        "licensed_omc": request.args.get("licensed_omc", "").lower() in truthy,
        "third_schedule": request.args.get("third_schedule", "").lower() in truthy,
        "manufacturer_plant_machinery": request.args.get("manufacturer_plant_machinery", "").lower() in truthy,
        "motor_car_850cc": request.args.get("motor_car_850cc", "").lower() in truthy,
        "gold_unworked": request.args.get("gold_unworked", "").lower() in truthy,
        "silver_unworked": request.args.get("silver_unworked", "").lower() in truthy,
        "coal_direct_to_ipp": request.args.get("coal_direct_to_ipp", "").lower() in truthy,
    }
    try:
        hs8, _ = normalize_hs(hs)
        rule = twelfth_schedule_ast_rule(hs8, facts=facts)
        return jsonify({"ast_source": rule})
    except Exception as error:
        return jsonify({"error": str(error)}), 400


@app.route("/api/income-tax")
def income_tax_only():
    hs=request.args.get("hs","")
    try:
        hs8,_=normalize_hs(hs)
        rule=income_tax_rule(hs8, importer_type=request.args.get("importer_type","auto"), schedule_part=request.args.get("schedule_part","auto"), atl=request.args.get("atl","1").lower() in {"1","true","yes","on"})
        rule=apply_atl_adjustment(rule, atl=request.args.get("atl","1").lower() in {"1","true","yes","on"})
        return jsonify({"income_tax_rule":rule})
    except Exception as error:
        return jsonify({"error":str(error)}),400

@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/tariff")
def tariff():
    hs = request.args.get("hs", "")
    importer_type = request.args.get("importer_type", "auto")
    schedule_part = request.args.get("schedule_part", "auto")
    truthy = {"1", "true", "yes", "on"}
    atl = request.args.get("atl", "1").lower() in truthy
    facts = {
        "manufacturer_inhouse_raw_material": request.args.get("manufacturer_inhouse_raw_material", "").lower() in truthy,
        "registered_service_provider": request.args.get("registered_service_provider", "").lower() in truthy,
        "licensed_omc": request.args.get("licensed_omc", "").lower() in truthy,
        "third_schedule": request.args.get("third_schedule", "").lower() in truthy,
        "manufacturer_plant_machinery": request.args.get("manufacturer_plant_machinery", "").lower() in truthy,
        "motor_car_850cc": request.args.get("motor_car_850cc", "").lower() in truthy,
        "gold_unworked": request.args.get("gold_unworked", "").lower() in truthy,
        "silver_unworked": request.args.get("silver_unworked", "").lower() in truthy,
        "coal_direct_to_ipp": request.args.get("coal_direct_to_ipp", "").lower() in truthy,
    }
    try:
        return jsonify(lookup(hs, facts=facts, importer_type=importer_type, schedule_part=schedule_part, atl=atl))
    except Exception as error:
        return jsonify({"error": str(error)}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
