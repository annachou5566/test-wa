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
MAX_SMALL_BODY_BYTES = 4096
MAX_TOTAL_CAPTURE_BYTES = 128 * 1024

BTCO = {
    "url": "https://www.invesco.com/us/en/financial-products/etfs/invesco-galaxy-bitcoin-etf.html",
    "domain": "invesco.com",
    "clicks": ["Individual Investor", "Confirm"],
}


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(data: bytes):
    return hashlib.sha256(data).hexdigest()


def issuer_owned(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == BTCO["domain"] or host.endswith("." + BTCO["domain"])


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


def main():
    chrome = "/usr/bin/google-chrome"
    if not os.path.exists(chrome):
        raise SystemExit("SYSTEM_CHROME_NOT_FOUND")

    captured = []
    total_bytes = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome)
        context = browser.new_context(locale="en-US")
        page = context.new_page()

        def on_response(response):
            nonlocal total_bytes
            request = response.request
            if request.resource_type not in {"fetch", "xhr"} or not issuer_owned(response.url):
                return
            row = {
                "url": response.url,
                "status": response.status,
                "resource_type": request.resource_type,
                "content_type": response.headers.get("content-type", ""),
                "body_size": None,
                "sha256": None,
                "body_file": None,
                "body_error": None,
            }
            try:
                body = response.body()
                row["body_size"] = len(body)
                row["sha256"] = sha256(body)
                if len(body) <= MAX_SMALL_BODY_BYTES and total_bytes + len(body) <= MAX_TOTAL_CAPTURE_BYTES:
                    text = body.decode("utf-8", errors="replace")
                    idx = len(captured)
                    path = OUT / f"BTCO-small-{idx:03d}-{safe_name(urlparse(response.url).path or 'root')}.txt"
                    path.write_text(text, encoding="utf-8")
                    row["body_file"] = path.name
                    total_bytes += len(body)
            except Exception as exc:
                row["body_error"] = f"{type(exc).__name__}:{exc}"
            captured.append(row)

        page.on("response", on_response)
        meta = {
            "scope": "BTCO_FIRST_PARTY_SMALL_DATA_RESPONSES_ONLY",
            "started_at_utc": now_utc(),
            "source_url": BTCO["url"],
            "issuer_domain_only": BTCO["domain"],
            "third_party_response_bodies_captured": False,
            "proxy_used": False,
            "stealth_used": False,
            "challenge_bypass_used": False,
            "secrets_used": False,
            "error": None,
        }
        try:
            response = page.goto(BTCO["url"], wait_until="domcontentloaded", timeout=35000)
            meta["navigation_http_status"] = response.status if response else None
            page.wait_for_timeout(1800)
            for text in BTCO["clicks"]:
                click_if_visible(page, text)
            for _ in range(10):
                page.evaluate("window.scrollBy(0, 750)")
                page.wait_for_timeout(250)
            page.wait_for_timeout(5000)
        except Exception as exc:
            meta["error"] = f"{type(exc).__name__}:{exc}"
        meta["finished_at_utc"] = now_utc()
        meta["captured_response_count"] = len(captured)
        (OUT / "BTCO-small-network.json").write_text(json.dumps(captured, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUT / "summary.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        for row in captured:
            print(f"BTCO_RESPONSE status={row['status']} size={row['body_size']} url={row['url']} body={row['body_file']}")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
