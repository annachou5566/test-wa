import hashlib
import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

URL = 'https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/39639/SINGLCLASS/franklin-bitcoin-etf/EZBC'
OUT = Path('artifacts/ezbc-current-state')
OUT.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 2 * 1024 * 1024


def click_if_visible(page, text):
    locator = page.get_by_text(text, exact=False).first
    try:
        if locator.is_visible(timeout=1200):
            locator.click(timeout=2500)
            page.wait_for_timeout(700)
            return True
    except Exception:
        return False
    return False


def main():
    chrome = '/usr/bin/google-chrome'
    if not os.path.exists(chrome):
        raise SystemExit('SYSTEM_CHROME_NOT_FOUND')
    meta = {
        'scope': 'EZBC_CURRENT_FIRST_PARTY_ONE_SHOT',
        'source_url': URL,
        'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
        'transport': 'ordinary-system-chrome-direct-first-party',
        'navigation_count': 1,
        'clicked_visible_controls': [],
        'proxy_used': False,
        'stealth_used': False,
        'challenge_bypass_used': False,
        'secrets_used': False,
        'private_wave_alpha_source_copied': False,
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome)
        context = browser.new_context(locale='en-US')
        page = context.new_page()
        try:
            response = page.goto(URL, wait_until='domcontentloaded', timeout=30000)
            meta['http_status'] = response.status if response else None
            page.wait_for_timeout(1500)
            for text in ['Individual Investor', 'Continue']:
                if click_if_visible(page, text):
                    meta['clicked_visible_controls'].append(text)
            for _ in range(8):
                page.evaluate('window.scrollBy(0, 700)')
                page.wait_for_timeout(350)
            page.evaluate('window.scrollTo(0, 0)')
            page.wait_for_timeout(3000)
            text = re.sub(r'[ \t]+', ' ', page.locator('body').inner_text(timeout=5000).replace('\xa0', ' ')).strip()
            data = text.encode('utf-8')
            if len(data) > MAX_BYTES:
                raise RuntimeError(f'SOURCE_TOO_LARGE:{len(data)}')
            (OUT / 'EZBC.txt').write_bytes(data)
            meta['final_url'] = page.url
            meta['title'] = page.title()
            meta['size_bytes'] = len(data)
            meta['sha256'] = hashlib.sha256(data).hexdigest()
            markers = ['Bitcoin in Fund', 'Bitcoin per Basket', 'Shares Outstanding', 'Total Net Assets', 'NAV', 'As of']
            meta['marker_counts'] = {m: text.lower().count(m.lower()) for m in markers}
            challenge_terms = ['failed to verify your browser','security checkpoint','sorry, you have been blocked','access denied','captcha','website temporarily unavailable']
            meta['challenge_markers'] = {m: text.lower().count(m) for m in challenge_terms}
            meta['error'] = None
        except Exception as exc:
            meta['error'] = f'{type(exc).__name__}:{exc}'
        finally:
            context.close()
            browser.close()
    (OUT / 'summary.json').write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(meta, sort_keys=True))


if __name__ == '__main__':
    main()
