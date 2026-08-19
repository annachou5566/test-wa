import csv
import hashlib
import io
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("artifacts/ibit-final-flow")
OUT.mkdir(parents=True, exist_ok=True)
PAGE_URL = "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf"
CSV_URL = "https://www.ishares.com/us/products/333011/ishares-bitcoin-trust-etf/latest-holdings.csv"
MAX_PAGE_TEXT = 800_000
MAX_BODY = 2 * 1024 * 1024


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_key_fact(text: str, label: str):
    pattern = rf"{re.escape(label)}\s+([^\n]+?)\s+as of\s+([A-Za-z]{{3}}\s+\d{{1,2}},\s+\d{{4}})"
    m = re.search(pattern, text, re.I | re.S)
    if not m:
        return None
    raw_value = re.sub(r"\s+", " ", m.group(1)).strip()
    return {"raw_value": raw_value, "as_of": m.group(2)}


def parse_int_value(raw):
    if raw is None:
        return None
    m = re.search(r"-?[\d,]+", raw)
    return int(m.group(0).replace(",", "")) if m else None


def parse_money(raw):
    if raw is None:
        return None
    m = re.search(r"-?\$?([\d,]+(?:\.\d+)?)", raw)
    return float(m.group(1).replace(",", "")) if m else None


def parse_decimal(raw):
    if raw is None:
        return None
    m = re.search(r"-?([\d,]+(?:\.\d+)?)", raw)
    return float(m.group(1).replace(",", "")) if m else None


def parse_csv(raw: bytes):
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    meta = {}
    header_idx = None
    for i, row in enumerate(rows):
        if row and row[0] == "Ticker":
            header_idx = i
            break
        if len(row) >= 2 and row[0].strip():
            meta[row[0].strip()] = row[1].strip()
    if header_idx is None:
        raise RuntimeError("IBIT_CSV_HEADER_NOT_FOUND")
    header = rows[header_idx]
    objs = [dict(zip(header, row + [""] * max(0, len(header) - len(row)))) for row in rows[header_idx + 1:] if row]
    btc = next((r for r in objs if r.get("Ticker") == "BTC"), None)
    return {
        "fund_holdings_as_of": meta.get("Fund Holdings as of"),
        "shares_outstanding_raw": meta.get("Shares Outstanding"),
        "shares_outstanding": parse_int_value(meta.get("Shares Outstanding")),
        "btc_quantity": parse_decimal(btc.get("Quantity")) if btc else None,
    }


def main():
    chrome = "/usr/bin/google-chrome"
    if not os.path.exists(chrome):
        raise SystemExit("SYSTEM_CHROME_NOT_FOUND")

    summary = {
        "scope": "IBIT_FINAL_FLOW_SOURCE_QUALIFICATION",
        "started_at_utc": now_utc(),
        "page_url": PAGE_URL,
        "csv_url": CSV_URL,
        "transport": "ordinary-system-chrome-plus-direct-first-party-csv",
        "proxy_used": False,
        "stealth_used": False,
        "challenge_bypass_used": False,
        "secrets_used": False,
        "page_http": None,
        "page_title": None,
        "page_fields": {},
        "csv": None,
        "error": None,
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=chrome)
            page = browser.new_page(locale="en-US")
            nav = page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=35000)
            summary["page_http"] = nav.status if nav else None
            page.wait_for_timeout(3500)
            for _ in range(8):
                page.evaluate("window.scrollBy(0, 1000)")
                page.wait_for_timeout(200)
            page.wait_for_timeout(3000)
            text = page.locator("body").inner_text(timeout=7000)
            text = text.replace("\xa0", " ")
            (OUT / "page-text.txt").write_text(text[:MAX_PAGE_TEXT], encoding="utf-8")
            summary["page_title"] = page.title()
            for label in ["Shares Outstanding", "Basket Amount", "Basket Bitcoin Amount", "Indicative Basket Bitcoin Amount", "Net Assets of Fund"]:
                summary["page_fields"][label] = parse_key_fact(text, label)
            browser.close()

        req = urllib.request.Request(CSV_URL, method="GET")
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise RuntimeError("IBIT_CSV_TOO_LARGE")
            (OUT / "latest-holdings.csv").write_bytes(body)
            summary["csv_http"] = resp.status
            summary["csv_sha256"] = sha256(body)
            summary["csv"] = parse_csv(body)

        pf = summary["page_fields"]
        page_shares = parse_int_value((pf.get("Shares Outstanding") or {}).get("raw_value"))
        page_basket_usd = parse_money((pf.get("Basket Amount") or {}).get("raw_value"))
        page_basket_btc = parse_decimal((pf.get("Basket Bitcoin Amount") or {}).get("raw_value"))
        page_date = (pf.get("Shares Outstanding") or {}).get("as_of")
        basket_date = (pf.get("Basket Amount") or {}).get("as_of")
        basket_btc_date = (pf.get("Basket Bitcoin Amount") or {}).get("as_of")
        summary["normalized"] = {
            "page_shares": page_shares,
            "page_basket_amount_usd": page_basket_usd,
            "page_basket_btc": page_basket_btc,
            "page_shares_date": page_date,
            "page_basket_date": basket_date,
            "page_basket_btc_date": basket_btc_date,
            "page_core_same_date": bool(page_date and page_date == basket_date == basket_btc_date),
            "page_csv_shares_equal": bool(page_shares is not None and summary["csv"] and page_shares == summary["csv"].get("shares_outstanding")),
        }
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}:{exc}"

    summary["finished_at_utc"] = now_utc()
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print("IBIT_PAGE_HTTP=" + str(summary.get("page_http")))
    print("IBIT_CSV_HTTP=" + str(summary.get("csv_http")))
    print("IBIT_PAGE_FIELDS=" + json.dumps(summary.get("page_fields", {}), sort_keys=True))
    print("IBIT_CSV=" + json.dumps(summary.get("csv"), sort_keys=True))
    print("IBIT_NORMALIZED=" + json.dumps(summary.get("normalized", {}), sort_keys=True))
    print("IBIT_ERROR=" + str(summary.get("error")))


if __name__ == "__main__":
    main()
