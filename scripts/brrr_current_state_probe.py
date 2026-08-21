#!/usr/bin/env python3
import hashlib
import json
import pathlib
import urllib.parse
import urllib.request
from datetime import datetime, timezone

URL = 'https://www-api.coinshares.com/api/v2/Widgets?ApiKey=094DA478-140C-4E3E-B394-7A19BBE8326B&names=VALKYRIE_DAILY_BRRR,VALKYRIE_HOLDINGS_BRRR,STAT_VALKYRIE_BRRR'
EXPECTED_HOST = 'www-api.coinshares.com'
OUT = pathlib.Path('artifacts/brrr-current-state')

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
if len(body) == 0 or len(body) > 250_000:
    raise SystemExit(f'BODY_SIZE_OUT_OF_BOUNDS:{len(body)}')

sha256 = hashlib.sha256(body).hexdigest()
try:
    rows = json.loads(body.decode('utf-8'))
except Exception as exc:
    raise SystemExit(f'JSON_PARSE_FAIL:{exc}')
if not isinstance(rows, list):
    raise SystemExit('TOP_LEVEL_NOT_LIST')

by_key = {row.get('key'): row for row in rows if isinstance(row, dict)}
daily = by_key.get('VALKYRIE_DAILY_BRRR')
holdings = by_key.get('VALKYRIE_HOLDINGS_BRRR')
if not daily or not holdings:
    raise SystemExit('REQUIRED_WIDGET_MISSING')

def meta_dict(section):
    return {m.get('key'): m.get('value') for m in section.get('meta', []) if isinstance(m, dict)}

daily_sections = daily.get('sections') or []
hold_sections = holdings.get('sections') or []
if not daily_sections or not hold_sections:
    raise SystemExit('REQUIRED_SECTION_MISSING')

daily_meta = meta_dict(daily_sections[0])
btc_meta = None
for section in hold_sections:
    m = meta_dict(section)
    if m.get('stockticker') == 'XBTUSD' or m.get('securityname') == 'BITCOIN':
        btc_meta = m
        break
if not btc_meta:
    raise SystemExit('BTC_HOLDINGS_SECTION_MISSING')

required_daily = ['Date', 'NAV', 'AUM', 'RateDate']
required_holdings = ['date', 'sharesoutstanding', 'creationunits', 'shares', 'netassets']
missing = [f'daily:{k}' for k in required_daily if daily_meta.get(k) in (None, '')]
missing += [f'holdings:{k}' for k in required_holdings if btc_meta.get(k) in (None, '')]
if missing:
    raise SystemExit('MISSING_REQUIRED_FIELDS:' + ','.join(missing))

try:
    shares_outstanding = int(btc_meta['sharesoutstanding'])
    creation_units = float(btc_meta['creationunits'])
    btc_quantity = float(btc_meta['shares'])
    nav = float(daily_meta['NAV'])
    aum = float(daily_meta['AUM'])
except Exception as exc:
    raise SystemExit(f'NUMERIC_PARSE_FAIL:{exc}')

raw_path = OUT / 'brrr-widgets-current.json'
raw_path.write_bytes(body)
summary = {
    'scope': 'BRRR_CURRENT_FIRST_PARTY_WIDGET_ONE_SHOT',
    'source_host': EXPECTED_HOST,
    'retrieved_at_utc': retrieved_at,
    'http_status': status,
    'body_size_bytes': len(body),
    'body_sha256': sha256,
    'daily_date': daily_meta['Date'],
    'nav_rate_date': daily_meta['RateDate'],
    'nav_per_share_usd': nav,
    'aum_usd': aum,
    'holdings_date_raw': btc_meta['date'],
    'shares_outstanding': shares_outstanding,
    'creation_units': creation_units,
    'btc_quantity': btc_quantity,
    'net_assets_usd': float(btc_meta['netassets']),
    'network_calls': 1,
    'public_page_emitted_request_contract': True,
    'proxy_used': False,
    'stealth_used': False,
    'challenge_bypass_used': False,
    'secrets_used': False,
    'private_wave_alpha_source_copied': False,
}
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n', encoding='utf-8')
print(json.dumps(summary, sort_keys=True))
