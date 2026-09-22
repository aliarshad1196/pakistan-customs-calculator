<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pakistan Customs Duty Calculator</title>
<style>
*{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif;background:#f4f7fb;color:#172033}
.wrap{max-width:620px;margin:auto;padding:18px}
.card{background:#fff;border-radius:18px;padding:20px;margin-bottom:14px;box-shadow:0 5px 20px #00000010}
h1{font-size:24px;margin:0 0 5px}.sub{color:#667085;font-size:13px;margin-bottom:18px}
label{display:block;font-weight:700;font-size:13px;margin:12px 0 6px}
input{width:100%;padding:13px;border:1px solid #d0d5dd;border-radius:10px;font-size:16px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
button{width:100%;border:0;border-radius:11px;padding:14px;font-size:16px;font-weight:700;cursor:pointer;background:#175cd3;color:white;margin-top:12px}
button.secondary{background:#eef4ff;color:#175cd3}
.status{font-size:13px;margin-top:10px;color:#667085}
.rates{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:12px}
.rate{background:#f8fafc;padding:11px;border-radius:10px}.rate b{display:block;font-size:12px;color:#667085}.rate span{font-size:18px;font-weight:700}
.result{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #eee}.total{font-size:19px;font-weight:800}
small{color:#667085}
@media(max-width:430px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
<div class="card">
<h1>Pakistan Customs Duty Calculator</h1>
<div class="sub">Online TIPP tariff lookup • FY 2026–27</div>
<label>HS / PCT Code</label>
<input id="hs" inputmode="numeric" placeholder="e.g. 9503.0090">
<button onclick="fetchTariff()">Fetch TIPP Rates</button>
<div id="status" class="status"></div>
<div id="commodity"></div>
<div id="rates" class="rates"></div>
</div>

<div class="card">
<h2>Import Details</h2>
<div class="grid">
<div><label>Quantity</label><input id="qty" type="number" step="any" value="1" oninput="calc()"></div>
<div><label>Unit Price</label><input id="price" type="number" step="any" value="0" oninput="calc()"></div>
<div><label>Exchange Rate</label><input id="fx" type="number" step="any" value="280" oninput="calc()"></div>
</div>
<button class="secondary" onclick="calc()">Calculate</button>
</div>

<div class="card">
<h2>Duty & Tax Breakdown</h2>
<div id="breakdown"><small>Enter values and fetch tariff rates.</small></div>
</div>
</div>

<script>
const rates={cd:0,acd:0,rd:0,st:0,ast:0,it:0};
const money=n=>Number(n||0).toLocaleString('en-PK',{maximumFractionDigits:2});
function pct(x){return (Number(x||0)*100).toFixed(2)+'%'}
function cleanHS(v){return String(v).replace(/[^0-9]/g,'')}
async function fetchTariff(){
 const hs=cleanHS(document.getElementById('hs').value);
 const status=document.getElementById('status');
 if(!hs){status.textContent='Enter an HS/PCT code.';return}
 status.textContent='Fetching current TIPP tariff…';
 try{
   const r=await fetch('/api/tariff?hs='+encodeURIComponent(hs));
   const d=await r.json();
   if(!r.ok) throw new Error(d.error||'Lookup failed');
   rates.cd=Number(d.rates.cd||0); rates.acd=Number(d.rates.acd||0);
   rates.rd=Number(d.rates.rd||0); rates.st=Number(d.rates.st||0);
   rates.ast=Number(d.rates.ast||0); rates.it=Number(d.rates.it||0);
   document.getElementById('commodity').innerHTML='<p><b>'+ (d.code||hs) +'</b> — '+(d.description||'')+'</p>';
   document.getElementById('rates').innerHTML=
    [['CD',rates.cd],['ACD',rates.acd],['RD',rates.rd],['ST',rates.st],['AST',rates.ast],['IT',rates.it]]
    .map(x=>'<div class="rate"><b>'+x[0]+'</b><span>'+pct(x[1])+'</span></div>').join('');
   status.textContent='TIPP tariff loaded. Validity: '+(d.validity||'see TIPP record');
   calc();
 }catch(e){status.textContent='Error: '+e.message}
}
function calc(){
 const q=Number(document.getElementById('qty').value||0);
 const p=Number(document.getElementById('price').value||0);
 const fx=Number(document.getElementById('fx').value||0);
 const cv=q*p*fx;
 const cd=cv*rates.cd, acd=cv*rates.acd, rd=cv*rates.rd;
 const st=(cv+cd+rd)*rates.st;
 const ast=(cv+cd+rd)*rates.ast;
 const it=(cv+cd+rd+acd+ast+st)*rates.it;
 const total=cd+acd+rd+st+ast+it;
 const landed=cv+total;
 const rows=[['Customs Value (CV)',cv],['CD',cd],['ACD',acd],['RD',rd],['ST',st],['AST',ast],['IT',it],['Total Duties & Taxes',total],['Total Landed Value',landed]];
 document.getElementById('breakdown').innerHTML=rows.map((r,i)=>'<div class="result '+(i>=7?'total':'')+'"><span>'+r[0]+'</span><b>'+money(r[1])+'</b></div>').join('');
}
</script>

<div id="nonCore" style="margin-top:16px;display:none;border:1px solid #ddd;border-radius:12px;padding:12px;background:#fafafa">
<b>Additional TIPP information</b>
<div style="font-size:13px;margin-top:6px;color:#555">Concessions/exemptions and provincial cess are informational only and <b>not automatically included</b> in CD/ACD/RD/ST/AST/IT.</div>
<div id="nonCoreBody" style="margin-top:8px"></div>
</div>
</body>
</html>
