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
import requests
from playwright.sync_api import sync_playwright

HOME = "https://hyperscreener.asxn.xyz/"
EVENTS = "https://api-hyperliquid.asxn.xyz/api/node/liquidations"
DAILY = "https://api-hyperliquid.asxn.xyz/api/node/liquidations/chart/daily?timeframe=all"

CAMPAIGN_START = datetime(2026, 8, 23, 7, 20, tzinfo=timezone.utc)
CAMPAIGN_END = datetime(2026, 8, 25, 7, 20, tzinfo=timezone.utc)

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


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "campaign_start_utc": iso(CAMPAIGN_START),
            "campaign_end_utc": iso(CAMPAIGN_END),
            "last_window_fps": [],
            "seen_fps_by_day": {},
            "event_daily": {},
            "provider_daily_hashes": {},
            "provider_revision_count": 0,
            "provider_revised_dates": [],
            "coverage_intervals": [],
            "gap_count": 0,
            "gap_dates": [],
            "poll_success_count": 0,
            "poll_403_count": 0,
            "poll_429_count": 0,
            "reverify_count": 0,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        raise ProbeError("state_unreadable") from None
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ProbeError("state_version")
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


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
    return {"rss_bytes": rss, "cpu_seconds": round(cpu, 3), "process_count": count}


def observe_resource(summary: dict[str, Any]) -> None:
    m = tree_metrics()
    r = summary.setdefault("resources", {
        "max_rss_bytes": 0,
        "max_process_count": 0,
        "max_cpu_seconds_observed": 0.0,
    })
    r["max_rss_bytes"] = max(int(r["max_rss_bytes"]), int(m["rss_bytes"]))
    r["max_process_count"] = max(int(r["max_process_count"]), int(m["process_count"]))
    r["max_cpu_seconds_observed"] = max(float(r["max_cpu_seconds_observed"]), float(m["cpu_seconds"]))


def provider_profile_dir() -> Path:
    path = Path(tempfile.mkdtemp(prefix="asxn-stage2-profile-"))
    os.chmod(path, 0o700)
    return path


def build_verified_session(profile: Path, summary: dict[str, Any]) -> requests.Session:
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    if not chrome:
        raise ProbeError("chrome_missing")

    verify_counts = summary.setdefault("verification", {
        "bootstrap_count": 0,
        "server_time_200": 0,
        "verify_200": 0,
        "protected_403_seen": 0,
    })
    verify_counts["bootstrap_count"] += 1

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            executable_path=chrome,
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        def record_response(resp):
            url = resp.url
            if "/server-time" in url and resp.status == 200:
                verify_counts["server_time_200"] += 1
            elif "/verify" in url and resp.status == 200:
                verify_counts["verify_200"] += 1
            elif "api-hyperliquid.asxn.xyz" in url and resp.status == 403:
                verify_counts["protected_403_seen"] += 1

        page.on("response", record_response)
        try:
            page.goto(HOME, wait_until="domcontentloaded", timeout=45_000)
            status = None
            for _ in range(20):
                status = page.evaluate(
                    """async (url) => {
                        try {
                            const r = await fetch(url, {credentials: 'include'});
                            return r.status;
                        } catch (_) {
                            return 0;
                        }
                    }""",
                    EVENTS,
                )
                if status == 200:
                    break
                page.wait_for_timeout(1500)
            if status != 200:
                raise ProbeError(f"browser_verification_status_{status}")

            session = requests.Session()
            for cookie in context.cookies():
                session.cookies.set(
                    cookie["name"],
                    cookie["value"],
                    domain=cookie.get("domain") or None,
                    path=cookie.get("path") or "/",
                )
            ua = page.evaluate("navigator.userAgent")
            session.headers.update({
                "User-Agent": ua,
                "Accept": "application/json,text/plain,*/*",
                "Referer": HOME,
                "Origin": "https://hyperscreener.asxn.xyz",
            })
            observe_resource(summary)
        finally:
            context.close()

    probe = session.get(EVENTS, timeout=15)
    if probe.status_code != 200:
        raise ProbeError(f"lightweight_reuse_status_{probe.status_code}")
    return session


