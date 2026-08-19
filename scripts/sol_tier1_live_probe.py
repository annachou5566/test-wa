#!/usr/bin/env python3
"""One-shot public-safe SOL issuer artifact probe; no Wave Alpha private source/secrets."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

OUT = Path('artifacts/sol-tier1-live')
OUT.mkdir(parents=True, exist_ok=True)
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
TIMEOUT = 30
MAX_CAPTURE_BYTES = 2 * 1024 * 1024

SOURCES = {
    'BSOL': {
        'url': 'https://bsoletf.com/',
        'markers': ['Shares Outstanding', 'Solana in Trust', 'Solana per Share', 'Fund Details'],
    },
    'SOEZ': {
        'url': 'https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/47315/SINGLCLASS/franklin-solana-etf/SOEZ',
        'markers': ['Shares Outstanding', 'SOL in Fund', 'SOL per Basket', 'Updated Daily'],
    },
    'SOLC': {
        'url': 'https://canaryetfs.com/solc/',
        'markers': ['SharesOutstanding', 'CreationUnits', 'SOLUSD', 'Holdings are subject to change'],
    },
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def marker_report(text: str, markers: list[str]) -> dict[str, int]:
    low = text.lower()
    return {marker: low.count(marker.lower()) for marker in markers}


def capture(ticker: str, spec: dict) -> dict:
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
    path = OUT / f'{ticker}.html'
    path.write_bytes(body)
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
        'artifact_path': str(path),
        'under_2mib': True,
        'markers': marker_report(text, spec['markers']),
    }


def main() -> None:
    summary = {
        'probe': 'US_SOL_TIER1_LIVE_ACQUISITION',
        'started_at_utc': now(),
        'network_owner': 'public-first-party-issuer-only',
        'secrets_used': False,
        'wave_alpha_private_source_copied': False,
        'stealth_or_challenge_evasion_used': False,
        'waf_bypass_used': False,
        'results': [],
    }
    for ticker, spec in SOURCES.items():
        try:
            summary['results'].append(capture(ticker, spec))
        except Exception as error:
            summary['results'].append({
                'ticker': ticker,
                'source_url': spec['url'],
                'acquisition_method': 'ordinary_requests_html',
                'retrieved_at_utc': now(),
                'error': f'{type(error).__name__}: {error}',
            })
    summary['finished_at_utc'] = now()
    summary['results'] = sorted(summary['results'], key=lambda row: row['ticker'])
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
