#!/usr/bin/env python3
"""One-shot public-safe XRP issuer artifact probe; no Wave Alpha private source/secrets."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

OUT = Path('artifacts/xrp-tier1-live')
OUT.mkdir(parents=True, exist_ok=True)
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
TIMEOUT = 30
MAX_CAPTURE_BYTES = 2 * 1024 * 1024

SOURCES = {
    'XRPC': {
        'url': 'https://canaryetfs.com/xrpc/',
        'method': 'requests',
        'suffix': '.html',
        'markers': ['Holdings are subject to change', 'XRPUSD', 'SharesOutstanding', 'CreationUnits'],
    },
    'XRP': {
        'url': 'https://bitxrpetf.com/',
        'method': 'requests',
        'suffix': '.html',
        'markers': ['Shares Outstanding', 'XRP in Trust', 'XRP per Share', 'Fund Details'],
    },
    'XRPZ': {
        'url': 'https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/47318/SINGLCLASS/franklin-xrp-etf/XRPZ',
        'method': 'browser_text',
        'suffix': '.txt',
        'markers': ['Shares Outstanding', 'XRP in Fund', 'XRP per Basket', 'Updated Daily'],
    },
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def marker_report(text: str, markers: list[str]) -> dict[str, int]:
    low = text.lower()
    return {marker: low.count(marker.lower()) for marker in markers}


def save(ticker: str, suffix: str, data: bytes) -> str:
    path = OUT / f'{ticker}{suffix}'
    path.write_bytes(data)
    return str(path)


def request_capture(ticker: str, spec: dict) -> dict:
    started = now()
    response = requests.get(
        spec['url'],
        headers={
            'User-Agent': UA,
            'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        timeout=TIMEOUT,
        allow_redirects=True,
    )
    body = response.content
    if len(body) > MAX_CAPTURE_BYTES:
        raise RuntimeError(f'SOURCE_TOO_LARGE:{len(body)}')
    text = body.decode('utf-8', 'replace')
    return {
        'ticker': ticker,
        'source_url': spec['url'],
        'final_url': response.url,
        'acquisition_method': 'ordinary_requests_html',
        'retrieved_at_utc': started,
        'http_status': response.status_code,
        'content_type': response.headers.get('content-type'),
        'size_bytes': len(body),
        'sha256': digest(body),
        'artifact_path': save(ticker, spec['suffix'], body),
        'under_2mib': True,
        'markers': marker_report(text, spec['markers']),
    }


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


def browser_capture(browser, ticker: str, spec: dict) -> dict:
    context = browser.new_context(user_agent=UA, locale='en-US', viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    started = now()
    try:
        response = page.goto(spec['url'], wait_until='domcontentloaded', timeout=TIMEOUT * 1000)
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
        return {
            'ticker': ticker,
            'source_url': spec['url'],
            'final_url': page.url,
            'acquisition_method': 'ordinary_playwright_visible_text_single_navigation',
            'retrieved_at_utc': started,
            'http_status': status,
            'content_type': 'text/plain; charset=utf-8',
            'size_bytes': len(body),
            'sha256': digest(body),
            'artifact_path': save(ticker, spec['suffix'], body),
            'under_2mib': True,
            'normal_gate_clicks': clicks,
            'markers': marker_report(text, spec['markers']),
        }
    finally:
        context.close()


def main() -> None:
    summary = {
        'probe': 'US_XRP_TIER1_LIVE_ACQUISITION',
        'started_at_utc': now(),
        'network_owner': 'public-first-party-issuer-only',
        'secrets_used': False,
        'wave_alpha_private_source_copied': False,
        'stealth_or_challenge_evasion_used': False,
        'waf_bypass_used': False,
        'one_navigation_per_browser_fund': True,
        'results': [],
    }

    for ticker, spec in SOURCES.items():
        if spec['method'] != 'requests':
            continue
        try:
            summary['results'].append(request_capture(ticker, spec))
        except Exception as error:
            summary['results'].append({
                'ticker': ticker,
                'source_url': spec['url'],
                'acquisition_method': 'ordinary_requests_html',
                'retrieved_at_utc': now(),
                'error': f'{type(error).__name__}: {error}',
            })

    browser_specs = [(ticker, spec) for ticker, spec in SOURCES.items() if spec['method'] == 'browser_text']
    browser_path = shutil.which('google-chrome') or shutil.which('google-chrome-stable') or shutil.which('chromium') or shutil.which('chromium-browser')
    summary['system_browser'] = browser_path
    if browser_specs and browser_path:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=browser_path, args=['--no-sandbox'])
            try:
                for ticker, spec in browser_specs:
                    try:
                        summary['results'].append(browser_capture(browser, ticker, spec))
                    except Exception as error:
                        summary['results'].append({
                            'ticker': ticker,
                            'source_url': spec['url'],
                            'acquisition_method': 'ordinary_playwright_visible_text_single_navigation',
                            'retrieved_at_utc': now(),
                            'error': f'{type(error).__name__}: {error}',
                        })
            finally:
                browser.close()
    elif browser_specs:
        for ticker, spec in browser_specs:
            summary['results'].append({
                'ticker': ticker,
                'source_url': spec['url'],
                'acquisition_method': 'ordinary_playwright_visible_text_single_navigation',
                'retrieved_at_utc': now(),
                'error': 'SYSTEM_CHROME_NOT_FOUND',
            })

    summary['finished_at_utc'] = now()
    summary['results'] = sorted(summary['results'], key=lambda row: row['ticker'])
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
