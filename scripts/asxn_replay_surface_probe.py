from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

import asxn_stage3a_canary as core

BASE = "https://api-hyperliquid.asxn.xyz/api/node/liquidations"
OUTPUT = Path("artifacts/asxn-replay-surface/summary.json")
MAX_RSS_BYTES = 2_500_000_000
MAX_PROCESS_COUNT = 32
CONTRACT = "ASXN_REPLAY_SURFACE_CAPABILITY_PROBE_V1"


class StopProbe(RuntimeError):
    pass


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def url(path: str = "", **params: object) -> str:
    base = BASE + path
    clean = {k: str(v) for k, v in params.items() if v is not None}
    return base + (("?" + urlencode(clean)) if clean else "")


def digest(fps: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(fps)).encode("utf-8")).hexdigest()


def validate_rows(rows: Any) -> tuple[list[dict[str, Any]], set[str], list[datetime]]:
    if not isinstance(rows, list) or not rows:
        raise StopProbe("events_not_nonempty_list")
    items = [r for r in rows if isinstance(r, dict)]
    if len(items) != len(rows):
        raise StopProbe("events_non_object_row")
    times: list[datetime] = []
    fps: set[str] = set()
    for row in items:
        if set(row.keys()) != core.EVENT_KEYS:
            raise StopProbe("events_schema_drift")
        if str(row.get("direction")) not in {"LONG LIQ", "SHORT LIQ"}:
            raise StopProbe("events_direction_drift")
        for field in ("notional_volume", "size", "price"):
            core.decimal_value(row.get(field))
        for field in ("timestamp_utc", "symbol", "direction", "address", "counterparty", "txn_hash"):
            if not isinstance(row.get(field), str) or not row.get(field):
                raise StopProbe("events_type_drift")
        parsed = core.parse_iso(row["timestamp_utc"])
        if parsed is None:
            raise StopProbe("events_timestamp_drift")
        times.append(parsed)
        fps.add(core.event_uid(row))
    return items, fps, times


def order_of(times: list[datetime]) -> str:
    if all(a >= b for a, b in zip(times, times[1:])):
        return "desc"
    if all(a <= b for a, b in zip(times, times[1:])):
        return "asc"
    return "mixed"


def fetch_variant(page, name: str, target: str, baseline: dict[str, Any] | None, summary: dict[str, Any]) -> dict[str, Any]:
    status, raw, latency_ms = core.browser_fetch_json(page, target)
    core.observe_resource(summary)
    result: dict[str, Any] = {
        "name": name,
        "http_status": status,
        "latency_ms": round(latency_ms, 3),
        "schema_exact": None,
        "row_count": None,
        "oldest_ts": None,
        "newest_ts": None,
        "window_span_seconds": None,
        "order": None,
        "fingerprint_digest": None,
        "overlap_with_baseline": None,
        "older_than_baseline_count": None,
        "newer_than_baseline_count": None,
    }
    if status == 429:
        raise StopProbe("http_429_provider_pressure")
    if status == 403:
        verified_at, reverify_ms = core.verify_same_context(page, summary, reason=f"variant_{name}")
        result["reverified_at"] = iso(verified_at)
        result["reverify_latency_ms"] = round(reverify_ms, 3)
        status, raw, latency_ms = core.browser_fetch_json(page, target)
        core.observe_resource(summary)
        result["http_status_after_reverify"] = status
        result["latency_after_reverify_ms"] = round(latency_ms, 3)
        if status == 429:
            raise StopProbe("http_429_provider_pressure")
    effective_status = int(result.get("http_status_after_reverify", status))
    if effective_status != 200:
        return result
    items, fps, times = validate_rows(raw)
    oldest = min(times)
    newest = max(times)
    result.update({
        "schema_exact": True,
        "row_count": len(items),
        "oldest_ts": iso(oldest),
        "newest_ts": iso(newest),
        "window_span_seconds": round((newest - oldest).total_seconds(), 3),
        "order": order_of(times),
        "fingerprint_digest": digest(fps),
    })
    result["_fps"] = fps
    result["_oldest"] = oldest
    result["_newest"] = newest
    if baseline is not None:
        base_fps: set[str] = baseline["_fps"]
        base_oldest: datetime = baseline["_oldest"]
        base_newest: datetime = baseline["_newest"]
        result["overlap_with_baseline"] = len(fps & base_fps)
        result["older_than_baseline_count"] = sum(1 for t in times if t < base_oldest)
        result["newer_than_baseline_count"] = sum(1 for t in times if t > base_newest)
    return result


