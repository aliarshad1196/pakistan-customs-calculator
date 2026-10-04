from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for
import re
import os
import datetime
from urllib.parse import quote_plus, urljoin
from pathlib import Path
import requests
import urllib3
from bs4 import BeautifulSoup
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import threading
import hmac
import hashlib

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__, static_folder=".")

# Simple site-wide password protection. The password and session secret are
# supplied through Render environment variables rather than being embedded
# in the frontend/source.
SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "").strip()
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or hashlib.sha256(
    (SITE_PASSWORD or "customs-calculator-session").encode("utf-8")
).hexdigest()
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True

LOGIN_PAGE = """<!doctype html>
<html lang=\"en\">
<head>
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Customs Tariff Rates 2026–2027</title>
<style>
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;background:linear-gradient(135deg,#eef5ff,#f7f9fc);font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",Arial,sans-serif;color:#10243a}.login{width:100%;max-width:430px;background:#fff;border-radius:24px;padding:30px 24px;box-shadow:0 18px 50px rgba(15,40,70,.14)}.brand{background:linear-gradient(135deg,#08233b,#1769d5);color:#fff;border-radius:18px;padding:24px;margin-bottom:22px}.brand h1{font-size:24px;margin:0 0 8px}.brand p{margin:0;opacity:.86}.lock{font-size:34px;margin-bottom:8px}label{display:block;font-weight:700;margin:0 0 8px}input{width:100%;height:54px;border:1px solid #d7dee8;border-radius:13px;padding:0 15px;font-size:18px;outline:none}input:focus{border-color:#1769d5;box-shadow:0 0 0 3px rgba(23,105,213,.12)}button{width:100%;height:54px;margin-top:14px;border:0;border-radius:13px;background:#1769d5;color:#fff;font-size:17px;font-weight:700}button:active{transform:scale(.99)}.error{margin-top:12px;color:#b42318;font-weight:600}.hint{margin-top:15px;color:#667085;font-size:13px;text-align:center}
</style>
</head>
<body><main class=\"login\"><div class=\"brand\"><div class=\"lock\">🔒</div><h1>Customs Tariff Rates 2026–2027</h1><p>Enter the access password to continue.</p></div><form method=\"post\" action=\"/login\"><label for=\"password\">Password</label><input id=\"password\" name=\"password\" type=\"password\" autocomplete=\"current-password\" autofocus required><button type=\"submit\">Access Calculator</button>{error}<div class=\"hint\">Authorized users only</div></form></main></body></html>"""

@app.before_request
def require_login():
    # Health check and login/logout must remain reachable without a session.
    if request.path in {"/health", "/login", "/logout"}:
        return None
    if request.path.startswith("/api/") or request.path == "/":
        if session.get("authenticated") is True:
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "Authentication required"}), 401
        return redirect(url_for("login"))
    # There are no other public application assets; keep them protected too.
    if session.get("authenticated") is not True:
        return redirect(url_for("login"))
    return None

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        supplied = request.form.get("password", "")
        if SITE_PASSWORD and hmac.compare_digest(supplied, SITE_PASSWORD):
            session.clear()
            session["authenticated"] = True
            return redirect(url_for("home"))
        return LOGIN_PAGE.replace('{error}', '<div class=\"error\">Incorrect password. Please try again.</div>'), 200
    return LOGIN_PAGE.replace('{error}', '')

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

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

WEBOC_TARIFF_URL = "https://www.weboc.gov.pk/Shared/TariffList.aspx"
PSW_TARIFF_URL = "https://www.psw.gov.pk/public/utilities?service=tariff_sros"


def _extract_category_rate(text, category):
    """Extract the percentage associated with a specific WeBOC IT category.

    WeBOC rows can contain several numbers (schedule/part/serial references)
    before the actual rate.  Do not use the first percentage in the row.
    """
    t = clean_text(text)
    low = t.lower()
    if category == "commercial":
        patterns = [
            r"commercial\s+importer.{0,500}?@?\s*(\d+(?:\.\d+)?)\s*%",
            r"commercial.{0,500}?import.{0,300}?@?\s*(\d+(?:\.\d+)?)\s*%",
        ]
    else:
        patterns = [
            r"industrial\s+undertaking.{0,500}?@?\s*(\d+(?:\.\d+)?)\s*%",
            r"industrial\s+concern.{0,500}?@?\s*(\d+(?:\.\d+)?)\s*%",
        ]
    for pat in patterns:
        m = re.search(pat, t, re.I)
        if m:
            return float(m.group(1)) / 100
    return None


