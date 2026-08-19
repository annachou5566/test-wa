#!/usr/bin/env python3
"""One-shot public-safe Tier 1 issuer artifact probe; no Wave Alpha private source/secrets."""
from __future__ import annotations
import hashlib, json, re, shutil
from datetime import datetime, timezone
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

OUT=Path('artifacts/etf-tier1-live'); OUT.mkdir(parents=True, exist_ok=True)
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
TIMEOUT=30; MAX_CAPTURE_BYTES=2*1024*1024
SOURCES={
 'IBIT': {'url':'https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf/latest-holdings.csv','method':'requests','suffix':'.csv','markers':['Shares Outstanding','Ticker,Name','BTC']},
 'ARKB': {'url':'https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_21SHARES_BITCOIN_ETF_ARKB_HOLDINGS.csv','method':'requests','suffix':'.csv','markers':['date,fund,company,ticker,cusip,shares','ARKB','BITCOIN']},
 'BITB': {'url':'https://bitbetf.com/','method':'requests','suffix':'.html','markers':['Shares Outstanding','Net Assets','Bitcoin']},
 'HODL': {'url':'https://www.vaneck.com/us/en/investments/bitcoin-etf-hodl/','method':'browser_text','suffix':'.txt','markers':['Shares Outstanding','ETF Statistics','Bitcoin']},
 'EZBC': {'url':'https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/39639/SINGLCLASS/franklin-bitcoin-etf/EZBC','method':'browser_text','suffix':'.txt','markers':['Shares Outstanding','Total Net Assets','Bitcoin']},
 'BTCW': {'url':'https://www.wisdomtree.com/us/products/crypto/btcw','method':'requests','suffix':'.html','markers':['Shares Outstanding','NAV','BTCW']},
 'OBTC': {'url':'https://www.rexshares.com/obtc/','method':'requests','suffix':'.html','markers':['Shares Outstanding','Bitcoin','OBTC']},
}
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
def digest(b): return hashlib.sha256(b).hexdigest()
def marker_report(text, markers):
 low=text.lower(); return {m: low.count(m.lower()) for m in markers}
def save(ticker,suffix,data):
 p=OUT/f'{ticker}{suffix}'; p.write_bytes(data); return str(p)
def request_capture(ticker,spec):
 started=now(); r=requests.get(spec['url'],headers={'User-Agent':UA,'Accept':'text/html,application/xhtml+xml,text/csv,text/plain;q=0.9,*/*;q=0.8','Accept-Language':'en-US,en;q=0.9'},timeout=TIMEOUT,allow_redirects=True)
 b=r.content; text=b.decode('utf-8','replace')
 return {'ticker':ticker,'source_url':spec['url'],'final_url':r.url,'acquisition_method':'requests','retrieved_at_utc':started,'http_status':r.status_code,'content_type':r.headers.get('content-type'),'size_bytes':len(b),'sha256':digest(b),'artifact_path':save(ticker,spec['suffix'],b),'under_2mib':len(b)<=MAX_CAPTURE_BYTES,'markers':marker_report(text,spec['markers'])}
def click_if_visible(page,pattern):
 rg=re.compile(pattern,re.I)
 for role in ('button','link'):
  try:
   loc=page.get_by_role(role,name=rg)
   if loc.count() and loc.first.is_visible(timeout=1000): loc.first.click(timeout=3000); page.wait_for_timeout(700); return True
  except Exception: pass
 return False
def browser_capture(browser,ticker,spec):
 ctx=browser.new_context(user_agent=UA,locale='en-US',viewport={'width':1440,'height':1000}); page=ctx.new_page(); started=now()
 try:
  response=page.goto(spec['url'],wait_until='domcontentloaded',timeout=TIMEOUT*1000); status=response.status if response else None
  try: page.wait_for_load_state('networkidle',timeout=8000)
  except PlaywrightTimeoutError: pass
  click_if_visible(page,r'accept|accept all|agree'); gate=click_if_visible(page,r'individual investor|retail investor|continue as.*investor')
  for _ in range(8): page.mouse.wheel(0,1800); page.wait_for_timeout(350)
  text=page.locator('body').inner_text(timeout=5000); b=text.encode('utf-8')
  return {'ticker':ticker,'source_url':spec['url'],'final_url':page.url,'acquisition_method':'plain_playwright_visible_text','retrieved_at_utc':started,'http_status':status,'content_type':'text/plain; charset=utf-8','size_bytes':len(b),'sha256':digest(b),'artifact_path':save(ticker,spec['suffix'],b),'under_2mib':len(b)<=MAX_CAPTURE_BYTES,'role_gate_clicked':gate,'markers':marker_report(text,spec['markers'])}
 finally: ctx.close()
def main():
 summary={'probe':'US_BTC_TIER1_LIVE_ACQUISITION','started_at_utc':now(),'network_owner':'public-first-party-issuer-only','secrets_used':False,'wave_alpha_private_source_copied':False,'results':[]}
 for ticker,spec in SOURCES.items():
  if spec['method']!='requests': continue
  try: summary['results'].append(request_capture(ticker,spec))
  except Exception as e: summary['results'].append({'ticker':ticker,'source_url':spec['url'],'acquisition_method':'requests','retrieved_at_utc':now(),'error':f'{type(e).__name__}: {e}'})
 browser_specs=[(t,s) for t,s in SOURCES.items() if s['method']=='browser_text']
 if browser_specs:
  browser_path=shutil.which('google-chrome') or shutil.which('google-chrome-stable') or shutil.which('chromium') or shutil.which('chromium-browser')
  if not browser_path:
   for ticker,spec in browser_specs: summary['results'].append({'ticker':ticker,'source_url':spec['url'],'acquisition_method':'plain_playwright_visible_text','retrieved_at_utc':now(),'error':'SYSTEM_CHROME_NOT_FOUND'})
  else:
   summary['system_browser']=browser_path
   with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=browser_path,args=['--no-sandbox'])
    try:
     for ticker,spec in browser_specs:
      try: summary['results'].append(browser_capture(browser,ticker,spec))
      except Exception as e: summary['results'].append({'ticker':ticker,'source_url':spec['url'],'acquisition_method':'plain_playwright_visible_text','retrieved_at_utc':now(),'error':f'{type(e).__name__}: {e}'})
    finally: browser.close()
 summary['finished_at_utc']=now(); summary['results']=sorted(summary['results'],key=lambda r:r['ticker']); (OUT/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True),encoding='utf-8'); print(json.dumps(summary,indent=2,sort_keys=True))
if __name__=='__main__': main()
