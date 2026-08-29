#!/usr/bin/env python3
"""One-shot public-safe BHYP issuer artifact probe; no Wave Alpha private source/secrets."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import requests

OUT = Path('artifacts/hyp-tier1-live')
OUT.mkdir(parents=True, exist_ok=True)
URL = 'https://www.bhypetf.com/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36'
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MARKERS = ['Shares Outstanding', 'Hyperliquid in Trust', 'Hyperliquid per Share', 'Fund Details']


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    started = now()
    response = requests.get(
        URL,
        headers={
            'User-Agent': UA,
            'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        timeout=30,
        allow_redirects=True,
    )
    body = response.content
    if len(body) > MAX_CAPTURE_BYTES:
        raise SystemExit(f'SOURCE_TOO_LARGE:{len(body)}')
    text = body.decode('utf-8', 'replace')
    artifact = OUT / 'BHYP.html'
    artifact.write_bytes(body)
    low = text.lower()
    result = {
        'ticker': 'BHYP',
        'source_url': URL,
        'final_url': response.url,
        'acquisition_method': 'ordinary_requests_html',
        'retrieved_at_utc': started,
        'http_status': response.status_code,
        'content_type': response.headers.get('content-type'),
        'size_bytes': len(body),
        'sha256': digest(body),
        'artifact_path': str(artifact),
        'under_2mib': True,
        'markers': {marker: low.count(marker.lower()) for marker in MARKERS},
    }
    summary = {
        'probe': 'US_HYP_TIER1_LIVE_ACQUISITION',
        'started_at_utc': started,
        'finished_at_utc': now(),
        'network_owner': 'public-first-party-issuer-only',
        'secrets_used': False,
        'wave_alpha_private_source_copied': False,
        'stealth_or_challenge_evasion_used': False,
        'waf_bypass_used': False,
        'results': [result],
    }
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8')
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
