"""Bounded offline contracts; seals detect drift, not execution authenticity."""

import hashlib
import json
import math
import re
from pathlib import Path

PREFIX = "test-software-in-container"
PROFILE, PLAN, RESULTS, REPORT = (
    f"{PREFIX}.{s}.v2" for s in ("profile", "plan", "results", "report")
)
OPERATIONS = ("install", "test", "upgrade", "patch", "migration")
LIFECYCLE = ("install", "upgrade", "patch", "migration")
STATUSES = {"PASS", "FAIL", "INCOMPLETE", "BLOCKED", "NOT_RUN"}
MAX_JSON_BYTES = 4 * 1024 * 1024
OPTIONAL = {"packages", "services", "repeat", "no_op", "recovery"}
KINDS = OPTIONAL | {
    "execution",
    "installed",
    "smoke",
    "patch",
    "migration",
    "interruption",
    "suite",
    "source",
}
RESERVED = {
    "execution",
    "installed",
    "smoke",
    "patch",
    "migration",
    "interruption",
    "recovery",
    "source",
}
INPUTS = {
    "install": {"artifact_sha256"},
    "upgrade": {"artifact_sha256"},
    "test": {"software_sha256"},
    "patch": {
        "source_state_sha256",
        "payload_sha256",
        "workflow_sha256",
        "postconditions_sha256",
    },
    "migration": {
        "source_state_sha256",
        "tool_sha256",
        "workflow_sha256",
        "postconditions_sha256",
    },
}
ENV_FIELDS = (
    "image",
    "image_digest",
    "platform",
    "profile_sha256",
    "fixture_sha256",
    "setup_sha256",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def object_value(value, label):
    require(isinstance(value, dict), f"{label}: expected object")
    return value


def list_value(value, label):
    require(isinstance(value, list), f"{label}: expected array")
    return value


def string(value, label):
    require(
        isinstance(value, str)
        and 0 < len(value) <= 256
        and all(ord(c) >= 32 and ord(c) != 127 for c in value),
        f"{label}: expected bounded single-line string",
    )
    return value


def identifier(value, label):
    string(value, label)
    require(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,99}", value) is not None,
        f"{label}: invalid identifier",
    )
    return value


def digest(value, label):
    require(
        isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None,
        f"{label}: expected SHA-256",
    )
    return value


def image_digest(value, label):
    require(
        isinstance(value, str) and value.startswith("sha256:"),
        f"{label}: expected image digest",
    )
    digest(value[7:], label)
    return value


def platform_value(value):
    string(value, "platform")
    require(
        re.fullmatch(r"[a-z0-9]+/[a-z0-9_]+(?:/[a-z0-9_.-]+)?", value) is not None,
        "invalid platform",
    )
    return value


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def seal(value):
    return dict(value, sha256=fingerprint(value))


def verify_seal(value, schema):
    object_value(value, "document")
    require(
        value.get("schema") == schema,
        "unsupported schema; use the canonical renamed contract",
    )
    digest(value.get("sha256"), "document digest")
    require(
        value["sha256"]
        == fingerprint({k: v for k, v in value.items() if k != "sha256"}),
        "document digest mismatch",
    )


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON object key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("non-finite JSON number")

    with Path(path).open("rb") as stream:
        data = stream.read(MAX_JSON_BYTES + 1)
    require(len(data) <= MAX_JSON_BYTES, "input exceeds 4 MiB")
    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)


def write_new(path, text):
    import os

    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


def evidence_refs(value):
    list_value(value, "evidence")
    require(bool(value), "executed checks require evidence references")
    for ref in value:
        string(ref, "evidence reference")
        require(
            not ref.startswith(("/", "\\"))
            and ":" not in ref
            and "\\" not in ref
            and all(p not in {"", ".", ".."} for p in ref.split("/")),
            "evidence references must be relative contained paths",
        )


def finite_seconds(value):
    require(
        type(value) in (int, float) and 0 <= value <= 31536000 and math.isfinite(value),
        "invalid elapsed time",
    )


def input_fields(operation, test_target=None):
    if operation == "test" and test_target == "source":
        return {"source_sha256", "dependencies_sha256", "harness_sha256"}
    return INPUTS[operation]


def required_checks(operation, test_target=None):
    if operation == "test" and test_target == "source":
        return {"execution": "execution", "source": "source"}
    checks = {"execution": "execution", "smoke": "smoke"}
    if operation in {"install", "test", "upgrade"}:
        checks["installed"] = "installed"
    if operation in {"patch", "migration"}:
        checks[operation] = operation
    return checks


def environment_identity(env):
    return {key: env[key] for key in ENV_FIELDS}


def validate_environment(env):
    string(env.get("image"), "image")
    image_digest(env.get("image_digest"), "image_digest")
    platform_value(env.get("platform"))
    for key in ("profile_sha256", "fixture_sha256", "setup_sha256"):
        digest(env.get(key), key)


def aggregate(statuses):
    if "FAIL" in statuses:
        return "FAIL"
    return "PASS" if statuses and all(s == "PASS" for s in statuses) else "INCOMPLETE"


