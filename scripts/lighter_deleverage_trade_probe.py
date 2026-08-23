#!/usr/bin/env python3
"""Bounded public-safe probe for Lighter Trade(type=deleverage).

Uses only official first-party REST surfaces. It resolves the public liquidity-pool
index from systemConfig, then queries /api/v1/trades with server-side
`type=deleverage` both globally and for the public pool. Output contains only
aggregate counts/schema/role diagnostics. It never emits account ids, tx hashes,
trade ids, raw rows, credentials, or proprietary Wave Alpha data.
"""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BASE = "https://mainnet.zklighter.elliot.ai"
TIMEOUT = 15
UA = "WaveAlpha-QA-Lighter-Deleverage-Trade/1.0"
LIMIT = 100


def get_json(path: str, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
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


def finite_num(value):
    try:
        x = float(value)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def sign_name(value):
    x = finite_num(value)
    if x is None:
        return "missing_or_invalid"
    if x > 0:
        return "positive"
    if x < 0:
        return "negative"
    return "zero"


def summarize(payload, pool_index=None):
    rows = payload.get("trades") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None

    type_counts = Counter()
    maker_ask_counts = Counter()
    taker_before_signs = Counter()
    maker_before_signs = Counter()
    pool_roles = Counter()
    fields_present = Counter()
    market_ids = set()
    timestamps = []
    malformed = 0
    inconsistent_type = 0
    side_candidate_counts = Counter()

    for row in rows:
        if not isinstance(row, dict):
            malformed += 1
            continue
        row_type = str(row.get("type") or "")
        type_counts[row_type] += 1
        if row_type != "deleverage":
            inconsistent_type += 1

        market_id = row.get("market_id")
        timestamp = row.get("timestamp")
        usd = finite_num(row.get("usd_amount"))
        price = finite_num(row.get("price"))
        size = finite_num(row.get("size"))
        if (
            isinstance(market_id, bool) or not isinstance(market_id, int) or market_id < 0
            or isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0
            or usd is None or usd < 0 or price is None or price < 0 or size is None or size < 0
        ):
            malformed += 1
            continue

        market_ids.add(market_id)
        timestamps.append(timestamp)
        maker_ask = row.get("is_maker_ask")
        maker_key = "true" if maker_ask is True else "false" if maker_ask is False else "other"
        maker_ask_counts[maker_key] += 1

        taker_sign = sign_name(row.get("taker_position_size_before"))
        maker_sign = sign_name(row.get("maker_position_size_before"))
        taker_before_signs[taker_sign] += 1
        maker_before_signs[maker_sign] += 1

        # Protocol source applies INTERNAL_DELEVERAGE with bankrupt account as taker.
        # Candidate liquidated side can therefore be checked without logging identities.
        if maker_ask is False and taker_sign == "positive":
            side_candidate_counts["long"] += 1
        elif maker_ask is True and taker_sign == "negative":
            side_candidate_counts["short"] += 1
        else:
            side_candidate_counts["unresolved"] += 1

        for key in (
            "trade_id", "trade_id_str", "tx_hash", "market_id", "size", "price", "usd_amount",
            "ask_id", "bid_id", "ask_account_id", "bid_account_id", "is_maker_ask", "block_height",
            "timestamp", "taker_position_size_before", "maker_position_size_before", "transaction_time",
        ):
            if key in row and row.get(key) not in (None, ""):
                fields_present[key] += 1

        if isinstance(pool_index, int):
            ask_id = row.get("ask_account_id")
            bid_id = row.get("bid_account_id")
            if ask_id == pool_index:
                pool_roles["ask"] += 1
            if bid_id == pool_index:
                pool_roles["bid"] += 1
            if ask_id != pool_index and bid_id != pool_index:
                pool_roles["neither"] += 1

    return {
        "row_count": len(rows),
        "type_counts": dict(sorted(type_counts.items())),
        "market_count": len(market_ids),
        "timestamp_range": [min(timestamps), max(timestamps)] if timestamps else None,
        "is_maker_ask_counts": dict(sorted(maker_ask_counts.items())),
        "taker_position_size_before_signs": dict(sorted(taker_before_signs.items())),
        "maker_position_size_before_signs": dict(sorted(maker_before_signs.items())),
        "candidate_liquidated_side_counts": dict(sorted(side_candidate_counts.items())),
        "public_pool_role_counts": dict(sorted(pool_roles.items())),
        "field_presence_counts": dict(sorted(fields_present.items())),
        "malformed_rows": malformed,
        "unexpected_non_deleverage_rows": inconsistent_type,
        "next_cursor_present": bool(payload.get("next_cursor")),
    }


def query_deleverage(account_index=None):
    params = {
        "market_type": "perp",
        "sort_by": "timestamp",
        "sort_dir": "desc",
        "type": "deleverage",
        "limit": LIMIT,
    }
    if account_index is not None:
        params["account_index"] = account_index
    return get_json("/api/v1/trades", params)


def main():
    config_http, config, config_bytes = get_json("/api/v1/systemConfig")
    pool_index = config.get("liquidity_pool_index") if isinstance(config, dict) else None
    output = {
        "probe": "lighter-deleverage-trade-v1",
        "system_config_http": config_http,
        "system_config_bytes": config_bytes,
        "liquidity_pool_index_present": isinstance(pool_index, int) and not isinstance(pool_index, bool),
        "global_query": None,
        "public_pool_query": None,
        "raw_trades_persisted": False,
        "account_ids_logged": False,
        "transaction_hashes_logged": False,
        "credentials_used": False,
    }
    if config_http != 200 or not isinstance(pool_index, int) or isinstance(pool_index, bool):
        print(json.dumps(output, indent=2, sort_keys=True))
        raise SystemExit(2)

    global_http, global_payload, global_bytes = query_deleverage()
    output["global_query"] = {
        "http": global_http,
        "response_bytes": global_bytes,
        "summary": summarize(global_payload, pool_index),
    }

    pool_http, pool_payload, pool_bytes = query_deleverage(pool_index)
    output["public_pool_query"] = {
        "http": pool_http,
        "response_bytes": pool_bytes,
        "summary": summarize(pool_payload, pool_index),
    }

    print(json.dumps(output, indent=2, sort_keys=True))

    usable = 0
    for item in (output["global_query"], output["public_pool_query"]):
        summary = item.get("summary") if isinstance(item, dict) else None
        if item.get("http") == 200 and isinstance(summary, dict):
            usable += 1
            if summary.get("malformed_rows") or summary.get("unexpected_non_deleverage_rows"):
                raise SystemExit(3)
    if usable == 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