def request_json(session: requests.Session, url: str) -> tuple[int, Any | None]:
    try:
        resp = session.get(url, timeout=15)
    except requests.RequestException:
        return -1, None
    if resp.status_code != 200:
        return resp.status_code, None
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, None


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
        if not all(isinstance(row.get(k), str) and row.get(k) for k in (
            "timestamp_utc", "symbol", "direction", "address", "counterparty", "txn_hash"
        )):
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


def ensure_open_interval(state: dict[str, Any], oldest: datetime, observed_at: datetime) -> None:
    intervals = state.setdefault("coverage_intervals", [])
    if intervals and intervals[-1].get("open"):
        intervals[-1]["observed_through"] = iso(observed_at)
        return
    intervals.append({
        "start": iso(oldest),
        "observed_through": iso(observed_at),
        "open": True,
    })


def record_gap(state: dict[str, Any], oldest: datetime, observed_at: datetime) -> None:
    intervals = state.setdefault("coverage_intervals", [])
    if intervals and intervals[-1].get("open"):
        intervals[-1]["open"] = False
    intervals.append({
        "start": iso(oldest),
        "observed_through": iso(observed_at),
        "open": True,
    })
    state["gap_count"] = int(state.get("gap_count", 0)) + 1
    day = observed_at.date().isoformat()
    dates = set(state.get("gap_dates", []))
    dates.add(day)
    state["gap_dates"] = sorted(dates)


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
        bucket = daily.setdefault(day, {
            "long_count": 0,
            "short_count": 0,
            "long_notional": "0",
            "short_notional": "0",
        })
        side = "long" if row["direction"] == "LONG LIQ" else "short"
        bucket[f"{side}_count"] = int(bucket[f"{side}_count"]) + 1
        bucket[f"{side}_notional"] = decimal_add(
            str(bucket[f"{side}_notional"]),
            decimal_value(row["notional_volume"]),
        )
        added += 1
    return added


def coverage_complete_for_day(state: dict[str, Any], day: str) -> bool:
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    for interval in state.get("coverage_intervals", []):
        i_start = parse_iso(interval.get("start"))
        i_end = parse_iso(interval.get("observed_through"))
        if i_start and i_end and i_start <= start and i_end >= end:
            return True
    return False


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


def refresh_daily(
    session: requests.Session,
    state: dict[str, Any],
    summary: dict[str, Any],
) -> None:
    status, raw = request_json(session, DAILY)
    summary.setdefault("http_status_counts", {})[f"daily_{status}"] = (
        int(summary.setdefault("http_status_counts", {}).get(f"daily_{status}", 0)) + 1
    )
    if status == 403:
        raise ProbeError("daily_403_requires_reverify")
    if status == 429:
        state["poll_429_count"] = int(state.get("poll_429_count", 0)) + 1
        return
    if status != 200:
        return

    items = validate_daily(raw)
    dates = [parse_iso(r["date"]) for r in items]
    typed_dates = [d for d in dates if d is not None]
    missing = sum(max(0, (b.date() - a.date()).days - 1) for a, b in zip(typed_dates, typed_dates[1:]))
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
        day = parse_iso(row["date"]).date().isoformat()
        h = canonical_hash(row)
        prior = provider_hashes.get(day, {})
        if prior.get("hash") and prior.get("hash") != h:
            state["provider_revision_count"] = int(state.get("provider_revision_count", 0)) + 1
            revised.add(day)
        provider_hashes[day] = {"hash": h, "observed_at": observed_at}
    state["provider_revised_dates"] = sorted(revised)

    rows_by_day = {parse_iso(r["date"]).date().isoformat(): r for r in items}
    reconciliation: dict[str, Any] = {}
    for day, agg in state.get("event_daily", {}).items():
        if day >= utc_now().date().isoformat():
            continue
        if not coverage_complete_for_day(state, day):
            continue
        provider = rows_by_day.get(day)
        if not provider:
            reconciliation[day] = {"status": "provider_day_missing"}
            continue
        reconciliation[day] = {
            "status": "candidate_complete_interval",
            "long_count_delta": int(agg["long_count"]) - int(provider["long_liquidations"]),
            "short_count_delta": int(agg["short_count"]) - int(provider["short_liquidations"]),
            "long_notional_delta": format(
                Decimal(str(agg["long_notional"])) - Decimal(str(provider["long_notional"])), "f"
            ),
            "short_notional_delta": format(
                Decimal(str(agg["short_notional"])) - Decimal(str(provider["short_notional"])), "f"
            ),
        }
    summary["daily_reconciliation"] = reconciliation


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


