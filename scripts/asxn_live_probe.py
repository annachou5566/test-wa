from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

HOME = "https://hyperscreener.asxn.xyz/"
EVENTS = "https://api-hyperliquid.asxn.xyz/api/node/liquidations"
DAILY = "https://api-hyperliquid.asxn.xyz/api/node/liquidations/chart/daily?timeframe=all"


def compact_body(text: str, limit: int = 240) -> str:
    return " ".join((text or "").split())[:limit]


def direct_probe(url: str) -> dict:
    try:
        r = requests.get(url, timeout=15, allow_redirects=True)
        out = {
            "url": url,
            "status": r.status_code,
            "content_type": r.headers.get("content-type", ""),
        }
        if r.status_code != 200:
            out["body_prefix"] = compact_body(r.text)
        return out
    except Exception as exc:
        return {"url": url, "error": type(exc).__name__}


def summarize_payload(payload):
    if isinstance(payload, list):
        result = {"type": "list", "count": len(payload)}
        if payload:
            first = payload[0]
            last = payload[-1]
            if isinstance(first, dict):
                result["first_keys"] = sorted(first.keys())
                result["first_date"] = first.get("date")
                result["first_timestamp_utc"] = first.get("timestamp_utc")
                result["first_direction"] = first.get("direction")
            if isinstance(last, dict):
                result["last_keys"] = sorted(last.keys())
                result["last_date"] = last.get("date")
                result["last_timestamp_utc"] = last.get("timestamp_utc")
                result["last_direction"] = last.get("direction")
        return result
    if isinstance(payload, dict):
        return {"type": "object", "keys": sorted(payload.keys())}
    return {"type": type(payload).__name__}


def browser_fetch(page, url: str) -> dict:
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
        return {"url": url, "browser_eval_error": type(exc).__name__}

    out = {
        "url": url,
        "browser_fetch_ok": bool(result.get("ok")),
        "status": result.get("status"),
        "content_type": result.get("contentType", ""),
    }
    if not result.get("ok"):
        out["error"] = result.get("error")
        return out

    text = result.get("text", "")
    if result.get("status") != 200:
        out["body_prefix"] = compact_body(text)
        return out
    try:
        out["payload"] = summarize_payload(json.loads(text))
    except Exception:
        out["parse"] = "non-json"
        out["body_prefix"] = compact_body(text)
    return out


def main() -> None:
    summary = {
        "probe": "asxn-live-stage0-stage1-bounded",
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
            user_data_dir=str(profile),
            executable_path=chrome,
            headless=True,
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
            summary["browser"]["observed_verification_network"] = observed[-40:]
            summary["browser"]["events"] = browser_fetch(page, EVENTS)
            summary["browser"]["daily"] = browser_fetch(page, DAILY)
        except Exception as exc:
            summary["browser"]["page_error"] = type(exc).__name__
            summary["browser"]["observed_verification_network"] = observed[-40:]
        finally:
            context.close()
            shutil.rmtree(profile, ignore_errors=True)

    print(json.dumps(summary, indent=2, sort_keys=True))

    out_dir = Path("artifacts/asxn-live")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()
