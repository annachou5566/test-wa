from __future__ import annotations

import json
import os
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

BASE = "https://wave-alpha.pages.dev"
CURRENT = "/api/etf-flows"
HISTORY = "/api/etf-flows/history"
SESSION = "/api/security/session"
UA = "wave-alpha-security-hostile-qa/2026-08-23"
TIMEOUT = 20


def hdr_same_origin(*, origin: bool = False, ua: str = UA) -> dict[str, str]:
    h = {
        "User-Agent": ua,
        "Accept": "application/json",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }
    if origin:
        h["Origin"] = BASE
    return h


def safe_error(response: requests.Response) -> str | None:
    try:
        body = response.json()
        value = body.get("error") if isinstance(body, dict) else None
        return str(value)[:64] if value else None
    except Exception:
        return None


def call(method: str, path: str, *, headers: dict[str, str] | None = None,
         cookie: str | None = None, json_body=None, data=None) -> tuple[dict, requests.Response]:
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = cookie
    r = requests.request(method, BASE + path, headers=h, json=json_body, data=data,
                         timeout=TIMEOUT, allow_redirects=False)
    out = {
        "status": r.status_code,
        "content_type": r.headers.get("content-type", ""),
        "error": safe_error(r),
    }
    return out, r


def expect(summary: dict, name: str, actual: dict, *, status: int,
           error: str | None = None, allow: str | None = None) -> None:
    ok = actual.get("status") == status
    if error is not None:
        ok = ok and actual.get("error") == error
    if allow is not None:
        ok = ok and actual.get("allow") == allow
    summary["cases"][name] = {**actual, "pass": bool(ok)}
    if not ok:
        summary["hard_failures"].append(name)


