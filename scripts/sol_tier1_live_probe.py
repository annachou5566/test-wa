#!/usr/bin/env python3
"""One-shot ordinary-browser retry for SOEZ after direct HTML proved to be an empty app shell."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

OUT = Path('artifacts/sol-tier1-live')
OUT.mkdir(parents=True, exist_ok=True)
URL = 'https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/47315/SINGLCLASS/franklin-solana-etf/SOEZ'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MARKERS = ['Shares Outstanding', 'SOL in Fund', 'SOL per Basket', 'Updated Daily']


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def marker_report(text: str) -> dict[str, int]:
    low = text.lower()
    return {marker: low.count(marker.lower()) for marker in MARKERS}


def click_normal_gate(page) -> list[str]:
    clicked = []
    for pattern in [r'accept all|accept|agree', r'individual investor|retail investor|continue as.*investor']:
        rg = re.compile(pattern, re.I)
        for role in ('button', 'link'):
            try:
                locator = page.get_by_role(role, name=rg)
                if locator.count() and locator.first.is_visible(timeout=800):
                    locator.first.click(timeout=2500)
                    page.wait_for_timeout(600)
                    clicked.append(f'{role}:{pattern}')
                    break
            except Exception:
                pass
    return clicked


def main() -> None:
    browser_path = shutil.which('google-chrome') or shutil.which('google-chrome-stable') or shutil.which('chromium') or shutil.which('chromium-browser')
    if not browser_path:
        raise SystemExit('SYSTEM_CHROME_NOT_FOUND')
    started = now()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, executable_path=browser_path, args=['--no-sandbox'])
        context = browser.new_context(user_agent=UA, locale='en-US', viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        try:
            response = page.goto(URL, wait_until='domcontentloaded', timeout=30000)
            status = response.status if response else None
            try:
                page.wait_for_load_state('networkidle', timeout=8000)
            except PlaywrightTimeoutError:
                pass
            clicks = click_normal_gate(page)
            for _ in range(8):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(300)
            text = page.locator('body').inner_text(timeout=5000)
            body = text.encode('utf-8')
            if len(body) > MAX_CAPTURE_BYTES:
                raise RuntimeError(f'SOURCE_TOO_LARGE:{len(body)}')
            artifact_path = OUT / 'SOEZ-browser.txt'
            artifact_path.write_bytes(body)
            result = {
                'ticker': 'SOEZ',
                'source_url': URL,
                'final_url': page.url,
                'acquisition_method': 'ordinary_playwright_visible_text_single_navigation',
                'retrieved_at_utc': started,
                'http_status': status,
                'content_type': 'text/plain; charset=utf-8',
                'size_bytes': len(body),
                'sha256': digest(body),
                'artifact_path': str(artifact_path),
                'normal_gate_clicks': clicks,
                'markers': marker_report(text),
            }
        finally:
            context.close()
            browser.close()
    summary = {
        'probe': 'US_SOL_SOEZ_FINAL_ORDINARY_BROWSER_RETRY',
        'started_at_utc': started,
        'finished_at_utc': now(),
        'network_owner': 'public-first-party-issuer-only',
        'secrets_used': False,
        'wave_alpha_private_source_copied': False,
        'stealth_or_challenge_evasion_used': False,
        'waf_bypass_used': False,
        'one_navigation_per_fund': True,
        'system_browser': browser_path,
        'results': [result],
    }
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
