from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import asxn_stage3a_canary as core

MAX_DURATION_SECONDS = 14_400
# Keep one full hour of shutdown/evidence margin before the 2026-08-27 23:00 ICT Day-7 gate.
HARD_STOP_UTC = datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc)
CONTRACT = "HYPERLIQUID_ASXN_LONGER_BOUNDED_CANARY_CONTRACT_2026-08-26"


def long_classification(summary: dict, full_duration_completed: bool) -> str:
    metrics = summary.get("event_metrics", {})
    clean_zero = int(metrics.get("clean_200_to_200_zero_overlap", 0))
    total_zero = int(metrics.get("zero_overlap_count_this_run", 0))
    boundary_zero = int(metrics.get("verification_boundary_zero_overlap", 0))
    if clean_zero > 0:
        return "LONG_CANARY_INTRINSIC_TURNOVER"
    if total_zero > 0 and boundary_zero == total_zero:
        return "LONG_CANARY_REVERIFY_BOUNDARY"
    if total_zero > 0:
        return "LONG_CANARY_INCONCLUSIVE_OR_FAILED"
    if full_duration_completed:
        return "LONG_CANARY_PROMISING"
    return "LONG_CANARY_INCONCLUSIVE_OR_FAILED"


def patch_core() -> None:
    core.MAX_DURATION_SECONDS = MAX_DURATION_SECONDS
    core.HARD_STOP_UTC = HARD_STOP_UTC
    core.classification = long_classification


def self_test() -> None:
    # First prove the exact restored Stage3A core still satisfies its own contract.
    core.self_test()
    patch_core()
    assert long_classification(
        {"event_metrics": {"clean_200_to_200_zero_overlap": 1, "zero_overlap_count_this_run": 1, "verification_boundary_zero_overlap": 0}},
        False,
    ) == "LONG_CANARY_INTRINSIC_TURNOVER"
    assert long_classification(
        {"event_metrics": {"clean_200_to_200_zero_overlap": 0, "zero_overlap_count_this_run": 1, "verification_boundary_zero_overlap": 1}},
        False,
    ) == "LONG_CANARY_REVERIFY_BOUNDARY"
    assert long_classification(
        {"event_metrics": {"clean_200_to_200_zero_overlap": 0, "zero_overlap_count_this_run": 0, "verification_boundary_zero_overlap": 0}},
        True,
    ) == "LONG_CANARY_PROMISING"
    assert long_classification(
        {"event_metrics": {"clean_200_to_200_zero_overlap": 0, "zero_overlap_count_this_run": 0, "verification_boundary_zero_overlap": 0}},
        False,
    ) == "LONG_CANARY_INCONCLUSIVE_OR_FAILED"
    print("LONG_CANARY_WRAPPER_SELF_TEST=PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=int)
    parser.add_argument("--output", default="artifacts/asxn-long-canary/summary.json")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        raise SystemExit(0)
    if args.duration_seconds is None:
        raise SystemExit("--duration-seconds is required")
    if args.duration_seconds <= 0 or args.duration_seconds > MAX_DURATION_SECONDS:
        raise SystemExit("duration out of bounds")

    patch_core()
    core_args = argparse.Namespace(duration_seconds=args.duration_seconds, output=args.output)
    summary, code = core.run(core_args)

    summary["probe"] = "asxn-longer-bounded-persistent-browser-canary-v1"
    summary["contract"] = CONTRACT
    summary["stage3a_core_reused"] = True
    summary["hard_stop_utc"] = core.iso(HARD_STOP_UTC)
    if summary.get("status") == "STOPPED_FOR_DAY3_AUDIT_BOUNDARY":
        summary["status"] = "STOPPED_FOR_DAY7_SAFETY_BOUNDARY"
    if summary.get("status") != "FAIL_CLOSED":
        summary["classification"] = long_classification(
            summary, bool(summary.get("full_requested_duration_completed"))
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"LONG_CANARY_FINAL_CLASSIFICATION={summary.get('classification')}")
    print(f"LONG_CANARY_FULL_DURATION={summary.get('full_requested_duration_completed')}")
    raise SystemExit(code)


if __name__ == "__main__":
    main()
