#!/usr/bin/env python3
"""Expand a frozen profile offline; never discover, build, or execute software."""

import argparse
import json
import sys

from tsc_support.contracts import (
    ENV_FIELDS,
    KINDS,
    LIFECYCLE,
    MAX_JSON_BYTES,
    OPERATIONS,
    OPTIONAL,
    PLAN,
    PROFILE,
    RESERVED,
    digest,
    environment_identity,
    fingerprint,
    identifier,
    image_digest,
    input_fields,
    list_value,
    object_value,
    read_json,
    require,
    required_checks,
    seal,
    string,
    validate_environment,
    validate_plan,
    write_new,
)
from project_context import select_suites, validate_catalog
from tsc_support.suite_contracts import validate_suite


def artifact_hash(item, platform):
    if item is None:
        return None
    object_value(item, "artifact")
    artifacts = object_value(item.get("artifacts", {}), "platform artifacts")
    entry = artifacts.get(platform)
    if entry is None:
        return None
    return digest(
        object_value(entry, "platform artifact").get("sha256"), "artifact digest"
    )


def select_operations(operations, all_tests, envs):
    require(type(all_tests) is bool, "invalid all-tests selection")
    require(not (all_tests and operations), "all-tests conflicts with individual modes")
    if operations is not None:
        list_value(operations, "operations")
        require(
            bool(operations) and all(op in OPERATIONS for op in operations),
            "invalid operations",
        )
    chosen = set(operations or ["test"])
    if all_tests:
        chosen = set(LIFECYCLE)
        if any(
            object_value(e.get("operations", {}), "operations")
            .get("test", {})
            .get("target")
            == "installed"
            for e in envs
        ):
            chosen.add("test")
    return [op for op in OPERATIONS if op in chosen]


def release_paths(profile, target, past_releases, from_releases, upgrade_path):
    catalog = {}
    for item in list_value(profile.get("releases", []), "releases"):
        object_value(item, "release")
        version = string(item.get("version"), "release version")
        require(
            version not in catalog and version != target, "duplicate/target predecessor"
        )
        require(type(item.get("stable")) is bool, "release stability must be boolean")
        catalog[version] = item
    if from_releases is not None:
        list_value(from_releases, "from releases")
        require(
            bool(from_releases) and len(set(from_releases)) == len(from_releases),
            "invalid exact release selection",
        )
        require(all(v in catalog for v in from_releases), "unknown predecessor")
        selected = [v for v in catalog if v in from_releases]
    else:
        selected = [v for v, item in catalog.items() if item["stable"]][-past_releases:]
    order = list(catalog) + ([target] if target else [])
    indexes = {version: index for index, version in enumerate(order)}
    supported = list_value(profile.get("supported_paths", []), "supported paths")
    for path in supported:
        list_value(path, "supported path")
        require(len(path) >= 2, "invalid supported path")
        for version in path:
            string(version, "supported path version")
        require(len(set(path)) == len(path), "repeated supported path version")
        if target is None:
            # Preserve declarations until the missing target can be resolved.
            continue
        require(
            all(v in indexes for v in path),
            "invalid supported path",
        )
        require(
            path[-1] == target
            and all(indexes[a] < indexes[b] for a, b in zip(path, path[1:])),
            "non-forward supported path",
        )
    require(
        len(set(tuple(p) for p in supported)) == len(supported),
        "duplicate supported path",
    )
    wanted = []
    if not selected or target is None:
        wanted.append((None, [], ["UNRESOLVED_UPGRADE_PATH"]))
    else:
        for version in selected:
            direct = [version, target]
            if upgrade_path in {"direct", "both"}:
                wanted.append(
                    (
                        "direct",
                        direct,
                        [] if direct in supported else ["UNSUPPORTED_DIRECT_PATH"],
                    )
                )
            if upgrade_path in {"sequential", "both"}:
                sequences = [p for p in supported if p[0] == version and len(p) > 2]
                if not sequences and direct in supported:
                    sequences = [] if upgrade_path == "both" else [direct]
                elif not sequences:
                    wanted.append(
                        ("sequential", direct, ["NO_DECLARED_SEQUENTIAL_PATH"])
                    )
                wanted.extend(("sequential", p, []) for p in sequences)
    return catalog, selected, order, wanted


