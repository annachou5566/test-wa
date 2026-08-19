import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

OUT = Path("artifacts/grayscale-gbtc-btc-source")
OUT.mkdir(parents=True, exist_ok=True)

FUNDS = {
    "GBTC": "https://etfs.grayscale.com/gbtc",
    "BTC": "https://etfs.grayscale.com/btc",
}

ALLOWED_SUFFIX = "grayscale.com"
MAX_BODY_BYTES = 512 * 1024
MAX_TOTAL_CAPTURE_BYTES = 4 * 1024 * 1024

KEY_TERMS = (
    "shares outstanding",
    "sharesoutstanding",
    "total bitcoin",
    "bitcoin per share",
    "bitcoinpershare",
    "net asset value",
    "nav per share",
    "gbtc",
    "grayscale bitcoin mini trust",
    "389637109",
)


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes):
    return hashlib.sha256(data).hexdigest()


def first_party(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host == ALLOWED_SUFFIX or host.endswith("." + ALLOWED_SUFFIX)


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:120]


def capture_one(browser, ticker: str, url: str):
    context = browser.new_context(locale="en-US")
    page = context.new_page()
    responses = []
    requests = []
    total_capture = 0

    def on_request(request):
        if not first_party(request.url):
            return
        if request.resource_type not in {"document", "xhr", "fetch"}:
            return
        post_data = request.post_data or ""
        requests.append({
            "url": request.url,
            "method": request.method,
            "resource_type": request.resource_type,
            "post_data": post_data[:8192] if len(post_data) <= 8192 else "POST_DATA_TOO_LARGE",
        })

    def on_response(response):
        nonlocal total_capture
        request = response.request
        if not first_party(response.url):
            return
        if request.resource_type not in {"document", "xhr", "fetch"}:
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
            if len(body) <= MAX_BODY_BYTES and total_capture + len(body) <= MAX_TOTAL_CAPTURE_BYTES:
                text = body.decode("utf-8", errors="replace")
                lowered = text.lower()
                terms = [term for term in KEY_TERMS if term in lowered]
                row["key_terms"] = terms
                if terms or request.resource_type in {"xhr", "fetch"}:
                    path = OUT / f"{ticker}-response-{len(responses):03d}-{safe_name(urlparse(response.url).path or 'root')}.txt"
                    path.write_text(text, encoding="utf-8")
                    row["body_file"] = path.name
                    total_capture += len(body)
        except Exception as exc:
            row["body_error"] = f"{type(exc).__name__}:{exc}"
        responses.append(row)

    page.on("request", on_request)
    page.on("response", on_response)

    meta = {
        "ticker": ticker,
        "source_url": url,
        "started_at_utc": now_utc(),
        "transport": "ordinary-system-chrome-direct-first-party",
        "first_party_suffix": ALLOWED_SUFFIX,
        "third_party_response_bodies_captured": False,
        "proxy_used": False,
        "stealth_used": False,
        "challenge_bypass_used": False,
        "secrets_used": False,
        "navigation_http_status": None,
        "final_url": None,
        "title": None,
        "error": None,
    }

    try:
        nav = page.goto(url, wait_until="domcontentloaded", timeout=35000)
        meta["navigation_http_status"] = nav.status if nav else None
        page.wait_for_timeout(2500)
        for _ in range(12):
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(250)
        page.wait_for_timeout(5000)
        body_text = page.locator("body").inner_text(timeout=5000)
        normalized = re.sub(r"[ \t]+", " ", body_text.replace("\xa0", " "))
        (OUT / f"{ticker}-page-text.txt").write_text(normalized[:512000], encoding="utf-8")
        contexts = []
        lower = normalized.lower()
        for term in ["shares outstanding", "total bitcoin", "bitcoin per share", "nav per share", ticker.lower()]:
            idx = lower.find(term)
            contexts.append({
                "term": term,
                "found": idx >= 0,
                "context": normalized[max(0, idx - 500):idx + 1600] if idx >= 0 else None,
            })
        (OUT / f"{ticker}-dom-context.json").write_text(json.dumps(contexts, indent=2, ensure_ascii=False), encoding="utf-8")
        meta["final_url"] = page.url
        meta["title"] = page.title()
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}:{exc}"

    meta["finished_at_utc"] = now_utc()
    meta["request_count"] = len(requests)
    meta["response_count"] = len(responses)
    meta["xhr_fetch_count"] = sum(1 for r in responses if r["resource_type"] in {"xhr", "fetch"})
    meta["candidate_response_count"] = sum(1 for r in responses if r["key_terms"])
    (OUT / f"{ticker}-requests.json").write_text(json.dumps(requests, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / f"{ticker}-network.json").write_text(json.dumps(responses, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / f"{ticker}-meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"{ticker}: http={meta['navigation_http_status']} xhr_fetch={meta['xhr_fetch_count']} candidates={meta['candidate_response_count']} error={meta['error']}")
    for row in responses:
        if row["key_terms"] or row["resource_type"] in {"xhr", "fetch"}:
            print(f"{ticker}_RESPONSE status={row['status']} type={row['resource_type']} url={row['url']} terms={row['key_terms']} body={row['body_file']}")

    context.close()
    return meta


def main():
    chrome = "/usr/bin/google-chrome"
    if not os.path.exists(chrome):
        raise SystemExit("SYSTEM_CHROME_NOT_FOUND")

    summary = {
        "scope": "GRAYSCALE_GBTC_BTC_FIRST_PARTY_DAILY_OWNER_DISCOVERY",
        "retrieved_at_utc": now_utc(),
        "secrets_used": False,
        "wave_alpha_private_source_copied": False,
        "third_party_response_bodies_captured": False,
        "proxy_used": False,
        "stealth_used": False,
        "challenge_bypass_used": False,
        "funds": [],
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome)
        try:
            for ticker, url in FUNDS.items():
                summary["funds"].append(capture_one(browser, ticker, url))
        finally:
            browser.close()

    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
