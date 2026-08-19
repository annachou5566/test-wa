import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

OUT = Path("artifacts/msbt-first-party")
OUT.mkdir(parents=True, exist_ok=True)

SOURCE_URL = "https://www.morganstanley.com/im/en-us/individual-investor/products/etfs/digital-assets/morgan-stanley-bitcoin-trust.html"
ISSUER_DOMAIN = "morganstanley.com"
MAX_BODY_BYTES = 512 * 1024
MAX_TOTAL_CAPTURE_BYTES = 4 * 1024 * 1024

KEY_TERMS = (
    "msbt",
    "61692g109",
    "100761",
    "shares outstanding",
    "sharesoutstanding",
    "share count",
    "holdings",
    "bitcoin",
    "nav",
    "daily characteristics",
)


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes):
    return hashlib.sha256(data).hexdigest()


def issuer_owned(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host == ISSUER_DOMAIN or host.endswith("." + ISSUER_DOMAIN)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:120]


def click_if_visible(page, text: str) -> bool:
    locator = page.get_by_text(text, exact=False).first
    try:
        if locator.is_visible(timeout=900):
            locator.click(timeout=2200)
            page.wait_for_timeout(500)
            return True
    except Exception:
        return False
    return False


def main():
    chrome = "/usr/bin/google-chrome"
    if not os.path.exists(chrome):
        raise SystemExit("SYSTEM_CHROME_NOT_FOUND")

    captured = []
    total_capture_bytes = 0
    request_rows = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome)
        context = browser.new_context(locale="en-US")
        page = context.new_page()

        def on_request(request):
            if not issuer_owned(request.url):
                return
            if request.resource_type not in {"xhr", "fetch", "document"}:
                return
            post_data = request.post_data or ""
            request_rows.append({
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "post_data": post_data[:8192] if len(post_data) <= 8192 else "POST_DATA_TOO_LARGE",
            })

        def on_response(response):
            nonlocal total_capture_bytes
            request = response.request
            if not issuer_owned(response.url):
                return
            if request.resource_type not in {"xhr", "fetch", "document"}:
                return
            row = {
                "url": response.url,
                "status": response.status,
                "method": request.method,
                "resource_type": request.resource_type,
                "content_type": response.headers.get("content-type", ""),
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
                if len(body) <= MAX_BODY_BYTES and total_capture_bytes + len(body) <= MAX_TOTAL_CAPTURE_BYTES:
                    text = body.decode("utf-8", errors="replace")
                    lowered = text.lower()
                    terms = [term for term in KEY_TERMS if term in lowered]
                    row["key_terms"] = terms
                    if terms:
                        path = OUT / f"response-{len(captured):03d}-{safe_name(urlparse(response.url).path or 'root')}.txt"
                        path.write_text(text, encoding="utf-8")
                        row["body_file"] = path.name
                        total_capture_bytes += len(body)
            except Exception as exc:
                row["body_error"] = f"{type(exc).__name__}:{exc}"
            captured.append(row)

        page.on("request", on_request)
        page.on("response", on_response)

        meta = {
            "scope": "MSBT_FIRST_PARTY_CURRENT_SOURCE_DISCOVERY",
            "source_url": SOURCE_URL,
            "issuer_domain_only": ISSUER_DOMAIN,
            "started_at_utc": now_utc(),
            "transport": "ordinary-system-chrome-direct-first-party",
            "third_party_response_bodies_captured": False,
            "proxy_used": False,
            "stealth_used": False,
            "challenge_bypass_used": False,
            "secrets_used": False,
            "navigation_http_status": None,
            "final_url": None,
            "title": None,
            "clicked_visible_controls": [],
            "error": None,
        }

        try:
            response = page.goto(SOURCE_URL, wait_until="domcontentloaded", timeout=35000)
            meta["navigation_http_status"] = response.status if response else None
            page.wait_for_timeout(1800)
            for text in ["Accept All", "Accept", "Individual Investor", "Continue"]:
                if click_if_visible(page, text):
                    meta["clicked_visible_controls"].append(text)
            for _ in range(12):
                page.evaluate("window.scrollBy(0, 800)")
                page.wait_for_timeout(300)
            page.wait_for_timeout(6000)
            body_text = page.locator("body").inner_text(timeout=5000)
            normalized = re.sub(r"[ \t]+", " ", body_text.replace("\xa0", " "))
            (OUT / "MSBT-page-text.txt").write_text(normalized[:512000], encoding="utf-8")
            context_rows = []
            lower = normalized.lower()
            for term in ["shares outstanding", "holdings", "daily characteristics", "nav", "61692g109", "msbt"]:
                idx = lower.find(term)
                context_rows.append({
                    "term": term,
                    "found": idx >= 0,
                    "context": normalized[max(0, idx - 400):idx + 1200] if idx >= 0 else None,
                })
            (OUT / "MSBT-dom-context.json").write_text(json.dumps(context_rows, indent=2, ensure_ascii=False), encoding="utf-8")
            meta["final_url"] = page.url
            meta["title"] = page.title()
        except Exception as exc:
            meta["error"] = f"{type(exc).__name__}:{exc}"

        meta["finished_at_utc"] = now_utc()
        meta["captured_response_count"] = len(captured)
        meta["candidate_response_count"] = sum(1 for row in captured if row["key_terms"])
        (OUT / "MSBT-requests.json").write_text(json.dumps(request_rows, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUT / "MSBT-network.json").write_text(json.dumps(captured, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUT / "summary.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        print(f"MSBT: http={meta['navigation_http_status']} responses={len(captured)} candidates={meta['candidate_response_count']} error={meta['error']}")
        for row in captured:
            if row["key_terms"]:
                print(f"MSBT_CANDIDATE status={row['status']} type={row['resource_type']} url={row['url']} terms={row['key_terms']} body={row['body_file']}")

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