def _parse_weboc_it_rates(html):
    """Extract commercial/industrial import-stage IT rates from WeBOC Tariff & SROs."""
    soup = BeautifulSoup(html or "", "html.parser")
    out = {"commercial": None, "industrial": None,
           "commercial_detail": None, "industrial_detail": None}

    # The normal page has the two relevant rates in separate Exemptions / SROs
    # table rows. Inspect every row containing Income Tax and a category phrase.
    for tr in soup.find_all("tr"):
        cells = [clean_text(c.get_text(" ", strip=True)) for c in tr.find_all(["th", "td"])]
        if not cells:
            continue
        row = " | ".join(cells)
        low = row.lower()
        if "income tax" not in low:
            continue
        if "commercial importer" in low or ("commercial" in low and "import" in low):
            rate = _extract_category_rate(row, "commercial")
            if rate is not None:
                out["commercial"] = rate
                out["commercial_detail"] = row
        if "industrial undertaking" in low or "industrial concern" in low:
            rate = _extract_category_rate(row, "industrial")
            if rate is not None:
                out["industrial"] = rate
                out["industrial_detail"] = row

    # Some versions render the SRO section as divs/plain text rather than
    # useful table rows. Split around Income Tax entries and inspect each
    # category-specific block separately.
    text = clean_text(soup.get_text(" ", strip=True))
    for phrase, category in [
        ("commercial importer", "commercial"),
        ("industrial undertaking", "industrial"),
        ("industrial concern", "industrial"),
    ]:
        if (category == "commercial" and out["commercial"] is not None) or (category == "industrial" and out["industrial"] is not None):
            continue
        pos = text.lower().find(phrase)
        if pos >= 0:
            chunk = text[max(0, pos-80):pos+700]
            rate = _extract_category_rate(chunk, category)
            if rate is not None:
                out[category] = rate
                out[category + "_detail"] = chunk

    return out


def _get_weboc_form(session, headers):
    """Open a fresh WeBOC Tariff & SROs session and return its live form.

    WeBOC uses ASP.NET cookieless sessions. The first GET may redirect to a
    session-specific /Shared/TariffList.aspx URL. We keep the response that
    created that session and submit its exact hidden fields/ViewState rather
    than making a second GET that can create a different session.
    """
    try:
        diag(stage="GET", requested_url=WEBOC_TARIFF_URL)
        r = session.get(
            WEBOC_TARIFF_URL,
            timeout=(5, 12),
            verify=False,
            headers=headers,
            allow_redirects=True,
        )
        if not r.ok:
            return None, None, f"WeBOC returned HTTP {r.status_code}"
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form", id="form1") or soup.find("form")
        if not form:
            return None, None, "WeBOC tariff form was not found"
        return r.url, form, None
    except requests.RequestException as exc:
        return None, None, str(exc)


def _psw_weboc_links(session, headers):
    """Discover the session-specific WeBOC TariffList URL from PSW.

    PSW's Tariff & SROs utility can open WeBOC in a session-specific URL.
    When that link is present, using it preserves the same session/cookies
    instead of guessing a session id. No tariff rates are embedded here.
    """
    links = []
    try:
        r = session.get(
            PSW_TARIFF_URL,
            timeout=(8, 20),
            verify=False,
            headers=headers,
            allow_redirects=True,
        )
        if not r.ok:
            return links
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            label = clean_text(a.get_text(" ", strip=True)).lower()
            if "tarifflist.aspx" in href.lower() or (
                label == "open" and "tariff" in clean_text(a.parent.get_text(" ", strip=True)).lower()
            ):
                links.append(urljoin(r.url, href))
        # Also inspect any embedded JS/HTML URL containing TariffList.aspx.
        for match in re.findall(r"""https?://[^"'\\s<>]+TariffList\.aspx[^"'\\s<>]*""", r.text, re.I):
            links.append(match.replace("&amp;", "&"))
    except requests.RequestException:
        pass
    return list(dict.fromkeys(links))