def run(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    started = utc_now()
    summary: dict[str, Any] = {
        "probe": "asxn-stage2-source-only-soak-v1",
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "started_at": iso(started),
        "campaign_start_utc": iso(CAMPAIGN_START),
        "campaign_end_utc": iso(CAMPAIGN_END),
        "requested_duration_seconds": int(args.duration_seconds),
        "source_only": True,
        "raw_events_persisted": False,
        "credentials_logged": False,
    }

    state_path = Path(args.state_path)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if started < CAMPAIGN_START or started >= CAMPAIGN_END:
        summary["status"] = "OUTSIDE_CAMPAIGN_WINDOW"
        summary["ended_at"] = iso(utc_now())
        out_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return summary, 0

    state = load_state(state_path)
    seen_by_day = {
        day: set(values)
        for day, values in state.get("seen_fps_by_day", {}).items()
        if isinstance(values, list)
    }
    profile = provider_profile_dir()
    session: requests.Session | None = None
    target_end = min(started + timedelta(seconds=args.duration_seconds), CAMPAIGN_END)
    next_daily_at = started
    session_started_at: datetime | None = None
    max_session_age = 0.0
    consecutive_429 = 0
    exit_code = 0

    try:
        session = build_verified_session(profile, summary)
        session_started_at = utc_now()
        state["reverify_count"] = int(state.get("reverify_count", 0)) + 1
        next_daily_at = utc_now()

        while utc_now() < target_end:
            now = utc_now()
            observe_resource(summary)
            if now >= next_daily_at:
                try:
                    refresh_daily(session, state, summary)
                except ProbeError as exc:
                    if str(exc) == "daily_403_requires_reverify":
                        state["poll_403_count"] = int(state.get("poll_403_count", 0)) + 1
                        if session_started_at:
                            max_session_age = max(max_session_age, (now - session_started_at).total_seconds())
                        session.close()
                        session = build_verified_session(profile, summary)
                        session_started_at = utc_now()
                        state["reverify_count"] = int(state.get("reverify_count", 0)) + 1
                        refresh_daily(session, state, summary)
                    else:
                        raise
                next_daily_at = now + timedelta(hours=4)

            status, raw = request_json(session, EVENTS)
            key = f"events_{status}"
            counts = summary.setdefault("http_status_counts", {})
            counts[key] = int(counts.get(key, 0)) + 1

            if status == 403:
                state["poll_403_count"] = int(state.get("poll_403_count", 0)) + 1
                if session_started_at:
                    max_session_age = max(max_session_age, (now - session_started_at).total_seconds())
                session.close()
                session = build_verified_session(profile, summary)
                session_started_at = utc_now()
                state["reverify_count"] = int(state.get("reverify_count", 0)) + 1
                continue

            if status == 429:
                state["poll_429_count"] = int(state.get("poll_429_count", 0)) + 1
                consecutive_429 += 1
                backoff = min(300.0, 30.0 * (2 ** min(consecutive_429, 3)))
                time.sleep(backoff)
                continue

            if status != 200:
                time.sleep(30.0)
                continue

            consecutive_429 = 0
            items, fps, newest, oldest, span = event_window(raw)
            prev = set(state.get("last_window_fps", []))
            overlap = len(prev & fps) if prev else None
            if prev and overlap == 0:
                record_gap(state, oldest, now)
            else:
                ensure_open_interval(state, oldest, now)

            unique_added = aggregate_new_events(state, items, seen_by_day)
            state["last_window_fps"] = sorted(fps)
            state["poll_success_count"] = int(state.get("poll_success_count", 0)) + 1
            state["last_success_at"] = iso(now)
            state["last_newest_event_at"] = iso(newest)
            state["last_oldest_event_at"] = iso(oldest)

            event_metrics = summary.setdefault("event_metrics", {
                "polls": 0,
                "rows_total": 0,
                "unique_added_total": 0,
                "duplicate_rows_total": 0,
                "min_window_span_seconds": None,
                "max_window_span_seconds": 0.0,
                "min_overlap_count": None,
                "gap_count_this_run": 0,
            })
            event_metrics["polls"] += 1
            event_metrics["rows_total"] += len(items)
            event_metrics["unique_added_total"] += unique_added
            event_metrics["duplicate_rows_total"] += len(items) - len(fps)
            event_metrics["min_window_span_seconds"] = (
                span if event_metrics["min_window_span_seconds"] is None
                else min(float(event_metrics["min_window_span_seconds"]), span)
            )
            event_metrics["max_window_span_seconds"] = max(float(event_metrics["max_window_span_seconds"]), span)
            if overlap is not None:
                event_metrics["min_overlap_count"] = (
                    overlap if event_metrics["min_overlap_count"] is None
                    else min(int(event_metrics["min_overlap_count"]), overlap)
                )
                if overlap == 0:
                    event_metrics["gap_count_this_run"] += 1

            delay = adaptive_delay(span, len(items))
            remaining = max(0.0, (target_end - utc_now()).total_seconds())
            if remaining <= 0:
                break
            time.sleep(min(delay, remaining))

        if session_started_at:
            max_session_age = max(max_session_age, (utc_now() - session_started_at).total_seconds())
        summary["status"] = "SOAK_SEGMENT_COMPLETE"
    except ProbeError as exc:
        summary["status"] = "FAIL_CLOSED"
        summary["error"] = str(exc)
        exit_code = 2
    except Exception as exc:
        summary["status"] = "FAIL_CLOSED"
        summary["error"] = type(exc).__name__
        exit_code = 3
    finally:
        if session is not None:
            session.close()
        shutil.rmtree(profile, ignore_errors=True)

        state["seen_fps_by_day"] = {day: sorted(values) for day, values in seen_by_day.items()}
        cutoff = (utc_now().date() - timedelta(days=3)).isoformat()
        state["seen_fps_by_day"] = {
            day: values for day, values in state["seen_fps_by_day"].items() if day >= cutoff
        }
        state["event_daily"] = {
            day: values for day, values in state.get("event_daily", {}).items() if day >= cutoff
        }
        state["last_run_ended_at"] = iso(utc_now())
        save_state(state_path, state)

        summary["ended_at"] = iso(utc_now())
        summary["session_valid_for_at_least_seconds_this_run"] = round(max_session_age, 3)
        summary["campaign_state"] = {
            "poll_success_count": state.get("poll_success_count", 0),
            "poll_403_count": state.get("poll_403_count", 0),
            "poll_429_count": state.get("poll_429_count", 0),
            "reverify_count": state.get("reverify_count", 0),
            "gap_count": state.get("gap_count", 0),
            "gap_dates": state.get("gap_dates", []),
            "provider_revision_count": state.get("provider_revision_count", 0),
            "provider_revised_dates": state.get("provider_revised_dates", []),
            "coverage_intervals": state.get("coverage_intervals", []),
        }
        out_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))

    return summary, exit_code


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--state-path", default=".asxn-stage2/state.json")
    parser.add_argument("--output", default="artifacts/asxn-stage2/summary.json")
    args = parser.parse_args()
    if args.duration_seconds <= 0 or args.duration_seconds > 19_800:
        raise SystemExit("duration out of bounds")
    _, code = run(args)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
