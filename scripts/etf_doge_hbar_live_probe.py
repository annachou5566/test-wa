#!/usr/bin/env python3
"""One-shot public first-party DOGE/HBAR acquisition probe. No private Wave Alpha source or secrets."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path("artifacts/doge-hbar-live")
OUT.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
SOURCES = [
    {
        "asset": "DOGE",
        "ticker": "BWOW",
        "url": "https://bwowetf.com/",
        "markers": ["Holdings", "DOGE in Trust", "DOGE per Share", "Fund Details", "Shares Outstanding", "Net Asset Value"],
    },
    {
        "asset": "HBAR",
        "ticker": "HBR",
        "url": "https://canaryetfs.com/hbr/",
        "markers": ["Holdings", "HBARUSD", "SharesOutstanding", "CreationUnits", "NetAssets"],
    },
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def bounded_read(response) -> bytes:
    parts: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise RuntimeError(f"SOURCE_TOO_LARGE:{total}>{MAX_BYTES}")
        parts.append(chunk)
    return b"".join(parts)


def fetch(source: dict[str, object]) -> dict[str, object]:
    retrieved = now()
    request = urllib.request.Request(
        str(source["url"]),
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    body = b""
    status = 0
    final_url = str(source["url"])
    content_type = ""
    etag = None
    last_modified = None
    error = None
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = bounded_read(response)
            status = int(getattr(response, "status", 0) or 0)
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            etag = response.headers.get("ETag")
            last_modified = response.headers.get("Last-Modified")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        final_url = exc.geturl()
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        etag = exc.headers.get("ETag") if exc.headers else None
        last_modified = exc.headers.get("Last-Modified") if exc.headers else None
        body = bounded_read(exc)
        error = f"HTTP_ERROR:{exc.code}"
    except Exception as exc:
        error = f"FETCH_ERROR:{type(exc).__name__}:{exc}"

    ticker = str(source["ticker"])
    artifact = OUT / f"{ticker}.html"
    artifact.write_bytes(body)
    text = body.decode("utf-8", errors="replace")
    marker_counts = {str(marker): text.lower().count(str(marker).lower()) for marker in source["markers"]}
    return {
        "asset": source["asset"],
        "ticker": ticker,
        "source_url": source["url"],
        "final_url": final_url,
        "acquisition_method": "urllib_stream_bounded_direct_issuer_html",
        "retrieved_at_utc": retrieved,
        "http_status": status,
        "content_type": content_type,
        "etag": etag,
        "last_modified": last_modified,
        "size_bytes": len(body),
        "sha256": sha256(body),
        "under_2mib": len(body) <= MAX_BYTES,
        "artifact_path": str(artifact),
        "markers": marker_counts,
        "error": error,
    }


def main() -> None:
    started = now()
    results = [fetch(source) for source in SOURCES]
    summary = {
        "probe": "US_DOGE_HBAR_LIVE_ACQUISITION",
        "started_at_utc": started,
        "finished_at_utc": now(),
        "network_owner": "public-first-party-issuer-only",
        "secrets_used": False,
        "wave_alpha_private_source_copied": False,
        "stealth_or_challenge_evasion_used": False,
        "waf_bypass_used": False,
        "max_capture_bytes_per_source": MAX_BYTES,
        "one_acquisition_per_fund": True,
        "results": results,
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
