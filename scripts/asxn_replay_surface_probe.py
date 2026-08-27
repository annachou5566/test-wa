from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

import asxn_stage3a_canary as core

HOST = "https://api-hyperliquid.asxn.xyz"
OPENAPI_CANDIDATES = (
    HOST + "/openapi.json",
    HOST + "/api/openapi.json",
    HOST + "/docs/openapi.json",
)
OUTPUT = Path("artifacts/asxn-replay-surface/summary.json")
CONTRACT = "ASXN_API_CONTRACT_DISCOVERY_PROBE_V3"


class StopProbe(RuntimeError):
    pass


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def effective_fetch(page, target: str, summary: dict[str, Any]) -> tuple[int, Any | None, float]:
    status, data, latency_ms = core.browser_fetch_json(page, target)
    core.observe_resource(summary)
    if status == 429:
        raise StopProbe("http_429_provider_pressure")
    if status == 403:
        core.verify_same_context(page, summary, reason="openapi_403")
        status, data, latency_ms = core.browser_fetch_json(page, target)
        core.observe_resource(summary)
        if status == 429:
            raise StopProbe("http_429_provider_pressure")
    return status, data, latency_ms


def parameter_summary(parameter: Any) -> dict[str, Any] | None:
    if not isinstance(parameter, dict):
        return None
    name = parameter.get("name")
    location = parameter.get("in")
    if not isinstance(name, str) or not isinstance(location, str):
        return None
    schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
    out: dict[str, Any] = {
        "name": name,
        "in": location,
        "required": bool(parameter.get("required")),
    }
    for key in ("type", "format", "default", "minimum", "maximum"):
        value = schema.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value is not None:
                out[key] = value
    enum = schema.get("enum")
    if isinstance(enum, list) and len(enum) <= 20 and all(isinstance(v, (str, int, float, bool)) for v in enum):
        out["enum"] = enum
    return out


def extract_liquidation_contract(document: Any) -> dict[str, Any] | None:
    if not isinstance(document, dict):
        return None
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return None
    relevant: dict[str, Any] = {}
    for path, path_item in paths.items():
        if not isinstance(path, str) or "liquidat" not in path.lower() or not isinstance(path_item, dict):
            continue
        path_params = path_item.get("parameters") if isinstance(path_item.get("parameters"), list) else []
        methods: dict[str, Any] = {}
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            if not isinstance(operation, dict):
                continue
            params = list(path_params)
            if isinstance(operation.get("parameters"), list):
                params.extend(operation["parameters"])
            sanitized = [p for p in (parameter_summary(p) for p in params) if p is not None]
            methods[method.upper()] = {
                "operation_id": operation.get("operationId") if isinstance(operation.get("operationId"), str) else None,
                "parameters": sanitized,
            }
        relevant[path] = methods
    info = document.get("info") if isinstance(document.get("info"), dict) else {}
    return {
        "info_title": info.get("title") if isinstance(info.get("title"), str) else None,
        "info_version": info.get("version") if isinstance(info.get("version"), str) else None,
        "liquidation_paths": relevant,
    }


def main() -> None:
    summary: dict[str, Any] = {
        "contract": CONTRACT,
        "run_id": os.getenv("GITHUB_RUN_ID", ""),
        "source_only": True,
        "started_at": iso(datetime.now(timezone.utc)),
        "raw_events_persisted": False,
        "cookies_persisted": False,
        "tokens_persisted": False,
        "browser_profile_persisted": False,
        "candidate_count": len(OPENAPI_CANDIDATES),
        "results": [],
    }
    profile = Path(tempfile.mkdtemp(prefix="asxn-openapi-profile-"))
    os.chmod(profile, 0o700)
    exit_code = 0
    try:
        chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
        if not chrome:
            raise StopProbe("chrome_missing")
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=str(profile),
                executable_path=chrome,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            try:
                verified_at, verify_ms = core.verify_same_context(page, summary, reason="initial")
                summary["verified_at"] = iso(verified_at)
                summary["initial_verify_latency_ms"] = round(verify_ms, 3)
                found: list[dict[str, Any]] = []
                for target in OPENAPI_CANDIDATES:
                    status, data, latency_ms = effective_fetch(page, target, summary)
                    contract = extract_liquidation_contract(data) if status == 200 else None
                    row = {
                        "candidate_path": target.removeprefix(HOST),
                        "http_status": status,
                        "latency_ms": round(latency_ms, 3),
                        "json_object": isinstance(data, dict),
                        "liquidation_contract_found": bool(contract and contract.get("liquidation_paths")),
                    }
                    if contract and contract.get("liquidation_paths"):
                        row["contract"] = contract
                        found.append(row)
                    summary["results"].append(row)
                if found:
                    names = sorted({
                        p.get("name")
                        for row in found
                        for methods in row["contract"]["liquidation_paths"].values()
                        for operation in methods.values()
                        for p in operation.get("parameters", [])
                        if isinstance(p, dict) and isinstance(p.get("name"), str)
                    })
                    replayish = [name for name in names if any(token in name.lower() for token in (
                        "cursor", "offset", "page", "skip", "before", "after", "start", "end", "from", "to", "timestamp", "time"
                    ))]
                    summary["decision"] = {
                        "classification": "OPENAPI_LIQUIDATION_CONTRACT_FOUND",
                        "query_parameter_names": names,
                        "replay_or_range_like_parameter_names": replayish,
                        "truth_limit": "OpenAPI metadata is capability evidence only. Any candidate replay/range parameter still requires bounded behavioral proof before it can repair event continuity.",
                    }
                else:
                    summary["decision"] = {
                        "classification": "NO_PUBLIC_OPENAPI_LIQUIDATION_CONTRACT_FOUND",
                        "query_parameter_names": [],
                        "replay_or_range_like_parameter_names": [],
                        "truth_limit": "No public OpenAPI contract was discovered at the bounded candidate paths. Do not infer that undocumented parameters do not exist; stop blind parameter hunting unless new provider/source evidence appears.",
                    }
                summary["status"] = "CAPABILITY_PROBE_COMPLETE"
            finally:
                context.close()
    except StopProbe as exc:
        summary["status"] = "FAIL_CLOSED"
        summary["fail_reason"] = str(exc)
        exit_code = 1
    except Exception as exc:
        summary["status"] = "FAIL_CLOSED"
        summary["fail_reason"] = type(exc).__name__
        exit_code = 1
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        summary["ended_at"] = iso(datetime.now(timezone.utc))
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps(summary, indent=2, sort_keys=True))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
