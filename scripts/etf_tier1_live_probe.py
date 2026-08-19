#!/usr/bin/env python3
"""One-shot public-safe U.S. BTC Tier 1 issuer artifact probe.

No Wave Alpha private source, secrets, derived flow logic, persistence, or cron ownership.
Captures only public first-party issuer artifacts plus transport/hash metadata.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

OUT = Path("artifacts/etf-tier1-live")
OUT.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
TIMEOUT = 30
MAX_CAPTURE_BYTES = 2 * 1024 * 1024

SOURCES = {
    "IBIT": {
        "url": "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf/latest-holdings.csv",
        "method": "requests",
        "suffix": ".csv",
        "markers": ["Shares Outstanding", "Ticker,Name", "BTC"],
    },
    "ARKB": {
        "url": "https://assets.ark-funds.com/fund-documents/funds-etf-csv/ARK_21SHARES_BITCOIN_ETF_ARKB_HOLDINGS.csv",
        "method": "requests",
        "suffix": ".csv",
        "markers": ["date,fund,company,ticker,cusip,shares", "ARKB", "BITCOIN"],
    },
    "BITB": {
        "url": "https://bitbetf.com/",
        "method": "requests",
        "suffix": ".html",
        "markers": ["Shares Outstanding", "Net Assets", "Bitcoin"],
    },
    "HODL": {
        "url": "https://www.vaneck.com/us/en/investments/bitcoin-etf-hodl/",
        "method": "browser_text",
        "suffix": ".txt",
        "markers": ["Shares Outstanding", "ETF Statistics", "Bitcoin"],
    },
    "EZBC": {
        "url": "https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/39639/SINGLCLASS/franklin-bitcoin-etf/EZBC",
        "method": "browser_text",
        "suffix": ".txt",
        "markers": ["Shares Outstanding", "Total Net Assets", "Bitcoin"],
    },
    "BTCW": {
        "url": "https://www.wisdomtree.com/us/products/crypto/btcw",
        "method": "requests",
        "suffix": ".html",
        "markers": ["Shares Outstanding", "NAV", "BTCW"],
    },
    "OBTC": {
        "url": "https://www.rexshares.com/obtc/",
        "method": "requests",
        "suffix": ".html",
        "markers": ["Shares Outstanding", "Bitcoin", "OBTC"],
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def marker_report(text: str, markers: list[str]) -> dict[str, int]:
    lower = text.lower()
    return {marker: lower.count(marker.lower()) for marker in markers}


def write_artifact(ticker: str, suffix: str, data: bytes) -> Path:
    path = OUT / f"{ticker}{suffix}"
    path.write_bytes(data)
    return path


def requests_capture(ticker: str, spec: dict) -> dict:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,text/csv,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    started = now_iso()
    response = requests.get(spec["url"], headers=headers, timeout=TIMEOUT, allow_redirects=True)
    data = response.content
    text = safe_text(data)
    path = write_artifact(ticker, spec["suffix"], data)
    return {
        "ticker": ticker,
        "source_url": spec["url"],
        "final_url": response.url,
        "acquisition_method": "requests",
        "retrieved_at_utc": started,
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type"),
        "size_bytes": len(data),
        "sha256": hash_bytes(data),
        "artifact_path": str(path),
        "under_2mib": len(data) <= MAX_CAPTURE_BYTES,
        "markers": marker_report(text, spec["markers"]),
    }


def click_if_visible(page, pattern: str) -> bool:
    regex = re.compile(pattern, re.I)
    for role in ("button", "link"):
        try:
            locator = page.get_by_role(role, name=regex)
            if locator.count() and locator.first.is_visible(timeout=1000):
                locator.first.click(timeout=3000)
                page.wait_for_timeout(1000)
                return True
        except Exception:
            pass
    return False


def browser_text_capture(browser, ticker: str, spec: dict) -> dict:
    context = browser.new_context(user_agent=UA, locale="en-US", viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    started = now_iso()
    status = None
    final_url = spec["url"]
    role_gate_clicked = False
    try:
        response = page.goto(spec["url"], wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
        status = response.status if response else None
        final_url = page.url
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeoutError:
            pass

        click_if_visible(page, r"accept|accept all|agree")
        role_gate_clicked = click_if_visible(page, r"individual investor|retail investor|continue as.*investor")

        for _ in range(8):
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(350)
        page.mouse.wheel(0, -20000)
        page.wait_for_timeout(500)

        text = page.locator("body").inner_text(timeout=5000)
        data = text.encode("utf-8")
        path = write_artifact(ticker, spec["suffix"], data)
        return {
            "ticker": ticker,
            "source_url": spec["url"],
            "final_url": page.url,
            "acquisition_method": "plain_playwright_visible_text",
            "retrieved_at_utc": started,
            "http_status": status,
            "content_type": "text/plain; charset=utf-8",
            "size_bytes": len(data),
            "sha256": hash_bytes(data),
            "artifact_path": str(path),
            "under_2mib": len(data) <= MAX_CAPTURE_BYTES,
            "role_gate_clicked": role_gate_clicked,
            "markers": marker_report(text, spec["markers"]),
        }
    finally:
        context.close()


def main() -> None:
    summary = {
        "probe": "US_BTC_TIER1_LIVE_ACQUISITION",
        "started_at_utc": now_iso(),
        "network_owner": "public-first-party-issuer-only",
        "secrets_used": False,
        "wave_alpha_private_source_copied": False,
        "results": [],
    }

    browser_specs = {ticker: spec for ticker, spec in SOURCES.items() if spec["method"] == "browser_text"}
    request_specs = {ticker: spec for ticker, spec in SOURCES.items() if spec["method"] == "requests"}

    for ticker, spec in request_specs.items():
        try:
            summary["results"].append(requests_capture(ticker, spec))
        except Exception as exc:
            summary["results"].append({
                "ticker": ticker,
                "source_url": spec["url"],
                "acquisition_method": "requests",
                "retrieved_at_utc": now_iso(),
                "error": f"{type(exc).__name__}: {exc}",
            })

    if browser_specs:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for ticker, spec in browser_specs.items():
                    try:
                        summary["results"].append(browser_text_capture(browser, ticker, spec))
                    except Exception as exc:
                        summary["results"].append({
                            "ticker": ticker,
                            "source_url": spec["url"],
                            "acquisition_method": "plain_playwright_visible_text",
                            "retrieved_at_utc": now_iso(),
                            "error": f"{type(exc).__name__}: {exc}",
                        })
            finally:
                browser.close()

    summary["finished_at_utc"] = now_iso()
    summary["results"] = sorted(summary["results"], key=lambda row: row["ticker"])
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
