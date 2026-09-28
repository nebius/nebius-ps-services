#!/usr/bin/env python3
"""Publish explicitly selected completed lab comparisons outside benchmark timing."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LABEL = re.compile(r"[a-z0-9][a-z0-9_-]{0,47}\Z")


def read_document(path: Path) -> dict:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > 16 * 1024 * 1024
    ):
        raise ValueError("Expected a regular result file smaller than 16 MiB")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")  # noqa: TRY004 -- invalid external document
    return value


def extract(value, path: str):
    """Only explicit recipe paths; wildcard lists have finite positional cases."""
    rows = [("", value)]
    for part in path.split("."):
        following = []
        for case, node in rows:
            if part == "*":
                if not isinstance(node, list) or len(node) > 128:
                    raise ValueError(f"Expected a bounded list at {path}")
                following.extend(
                    (f"{case}-{i}".strip("-"), child) for i, child in enumerate(node)
                )
            elif isinstance(node, dict) and part in node:
                following.append((case, node[part]))
            else:
                raise ValueError(f"Missing declared measurement: {path}")
        rows = following
    return rows


def validate_result(value: dict, lab: str, *, diagnostic: bool = False) -> None:
    if value.get("schema") != "gpu-course-result/v1" or value.get("lab_id") != lab:
        raise ValueError("Result schema or lab identity differs")
    checks = value.get("correctness")

    def failed(node):
        return (
            node is False
            or (isinstance(node, dict) and any(failed(v) for v in node.values()))
            or (isinstance(node, list) and any(failed(v) for v in node))
        )

    if not isinstance(checks, dict) or not checks or failed(checks):
        raise ValueError("Result has no successful correctness evidence")
    if (
        value.get("measurements", {}).get("acceptance_timing") is False
        and not diagnostic
    ):
        raise ValueError("This artifact explicitly excludes acceptance timing")
    experiment = value.get("experiment", {})
    if not isinstance(value.get("environment"), dict) or not isinstance(
        experiment.get("parameters"), dict
    ):
        raise ValueError("Result requires runtime and experiment-control objects")  # noqa: TRY004 -- invalid external document
    if experiment.get("instrumented") is not False and not diagnostic:
        raise ValueError(
            "Acceptance requires an unprofiled result with experiment provenance"
        )
    for key in ("started_unix_seconds", "ended_unix_seconds", "slurm_job_id"):
        number = experiment.get(key)
        if type(number) not in (int, float) or not math.isfinite(number) or number < 0:
            raise ValueError(f"Invalid experiment {key}")
    if experiment["ended_unix_seconds"] < experiment["started_unix_seconds"]:
        raise ValueError("Experiment end precedes its start")


def comparison_payload(baseline: dict, candidate: dict, recipe: dict) -> list[tuple]:
    for document in (baseline, candidate):
        validate_result(
            document, recipe["lab"], diagnostic=recipe.get("kind") == "diagnostic"
        )
    if recipe["lab"] == "environment_readiness":
        for key in ("run_id", "worker_identity", "gpu_uuid"):
            if (
                not baseline.get(key)
                or not candidate.get(key)
                or baseline[key] == candidate[key]
            ):
                raise ValueError(
                    "Readiness requires distinct workers, GPUs, and completed runs"
                )
    if recipe.get("paired_digests"):
        left_digests = baseline["measurements"].get("response_digests")
        right_digests = candidate["measurements"].get("response_digests")
        if (
            not isinstance(left_digests, list)
            or not left_digests
            or len(left_digests) > 128
            or left_digests != right_digests
            or any(
                not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
                for item in left_digests
            )
        ):
            raise ValueError(
                "Paired greedy responses must have identical bounded digests"
            )
    if baseline.get("environment", {}).get("runtime_identity") == "unqualified":
        raise ValueError(
            "Qualify and record the serving runtime image before publishing"
        )
    for key in ("profile", "seed", "environment"):
        left_value, right_value = baseline.get(key), candidate.get(key)
        if key == "environment" and "world_size" in recipe.get("tuning_parameters", []):
            left_value = {k: v for k, v in left_value.items() if k != "rank_count"}
            right_value = {k: v for k, v in right_value.items() if k != "rank_count"}
        if left_value != right_value:
            raise ValueError(f"Comparison requires the same {key}")
    left = baseline["experiment"].get("parameters", {})
    right = candidate["experiment"].get("parameters", {})
    left = dict(left)
    right = dict(right)
    left.pop("variant_order", None)
    right.pop("variant_order", None)
    changed = {
        key for key in left.keys() | right.keys() if left.get(key) != right.get(key)
    }
    allowed = set(recipe.get("tuning_parameters", []))
    if recipe.get("allow_source_change"):
        allowed.add("implementation_sha256")
    if changed - allowed or len(changed) > 1:
        raise ValueError(
            "Comparison changed undeclared controls or more than one variable"
        )
    invariants = recipe.get("invariants", [])
    condition = recipe.get("conditional_invariants", {})
    if condition:
        choice = baseline["measurements"].get(condition["field"])
        invariants = [*invariants, *condition["cases"].get(choice, [])]
    for invariant in invariants:
        path = invariant if isinstance(invariant, str) else invariant["path"]
        varies_with = (
            [] if isinstance(invariant, str) else invariant.get("varies_with", [])
        )
        if changed.intersection(varies_with):
            continue
        if extract(baseline["measurements"], path) != extract(
            candidate["measurements"], path
        ):
            raise ValueError(f"Comparison changed workload invariant: {path}")
    for check in recipe.get("equivalence", []):
        left_values = extract(baseline["measurements"], check["path"])
        right_values = extract(candidate["measurements"], check["path"])
        if len(left_values) != len(right_values):
            raise ValueError("Numerical equivalence cases differ")
        for (left_case, left_value), (right_case, right_value) in zip(
            left_values, right_values, strict=True
        ):
            if (
                left_case != right_case
                or type(left_value) not in (int, float)
                or type(right_value) not in (int, float)
                or not math.isfinite(left_value)
                or not math.isfinite(right_value)
                or not math.isclose(
                    left_value,
                    right_value,
                    rel_tol=check["rtol"],
                    abs_tol=check["atol"],
                )
            ):
                raise ValueError(
                    f"Paired numerical equivalence failed: {check['path']}"
                )
    rows = []
    for slot, document in (("baseline", baseline), ("candidate", candidate)):
        for metric in recipe["metrics"]:
            try:
                values = extract(document["measurements"], metric["path"])
            except ValueError:
                if metric.get("optional", False):
                    continue
                raise
            for index, value in values:
                if value is None and metric.get("optional", False):
                    continue
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise ValueError(f"Non-finite/non-numeric {metric['path']}")
                cases = metric.get("case_values", {}).get(document.get("profile"))
                if cases is not None:
                    if (
                        not index.isdecimal()
                        or int(index) >= len(cases)
                        or len(values) != len(cases)
                    ):
                        raise ValueError(
                            "Measurement cases differ from the workload profile"
                        )
                    case = cases[int(index)]
                    if (
                        recipe["lab"] == "01_cpu_gpu_crossover"
                        and str(
                            document["measurements"]["sizes"][int(index)]["elements"]
                        )
                        != case
                    ):
                        raise ValueError(
                            "Crossover element count differs from the declared case"
                        )
                else:
                    case = metric.get("case", "value") + ("-" + index if index else "")
                rows.append(
                    (metric["name"], slot, case, value * metric.get("scale", 1))
                )
        for key in ("started_unix_seconds", "ended_unix_seconds", "slurm_job_id"):
            rows.append((key, slot, "value", document["experiment"][key]))
        rows.append(("correctness", slot, "value", 1))
    return rows


def validate_endpoint(endpoint: str) -> str:
    parsed = urllib.parse.urlsplit(endpoint)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Use an HTTP(S) service URL without credentials, query or fragment"
        )
    return endpoint.rstrip("/")


def readback(url: str, labels: dict, generation: int, not_before: float = 0) -> bool:
    selector = ",".join(f'{key}="{value}"' for key, value in labels.items())
    metric = f"course_lab_publication_generation{{{selector}}}"
    query = f"({metric} == {generation}) and (timestamp({metric}) >= {not_before:.6f})"
    target = url + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
    with urllib.request.urlopen(target, timeout=5) as response:
        document = json.loads(response.read(2 * 1024 * 1024))
    values = document.get("data", {}).get("result", [])
    return (
        document.get("status") == "success"
        and len(values) == 1
        and float(values[0]["value"][1]) == generation
    )


def write_selection(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".publication-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish(args) -> int:
    from prometheus_client import CollectorRegistry, Gauge, push_to_gateway

    recipes = read_document(ROOT / "reference/observability.json")
    recipe = (
        recipes["setup"]
        if args.lab == "environment_readiness"
        else recipes["labs"][args.lab]
    )
    baseline, candidate = read_document(args.baseline), read_document(args.candidate)
    rows = comparison_payload(baseline, candidate, recipe)
    profile = baseline.get("profile")
    labels = {
        "course": recipes["course"],
        "lab": args.lab,
        "profile": profile,
        "workspace": args.workspace,
    }
    if profile not in ("small", "large") or not all(
        LABEL.fullmatch(v) for v in labels.values()
    ):
        raise ValueError("Invalid fixed workspace/course/lab/profile identity")
    gateway, metrics_url = (
        validate_endpoint(args.gateway),
        validate_endpoint(args.metrics_url),
    )
    os.umask(0o077)
    state_dir = ROOT / "results" / "publications" / args.workspace
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    key = args.lab + "-" + profile
    selection = hashlib.sha256(
        json.dumps([baseline, candidate], sort_keys=True).encode()
    ).hexdigest()
    state_path = state_dir / (key + ".json")
    lock_path = state_dir / (key + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_document(state_path) if state_path.exists() else {"generation": 0}
        if args.expected_generation != state["generation"]:
            raise ValueError(
                f"Selection changed; current generation is {state['generation']}. Review it before publishing."
            )
        same = state.get("selection") == selection
        generation = state["generation"] if same else state["generation"] + 1
        registry = CollectorRegistry()
        gauges = {}
        for name, slot, case, value in rows:
            if name not in gauges:
                gauges[name] = Gauge(
                    "course_lab_" + name,
                    "Selected completed lab result: " + name,
                    ["slot", "case"],
                    registry=registry,
                )
            gauges[name].labels(slot, case).set(value)
        Gauge(
            "course_lab_publication_generation",
            "Selected comparison generation",
            registry=registry,
        ).set(generation)
        # Persist the selection before transport: ambiguous delivery is retried only
        # with the same generation/artifacts, and older queued writers fail CAS.
        updated = {
            "generation": generation,
            "selection": selection,
            "status": "pending",
        }
        write_selection(state_path, updated)
        attempted_at = time.time()
        try:
            push_to_gateway(
                gateway,
                job="course_lab_results",
                grouping_key=labels,
                registry=registry,
                timeout=5,
            )
        except OSError as exc:
            raise RuntimeError(
                f"Publication transport failed; benchmark artifacts remain valid. Republish with --expected-generation {generation}."
            ) from exc
        deadline = time.monotonic() + args.readback_timeout
        while True:
            try:
                confirmed = readback(metrics_url, labels, generation, attempted_at)
            except (OSError, ValueError, KeyError):
                confirmed = False
            if confirmed:
                updated["status"] = "confirmed"
                write_selection(state_path, updated)
                print(
                    f"Published comparison generation {generation}; selected completed runs, not current activity."
                )
                return generation
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Publication not confirmed. Benchmark artifacts remain valid. Republish the same selection with --expected-generation {generation}."
                )
            time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lab", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--workspace",
        default=os.environ.get("COURSE_WORKSPACE"),
        required=not bool(os.environ.get("COURSE_WORKSPACE")),
    )
    parser.add_argument(
        "--gateway",
        default=os.environ.get("COURSE_PUSHGATEWAY"),
        required=not bool(os.environ.get("COURSE_PUSHGATEWAY")),
    )
    parser.add_argument(
        "--metrics-url",
        default=os.environ.get("COURSE_METRICS_URL"),
        required=not bool(os.environ.get("COURSE_METRICS_URL")),
    )
    parser.add_argument(
        "--expected-generation",
        type=int,
        required=True,
        help="0 for a new comparison; otherwise use the reviewed current generation.",
    )
    parser.add_argument("--readback-timeout", type=int, default=90)
    args = parser.parse_args()
    if not 1 <= args.readback_timeout <= 300 or args.expected_generation < 0:
        parser.error(
            "readback timeout must be 1..300 seconds and generation nonnegative"
        )
    try:
        publish(args)
    except (ValueError, RuntimeError, OSError, KeyError, ImportError) as exc:
        raise SystemExit(
            f"Publication failed separately from benchmark execution: {exc}"
        ) from exc


if __name__ == "__main__":
    main()
