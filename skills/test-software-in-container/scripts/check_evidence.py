#!/usr/bin/env python3
"""Check plan-bound observations and render scoped claims, not attest execution."""

import argparse
import json
import re
import sys

from tsc_support.contracts import (
    REPORT,
    RESULTS,
    STATUSES,
    aggregate,
    evidence_refs,
    finite_seconds,
    fingerprint,
    list_value,
    object_value,
    read_json,
    require,
    validate_plan,
    write_new,
)
from tsc_support.suite_contracts import assess_receipt


def check_observation(kind, check):
    status = check.get("status")
    require(status in STATUSES, "invalid check status")
    if status in {"PASS", "FAIL"}:
        evidence_refs(check.get("evidence"))
    if status != "PASS":
        return
    observation = object_value(check.get("observation"), "observation")
    if kind == "execution":
        require(
            observation.get("entrypoint_observed") is True
            and observation.get("intervened") is False,
            "execution not observed or intervened",
        )
    elif kind == "installed":
        require(
            observation.get("probe_origin") == "installed-artifact",
            "installed probe must inspect installed software",
        )
    elif kind == "source":
        require(
            observation.get("probe_origin") == "frozen-source"
            and observation.get("restricted_access_verified") is True,
            "source origin/access not verified",
        )
    elif kind == "smoke":
        require(
            observation.get("assertions_verified") is True,
            "suite assertions not verified",
        )
    elif kind == "patch":
        require(
            observation.get("delta_verified") is True
            and observation.get("invariants_preserved") is True,
            "patch delta/invariants not verified",
        )
    elif kind == "migration":
        require(
            observation.get("target_state_verified") is True
            and observation.get("invariants_preserved") is True,
            "migration state/invariants not verified",
        )
    elif kind == "no_op":
        require(
            type(observation.get("mutations")) is int and observation["mutations"] == 0,
            "no-op recorded mutations",
        )
    elif kind == "interruption":
        require(
            observation.get("boundary_observed") is True, "fault boundary not observed"
        )
    elif kind == "recovery":
        require(
            observation.get("product_owned") is True
            and observation.get("intervened") is False,
            "recovery was not product-owned or was intervened",
        )
    elif kind == "services":
        require(
            observation.get("real_manager") is True
            and observation.get("stable_window_observed") is True,
            "service startup evidence missing",
        )


def summarize(plan, results):
    validate_plan(plan)
    object_value(results, "results")
    require(results.get("schema") == RESULTS, "unsupported results schema")
    require(
        results.get("plan_sha256") == plan["sha256"], "results belong to another plan"
    )
    planned = {case["id"]: case for case in plan["cases"]}
    observations = {}
    for result in list_value(results.get("cases"), "result cases"):
        object_value(result, "case result")
        cid = result.get("id")
        require(
            cid in planned and cid not in observations, "unknown/duplicate case result"
        )
        observations[cid] = result
    rows = []
    for cid, case in planned.items():
        result = observations.get(cid)
        checks, timings = {}, {}
        mode, elapsed = None, None
        if result is not None:
            for field in ("baseline_key", "platform", "image_digest"):
                require(result.get(field) == case[field], "case identity mismatch")
            require(
                result.get("inputs_sha256") == fingerprint(case["inputs"]),
                "operation input identity mismatch",
            )
            mode = result.get("execution_mode")
            require(mode in {"native", "emulated", "not-run"}, "unknown execution mode")
            elapsed = result.get("elapsed_seconds")
            finite_seconds(elapsed)
            timings = object_value(result.get("phase_seconds", {}), "phase_seconds")
            require(
                set(timings)
                <= {
                    "preflight",
                    "preparation",
                    "operation",
                    "verification",
                    "recovery",
                    "cleanup",
                    "collection",
                    "suite",
                },
                "unknown timing phase",
            )
            for seconds in timings.values():
                finite_seconds(seconds)
            for check in list_value(result.get("checks"), "checks"):
                object_value(check, "check")
                name = check.get("id")
                require(
                    name in case["checks"] and name not in checks,
                    "unknown/duplicate check",
                )
                check_observation(case["checks"][name], check)
                require(
                    not (mode == "not-run" and check["status"] in {"PASS", "FAIL"}),
                    "assertion claimed without execution",
                )
                require(
                    not (case["blockers"] and check["status"] == "PASS"),
                    "blocked case contains passing claims",
                )
                if name == "source" and check["status"] == "PASS":
                    require(
                        check["observation"].get("source_sha256")
                        == case["inputs"]["source_sha256"],
                        "source identity mismatch",
                    )
                if name.startswith("suite.") and check["status"] in {
                    "PASS",
                    "FAIL",
                    "INCOMPLETE",
                }:
                    evidence_refs(check.get("evidence"))
                    observation = object_value(
                        check.get("observation"), "suite observation"
                    )
                    assessment = assess_receipt(
                        case["suites"][name[6:]],
                        observation.get("receipt"),
                        case["test_target"] or "installed",
                        plan["bundle_sha256"],
                        case_id=cid,
                        plan_sha256=plan["sha256"],
                    )
                    require(
                        check["status"] == assessment["status"],
                        "suite outcome contradicts receipt",
                    )
                    check = dict(check, assessment=assessment)
                checks[name] = check
        normalized = [
            {
                "id": name,
                "kind": kind,
                **{
                    k: v
                    for k, v in checks.get(name, {"status": "NOT_RUN"}).items()
                    if k in {"status", "evidence", "observation", "assessment"}
                },
            }
            for name, kind in case["checks"].items()
        ]
        status = aggregate([c["status"] for c in normalized])
        rows.append(
            {
                **case,
                "checks": normalized,
                "status": status,
                "execution_mode": mode,
                "elapsed_seconds": elapsed,
                "phase_seconds": timings,
            }
        )
    operations = {}
    for operation in plan["selected_operations"]:
        group = [c for c in rows if c["operation"] == operation]
        operations[operation] = {
            "status": aggregate([c["status"] for c in group])
            if group
            else "NOT_APPLICABLE",
            "planned_cases": len(group),
            "reported_cases": sum(c["id"] in observations for c in group),
        }
    return {
        "schema": REPORT,
        "plan_sha256": plan["sha256"],
        "validation": "STRUCTURE_VALID",
        "claim_status": aggregate([c["status"] for c in rows]),
        "proof_limit": "Supplied observations require independent review; this helper does not authenticate execution.",
        "scope": "Only the named checks and selected suites in the reported containers are covered.",
        "excludes": [
            "VM boot",
            "target kernel/hardware equivalence",
            "production connectivity",
            "release approval",
        ],
        "planned_cases": len(rows),
        "reported_cases": len(observations),
        "operations": operations,
        "selected_suites": plan["selected_suites"],
        "test_target": plan["test_target"],
        "not_applicable": plan["not_applicable"],
        "cases": rows,
    }


