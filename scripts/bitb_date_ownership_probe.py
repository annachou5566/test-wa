import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

OUT = Path("artifacts/bitb-date-ownership")
OUT.mkdir(parents=True, exist_ok=True)
URL = "https://bitbetf.com/"
FIRST_PARTY_SUFFIXES = ("bitbetf.com", "bitwiseinvestments.com")
MAX_BODY_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 3 * 1024 * 1024
KEY_TERMS = (
    "shares outstanding",
    "bitcoin in trust",
    "bitcoin per share",
    "net assets",
    "net asset value",
    "data as of",
    "09174c104",
)


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def first_party(url):
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in FIRST_PARTY_SUFFIXES)


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:120]


def section_date(text, heading, next_heading=None):
    if next_heading:
        pattern = rf"{re.escape(heading)}(?P<body>.*?)(?={re.escape(next_heading)})"
    else:
        pattern = rf"{re.escape(heading)}(?P<body>.*)"
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None
    d = re.search(r"Data\s+as\s+of\s+(\d{1,2}/\d{1,2}/\d{4})", m.group("body"), re.I)
    return d.group(1) if d else None


def context(text, needle, radius=1400):
    i = text.lower().find(needle.lower())
    if i < 0:
        return None
    return text[max(0, i - 250): i + radius]


def main():
    chrome = "/usr/bin/google-chrome"
    if not os.path.exists(chrome):
        raise SystemExit("SYSTEM_CHROME_NOT_FOUND")

    request_rows = []
    response_rows = []
    total_capture = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome)
        ctx = browser.new_context(locale="en-US")
        page = ctx.new_page()

        def on_request(req):
            if req.resource_type not in {"document", "xhr", "fetch"}:
                return
            request_rows.append({
                "url": req.url,
                "method": req.method,
                "resource_type": req.resource_type,
                "first_party": first_party(req.url),
                "post_data": (req.post_data or "")[:4096],
            })

        def on_response(resp):
            nonlocal total_capture
            req = resp.request
            if req.resource_type not in {"document", "xhr", "fetch"}:
                return
            row = {
                "url": resp.url,
                "status": resp.status,
                "resource_type": req.resource_type,
                "first_party": first_party(resp.url),
                "content_type": resp.headers.get("content-type", ""),
                "size": None,
                "sha256": None,
                "terms": [],
                "body_file": None,
            }
            if row["first_party"]:
                try:
                    body = resp.body()
                    row["size"] = len(body)
                    row["sha256"] = sha256(body)
                    if len(body) <= MAX_BODY_BYTES and total_capture + len(body) <= MAX_TOTAL_BYTES:
                        text = body.decode("utf-8", errors="replace")
                        row["terms"] = [t for t in KEY_TERMS if t in text.lower()]
                        if row["terms"] or req.resource_type in {"xhr", "fetch"}:
                            path = OUT / f"response-{len(response_rows):03d}-{safe_name(urlparse(resp.url).path or 'root')}.txt"
                            path.write_text(text, encoding="utf-8")
                            row["body_file"] = path.name
                            total_capture += len(body)
                except Exception as exc:
                    row["body_error"] = f"{type(exc).__name__}:{exc}"
            response_rows.append(row)

        page.on("request", on_request)
        page.on("response", on_response)

        meta = {
            "scope": "BITB_DATE_OWNERSHIP_CURRENT_FIRST_PARTY",
            "url": URL,
            "started_at_utc": now_utc(),
            "transport": "ordinary-system-chrome-direct",
            "proxy_used": False,
            "stealth_used": False,
            "challenge_bypass_used": False,
            "secrets_used": False,
            "third_party_response_bodies_captured": False,
            "navigation_http_status": None,
            "final_url": None,
            "title": None,
            "error": None,
        }

        try:
            nav = page.goto(URL, wait_until="domcontentloaded", timeout=35000)
            meta["navigation_http_status"] = nav.status if nav else None
            page.wait_for_timeout(2500)
            for _ in range(12):
                page.evaluate("window.scrollBy(0, 900)")
                page.wait_for_timeout(250)
            page.wait_for_timeout(5500)
            text = page.locator("body").inner_text(timeout=6000)
            text = re.sub(r"[ \t]+", " ", text.replace("\xa0", " "))
            (OUT / "BITB-page-text.txt").write_text(text[:700000], encoding="utf-8")

            dates = {
                "fund_details": section_date(text, "Fund Details", "Fund Materials"),
                "nav_market_price": section_date(text, "Net Asset Value (NAV) and Market Price", "BITB Quarter-End Performance"),
                "fund_holdings": section_date(text, "Fund Holdings", "Proof of Reserves Transparency"),
                "proof_of_reserves": section_date(text, "Proof of Reserves Transparency"),
            }
            contexts = {
                key: context(text, needle)
                for key, needle in {
                    "fund_details": "Fund Details",
                    "nav": "Net Asset Value (NAV) and Market Price",
                    "holdings": "Fund Holdings",
                    "reserves": "Proof of Reserves Transparency",
                    "shares": "Shares Outstanding",
                }.items()
            }
            (OUT / "BITB-section-dates.json").write_text(json.dumps(dates, indent=2), encoding="utf-8")
            (OUT / "BITB-contexts.json").write_text(json.dumps(contexts, indent=2, ensure_ascii=False), encoding="utf-8")
            meta["section_dates"] = dates
            meta["core_same_date"] = bool(dates["fund_details"] and dates["fund_details"] == dates["fund_holdings"] == dates["nav_market_price"])
            meta["reserve_is_t_plus_1_candidate"] = bool(dates["proof_of_reserves"] and dates["fund_holdings"] and dates["proof_of_reserves"] != dates["fund_holdings"])
            meta["final_url"] = page.url
            meta["title"] = page.title()
        except Exception as exc:
            meta["error"] = f"{type(exc).__name__}:{exc}"

        meta["finished_at_utc"] = now_utc()
        meta["request_count"] = len(request_rows)
        meta["response_count"] = len(response_rows)
        meta["first_party_xhr_fetch_count"] = sum(1 for r in response_rows if r["first_party"] and r["resource_type"] in {"xhr", "fetch"})
        meta["candidate_first_party_response_count"] = sum(1 for r in response_rows if r.get("terms"))
        (OUT / "BITB-requests.json").write_text(json.dumps(request_rows, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUT / "BITB-network.json").write_text(json.dumps(response_rows, indent=2, ensure_ascii=False), encoding="utf-8")
        (OUT / "summary.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

        print("BITB_HTTP=" + str(meta["navigation_http_status"]))
        print("BITB_DATES=" + json.dumps(meta.get("section_dates", {}), sort_keys=True))
        print("BITB_CORE_SAME_DATE=" + str(meta.get("core_same_date")))
        print("BITB_FIRST_PARTY_XHR_FETCH=" + str(meta["first_party_xhr_fetch_count"]))
        for row in response_rows:
            if row["first_party"] and (row["resource_type"] in {"xhr", "fetch"} or row.get("terms")):
                print(f"BITB_RESPONSE status={row['status']} type={row['resource_type']} url={row['url']} terms={row.get('terms')} body={row.get('body_file')}")

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