def direct_cases(summary: dict) -> None:
    a, r = call("GET", CURRENT, headers={"User-Agent": UA, "Accept": "application/json"})
    expect(summary, "DIRECT_ANONYMOUS_CURRENT", a, status=403, error="ACCESS_DENIED")

    a, _ = call("GET", HISTORY, headers={"User-Agent": UA, "Accept": "application/json"})
    expect(summary, "DIRECT_ANONYMOUS_HISTORY", a, status=403, error="ACCESS_DENIED")

    a, _ = call("GET", CURRENT, headers=hdr_same_origin())
    expect(summary, "MISSING_COOKIE", a, status=401, error="SESSION_REQUIRED")

    a, _ = call("GET", CURRENT, headers=hdr_same_origin(),
                cookie="__Host-wa_session=malformed.invalid")
    expect(summary, "MALFORMED_COOKIE", a, status=401, error="SESSION_REQUIRED")

    cross = hdr_same_origin(origin=True)
    cross["Sec-Fetch-Site"] = "cross-site"
    cross["Origin"] = "https://example.invalid"
    a, _ = call("GET", CURRENT, headers=cross,
                cookie="__Host-wa_session=malformed.invalid")
    expect(summary, "CROSS_ORIGIN", a, status=403, error="ACCESS_DENIED")

    a, r_method = call("POST", CURRENT, headers=hdr_same_origin(origin=True), json_body={})
    a["allow"] = r_method.headers.get("Allow")
    expect(summary, "WRONG_METHOD_CURRENT", a, status=405, error="METHOD_NOT_ALLOWED", allow="GET")

    a, r_cfg = call("GET", SESSION, headers=hdr_same_origin())
    cfg_keys: list[str] = []
    cfg_action = None
    try:
        cfg = r_cfg.json()
        if isinstance(cfg, dict):
            cfg_keys = sorted(str(k) for k in cfg.keys())
            cfg_action = cfg.get("action")
    except Exception:
        pass
    a["keys"] = cfg_keys
    a["action"] = cfg_action
    a["minimal_disclosure"] = cfg_keys == ["action", "sitekey"] and cfg_action == "wave-session"
    summary["cases"]["CONFIG_MIN_DISCLOSURE_CURRENT_PROD"] = {
        **a, "pass": a["status"] == 200 and a["minimal_disclosure"]
    }
    if not summary["cases"]["CONFIG_MIN_DISCLOSURE_CURRENT_PROD"]["pass"]:
        summary["hard_failures"].append("CONFIG_MIN_DISCLOSURE_CURRENT_PROD")

    a, _ = call("POST", SESSION, headers={**hdr_same_origin(origin=True), "Content-Type": "application/json"},
                json_body={"token": "invalid-wave-alpha-qa-token"})
    expect(summary, "INVALID_TURNSTILE", a, status=403, error="ACCESS_DENIED")

    oversized = json.dumps({"token": "x" * 5000})
    a, _ = call("POST", SESSION,
                headers={**hdr_same_origin(origin=True), "Content-Type": "application/json"},
                data=oversized)
    expect(summary, "OVERSIZED_SESSION_BODY", a, status=400, error="BAD_REQUEST")

    a, r = call("PUT", SESSION, headers=hdr_same_origin(origin=True), json_body={})
    a["allow"] = r.headers.get("Allow")
    expect(summary, "SESSION_WRONG_METHOD", a, status=405, error="METHOD_NOT_ALLOWED", allow="GET, POST")

    # Security headers from a denied proprietary response; never persist proprietary JSON.
    headers_ok = {
        "cache_private_no_store": "private" in r_cfg.headers.get("Cache-Control", "") and "no-store" in r_cfg.headers.get("Cache-Control", ""),
    }
    denied = requests.get(BASE + CURRENT, headers={"User-Agent": UA, "Accept": "application/json"}, timeout=TIMEOUT)
    headers_ok.update({
        "corp_same_origin": denied.headers.get("Cross-Origin-Resource-Policy") == "same-origin",
        "nosniff": denied.headers.get("X-Content-Type-Options") == "nosniff",
        "frame_deny": denied.headers.get("X-Frame-Options") == "DENY",
        "referrer_no_referrer": denied.headers.get("Referrer-Policy") == "no-referrer",
        "robots_noindex": "noindex" in denied.headers.get("X-Robots-Tag", ""),
        "cors_not_wildcard": denied.headers.get("Access-Control-Allow-Origin") != "*",
    })
    summary["cases"]["PROTECTED_HEADERS"] = {**headers_ok, "pass": all(headers_ok.values())}
    if not all(headers_ok.values()):
        summary["hard_failures"].append("PROTECTED_HEADERS")

    # Bounded current Production session-mint limiter exercise. Invalid token means no session is minted.
    statuses: list[int] = []
    first_429 = None
    mint_headers = {**hdr_same_origin(origin=True), "Content-Type": "application/json"}
    for attempt in range(1, 71):
        try:
            rr = requests.post(BASE + SESSION, headers=mint_headers,
                               json={"token": "invalid-wave-alpha-qa-token"}, timeout=TIMEOUT)
            statuses.append(rr.status_code)
            if rr.status_code == 429:
                first_429 = attempt
                break
        except requests.RequestException:
            statuses.append(-1)
        time.sleep(0.05)
    counts = Counter(statuses)
    summary["cases"]["SESSION_MINT_RATE_LIMIT"] = {
        "attempts": len(statuses),
        "first_429": first_429,
        "status_counts": {str(k): v for k, v in sorted(counts.items())},
        "pass": first_429 is not None,
    }
    if first_429 is None:
        summary["hard_failures"].append("SESSION_MINT_RATE_LIMIT")

    # Proxy/text-reader is supporting evidence only. Unavailable proxy is not converted to PASS/FAIL.
    proxy_url = "https://r.jina.ai/https://wave-alpha.pages.dev/api/etf-flows"
    try:
        p = requests.get(proxy_url, headers={"User-Agent": UA}, timeout=20)
        text = p.text[:8192]
        markers = any(token in text for token in ('"etfs"', '"totals"', '"history"'))
        summary["cases"]["PROXY_TEXT_READER"] = {
            "status": p.status_code,
            "proprietary_markers_seen": markers,
            "denial_marker_seen": ("ACCESS_DENIED" in text or "SESSION_REQUIRED" in text),
            "classification": "PASS" if not markers else "FAIL",
        }
        if markers:
            summary["hard_failures"].append("PROXY_TEXT_READER")
    except requests.RequestException as exc:
        summary["cases"]["PROXY_TEXT_READER"] = {
            "classification": "UNAVAILABLE",
            "error_type": type(exc).__name__,
        }


def browser_status(page, path: str, method: str = "GET", body=None) -> int:
    return int(page.evaluate(
        """async ({path,method,body}) => {
          const init = {method, credentials:'same-origin', headers:{Accept:'application/json'}};
          if (body !== null) { init.headers['Content-Type']='application/json'; init.body=JSON.stringify(body); }
          const r = await fetch(path, init); return r.status;
        }""",
        {"path": path, "method": method, "body": body},
    ))


def replay_status(browser, cookie: dict, ua: str) -> int:
    ctx = browser.new_context(user_agent=ua)
    ctx.add_cookies([cookie])
    page = ctx.new_page()
    page.route("**/*.js", lambda route: route.abort())
    page.route("**/*.{png,jpg,jpeg,gif,webp,svg,css,woff,woff2}", lambda route: route.abort())
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
        return browser_status(page, CURRENT)
    finally:
        ctx.close()


