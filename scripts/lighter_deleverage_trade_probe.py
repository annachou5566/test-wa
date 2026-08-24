#!/usr/bin/env python3
"""Bounded first-party Lighter whole-exchange liquidation WebSocket monitor.

Purpose: determine whether public trade/<market_id> updates expose full
liquidation / INTERNAL_DELEVERAGE events as Trade(type="deleverage"), while also
counting the documented liquidation_trades field. Public-safe aggregate evidence
only: no account ids, tx hashes, trade ids, raw rows, credentials, or proprietary
Wave Alpha data are persisted or printed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
import urllib.request
from collections import Counter

import websockets

HTTP_BASE = "https://mainnet.zklighter.elliot.ai"
WS_URI = "wss://mainnet.zklighter.elliot.ai/stream?readonly=true"
HTTP_TIMEOUT = 15
DURATION_SECONDS = int(os.environ.get("LIGHTER_MONITOR_SECONDS", "7200"))
SHARD_COUNT = int(os.environ.get("LIGHTER_WS_SHARDS", "4"))
SUBSCRIBE_PACE_SECONDS = 0.03
EARLY_DELEVERAGE_TARGET = 3
EARLY_DELEVERAGE_MARKET_TARGET = 2


def get_json(path: str):
    req = urllib.request.Request(
        HTTP_BASE + path,
        headers={"Accept": "application/json", "User-Agent": "WaveAlpha-QA-Lighter-WS/1.0"},
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def active_perps(payload):
    rows = payload.get("order_books") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("market_type") or "").lower() != "perp":
            continue
        if str(row.get("status") or "").lower() != "active":
            continue
        market_id = row.get("market_id")
        if isinstance(market_id, int) and not isinstance(market_id, bool) and market_id >= 0:
            out.append(market_id)
    return sorted(set(out))


def as_rows(value):
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def finite(value):
    try:
        x = float(value)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def sign_name(value):
    x = finite(value)
    if x is None:
        return "missing_or_invalid"
    if x > 0:
        return "positive"
    if x < 0:
        return "negative"
    return "zero"


class Evidence:
    def __init__(self, active_markets):
        self.active_markets = active_markets
        self.started_at = time.time()
        self.connections = 0
        self.reconnects = 0
        self.subscription_messages_sent = 0
        self.messages = 0
        self.update_messages = 0
        self.market_updates = Counter()
        self.trade_rows = 0
        self.liquidation_field_rows = 0
        self.trades_type_counts = Counter()
        self.liquidation_field_type_counts = Counter()
        self.nontrade_source_counts = Counter()
        self.nontrade_markets = set()
        self.deleverage_markets = set()
        self.field_presence = Counter()
        self.side_candidates = Counter()
        self.seen_trade_keys = set()
        self.duplicate_trade_keys = 0
        self.seen_tx_hashes = set()
        self.errors = Counter()
        self.stop = asyncio.Event()

    def record_trade(self, row, source):
        row_type = str(row.get("type") or "missing")
        market_id = row.get("market_id")
        if source == "trades":
            self.trade_rows += 1
            self.trades_type_counts[row_type] += 1
        else:
            self.liquidation_field_rows += 1
            self.liquidation_field_type_counts[row_type] += 1

        if row_type != "trade" or source == "liquidation_trades":
            self.nontrade_source_counts[f"{source}:{row_type}"] += 1
            if isinstance(market_id, int):
                self.nontrade_markets.add(market_id)
            if row_type == "deleverage" and isinstance(market_id, int):
                self.deleverage_markets.add(market_id)

            for key in (
                "market_id", "size", "price", "usd_amount", "is_maker_ask",
                "block_height", "timestamp", "taker_position_size_before",
                "maker_position_size_before", "transaction_time",
            ):
                if row.get(key) not in (None, ""):
                    self.field_presence[key] += 1

            # Protocol evidence: bankrupt account is taker. Use both maker/taker
            # direction and pre-position sign; otherwise keep the side unresolved.
            maker_ask = row.get("is_maker_ask")
            taker_sign = sign_name(row.get("taker_position_size_before"))
            if maker_ask is False and taker_sign == "positive":
                self.side_candidates["long"] += 1
            elif maker_ask is True and taker_sign == "negative":
                self.side_candidates["short"] += 1
            else:
                self.side_candidates["unresolved"] += 1

        trade_id = row.get("trade_id")
        if trade_id not in (None, "") and isinstance(market_id, int):
            # Keep only a one-way hash in memory; never emit the raw identity.
            digest = hashlib.sha256(f"{market_id}:{trade_id}".encode()).digest()
            if digest in self.seen_trade_keys:
                self.duplicate_trade_keys += 1
            else:
                self.seen_trade_keys.add(digest)

        tx_hash = row.get("tx_hash")
        if tx_hash not in (None, ""):
            self.seen_tx_hashes.add(hashlib.sha256(str(tx_hash).encode()).digest())

        deleverage_count = self.trades_type_counts.get("deleverage", 0) + self.liquidation_field_type_counts.get("deleverage", 0)
        if deleverage_count >= EARLY_DELEVERAGE_TARGET and len(self.deleverage_markets) >= EARLY_DELEVERAGE_MARKET_TARGET:
            self.stop.set()

    def summary(self):
        elapsed = max(0.0, time.time() - self.started_at)
        deleverage_rows = self.trades_type_counts.get("deleverage", 0) + self.liquidation_field_type_counts.get("deleverage", 0)
        liquidation_rows = self.trades_type_counts.get("liquidation", 0) + self.liquidation_field_type_counts.get("liquidation", 0)
        return {
            "probe": "lighter-full-212-public-ws-v1",
            "source": WS_URI,
            "requested_duration_seconds": DURATION_SECONDS,
            "observed_duration_seconds": round(elapsed, 3),
            "active_perp_count": len(self.active_markets),
            "ws_shards": SHARD_COUNT,
            "connections_opened": self.connections,
            "reconnects": self.reconnects,
            "subscription_messages_sent": self.subscription_messages_sent,
            "all_active_markets_subscribed": self.subscription_messages_sent >= len(self.active_markets),
            "messages_received": self.messages,
            "trade_update_messages": self.update_messages,
            "markets_with_updates": len(self.market_updates),
            "trade_rows": self.trade_rows,
            "trades_type_counts": dict(sorted(self.trades_type_counts.items())),
            "liquidation_trades_rows": self.liquidation_field_rows,
            "liquidation_trades_type_counts": dict(sorted(self.liquidation_field_type_counts.items())),
            "nontrade_source_counts": dict(sorted(self.nontrade_source_counts.items())),
            "nontrade_market_count": len(self.nontrade_markets),
            "deleverage_rows_observed": deleverage_rows,
            "deleverage_market_count": len(self.deleverage_markets),
            "liquidation_rows_observed": liquidation_rows,
            "side_candidate_counts": dict(sorted(self.side_candidates.items())),
            "nontrade_field_presence_counts": dict(sorted(self.field_presence.items())),
            "unique_trade_identity_hashes": len(self.seen_trade_keys),
            "duplicate_trade_identity_count": self.duplicate_trade_keys,
            "unique_tx_hash_hashes": len(self.seen_tx_hashes),
            "connection_error_counts": dict(sorted(self.errors.items())),
            "credentials_used": False,
            "raw_rows_persisted": False,
            "account_ids_logged": False,
            "trade_ids_logged": False,
            "transaction_hashes_logged": False,
            "verdict": (
                "DELEVERAGE_OBSERVED"
                if deleverage_rows > 0
                else "NONTRADE_OBSERVED_NO_DELEVERAGE"
                if self.nontrade_source_counts
                else "NO_NONTRADE_EVENT_OBSERVED_IN_WINDOW"
            ),
        }


async def shard_worker(shard_id, market_ids, evidence, deadline):
    attempt = 0
    while time.time() < deadline and not evidence.stop.is_set():
        try:
            async with websockets.connect(
                WS_URI,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=16 * 1024 * 1024,
                open_timeout=15,
            ) as ws:
                evidence.connections += 1
                if attempt:
                    evidence.reconnects += 1
                attempt = 0

                for market_id in market_ids:
                    await ws.send(json.dumps({"type": "subscribe", "channel": f"trade/{market_id}"}))
                    evidence.subscription_messages_sent += 1
                    await asyncio.sleep(SUBSCRIBE_PACE_SECONDS)

                while time.time() < deadline and not evidence.stop.is_set():
                    remaining = max(0.1, min(5.0, deadline - time.time()))
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    except asyncio.TimeoutError:
                        continue
                    evidence.messages += 1
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        evidence.errors["json_decode"] += 1
                        continue
                    if payload.get("type") != "update/trade":
                        continue
                    evidence.update_messages += 1
                    channel = str(payload.get("channel") or "")
                    if ":" in channel:
                        try:
                            evidence.market_updates[int(channel.rsplit(":", 1)[1])] += 1
                        except Exception:
                            pass
                    for row in as_rows(payload.get("trades")):
                        evidence.record_trade(row, "trades")
                    for row in as_rows(payload.get("liquidation_trades")):
                        evidence.record_trade(row, "liquidation_trades")
        except Exception as exc:
            evidence.errors[type(exc).__name__] += 1
            attempt += 1
            if time.time() >= deadline or evidence.stop.is_set():
                break
            await asyncio.sleep(min(10, 2 ** min(attempt - 1, 3)))


async def main():
    status, meta = get_json("/api/v1/orderBooks")
    markets = active_perps(meta)
    if status != 200 or not markets:
        print(json.dumps({"probe": "lighter-full-212-public-ws-v1", "order_books_http": status, "active_perp_count": len(markets), "verdict": "METADATA_FAIL"}, sort_keys=True))
        raise SystemExit(2)

    shards = [[] for _ in range(SHARD_COUNT)]
    for index, market_id in enumerate(markets):
        shards[index % SHARD_COUNT].append(market_id)

    evidence = Evidence(markets)
    deadline = time.time() + DURATION_SECONDS
    tasks = [asyncio.create_task(shard_worker(i, shard, evidence, deadline)) for i, shard in enumerate(shards)]

    try:
        while time.time() < deadline and not evidence.stop.is_set():
            await asyncio.sleep(min(5, max(0.1, deadline - time.time())))
    finally:
        evidence.stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    summary = evidence.summary()
    print(json.dumps(summary, indent=2, sort_keys=True))

    # Technical failure only if we could not establish all shards or send every
    # active-market subscription. Absence of rare liquidation/deleverage events
    # is evidence, not an error and must not be converted to zero/PASS.
    if summary["connections_opened"] < SHARD_COUNT or not summary["all_active_markets_subscribed"]:
        raise SystemExit(3)


if __name__ == "__main__":
    asyncio.run(main())
