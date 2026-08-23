#!/usr/bin/env python3
"""Bounded public-safe probe for Lighter full-liquidation/ADL transaction surface.

Reads systemConfig once, then accountTxs for the public liquidity-pool account
filtered to official INTERNAL_DELEVERAGE transaction type 23. Output contains
only aggregate schema/type/marker information; account ids and raw tx payloads
are never emitted or persisted.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BASE = "https://mainnet.zklighter.elliot.ai"
TIMEOUT = 15
USER_AGENT = "WaveAlpha-QA-Lighter-Internal-Deleverage/1.0"
TX_INTERNAL_DELEVERAGE = 23


def get_json(path: str, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        payload = None
    return status, payload, len(body)


def parse_json_string(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def main():
    config_http, config, config_bytes = get_json("/api/v1/systemConfig")
    pool_index = config.get("liquidity_pool_index") if isinstance(config, dict) else None
    output = {
        "probe": "lighter-internal-deleverage-v1",
        "system_config_http": config_http,
        "system_config_bytes": config_bytes,
        "liquidity_pool_index_present": isinstance(pool_index, int) and not isinstance(pool_index, bool),
        "account_txs_http": None,
        "account_txs_response_bytes": None,
        "account_txs_code": None,
        "account_txs_message": None,
        "txs_list": False,
        "row_count": None,
        "tx_type_counts": {},
        "info_key_sets": {},
        "event_info_key_sets": {},
        "malformed_rows": 0,
        "raw_transactions_persisted": False,
        "account_ids_logged": False,
        "credentials_used": False,
    }
    if config_http != 200 or not isinstance(pool_index, int) or isinstance(pool_index, bool):
        print(json.dumps(output, indent=2, sort_keys=True))
        raise SystemExit(2)

    http, payload, size = get_json("/api/v1/accountTxs", {
        "limit": 100,
        "by": "index",
        "value": str(pool_index),
        "types": [TX_INTERNAL_DELEVERAGE],
    })
    output["account_txs_http"] = http
    output["account_txs_response_bytes"] = size
    if isinstance(payload, dict):
        output["account_txs_code"] = payload.get("code")
        output["account_txs_message"] = str(payload.get("message") or "")[:160]
    txs = payload.get("txs") if isinstance(payload, dict) else None
    output["txs_list"] = isinstance(txs, list)
    output["row_count"] = len(txs) if isinstance(txs, list) else None

    type_counts = Counter()
    info_sets = Counter()
    event_sets = Counter()
    malformed = 0
    if isinstance(txs, list):
        for row in txs:
            if not isinstance(row, dict):
                malformed += 1
                continue
            tx_type = row.get("type")
            if not isinstance(tx_type, int):
                malformed += 1
                continue
            type_counts[str(tx_type)] += 1
            info = parse_json_string(row.get("info"))
            event = parse_json_string(row.get("event_info"))
            if info is not None:
                # Keys only, never values (which include account ids).
                info_sets[",".join(sorted(str(key) for key in info.keys()))] += 1
            if event is not None:
                event_sets[",".join(sorted(str(key) for key in event.keys()))] += 1

    output["tx_type_counts"] = dict(sorted(type_counts.items()))
    output["info_key_sets"] = dict(sorted(info_sets.items()))
    output["event_info_key_sets"] = dict(sorted(event_sets.items()))
    output["malformed_rows"] = malformed
    print(json.dumps(output, indent=2, sort_keys=True))

    # Auth rejection/unsupported by-mode is evidence; never bypass it.
    if http != 200 or not isinstance(txs, list) or malformed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
