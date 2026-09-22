from flask import Flask, request, jsonify, send_from_directory
import re
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__, static_folder=".")

TIPP_BASE = "https://www.tipp.gov.pk/index.php"


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


def record_id_from_row(row):
    # Yii GridView commonly stores the model key here.
    for attribute in ("data-key", "data-id", "data-record-id", "data-product-id"):
        value = row.get(attribute)
        if value and re.fullmatch(r"\d+", str(value).strip()):
            return str(value).strip()

    # Fallback: a real numeric ID in a link.
    for link in row.find_all("a", href=True):
        record_id = numeric_id_from_href(link.get("href"))
        if record_id:
            return record_id

    return None


def find_record_id(html, requested_code):
    soup = BeautifulSoup(html, "html.parser")
    compact_code = re.sub(r"[^0-9]", "", requested_code)

    # First choice: identify the result row containing the requested code,
    # then read its Yii data-key.
    for row in soup.find_all("tr"):
        row_text = re.sub(r"[^0-9A-Za-z]", "", row.get_text(" ", strip=True))

        if compact_code in row_text or (
            len(compact_code) >= 8 and compact_code[:8] in row_text
        ):
            record_id = record_id_from_row(row)
            if record_id:
                return record_id

    # Second choice: valid numeric view link only.
    for link in soup.find_all("a", href=True):
        record_id = numeric_id_from_href(link.get("href"))
        if not record_id:
            continue

        link_text = re.sub(r"[^0-9A-Za-z]", "", link.get_text(" ", strip=True))
        if compact_code in link_text or (
            len(compact_code) >= 8 and compact_code[:8] in link_text
        ):
            return record_id

    return None


def search_tipp(session, tipp_code):
    # TIPP's public commodity page contains the actual search form. Instead of
    # guessing Yii parameter names, discover the HSCODE input and submit the
    # form exactly as the site defines it.
    index_url = "https://www.tipp.gov.pk/index.php?r=tradeInfo/index"

    try:
        landing = session.get(index_url, timeout=30, verify=False)
        landing.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not open TIPP search page: {exc}")

    soup = BeautifulSoup(landing.text, "html.parser")
    forms = soup.find_all("form")

    for form in forms:
        inputs = form.find_all(["input", "select", "textarea"])
        hscode_input = None

        for element in inputs:
            attrs = " ".join(
                str(element.get(attr, "")) for attr in
                ("name", "id", "placeholder", "title", "class")
            ).lower()
            if "hscode" in attrs or "hs code" in attrs or "hs_code" in attrs:
                hscode_input = element
                break

        if hscode_input is None:
            continue

        payload = {}

        for element in inputs:
            name = element.get("name")
            if not name:
                continue

            tag = element.name

            if tag == "select":
                option = element.find("option", selected=True) or element.find("option")
                if option:
                    payload[name] = option.get("value", "")
                continue

            element_type = (element.get("type") or "").lower()

            if element_type in ("submit", "button", "reset", "file"):
                continue

            if element_type in ("checkbox", "radio") and not element.has_attr("checked"):
                continue

            payload[name] = element.get("value", "")

        # Override only the discovered HS-code field.
        payload[hscode_input.get("name")] = tipp_code

        action = form.get("action") or index_url
        action = requests.compat.urljoin(index_url, action)
        method = (form.get("method") or "get").lower()

        try:
            if method == "post":
                result = session.post(
                    action, data=payload, timeout=30, verify=False
                )
            else:
                result = session.get(
                    action, params=payload, timeout=30, verify=False
                )

            if not result.ok:
                continue

            record_id = find_record_id(result.text, tipp_code)
            if record_id:
                return record_id

            # Some Yii pages return the commodity result as a link even when
            # the exact code is formatted differently.
            normalized_page = re.sub(r"[^0-9]", "", result.text)
            if tipp_code in normalized_page:
                record_id = find_record_id(result.text, tipp_code)
                if record_id:
                    return record_id

        except requests.RequestException:
            continue

    # Fallback: TIPP's search-product endpoint may expose the code even if the
    # landing-page form changes. We deliberately do not trust malformed id= links.
    raise RuntimeError(
        f"TIPP returned HTTP 200 but did not expose a valid commodity record ID "
        f"for {tipp_code}. The TIPP search form may have changed."
    )


def parse_tariff_page(html):
    soup = BeautifulSoup(html, "html.parser")

    rates = {
        "cd": 0.0,
        "acd": 0.0,
        "rd": 0.0,
        "st": 0.0,
        "ast": 0.0,
        "it": 0.0,
    }

    non_core = {
        "concessions": [],
        "exemptions": [],
        "provincial_cess": [],
    }

    for row in soup.find_all("tr"):
        cells = [
            re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).strip()
            for cell in row.find_all(["th", "td"])
        ]

        if not cells:
            continue

        text = " | ".join(cells)
        lower = text.lower()

        if "additional customs duty" in lower or re.search(r"\bacd\b", lower):
            rates["acd"] = parse_rate(text)

        elif "customs duty" in lower and "additional" not in lower:
            rates["cd"] = parse_rate(text)

        elif "regulatory duty" in lower or re.search(r"\brd\b", lower):
            rates["rd"] = parse_rate(text)

        elif "additional sales tax" in lower or re.search(r"\bast\b", lower):
            rates["ast"] = parse_rate(text)

        elif "sales tax" in lower and "additional" not in lower:
            rates["st"] = parse_rate(text)

        elif "income tax" in lower or re.search(r"\bit\b", lower):
            rates["it"] = parse_rate(text)

        # These are informational only. They are never added to rates.
        if "concession" in lower:
            non_core["concessions"].append(text)

        elif "exemption" in lower or "exempt" in lower:
            non_core["exemptions"].append(text)

        elif "provincial cess" in lower or (
            "cess" in lower and "provincial" in lower
        ):
            non_core["provincial_cess"].append(text)

    for key in non_core:
        seen = set()
        unique = []

        for item in non_core[key]:
            if item and item not in seen:
                seen.add(item)
                unique.append(item)

        non_core[key] = unique

    return rates, non_core


def lookup(hs):
    hs8, tipp_code = normalize_hs(hs)

    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
            )
        }
    )

    record_id = search_tipp(session, tipp_code)

    # IMPORTANT:
    # Build the commodity URL ourselves from the verified numeric ID.
    # We never follow a malformed URL ending in "id=".
    commodity_url = f"{TIPP_BASE}?r=tradeInfo/view&id={record_id}"

    response = session.get(
        commodity_url,
        timeout=30,
        verify=False,
    )
    response.raise_for_status()

    rates, non_core = parse_tariff_page(response.text)

    return {
        "hs_code": hs8,
        "tipp_code": tipp_code,
        "record_id": record_id,
        "rates": rates,
        "non_core": non_core,
        "source_url": commodity_url,
    }


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/tariff")
def tariff():
    hs = request.args.get("hs", "")

    try:
        return jsonify(lookup(hs))
    except Exception as error:
        return jsonify({"error": str(error)}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
