#!/usr/bin/env python3
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://mainnet.zklighter.elliot.ai"
UA = "WaveAlpha-QA-Lighter-Liquidation-Semantics/1.0"
TIMEOUT = 15


def get(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
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


def num(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def sign(value):
    x = num(value)
    if x is None:
        return "invalid"
    if x > 0:
        return "positive"
    if x < 0:
        return "negative"
    return "zero"


def main():
    variants = [
        ("timestamp", "desc"),
        ("transaction_time", "desc"),
        ("trade_id", "desc"),
    ]
    selected = None
    attempts = []
    for sort_by, sort_dir in variants:
        status, payload, size = get("/api/v1/trades", {
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "limit": 100,
            "market_type": "perp",
            "type": "liquidation",
        })
        trades = payload.get("trades") if isinstance(payload, dict) else None
        attempts.append({
            "sort_by": sort_by,
            "sort_dir": sort_dir,
            "http": status,
            "response_bytes": size,
            "code": payload.get("code") if isinstance(payload, dict) else None,
            "message": str(payload.get("message", ""))[:160] if isinstance(payload, dict) else None,
            "rows": len(trades) if isinstance(trades, list) else None,
        })
        if status == 200 and isinstance(trades, list):
            selected = (sort_by, sort_dir, trades, payload)
            break

    output = {
        "probe": "lighter-liquidation-semantics-v1",
        "attempts": attempts,
        "credentials_used": False,
        "raw_trades_persisted": False,
    }
    if selected is None:
        print(json.dumps(output, indent=2, sort_keys=True))
        return 2

    sort_by, sort_dir, trades, payload = selected
    type_counts = {}
    is_maker_ask_counts = {"true": 0, "false": 0, "other": 0}
    maker_before_signs = {}
    taker_before_signs = {}
    bid_pnl_signs = {}
    ask_pnl_signs = {}
    sign_pairs = {}
    malformed = 0
    market_ids = set()
    timestamps = []
    duplicate_trade_ids = 0
    seen_trade_ids = set()
    duplicate_hash_trade = 0
    seen_hash_trade = set()

    for trade in trades:
        if not isinstance(trade, dict):
            malformed += 1
            continue
        trade_type = str(trade.get("type", ""))
        type_counts[trade_type] = type_counts.get(trade_type, 0) + 1
        market_id = trade.get("market_id")
        timestamp = trade.get("timestamp")
        trade_id = trade.get("trade_id")
        tx_hash = str(trade.get("tx_hash", ""))
        usd_amount = num(trade.get("usd_amount"))
        price = num(trade.get("price"))
        size = num(trade.get("size"))
        if not isinstance(market_id, int) or not isinstance(timestamp, int) or not isinstance(trade_id, int) or not tx_hash or usd_amount is None or usd_amount < 0 or price is None or price < 0 or size is None or size < 0:
            malformed += 1
            continue
        market_ids.add(market_id)
        timestamps.append(timestamp)
        if trade_id in seen_trade_ids:
            duplicate_trade_ids += 1
        seen_trade_ids.add(trade_id)
        hash_trade_key = (tx_hash, trade_id)
        if hash_trade_key in seen_hash_trade:
            duplicate_hash_trade += 1
        seen_hash_trade.add(hash_trade_key)

        maker_ask = trade.get("is_maker_ask")
        key = "true" if maker_ask is True else "false" if maker_ask is False else "other"
        is_maker_ask_counts[key] += 1
        maker_sign = sign(trade.get("maker_position_size_before"))
        taker_sign = sign(trade.get("taker_position_size_before"))
        bid_sign = sign(trade.get("bid_account_pnl"))
        ask_sign = sign(trade.get("ask_account_pnl"))
        maker_before_signs[maker_sign] = maker_before_signs.get(maker_sign, 0) + 1
        taker_before_signs[taker_sign] = taker_before_signs.get(taker_sign, 0) + 1
        bid_pnl_signs[bid_sign] = bid_pnl_signs.get(bid_sign, 0) + 1
        ask_pnl_signs[ask_sign] = ask_pnl_signs.get(ask_sign, 0) + 1
        pair = f"maker:{maker_sign}|taker:{taker_sign}|makerAsk:{key}"
        sign_pairs[pair] = sign_pairs.get(pair, 0) + 1

    output.update({
        "selected_query": {"sort_by": sort_by, "sort_dir": sort_dir},
        "row_count": len(trades),
        "next_cursor_present": bool(payload.get("next_cursor")) if isinstance(payload, dict) else False,
        "type_counts": type_counts,
        "market_count": len(market_ids),
        "timestamp_range": [min(timestamps), max(timestamps)] if timestamps else None,
        "is_maker_ask_counts": is_maker_ask_counts,
        "maker_position_size_before_signs": maker_before_signs,
        "taker_position_size_before_signs": taker_before_signs,
        "bid_account_pnl_signs": bid_pnl_signs,
        "ask_account_pnl_signs": ask_pnl_signs,
        "position_sign_patterns": dict(sorted(sign_pairs.items())),
        "duplicate_trade_ids": duplicate_trade_ids,
        "duplicate_txhash_tradeid": duplicate_hash_trade,
        "malformed_rows": malformed,
    })
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0 if malformed == 0 else 3


if __name__ == "__main__":
    sys.exit(main())
