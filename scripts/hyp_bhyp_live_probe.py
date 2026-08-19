#!/usr/bin/env python3
"""One-shot public first-party BHYP acquisition probe. No private Wave Alpha source or secrets."""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://www.bhypetf.com/"
OUT = Path("artifacts/hyp-bhyp-live")
OUT.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
MARKERS = [
    "Holdings",
    "Fund Details",
    "Shares Outstanding",
    "Hyperliquid in Trust",
    "Hyperliquid per Share",
    "Hyperliquid Staking Details",
    "Net Asset Value",
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(data: bytes) -> str:
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


def visible_text(raw_html: str) -> str:
    text = re.sub(r"(?is)<script\b.*?</script>", " ", raw_html)
    text = re.sub(r"(?is)<style\b.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def marker_report(text: str) -> dict[str, int]:
    low = text.lower()
    return {marker: low.count(marker.lower()) for marker in MARKERS}


def main() -> None:
    started = now()
    request = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        body = bounded_read(response)
        status = int(getattr(response, "status", 0) or 0)
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
        etag = response.headers.get("ETag")
        last_modified = response.headers.get("Last-Modified")

    artifact = OUT / "BHYP.html"
    artifact.write_bytes(body)
    text = visible_text(body.decode("utf-8", errors="replace"))
    result = {
        "ticker": "BHYP",
        "source_url": URL,
        "final_url": final_url,
        "acquisition_method": "urllib_stream_bounded_direct_issuer_html",
        "retrieved_at_utc": started,
        "http_status": status,
        "content_type": content_type,
        "size_bytes": len(body),
        "sha256": digest(body),
        "etag": etag,
        "last_modified": last_modified,
        "under_2mib": len(body) <= MAX_BYTES,
        "artifact_path": str(artifact),
        "markers": marker_report(text),
    }
    summary = {
        "probe": "US_HYP_BHYP_LIVE_ACQUISITION",
        "started_at_utc": started,
        "finished_at_utc": now(),
        "network_owner": "public-first-party-issuer-only",
        "secrets_used": False,
        "wave_alpha_private_source_copied": False,
        "stealth_or_challenge_evasion_used": False,
        "waf_bypass_used": False,
        "max_capture_bytes": MAX_BYTES,
        "results": [result],
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
