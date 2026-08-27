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
CONTRACT = "ASXN_REPLAY_SURFACE_CAPABILITY_PROBE_V2"


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


def fetch_variant(page, name: str, target: str, reference: dict[str, Any] | None, summary: dict[str, Any]) -> dict[str, Any]:
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
        "overlap_with_reference": None,
        "older_than_reference_count": None,
        "newer_than_reference_count": None,
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
    if reference is not None:
        ref_fps: set[str] = reference["_fps"]
        ref_oldest: datetime = reference["_oldest"]
        ref_newest: datetime = reference["_newest"]
        result["overlap_with_reference"] = len(fps & ref_fps)
        result["older_than_reference_count"] = sum(1 for t in times if t < ref_oldest)
        result["newer_than_reference_count"] = sum(1 for t in times if t > ref_newest)
    return result


def public_result(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def effective_status(row: dict[str, Any]) -> int:
    return int(row.get("http_status_after_reverify", row.get("http_status") or 0))


def classify(baseline: dict[str, Any], asc100: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {r["name"]: r for r in results}
    historical_surface = (
        effective_status(asc100) == 200
        and int(asc100.get("row_count") or 0) > 0
        and asc100.get("_newest") is not None
        and baseline.get("_oldest") is not None
        and asc100["_newest"] < baseline["_oldest"]
    )

    pagination_candidates: list[str] = []
    for name in ("asc_offset_100", "asc_page_2", "asc_skip_100"):
        r = by_name.get(name)
        if not r or effective_status(r) != 200:
            continue
        count = int(r.get("row_count") or 0)
        overlap = int(r.get("overlap_with_reference") or 0)
        newer = int(r.get("newer_than_reference_count") or 0)
        # Reference is asc100. A page/skip candidate must move materially forward
        # from the earliest 100 instead of returning the same set.
        if count >= 20 and overlap <= count * 0.75 and newer >= max(10, count // 4):
            pagination_candidates.append(name)

    limit_names = ("asc_limit_500", "asc_limit_1000", "asc_limit_2000", "desc_limit_2000")
    honored_limits = [
        name for name in limit_names
        if by_name.get(name) and effective_status(by_name[name]) == 200 and int(by_name[name].get("row_count") or 0) >= int(name.split("_")[-1])
    ]
    max_rows_observed = max(
        [int(r.get("row_count") or 0) for r in results if effective_status(r) == 200] or [0]
    )

    if pagination_candidates:
        classification = "HISTORICAL_RETENTION_PLUS_PAGINATION_CANDIDATE_OBSERVED"
    elif historical_surface:
        classification = "HISTORICAL_RETENTION_OBSERVED_NO_PAGE_OR_CURSOR_PROOF"
    else:
        classification = "NO_DETERMINISTIC_HISTORICAL_RETRIEVAL_PROVEN"

    return {
        "classification": classification,
        "historical_retention_surface_observed": historical_surface,
        "pagination_candidates": pagination_candidates,
        "honored_large_limit_variants": honored_limits,
        "max_rows_observed_in_one_response": max_rows_observed,
        "baseline_oldest_ts": baseline.get("oldest_ts"),
        "earliest_surface_oldest_ts": asc100.get("oldest_ts"),
        "earliest_surface_newest_ts": asc100.get("newest_ts"),
        "truth_limit": "Ascending timestamp sort can prove retained old events, but without deterministic page/cursor/range traversal it cannot bridge the middle of history or repair newest-N turnover. Large one-shot limits are capacity observations, not replay semantics.",
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
                if effective_status(baseline) != 200 or baseline.get("schema_exact") is not True:
                    raise StopProbe("baseline_invalid")

                asc100 = fetch_variant(
                    page,
                    "asc_limit_100",
                    url(sort_by="timestamp_utc", sort_order="asc", limit=100),
                    baseline,
                    summary,
                )
                if effective_status(asc100) != 200 or asc100.get("schema_exact") is not True:
                    raise StopProbe("ascending_history_surface_invalid")

                results = [baseline, asc100]
                variants: list[tuple[str, str, dict[str, Any]]] = [
                    ("asc_limit_500", url(sort_by="timestamp_utc", sort_order="asc", limit=500), asc100),
                    ("asc_limit_1000", url(sort_by="timestamp_utc", sort_order="asc", limit=1000), asc100),
                    ("asc_limit_2000", url(sort_by="timestamp_utc", sort_order="asc", limit=2000), asc100),
                    ("desc_limit_2000", url(sort_by="timestamp_utc", sort_order="desc", limit=2000), baseline),
                    ("asc_offset_100", url(sort_by="timestamp_utc", sort_order="asc", limit=100, offset=100), asc100),
                    ("asc_page_2", url(sort_by="timestamp_utc", sort_order="asc", limit=100, page=2), asc100),
                    ("asc_skip_100", url(sort_by="timestamp_utc", sort_order="asc", limit=100, skip=100), asc100),
                ]
                for name, target, reference in variants:
                    results.append(fetch_variant(page, name, target, reference, summary))

                summary["results"] = [public_result(r) for r in results]
                summary["decision"] = classify(baseline, asc100, results)
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
        print(json.dumps(summary, indent=2, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
