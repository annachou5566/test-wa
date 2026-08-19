#!/usr/bin/env python3
"""One-shot public first-party DEFI XLSX schema probe. No Wave Alpha private source or secrets."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

URL = "https://hdx-website-cms-prod-upload-bucket.s3.amazonaws.com/DEFI_Holdings.xlsx"
OUT = Path("artifacts/defi-xlsx-schema")
OUT.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 4 * 1024 * 1024
NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "p": "http://schemas.openxmlformats.org/package/2006/relationships",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def col_index(cell_ref: str) -> int:
    letters = re.match(r"[A-Z]+", cell_ref or "")
    if not letters:
        return -1
    value = 0
    for ch in letters.group(0):
        value = value * 26 + (ord(ch) - 64)
    return value - 1


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values = []
    for si in root.findall("m:si", NS):
        values.append("".join((node.text or "") for node in si.findall(".//m:t", NS)))
    return values


def workbook_sheets(zf: zipfile.ZipFile) -> list[dict]:
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    targets = {r.attrib["Id"]: r.attrib["Target"] for r in rels.findall("p:Relationship", NS)}
    out = []
    for sheet in wb.findall("m:sheets/m:sheet", NS):
        rid = sheet.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = targets.get(rid, "")
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target.lstrip("./")
        out.append({"name": sheet.attrib.get("name"), "sheet_id": sheet.attrib.get("sheetId"), "path": path})
    return out


def parse_sheet(zf: zipfile.ZipFile, path: str, sst: list[str], max_rows: int = 120) -> dict:
    root = ET.fromstring(zf.read(path))
    dim = root.find("m:dimension", NS)
    rows_out = []
    for row in root.findall("m:sheetData/m:row", NS):
        values = {}
        for cell in row.findall("m:c", NS):
            ref = cell.attrib.get("r", "")
            ctype = cell.attrib.get("t")
            style = cell.attrib.get("s")
            formula = cell.find("m:f", NS)
            v = cell.find("m:v", NS)
            inline = cell.find("m:is", NS)
            raw = None
            if inline is not None:
                raw = "".join((node.text or "") for node in inline.findall(".//m:t", NS))
            elif v is not None:
                raw = v.text
                if ctype == "s" and raw is not None:
                    try:
                        raw = sst[int(raw)]
                    except Exception:
                        pass
                elif ctype == "b":
                    raw = raw == "1"
            if raw not in (None, "") or formula is not None:
                values[ref] = {
                    "value": raw,
                    "type": ctype,
                    "style": style,
                    "formula": formula.text if formula is not None else None,
                    "col_index": col_index(ref),
                }
        if values:
            rows_out.append({"row": int(row.attrib.get("r", "0")), "cells": values})
        if len(rows_out) >= max_rows:
            break
    return {"dimension": dim.attrib.get("ref") if dim is not None else None, "nonempty_rows": rows_out}


def main() -> None:
    req = urllib.request.Request(
        URL,
        headers={
            "User-Agent": "Mozilla/5.0 WaveAlphaPublicSourceProbe/1.0",
            "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/octet-stream,*/*;q=0.5",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read(MAX_BYTES + 1)
        meta = {
            "source_url": URL,
            "http_status": response.status,
            "content_type": response.headers.get("content-type"),
            "content_length_header": response.headers.get("content-length"),
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
        }
    if len(body) > MAX_BYTES:
        raise SystemExit(f"SOURCE_TOO_LARGE:{len(body)}")
    if not body.startswith(b"PK"):
        raise SystemExit("NOT_XLSX_ZIP")

    raw_path = OUT / "DEFI_Holdings.xlsx"
    raw_path.write_bytes(body)
    meta.update({"size_bytes": len(body), "sha256": sha256(body)})

    with zipfile.ZipFile(raw_path) as zf:
        sst = shared_strings(zf)
        sheets = workbook_sheets(zf)
        manifest = {
            "source": meta,
            "shared_strings_count": len(sst),
            "sheets": [],
            "zip_members": sorted(zf.namelist()),
        }
        for sheet in sheets:
            manifest["sheets"].append({**sheet, **parse_sheet(zf, sheet["path"], sst)})
        try:
            core = ET.fromstring(zf.read("docProps/core.xml"))
            manifest["core_properties_raw"] = {child.tag.split("}")[-1]: child.text for child in core}
        except KeyError:
            manifest["core_properties_raw"] = None

    (OUT / "schema.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "probe": "DEFI_FIRST_PARTY_XLSX_SCHEMA",
        "secrets_used": False,
        "wave_alpha_private_source_copied": False,
        "network_owner": "first-party-hashdex-only",
        "source": meta,
        "sheet_names": [s["name"] for s in manifest["sheets"]],
        "sheet_dimensions": {s["name"]: s["dimension"] for s in manifest["sheets"]},
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
