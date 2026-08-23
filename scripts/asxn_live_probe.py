from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

HOME = "https://hyperscreener.asxn.xyz/"
EVENTS = "https://api-hyperliquid.asxn.xyz/api/node/liquidations"
DAILY = "https://api-hyperliquid.asxn.xyz/api/node/liquidations/chart/daily?timeframe=all"
DAILY_NUMERIC_FIELDS = (
    "long_liquidations",
    "short_liquidations",
    "long_unique_addresses",
    "short_unique_addresses",
    "long_notional",
    "short_notional",
)
EVENT_UID_FIELDS = (
    "timestamp_utc",
    "txn_hash",
    "address",
    "counterparty",
    "symbol",
    "direction",
    "size",
    "price",
    "notional_volume",
)


def compact_body(text: str, limit: int = 240) -> str:
    return " ".join((text or "").split())[:limit]


def direct_probe(url: str) -> dict:
    try:
        r = requests.get(url, timeout=15, allow_redirects=True)
        out = {"url": url, "status": r.status_code, "content_type": r.headers.get("content-type", "")}
        if r.status_code != 200:
            out["body_prefix"] = compact_body(r.text)
        return out
    except Exception as exc:
        return {"url": url, "error": type(exc).__name__}


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


def event_uid(event: dict) -> str:
    raw = "\x1f".join(str(event.get(k, "")) for k in EVENT_UID_FIELDS)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def summarize_daily(rows: list) -> tuple[dict, str | None]:
    result = {"type": "list", "count": len(rows)}
    dict_rows = [r for r in rows if isinstance(r, dict)]
    result["dict_rows"] = len(dict_rows)
    if not dict_rows:
        return result, None

    dates = [parse_iso(r.get("date")) for r in dict_rows]
    valid_dates = [d for d in dates if d is not None]
    result["first_date"] = dict_rows[0].get("date")
    result["last_date"] = dict_rows[-1].get("date")
    result["keys"] = sorted(dict_rows[0].keys())
    result["field_types_first"] = {k: type(dict_rows[0].get(k)).__name__ for k in result["keys"]}
    result["ascending_by_date"] = len(valid_dates) == len(dict_rows) and all(a <= b for a, b in zip(valid_dates, valid_dates[1:]))
    date_strings = [d.date().isoformat() for d in valid_dates]
    result["duplicate_date_count"] = len(date_strings) - len(set(date_strings))
    missing = 0
    for a, b in zip(valid_dates, valid_dates[1:]):
        delta = (b.date() - a.date()).days
        if delta > 1:
            missing += delta - 1
    result["missing_date_count"] = missing
    latest = dict_rows[-1]
    latest_hash = canonical_hash(latest)
    result["latest_row_hash"] = latest_hash
    result["latest_row"] = {"date": latest.get("date")}
    for field in DAILY_NUMERIC_FIELDS:
        result["latest_row"][field] = latest.get(field)
    return result, latest_hash


def summarize_events(rows: list) -> tuple[dict, set[str]]:
    result = {"type": "list", "count": len(rows)}
    dict_rows = [r for r in rows if isinstance(r, dict)]
    result["dict_rows"] = len(dict_rows)
    if not dict_rows:
        return result, set()

    times = [parse_iso(r.get("timestamp_utc")) for r in dict_rows]
    valid_times = [t for t in times if t is not None]
    result["keys"] = sorted(dict_rows[0].keys())
    result["field_types_first"] = {k: type(dict_rows[0].get(k)).__name__ for k in result["keys"]}
    result["newest_first"] = len(valid_times) == len(dict_rows) and all(a >= b for a, b in zip(valid_times, valid_times[1:]))
    result["newest_timestamp_utc"] = dict_rows[0].get("timestamp_utc")
    result["oldest_timestamp_utc"] = dict_rows[-1].get("timestamp_utc")
    if len(valid_times) >= 2:
        result["window_span_seconds"] = round((valid_times[0] - valid_times[-1]).total_seconds(), 6)
    result["directions_observed"] = sorted({str(r.get("direction")) for r in dict_rows})
    result["unique_symbols"] = len({str(r.get("symbol")) for r in dict_rows})
    result["unique_txn_hashes"] = len({str(r.get("txn_hash")) for r in dict_rows})
    fingerprints = {event_uid(r) for r in dict_rows}
    result["unique_event_fingerprints"] = len(fingerprints)
    return result, fingerprints


