#!/usr/bin/env python3
import hashlib
import json
import pathlib
import urllib.request

URL = "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/46091J101/prices"
MAX_BYTES = 1_000_000
OUT = pathlib.Path("artifacts")
OUT.mkdir(parents=True, exist_ok=True)

request = urllib.request.Request(
    URL,
    headers={
        "User-Agent": "Mozilla/5.0 (compatible; WaveAlphaPublicQA/1.0)",
        "Accept": "application/json",
    },
)

with urllib.request.urlopen(request, timeout=20) as response:
    status = int(response.status)
    raw = response.read(MAX_BYTES + 1)

if status != 200:
    raise SystemExit(f"BTCO_HTTP_STATUS=FAIL|{status}")
if len(raw) > MAX_BYTES:
    raise SystemExit(f"BTCO_RESPONSE_SIZE=FAIL|>{MAX_BYTES}")

sha256 = hashlib.sha256(raw).hexdigest()
try:
    payload = json.loads(raw)
except json.JSONDecodeError as exc:
    raise SystemExit(f"BTCO_JSON_PARSE=FAIL|{exc}")

if not isinstance(payload, dict):
    raise SystemExit("BTCO_JSON_SHAPE=FAIL|not_object")
if str(payload.get("cusip") or "") != "46091J101":
    raise SystemExit(f"BTCO_CUSIP=FAIL|{payload.get('cusip')}")

required = ["effectiveDate", "sharesOutstanding", "nav", "basketValueAtMarketClose"]
missing = [key for key in required if payload.get(key) is None]
if missing:
    raise SystemExit("BTCO_REQUIRED_FIELDS=FAIL|" + ",".join(missing))

shares = payload.get("sharesOutstanding")
if not isinstance(shares, int) or shares < 0:
    raise SystemExit(f"BTCO_SHARES=FAIL|{shares}")

(OUT / "btco-prices.json").write_bytes(raw)
summary = {
    "url": URL,
    "http_status": status,
    "bytes": len(raw),
    "sha256": sha256,
    "effectiveDate": payload.get("effectiveDate"),
    "cusip": payload.get("cusip"),
    "sharesOutstanding": shares,
    "nav": payload.get("nav"),
    "basketValueAtMarketClose": payload.get("basketValueAtMarketClose"),
    "openingPriceEffectiveBusinessDate": payload.get("openingPriceEffectiveBusinessDate"),
}
(OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

print("BTCO_PUBLIC_PROBE=PASS")
for key in [
    "http_status", "bytes", "sha256", "effectiveDate", "cusip",
    "sharesOutstanding", "nav", "basketValueAtMarketClose",
    "openingPriceEffectiveBusinessDate",
]:
    print(f"BTCO_{key}={summary[key]}")
print("NETWORK_CALLS=1")
