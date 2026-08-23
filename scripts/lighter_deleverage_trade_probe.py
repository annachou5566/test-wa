#!/usr/bin/env python3
"""Bounded public-safe probe for Lighter Trade(type=deleverage).

New hypothesis: official /api/v1/trades is market/account scoped, so a request
without either scope can return 400 even though deleverage trades are exposed.
This probe resolves active perp markets from orderBooks and queries a small paced
set by market_id with server-side type=deleverage, stopping after enough positive
evidence. Output is aggregate-only: no account ids, tx hashes, trade ids or raw
rows are emitted or persisted.
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

BASE = "https://mainnet.zklighter.elliot.ai"
TIMEOUT = 15
UA = "WaveAlpha-QA-Lighter-Deleverage-Trade/2.0"
LIMIT = 20
MAX_MARKETS = 24
POSITIVE_MARKET_TARGET = 3
PACE_SECONDS = 1.1
EXPECTED_TYPE = "deleverage"


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


def active_perps(payload):
    rows = payload.get("order_books") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("market_type") or "").lower() != "perp":
            continue
        if str(row.get("status") or "").lower() != "active":
            continue
        market_id = row.get("market_id")
        if isinstance(market_id, bool) or not isinstance(market_id, int) or market_id < 0:
            continue
        out.append(market_id)
    return sorted(set(out))


def summarize_rows(payload):
    rows = payload.get("trades") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None

    type_counts = Counter()
    maker_ask_counts = Counter()
    taker_before_signs = Counter()
    maker_before_signs = Counter()
    field_presence = Counter()
    side_candidates = Counter()
    timestamps = []
    malformed = 0
    inconsistent_type = 0

    for row in rows:
        if not isinstance(row, dict):
            malformed += 1
            continue
        row_type = str(row.get("type") or "")
        type_counts[row_type] += 1
        if row_type != EXPECTED_TYPE:
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

        timestamps.append(timestamp)
        maker_ask = row.get("is_maker_ask")
        maker_key = "true" if maker_ask is True else "false" if maker_ask is False else "other"
        maker_ask_counts[maker_key] += 1
        taker_sign = sign_name(row.get("taker_position_size_before"))
        maker_sign = sign_name(row.get("maker_position_size_before"))
        taker_before_signs[taker_sign] += 1
        maker_before_signs[maker_sign] += 1

        # Official INTERNAL_DELEVERAGE circuit applies bankrupt account as taker.
        if maker_ask is False and taker_sign == "positive":
            side_candidates["long"] += 1
        elif maker_ask is True and taker_sign == "negative":
            side_candidates["short"] += 1
        else:
            side_candidates["unresolved"] += 1

        for key in (
            "market_id", "size", "price", "usd_amount", "is_maker_ask", "block_height",
            "timestamp", "taker_position_size_before", "maker_position_size_before", "transaction_time",
        ):
            if key in row and row.get(key) not in (None, ""):
                field_presence[key] += 1

    return {
        "row_count": len(rows),
        "type_counts": dict(sorted(type_counts.items())),
        "timestamp_range": [min(timestamps), max(timestamps)] if timestamps else None,
        "is_maker_ask_counts": dict(sorted(maker_ask_counts.items())),
        "taker_position_size_before_signs": dict(sorted(taker_before_signs.items())),
        "maker_position_size_before_signs": dict(sorted(maker_before_signs.items())),
        "candidate_liquidated_side_counts": dict(sorted(side_candidates.items())),
        "field_presence_counts": dict(sorted(field_presence.items())),
        "malformed_rows": malformed,
        "unexpected_non_deleverage_rows": inconsistent_type,
        "next_cursor_present": bool(payload.get("next_cursor")),
    }


def query_market(market_id):
    return get_json("/api/v1/trades", {
        "market_id": market_id,
        "market_type": "perp",
        "sort_by": "timestamp",
        "sort_dir": "desc",
        "type": EXPECTED_TYPE,
        "limit": LIMIT,
    })


def main():
    meta_http, meta, meta_bytes = get_json("/api/v1/orderBooks")
    markets = active_perps(meta)
    output = {
        "probe": "lighter-deleverage-by-market-v1",
        "expected_type": EXPECTED_TYPE,
        "order_books_http": meta_http,
        "order_books_response_bytes": meta_bytes,
        "active_perp_count": len(markets) if isinstance(markets, list) else None,
        "max_markets": MAX_MARKETS,
        "positive_market_target": POSITIVE_MARKET_TARGET,
        "market_reads": [],
        "markets_attempted": 0,
        "positive_markets": 0,
        "deleverage_rows_observed": 0,
        "aggregate_side_candidates": {},
        "raw_trades_persisted": False,
        "account_ids_logged": False,
        "transaction_hashes_logged": False,
        "trade_ids_logged": False,
        "credentials_used": False,
    }
    if meta_http != 200 or not isinstance(markets, list) or not markets:
        print(json.dumps(output, indent=2, sort_keys=True))
        raise SystemExit(2)

    aggregate_sides = Counter()
    hard_failure = False
    for market_id in markets[:MAX_MARKETS]:
        http, payload, size = query_market(market_id)
        summary = summarize_rows(payload)
        entry = {
            "market_id": market_id,
            "http": http,
            "response_bytes": size,
            "summary": summary,
        }
        if isinstance(payload, dict) and summary is None:
            entry["code"] = payload.get("code")
            entry["message"] = str(payload.get("message") or "")[:120]
        output["market_reads"].append(entry)
        output["markets_attempted"] += 1

        if http == 200 and isinstance(summary, dict):
            if summary["malformed_rows"] or summary["unexpected_non_deleverage_rows"]:
                hard_failure = True
            if summary["row_count"] > 0:
                output["positive_markets"] += 1
                output["deleverage_rows_observed"] += summary["row_count"]
                aggregate_sides.update(summary["candidate_liquidated_side_counts"])
                if output["positive_markets"] >= POSITIVE_MARKET_TARGET:
                    break
        elif http == 429:
            # Respect provider boundary; one bounded cooldown then continue.
            time.sleep(5)
        elif http not in (200, 400, 403):
            hard_failure = True

        time.sleep(PACE_SECONDS)

    output["aggregate_side_candidates"] = dict(sorted(aggregate_sides.items()))
    print(json.dumps(output, indent=2, sort_keys=True))

    if hard_failure:
        raise SystemExit(3)
    if not any(item.get("http") == 200 and isinstance(item.get("summary"), dict) for item in output["market_reads"]):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