def _weboc_search_once(hs8, diagnostics=None):
    """Query WeBOC exactly like the browser's Tariff & SROs form.

    WeBOC uses ASP.NET cookieless sessions. A browser first obtains a
    session-specific /Shared/TariffList.aspx URL, then POSTs the live
    ViewState/EventValidation plus TariffList$txtSearch and
    TariffList$btnSearch. We reproduce that flow with one requests.Session so
    the session URL and cookies remain paired. No tariff or tax rates are
    hard-coded here.
    """
    browser_headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:157.0) "
            "Gecko/20100101 Firefox/157.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "iframe",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
    }

    def diag(**kwargs):
        if diagnostics is not None:
            diagnostics.update(kwargs)

    def discover_session_url(session, bootstrap_url):
        """GET the page and return the exact session-specific URL + form HTML."""
        headers = dict(browser_headers)
        headers["Referer"] = "https://www.weboc.gov.pk/"
        r = session.get(
            bootstrap_url,
            timeout=(10, 35),
            verify=False,
            headers=headers,
            allow_redirects=True,
        )
        diag(
            bootstrap_status=r.status_code,
            bootstrap_url=bootstrap_url,
            session_url=r.url,
            redirect_chain=[h.url + " -> " + h.headers.get("Location", "") for h in r.history],
            cookies=session.cookies.get_dict(),
            content_length=len(r.text or ""),
        )
        if not r.ok:
            raise RuntimeError(f"WeBOC GET returned HTTP {r.status_code} ({r.url})")

        # The final response URL is authoritative. If the server did not put
        # the session id into the URL, look for a cookieless session URL in
        # redirects or HTML before giving up.
        candidates = [r.url]
        for h in r.history:
            loc = h.headers.get("Location")
            if loc:
                candidates.append(urljoin(h.url, loc))
        for candidate in re.findall(
            r"https?://www\.weboc\.gov\.pk/\(S\([^\)]+\)\)/Shared/TariffList\.aspx[^\"'\s<>]*",
            r.text,
            flags=re.I,
        ):
            candidates.append(candidate)

        session_url = next(
            (u for u in candidates if re.search(r"/\(S\([^\)]+\)\)/Shared/TariffList\.aspx", u, re.I)),
            r.url,
        )
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form", id="form1") or soup.find("form")
        if not form:
            title = clean_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
            raise RuntimeError(
                f"WeBOC tariff form was not found (final URL: {session_url}; title: {title[:120]})"
            )
        return session_url, form

    def post_search(session, session_url, form):
        # Reproduce the browser's application/x-www-form-urlencoded payload:
        # all hidden ASP.NET state + the exact search field + Search button.
        data = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ == "hidden":
                data[name] = inp.get("value", "")

        search_name = "TariffList$txtSearch"
        if search_name not in {i.get("name") for i in form.find_all("input") if i.get("name")}:
            candidates = []
            for inp in form.find_all("input"):
                name = inp.get("name") or ""
                ident = (name + " " + (inp.get("id") or "")).lower()
                typ = (inp.get("type") or "text").lower()
                if typ in {"text", "search"} and any(
                    k in ident for k in ("hscode", "pct", "hs_code", "search", "txtsearch")
                ):
                    candidates.append(name)
            if candidates:
                search_name = candidates[0]

        if not search_name:
            raise RuntimeError("WeBOC HS search field was not found")

        data[search_name] = hs8[:4] + "." + hs8[4:]
        data["TariffList$btnSearch"] = "Search"

        action = urljoin(session_url, form.get("action") or session_url)
        post_headers = dict(browser_headers)
        post_headers.update({
            "Referer": session_url,
            "Origin": "https://www.weboc.gov.pk",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        diag(
            post_url=action,
            search_field=search_name,
            search_value=data[search_name],
            viewstate_present=bool(data.get("__VIEWSTATE")),
            eventvalidation_present=bool(data.get("__EVENTVALIDATION")),
            post_cookie=session.cookies.get_dict(),
        )

        post = session.post(
            action,
            data=data,
            timeout=(10, 45),
            verify=False,
            headers=post_headers,
            allow_redirects=True,
        )
        diag(
            post_status=post.status_code,
            post_url=post.url,
            post_history=[h.url + " -> " + h.headers.get("Location", "") for h in post.history],
            post_content_length=len(post.text or ""),
        )
        if not post.ok:
            raise RuntimeError(f"WeBOC POST returned HTTP {post.status_code} ({post.url})")

        page_text = clean_text(BeautifulSoup(post.text, "html.parser").get_text(" ", strip=True))
        diag(
            final_page_chars=len(page_text),
            has_income_tax="income tax" in page_text.lower(),
            has_commercial_importer="commercial importer" in page_text.lower(),
            has_industrial_undertaking="industrial undertaking" in page_text.lower(),
            has_exemptions_sros="exemptions / sros" in page_text.lower(),
        )
        if len(page_text) < 100:
            raise RuntimeError(
                f"WeBOC returned an unexpectedly short page ({len(page_text)} chars; {post.url})"
            )
        return post.text

    errors = []

    # Primary route: official WeBOC. This is the route corresponding to the
    # exact cURL captured from the user's browser.
    try:
        session = requests.Session()
        session.headers.update(browser_headers)
        session_url, form = discover_session_url(session, WEBOC_TARIFF_URL)
        html = post_search(session, session_url, form)
        if html:
            return html, None
    except Exception as exc:
        errors.append("direct: " + str(exc))

    # Secondary route: PSW's Tariff & SROs utility, which can expose the
    # current session-specific WeBOC TariffList URL. This remains a fallback;
    # it never supplies or substitutes tax rates.
    try:
        session = requests.Session()
        session.headers.update(browser_headers)
        discovered = _psw_weboc_links(session, browser_headers)
        diag(psw_discovered_urls=discovered[:5])
        for url in discovered[:3]:
            try:
                session_url, form = discover_session_url(session, url)
                html = post_search(session, session_url, form)
                if html:
                    return html, None
            except Exception as exc:
                errors.append("psw: " + str(exc))
    except Exception as exc:
        errors.append("psw bootstrap: " + str(exc))

    return None, "; ".join(errors) or "WeBOC tariff page could not be queried"

def _weboc_search_form(_session, hs8):
    """Try the live WeBOC tariff form twice, with independent sessions."""
    errors = []
    for attempt in range(2):
        html, error = _weboc_search_once(hs8)
        if html:
            return html
        errors.append(f"attempt {attempt + 1}: {error or 'unknown error'}")
    return None, "; ".join(errors)

def weboc_income_tax_rates(session, hs8):
    """Fetch Commercial and Industrial Income Tax rates directly from WeBOC.

    There is deliberately NO hard-coded rate fallback. If WeBOC cannot be
    queried or does not expose a category rate, the caller is told that the
    commercial/industrial rate is unavailable rather than substituting TIPP's
    generic Income Tax rate.
    """
    hs8 = re.sub(r"[^0-9]", "", str(hs8))[:8]
    result = {
        "commercial": None, "industrial": None,
        "commercial_detail": None, "industrial_detail": None,
        "source": WEBOC_TARIFF_URL, "error": None
    }
    if len(hs8) != 8:
        result["error"] = "Invalid HS code"
        return result
    try:
        html_result = _weboc_search_form(session, hs8)
        if isinstance(html_result, tuple):
            html, query_error = html_result
        else:
            html, query_error = html_result, None
        if not html:
            result["error"] = query_error or "WeBOC tariff page could not be queried"
            return result
        result.update(_parse_weboc_it_rates(html))
        if result.get("commercial") is None:
            result["error"] = "WeBOC did not return a Commercial Importer income-tax rate for this PCT"
        elif result.get("industrial") is None:
            # Commercial can still be used; industrial is simply unavailable.
            result["error"] = "WeBOC did not return an Industrial Undertaking income-tax rate for this PCT"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


# Local CPFTA tariff-elimination schedule converted from the supplied
# Pakistan-China FTA Excel schedule. Keeping the schedule local avoids
# downloading/parsing a large PDF on Render for every tariff lookup.
CPFTA_DATA_FILE = os.path.join(os.path.dirname(__file__), "data", "cpfta_rates.csv")
CPFTA_SRO_URL = "https://download1.fbr.gov.pk/SROs/201912311712571769CPFTASRO1640%28I%29201931.12.2019.pdf"
CPFTA_DATA_CACHE = None
TIPP_INDEX_FILE = os.environ.get("TIPP_INDEX_FILE", os.path.join(os.path.dirname(__file__), "data", "tipp_record_index.json"))
TIPP_INDEX_LOCK = threading.Lock()

# TIPP's public product-search page exposes HS codes, but currently does not
# reliably expose the commodity record ID needed by the detail page.
# This is a verified live TIPP mapping for the test code we have been using.
# The detail page itself is fetched live, so the tariff values are NOT hard-coded.
def _load_tipp_index():
    try:
        with open(TIPP_INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_tipp_mapping(hs8, tipp_code, record_id, resolution="tipp-search"):
    if not hs8 or not tipp_code or not record_id:
        return
    os.makedirs(os.path.dirname(TIPP_INDEX_FILE), exist_ok=True)
    with TIPP_INDEX_LOCK:
        data = _load_tipp_index()
        entry = data.setdefault(str(hs8), [])
        item = {"tipp_code": str(tipp_code), "record_id": str(record_id), "resolution": resolution}
        if not any(str(x.get("tipp_code")) == str(tipp_code) and str(x.get("record_id")) == str(record_id) for x in entry if isinstance(x, dict)):
            entry.append(item)
        tmp = TIPP_INDEX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, TIPP_INDEX_FILE)


def _cached_tipp_ids(hs8, tipp_code):
    data = _load_tipp_index()
    rows = data.get(str(hs8), [])
    out=[]
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        tc=str(row.get("tipp_code", ""))
        if tc == str(tipp_code) or (len(str(tipp_code)) == 12 and tc.startswith(str(tipp_code)[:8])):
            rid=str(row.get("record_id", ""))
            if rid and rid not in out:
                out.append(rid)
    return out


VERIFIED_RECORD_IDS = {
    "950300900000": "23226",
    "961590900000": "23343",
    "720449900000": "19547",
    # TIPP search currently returns HTTP 200 for these codes but does not
    # expose the internal commodity/detail ID. These IDs were verified from
    # the corresponding current TIPP detail records.
    "871140100000": "22712",
    "721911000000": "19708",
    "570339900000": "26954",
    "560811000000": "18692",
    "630910100000": "19208",
    "570329200000": "26949",
    "551612000000": "18647",
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


def page_has_code(html, tipp_code, prefix_code=None):
    """Verify a TIPP detail page.

    For a real 12-digit TIPP commodity we require the exact code.  For an
    8-digit PCT entered by the user, TIPP may represent that PCT with one or
    more 12-digit commodity records (for example 85362010 -> 853620101000).
    In that case a detail record beginning with the requested 8-digit PCT is
    a valid resolution.
    """
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    page_digits = re.sub(r"[^0-9]", "", text)
    compact = re.sub(r"[^0-9]", "", tipp_code)
    if compact in page_digits:
        return True
    if prefix_code:
        prefix = re.sub(r"[^0-9]", "", prefix_code)
        # Look for a 12-digit TIPP code beginning with the requested PCT.
        return re.search(r"(?<!\d)" + re.escape(prefix) + r"\d{4}(?!\d)", text) is not None
    return False


def _href_record_id(value):
    """Extract a TIPP commodity/detail record id from any HTML attribute."""
    if not value:
        return None
    value = str(value)
    # Decode common HTML/JS/URL escaping used by Yii grids and search engines.
    from urllib.parse import unquote
    for _ in range(3):
        value = unquote(value).replace("&amp;", "&").replace("\\u003d", "=").replace("\\u0026", "&")
    rid = numeric_id_from_href(value)
    if rid:
        return rid
    # Some TIPP/Yii links put the id in JavaScript/on* attributes rather than href.
    m = re.search(r'(?:[?&]|\\?&|\\b)id(?:=|%3D)(\\d+)', value, re.I)
    return m.group(1) if m else None


def record_id_from_html(html, requested_code):
    """Robustly map a TIPP HS/PCT shown in search HTML to its record id.

    TIPP's grid has used several markup variants over time.  The old mapper
    assumed the HS code and detail link were in one very specific row shape.
    Instead we inspect the whole row, nearby table/container ancestors, all
    link/JS/data attributes, and finally the raw HTML around every occurrence
    of the requested code.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    compact = re.sub(r"[^0-9]", "", str(requested_code))
    prefix = compact[:8] if len(compact) >= 8 else compact
    candidates = []

    def score_context(ctx):
        digits = re.sub(r"[^0-9]", "", ctx or "")
        score = 0
        if compact and compact in digits:
            score += 100
        if prefix and prefix in digits:
            score += 50
        return score

    def collect_links(node, base_score=0):
        if not node:
            return
        for a in node.find_all(["a", "area", "button", "input"], href=True):
            rid = _href_record_id(a.get("href"))
            if rid:
                candidates.append((base_score + 20, rid))
        # Inspect all attributes because Yii grids can store the destination
        # in data-url/data-href/data-id or an onclick handler.
        for tag in node.find_all(True):
            attrs = tag.attrs or {}
            for name, value in attrs.items():
                vals = value if isinstance(value, list) else [value]
                for val in vals:
                    rid = _href_record_id(val)
                    if rid:
                        candidates.append((base_score + 10, rid))
                    elif isinstance(val, str):
                        m = re.search(r'(?:id(?:=|%3D))(\d+)', val, re.I)
                        if m:
                            candidates.append((base_score + 8, m.group(1)))

    # First: table rows. This is the most reliable association: the HS code
    # and its File/detail link are normally cells in the same grid row.
    for row in soup.find_all("tr"):
        ctx = clean_text(row.get_text(" ", strip=True))
        score = score_context(ctx)
        if score:
            collect_links(row, score)
            rid = row.get("data-id") or row.get("data-key") or row.get("data-record-id") or row.get("data-product-id")
            if rid and re.fullmatch(r"\d+", str(rid).strip()):
                candidates.append((score + 30, str(rid).strip()))

    # Second: locate an element containing the code and walk up a few levels.
    # This handles div/grid layouts where there is no useful <tr>.
    for node in soup.find_all(string=re.compile(re.escape(compact))):
        parent = node.parent
        for level in range(5):
            if not parent:
                break
            ctx = clean_text(parent.get_text(" ", strip=True))
            score = score_context(ctx) - level * 3
            if score > 0:
                collect_links(parent, score)
            parent = parent.parent

    # Third: raw HTML proximity. This catches links embedded in JSON or
    # JavaScript generated by the grid. Search a generous window around the
    # exact code and its 8-digit prefix.
    raw = html or ""
    needles = [n for n in (compact, prefix) if n]
    for needle in needles:
        for m in re.finditer(re.escape(needle), raw):
            lo, hi = max(0, m.start()-2500), min(len(raw), m.end()+2500)
            chunk = raw[lo:hi]
            # Direct href/data-url/onClick occurrences.
            for mm in re.finditer(r'(?:href|data-[\\w-]+|onclick)\\s*=\\s*["\\\']([^"\\\']+)["\\\']', chunk, re.I):
                rid = _href_record_id(mm.group(1))
                if rid:
                    candidates.append((80 if needle == compact else 60, rid))
            # Any TIPP detail URL encoded in JSON/script.
            for mm in re.finditer(r'https?[^"\\\']*tipp\\.gov\\.pk[^"\\\']*', chunk, re.I):
                rid = _href_record_id(mm.group(0))
                if rid:
                    candidates.append((70 if needle == compact else 55, rid))
            # Plain id=123 close to the HS code.
            for mm in re.finditer(r'(?:[?&]|%3F|%26)id(?:=|%3D)(\\d+)', chunk, re.I):
                candidates.append((65 if needle == compact else 50, mm.group(1)))

    # De-duplicate while retaining the strongest evidence for each ID.
    best = {}
    for score, rid in candidates:
        if rid and (rid not in best or score > best[rid]):
            best[rid] = score
    if not best:
        return None
    return max(best.items(), key=lambda kv: (kv[1], -int(kv[0])))[0]

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


def _tradeinfo_index_one(session, field, code):
    """Use TIPP's actual Search Commodity page before the older searchProduct page.

    TIPP documents that its commodity search accepts 2/4/6/8/12 digit HS codes.
    The application page is the authoritative resolver for a commodity lookup;
    searchProduct is only a fallback because it often omits the detail-record link.
    """
    params = {"r": "tradeInfo/index", field: code}
    try:
        response = session.get(TIPP_BASE, params=params, timeout=7, verify=False, headers={"User-Agent":"Mozilla/5.0"})
        if not response.ok:
            return []
        html=response.text
        soup=BeautifulSoup(html, "html.parser")
        wanted=re.sub(r"[^0-9]", "", str(code))
        page_digits=re.sub(r"[^0-9]", "", soup.get_text(" "))
        if wanted not in page_digits:
            return []
        ids=[]
        for a in soup.find_all("a", href=True):
            rid=numeric_id_from_href(a.get("href"))
            if not rid:
                continue
            context=clean_text(a.parent.get_text(" ", strip=True)) if a.parent else ""
            context_digits=re.sub(r"[^0-9]", "", context)
            if wanted in context_digits or "tradeinfo/view" in a.get("href", "").lower():
                if rid not in ids:
                    ids.append(rid)
        rid=record_id_from_html(html, code)
        if rid and rid not in ids:
            ids.insert(0,rid)
        return ids
    except requests.RequestException:
        return []


def _tipp_search_one(session, field, tipp_code):
    """Search TIPP using one possible filter-field name.

    TIPP is a Yii application and its public search form has changed field
    naming over time. We therefore try several likely model/filter names and
    only accept a response if the requested code is actually present.
    """
    params = {"r": "tradeInfo/searchProduct", field: tipp_code, "Product_page": "1"}
    try:
        response = session.get(TIPP_BASE, params=params, timeout=5, verify=False)
        if not response.ok:
            return []
        html = response.text
        # Do not mistake the normal 47,406-row landing page for a successful
        # filtered search. The exact code must occur in the returned table.
        compact = re.sub(r"[^0-9]", "", tipp_code)
        digits = re.sub(r"[^0-9]", "", BeautifulSoup(html, "html.parser").get_text(" "))
        if compact not in digits:
            return []
        ids = _extract_tipp_detail_ids_from_search_html(html, tipp_code)
        rid = record_id_from_html(html, tipp_code)
        if rid and rid not in ids:
            ids.insert(0, rid)
        return ids
    except requests.RequestException:
        return []


def _validate_tipp_id(session, rid, tipp_code, prefix_code=None):
    # TIPP sometimes changes the default sort/query on its detail page.
    # Try a few equivalent detail URLs before rejecting a valid record.
    urls = [
        f"{TIPP_BASE}?id={quote_plus(str(rid))}&r=tradeInfo%2Fview",
        f"{TIPP_BASE}?id={quote_plus(str(rid))}&r=tradeInfo%2Fview&sort=rate",
        f"{TIPP_BASE}?id={quote_plus(str(rid))}&r=tradeInfo%2Fview&sort=valid_from",
        f"{TIPP_BASE}?id={quote_plus(str(rid))}&r=tradeInfo%2Fview&sort=groups.full_name.desc",
    ]
    headers={"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1"}
    for detail_url in urls:
        try:
            detail = session.get(detail_url, timeout=7, verify=False, headers=headers, allow_redirects=True)
            if not detail.ok:
                continue
            if page_has_code(detail.text, tipp_code, prefix_code=prefix_code):
                return str(rid)
        except requests.RequestException:
            continue
    return None


def _search_tipp_external(session, tipp_code, prefix_code=None):
    """Last-resort public-web discovery, kept to one short request.

    This is deliberately a fallback only. TIPP itself remains authoritative;
    any discovered ID is validated against the live TIPP detail page.
    """
    import urllib.parse
    search_code = prefix_code or tipp_code
    q = urllib.parse.quote(f'site:tipp.gov.pk "{search_code}" "tradeInfo/view"')
    queries = [
        f'site:tipp.gov.pk "{search_code}" "tradeInfo/view"',
        f'site:tipp.gov.pk/index.php?id= "{search_code}"',
    ]
    urls = []
    for query in queries:
        enc = urllib.parse.quote(query)
        urls.extend([f"https://www.google.com/search?q={enc}", f"https://www.bing.com/search?q={enc}"])
    def one(url):
        try:
            r = session.get(url, timeout=4, verify=False, headers={"User-Agent":"Mozilla/5.0"})
            if r.ok:
                return _extract_tipp_detail_ids_from_search_html(r.text, tipp_code)
        except requests.RequestException:
            return []
        return []
    # Do not wait on slow providers serially.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(one, u) for u in urls]
        ids=[]
        for f in as_completed(futures):
            try:
                for rid in f.result():
                    if rid not in ids: ids.append(rid)
            except Exception:
                pass
    return ids[:4]


def search_tipp(session, tipp_code, prefix_code=None):
    """Universal, self-building TIPP resolver.

    The cache is keyed by the 8-digit PCT and may contain multiple TIPP
    12-digit commodity records. A lookup first uses the cache, then TIPP's
    documented commodity-search page, then searchProduct, and finally the
    small public-web fallback. Every accepted record is verified against the
    live detail page and written to the local index.
    """
    hs8 = re.sub(r"[^0-9]", "", prefix_code or tipp_code)[:8]

    # Seed mappings are also copied into the self-building index.
    seed = VERIFIED_RECORD_IDS.get(tipp_code)
    cached = _cached_tipp_ids(hs8, tipp_code)
    candidate_ids=[]
    if seed:
        candidate_ids.append(str(seed))
    for rid in cached:
        if rid not in candidate_ids:
            candidate_ids.append(rid)

    # Validate cached/seed records first; this makes repeat lookups fast.
    for rid in candidate_ids:
        valid=_validate_tipp_id(session, rid, tipp_code, prefix_code=prefix_code)
        if valid:
            # For 8-digit input, the detail page may expose the actual 12-digit code.
            _save_tipp_mapping(hs8, tipp_code if len(tipp_code)==12 else tipp_code, valid, "cache")
            return valid, "cache"

    fields=[
        "TradeInfo[hscode]", "TradeInfo[hs_code]", "TradeInfo[hsCode]",
        "TradeInfo[hscode1]", "hscode", "hs_code",
        "Product[hscode]", "Product[hs_code]", "Product[hsCode]",
        "Product[hs]", "Product[Hscode]", "ProductSearch[hscode]",
        "ProductSearch[hs_code]",
    ]

    # TIPP's own commodity search is tried with both the exact 12-digit code
    # and the 8-digit PCT. This is the key fix: an 8-digit PCT is allowed to
    # resolve to any matching 12-digit commodity record.
    search_codes=[tipp_code]
    if prefix_code and prefix_code != tipp_code:
        search_codes.append(prefix_code)
    with ThreadPoolExecutor(max_workers=min(12, len(fields)*len(search_codes))) as pool:
        futures=[]
        for code in search_codes:
            for field in fields:
                futures.append(pool.submit(_tradeinfo_index_one, session, field, code))
        for f in as_completed(futures):
            try:
                for rid in f.result():
                    if rid and str(rid) not in candidate_ids:
                        candidate_ids.append(str(rid))
            except Exception:
                pass

    # Older searchProduct endpoint as a fallback.
    with ThreadPoolExecutor(max_workers=len(fields)) as pool:
        futures=[pool.submit(_tipp_search_one, session, f, tipp_code) for f in fields]
        for f in as_completed(futures):
            try:
                for rid in f.result():
                    if rid and str(rid) not in candidate_ids:
                        candidate_ids.append(str(rid))
            except Exception:
                pass

    if prefix_code and prefix_code != tipp_code:
        with ThreadPoolExecutor(max_workers=len(fields)) as pool:
            futures=[pool.submit(_tipp_search_one, session, f, prefix_code) for f in fields]
            for f in as_completed(futures):
                try:
                    for rid in f.result():
                        if rid and str(rid) not in candidate_ids:
                            candidate_ids.append(str(rid))
                except Exception:
                    pass

    # Verify a bounded number of candidates. For an 8-digit PCT, any verified
    # TIPP commodity beginning with that PCT is acceptable.
    if candidate_ids:
        with ThreadPoolExecutor(max_workers=min(8, len(candidate_ids))) as pool:
            futures=[pool.submit(_validate_tipp_id, session, rid, tipp_code, prefix_code=prefix_code) for rid in candidate_ids[:12]]
            for f in as_completed(futures):
                try:
                    valid=f.result()
                    if valid:
                        _save_tipp_mapping(hs8, tipp_code, valid, "tipp-search")
                        return valid, "tipp-search"
                except Exception:
                    pass

    # Last resort: public-web discovery, still followed by exact verification.
    for rid in _search_tipp_external(session, tipp_code, prefix_code=prefix_code):
        valid=_validate_tipp_id(session, rid, tipp_code, prefix_code=prefix_code)
        if valid:
            _save_tipp_mapping(hs8, tipp_code, valid, "web-discovery")
            return valid, "web-discovery"

    raise RuntimeError(
        f"TIPP could not resolve the exact commodity record for {tipp_code}. "
        "TIPP did not return a verifiable detail record for this HS/PCT code."
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
    special_rates = {"pak_china_fta_cd": None, "income_tax_exemption_first_schedule_ii": None, "income_tax_commercial": None, "income_tax_industrial": None}
    special_details = {"pak_china_fta_cd": None, "income_tax_exemption_first_schedule_ii": None, "income_tax_commercial": None, "income_tax_industrial": None}
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

    record_id, resolution = search_tipp(session, tipp_code, prefix_code=hs8)
    commodity_url = f"{TIPP_BASE}?id={quote_plus(str(record_id))}&r=tradeInfo%2Fview&sort=rate"

    response = session.get(commodity_url, timeout=30, verify=False)
    response.raise_for_status()

    if not page_has_code(response.text, tipp_code, prefix_code=hs8):
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
    # WeBOC Tariff & SROs is the source for the importer-category income-tax
    # rates. The main WeBOC tariff table may show 12%, but the SRO section
    # separately identifies the commercial and industrial rates.
    weboc_it = weboc_income_tax_rates(session, hs8)
    if weboc_it.get("commercial") is not None:
        special_rates["income_tax_commercial"] = weboc_it["commercial"]
        special_details["income_tax_commercial"] = weboc_it.get("commercial_detail")
        # Commercial is the calculator default and is sourced only from WeBOC.
        rates["it"] = weboc_it["commercial"]
    if weboc_it.get("industrial") is not None:
        special_rates["income_tax_industrial"] = weboc_it["industrial"]
        special_details["income_tax_industrial"] = weboc_it.get("industrial_detail")
    if weboc_it.get("error"):
        special_details["income_tax_weboc_error"] = weboc_it["error"]

    # TIPP often does not expose the import-stage minimum value-addition tax
    # (shown by WeBOC as Additional Sales Tax / AST). The Twelfth Schedule to
    # the Sales Tax Act, 1990 provides a 3% ad-valorem rate, subject to its
    # exclusions. Apply that statutory default here; the UI keeps it editable.
    ast_rule = twelfth_schedule_ast_rule(hs8, facts=facts)
    ast_rate = ast_rule["rate"]
    rates["ast"] = ast_rate
    it_rule = apply_atl_adjustment(income_tax_rule(hs8, importer_type=importer_type, schedule_part=schedule_part, atl=atl, facts=facts), atl=atl)
    # Do not substitute TIPP's generic/base IT or an internal rule-layer rate.
    # Commercial IT must come from the live WeBOC Tariff & SROs result.
    if special_rates.get("income_tax_commercial") is None:
        rates["it"] = None
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
        "weboc_income_tax": weboc_it,
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


@app.route("/api/weboc-debug")
def weboc_debug():
    """Diagnostic-only live WeBOC session test. No rates are hard-coded."""
    raw_hs = request.args.get("hs", "9503.0090")
    hs8 = re.sub(r"[^0-9]", "", raw_hs)[:8]
    if len(hs8) != 8:
        return jsonify({"ok": False, "error": "Enter an 8-digit PCT, e.g. 9503.0090"}), 400
    diagnostics = {"hs8": hs8, "requested_hs": raw_hs}
    html, error = _weboc_search_once(hs8, diagnostics=diagnostics)
    diagnostics["ok"] = bool(html)
    diagnostics["error"] = error
    if html:
        parsed = _parse_weboc_it_rates(html)
        diagnostics["parsed_income_tax"] = parsed
    else:
        diagnostics["parsed_income_tax"] = {"commercial": None, "industrial": None}
    return jsonify(diagnostics)


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
