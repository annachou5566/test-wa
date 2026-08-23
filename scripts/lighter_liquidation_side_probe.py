#!/usr/bin/env python3
"""Bounded public-safe Lighter liquidation side-semantics probe.

Uses only official public orderBooks + recentTrades endpoints. It never persists
raw trades/account ids. Requests are sequential and deliberately paced; 429 gets
bounded backoff rather than a burst retry.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from decimal import Decimal, InvalidOperation

BASE = "https://mainnet.zklighter.elliot.ai"
TIMEOUT = 12
LIMIT = 100
REQUEST_DELAY_SEC = 0.35
MAX_429_RETRIES = 2
USER_AGENT = "WaveAlpha-QA-Lighter-Side-Semantics/1.1"


def get_json(path: str, params: dict | None = None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            body = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        status = exc.code
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        payload = None
    return status, payload


def decimal_or_none(value):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def sign(value):
    parsed = decimal_or_none(value)
    if parsed is None:
        return "invalid"
    if parsed > 0:
        return "positive"
    if parsed < 0:
        return "negative"
    return "zero"


def fetch_market(market):
    market_id = int(market["market_id"])
    symbol = str(market.get("symbol") or "")
    attempts = 0
    while True:
        attempts += 1
        status, payload = get_json("/api/v1/recentTrades", {"market_id": market_id, "limit": LIMIT})
        if status != 429 or attempts > MAX_429_RETRIES:
            break
        time.sleep(float(attempts))
    trades = payload.get("trades") if isinstance(payload, dict) else None
    liquidation_rows = []
    if isinstance(trades, list):
        for row in trades:
            if isinstance(row, dict) and str(row.get("type") or "").lower() == "liquidation":
                liquidation_rows.append(row)
    return market_id, symbol, status, liquidation_rows, isinstance(trades, list), attempts


def main():
    status, metadata = get_json("/api/v1/orderBooks")
    books = metadata.get("order_books") if isinstance(metadata, dict) else None
    if status != 200 or not isinstance(books, list):
        raise SystemExit("orderBooks unavailable")
    markets = [
        row for row in books
        if isinstance(row, dict)
        and row.get("market_type") == "perp"
        and row.get("status") == "active"
        and isinstance(row.get("market_id"), int)
    ]
    markets.sort(key=lambda row: int(row["market_id"]))

    results = []
    for market in markets:
        results.append(fetch_market(market))
        time.sleep(REQUEST_DELAY_SEC)

    http_counts = Counter()
    attempt_counts = Counter()
    rows_total = 0
    unique = {}
    duplicate_rows = 0
    pnl_patterns = Counter()
    taker_patterns = Counter()
    liquidation_market_ids = set()
    malformed = 0
    list_payload_markets = 0

    for market_id, _symbol, http, rows, has_list, attempts in results:
        http_counts[str(http)] += 1
        attempt_counts[str(attempts)] += 1
        if has_list:
            list_payload_markets += 1
        for row in rows:
            rows_total += 1
            trade_id = row.get("trade_id_str") or row.get("trade_id")
            tx_hash = str(row.get("tx_hash") or "")
            row_market_id = row.get("market_id")
            if trade_id in {None, ""} or not tx_hash or not isinstance(row_market_id, int):
                malformed += 1
                continue
            key = (row_market_id, str(trade_id), tx_hash)
            if key in unique:
                duplicate_rows += 1
                continue
            unique[key] = True
            liquidation_market_ids.add(row_market_id)
            pnl_patterns[f"ask:{sign(row.get('ask_account_pnl'))}|bid:{sign(row.get('bid_account_pnl'))}"] += 1
            taker_patterns[
                f"takerBefore:{sign(row.get('taker_position_size_before'))}|"
                f"makerAsk:{row.get('is_maker_ask')}|"
                f"takerSignChanged:{row.get('taker_position_sign_changed')}"
            ] += 1

    summary = {
        "probe": "lighter-liquidation-side-semantics-v2-paced",
        "order_books_http": status,
        "active_perp_markets": len(markets),
        "request_delay_sec": REQUEST_DELAY_SEC,
        "max_429_retries": MAX_429_RETRIES,
        "recent_trades_http_counts": dict(sorted(http_counts.items())),
        "request_attempt_counts": dict(sorted(attempt_counts.items())),
        "markets_with_list_payload": list_payload_markets,
        "recent_trade_limit_per_market": LIMIT,
        "liquidation_rows_seen": rows_total,
        "liquidation_unique_rows": len(unique),
        "liquidation_duplicate_rows": duplicate_rows,
        "liquidation_market_count": len(liquidation_market_ids),
        "liquidation_market_ids": sorted(liquidation_market_ids),
        "pnl_side_patterns": dict(sorted(pnl_patterns.items())),
        "taker_patterns": dict(sorted(taker_patterns.items())),
        "malformed_liquidation_rows": malformed,
        "raw_trades_persisted": False,
        "account_ids_logged": False,
        "credentials_used": False,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    if len(markets) == 0 or list_payload_markets != len(markets):
        raise SystemExit(2)
    if malformed:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