def validate_case(case):
    object_value(case, "case")
    cid = case.get("id")
    require(
        isinstance(cid, str)
        and cid == fingerprint({k: v for k, v in case.items() if k != "id"})[:24],
        "invalid case identity",
    )
    identifier(case.get("environment"), "environment")
    validate_environment(case)
    operation = case.get("operation")
    require(operation in OPERATIONS, "unknown operation")
    target = case.get("test_target")
    require(
        target in {"source", "installed"} if operation == "test" else target is None,
        "invalid test target",
    )
    blockers = list_value(case.get("blockers"), "blockers")
    require(len(blockers) == len(set(blockers)), "duplicate blocker")
    for blocker in blockers:
        identifier(blocker, "blocker")
    inputs = object_value(case.get("inputs"), "inputs")
    require(
        set(inputs) == input_fields(operation, target),
        "incorrect operation input fields",
    )
    for value in inputs.values():
        if value is None:
            require("MISSING_INPUT" in blockers, "missing input without blocker")
        else:
            digest(value, "input digest")
    digest(case.get("baseline_key"), "baseline_key")
    checks = object_value(case.get("checks"), "checks")
    for name, kind in checks.items():
        identifier(name, "check")
        require(kind in KINDS, "unknown check kind")
    for name, kind in required_checks(operation, target).items():
        require(checks.get(name) == kind, "missing operation-required check")
    fault = case.get("fault_point")
    if fault is not None:
        identifier(fault, "fault point")
        require(
            checks.get("interruption") == "interruption"
            and checks.get("recovery") == "recovery",
            "missing fault assertions",
        )
    else:
        require(
            "interruption" not in checks.values() and "recovery" not in checks.values(),
            "fault assertions in normal trial",
        )
    exclusions = object_value(case.get("exclusions"), "exclusions")
    require(set(exclusions) <= OPTIONAL, "unknown exclusion category")
    for reason in exclusions.values():
        string(reason, "exclusion reason")
    faults = list_value(case.get("fault_points"), "fault points")
    require(len(faults) == len(set(faults)), "duplicate fault point")
    for point in faults:
        identifier(point, "fault point")
    require(fault is None or fault in faults, "fault without recovery contract")
    if not blockers:
        for kind in () if target == "source" else OPTIONAL:
            present = bool(faults) if kind == "recovery" else kind in checks.values()
            require(
                present != (kind in exclusions), "category requires checks or exclusion"
            )
    suites = object_value(case.get("suites"), "suites")
    if target == "source":
        require(
            bool(suites) or "UNAVAILABLE_SUITE" in blockers,
            "source requires a selected suite",
        )
    for name, suite in suites.items():
        identifier(name, "suite")
        object_value(suite, "suite")
        from tsc_support.suite_contracts import validate_suite

        validate_suite(suite, allow_missing_inventory="MISSING_INVENTORY" in blockers)
        require(checks.get("suite." + name) == "suite", "missing selected suite check")
    require(
        {k for k, v in checks.items() if v == "suite"}
        == {"suite." + n for n in suites},
        "suite check without frozen definition",
    )
    path = list_value(case.get("path"), "path")
    artifacts = object_value(case.get("artifacts"), "artifacts")
    if operation == "upgrade" and not path:
        require(
            "UNRESOLVED_UPGRADE_PATH" in blockers
            and artifacts == {}
            and case.get("path_kind") is None,
            "unresolved upgrade path without blocker",
        )
    elif operation == "upgrade":
        require(
            case.get("path_kind") in {"direct", "sequential"},
            "invalid upgrade path kind",
        )
        require(len(path) >= 2 and len(set(path)) == len(path), "invalid upgrade path")
        if case["path_kind"] == "direct":
            require(len(path) == 2, "direct path has intermediate releases")
        for version in path:
            string(version, "version")
        require(set(artifacts) == set(path), "incomplete path artifacts")
        for sha in artifacts.values():
            if sha is None:
                require(
                    "MISSING_INPUT" in blockers, "missing path artifact without blocker"
                )
            else:
                digest(sha, "artifact digest")
        require(
            artifacts[path[-1]] == inputs["artifact_sha256"], "target artifact mismatch"
        )
    else:
        require(
            path == [] and artifacts == {} and case.get("path_kind") is None,
            "upgrade fields on another operation",
        )


