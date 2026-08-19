import json, os, hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

OUT=Path('artifacts/etfcom-obtc-defi-history'); OUT.mkdir(parents=True,exist_ok=True)
TARGETS={
 'OBTC':'https://www.etf.com/etfanalytics/etf-fund-flows-tool-result?endDate=2026-08-18&frequency=DAILY&startDate=2025-12-19&tickers=OBTC',
 'DEFI':'https://www.etf.com/etfanalytics/etf-fund-flows-tool-result?endDate=2026-08-17&frequency=DAILY&startDate=2024-03-27&tickers=DEFI',
}
MAX_BODY=1_000_000

def now(): return datetime.now(timezone.utc).isoformat()
def sha(b): return hashlib.sha256(b).hexdigest()

def main():
  chrome='/usr/bin/google-chrome'
  if not os.path.exists(chrome): raise SystemExit('SYSTEM_CHROME_NOT_FOUND')
  summary={'scope':'ETFCOM_OBTC_DEFI_DAILY_HISTORY','transport':'ordinary-system-chrome','proxy_used':False,'stealth_used':False,'challenge_bypass_used':False,'secrets_used':False,'started_at_utc':now(),'targets':{}}
  with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path=chrome)
    for ticker,url in TARGETS.items():
      rec={'url':url,'http':None,'title':None,'error':None,'text_sha256':None,'first_party_data_responses':[]}
      ctx=browser.new_context(locale='en-US'); page=ctx.new_page(); seen=[]
      def on_response(resp):
        try:
          u=urlparse(resp.url)
          if u.hostname and (u.hostname=='www.etf.com' or u.hostname.endswith('.etf.com')) and resp.request.resource_type in ('xhr','fetch'):
            ct=(resp.headers.get('content-type') or '').lower()
            if any(x in ct for x in ('json','text','javascript')):
              body=resp.body()
              if len(body)<=MAX_BODY:
                seen.append({'url':resp.url,'status':resp.status,'content_type':ct,'sha256':sha(body),'body':body.decode('utf-8','replace')[:MAX_BODY]})
        except Exception: pass
      page.on('response',on_response)
      try:
        nav=page.goto(url,wait_until='domcontentloaded',timeout=35000); rec['http']=nav.status if nav else None
        page.wait_for_timeout(7000)
        text=page.locator('body').inner_text(timeout=7000).replace('\xa0',' ')
        b=text.encode(); rec['title']=page.title(); rec['text_sha256']=sha(b); rec['text_chars']=len(text)
        rec['contains_ticker']=ticker in text; rec['contains_daily']='DAILY' in text.upper() or 'Daily' in text
        rec['contains_flow']='flow' in text.lower(); rec['contains_start_date']=TARGETS[ticker].split('startDate=')[1].split('&')[0] in text
        rec['contains_end_date']=TARGETS[ticker].split('endDate=')[1].split('&')[0] in text
        (OUT/f'{ticker}-page-text.txt').write_text(text[:800000],encoding='utf-8')
        (OUT/f'{ticker}-page.html').write_text(page.content()[:1500000],encoding='utf-8')
        for i,item in enumerate(seen[:30]):
          body=item.pop('body'); (OUT/f'{ticker}-response-{i:02d}.txt').write_text(body,encoding='utf-8')
          rec['first_party_data_responses'].append(item)
      except Exception as e: rec['error']=f'{type(e).__name__}:{e}'
      ctx.close(); summary['targets'][ticker]=rec
    browser.close()
  summary['finished_at_utc']=now(); (OUT/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
  for t,r in summary['targets'].items():
    print(f"{t}_HTTP={r['http']} TITLE={r['title']} TEXT_CHARS={r.get('text_chars')} DATA_RESPONSES={len(r['first_party_data_responses'])} ERROR={r['error']}")

if __name__=='__main__': main()
