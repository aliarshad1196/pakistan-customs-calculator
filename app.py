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

def lookup(hs):
    digits=re.sub(r'[^0-9]','',hs)
    if len(digits) not in (2,4,6,8,12):
        raise ValueError("HS code must contain 2, 4, 6, 8 or 12 digits.")

    # TIPP's product search is server-rendered, but its search form has changed
    # over time. Instead of depending on a particular input name, try the
    # common Yii search parameter names and verify that the returned HTML
    # actually contains the requested HS code.
    s=requests.Session()
    s.headers.update({
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/140 Safari/537.36",
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    })

    base="https://www.tipp.gov.pk/index.php"
    search_url=base
    exact=digits.zfill(12) if len(digits)<12 else digits

    candidates=[
        {"Product[hscode]": exact},
        {"Product[hs_code]": exact},
        {"Product[hsCode]": exact},
        {"SearchProduct[hscode]": exact},
        {"SearchProduct[hs_code]": exact},
        {"hscode": exact},
        {"hs_code": exact},
        {"HSCode": exact},
    ]

    html=""
    for params in candidates:
        params["r"]="tradeInfo/searchProduct"
        try:
            rr=s.get(search_url,params=params,timeout=30,verify=False)
            if rr.ok and exact in rr.text.replace(" ",""):
                html=rr.text
                break
        except requests.RequestException:
            pass

    # If the exact 12-digit code isn't returned by the filtered search,
    # use the public product index as a fallback and scan its paginated
    # results. This is slower but avoids relying on a fragile form name.
    if not html:
        try:
            rr=s.get(search_url,params={"r":"tradeInfo/searchProduct"},timeout=30,verify=False)
            rr.raise_for_status()
            html=rr.text
        except requests.RequestException as e:
            raise RuntimeError(f"TIPP product search failed: {e}")

    soup=BeautifulSoup(html,"html.parser")

    # Find a link whose visible text is the exact HS code. If found, follow it.
    view_url=None
    for a in soup.find_all("a",href=True):
        txt=re.sub(r'\s+','',a.get_text(" ",strip=True))
        if txt==exact:
            view_url=a["href"]
            if view_url.startswith("/"):
                view_url="https://www.tipp.gov.pk"+view_url
            elif not view_url.startswith("http"):
                view_url="https://www.tipp.gov.pk/"+view_url.lstrip("/")
            break

    # Some TIPP tables expose the code in a row without making the code
    # itself the link. Look for any link in the same row.
    if not view_url:
        for tr in soup.find_all("tr"):
            rowtxt=re.sub(r'\s+','',tr.get_text(" ",strip=True))
            if exact in rowtxt:
                a=tr.find("a",href=True)
                if a:
                    view_url=a["href"]
                    if view_url.startswith("/"):
                        view_url="https://www.tipp.gov.pk"+view_url
                    elif not view_url.startswith("http"):
                        view_url="https://www.tipp.gov.pk/"+view_url.lstrip("/")
                    break

    # A direct TIPP commodity page is the authoritative source for the
    # tariff table. Follow the discovered link.
    if view_url:
        try:
            rr=s.get(view_url,timeout=30,verify=False)
            rr.raise_for_status()
            soup=BeautifulSoup(rr.text,"html.parser")
        except requests.RequestException as e:
            raise RuntimeError(f"TIPP commodity page could not be opened: {e}")
    else:
        # We must not guess a tariff if TIPP didn't identify the commodity.
        raise RuntimeError(
            f"TIPP did not return a direct commodity record for HS code {digits}. "
            "Try the full 12-digit TIPP code if available."
        )

    text=soup.get_text("\n",strip=True)
    rates={}
    labels={'CD':'cd','ACD':'acd','RD':'rd','ST (VAT)':'st','ST':'st','AST':'ast','IT':'it'}

    for tr in soup.find_all('tr'):
        cells=[c.get_text(" ",strip=True) for c in tr.find_all(['td','th'])]
        if len(cells)>=2:
            duty=cells[0].strip().upper()
            for label,key in labels.items():
                if duty==label.upper():
                    rate=next((c for c in cells if '%' in c),None)
                    if rate is not None:
                        rates[key]=pct(rate)

    if not rates:
        for label,key in labels.items():
            m=re.search(re.escape(label)+r'.{0,250}?(\d+(?:\.\d+)?)\s*%',text,re.I|re.S)
            if m:
                rates[key]=float(m.group(1))/100

    if not rates:
        raise RuntimeError("TIPP commodity record was found, but no tariff rates were parsed.")

    code=exact
    m=re.search(r'Code\s+(\d{12})',text)
    if m: code=m.group(1)

    desc=''
    m=re.search(r'Code\s+\d{12}\s+(.+?)\n\nTariffs',text,re.S)
    if m: desc=m.group(1).strip()

    dates=re.findall(r'\d{2}-\d{2}-\d{4}',text)
    validity=(dates[0]+' to '+dates[-1]) if len(dates)>=2 else ''

    return {'code':code,'description':desc,'rates':rates,'validity':validity}

@app.get('/')
def home():
    return send_from_directory('.', 'index.html')

@app.get('/health')
def health():
    return jsonify({'status':'ok'})

@app.get('/api/tariff')
def tariff():
    try:
        return jsonify(lookup(request.args.get('hs','')))
    except Exception as e:
        return jsonify({'error':str(e)}),400

if __name__=='__main__':
    app.run(host='0.0.0.0',port=5000)