def prepare_checks(operation, config, selected_suites, catalog, blocked, target):
    checks = required_checks(operation, target)
    exclusions = {}
    faults = []
    if config is not None and "not_applicable" not in config:
        extra = object_value(config.get("checks", {}), "checks")
        for name, kind in extra.items():
            identifier(name, "check id")
            require(
                name not in RESERVED and not name.startswith("suite."),
                "reserved check id",
            )
            require(
                kind
                in KINDS
                - {
                    "execution",
                    "interruption",
                    "recovery",
                    "suite",
                    "source",
                },
                "invalid additional check kind",
            )
            checks[name] = kind
        exclusions = object_value(config.get("exclusions", {}), "exclusions")
        require(set(exclusions) <= OPTIONAL, "unknown exclusion category")
        for reason in exclusions.values():
            string(reason, "exclusion reason")
        faults = list_value(config.get("fault_points", []), "fault points")
        require(len(faults) == len(set(faults)), "duplicate fault point")
        for fault in faults:
            identifier(fault, "fault point")
        for kind in () if target == "source" else OPTIONAL:
            present = bool(faults) if kind == "recovery" else kind in checks.values()
            require(
                present != (kind in exclusions),
                "each category needs checks or an exclusion",
            )
    suites = {}
    for name in selected_suites:
        suite = catalog.get(name)
        if suite is None or operation not in suite["operations"]:
            blocked.append("UNAVAILABLE_SUITE")
        else:
            validate_suite(suite, allow_missing_inventory=True)
            suites[name] = {
                key: suite[key]
                for key in ("kind", "sha256", "inventory", "exemptions")
                if key in suite
            }
            if suite.get("inventory") is None:
                blocked.append("MISSING_INVENTORY")
            checks["suite." + name] = "suite"
    return checks, exclusions, faults, suites


def validate_baseline(baseline, env, expected, state, blocked):
    if baseline is None:
        blocked.append("MISSING_VERIFIED_BASELINE")
        return
    object_value(baseline, "baseline")
    image_digest(baseline.get("image_digest"), "baseline image")
    digest(baseline.get("environment_sha256"), "baseline environment")
    if baseline["environment_sha256"] != fingerprint(environment_identity(env)):
        blocked.append("BASELINE_ENVIRONMENT_MISMATCH")
    if baseline.get("verified") is not True:
        blocked.append("UNVERIFIED_BASELINE")
    field = "state_sha256" if state else "installed_artifact_sha256"
    digest(baseline.get(field), field)
    if baseline[field] != expected:
        blocked.append(
            "BASELINE_STATE_MISMATCH" if state else "BASELINE_ARTIFACT_MISMATCH"
        )
    if not state:
        digest(baseline.get("dependencies_sha256"), "baseline dependencies")
        if baseline.get("target_present") is not False:
            blocked.append("TARGET_CONTAMINATION")


