import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

OUT = Path("artifacts/btco-brrr-exact-shares")
OUT.mkdir(parents=True, exist_ok=True)
MAX_BODY_BYTES = 512 * 1024
MAX_TOTAL_CAPTURE_BYTES = 4 * 1024 * 1024

FUNDS = {
    "BTCO": {
        "url": "https://www.invesco.com/us/en/financial-products/etfs/invesco-galaxy-bitcoin-etf.html",
        "domain": "invesco.com",
        "clicks": ["Individual Investor", "Confirm"],
        "labels": ["Shares outstanding", "Total units of crypto", "Basket value at market close", "Export data"],
    },
    "BRRR": {
        "url": "https://coinshares.com/us/etf/brrr/",
        "domain": "coinshares.com",
        "clicks": [],
        "labels": ["Shares outstanding", "Key information", "BRRR Holdings", "AuM", "NAV"],
    },
}

KEY_TERMS = (
    "shares outstanding",
    "sharesoutstanding",
    "shares_outstanding",
    "outstanding shares",
    "share count",
    "sharecount",
    "btco",
    "brrr",
)


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes):
    return hashlib.sha256(data).hexdigest()


def issuer_owned(url: str, domain: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host == domain or host.endswith("." + domain)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:120]


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


def bounded_scroll(page):
    for _ in range(10):
        page.evaluate("window.scrollBy(0, 750)")
        page.wait_for_timeout(250)
    page.evaluate("window.scrollTo(0, 0)")


def capture_dom_context(page, ticker: str, labels):
    rows = []
    for label in labels:
        locator = page.get_by_text(label, exact=False).first
        try:
            if not locator.is_visible(timeout=800):
                rows.append({"label": label, "visible": False})
                continue
            text = locator.inner_text(timeout=1000)
            outer = locator.evaluate("el => el.outerHTML")
            parent = locator.evaluate("el => el.parentElement ? el.parentElement.outerHTML : ''")
            rows.append({
                "label": label,
                "visible": True,
                "text": text[:1000],
                "outer_html": outer[:4000],
                "parent_html": parent[:8000],
            })
        except Exception as exc:
            rows.append({"label": label, "visible": None, "error": f"{type(exc).__name__}:{exc}"})
    (OUT / f"{ticker}-dom-context.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def capture_fund(browser, ticker: str, spec: dict):
    context = browser.new_context(locale="en-US", accept_downloads=True)
    page = context.new_page()
    captured = []
    total_bytes = 0

    def on_response(response):
        nonlocal total_bytes
        request = response.request
        url = response.url
        if not issuer_owned(url, spec["domain"]):
            return
        resource_type = request.resource_type
        if resource_type not in {"xhr", "fetch", "document"}:
            return
        headers = response.headers
        content_type = headers.get("content-type", "")
        row = {
            "url": url,
            "status": response.status,
            "method": request.method,
            "resource_type": resource_type,
            "content_type": content_type,
            "body_size": None,
            "sha256": None,
            "key_terms": [],
            "body_file": None,
            "body_error": None,
        }
        try:
            body = response.body()
            row["body_size"] = len(body)
            row["sha256"] = sha256(body)
            if len(body) <= MAX_BODY_BYTES and total_bytes + len(body) <= MAX_TOTAL_CAPTURE_BYTES:
                text = body.decode("utf-8", errors="replace")
                lowered = text.lower()
                terms = [term for term in KEY_TERMS if term in lowered]
                row["key_terms"] = terms
                if terms:
                    idx = len(captured)
                    path = OUT / f"{ticker}-response-{idx:03d}-{safe_name(urlparse(url).path or 'root')}.txt"
                    path.write_text(text, encoding="utf-8")
                    row["body_file"] = path.name
                    total_bytes += len(body)
        except Exception as exc:
            row["body_error"] = f"{type(exc).__name__}:{exc}"
        captured.append(row)

    page.on("response", on_response)
    meta = {
        "ticker": ticker,
        "source_url": spec["url"],
        "started_at_utc": now_utc(),
        "transport": "ordinary-system-chrome-direct-first-party-network-metadata",
        "issuer_domain_only": spec["domain"],
        "third_party_response_bodies_captured": False,
        "stealth_used": False,
        "proxy_used": False,
        "challenge_bypass_used": False,
        "clicked_visible_controls": [],
        "navigation_http_status": None,
        "final_url": None,
        "title": None,
        "error": None,
    }
    try:
        response = page.goto(spec["url"], wait_until="domcontentloaded", timeout=35000)
        meta["navigation_http_status"] = response.status if response else None
        page.wait_for_timeout(1800)
        for label in spec.get("clicks", []):
            if click_if_visible(page, label):
                meta["clicked_visible_controls"].append(label)
        bounded_scroll(page)
        page.wait_for_timeout(5000)
        capture_dom_context(page, ticker, spec["labels"])
        meta["final_url"] = page.url
        meta["title"] = page.title()
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}:{exc}"
    meta["finished_at_utc"] = now_utc()
    meta["captured_response_count"] = len(captured)
    meta["candidate_response_count"] = sum(1 for row in captured if row["key_terms"])
    (OUT / f"{ticker}-network.json").write_text(json.dumps(captured, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / f"{ticker}-meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    context.close()
    return meta, captured


def main():
    chrome = "/usr/bin/google-chrome"
    if not os.path.exists(chrome):
        raise SystemExit("SYSTEM_CHROME_NOT_FOUND")
    summary = {
        "scope": "BTCO_BRRR_EXACT_DAILY_SHARES_FIRST_PARTY_NETWORK_DISCOVERY",
        "retrieved_at_utc": now_utc(),
        "secrets_used": False,
        "wave_alpha_private_source_copied": False,
        "third_party_response_bodies_captured": False,
        "stealth_used": False,
        "proxy_used": False,
        "challenge_bypass_used": False,
        "funds": [],
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome)
        try:
            for ticker, spec in FUNDS.items():
                meta, rows = capture_fund(browser, ticker, spec)
                candidates = [row for row in rows if row["key_terms"]]
                summary["funds"].append({
                    "ticker": ticker,
                    "navigation_http_status": meta["navigation_http_status"],
                    "error": meta["error"],
                    "captured_response_count": len(rows),
                    "candidate_response_count": len(candidates),
                    "candidate_urls": [row["url"] for row in candidates],
                })
                print(f"{ticker}: http={meta['navigation_http_status']} responses={len(rows)} candidates={len(candidates)} error={meta['error']}")
                for row in candidates:
                    print(f"{ticker}_CANDIDATE status={row['status']} type={row['resource_type']} url={row['url']} terms={row['key_terms']} body={row['body_file']}")
        finally:
            browser.close()
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
