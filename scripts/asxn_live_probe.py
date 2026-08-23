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
    "long_liquidations", "short_liquidations", "long_unique_addresses",
    "short_unique_addresses", "long_notional", "short_notional",
)
EVENT_UID_FIELDS = (
    "timestamp_utc", "txn_hash", "address", "counterparty", "symbol",
    "direction", "size", "price", "notional_volume",
)


def compact_body(text: str, limit: int = 160) -> str:
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
    return hashlib.sha256(raw.encode()).hexdigest()


def event_uid(event: dict) -> str:
    raw = "\x1f".join(str(event.get(k, "")) for k in EVENT_UID_FIELDS)
    return hashlib.sha256(raw.encode()).hexdigest()


def summarize_daily(rows: list) -> tuple[dict, str | None]:
    result = {"type": "list", "count": len(rows)}
    items = [r for r in rows if isinstance(r, dict)]
    result["dict_rows"] = len(items)
    if not items:
        return result, None
    dates = [parse_iso(r.get("date")) for r in items]
    valid_dates = [d for d in dates if d is not None]
    result["first_date"] = items[0].get("date")
    result["last_date"] = items[-1].get("date")
    result["keys"] = sorted(items[0].keys())
    result["field_types_first"] = {k: type(items[0].get(k)).__name__ for k in result["keys"]}
    result["ascending_by_date"] = len(valid_dates) == len(items) and all(a <= b for a, b in zip(valid_dates, valid_dates[1:]))
    date_strings = [d.date().isoformat() for d in valid_dates]
    result["duplicate_date_count"] = len(date_strings) - len(set(date_strings))
    result["missing_date_count"] = sum(max(0, (b.date() - a.date()).days - 1) for a, b in zip(valid_dates, valid_dates[1:]))
    latest = items[-1]
    latest_hash = canonical_hash(latest)
    result["latest_row_hash"] = latest_hash
    result["latest_row"] = {"date": latest.get("date"), **{f: latest.get(f) for f in DAILY_NUMERIC_FIELDS}}
    return result, latest_hash


def summarize_events(rows: list) -> tuple[dict, set[str]]:
    result = {"type": "list", "count": len(rows)}
    items = [r for r in rows if isinstance(r, dict)]
    result["dict_rows"] = len(items)
    if not items:
        return result, set()
    times = [parse_iso(r.get("timestamp_utc")) for r in items]
    valid_times = [t for t in times if t is not None]
    result["keys"] = sorted(items[0].keys())
    result["field_types_first"] = {k: type(items[0].get(k)).__name__ for k in result["keys"]}
    result["newest_first"] = len(valid_times) == len(items) and all(a >= b for a, b in zip(valid_times, valid_times[1:]))
    result["newest_timestamp_utc"] = items[0].get("timestamp_utc")
    result["oldest_timestamp_utc"] = items[-1].get("timestamp_utc")
    if len(valid_times) >= 2:
        result["window_span_seconds"] = round((valid_times[0] - valid_times[-1]).total_seconds(), 6)
    result["directions_observed"] = sorted({str(r.get("direction")) for r in items})
    result["unique_symbols"] = len({str(r.get("symbol")) for r in items})
    result["unique_txn_hashes"] = len({str(r.get("txn_hash")) for r in items})
    fp = {event_uid(r) for r in items}
    result["unique_event_fingerprints"] = len(fp)
    return result, fp


def browser_get_json(page, url: str) -> tuple[dict, object | None]:
    try:
        result = page.evaluate("""async (url) => { try { const r = await fetch(url,{credentials:'include'}); return {ok:true,status:r.status,contentType:r.headers.get('content-type')||'',text:await r.text()}; } catch(e) { return {ok:false,error:String(e&&e.name?e.name:e)}; } }""", url)
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
        return out, None


def lightweight_reuse_probe(context, page) -> dict:
    """Reuse provider-issued browser cookies in a co-located HTTP client; never output cookie material."""
    try:
        session = requests.Session()
        for cookie in context.cookies():
            session.cookies.set(
                cookie["name"], cookie["value"],
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
        result = {}
        for name, url in (("events", EVENTS), ("daily", DAILY)):
            r = session.get(url, timeout=15)
            result[name] = {"status": r.status_code, "content_type": r.headers.get("content-type", "")}
            if r.status_code != 200:
                result[name]["body_prefix"] = compact_body(r.text)
        return result
    except Exception as exc:
        return {"error": type(exc).__name__}


def main() -> None:
    summary = {
        "probe": "asxn-live-stage0-stage1-session-reuse-v4",
        "direct": [direct_probe(HOME), direct_probe(EVENTS), direct_probe(DAILY)],
        "browser": {},
    }
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    summary["browser"]["chrome_executable"] = bool(chrome)
    if not chrome:
        summary["browser"]["result"] = "NO_CHROME"
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
        page.on("response", lambda resp: observed.append({"url": resp.url.split("?")[0], "status": resp.status}) if ("/server-time" in resp.url or "/verify" in resp.url or "api-hyperliquid.asxn.xyz" in resp.url) else None)
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

            summary["browser"]["lightweight_http_reuse"] = lightweight_reuse_probe(context, page)
            page.wait_for_timeout(8000)

            e2_meta, e2_raw = browser_get_json(page, EVENTS)
            d2_meta, d2_raw = browser_get_json(page, DAILY)
            e2_summary, e2_fp = summarize_events(e2_raw if isinstance(e2_raw, list) else [])
            d2_summary, d2_hash = summarize_daily(d2_raw if isinstance(d2_raw, list) else [])
            e2_meta["payload"] = e2_summary
            d2_meta["payload"] = d2_summary

            overlap = len(e1_fp & e2_fp)
            summary["browser"].update({
                "events_first": e1_meta,
                "events_second": e2_meta,
                "event_window_overlap_count": overlap,
                "event_window_gap": bool(e1_fp and e2_fp and overlap == 0),
                "event_new_fingerprints_second": len(e2_fp - e1_fp),
                "daily_first": d1_meta,
                "daily_second": d2_meta,
                "daily_latest_hash_changed_over_8s": bool(d1_hash and d2_hash and d1_hash != d2_hash),
            })
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
