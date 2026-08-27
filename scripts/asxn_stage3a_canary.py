from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psutil
from playwright.sync_api import Page, sync_playwright

HOME = "https://hyperscreener.asxn.xyz/"
EVENTS = "https://api-hyperliquid.asxn.xyz/api/node/liquidations"
DAILY = "https://api-hyperliquid.asxn.xyz/api/node/liquidations/chart/daily?timeframe=all"

MAX_DURATION_SECONDS = 7_200
HARD_STOP_UTC = datetime(2026, 8, 26, 14, 0, tzinfo=timezone.utc)  # 21:00 ICT Day-3 interrupt
MAX_RSS_BYTES = 2_500_000_000
MAX_PROCESS_COUNT = 32
MAX_CONSECUTIVE_TRANSPORT_ERRORS = 3
VERIFY_ATTEMPTS = 20
VERIFY_WAIT_MS = 1_500
FETCH_TIMEOUT_MS = 15_000

EVENT_UID_FIELDS = (
    "timestamp_utc", "txn_hash", "address", "counterparty", "symbol",
    "direction", "size", "price", "notional_volume",
)
EVENT_KEYS = set(EVENT_UID_FIELDS)
DAILY_KEYS = {
    "date", "long_liquidations", "short_liquidations",
    "long_unique_addresses", "short_unique_addresses",
    "long_notional", "short_notional",
}
DAILY_NUMERIC_FIELDS = (
    "long_liquidations", "short_liquidations",
    "long_unique_addresses", "short_unique_addresses",
    "long_notional", "short_notional",
)

FETCH_JS = r"""
async ({url, timeoutMs}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      credentials: 'include',
      signal: controller.signal,
    });
    if (response.status !== 200) {
      return {status: response.status, data: null};
    }
    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      return {status: 200, data: null};
    }
    return {status: 200, data};
  } catch (_) {
    return {status: 0, data: null};
  } finally {
    clearTimeout(timer);
  }
}
"""