def public_result(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def classify(baseline: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {r["name"]: r for r in results}
    limit_rows = [by_name.get("limit_200"), by_name.get("limit_500")]
    larger = [r for r in limit_rows if r and r.get("http_status") == 200 and int(r.get("row_count") or 0) > 100]

    pagination_names = ("offset_100", "page_2", "before_oldest")
    pagination_candidates: list[str] = []
    for name in pagination_names:
        r = by_name.get(name)
        if not r or int(r.get("http_status_after_reverify", r.get("http_status") or 0)) != 200:
            continue
        row_count = int(r.get("row_count") or 0)
        older = int(r.get("older_than_baseline_count") or 0)
        overlap = int(r.get("overlap_with_baseline") or 0)
        # Require a strong older-window shift, not just normal newest-window drift.
        if row_count >= 20 and older >= max(10, row_count // 4) and overlap <= row_count * 0.75:
            pagination_candidates.append(name)

    symbol_candidates = []
    for name in ("btc_24h_200", "btc_all_500"):
        r = by_name.get(name)
        if r and int(r.get("row_count") or 0) > 100:
            symbol_candidates.append(name)

    if pagination_candidates:
        classification = "REPLAY_OR_PAGINATION_CANDIDATE_OBSERVED"
    elif larger:
        classification = "LARGER_NEWEST_N_WINDOW_OBSERVED_NO_REPLAY_PROOF"
    elif symbol_candidates:
        classification = "SYMBOL_HISTORY_SURFACE_OBSERVED_NO_GLOBAL_REPLAY_PROOF"
    else:
        classification = "NO_REPLAY_OR_PAGINATION_SEMANTICS_PROVEN"

    return {
        "classification": classification,
        "pagination_candidates": pagination_candidates,
        "larger_limit_variants": [r["name"] for r in larger],
        "symbol_history_candidates": symbol_candidates,
        "baseline_row_count": baseline.get("row_count"),
        "truth_limit": "A larger limit is not replay. Only deterministic older-page/cursor/range retrieval can bridge turnover; this probe is capability discovery, not completeness qualification.",
    }


def main() -> None:
    summary: dict[str, Any] = {
        "contract": CONTRACT,
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "source_only": True,
        "started_at": iso(datetime.now(timezone.utc)),
        "raw_events_persisted": False,
        "cookies_persisted": False,
        "tokens_persisted": False,
        "browser_profile_persisted": False,
        "request_count_ceiling": 10,
        "resource_limits": {"max_rss_bytes": MAX_RSS_BYTES, "max_process_count": MAX_PROCESS_COUNT},
        "results": [],
    }
    profile = Path(tempfile.mkdtemp(prefix="asxn-replay-surface-profile-"))
    os.chmod(profile, 0o700)
    exit_code = 0
    try:
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not chrome:
            raise StopProbe("chrome_missing")
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), executable_path=chrome, headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                verified_at, verify_ms = core.verify_same_context(page, summary, reason="initial")
                summary["verified_at"] = iso(verified_at)
                summary["initial_verify_latency_ms"] = round(verify_ms, 3)
                baseline = fetch_variant(page, "baseline", BASE, None, summary)
                if int(baseline.get("http_status_after_reverify", baseline.get("http_status") or 0)) != 200:
                    raise StopProbe("baseline_not_http_200")
                if baseline.get("schema_exact") is not True:
                    raise StopProbe("baseline_schema_invalid")

                variants: list[tuple[str, str]] = [
                    ("limit_200", url(limit=200)),
                    ("limit_500", url(limit=500)),
                    ("sort_timestamp_desc", url(sort_by="timestamp_utc", sort_order="desc", limit=100)),
                    ("sort_timestamp_asc", url(sort_by="timestamp_utc", sort_order="asc", limit=100)),
                    ("offset_100", url(offset=100, limit=100)),
                    ("page_2", url(page=2, limit=100)),
                    ("before_oldest", url(before=baseline["oldest_ts"], limit=100)),
                    ("btc_24h_200", url("/BTC", limit=200, timeframe="24h")),
                    ("btc_all_500", url("/BTC", limit=500, timeframe="all")),
                ]
                results = [baseline]
                for name, target in variants:
                    results.append(fetch_variant(page, name, target, baseline, summary))
                summary["results"] = [public_result(r) for r in results]
                summary["decision"] = classify(baseline, results)
                summary["status"] = "CAPABILITY_PROBE_COMPLETE"
            finally:
                context.close()
    except StopProbe as exc:
        summary["status"] = "FAIL_CLOSED"
        summary["fail_reason"] = str(exc)
        exit_code = 1
    except Exception as exc:
        summary["status"] = "FAIL_CLOSED"
        summary["fail_reason"] = type(exc).__name__
        exit_code = 1
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        summary["ended_at"] = iso(datetime.now(timezone.utc))
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        # Sanitized summary only. No raw events or auth material.
        print(json.dumps(summary, indent=2, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
