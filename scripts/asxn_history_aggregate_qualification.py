from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import psutil
from playwright.sync_api import Page, sync_playwright

HOME = "https://hyperscreener.asxn.xyz/"
BASE = "https://api-hyperliquid.asxn.xyz/api"
OUTPUT = Path("artifacts/asxn-history-aggregate/summary.json")
FETCH_TIMEOUT_MS = 20_000
VERIFY_ATTEMPTS = 20
VERIFY_WAIT_MS = 1_500
MAX_RSS_BYTES = 2_500_000_000
MAX_PROCESS_COUNT = 32

FETCH_JS = r"""
async ({url, timeoutMs}) => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const r = await fetch(url, {credentials: 'include', signal: controller.signal});
    let data = null;
    try { data = await r.json(); } catch (_) {}
    return {status: r.status, data};
  } catch (_) {
    return {status: 0, data: null};
  } finally { clearTimeout(timer); }
}
"""


class ProbeError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 1e12:
            raw /= 1000.0
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except ValueError:
        return None


def decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def first_value(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def metric(row: dict[str, Any], kind: str) -> Decimal | None:
    aliases = {
        "long_notional": ("long_notional", "long_notional_usd", "long_usd"),
        "short_notional": ("short_notional", "short_notional_usd", "short_usd"),
        "total_notional": ("total_notional_usd", "total_notional", "total_notional_volume"),
        "long_count": ("long_liquidations", "long_count"),
        "short_count": ("short_liquidations", "short_count"),
        "total_count": ("total_liquidations", "count"),
    }
    return decimal(first_value(row, aliases[kind]))


def row_time(row: dict[str, Any]) -> datetime | None:
    return parse_time(first_value(row, ("hour", "hour_start", "timestamp", "time", "date")))


def rows_from_payload(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("data", "stats", "items", "rows", "chart_data", "symbols"):
            value = data.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def tree_metrics() -> dict[str, Any]:
    root = psutil.Process()
    procs = [root]
    try:
        procs += root.children(recursive=True)
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
    current = tree_metrics()
    res = summary.setdefault("resources", {
        "max_rss_bytes": 0,
        "max_process_count": 0,
        "max_cpu_seconds": 0.0,
        "rss_fail_closed_limit": MAX_RSS_BYTES,
        "process_fail_closed_limit": MAX_PROCESS_COUNT,
    })
    res["max_rss_bytes"] = max(int(res["max_rss_bytes"]), int(current["rss_bytes"]))
    res["max_process_count"] = max(int(res["max_process_count"]), int(current["process_count"]))
    res["max_cpu_seconds"] = max(float(res["max_cpu_seconds"]), float(current["cpu_seconds"]))
    if current["rss_bytes"] > MAX_RSS_BYTES or current["process_count"] > MAX_PROCESS_COUNT:
        raise ProbeError("resource_fail_closed")


def fetch_json(page: Page, url: str) -> tuple[int, Any, float]:
    started = time.perf_counter()
    result = page.evaluate(FETCH_JS, {"url": url, "timeoutMs": FETCH_TIMEOUT_MS})
    latency = (time.perf_counter() - started) * 1000.0
    if not isinstance(result, dict):
        return 0, None, latency
    return int(result.get("status") or 0), result.get("data"), latency


def verified_page(page: Page, summary: dict[str, Any]) -> None:
    started = time.perf_counter()
    page.goto(HOME, wait_until="domcontentloaded", timeout=45_000)
    observe_resource(summary)
    probe = BASE + "/node/liquidations/daily/stats?days=2"
    last_status = 0
    for _ in range(VERIFY_ATTEMPTS):
        last_status, data, _ = fetch_json(page, probe)
        observe_resource(summary)
        if last_status == 200 and rows_from_payload(data):
            summary["verification"] = {
                "protected_status": 200,
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "browser_session_only": True,
                "cookies_persisted": False,
                "tokens_persisted": False,
            }
            return
        if last_status == 429:
            raise ProbeError("verification_429")
        page.wait_for_timeout(VERIFY_WAIT_MS)
    raise ProbeError(f"verification_failed_{last_status}")


def fetch_rows(page: Page, summary: dict[str, Any], name: str, path: str) -> tuple[list[dict[str, Any]], Any]:
    status, data, latency = fetch_json(page, BASE + path)
    observe_resource(summary)
    summary.setdefault("requests", {})[name] = {
        "status": status,
        "latency_ms": round(latency, 3),
        "json_type": type(data).__name__,
    }
    if status == 429:
        raise ProbeError(f"{name}_429")
    if status != 200:
        raise ProbeError(f"{name}_status_{status}")
    return rows_from_payload(data), data


def compact_stats_row(row: dict[str, Any]) -> dict[str, Any]:
    dt = row_time(row)
    result: dict[str, Any] = {
        "time": iso(dt) if dt else None,
        "keys": sorted(row.keys()),
    }
    for key in ("long_notional", "short_notional", "total_notional", "long_count", "short_count", "total_count"):
        value = metric(row, key)
        result[key] = format(value, "f") if value is not None else None
    return result


def daily_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for row in rows:
        dt = row_time(row)
        if dt:
            out[dt.date().isoformat()] = row
    return out


def hourly_by_day(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        dt = row_time(row)
        if dt:
            out.setdefault(dt.date().isoformat(), []).append(row)
    return out


def sum_metric(rows: list[dict[str, Any]], key: str) -> Decimal | None:
    values = [metric(row, key) for row in rows]
    if any(value is None for value in values) or not values:
        return None
    return sum((value for value in values if value is not None), Decimal("0"))


def compare_daily_hourly(daily_rows: list[dict[str, Any]], hourly_rows: list[dict[str, Any]]) -> dict[str, Any]:
    dmap = daily_map(daily_rows)
    hmap = hourly_by_day(hourly_rows)
    today = utc_now().date().isoformat()
    candidates = sorted(set(dmap).intersection(hmap), reverse=True)
    target = next((day for day in candidates if day < today and len(hmap[day]) >= 20), None)
    if not target:
        return {"available": False, "reason": "no_closed_day_with_enough_hourly_buckets"}
    result: dict[str, Any] = {"available": True, "date": target, "hourly_bucket_count": len(hmap[target])}
    for key in ("long_notional", "short_notional", "total_notional", "long_count", "short_count", "total_count"):
        d = metric(dmap[target], key)
        h = sum_metric(hmap[target], key)
        result[key] = {
            "daily": format(d, "f") if d is not None else None,
            "hourly_sum": format(h, "f") if h is not None else None,
            "delta": format(d - h, "f") if d is not None and h is not None else None,
        }
    return result


def hash_row(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def main() -> int:
    summary: dict[str, Any] = {
        "contract": "ASXN_HISTORY_AGGREGATE_QUALIFICATION_Q1_2026_08_27",
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "started_at": iso(),
        "source_only": True,
        "raw_events_requested": False,
        "raw_events_persisted": False,
        "production_mutation": False,
        "tests": {},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="asxn-history-q1-"))
    os.chmod(profile, 0o700)
    exit_code = 0
    try:
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
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
            verified_page(page, summary)

            daily30, _ = fetch_rows(page, summary, "daily30", "/node/liquidations/daily/stats?days=30")
            hourly72, _ = fetch_rows(page, summary, "hourly72", "/node/liquidations/hourly/stats?hours=72")
            summary1h_rows, summary1h_raw = fetch_rows(page, summary, "summary1h", "/node/liquidations/summary?timeframe=1h")
            symbols_rows, symbols_raw = fetch_rows(page, summary, "symbols24h", "/node/liquidations/stats/symbols?timeframe=24h&limit=all")
            xyz_daily, _ = fetch_rows(page, summary, "xyzDaily7", "/node/liquidations/daily/stats?symbol=xyz%3ANVDA&days=7")
            hip3_xyz, hip3_raw = fetch_rows(page, summary, "hip3xyz7", "/meta/hip3/liquidations-chart?timeframe=7d&dex=xyz")

            # Test 1: current-day presence and current-hour aggregate surface.
            now = utc_now()
            dmap = daily_map(daily30)
            today_key = now.date().isoformat()
            today_row = dmap.get(today_key)
            hourly_times = [(row_time(row), row) for row in hourly72]
            hourly_times = [(dt, row) for dt, row in hourly_times if dt]
            latest_hour = max(hourly_times, key=lambda pair: pair[0]) if hourly_times else None
            summary["tests"]["intraday_surface"] = {
                "current_daily_present": today_row is not None,
                "current_daily": compact_stats_row(today_row) if today_row else None,
                "latest_hour": compact_stats_row(latest_hour[1]) if latest_hour else None,
                "latest_hour_age_minutes": round((now - latest_hour[0]).total_seconds() / 60.0, 3) if latest_hour else None,
                "latest_hour_same_utc_hour": bool(latest_hour and latest_hour[0].replace(minute=0, second=0, microsecond=0) == now.replace(minute=0, second=0, microsecond=0)),
                "summary1h_keys": sorted(summary1h_raw.keys()) if isinstance(summary1h_raw, dict) else [],
            }

            # Test 2: UTC closed-day reconciliation.
            summary["tests"]["utc_reconciliation"] = compare_daily_hourly(daily30, hourly72)

            # Test 3 baseline: retention depth probes.
            retention = {}
            for days in (7, 30, 90, 180, 365):
                if days == 30:
                    rows = daily30
                else:
                    rows, _ = fetch_rows(page, summary, f"daily{days}", f"/node/liquidations/daily/stats?days={days}")
                times = sorted(dt for dt in (row_time(row) for row in rows) if dt)
                retention[str(days)] = {
                    "row_count": len(rows),
                    "first_date": times[0].date().isoformat() if times else None,
                    "last_date": times[-1].date().isoformat() if times else None,
                }
            summary["tests"]["retention"] = retention

            # Test 4: HIP-3 scope evidence only; no whole-scope claim.
            symbol_names = []
            for row in symbols_rows:
                name = first_value(row, ("symbol", "coin", "name"))
                if name is not None:
                    symbol_names.append(str(name))
            summary["tests"]["hip3_scope"] = {
                "xyz_nvda_daily_row_count": len(xyz_daily),
                "xyz_nvda_daily_latest": compact_stats_row(xyz_daily[-1]) if xyz_daily else None,
                "xyz_nvda_present_in_24h_symbol_stats": "xyz:NVDA" in symbol_names,
                "symbol_stats_row_count": len(symbols_rows),
                "hip3_xyz_chart_row_count": len(hip3_xyz),
                "hip3_xyz_top_keys": sorted(hip3_raw.keys()) if isinstance(hip3_raw, dict) else [],
            }

            # Test 5 baseline: recent closed-day hashes for later finality comparison.
            closed_days = sorted(day for day in dmap if day < today_key)
            recent_closed = closed_days[-3:]
            summary["tests"]["finality_baseline"] = {
                "captured_at": iso(),
                "days": {day: {"hash": hash_row(dmap[day]), "stats": compact_stats_row(dmap[day])} for day in recent_closed},
                "finality_pass": False,
                "reason": "baseline_only_requires_later_comparison",
            }

            summary["tests"]["daily30"] = {
                "row_count": len(daily30),
                "first": compact_stats_row(daily30[0]) if daily30 else None,
                "last": compact_stats_row(daily30[-1]) if daily30 else None,
            }
            summary["tests"]["hourly72"] = {
                "row_count": len(hourly72),
                "first": compact_stats_row(hourly72[0]) if hourly72 else None,
                "last": compact_stats_row(hourly72[-1]) if hourly72 else None,
            }
            context.close()

        summary["status"] = "Q1_COMPLETE"
    except Exception as exc:
        summary["status"] = "Q1_FAIL_CLOSED"
        summary["error"] = str(exc)
        exit_code = 1
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        summary["ended_at"] = iso()
        summary["browser_profile_persisted"] = False
        OUTPUT.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