def cell(value):
    text = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\\x60*_{}\[\]()#+.!|\-])", r"\\\1", text)


def markdown(report):
    lines = [
        "# Container software test report",
        "",
        f"Reported outcome: **{report['claim_status']}**. Validation: STRUCTURE_VALID.",
        "",
        report["proof_limit"],
        "",
        report["scope"],
        "",
        f"Coverage: {report['reported_cases']}/{report['planned_cases']} cases reported.",
        "",
        "Plan SHA-256: " + report["plan_sha256"],
        "",
        "Excluded: " + ", ".join(report["excludes"]) + ".",
        "",
        "| Operation | Outcome | Reported / Planned |",
        "| --- | --- | --- |",
    ]
    for op, summary in report["operations"].items():
        lines.append(
            f"| {op} | {summary['status']} | {summary['reported_cases']} / {summary['planned_cases']} |"
        )
    lines.extend(
        [
            "",
            "| Case | Operation | Path | Environment | Trial | Outcome | Mode | Seconds |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for case in report["cases"]:
        values = [
            case["id"],
            case["operation"],
            " → ".join(case["path"]) or "—",
            case["environment"],
            case["fault_point"] or "normal",
            case["status"],
            case["execution_mode"] or "not-run",
            case["elapsed_seconds"],
        ]
        lines.append("| " + " | ".join(cell(v) for v in values) + " |")
    for case in report["cases"]:
        lines.extend(
            [
                "",
                "### Case " + case["id"],
                "",
                f"Image: {cell(case['image'])}; platform: {cell(case['platform'])}.",
                f"Test target: {cell(case['test_target'] or 'lifecycle')}.",
                "Image digest: " + case["image_digest"] + ".",
                "Baseline: " + case["baseline_key"] + ".",
                "",
            ]
        )
        lines.extend("- " + cell(c["id"]) + ": " + c["status"] for c in case["checks"])
        for check in case["checks"]:
            if "assessment" in check:
                summary = check["assessment"]
                lines.extend(
                    [
                        "",
                        cell(check["id"]) + " counts: " + cell(summary["counts"]),
                        "Coverage gaps: "
                        + cell(", ".join(summary["gaps"]) or "none")
                        + ".",
                    ]
                )
        if case["blockers"]:
            lines.extend(["", "Blockers: " + ", ".join(case["blockers"]) + "."])
        if case["phase_seconds"]:
            lines.extend(
                [
                    "",
                    "Phase seconds: "
                    + ", ".join(f"{k}={v}" for k, v in case["phase_seconds"].items())
                    + ".",
                ]
            )
        if case["exclusions"]:
            lines.extend(["", "Declared category exclusions:", ""])
            lines.extend(
                "- " + cell(k) + ": " + cell(v) for k, v in case["exclusions"].items()
            )
    if report["not_applicable"]:
        lines.extend(["", "## Non-applicable operations", ""])
        lines.extend(
            "- "
            + cell(r["environment"])
            + " / "
            + cell(r["operation"])
            + ": "
            + cell(r["reason"])
            for r in report["not_applicable"]
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument("results")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", help="New private report file; default stdout")
    args = parser.parse_args()
    try:
        result = summarize(read_json(args.plan), read_json(args.results))
        text = (
            markdown(result)
            if args.format == "markdown"
            else json.dumps(result, indent=2, allow_nan=False) + "\n"
        )
        write_new(args.output, text) if args.output else print(text, end="")
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as exc:
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "malformed input or unavailable output"
        )
        print(f"Invalid evidence: {message}", file=sys.stderr)
        return 2
    return 0 if result["claim_status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
