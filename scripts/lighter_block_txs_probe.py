#!/usr/bin/env python3
"""Bounded public-safe probe for Lighter block transaction visibility.

The probe reads currentHeight once and at most four nearby blocks through the
official blockTxs endpoint. It emits only aggregate transaction type counts and,
for INTERNAL_DELEVERAGE type 23 only, JSON key names from info/event_info. It
never logs account ids, hashes, raw tx payloads, credentials, or proprietary
Wave Alpha data.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BASE = "https://mainnet.zklighter.elliot.ai"
TIMEOUT = 15
USER_AGENT = "WaveAlpha-QA-Lighter-BlockTxs/1.0"
TX_INTERNAL_DELEVERAGE = 23
MAX_BLOCKS = 4


def get_json(path: str, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        payload = None
    return status, payload, len(body)


def json_object_keys(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return ",".join(sorted(str(key) for key in parsed.keys()))


def summarize_txs(payload):
    txs = payload.get("txs") if isinstance(payload, dict) else None
    if not isinstance(txs, list):
        return None
    types = Counter()
    type23_info_keys = Counter()
    type23_event_keys = Counter()
    malformed = 0
    type23_rows = 0
    for row in txs:
        if not isinstance(row, dict) or isinstance(row.get("type"), bool) or not isinstance(row.get("type"), int):
            malformed += 1
            continue
        tx_type = row["type"]
        types[str(tx_type)] += 1
        if tx_type == TX_INTERNAL_DELEVERAGE:
            type23_rows += 1
            info_keys = json_object_keys(row.get("info"))
            event_keys = json_object_keys(row.get("event_info"))
            if info_keys is not None:
                type23_info_keys[info_keys] += 1
            if event_keys is not None:
                type23_event_keys[event_keys] += 1
    return {
        "row_count": len(txs),
        "type_counts": dict(sorted(types.items())),
        "type23_rows": type23_rows,
        "type23_info_key_sets": dict(sorted(type23_info_keys.items())),
        "type23_event_info_key_sets": dict(sorted(type23_event_keys.items())),
        "malformed_rows": malformed,
    }


def main():
    height_http, height_payload, height_bytes = get_json("/api/v1/currentHeight")
    height = height_payload.get("height") if isinstance(height_payload, dict) else None
    output = {
        "probe": "lighter-block-txs-v1",
        "current_height_http": height_http,
        "current_height_response_bytes": height_bytes,
        "height_present": isinstance(height, int) and not isinstance(height, bool) and height >= 0,
        "max_blocks": MAX_BLOCKS,
        "block_reads": [],
        "type23_found": False,
        "raw_transactions_persisted": False,
        "account_ids_logged": False,
        "transaction_hashes_logged": False,
        "credentials_used": False,
    }
    if height_http != 200 or not isinstance(height, int) or isinstance(height, bool) or height < 0:
        print(json.dumps(output, indent=2, sort_keys=True))
        raise SystemExit(2)

    usable_reads = 0
    hard_schema_failures = 0
    for offset in range(MAX_BLOCKS):
        block_height = height - offset
        if block_height < 0:
            break
        http, payload, size = get_json("/api/v1/blockTxs", {"by": "height", "value": str(block_height)})
        summary = summarize_txs(payload)
        entry = {
            "height_offset": offset,
            "http": http,
            "response_bytes": size,
            "code": payload.get("code") if isinstance(payload, dict) else None,
            "message": str(payload.get("message") or "")[:160] if isinstance(payload, dict) else None,
            "txs_list": summary is not None,
        }
        if summary is not None:
            usable_reads += 1
            entry.update(summary)
            if summary["malformed_rows"]:
                hard_schema_failures += 1
            if summary["type23_rows"]:
                output["type23_found"] = True
        output["block_reads"].append(entry)
        if output["type23_found"]:
            break

    print(json.dumps(output, indent=2, sort_keys=True))
    # Access denial/shape drift is evidence. No bypass and no unbounded scan.
    if usable_reads == 0 or hard_schema_failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