def expand(
    profile,
    *,
    operations=None,
    all_tests=False,
    suites=None,
    past_releases=None,
    from_releases=None,
    upgrade_path=None,
    image=None,
    platform=None,
    test_target=None,
):
    object_value(profile, "profile")
    require(
        profile.get("schema") == PROFILE,
        "unsupported profile schema; old skill contracts are not accepted",
    )
    digest(profile.get("bundle_sha256"), "bundle_sha256")
    envs = list_value(profile.get("environments"), "environments")
    require(bool(envs), "at least one environment is required")
    for env in envs:
        object_value(env, "environment")
        for config in object_value(env.get("operations", {}), "operations").values():
            object_value(config, "operation")
    chosen = select_operations(operations, all_tests, envs)
    require(
        test_target is None or test_target in {"source", "installed"},
        "invalid test target",
    )
    require(test_target is None or "test" in chosen, "test-target requires test mode")
    target_kind = (
        (test_target or ("installed" if all_tests else "source"))
        if "test" in chosen
        else None
    )
    require(
        not all_tests or target_kind != "source", "all-tests excludes source suites"
    )
    require(
        target_kind != "source" or chosen == ["test"],
        "source campaign must be separate; combined lifecycle tests require explicit installed target",
    )
    require(
        not (past_releases is not None and from_releases is not None),
        "conflicting release selectors",
    )
    require(
        "upgrade" in chosen
        or all(v is None for v in (past_releases, from_releases, upgrade_path)),
        "upgrade selectors require upgrade mode",
    )
    if past_releases is not None:
        require(
            type(past_releases) is int and past_releases > 0,
            "release count must be positive",
        )
    if upgrade_path is not None:
        require(
            upgrade_path in {"direct", "sequential", "both"}, "invalid upgrade path"
        )
    suite_catalog = validate_catalog(profile)
    applicable = chosen
    if all_tests:
        applicable = [
            op
            for op in chosen
            if any(
                "not_applicable" not in env.get("operations", {}).get(op, {})
                and (
                    op != "test"
                    or env.get("operations", {}).get(op, {}).get("target")
                    == "installed"
                )
                for env in envs
            )
        ]
    selected_suites = select_suites(profile, suites, applicable, target_kind)
    target_artifact = (
        profile.get("artifact") if set(chosen) & {"install", "upgrade"} else None
    )
    target = None
    if target_artifact is not None:
        object_value(target_artifact, "artifact")
        target = string(target_artifact.get("version"), "artifact version")
        digest(target_artifact.get("source_sha256"), "artifact source")
    catalog, selected_releases, order, paths = {}, [], [], []
    if "upgrade" in chosen:
        catalog, selected_releases, order, paths = release_paths(
            profile, target, past_releases or 4, from_releases, upgrade_path or "direct"
        )
    defaults = object_value(profile.get("defaults", {}), "defaults")
    baselines = object_value(profile.get("baselines", {}), "baselines")
    cases, skipped, environment_ids = [], [], []
    for original in envs:
        env = dict(original)
        eid = identifier(env.get("id"), "environment id")
        require(eid not in environment_ids, "duplicate environment")
        environment_ids.append(eid)
        explicit_image = image is not None or "image" in original or "image" in defaults
        for key, override in (("image", image), ("platform", platform)):
            if override is not None:
                require(
                    key not in original or original[key] == override,
                    "override conflicts with explicit environment",
                )
                # An override cannot reinterpret an already resolved digest.
                require(
                    override
                    == original.get(
                        key,
                        defaults.get(key, "ubuntu:24.04" if key == "image" else None),
                    ),
                    "override requires a freshly resolved matching profile",
                )
            env[key] = override or original.get(
                key, defaults.get(key, "ubuntu:24.04" if key == "image" else None)
            )
        validate_environment(env)
        configs = object_value(env.get("operations", {}), "operations")
        require(set(configs) <= set(OPERATIONS), "unknown profile operation")
        for op, config in configs.items():
            object_value(config, "operation")
            if "not_applicable" in config:
                require(
                    set(config) == {"not_applicable"},
                    "non-applicable operation has execution fields",
                )
                string(config["not_applicable"], "non-applicability reason")
        for operation in chosen:
            case_target = target_kind if operation == "test" else None
            config = configs.get(operation)
            if (
                all_tests
                and operation == "test"
                and config
                and config.get("target") == "source"
            ):
                config = None
            if operation == "test" and config and "not_applicable" not in config:
                require(
                    config.get("target") == case_target,
                    "operation test target mismatch",
                )
            if all_tests and (config is not None and "not_applicable" in config):
                skipped.append(
                    {
                        "environment": eid,
                        "operation": operation,
                        "reason": config["not_applicable"],
                    }
                )
                continue
            if all_tests and operation == "test" and config is None:
                skipped.append(
                    {
                        "environment": eid,
                        "operation": operation,
                        "reason": "No separate preinstalled test target declared.",
                    }
                )
                continue
            initial = []
            if config is None:
                initial.append("UNDECLARED_OPERATION")
            elif "not_applicable" in config:
                initial.append("EXPLICIT_OPERATION_NOT_APPLICABLE")
            inputs_config = object_value(
                (config or {}).get("inputs", {}), "operation inputs"
            )
            require(
                set(inputs_config)
                <= input_fields(operation, case_target) - {"artifact_sha256"},
                "unknown operation input",
            )
            inputs = {
                key: inputs_config.get(key)
                for key in input_fields(operation, case_target)
            }
            if operation in {"install", "upgrade"}:
                inputs["artifact_sha256"] = artifact_hash(
                    target_artifact, env["platform"]
                )
            for value in inputs.values():
                if value is None:
                    initial.append("MISSING_INPUT")
                else:
                    digest(value, "input digest")
            if case_target == "installed" and not explicit_image:
                initial.append("MISSING_EXPLICIT_IMAGE")
            checks, exclusions, faults, frozen_suites = prepare_checks(
                operation, config, selected_suites, suite_catalog, initial, case_target
            )
            for suite in frozen_suites.values():
                if suite.get("inventory"):
                    require(
                        suite["inventory"]["input_sha256"] == profile["bundle_sha256"],
                        "inventory input mismatch",
                    )
            wanted = paths if operation == "upgrade" else [(None, [], [])]
            for path_kind, path, reasons in wanted:
                blocked = initial + reasons
                artifacts, baseline = {}, None
                if operation == "upgrade" and path:
                    for version in path:
                        item = (
                            target_artifact if version == target else catalog[version]
                        )
                        artifacts[version] = artifact_hash(item, env["platform"])
                        if artifacts[version] is None:
                            blocked.append("MISSING_INPUT")
                    by_env = object_value(
                        baselines.get(eid, {}), "environment baselines"
                    )
                    baseline = by_env.get(path[0])
                    validate_baseline(baseline, env, artifacts[path[0]], False, blocked)
                elif operation in {"patch", "migration"}:
                    baseline = (config or {}).get("baseline")
                    validate_baseline(
                        baseline, env, inputs["source_state_sha256"], True, blocked
                    )
                base_key = fingerprint(
                    {
                        "environment": environment_identity(env),
                        "baseline": baseline,
                        **(
                            {"source_environment": inputs}
                            if case_target == "source"
                            else {}
                        ),
                    }
                )
                for fault in [None] + faults:
                    trial_checks = dict(checks)
                    if fault is not None:
                        trial_checks.update(
                            interruption="interruption", recovery="recovery"
                        )
                    case = {
                        "environment": eid,
                        **{k: env[k] for k in ENV_FIELDS},
                        "operation": operation,
                        "test_target": case_target,
                        "path_kind": path_kind,
                        "path": path,
                        "fault_point": fault,
                        "fault_points": faults,
                        "inputs": inputs,
                        "artifacts": artifacts,
                        "baseline_key": base_key,
                        "checks": trial_checks,
                        "suites": frozen_suites,
                        "exclusions": exclusions,
                        "blockers": sorted(set(blocked)),
                    }
                    cases.append(dict(case, id=fingerprint(case)[:24]))
                    require(
                        len(cases) <= 10000,
                        "matrix exceeds 10000 cases; split the campaign",
                    )
    result = seal(
        {
            "schema": PLAN,
            "profile_sha256": fingerprint(profile),
            "bundle_sha256": profile["bundle_sha256"],
            "selected_operations": chosen,
            "test_target": target_kind,
            "selected_suites": selected_suites,
            "environments": environment_ids,
            "selected_releases": selected_releases,
            "release_order": order,
            "upgrade_paths": [{"kind": kind, "path": path} for kind, path, _ in paths],
            "target_version": target,
            "cases": cases,
            "not_applicable": skipped,
        }
    )
    validate_plan(result)
    require(
        len((json.dumps(result, indent=2, allow_nan=False) + "\n").encode())
        <= MAX_JSON_BYTES,
        "expanded plan exceeds 4 MiB; split the campaign",
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        required=True,
        help="Frozen profile JSON; see references/profile.md",
    )
    for operation in OPERATIONS:
        parser.add_argument(
            "--" + operation, action="store_true", help=f"Select {operation}"
        )
    parser.add_argument(
        "--all-tests",
        action="store_true",
        help="Select applicable lifecycle modes and declared image tests",
    )
    parser.add_argument(
        "--suite",
        action="append",
        default=None,
        help="Named suite; repeatable; default auto",
    )
    selectors = parser.add_mutually_exclusive_group()
    selectors.add_argument("--past-releases", type=int)
    selectors.add_argument(
        "--from-releases", help="Comma-separated exact predecessor labels"
    )
    parser.add_argument("--upgrade-path", choices=("direct", "sequential", "both"))
    parser.add_argument("--image", help="Must match a freshly resolved frozen profile")
    parser.add_argument(
        "--platform", help="Must match a freshly resolved frozen profile"
    )
    parser.add_argument(
        "--test-target", choices=("source", "installed"), help="Default source for test"
    )
    parser.add_argument(
        "--output",
        help="New private JSON file; default stdout (helper-only file semantics)",
    )
    args = parser.parse_args()
    try:
        result = expand(
            read_json(args.profile),
            operations=[op for op in OPERATIONS if getattr(args, op)] or None,
            all_tests=args.all_tests,
            suites=args.suite,
            past_releases=args.past_releases,
            from_releases=args.from_releases.split(",")
            if args.from_releases is not None
            else None,
            upgrade_path=args.upgrade_path,
            image=args.image,
            platform=args.platform,
            test_target=args.test_target,
        )
        text = json.dumps(result, indent=2, allow_nan=False) + "\n"
        write_new(args.output, text) if args.output else print(text, end="")
    except (ValueError, OSError, TypeError, KeyError, RecursionError) as exc:
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else "malformed input or unavailable output"
        )
        print(f"Invalid profile: {message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
