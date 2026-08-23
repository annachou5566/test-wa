#!/usr/bin/env python3
import collections
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://mainnet.zklighter.elliot.ai"
TIMEOUT = 15
USER_AGENT = "WaveAlpha-QA-Lighter-Source-Qualification/1.0"


def get_json(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read()
            status = response.status
            content_type = response.headers.get("content-type", "")
    except urllib.error.HTTPError as error:
        body = error.read()
        status = error.code
        content_type = error.headers.get("content-type", "")
    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        payload = None
    return {
        "url_path": path,
        "status": status,
        "content_type": content_type,
        "bytes": len(body),
        "payload": payload,
    }


def parse_json_string(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = json.loads(value)
    except Exception:
        return None
    return parsed


def flatten_keys(value, prefix="", depth=0):
    if depth > 3:
        return set()
    keys = set()
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            keys.add(path)
            keys |= flatten_keys(child, path, depth + 1)
    elif isinstance(value, list):
        for child in value[:10]:
            keys |= flatten_keys(child, prefix + "[]", depth + 1)
    return keys


def marker_text(value):
    if value is None:
        return ""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).lower()
    except Exception:
        return str(value).lower()


def summarize_txs(label, result):
    payload = result["payload"]
    txs = payload.get("txs") if isinstance(payload, dict) else None
    summary = {
        "label": label,
        "http": result["status"],
        "content_type": result["content_type"],
        "response_bytes": result["bytes"],
        "code": payload.get("code") if isinstance(payload, dict) else None,
        "row_count": len(txs) if isinstance(txs, list) else None,
    }
    if not isinstance(txs, list):
        return summary

    type_counts = collections.Counter()
    tx_indexes = []
    seq_indexes = []
    block_heights = []
    top_info_keys = set()
    top_event_keys = set()
    marker_types = collections.Counter()
    marker_field_sources = collections.Counter()
    json_info_count = 0
    json_event_count = 0

    for tx in txs:
        if not isinstance(tx, dict):
            continue
        tx_type = tx.get("type")
        type_counts[str(tx_type)] += 1
        for key, target in (
            ("transaction_index", tx_indexes),
            ("sequence_index", seq_indexes),
            ("block_height", block_heights),
        ):
            value = tx.get(key)
            if isinstance(value, int):
                target.append(value)

        info = parse_json_string(tx.get("info"))
        event = parse_json_string(tx.get("event_info"))
        if info is not None:
            json_info_count += 1
            top_info_keys |= flatten_keys(info)
        if event is not None:
            json_event_count += 1
            top_event_keys |= flatten_keys(event)

        marked = False
        if "liq" in marker_text(info):
            marker_field_sources["info"] += 1
            marked = True
        if "liq" in marker_text(event):
            marker_field_sources["event_info"] += 1
            marked = True
        if marked:
            marker_types[str(tx_type)] += 1

    def bounds(values):
        return [min(values), max(values)] if values else None

    summary.update({
        "type_counts": dict(sorted(type_counts.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 9999)),
        "transaction_index_range": bounds(tx_indexes),
        "sequence_index_range": bounds(seq_indexes),
        "block_height_range": bounds(block_heights),
        "json_info_count": json_info_count,
        "json_event_info_count": json_event_count,
        "info_keys": sorted(top_info_keys),
        "event_info_keys": sorted(top_event_keys),
        "liq_marker_count": sum(marker_types.values()),
        "liq_marker_types": dict(marker_types),
        "liq_marker_sources": dict(marker_field_sources),
    })
    return summary


def main():
    status = get_json("/")
    output = {
        "probe": "lighter-public-source-shape-v1",
        "base": BASE,
        "status_http": status["status"],
        "status_json": isinstance(status["payload"], dict),
        "tx_reads": [],
        "raw_transactions_persisted": False,
        "credentials_used": False,
    }

    queries = [
        ("latest", {"limit": 100}),
        ("index-0", {"limit": 100, "index": 0}),
        ("index-100", {"limit": 100, "index": 100}),
        ("index-1000", {"limit": 100, "index": 1000}),
    ]
    for label, params in queries:
        result = get_json("/api/v1/txs", params)
        output["tx_reads"].append(summarize_txs(label, result))

    print(json.dumps(output, indent=2, sort_keys=True))

    latest = output["tx_reads"][0]
    if output["status_http"] != 200 or latest.get("http") != 200 or latest.get("row_count") is None:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