def validate_plan(plan):
    verify_seal(plan, PLAN)
    for name in ("profile_sha256", "bundle_sha256"):
        digest(plan.get(name), name)
    selected = list_value(plan.get("selected_operations"), "selected_operations")
    require(
        bool(selected)
        and len(set(selected)) == len(selected)
        and all(s in OPERATIONS for s in selected),
        "invalid selected operations",
    )
    target = plan.get("test_target")
    require(
        target in {"source", "installed"} if "test" in selected else target is None,
        "invalid plan test target",
    )
    require(
        target != "source" or selected == ["test"], "source campaign must be separate"
    )
    requested_suites = list_value(plan.get("selected_suites"), "selected_suites")
    require(
        len(set(requested_suites)) == len(requested_suites), "duplicate selected suite"
    )
    for name in requested_suites:
        identifier(name, "selected suite")
    environments = list_value(plan.get("environments"), "environments")
    require(
        bool(environments) and len(set(environments)) == len(environments),
        "invalid environments",
    )
    for eid in environments:
        identifier(eid, "environment")
    order = list_value(plan.get("release_order"), "release_order")
    require(len(set(order)) == len(order), "duplicate release order")
    for version in order:
        string(version, "release version")
    indexes = {version: i for i, version in enumerate(order)}
    releases = list_value(plan.get("selected_releases"), "selected releases")
    require(
        len(set(releases)) == len(releases) and all(v in indexes for v in releases),
        "invalid selected releases",
    )
    declared_paths = list_value(plan.get("upgrade_paths"), "upgrade paths")
    path_keys = set()
    for entry in declared_paths:
        object_value(entry, "upgrade path")
        path = list_value(entry.get("path"), "upgrade path")
        kind = entry.get("kind")
        key = (kind, tuple(path))
        require(key not in path_keys, "duplicate upgrade path")
        path_keys.add(key)
        if path:
            require(
                kind in {"direct", "sequential"}
                and len(path) >= 2
                and (kind != "direct" or len(path) == 2)
                and path[0] in releases
                and path[-1] == plan.get("target_version")
                and all(v in indexes for v in path)
                and all(indexes[a] < indexes[b] for a, b in zip(path, path[1:])),
                "invalid declared upgrade path",
            )
        else:
            require(
                kind is None and (not releases or plan.get("target_version") is None),
                "unjustified unresolved upgrade path",
            )
    if "upgrade" in selected:
        require(bool(path_keys), "missing declared upgrade paths")
        if releases and plan.get("target_version") is not None:
            require(
                {p[0] for _, p in path_keys} == set(releases),
                "missing predecessor coverage",
            )
    else:
        require(
            not path_keys and not releases and not order, "irrelevant upgrade metadata"
        )
    cases = list_value(plan.get("cases"), "cases")
    require(len(cases) <= 10000, "too many cases")
    ids, covered, trials, path_coverage = set(), set(), {}, {}
    for case in cases:
        validate_case(case)
        require(
            case["test_target"] == (target if case["operation"] == "test" else None),
            "case test target mismatch",
        )
        for suite in case["suites"].values():
            if suite.get("inventory"):
                require(
                    suite["inventory"]["input_sha256"] == plan["bundle_sha256"],
                    "inventory input mismatch",
                )
        require(case["id"] not in ids, "duplicate case")
        ids.add(case["id"])
        require(
            case["operation"] in selected and case["environment"] in environments,
            "unselected case",
        )
        covered.add((case["environment"], case["operation"]))
        group = (
            case["environment"],
            case["operation"],
            case["path_kind"],
            tuple(case["path"]),
        )
        normalized = {
            k: v for k, v in case.items() if k not in {"id", "fault_point", "checks"}
        }
        normalized["checks"] = {
            k: v
            for k, v in case["checks"].items()
            if k not in {"interruption", "recovery"}
        }
        shape, seen = trials.setdefault(group, (fingerprint(normalized), set()))
        require(shape == fingerprint(normalized), "inconsistent trial declaration")
        require(case["fault_point"] not in seen, "duplicate trial")
        seen.add(case["fault_point"])
        if case["operation"] == "upgrade":
            key = (case["path_kind"], tuple(case["path"]))
            require(key in path_keys, "undeclared upgrade path")
            path_coverage.setdefault(case["environment"], set()).add(key)
        if case["operation"] == "upgrade" and case["path"]:
            path = case["path"]
            require(
                path[0] in releases
                and path[-1] == plan.get("target_version")
                and all(v in indexes for v in path)
                and all(indexes[a] < indexes[b] for a, b in zip(path, path[1:])),
                "upgrade path conflicts with selected release order",
            )
        for suite in requested_suites:
            require(
                suite in case["suites"] or "UNAVAILABLE_SUITE" in case["blockers"],
                "missing requested suite",
            )
    for case in cases:
        group = (
            case["environment"],
            case["operation"],
            case["path_kind"],
            tuple(case["path"]),
        )
        require(
            trials[group][1] == {None, *case["fault_points"]},
            "missing normal or fault trial",
        )
    for actual in path_coverage.values():
        require(actual == path_keys, "missing upgrade path coverage")
    na = list_value(plan.get("not_applicable"), "not_applicable")
    for item in na:
        object_value(item, "not applicable")
        key = (item.get("environment"), item.get("operation"))
        require(
            key[0] in environments and key[1] in selected and key not in covered,
            "invalid/duplicate applicability record",
        )
        string(item.get("reason"), "non-applicability reason")
        covered.add(key)
    require(
        covered == {(eid, op) for eid in environments for op in selected},
        "missing operation coverage",
    )
