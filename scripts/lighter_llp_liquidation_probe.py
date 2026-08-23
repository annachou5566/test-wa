#!/usr/bin/env python3
"""Bounded public-safe probe for Lighter LLP/full-liquidation representation.

The probe performs only a handful of first-party public requests. It resolves the
current liquidity-pool account index from systemConfig and then asks the trades
endpoint for that public-pool account with liquidation filtering. It logs only
aggregate role/position-sign patterns; no account ids or raw trades are emitted.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from decimal import Decimal, InvalidOperation

BASE = "https://mainnet.zklighter.elliot.ai"
TIMEOUT = 15
USER_AGENT = "WaveAlpha-QA-Lighter-LLP-Representation/1.0"


def get_json(path: str, params: dict | None = None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
    )
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


def sign(value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return "invalid"
    if not parsed.is_finite():
        return "invalid"
    if parsed > 0:
        return "positive"
    if parsed < 0:
        return "negative"
    return "zero"


def summarize_trade_payload(label: str, status: int, payload, size: int):
    trades = payload.get("trades") if isinstance(payload, dict) else None
    summary = {
        "label": label,
        "http": status,
        "response_bytes": size,
        "code": payload.get("code") if isinstance(payload, dict) else None,
        "message": str(payload.get("message") or "")[:160] if isinstance(payload, dict) else None,
        "trades_list": isinstance(trades, list),
        "row_count": len(trades) if isinstance(trades, list) else None,
    }
    if not isinstance(trades, list):
        return summary

    type_counts = Counter()
    role_patterns = Counter()
    malformed = 0
    unique = set()
    duplicates = 0
    for row in trades:
        if not isinstance(row, dict):
            malformed += 1
            continue
        trade_id = row.get("trade_id_str") or row.get("trade_id")
        tx_hash = str(row.get("tx_hash") or "")
        market_id = row.get("market_id")
        if trade_id in {None, ""} or not tx_hash or not isinstance(market_id, int):
            malformed += 1
            continue
        identity = (market_id, str(trade_id), tx_hash)
        if identity in unique:
            duplicates += 1
            continue
        unique.add(identity)
        trade_type = str(row.get("type") or "")
        type_counts[trade_type or "<missing>"] += 1
        if trade_type == "liquidation":
            role_patterns[
                f"makerAsk:{row.get('is_maker_ask')}|"
                f"takerBefore:{sign(row.get('taker_position_size_before'))}|"
                f"makerBefore:{sign(row.get('maker_position_size_before'))}|"
                f"takerChanged:{row.get('taker_position_sign_changed')}|"
                f"makerChanged:{row.get('maker_position_sign_changed')}"
            ] += 1

    summary.update({
        "unique_rows": len(unique),
        "duplicate_rows": duplicates,
        "malformed_rows": malformed,
        "type_counts": dict(sorted(type_counts.items())),
        "liquidation_role_patterns": dict(sorted(role_patterns.items())),
    })
    return summary


def main():
    config_http, config, config_bytes = get_json("/api/v1/systemConfig")
    pool_index = config.get("liquidity_pool_index") if isinstance(config, dict) else None
    output = {
        "probe": "lighter-llp-liquidation-representation-v1",
        "system_config_http": config_http,
        "system_config_bytes": config_bytes,
        "liquidity_pool_index_present": isinstance(pool_index, int) and not isinstance(pool_index, bool),
        "trade_reads": [],
        "raw_trades_persisted": False,
        "account_ids_logged": False,
        "credentials_used": False,
    }
    if config_http != 200 or not isinstance(pool_index, int) or isinstance(pool_index, bool):
        print(json.dumps(output, indent=2, sort_keys=True))
        raise SystemExit(2)

    # A small set of documented parameters only. Stop after the first usable
    # list response so the probe does not multiply provider load.
    queries = [
        ("timestamp-desc-liquidation", {
            "sort_by": "timestamp", "sort_dir": "desc", "limit": 100,
            "account_index": pool_index, "market_type": "perp", "type": "liquidation",
        }),
        ("trade-id-desc-liquidation", {
            "sort_by": "trade_id", "sort_dir": "desc", "limit": 100,
            "account_index": pool_index, "market_type": "perp", "type": "liquidation",
        }),
        ("timestamp-desc-all-types", {
            "sort_by": "timestamp", "sort_dir": "desc", "limit": 100,
            "account_index": pool_index, "market_type": "perp",
        }),
    ]
    usable = False
    for label, params in queries:
        status, payload, size = get_json("/api/v1/trades", params)
        summary = summarize_trade_payload(label, status, payload, size)
        output["trade_reads"].append(summary)
        if summary.get("trades_list"):
            usable = True
            break

    print(json.dumps(output, indent=2, sort_keys=True))
    # Auth denial or no list is evidence, not a reason to bypass the boundary.
    # The job exits success once systemConfig worked; interpretation is manual.
    if config_http != 200:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
