from flask import Flask, request, jsonify, send_from_directory
import re, requests
from bs4 import BeautifulSoup
from urllib.parse import quote

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

    s=requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"})
    r=s.get(TIPP_SEARCH,timeout=25)
    r.raise_for_status()
    soup=BeautifulSoup(r.text,'html.parser')
    form=None
    for f in soup.find_all('form'):
        text=f.get_text(" ",strip=True).lower()
        if 'hscode' in text or 'hs code' in text:
            form=f; break
    if not form:
        raise RuntimeError("Could not locate TIPP HS-code search form.")

    data={}
    for inp in form.find_all(['input','select','textarea']):
        name=inp.get('name')
        if not name: continue
        typ=(inp.get('type') or '').lower()
        if typ in ('submit','button','reset','image','file'): continue
        data[name]=inp.get('value','')
    target=None
    for inp in form.find_all('input'):
        ident=((inp.get('name') or '')+' '+(inp.get('id') or '')+' '+(inp.get('placeholder') or '')).lower()
        if 'hscode' in ident or 'hs code' in ident:
            target=inp.get('name'); break
    if not target:
        raise RuntimeError("Could not locate TIPP HS-code field.")
    data[target]=digits
    action=form.get('action') or TIPP_SEARCH
    if action.startswith('/'): action='https://www.tipp.gov.pk'+action
    method=(form.get('method') or 'get').lower()
    rr=s.post(action,data=data,timeout=25) if method=='post' else s.get(action,params=data,timeout=25)
    rr.raise_for_status()
    soup=BeautifulSoup(rr.text,'html.parser')

    # Find an exact 12-digit code link if search returns a result list.
    exact=digits.zfill(12) if len(digits)<12 else digits
    for a in soup.find_all('a',href=True):
        txt=a.get_text(" ",strip=True).replace(" ","")
        if txt==exact:
            href=a['href']
            if href.startswith('/'): href='https://www.tipp.gov.pk'+href
            rr=s.get(href,timeout=25); rr.raise_for_status()
            soup=BeautifulSoup(rr.text,'html.parser')
            break

    text=soup.get_text("\n",strip=True)
    rates={}
    labels={'CD':'cd','ACD':'acd','RD':'rd','ST (VAT)':'st','ST':'st','AST':'ast','IT':'it'}
    # Parse tariff table rows.
    for tr in soup.find_all('tr'):
        cells=[c.get_text(" ",strip=True) for c in tr.find_all(['td','th'])]
        if len(cells)>=4:
            duty=cells[0].strip().upper()
            for label,key in labels.items():
                if duty==label.upper():
                    rate=next((c for c in cells if '%' in c),None)
                    if rate is not None: rates[key]=pct(rate)

    if not rates:
        # Fallback line-based parsing for TIPP's rendered HTML.
        for label,key in labels.items():
            m=re.search(re.escape(label)+r'.{0,250}?(\d+(?:\.\d+)?)\s*%',text,re.I|re.S)
            if m: rates[key]=float(m.group(1))/100

    if not rates:
        raise RuntimeError("TIPP returned no tariff rows for this code.")

    # Try to identify code/description and validity from the page.
    code=exact
    m=re.search(r'Code\s+(\d{12})',text)
    if m: code=m.group(1)
    desc=''
    m=re.search(r'Sub Heading.*?\n.*?\n.*?\n(.+?)\nCode',text,re.S)
    if m: desc=m.group(1).strip()
    dates=re.findall(r'\d{2}-\d{2}-\d{4}',text)
    validity=(dates[0]+' to '+dates[-1]) if len(dates)>=2 else ''
    return {'code':code,'description':desc,'rates':rates,'validity':validity}

@app.get('/')
def home():
    return send_from_directory('.', 'index.html')

@app.get('/api/tariff')
def tariff():
    try:
        return jsonify(lookup(request.args.get('hs','')))
    except Exception as e:
        return jsonify({'error':str(e)}),400

if __name__=='__main__':
    app.run(host='0.0.0.0',port=5000)