def browser_get_json(page, url: str) -> tuple[dict, object | None]:
    try:
        result = page.evaluate(
            """async (url) => {
                try {
                    const r = await fetch(url, {credentials: 'include'});
                    const text = await r.text();
                    return {ok: true, status: r.status, contentType: r.headers.get('content-type') || '', text};
                } catch (e) {
                    return {ok: false, error: String(e && e.name ? e.name : e)};
                }
            }""",
            url,
        )
    except Exception as exc:
        return {"url": url, "browser_eval_error": type(exc).__name__}, None

    out = {"url": url, "browser_fetch_ok": bool(result.get("ok")), "status": result.get("status"), "content_type": result.get("contentType", "")}
    if not result.get("ok"):
        out["error"] = result.get("error")
        return out, None
    text = result.get("text", "")
    if result.get("status") != 200:
        out["body_prefix"] = compact_body(text)
        return out, None
    try:
        return out, json.loads(text)
    except Exception as exc:
        out["parse"] = type(exc).__name__
        out["body_prefix"] = compact_body(text)
        return out, None


def main() -> None:
    summary = {
        "probe": "asxn-live-stage0-stage1-bounded-paired-v3",
        "direct": [direct_probe(HOME), direct_probe(EVENTS), direct_probe(DAILY)],
        "browser": {},
    }

    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    summary["browser"]["chrome_executable"] = bool(chrome)
    if not chrome:
        summary["browser"]["result"] = "NO_CHROME"
        print(json.dumps(summary, indent=2, sort_keys=True))
        raise SystemExit(2)

    observed = []
    with sync_playwright() as pw:
        profile = Path(tempfile.mkdtemp(prefix="asxn-profile-"))
        os.chmod(profile, 0o700)
        context = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile), executable_path=chrome, headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        def record_response(resp):
            u = resp.url
            if "/server-time" in u or "/verify" in u or "api-hyperliquid.asxn.xyz" in u:
                observed.append({"url": u.split("?")[0], "status": resp.status})

        page.on("response", record_response)
        try:
            page.goto(HOME, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(15000)
            summary["browser"]["title"] = page.title()[:120]
            summary["browser"]["observed_verification_network"] = observed[-50:]

            e1_meta, e1_raw = browser_get_json(page, EVENTS)
            d1_meta, d1_raw = browser_get_json(page, DAILY)
            e1_summary, e1_fp = summarize_events(e1_raw if isinstance(e1_raw, list) else [])
            d1_summary, d1_hash = summarize_daily(d1_raw if isinstance(d1_raw, list) else [])
            e1_meta["payload"] = e1_summary
            d1_meta["payload"] = d1_summary

            page.wait_for_timeout(8000)

            e2_meta, e2_raw = browser_get_json(page, EVENTS)
            d2_meta, d2_raw = browser_get_json(page, DAILY)
            e2_summary, e2_fp = summarize_events(e2_raw if isinstance(e2_raw, list) else [])
            d2_summary, d2_hash = summarize_daily(d2_raw if isinstance(d2_raw, list) else [])
            e2_meta["payload"] = e2_summary
            d2_meta["payload"] = d2_summary

            overlap = len(e1_fp & e2_fp)
            summary["browser"]["events_first"] = e1_meta
            summary["browser"]["events_second"] = e2_meta
            summary["browser"]["event_window_overlap_count"] = overlap
            summary["browser"]["event_window_gap"] = bool(e1_fp and e2_fp and overlap == 0)
            summary["browser"]["event_new_fingerprints_second"] = len(e2_fp - e1_fp)
            summary["browser"]["daily_first"] = d1_meta
            summary["browser"]["daily_second"] = d2_meta
            summary["browser"]["daily_latest_hash_changed_over_8s"] = bool(d1_hash and d2_hash and d1_hash != d2_hash)
        except Exception as exc:
            summary["browser"]["page_error"] = type(exc).__name__
            summary["browser"]["observed_verification_network"] = observed[-50:]
        finally:
            context.close()
            shutil.rmtree(profile, ignore_errors=True)

    print(json.dumps(summary, indent=2, sort_keys=True))
    out_dir = Path("artifacts/asxn-live")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
