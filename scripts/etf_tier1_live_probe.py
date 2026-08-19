#!/usr/bin/env python3
"""One final public-safe ordinary-browser retry for BTCW and EZBC only.

No Wave Alpha private source, no secrets, no WAF bypass, no stealth plugins,
no challenge-token spoofing, and no retries/reloads. Each fund gets one normal
Chrome/Playwright navigation. Franklin consent/investor gates are clicked only
when visibly offered, matching the site's normal user flow.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

OUT = Path("artifacts/etf-tier1-live")
OUT.mkdir(parents=True, exist_ok=True)
TIMEOUT_MS = 30_000
MAX_CAPTURE_BYTES = 2 * 1024 * 1024

SOURCES = {
    "BTCW": {
        "url": "https://www.wisdomtree.com/us/products/crypto/btcw",
        "markers": ["Shares Outstanding", "NAV", "BTCW"],
        "normal_gate_labels": [],
    },
    "EZBC": {
        "url": "https://www.franklintempleton.com/investments/options/exchange-traded-funds/products/39639/SINGLCLASS/franklin-bitcoin-etf/EZBC",
        "markers": ["Shares Outstanding", "Total Net Assets", "Bitcoin"],
        "normal_gate_labels": [
            r"Individual Investor",
            r"Continue",
            r"Accept",
            r"Agree",
            r"I Agree",
            r"Yes",
            r"Confirm",
            r"United States",
            r"Enter",
            r"OK",
            r"Proceed",
        ],
    },
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def marker_report(text: str, markers: list[str]) -> dict[str, int]:
    low = text.lower()
    return {marker: low.count(marker.lower()) for marker in markers}


def save(ticker: str, data: bytes) -> str:
    path = OUT / f"{ticker}.txt"
    path.write_bytes(data)
    return str(path)


def click_visible_normal_gate(page, pattern: str) -> str | None:
    """Click one visible ordinary site control, including controls in child frames."""
    regex = re.compile(pattern, re.I)
    for frame in page.frames:
        for role in ("button", "link"):
            try:
                locator = frame.get_by_role(role, name=regex)
                count = min(locator.count(), 5)
                for index in range(count):
                    candidate = locator.nth(index)
                    if candidate.is_visible(timeout=500):
                        candidate.click(timeout=3_000)
                        page.wait_for_timeout(900)
                        return f"{role}:{pattern}"
            except Exception:
                pass
        try:
            locator = frame.get_by_text(regex, exact=True)
            count = min(locator.count(), 5)
            for index in range(count):
                candidate = locator.nth(index)
                if candidate.is_visible(timeout=500):
                    candidate.click(timeout=3_000)
                    page.wait_for_timeout(900)
                    return f"text:{pattern}"
        except Exception:
            pass
    return None


def body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5_000)
    except Exception:
        return ""


def browser_capture(browser, ticker: str, spec: dict) -> dict:
    context = browser.new_context(locale="en-US", viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    started = now()
    clicked: list[str] = []
    try:
        response = page.goto(spec["url"], wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        status = response.status if response else None
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except PlaywrightTimeoutError:
            pass

        # One normal page visit only. No reload, alternate endpoint, stealth, or challenge bypass.
        for pattern in spec["normal_gate_labels"]:
            action = click_visible_normal_gate(page, pattern)
            if action:
                clicked.append(action)

        # Allow normal lazy-loaded product modules to enter the viewport.
        for _ in range(12):
            page.mouse.wheel(0, 1_500)
            page.wait_for_timeout(450)

        # Wait briefly for ordinary client rendering; never reload or create a second acquisition attempt.
        for _ in range(10):
            text = body_text(page)
            report = marker_report(text, spec["markers"])
            if any(report.values()):
                break
            page.wait_for_timeout(1_000)

        text = body_text(page)
        data = text.encode("utf-8")
        return {
            "ticker": ticker,
            "source_url": spec["url"],
            "final_url": page.url,
            "acquisition_method": "ordinary_playwright_visible_text_single_navigation",
            "retrieved_at_utc": started,
            "http_status": status,
            "content_type": "text/plain; charset=utf-8",
            "size_bytes": len(data),
            "sha256": digest(data),
            "artifact_path": save(ticker, data),
            "under_2mib": len(data) <= MAX_CAPTURE_BYTES,
            "normal_gate_clicks": clicked,
            "markers": marker_report(text, spec["markers"]),
        }
    finally:
        context.close()


def main() -> None:
    summary = {
        "probe": "US_BTC_TIER1_FINAL_ORDINARY_BROWSER_RETRY",
        "started_at_utc": now(),
        "network_owner": "public-first-party-issuer-only",
        "secrets_used": False,
        "wave_alpha_private_source_copied": False,
        "waf_bypass_used": False,
        "stealth_or_challenge_evasion_used": False,
        "one_navigation_per_fund": True,
        "results": [],
    }

    browser_path = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    if not browser_path:
        for ticker, spec in SOURCES.items():
            summary["results"].append(
                {
                    "ticker": ticker,
                    "source_url": spec["url"],
                    "acquisition_method": "ordinary_playwright_visible_text_single_navigation",
                    "retrieved_at_utc": now(),
                    "error": "SYSTEM_CHROME_NOT_FOUND",
                }
            )
    else:
        summary["system_browser"] = browser_path
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path=browser_path,
                args=["--no-sandbox"],
            )
            try:
                for ticker, spec in SOURCES.items():
                    try:
                        summary["results"].append(browser_capture(browser, ticker, spec))
                    except Exception as exc:
                        summary["results"].append(
                            {
                                "ticker": ticker,
                                "source_url": spec["url"],
                                "acquisition_method": "ordinary_playwright_visible_text_single_navigation",
                                "retrieved_at_utc": now(),
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
            finally:
                browser.close()

    summary["finished_at_utc"] = now()
    summary["results"] = sorted(summary["results"], key=lambda row: row["ticker"])
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
