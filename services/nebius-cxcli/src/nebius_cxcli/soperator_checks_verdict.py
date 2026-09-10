"""Read actual native diagnostic verdicts; a successful wrapper is insufficient."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from typing import Any

OUTPUT_LIMIT = 4 * 1024 * 1024
_CUDA_SAMPLES = {"deviceQuery", "vectorAdd", "simpleMultiGPU", "p2pBandwidthLatencyTest"}
_HEALTH_CHECKS = {
    "all-reduce-perf-nccl-with-ib",
    "all-reduce-perf-nccl-without-ib",
    "cuda-samples",
    "dcgmi-diag-r2",
    "dcgmi-diag-r3",
    "extensive-check",
    "gpu-fryer",
    "ib-gpu-perf",
    "mem-perf",
}


def _enabled(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list) or any(
        not isinstance(item, dict) or type(item.get("enable")) is not bool for item in items
    ):
        raise RuntimeError("native health diagnostic has malformed test coverage")
    enabled = [item for item in items if item["enable"]]
    if not enabled:
        raise RuntimeError("native health diagnostic has no enabled test coverage")
    return enabled


def _cuda_coverage(tests: list[dict[str, Any]]) -> None:
    names = [test.get("name") for test in tests]
    if (
        len(tests) != len(_CUDA_SAMPLES)
        or any(not isinstance(name, str) for name in names)
        or set(names) != _CUDA_SAMPLES
        or any(
            test.get("enable") is not True
            or not isinstance(test.get("cmd"), str)
            or test["cmd"].strip() != test["name"]
            or "checks" not in test
            or test["checks"] is not None
            for test in tests
        )
    ):
        raise RuntimeError("native health diagnostic CUDA sample coverage changed")


def health_verdict(
    output: str, *, extensive: bool = False, cuda_samples: bool = False
) -> dict[str, Any]:
    if extensive:
        markers = list(
            re.finditer(r"(?m)^Start health-checker run '([^']+)' on [^\n]+\.\.\.$", output)
        )
        phases = [m[1] for m in markers]
        if phases != [
            "passive_checks",
            "all_reduce_with_ib",
            "all_reduce_without_ib",
            "cuda_samples",
            "gpu_fryer",
            "mem_perf",
        ]:
            raise RuntimeError("native health diagnostic extensive phase coverage is incomplete")
        counts = []
        for index, marker in enumerate(markers):
            end = markers[index + 1].start() if index + 1 < len(markers) else len(output)
            segment = output[marker.end() : end]
            segment = re.sub(
                r"(?m)^Health checker finished with status '([^'\n]*)'\s*$",
                r"Health checker status: \1",
                segment,
            )
            counts.append(
                health_verdict(segment, cuda_samples=phases[index] == "cuda_samples")["checks"]
            )
        return {"kind": "native-health", "status": "PASS", "reports": 6, "checks": sum(counts)}
    blocks = re.split(r"(?m)^Health checker output:\s*\n", output)[1:]
    pattern = r"(?m)^Health checker status: ([^\n]*)$"
    statuses = [s.strip() for s in re.findall(pattern, output)]
    if not blocks or (not extensive and len(blocks) != 1) or len(statuses) != len(blocks):
        raise RuntimeError("native health diagnostic report is missing or ambiguous")
    count = 0
    for block, status in zip(blocks, statuses, strict=True):
        try:
            start = re.search(r"(?m)^\s*\{", block)
            if start is None:
                raise ValueError("missing report object")
            report, end = json.JSONDecoder().raw_decode(block[start.start() :].lstrip())
            tail = block[start.start() :].lstrip()[end:]
            if re.search(r"(?m)^\s*\{", tail):
                raise ValueError("ambiguous report objects")
        except (ValueError, RecursionError) as exc:
            raise RuntimeError("native health diagnostic report is malformed") from exc
        if not isinstance(report, dict):
            raise RuntimeError("native health diagnostic report is malformed")
        if status != "PASS" or report.get("status") != "PASS":
            # Only allow known status tokens into the error, never diagnostic content.
            failed = next(
                (
                    s
                    for s in (status, report.get("status"))
                    if isinstance(s, str) and s in {"FAIL", "ERROR", "EMPTY"}
                ),
                "non-PASS",
            )
            raise RuntimeError(f"native health diagnostic reported {failed}")
        tests = _enabled(report.get("tests"))
        if cuda_samples:
            _cuda_coverage(report["tests"])
        for test in tests:
            state = test.get("state")
            if (
                not isinstance(state, dict)
                or type(state.get("code")) is not int
                or state["code"] != 0
                or state.get("error") != ""
            ):
                raise RuntimeError("native health diagnostic contains a failed test execution")
            # The four pinned CUDA sample commands encode success in their exit
            # status; their native health reports have no threshold subchecks.
            if cuda_samples:
                count += 1
                continue
            for check in _enabled(test.get("checks")):
                state = check.get("state")
                if (
                    not isinstance(state, dict)
                    or state.get("status") != "PASS"
                    or state.get("error") != ""
                ):
                    raise RuntimeError("native health diagnostic contains a non-PASS check")
                count += 1
    return {"kind": "native-health", "status": "PASS", "reports": len(blocks), "checks": count}


def nccl_verdict(output: str, *, gpu_count: int) -> dict[str, Any]:
    starts = re.findall(r"(?m)^# Collective test starting: (\S+)\s*$", output)
    ends = re.findall(r"(?m)^# Collective test concluded: (\S+)\s*$", output)
    gpus = re.findall(r"(?m)^# nThread \d+ nGpus (\d+) .*\bvalidation: (\d+)\b", output)
    bounds = re.findall(r"(?m)^# Out of bounds values\s*:\s*(\d+)\s+(\S+)\s*$", output)
    bandwidth = re.findall(r"(?m)^# Avg bus bandwidth\s*:\s*(\S+)\s*$", output)
    if (
        starts != ["all_reduce_perf"]
        or ends != starts
        or gpu_count <= 0
        or gpus != [(str(gpu_count), "1")]
        or bounds != [("0", "OK")]
        or len(bandwidth) != 1
    ):
        raise RuntimeError("native NCCL diagnostic is missing or did not validate every GPU")
    try:
        speed = float(bandwidth[0])
    except ValueError as exc:
        raise RuntimeError("native NCCL diagnostic bandwidth is malformed") from exc
    if not math.isfinite(speed) or speed <= 0:
        raise RuntimeError("native NCCL diagnostic has no successful transfer")
    return {"kind": "native-nccl", "status": "PASS", "gpus": gpu_count, "outOfBounds": 0}


def read_native_verdict(
    slurm: Callable[[str], str],
    *,
    check: str,
    name: str,
    result: Mapping[str, Any],
) -> dict[str, Any]:
    nodes = result["nodes"]
    job_id = result["jobId"]
    if (
        len(nodes) != 1
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", nodes[0])
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", name)
        or not re.fullmatch(r"[1-9][0-9]*", job_id)
    ):
        raise RuntimeError("native diagnostic output identity is ambiguous")
    # This is the pinned upstream submitter's SBATCH_OUTPUT pattern. No globbing,
    # command substitution, or user-supplied path participates in the read.
    path = f"/opt/soperator-outputs/slurm_jobs/{nodes[0]}.{name}.{job_id}.out"
    output = slurm(f"head -c {OUTPUT_LIMIT + 1} -- {path}")
    raw = output.encode()
    if len(raw) > OUTPUT_LIMIT:
        raise RuntimeError("native diagnostic output exceeds the verification limit")
    if check in _HEALTH_CHECKS:
        verdict = health_verdict(
            output, extensive=check == "extensive-check", cuda_samples=check == "cuda-samples"
        )
    elif check == "all-reduce-perf-nccl-in-docker":
        verdict = nccl_verdict(output, gpu_count=result["allocatedGpus"])
    elif check == "ensure-healthy-nodes":
        if output.splitlines().count("All nodes are healthy") != 1:
            raise RuntimeError("native worker health confirmation is missing")
        verdict = {"kind": "native-node-health", "status": "PASS"}
    elif check in {"prepull-container-image", "enroot-cleanup"}:
        verdict = {"kind": "native-exit", "status": "PASS"}
    else:
        raise RuntimeError("unsupported native diagnostic verdict contract")
    return {**verdict, "outputSha256": "sha256:" + hashlib.sha256(raw).hexdigest()}