def browser_cases(summary: dict) -> None:
    out = summary["browser"] = {
        "attempted": True,
        "valid_session": "NOT_PROVEN",
        "credentials_logged": False,
        "token_logged": False,
    }
    chrome = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    out["chrome_available"] = bool(chrome)
    if not chrome:
        out["reason"] = "CHROME_UNAVAILABLE"
        return

    captured_token: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chrome, headless=True,
                                     args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context()
        page = context.new_page()

        def observe_request(req) -> None:
            if req.method == "POST" and req.url.split("?", 1)[0] == BASE + SESSION:
                try:
                    payload = req.post_data_json
                    token = payload.get("token") if isinstance(payload, dict) else None
                    if isinstance(token, str) and token:
                        captured_token[:] = [token]
                except Exception:
                    pass

        page.on("request", observe_request)
        try:
            page.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_function("window.WaveSecurity && window.WaveSecurity.installed === true", timeout=30000)
            try:
                page.evaluate("""async () => { await window.WaveSecurity.ensureSession(); return true; }""")
            except Exception as exc:
                out["valid_session"] = "NOT_PROVEN"
                out["reason"] = f"SESSION_ESTABLISH_{type(exc).__name__}"
                return

            cookies = context.cookies(BASE)
            sess = next((c for c in cookies if c.get("name") == "__Host-wa_session"), None)
            out["session_cookie_present"] = bool(sess)
            if not sess:
                out["reason"] = "SESSION_COOKIE_MISSING"
                return

            out["valid_session"] = "PASS"
            original_ua = page.evaluate("navigator.userAgent")
            out["VALID_PROTECTED_READ"] = browser_status(page, CURRENT)
            out["VALID_HISTORY_READ_1D"] = browser_status(page, HISTORY + "?days=1")
            out["INVALID_QUERY_AFTER_AUTH"] = browser_status(page, HISTORY + "?days=abc")

            # Cookie value is copied only in runner memory and is never printed/artifacted.
            out["SESSION_REPLAY_SAME_UA"] = replay_status(browser, sess, original_ua)
            out["SESSION_REPLAY_CHANGED_UA"] = replay_status(browser, sess, original_ua + " changed")

            if captured_token:
                out["VALID_TURNSTILE_REPLAY"] = browser_status(
                    page, SESSION, "POST", {"token": captured_token[0]}
                )
                captured_token.clear()
            else:
                out["VALID_TURNSTILE_REPLAY"] = "NOT_PROVEN_TOKEN_NOT_OBSERVED"

            heavy_first_429 = None
            for i in range(1, 46):
                status = browser_status(page, HISTORY + "?days=1")
                if status == 429:
                    heavy_first_429 = i
                    break
            out["HEAVY_READ_FIRST_429"] = heavy_first_429

            standard_first_429 = None
            for i in range(1, 141):
                status = browser_status(page, CURRENT)
                if status == 429:
                    standard_first_429 = i
                    break
            out["STANDARD_READ_FIRST_429"] = standard_first_429

            checks = {
                "VALID_PROTECTED_READ": out["VALID_PROTECTED_READ"] == 200,
                "VALID_HISTORY_READ_1D": out["VALID_HISTORY_READ_1D"] == 200,
                "INVALID_QUERY_AFTER_AUTH": out["INVALID_QUERY_AFTER_AUTH"] == 400,
                "SESSION_REPLAY_SAME_UA": out["SESSION_REPLAY_SAME_UA"] == 200,
                "SESSION_REPLAY_CHANGED_UA": out["SESSION_REPLAY_CHANGED_UA"] == 401,
                "HEAVY_READ_RATE_LIMIT": heavy_first_429 is not None,
                "STANDARD_READ_RATE_LIMIT": standard_first_429 is not None,
            }
            if isinstance(out["VALID_TURNSTILE_REPLAY"], int):
                checks["VALID_TURNSTILE_REPLAY"] = out["VALID_TURNSTILE_REPLAY"] == 403
            out["checks"] = checks
            out["pass_current_browser_subset"] = all(checks.values())
        finally:
            captured_token.clear()
            context.close()
            browser.close()


def main() -> int:
    summary = {
        "probe": "wave-security-production-hostile-nondestructive-v1",
        "target": "wave-alpha.pages.dev",
        "source_only": False,
        "production_config_mutation": False,
        "proprietary_payload_persisted": False,
        "secret_material_persisted": False,
        "cases": {},
        "browser": {},
        "hard_failures": [],
    }
    direct_cases(summary)
    browser_cases(summary)

    out = Path(os.environ.get("OUTPUT", "artifacts/security-prod-hostile/summary.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary["hard_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
