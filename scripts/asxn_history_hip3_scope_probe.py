from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

import asxn_history_aggregate_qualification as q

OUTPUT = Path("artifacts/asxn-history-hip3-scope/summary.json")
PREFIXES = ("xyz", "flx", "vntl", "hyna")


def symbol_name(row):
    for key in ("symbol", "coin", "name"):
        if row.get(key) is not None:
            return str(row[key])
    return ""


def compact(row):
    return q.compact_stats_row(row) if isinstance(row, dict) else None


def main() -> int:
    summary = {
        "contract": "ASXN_HISTORY_HIP3_SCOPE_Q1B_2026_08_27",
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "started_at": q.iso(),
        "source_only": True,
        "raw_events_requested": False,
        "raw_events_persisted": False,
        "production_mutation": False,
        "prefixes": {},
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="asxn-hip3-q1b-"))
    os.chmod(profile, 0o700)
    exit_code = 0
    try:
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not chrome:
            raise q.ProbeError("chrome_missing")
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile), executable_path=chrome, headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            q.verified_page(page, summary)
            symbol_rows, _ = q.fetch_rows(
                page, summary, "symbols24h",
                "/node/liquidations/stats/symbols?timeframe=24h&limit=all",
            )
            names = [symbol_name(row) for row in symbol_rows]
            summary["symbol_stats_row_count"] = len(symbol_rows)
            for prefix in PREFIXES:
                matching = sorted(name for name in names if name.lower().startswith(prefix + ":"))
                exemplar = matching[0] if matching else None
                daily_rows = []
                daily_status = None
                if exemplar:
                    encoded = quote(exemplar, safe="")
                    daily_rows, _ = q.fetch_rows(
                        page, summary, f"{prefix}Daily7",
                        f"/node/liquidations/daily/stats?symbol={encoded}&days=7",
                    )
                    daily_status = summary["requests"][f"{prefix}Daily7"]["status"]
                chart_rows, chart_raw = q.fetch_rows(
                    page, summary, f"{prefix}Chart7",
                    f"/meta/hip3/liquidations-chart?timeframe=7d&dex={prefix}",
                )
                summary["prefixes"][prefix] = {
                    "symbols_in_global_24h_stats": len(matching),
                    "exemplar_symbol": exemplar,
                    "exemplar_daily_status": daily_status,
                    "exemplar_daily_row_count": len(daily_rows),
                    "exemplar_daily_latest": compact(daily_rows[-1]) if daily_rows else None,
                    "hip3_chart_row_count": len(chart_rows),
                    "hip3_chart_top_keys": sorted(chart_raw.keys()) if isinstance(chart_raw, dict) else [],
                }
            context.close()
        summary["status"] = "Q1B_COMPLETE"
    except Exception as exc:
        summary["status"] = "Q1B_FAIL_CLOSED"
        summary["error"] = str(exc)
        exit_code = 1
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        summary["ended_at"] = q.iso()
        summary["browser_profile_persisted"] = False
        OUTPUT.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
