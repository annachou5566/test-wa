from __future__ import annotations

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

HOST = "https://api-hyperliquid.asxn.xyz"
OUTPUT = Path("artifacts/asxn-replay-surface/summary.json")
CONTRACT = "ASXN_S3_DATE_ARCHIVE_CAPABILITY_PROBE_V4"
PROBES = (
    ("btc_recent_day", "BTC", "2026-08-26"),
    ("btc_earliest_observed_day", "BTC", "2025-10-30"),
)


class StopProbe(RuntimeError):
    pass


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def target(symbol: str, day: str) -> str:
    return f"{HOST}/api/s3-liquidations/{symbol}?" + urlencode({"date": day})


def effective_fetch(page, url: str, summary: dict[str, Any]) -> tuple[int, Any | None, float]:
    status, data, latency_ms = core.browser_fetch_json(page, url)
    core.observe_resource(summary)
    if status == 429:
        raise StopProbe("http_429_provider_pressure")
    if status == 403:
        core.verify_same_context(page, summary, reason="s3_archive_403")
        status, data, latency_ms = core.browser_fetch_json(page, url)
        core.observe_resource(summary)
        if status == 429:
            raise StopProbe("http_429_provider_pressure")
    return status, data, latency_ms


def exact_event_rows(rows: Any) -> tuple[bool, int, str | None, str | None, bool | None]:
    if not isinstance(rows, list) or not rows:
        return False, len(rows) if isinstance(rows, list) else 0, None, None, None
    if not all(isinstance(row, dict) for row in rows):
        return False, len(rows), None, None, None
    exact = all(set(row.keys()) == core.EVENT_KEYS for row in rows)
    timestamps: list[datetime] = []
    if exact:
        for row in rows:
            parsed = core.parse_iso(row.get("timestamp_utc"))
            if parsed is None:
                exact = False
                break
            timestamps.append(parsed)
    if not exact or not timestamps:
        return False, len(rows), None, None, None
    oldest = min(timestamps)
    newest = max(timestamps)
    monotonic_desc = all(a >= b for a, b in zip(timestamps, timestamps[1:]))
    return True, len(rows), iso(oldest), iso(newest), monotonic_desc


def describe_payload(data: Any, requested_day: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "json_type": type(data).__name__,
        "top_level_keys": [],
        "list_count": None,
        "item_keys": [],
        "exact_event_schema": False,
        "oldest_ts": None,
        "newest_ts": None,
        "timestamps_descending": None,
        "all_timestamps_within_requested_utc_day": None,
        "nested_lists": {},
    }

    candidate_lists: list[tuple[str, list[Any]]] = []
    if isinstance(data, list):
        out["list_count"] = len(data)
        candidate_lists.append(("$", data))
        if data and isinstance(data[0], dict):
            out["item_keys"] = sorted(data[0].keys())
    elif isinstance(data, dict):
        out["top_level_keys"] = sorted(data.keys())
        for key, value in data.items():
            if isinstance(value, list):
                meta: dict[str, Any] = {"count": len(value), "item_keys": []}
                if value and isinstance(value[0], dict):
                    meta["item_keys"] = sorted(value[0].keys())
                out["nested_lists"][key] = meta
                candidate_lists.append((key, value))

    for list_name, rows in candidate_lists:
        exact, count, oldest, newest, desc = exact_event_rows(rows)
        if not exact:
            continue
        out["exact_event_schema"] = True
        out["event_list_location"] = list_name
        out["list_count"] = count
        out["oldest_ts"] = oldest
        out["newest_ts"] = newest
        out["timestamps_descending"] = desc
        day_prefix = requested_day + "T"
        out["all_timestamps_within_requested_utc_day"] = bool(
            oldest and newest and oldest.startswith(day_prefix) and newest.startswith(day_prefix)
        )
        break
    return out


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
        "provider_contract_basis": "/openapi.json exposed GET /api/s3-liquidations/{symbol} with optional query parameter date",
        "probe_count": len(PROBES),
        "results": [],
    }
    profile = Path(tempfile.mkdtemp(prefix="asxn-s3-archive-profile-"))
    os.chmod(profile, 0o700)
    exit_code = 0
    try:
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not chrome:
            raise StopProbe("chrome_missing")
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                executable_path=chrome,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                verified_at, verify_ms = core.verify_same_context(page, summary, reason="initial")
                summary["verified_at"] = iso(verified_at)
                summary["initial_verify_latency_ms"] = round(verify_ms, 3)

                for name, symbol, day in PROBES:
                    status, data, latency_ms = effective_fetch(page, target(symbol, day), summary)
                    row = {
                        "name": name,
                        "symbol": symbol,
                        "requested_date": day,
                        "http_status": status,
                        "latency_ms": round(latency_ms, 3),
                    }
                    if status == 200:
                        row["payload"] = describe_payload(data, day)
                    summary["results"].append(row)

                exact_rows = [
                    row for row in summary["results"]
                    if row.get("http_status") == 200
                    and isinstance(row.get("payload"), dict)
                    and row["payload"].get("exact_event_schema") is True
                    and int(row["payload"].get("list_count") or 0) > 0
                ]
                date_exact = [
                    row for row in exact_rows
                    if row["payload"].get("all_timestamps_within_requested_utc_day") is True
                ]
                old_available = any(row["name"] == "btc_earliest_observed_day" for row in date_exact)

                if date_exact:
                    classification = "DATE_SCOPED_S3_EVENT_ARCHIVE_REPLAY_CANDIDATE"
                elif any(row.get("http_status") == 200 for row in summary["results"]):
                    classification = "S3_DATE_SURFACE_OBSERVED_SCHEMA_NOT_EVENT_MATCH"
                else:
                    classification = "S3_DATE_ROUTE_UNAVAILABLE"

                summary["decision"] = {
                    "classification": classification,
                    "exact_date_scoped_event_probes": [row["name"] for row in date_exact],
                    "old_date_archive_observed": old_available,
                    "truth_limit": "A date-scoped S3 event response can be a replay candidate, but whole-exchange completeness still requires archive-day boundary semantics, symbol universe coverage, deterministic repeatability, revision/finality behavior, and prospective continuity reconciliation. This probe does not authorize Production.",
                }
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
