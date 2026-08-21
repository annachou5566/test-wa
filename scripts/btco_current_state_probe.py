#!/usr/bin/env python3
import hashlib
import json
import pathlib
import urllib.parse
import urllib.request
from datetime import datetime, timezone

URL = 'https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46091J101/prices?idType=cusip&variationType=priceListing&productType=ETF&productSubType=ETF-Non-40%20Act'
EXPECTED_HOST = 'dng-api.invesco.com'
EXPECTED_CUSIP = '46091J101'
OUT = pathlib.Path('artifacts/btco-current-state')

parts = urllib.parse.urlsplit(URL)
if parts.scheme != 'https' or parts.hostname != EXPECTED_HOST:
    raise SystemExit('SOURCE_URL_GUARD_FAIL')

OUT.mkdir(parents=True, exist_ok=True)
retrieved_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
req = urllib.request.Request(URL, headers={'User-Agent': 'WaveAlpha-Public-QA/1.0'})
with urllib.request.urlopen(req, timeout=20) as response:
    body = response.read()
    status = response.status

if status != 200:
    raise SystemExit(f'HTTP_STATUS_NOT_200:{status}')
if len(body) == 0 or len(body) > 100_000:
    raise SystemExit(f'BODY_SIZE_OUT_OF_BOUNDS:{len(body)}')

sha256 = hashlib.sha256(body).hexdigest()
try:
    data = json.loads(body.decode('utf-8'))
except Exception as exc:
    raise SystemExit(f'JSON_PARSE_FAIL:{exc}')

required = ['effectiveDate', 'cusip', 'nav', 'sharesOutstanding', 'basketValueAtMarketClose']
missing = [key for key in required if data.get(key) is None]
if missing:
    raise SystemExit('MISSING_REQUIRED_FIELDS:' + ','.join(missing))
if data.get('cusip') != EXPECTED_CUSIP:
    raise SystemExit(f'CUSIP_MISMATCH:{data.get("cusip")}')
if not isinstance(data.get('sharesOutstanding'), int):
    raise SystemExit('SHARES_NOT_INTEGER')

raw_path = OUT / 'btco-prices-current.json'
raw_path.write_bytes(body)
summary = {
    'scope': 'BTCO_CURRENT_FIRST_PARTY_PRICES_ONE_SHOT',
    'source_url': URL,
    'retrieved_at_utc': retrieved_at,
    'http_status': status,
    'body_size_bytes': len(body),
    'body_sha256': sha256,
    'effectiveDate': data['effectiveDate'],
    'cusip': data['cusip'],
    'nav': data['nav'],
    'sharesOutstanding': data['sharesOutstanding'],
    'basketValueAtMarketClose': data['basketValueAtMarketClose'],
    'openingPriceEffectiveBusinessDate': data.get('openingPriceEffectiveBusinessDate'),
    'network_calls': 1,
    'proxy_used': False,
    'stealth_used': False,
    'challenge_bypass_used': False,
    'secrets_used': False,
    'private_wave_alpha_source_copied': False,
}
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
print(json.dumps(summary, sort_keys=True))