class ProbeError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def parse_iso(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def canonical_hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def event_uid(event: dict[str, Any]) -> str:
    raw = "\x1f".join(str(event.get(k, "")) for k in EVENT_UID_FIELDS)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint_digest(fps: set[str]) -> str:
    return hashlib.sha256("\n".join(sorted(fps)).encode("utf-8")).hexdigest()


def decimal_value(value: object) -> Decimal:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ProbeError("invalid_decimal") from None
    if not d.is_finite() or d < 0:
        raise ProbeError("invalid_decimal")
    return d


def decimal_add(left: str, right: Decimal) -> str:
    return format(Decimal(left) + right, "f")


def profile_dir() -> Path:
    path = Path(tempfile.mkdtemp(prefix="asxn-stage3a-profile-"))
    os.chmod(path, 0o700)
    return path


def tree_metrics() -> dict[str, int | float]:
    root = psutil.Process()
    procs = [root]
    try:
        procs.extend(root.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

    rss = 0
    cpu = 0.0
    count = 0
    for proc in procs:
        try:
            rss += proc.memory_info().rss
            t = proc.cpu_times()
            cpu += float(t.user + t.system)
            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return {
        "rss_bytes": rss,
        "cpu_seconds": round(cpu, 3),
        "process_count": count,
    }


def observe_resource(summary: dict[str, Any]) -> dict[str, int | float]:
    current = tree_metrics()
    resource = summary.setdefault(
        "resources",
        {
            "max_rss_bytes": 0,
            "max_process_count": 0,
            "max_cpu_seconds_observed": 0.0,
            "rss_fail_closed_limit_bytes": MAX_RSS_BYTES,
            "process_fail_closed_limit": MAX_PROCESS_COUNT,
        },
    )
    resource["max_rss_bytes"] = max(int(resource["max_rss_bytes"]), int(current["rss_bytes"]))
    resource["max_process_count"] = max(
        int(resource["max_process_count"]), int(current["process_count"])
    )
    resource["max_cpu_seconds_observed"] = max(
        float(resource["max_cpu_seconds_observed"]), float(current["cpu_seconds"])
    )
    if int(current["rss_bytes"]) > MAX_RSS_BYTES:
        raise ProbeError("unsafe_resource_rss_growth")
    if int(current["process_count"]) > MAX_PROCESS_COUNT:
        raise ProbeError("unsafe_resource_process_growth")
    return current


def browser_fetch_json(page: Page, url: str) -> tuple[int, Any | None, float]:
    started = time.perf_counter()
    result = page.evaluate(FETCH_JS, {"url": url, "timeoutMs": FETCH_TIMEOUT_MS})
    latency_ms = (time.perf_counter() - started) * 1000.0
    if not isinstance(result, dict):
        return 0, None, latency_ms
    status = int(result.get("status") or 0)
    return status, result.get("data"), latency_ms


def event_window(rows: Any) -> tuple[list[dict[str, Any]], set[str], datetime, datetime, float]:
    if not isinstance(rows, list) or not rows:
        raise ProbeError("events_not_nonempty_list")
    items = [r for r in rows if isinstance(r, dict)]
    if len(items) != len(rows):
        raise ProbeError("events_non_object_row")

    for row in items:
        if set(row.keys()) != EVENT_KEYS:
            raise ProbeError("events_schema_drift")
        if str(row.get("direction")) not in {"LONG LIQ", "SHORT LIQ"}:
            raise ProbeError("events_direction_drift")
        decimal_value(row.get("notional_volume"))
        decimal_value(row.get("size"))
        decimal_value(row.get("price"))
        if not all(
            isinstance(row.get(k), str) and row.get(k)
            for k in (
                "timestamp_utc", "symbol", "direction", "address",
                "counterparty", "txn_hash",
            )
        ):
            raise ProbeError("events_type_drift")

    times = [parse_iso(r["timestamp_utc"]) for r in items]
    if any(t is None for t in times):
        raise ProbeError("events_timestamp_drift")
    typed_times = [t for t in times if t is not None]
    if not all(a >= b for a, b in zip(typed_times, typed_times[1:])):
        raise ProbeError("events_order_drift")
    newest = typed_times[0]
    oldest = typed_times[-1]
    span = max(0.0, (newest - oldest).total_seconds())
    fps = {event_uid(r) for r in items}
    return items, fps, newest, oldest, span


def validate_daily(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows:
        raise ProbeError("daily_not_nonempty_list")
    items = [r for r in rows if isinstance(r, dict)]
    if len(items) != len(rows):
        raise ProbeError("daily_non_object_row")

    dates: list[datetime] = []
    for row in items:
        if set(row.keys()) != DAILY_KEYS:
            raise ProbeError("daily_schema_drift")
        dt = parse_iso(row.get("date"))
        if dt is None:
            raise ProbeError("daily_date_drift")
        dates.append(dt)
        for field in DAILY_NUMERIC_FIELDS:
            decimal_value(row.get(field))
    if not all(a <= b for a, b in zip(dates, dates[1:])):
        raise ProbeError("daily_order_drift")
    return items


def adaptive_delay(window_span_seconds: float, row_count: int) -> float:
    if row_count >= 95:
        if window_span_seconds <= 120:
            base = 15.0
        elif window_span_seconds <= 300:
            base = 25.0
        elif window_span_seconds <= 900:
            base = 35.0
        else:
            base = 60.0
    else:
        base = 60.0
    return max(10.0, base + random.uniform(-2.0, 2.0))


def ensure_open_event_interval(state: dict[str, Any], oldest: datetime, observed_at: datetime) -> None:
    intervals = state.setdefault("coverage_intervals_strict", [])
    if intervals and intervals[-1].get("open"):
        intervals[-1]["observed_through"] = iso(observed_at)
        return
    intervals.append(
        {"start": iso(oldest), "observed_through": iso(observed_at), "open": True}
    )


def record_event_gap(state: dict[str, Any], oldest: datetime, observed_at: datetime) -> None:
    intervals = state.setdefault("coverage_intervals_strict", [])
    if intervals and intervals[-1].get("open"):
        intervals[-1]["open"] = False
    intervals.append(
        {"start": iso(oldest), "observed_through": iso(observed_at), "open": True}
    )
    state["zero_overlap_count"] = int(state.get("zero_overlap_count", 0)) + 1
    dates = set(state.get("zero_overlap_dates", []))
    dates.add(observed_at.date().isoformat())
    state["zero_overlap_dates"] = sorted(dates)


def mark_transport_success(state: dict[str, Any], observed_at: datetime) -> None:
    intervals = state.setdefault("transport_intervals", [])
    if intervals and intervals[-1].get("open"):
        intervals[-1]["observed_through"] = iso(observed_at)
        return
    intervals.append(
        {
            "start": iso(observed_at),
            "observed_through": iso(observed_at),
            "open": True,
        }
    )


def mark_transport_break(state: dict[str, Any], observed_at: datetime, reason: str) -> None:
    intervals = state.setdefault("transport_intervals", [])
    if intervals and intervals[-1].get("open"):
        intervals[-1]["open"] = False
        intervals[-1]["break_observed_at"] = iso(observed_at)
        intervals[-1]["break_reason"] = reason


def aggregate_new_events(
    state: dict[str, Any],
    items: list[dict[str, Any]],
    seen_by_day: dict[str, set[str]],
) -> int:
    added = 0
    daily = state.setdefault("event_daily", {})
    for row in items:
        ts = parse_iso(row["timestamp_utc"])
        if ts is None:
            continue
        day = ts.date().isoformat()
        fp = event_uid(row)
        seen = seen_by_day.setdefault(day, set())
        if fp in seen:
            continue
        seen.add(fp)
        bucket = daily.setdefault(
            day,
            {
                "long_count": 0,
                "short_count": 0,
                "long_notional": "0",
                "short_notional": "0",
            },
        )
        side = "long" if row["direction"] == "LONG LIQ" else "short"
        bucket[f"{side}_count"] = int(bucket[f"{side}_count"]) + 1
        bucket[f"{side}_notional"] = decimal_add(
            str(bucket[f"{side}_notional"]),
            decimal_value(row["notional_volume"]),
        )
        added += 1
    return added


def verify_same_context(
    page: Page,
    summary: dict[str, Any],
    *,
    reason: str,
) -> tuple[datetime, float]:
    verify_started = time.perf_counter()
    verification = summary.setdefault(
        "verification",
        {
            "count": 0,
            "server_time_200": 0,
            "verify_200": 0,
            "protected_403_seen": 0,
            "reverify_latencies_ms": [],
            "session_expiry_observations_ms": [],
        },
    )
    verification["count"] = int(verification["count"]) + 1

    page.goto(HOME, wait_until="domcontentloaded", timeout=45_000)
    observe_resource(summary)
    status = 0
    for _ in range(VERIFY_ATTEMPTS):
        status, _, _ = browser_fetch_json(page, EVENTS)
        observe_resource(summary)
        if status == 200:
            verified_at = utc_now()
            latency_ms = (time.perf_counter() - verify_started) * 1000.0
            if reason != "initial":
                verification["reverify_latencies_ms"].append(round(latency_ms, 3))
            return verified_at, latency_ms
        if status == 429:
            raise ProbeError("verification_http_429")
        page.wait_for_timeout(VERIFY_WAIT_MS)
    raise ProbeError(f"browser_verification_status_{status}")


def refresh_daily(
    page: Page,
    state: dict[str, Any],
    summary: dict[str, Any],
) -> int:
    status, raw, latency_ms = browser_fetch_json(page, DAILY)
    counts = summary.setdefault("http_status_counts", {})
    counts[f"daily_{status}"] = int(counts.get(f"daily_{status}", 0)) + 1
    summary.setdefault("daily_fetch_latencies_ms", []).append(round(latency_ms, 3))

    if status != 200:
        return status

    items = validate_daily(raw)
    dates = [parse_iso(r["date"]) for r in items]
    typed_dates = [d for d in dates if d is not None]
    missing = sum(
        max(0, (b.date() - a.date()).days - 1)
        for a, b in zip(typed_dates, typed_dates[1:])
    )
    duplicate_dates = len(typed_dates) - len({d.date() for d in typed_dates})
    summary["daily_snapshot"] = {
        "row_count": len(items),
        "first_date": items[0]["date"],
        "last_date": items[-1]["date"],
        "missing_date_count": missing,
        "duplicate_date_count": duplicate_dates,
    }

    observed_at = iso(utc_now())
    provider_hashes = state.setdefault("provider_daily_hashes", {})
    revised = set(state.get("provider_revised_dates", []))
    for row in items[-4:]:
        dt = parse_iso(row["date"])
        if dt is None:
            raise ProbeError("daily_date_drift")
        day = dt.date().isoformat()
        digest = canonical_hash(row)
        prior = provider_hashes.get(day, {})
        if prior.get("hash") and prior.get("hash") != digest:
            state["provider_revision_count"] = int(
                state.get("provider_revision_count", 0)
            ) + 1
            revised.add(day)
        provider_hashes[day] = {"hash": digest, "observed_at": observed_at}
    state["provider_revised_dates"] = sorted(revised)
    return 200


def classification(summary: dict[str, Any], full_duration_completed: bool) -> str:
    metrics = summary.get("event_metrics", {})
    clean_zero = int(metrics.get("clean_200_to_200_zero_overlap", 0))
    total_zero = int(metrics.get("zero_overlap_count_this_run", 0))
    if clean_zero > 0:
        return "CASE_A_INTRINSIC_TURNOVER_CADENCE_BLOCKER"
    if total_zero > 0:
        return "BOUNDARY_ONLY_ZERO_OVERLAP_BASELINE_LATENCY_COMPARE_REQUIRED"
    if full_duration_completed:
        return "CASE_C_CANARY_PROMISING"
    return "INCOMPLETE_FAIL_CLOSED"


def self_test() -> None:
    base = {
        "timestamp_utc": "2026-08-26T00:00:02Z",
        "txn_hash": "tx-a",
        "address": "addr-a",
        "counterparty": "cp-a",
        "symbol": "BTC",
        "direction": "LONG LIQ",
        "size": "1",
        "price": "1",
        "notional_volume": "1",
    }
    older = dict(base)
    older["timestamp_utc"] = "2026-08-26T00:00:01Z"
    older["txn_hash"] = "tx-b"
    items, fps, newest, oldest, span = event_window([base, older])
    assert len(items) == 2 and len(fps) == 2 and newest > oldest and span == 1.0

    state: dict[str, Any] = {}
    ensure_open_event_interval(state, oldest, newest)
    assert len(state["coverage_intervals_strict"]) == 1
    record_event_gap(state, oldest, newest + timedelta(seconds=1))
    assert state["zero_overlap_count"] == 1
    assert len(state["coverage_intervals_strict"]) == 2

    random.seed(1)
    assert adaptive_delay(3.0, 100) >= 10.0

    assert classification(
        {"event_metrics": {"clean_200_to_200_zero_overlap": 1, "zero_overlap_count_this_run": 1}},
        False,
    ).startswith("CASE_A")
    assert classification(
        {"event_metrics": {"clean_200_to_200_zero_overlap": 0, "zero_overlap_count_this_run": 0}},
        True,
    ) == "CASE_C_CANARY_PROMISING"

    print("STAGE3A_SELF_TEST=PASS")


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    started = utc_now()
    summary: dict[str, Any] = {
        "probe": "asxn-stage3a-persistent-browser-canary-v1",
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "started_at": iso(started),
        "requested_duration_seconds": int(args.duration_seconds),
        "hard_stop_utc": iso(HARD_STOP_UTC),
        "source_only": True,
        "persistent_browser_context": True,
        "browser_fetch_credentials_include": True,
        "raw_events_persisted": False,
        "cookies_persisted": False,
        "tokens_persisted": False,
        "browser_profile_persisted": False,
        "proactive_refresh_enabled": False,
        "stage2_event_overlap_semantics_preserved": True,
        "transitions": [],
    }
    state: dict[str, Any] = {
        "version": 2,
        "coverage_intervals_strict": [],
        "transport_intervals": [],
        "zero_overlap_count": 0,
        "zero_overlap_dates": [],
        "window_turnover_count": 0,
        "poll_success_count": 0,
        "poll_403_count": 0,
        "poll_429_count": 0,
        "transport_error_count": 0,
        "reverify_count": 0,
        "provider_daily_hashes": {},
        "provider_revision_count": 0,
        "provider_revised_dates": [],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if started >= HARD_STOP_UTC:
        summary["status"] = "STOPPED_FOR_DAY3_AUDIT_BOUNDARY"
        summary["classification"] = "NOT_RUN"
        summary["ended_at"] = iso(utc_now())
        output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary, 0

    target_end = min(
        started + timedelta(seconds=args.duration_seconds),
        HARD_STOP_UTC,
    )
    full_requested_end = started + timedelta(seconds=args.duration_seconds)
    profile = profile_dir()
    exit_code = 0
    full_duration_completed = False

    prev_fps: set[str] = set()
    prev_success_at: datetime | None = None
    seen_by_day: dict[str, set[str]] = {}
    next_daily_at = started
    verified_at: datetime | None = None
    consecutive_transport_errors = 0
    boundary_403_since_success = False
    boundary_reverify_since_success = False
    boundary_reverify_latency_ms: float | None = None

    try:
        chrome = (
            shutil.which("google-chrome")
            or shutil.which("chromium")
            or shutil.which("chromium-browser")
        )
        if not chrome:
            raise ProbeError("chrome_missing")

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                executable_path=chrome,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = context.pages[0] if context.pages else context.new_page()

            verification = summary.setdefault(
                "verification",
                {
                    "count": 0,
                    "server_time_200": 0,
                    "verify_200": 0,
                    "protected_403_seen": 0,
                    "reverify_latencies_ms": [],
                    "session_expiry_observations_ms": [],
                },
            )

            def record_response(resp: Any) -> None:
                url = str(resp.url)
                if "/server-time" in url and resp.status == 200:
                    verification["server_time_200"] = int(
                        verification["server_time_200"]
                    ) + 1
                elif "/verify" in url and resp.status == 200:
                    verification["verify_200"] = int(verification["verify_200"]) + 1
                elif "api-hyperliquid.asxn.xyz" in url and resp.status == 403:
                    verification["protected_403_seen"] = int(
                        verification["protected_403_seen"]
                    ) + 1

            page.on("response", record_response)
            try:
                verified_at, _ = verify_same_context(
                    page, summary, reason="initial"
                )
                state["reverify_count"] = 0
                observe_resource(summary)

                while utc_now() < target_end:
                    now = utc_now()
                    current_resource = observe_resource(summary)

                    if now >= next_daily_at:
                        daily_status = refresh_daily(page, state, summary)
                        if daily_status == 403:
                            mark_transport_break(state, now, "daily_403")
                            state["poll_403_count"] = int(state["poll_403_count"]) + 1
                            boundary_403_since_success = True
                            if verified_at:
                                age_ms = (now - verified_at).total_seconds() * 1000.0
                                verification["session_expiry_observations_ms"].append(
                                    round(age_ms, 3)
                                )
                            verified_at, reverify_latency = verify_same_context(
                                page, summary, reason="daily_403"
                            )
                            state["reverify_count"] = int(state["reverify_count"]) + 1
                            boundary_reverify_since_success = True
                            boundary_reverify_latency_ms = reverify_latency
                            retry_status = refresh_daily(page, state, summary)
                            if retry_status != 200:
                                raise ProbeError(
                                    f"daily_retry_after_reverify_status_{retry_status}"
                                )
                        elif daily_status == 429:
                            state["poll_429_count"] = int(state["poll_429_count"]) + 1
                            raise ProbeError("provider_pressure_http_429")
                        elif daily_status != 200:
                            state["transport_error_count"] = int(
                                state["transport_error_count"]
                            ) + 1
                            raise ProbeError(
                                f"daily_unexpected_status_{daily_status}"
                            )
                        next_daily_at = now + timedelta(hours=4)

                    poll_started_at = utc_now()
                    status, raw, poll_latency_ms = browser_fetch_json(page, EVENTS)
                    counts = summary.setdefault("http_status_counts", {})
                    counts[f"events_{status}"] = int(
                        counts.get(f"events_{status}", 0)
                    ) + 1

                    if status == 403:
                        mark_transport_break(state, poll_started_at, "events_403")
                        state["poll_403_count"] = int(state["poll_403_count"]) + 1
                        boundary_403_since_success = True
                        if verified_at:
                            age_ms = (
                                poll_started_at - verified_at
                            ).total_seconds() * 1000.0
                            verification["session_expiry_observations_ms"].append(
                                round(age_ms, 3)
                            )
                        verified_at, reverify_latency = verify_same_context(
                            page, summary, reason="events_403"
                        )
                        state["reverify_count"] = int(state["reverify_count"]) + 1
                        boundary_reverify_since_success = True
                        boundary_reverify_latency_ms = reverify_latency

                        retry_started_at = utc_now()
                        status, raw, poll_latency_ms = browser_fetch_json(page, EVENTS)
                        counts[f"events_retry_{status}"] = int(
                            counts.get(f"events_retry_{status}", 0)
                        ) + 1
                        poll_started_at = retry_started_at
                        if status == 403:
                            raise ProbeError("events_retry_after_reverify_403")

                    if status == 429:
                        state["poll_429_count"] = int(state["poll_429_count"]) + 1
                        mark_transport_break(state, poll_started_at, "events_429")
                        raise ProbeError("provider_pressure_http_429")

                    if status != 200:
                        state["transport_error_count"] = int(
                            state["transport_error_count"]
                        ) + 1
                        consecutive_transport_errors += 1
                        mark_transport_break(
                            state, poll_started_at, f"events_status_{status}"
                        )
                        if (
                            consecutive_transport_errors
                            >= MAX_CONSECUTIVE_TRANSPORT_ERRORS
                        ):
                            raise ProbeError("transport_errors_bounded_stop")
                        remaining = max(
                            0.0, (target_end - utc_now()).total_seconds()
                        )
                        if remaining <= 0:
                            break
                        time.sleep(min(30.0, remaining))
                        continue

                    consecutive_transport_errors = 0
                    observed_at = utc_now()
                    mark_transport_success(state, observed_at)
                    items, fps, newest, oldest, span = event_window(raw)
                    overlap = len(prev_fps & fps) if prev_fps else None

                    if prev_fps and overlap == 0:
                        record_event_gap(state, oldest, observed_at)
                        state["window_turnover_count"] = int(
                            state["window_turnover_count"]
                        ) + 1
                    else:
                        ensure_open_event_interval(state, oldest, observed_at)

                    unique_added = aggregate_new_events(
                        state, items, seen_by_day
                    )
                    state["poll_success_count"] = int(
                        state["poll_success_count"]
                    ) + 1
                    current_resource = observe_resource(summary)

                    event_metrics = summary.setdefault(
                        "event_metrics",
                        {
                            "polls": 0,
                            "rows_total": 0,
                            "unique_added_total": 0,
                            "duplicate_rows_total": 0,
                            "min_window_span_seconds": None,
                            "max_window_span_seconds": 0.0,
                            "min_overlap_count": None,
                            "zero_overlap_count_this_run": 0,
                            "clean_200_to_200_zero_overlap": 0,
                            "verification_boundary_zero_overlap": 0,
                        },
                    )
                    event_metrics["polls"] += 1
                    event_metrics["rows_total"] += len(items)
                    event_metrics["unique_added_total"] += unique_added
                    event_metrics["duplicate_rows_total"] += len(items) - len(fps)
                    event_metrics["min_window_span_seconds"] = (
                        span
                        if event_metrics["min_window_span_seconds"] is None
                        else min(
                            float(event_metrics["min_window_span_seconds"]),
                            span,
                        )
                    )
                    event_metrics["max_window_span_seconds"] = max(
                        float(event_metrics["max_window_span_seconds"]), span
                    )

                    if overlap is not None:
                        event_metrics["min_overlap_count"] = (
                            overlap
                            if event_metrics["min_overlap_count"] is None
                            else min(
                                int(event_metrics["min_overlap_count"]),
                                overlap,
                            )
                        )
                        if overlap == 0:
                            event_metrics["zero_overlap_count_this_run"] += 1
                            if (
                                not boundary_403_since_success
                                and not boundary_reverify_since_success
                            ):
                                event_metrics[
                                    "clean_200_to_200_zero_overlap"
                                ] += 1
                            else:
                                event_metrics[
                                    "verification_boundary_zero_overlap"
                                ] += 1

                    delay = adaptive_delay(span, len(items))
                    session_age_ms = (
                        (observed_at - verified_at).total_seconds() * 1000.0
                        if verified_at
                        else None
                    )
                    elapsed_ms = (
                        (observed_at - prev_success_at).total_seconds() * 1000.0
                        if prev_success_at
                        else None
                    )
                    transition = {
                        "observed_at": iso(observed_at),
                        "poll_latency_ms": round(poll_latency_ms, 3),
                        "elapsed_since_previous_success_ms": (
                            round(elapsed_ms, 3)
                            if elapsed_ms is not None
                            else None
                        ),
                        "row_count": len(items),
                        "window_newest_ts": iso(newest),
                        "window_oldest_ts": iso(oldest),
                        "window_span_ms": round(span * 1000.0, 3),
                        "previous_window_digest": (
                            fingerprint_digest(prev_fps) if prev_fps else None
                        ),
                        "current_window_digest": fingerprint_digest(fps),
                        "overlap_count": overlap,
                        "http_status": 200,
                        "403_since_previous_success": boundary_403_since_success,
                        "reverify_since_previous_success": boundary_reverify_since_success,
                        "reverify_latency_ms": (
                            round(boundary_reverify_latency_ms, 3)
                            if boundary_reverify_latency_ms is not None
                            else None
                        ),
                        "session_age_ms": (
                            round(session_age_ms, 3)
                            if session_age_ms is not None
                            else None
                        ),
                        "poll_delay_ms": round(delay * 1000.0, 3),
                        "resource_metrics": current_resource,
                    }
                    summary["transitions"].append(transition)

                    prev_fps = fps
                    prev_success_at = observed_at
                    boundary_403_since_success = False
                    boundary_reverify_since_success = False
                    boundary_reverify_latency_ms = None

                    if (
                        int(
                            event_metrics.get(
                                "clean_200_to_200_zero_overlap", 0
                            )
                        )
                        > 0
                    ):
                        summary["status"] = "EARLY_CLASSIFICATION_COMPLETE"
                        break

                    remaining = max(
                        0.0, (target_end - utc_now()).total_seconds()
                    )
                    if remaining <= 0:
                        break
                    time.sleep(min(delay, remaining))

                ended_loop = utc_now()
                full_duration_completed = (
                    ended_loop >= full_requested_end - timedelta(seconds=2)
                    and full_requested_end <= HARD_STOP_UTC
                )
                if "status" not in summary:
                    if ended_loop >= HARD_STOP_UTC and full_requested_end > HARD_STOP_UTC:
                        summary["status"] = "STOPPED_FOR_DAY3_AUDIT_BOUNDARY"
                    else:
                        summary["status"] = "CANARY_COMPLETE"
            finally:
                context.close()

    except ProbeError as exc:
        summary["status"] = "FAIL_CLOSED"
        summary["error"] = str(exc)
        exit_code = 2
    except Exception as exc:
        summary["status"] = "FAIL_CLOSED"
        summary["error"] = type(exc).__name__
        exit_code = 3
    finally:
        shutil.rmtree(profile, ignore_errors=True)

        summary["ended_at"] = iso(utc_now())
        summary["full_requested_duration_completed"] = full_duration_completed
        summary["classification"] = (
            classification(summary, full_duration_completed)
            if summary.get("status") != "FAIL_CLOSED"
            else "FAIL_CLOSED"
        )
        summary["state"] = {
            "version": 2,
            "coverage_intervals_strict": state.get(
                "coverage_intervals_strict", []
            ),
            "transport_intervals": state.get("transport_intervals", []),
            "zero_overlap_count": state.get("zero_overlap_count", 0),
            "zero_overlap_dates": state.get("zero_overlap_dates", []),
            "window_turnover_count": state.get("window_turnover_count", 0),
            "poll_success_count": state.get("poll_success_count", 0),
            "poll_403_count": state.get("poll_403_count", 0),
            "poll_429_count": state.get("poll_429_count", 0),
            "transport_error_count": state.get("transport_error_count", 0),
            "reverify_count": state.get("reverify_count", 0),
            "provider_daily_hashes": state.get("provider_daily_hashes", {}),
            "provider_revision_count": state.get(
                "provider_revision_count", 0
            ),
            "provider_revised_dates": state.get(
                "provider_revised_dates", []
            ),
            "unique_event_counts_by_day": {
                day: len(values) for day, values in seen_by_day.items()
            },
        }
        output.write_text(
            json.dumps(summary, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps(summary, indent=2, sort_keys=True))

    return summary, exit_code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument(
        "--output",
        default="artifacts/asxn-stage3a/summary.json",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        raise SystemExit(0)

    if args.duration_seconds is None:
        raise SystemExit("--duration-seconds is required")
    if args.duration_seconds <= 0 or args.duration_seconds > MAX_DURATION_SECONDS:
        raise SystemExit("duration out of bounds")
    _, code = run(args)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
