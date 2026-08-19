import hashlib
import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

OUT = Path("artifacts/btc-old-cron-migration")
OUT.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 2 * 1024 * 1024

FUNDS = {
    "BTCO": {
        "url": "https://www.invesco.com/us/en/financial-products/etfs/invesco-galaxy-bitcoin-etf.html",
        "clicks": ["Individual Investor", "Confirm"],
        "markers": ["Total units of crypto", "Shares Outstanding", "Fund characteristics", "NAV", "BTCO", "Bitcoin"],
    },
}


def normalized_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\xa0", " ")).strip()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def click_if_visible(page, text: str) -> bool:
    locator = page.get_by_text(text, exact=False).first
    try:
        if locator.is_visible(timeout=1200):
            locator.click(timeout=2500)
            page.wait_for_timeout(700)
            return True
    except Exception:
        return False
    return False


def bounded_scroll(page) -> None:
    for _ in range(10):
        page.evaluate("window.scrollBy(0, 700)")
        page.wait_for_timeout(350)
    page.evaluate("window.scrollTo(0, 0)")


def capture(page, ticker: str, spec: dict) -> dict:
    meta = {
        "ticker": ticker,
        "source_url": spec["url"],
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "transport": "ordinary-system-chrome-direct-first-party",
        "third_party_proxy_used": False,
        "stealth_used": False,
        "challenge_bypass_used": False,
        "navigation_count": 1,
        "clicked_visible_controls": [],
        "http_status": None,
        "final_url": None,
        "title": None,
        "size_bytes": 0,
        "sha256": None,
        "marker_counts": {},
        "challenge_markers": {},
        "error": None,
    }
    try:
        response = page.goto(spec["url"], wait_until="domcontentloaded", timeout=30000)
        meta["http_status"] = response.status if response else None
        page.wait_for_timeout(1800)
        for text in spec.get("clicks", []):
            if click_if_visible(page, text):
                meta["clicked_visible_controls"].append(text)
        bounded_scroll(page)
        page.wait_for_timeout(3500)
        text = normalized_text(page.locator("body").inner_text(timeout=5000))
        data = text.encode("utf-8")
        if len(data) > MAX_BYTES:
            raise RuntimeError(f"SOURCE_TOO_LARGE:{len(data)}")
        (OUT / f"{ticker}.txt").write_bytes(data)
        meta["final_url"] = page.url
        meta["title"] = page.title()
        meta["size_bytes"] = len(data)
        meta["sha256"] = sha256_bytes(data)
        meta["marker_counts"] = {marker: text.lower().count(marker.lower()) for marker in spec["markers"]}
        challenge_terms = [
            "failed to verify your browser",
            "security checkpoint",
            "sorry, you have been blocked",
            "access denied",
            "captcha",
            "website temporarily unavailable",
            "loading...",
        ]
        meta["challenge_markers"] = {term: text.lower().count(term) for term in challenge_terms}
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}:{exc}"
        try:
            meta["final_url"] = page.url
            meta["title"] = page.title()
        except Exception:
            pass
    meta["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    (OUT / f"{ticker}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return meta


def main() -> None:
    chrome = "/usr/bin/google-chrome"
    if not os.path.exists(chrome):
        raise SystemExit("SYSTEM_CHROME_NOT_FOUND")
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome)
        try:
            for ticker, spec in FUNDS.items():
                context = browser.new_context(locale="en-US")
                page = context.new_page()
                result = capture(page, ticker, spec)
                results.append(result)
                context.close()
                print(
                    f"{ticker}: http={result['http_status']} size={result['size_bytes']} "
                    f"sha256={result['sha256']} markers={result['marker_counts']} "
                    f"challenge={result['challenge_markers']} error={result['error']}"
                )
        finally:
            browser.close()
    summary = {
        "scope": "BTC_BTCO_CURRENT_OFFICIAL_PAGE_DIRECT_FIRST_PARTY_ONLY",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "funds": results,
        "secrets_used": False,
        "wave_alpha_private_source_copied": False,
        "third_party_proxy_used": False,
        "stealth_used": False,
        "challenge_bypass_used": False,
        "max_source_bytes": MAX_BYTES,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
