"""Verify original artifacts and construct bounded, sanitized student projections."""

from __future__ import annotations

import csv
import importlib.util
import math
import re
import shutil
import struct
from pathlib import Path

from run_labs_common import digest, directory, read, safe_path, token, write


def publisher(root):
    spec = importlib.util.spec_from_file_location(
        "run_labs_publisher", root / "tools/publish_results.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def png(path):
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Invalid screenshot PNG")
    width, height = struct.unpack(">II", header[16:24])
    if width < 640 or height < 360:
        raise ValueError("Screenshot is too small for evidence review")
    return {"width": width, "height": height}


def verify_inventory(raw, inventory):
    if inventory.get("schema") != "run-labs-inventory/v1":
        raise ValueError("Invalid raw inventory")
    seen = set()
    for item in inventory["files"]:
        name = item["path"]
        p = Path(name)
        if p.is_absolute() or ".." in p.parts or name in seen:
            raise ValueError("Invalid or duplicate artifact path")
        seen.add(name)
        path = safe_path(raw / p)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item["size"]
            or digest(path) != item["sha256"]
        ):
            raise ValueError("Local artifact differs from remote checksum inventory")
    if not seen:
        raise ValueError("Empty raw artifact inventory")
    return {r["path"]: r for r in inventory["files"]}


def verify_nixl_transfer(unit, report):
    """Verify the pinned UCX exercise, whose GPU DMA needs no CUDA kernel."""
    proof = report.get("nixl_transfer")
    if (
        report["tool"] != "systems"
        or report.get("producer") not in {"rank-0", "rank-1"}
        or not isinstance(proof, dict)
        or proof.get("role") not in {"initiator", "target"}
    ):
        raise ValueError("Native NIXL report lacks its exact process role")
    role = proof["role"]
    required = {role, "nixl::registerMem"}
    required.add(
        "nixl::postXferReq.write" if role == "initiator" else "nixl::getNotifs"
    )
    if not required <= set(report["nvtx"]) or any(
        type(proof.get(key)) is not int or proof[key] <= 0
        for key in ("cuda_api_events", "osrt_events", "registration_events")
    ):
        raise ValueError("Native NIXL report lacks workload activity")
    if (
        type(proof.get("buffer_bytes")) is not int
        or proof["buffer_bytes"] != 2**30
        or type(proof.get("buffer_device")) is not int
        or proof["buffer_device"] != 0
    ):
        raise ValueError("Native NIXL GPU buffer differs from the workload")
    size = {"small": 4096, "large": 4 * 2**20}[unit["profile"]]
    if role == "initiator":
        expected = {
            "write_ranges": 550,
            "put_submissions": 550,
            "put_completions": 550,
            "put_bytes": 550 * size,
        }
        if any(
            type(proof.get(k)) is not int or proof[k] != v for k, v in expected.items()
        ):
            raise ValueError(
                "Native NIXL transfers differ from all warmup and measured writes"
            )
    else:
        copy = proof.get("validation_copy")
        if (
            type(proof.get("notification_polls")) is not int
            or proof["notification_polls"] <= 0
            or type(proof.get("notification_end_ns")) is not int
            or proof["notification_end_ns"] <= 0
            or not isinstance(copy, dict)
            or any(
                type(copy.get(k)) is not int
                for k in ("bytes", "device", "start_ns", "end_ns")
            )
            or copy["bytes"] != size
            or copy["device"] != 0
            or copy.get("direction") != "device-to-host"
            or not proof["notification_end_ns"] < copy["start_ns"] < copy["end_ns"]
        ):
            raise ValueError(
                "Native NIXL target lacks its final payload validation copy"
            )
    return "matching_nixl_transfer"


def verify_routing_idle(unit, stage, report):
    """Validate a profiled KV replica that received no service requests."""
    proof = report.get("routing_idle")
    if (
        (unit["course"], unit["lab"])
        != ("advanced-gpu-communication", "33_dynamo_routing")
        or stage.get("tool") != "systems"
        or stage.get("variant") != "candidate"
        or not isinstance(proof, dict)
        or proof.get("schema") != "run-labs-routing-idle/v1"
        or proof.get("router") != "kv"
        or report["kernels"]
        or any(name.startswith("execute_context_") for name in report["nvtx"])
    ):
        raise ValueError("Native idle proof is not an eligible KV routing producer")
    producer = report.get("producer")
    if (
        not isinstance(producer, str)
        or producer not in {"rank-0", "rank-1"}
        or proof.get("producer") != producer
        or not isinstance(proof.get("peer_producer"), str)
        or proof.get("peer_producer") not in {"rank-0", "rank-1"} - {producer}
        or type(proof.get("job")) is not int
        or type(report.get("job")) is not int
        or type(stage.get("dispatch", {}).get("job")) is not int
        or proof["job"] != report.get("job")
        or proof["job"] != stage.get("dispatch", {}).get("job")
    ):
        raise ValueError("Native idle producer identity differs")
    # The authored Lab33 service session has 64 measured, four warmup and
    # one readiness request. Every selection/completion must belong to its peer.
    expected = {
        "selected_requests": 0,
        "completed_requests": 0,
        "kernel_events": 0,
        "inference_nvtx_events": 0,
        "session_requests": 69,
        "peer_selected_requests": 69,
        "peer_completed_requests": 69,
    }
    if any(type(proof.get(k)) is not int or proof[k] != v for k, v in expected.items()):
        raise ValueError("Native idle routing or activity counts differ")
    devices = proof.get("gpu_devices")
    starts = proof.get("profiler_starts")
    stops = proof.get("profiler_stops")
    if (
        not isinstance(devices, list)
        or any(type(v) is not int for v in devices)
        or sorted(devices) != list(range(8))
        or not isinstance(starts, list)
        or len(starts) != 8
        or not isinstance(stops, list)
        or not 1 <= len(stops) <= 8
        or proof.get("capture_start_acknowledged") is not True
        or proof.get("capture_stop_acknowledged") is not True
    ):
        raise ValueError("Native idle capture must cover eight GPU workers")
    processes = {}
    for label, rows in (("start", starts), ("stop", stops)):
        seen = set()
        for row in rows:
            if (
                not isinstance(row, dict)
                or any(type(row.get(k)) is not int for k in ("pid", "timestamp_ns"))
                or row["pid"] <= 0
                or row["pid"] in seen
                or row["timestamp_ns"] <= 0
            ):
                raise ValueError("Native idle profiler API evidence differs")
            seen.add(row["pid"])
            if label == "start":
                processes[row["pid"]] = row["timestamp_ns"]
            elif row["pid"] not in processes or row["timestamp_ns"] <= max(
                processes.values()
            ):
                raise ValueError("Native idle profiler stop must follow all starts")
    return "matching_routing_idle"


def verify_routing_producer_coverage(unit, stage, reports):
    """An idle replica never replaces the active peer's inference evidence."""
    idle = [r for r in reports if "routing_idle" in r]
    if not idle:
        return
    if len(idle) != 1 or len(reports) != 2:
        raise ValueError("Native idle routing requires exactly one active peer")
    report = idle[0]
    verify_native_content(unit, stage, report)
    proof = report["routing_idle"]
    peers = [r for r in reports if r.get("producer") == proof["peer_producer"]]
    if len(peers) != 1:
        raise ValueError("Native idle routing peer is missing or duplicated")
    peer = peers[0]
    if (
        peer.get("job") != report["job"]
        or verify_native_content(unit, stage, peer) != "matching_kernel_or_nvtx"
        or peer.get("routing_activity")
        != {
            "selected_requests": proof["peer_selected_requests"],
            "completed_requests": proof["peer_completed_requests"],
        }
        or any(type(v) is not int for v in peer["routing_activity"].values())
    ):
        raise ValueError("Native idle routing peer lacks complete inference evidence")


def verify_host_copy(unit, stage, report):
    """Require the complete Lab 03 copy mechanism, not incidental CUDA kernels."""
    proof = report.get("host_copy")
    if (
        (unit["course"], unit["lab"]) != ("gpu-fundamentals", "03_transfer_and_pinning")
        or stage.get("kind") != "profile"
        or stage.get("tool") != "systems"
        or stage.get("variant") not in {"baseline", "candidate"}
        or stage.get("id") != "systems-" + stage["variant"]
        or report.get("stage") != stage["id"]
        or report.get("producer") != "rank-0"
        or not isinstance(proof, dict)
        or proof.get("schema") != "run-labs-host-copy/v1"
        or proof.get("producer") != report["producer"]
        or any(
            type(value) is not int
            for value in (
                proof.get("job"),
                report.get("job"),
                stage.get("dispatch", {}).get("job"),
            )
        )
        or not proof["job"] == report["job"] == stage["dispatch"]["job"] > 0
        or "lab_workload" not in report["nvtx"]
    ):
        raise ValueError("Native host-copy identity or workload differs")
    argv = stage.get("argv", [])
    if (
        not isinstance(argv, list)
        or any(not isinstance(value, str) for value in argv)
        or argv.count("labs/03_transfer_and_pinning.py") != 1
    ):
        raise ValueError("Native host-copy command differs")
    size_mib = {"baseline": 64, "candidate": 128}[stage["variant"]]
    # These are the reviewed recipe's explicit sizes and the frozen source's
    # defaults, independent of the small/large label.
    expected = {"--size-mib": str(size_mib), "--workload": unit["profile"]}
    for flag, value in {**expected, "--warmup": "5", "--iterations": "20"}.items():
        count = argv.count(flag)
        if (
            any(value.startswith(flag + "=") for value in argv)
            or (flag in expected and count != 1)
            or count > 1
            or (
                count
                and (
                    argv.index(flag) + 1 >= len(argv)
                    or argv[argv.index(flag) + 1] != value
                )
            )
        ):
            raise ValueError("Native host-copy effective parameters differ")
    workload, modes = proof.get("workload"), proof.get("modes")
    if (
        unit["profile"] not in {"small", "large"}
        or type(proof.get("buffer_bytes")) is not int
        or proof["buffer_bytes"] != size_mib * 2**20
        or type(proof.get("device")) is not int
        or proof["device"] != 0
        or any(
            type(proof.get(k)) is not int or proof[k] <= 0
            for k in ("context_id", "global_pid", "global_tid")
        )
        # Nsight serializes the local thread ID into the low 24 bits.
        or proof["global_pid"] != proof["global_tid"] - proof["global_tid"] % 0x1000000
        or type(proof.get("stream_id")) is not int
        or proof["stream_id"] < 0
        or not isinstance(workload, dict)
        or workload.get("name") != "lab_workload"
        or any(type(workload.get(k)) is not int for k in ("start_ns", "end_ns"))
        or not 0 < workload["start_ns"] < workload["end_ns"]
        or not isinstance(modes, list)
        or len(modes) != 4
    ):
        raise ValueError("Native host-copy buffer, process or scope differs")
    previous_end = workload["start_ns"]
    configurations = [
        ("pageable_blocking", "Pageable", 25),
        ("pageable_nonblocking", "Pageable", 0),
        ("pinned_blocking", "Pinned", 25),
        ("pinned_nonblocking", "Pinned", 0),
        (None, "Pageable", 1),
    ]
    for index, ((name, memory, stream_syncs), row) in enumerate(
        zip(configurations, [*modes, proof.get("validation_copy")])
    ):
        count = 25 if index < 4 else 1
        counters = {
            "submissions": count,
            "completions": count,
            "correlated_copies": count,
            "bytes": count * proof["buffer_bytes"],
            "device_synchronizations": 21 if index < 4 else 0,
            "stream_synchronizations": stream_syncs,
        }
        times = (
            "first_submission_ns",
            "last_submission_end_ns",
            "first_copy_start_ns",
            "last_copy_end_ns",
        )
        if (
            not isinstance(row, dict)
            or (name is not None and row.get("mode") != name)
            or row.get("host_memory") != memory
            or row.get("direction")
            != ("host-to-device" if index < 4 else "device-to-host")
            or any(
                type(row.get(k)) is not int or row[k] != v for k, v in counters.items()
            )
            or type(row.get("segments")) is not int
            or row["segments"] < count
            or any(type(row.get(k)) is not int for k in times)
            or not previous_end
            < row["first_submission_ns"]
            < row["last_submission_end_ns"]
            < workload["end_ns"]
            or not row["first_submission_ns"]
            <= row["first_copy_start_ns"]
            < row["last_copy_end_ns"]
            < workload["end_ns"]
        ):
            raise ValueError("Native host-copy modes or final readback differ")
        previous_end = max(row["last_submission_end_ns"], row["last_copy_end_ns"])
    return "matching_host_copy"


def verify_native_content(unit, stage, report):
    """Require the measured mechanism, including vendor workloads without NVTX."""
    kernels, nvtx = report.get("kernels"), report.get("nvtx")
    if (
        report.get("tool") != stage["tool"]
        or not isinstance(kernels, list)
        or not isinstance(nvtx, list)
        or any(not isinstance(value, str) or not value for value in kernels + nvtx)
    ):
        raise ValueError("Native report has invalid kernel or NVTX content")
    if "host_copy" in report or (unit["course"], unit["lab"]) == (
        "gpu-fundamentals",
        "03_transfer_and_pinning",
    ):
        return verify_host_copy(unit, stage, report)
    if "routing_idle" in report:
        return verify_routing_idle(unit, stage, report)
    if (unit["course"], unit["lab"]) == (
        "advanced-gpu-communication",
        "29_nixl_transfer",
    ):
        return verify_nixl_transfer(unit, report)
    if not kernels:
        raise ValueError("Native report has invalid kernel or NVTX content")
    if (unit["course"], unit["lab"]) != (
        "advanced-gpu-communication",
        "06_nvlink_bandwidth",
    ):
        if not nvtx:
            raise ValueError("Native report lacks expected kernels and NVTX content")
        return "matching_kernel_or_nvtx"
    if stage["tool"] != "systems" or report["producer"] != "vendor-0":
        raise ValueError("Native nvbandwidth capture must identify its vendor producer")
    if stage["variant"] == "candidate":
        if not any("stridingMemcpyKernel" in name for name in kernels):
            raise ValueError("Native nvbandwidth capture lacks its SM copy kernel")
        return "matching_kernel_or_nvtx"
    if stage["variant"] != "baseline":
        raise ValueError("Native nvbandwidth capture has an unknown mechanism")
    pairs = report.get("peer_copies")
    if not isinstance(pairs, list) or len(pairs) != 56:
        raise ValueError("Native peer-copy evidence must cover all 56 directed pairs")
    buffer_bytes = {"small": 64, "large": 512}[unit["profile"]] * 2**20
    seen = set()
    for pair in pairs:
        if not isinstance(pair, dict) or any(
            type(pair.get(key)) is not int
            for key in ("source", "destination", "events", "bytes")
        ):
            raise ValueError("Native peer-copy evidence has invalid counters")
        key = pair["source"], pair["destination"]
        if (
            key in seen
            or not all(0 <= device < 8 for device in key)
            or key[0] == key[1]
            or pair["events"] <= 0
            or pair["bytes"] != pair["events"] * buffer_bytes
        ):
            raise ValueError("Native peer-copy evidence differs from the workload")
        seen.add(key)
    return "matching_peer_copy"


def validate(unit, raw, receipt, root):
    """Receipt selects originals; correctness and provenance are checked again here."""
    if (
        receipt.get("schema") != "run-labs-evidence/v1"
        or receipt.get("lab") != unit["lab"]
        or receipt.get("profile") != unit["profile"]
    ):
        raise ValueError("Evidence identity differs")
    inventory = verify_inventory(raw, read(raw / "inventory.json"))
    api = publisher(root)
    obs = read(root / unit["course"] / "reference/observability.json")["labs"][
        unit["lab"]
    ]
    stages = {
        s["id"]: s
        for s in unit["stages"]
        if s["kind"] in ("execute", "profile", "dependency")
    }
    selections = receipt["results"]
    accepted = {}
    for stage_id, names in selections.items():
        stage = stages.get(stage_id)
        if (
            not stage
            or stage["kind"] != "execute"
            or len(names) != unit["recipe"]["result_count"]
        ):
            raise ValueError(
                "Acceptance stage result cardinality differs from its trial contract"
            )
        if len(set(names)) != len(names):
            raise ValueError("A result cannot satisfy multiple trials")
        for name in names:
            if name not in inventory:
                raise ValueError("Selected result is absent from original inventory")
            result = read(raw / name)
            api.validate_result(
                result, unit["lab"], diagnostic=unit["recipe"]["kind"] == "diagnostic"
            )
            if (
                result["profile"] != unit["profile"]
                or result["experiment"]["slurm_job_id"] != stage["dispatch"]["job"]
            ):
                raise ValueError("Result profile or producing job differs")
            accepted[name] = result
        trials = [accepted[name] for name in names]
        contract = unit["recipe"].get("trial_contract")
        if contract == "capstone":
            orders = [r["measurements"].get("variant_order") for r in trials]
            if (
                orders != ["baseline-first", "candidate-first", "baseline-first"]
                or len({r["run_id"] for r in trials}) != 3
            ):
                raise ValueError(
                    "Capstone requires three fresh counterbalanced trials in order"
                )
        elif contract == "engine-pairs":
            for index in range(0, 6, 2):
                api.comparison_payload(trials[index], trials[index + 1], obs)
            # Server launchers share one campaign run ID across fresh clients.
            # Distinct process windows and paired variants establish six trials.
            windows = {
                (
                    r["experiment"]["started_unix_seconds"],
                    r["experiment"]["ended_unix_seconds"],
                )
                for r in trials
            }
            if len(windows) != 6:
                raise ValueError(
                    "Engine clients must have six distinct process windows"
                )
    expected = {key for key, s in stages.items() if s["kind"] == "execute"}
    if set(selections) != expected:
        raise ValueError("Missing or extra acceptance stages")
    for pair in unit["recipe"]["comparisons"]:
        for trial in range(1, unit["recipe"]["repetitions"] + 1):
            left = selections[f"{pair[0]}-trial{trial}"]
            right = selections[f"{pair[1]}-trial{trial}"]
            if len(left) != 1 or len(right) != 1:
                raise ValueError("A comparison must select exact single results")
            api.comparison_payload(accepted[left[0]], accepted[right[0]], obs)
    screenshots = receipt["screenshots"]
    required = {"grafana"} | {
        tool for tool, cmd in unit["recipe"]["profilers"].items() if cmd
    }
    if {r["tool"] for r in screenshots} != required:
        raise ValueError("Missing or unexpected browser evidence lane")
    expected_metrics = {}
    for stage_id, names in selections.items():
        for result_index, name in enumerate(names):
            for metric in obs["metrics"]:
                try:
                    values = api.extract(accepted[name]["measurements"], metric["path"])
                except ValueError:
                    if metric.get("optional"):
                        continue
                    raise
                for case, value in values:
                    if value is None and metric.get("optional"):
                        continue
                    expected_metrics[(stage_id, result_index, metric["name"], case)] = (
                        value * metric.get("scale", 1),
                        metric["unit"],
                    )
    seen_metrics = set()
    native = receipt.get("native_reports", [])
    native_paths = set()
    native_content_checks = {}
    for stage_id, stage in stages.items():
        if stage["kind"] != "profile":
            continue
        reports = [r for r in native if r["stage"] == stage_id]
        if len(reports) != stage["expected_reports"]:
            raise ValueError(
                "Native report producer coverage differs from frozen capture recipe"
            )
        producers = set()
        for report in reports:
            name = report["path"]
            producer = report["producer"]
            if name in native_paths or producer in producers or name not in inventory:
                raise ValueError("Missing or duplicate native producer/report")
            native_paths.add(name)
            producers.add(producer)
            if (
                report["sha256"] != inventory[name]["sha256"]
                or report["job"] != stage["dispatch"]["job"]
            ):
                raise ValueError("Native report hash or producing job differs")
            native_content_checks[name] = verify_native_content(unit, stage, report)
            proof = report["proof"]
            if (
                proof not in inventory
                or inventory[proof]["sha256"] != report["proof_sha256"]
            ):
                raise ValueError("Native CLI proof is not bound to collected bytes")
            if report.get("native_cli_verified") is not True or not isinstance(
                report.get("warnings"), list
            ):
                raise ValueError(
                    "Native report verification and warning scope are required"
                )
        verify_routing_producer_coverage(unit, stage, reports)
        if (unit["course"], unit["lab"]) == (
            "advanced-gpu-communication",
            "29_nixl_transfer",
        ) and {r["nixl_transfer"]["role"] for r in reports} != {"initiator", "target"}:
            raise ValueError("Native NIXL capture must cover both process roles")
    shown_stages = set()
    for shot in screenshots:
        path = safe_path(raw / shot["path"])
        relative = Path(shot["path"])
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or path.is_symlink()
            or digest(path) != shot["sha256"]
        ):
            raise ValueError("Screenshot checksum or local staging path differs")
        png(path)
        if (
            shot.get("browser") != "playwright-headless"
            or shot.get("visually_reviewed") is not True
        ):
            raise ValueError("Headless Playwright and visual review are required")
        if shot.get("public_view_reviewed") is not True:
            raise ValueError(
                "Review the public capture for private paths, hostnames and credentials"
            )
        if shot["tool"] == "grafana":
            dashboard = read(
                root / unit["course"] / "reference/grafana" / (unit["lab"] + ".json")
            )
            if (
                shot.get("dashboard_uid") != dashboard["uid"]
                or shot.get("profile") != unit["profile"]
                or type(shot.get("generation")) is not int
                or shot["generation"] < 1
            ):
                raise ValueError(
                    "Grafana dashboard, profile or publication generation differs"
                )
            numeric_checks = shot.get("numeric_checks")
            if not isinstance(numeric_checks, list) or (
                expected_metrics and not numeric_checks
            ):
                raise ValueError("Grafana numeric comparisons are incomplete")
            for check in numeric_checks:
                key = (
                    check["stage"],
                    check["result_index"],
                    check["metric"],
                    check["case"],
                )
                if key not in expected_metrics:
                    raise ValueError("Unexpected Grafana metric selection")
                expected, unit_name = expected_metrics[key]
                observed = check["observed_base_units"]
                if (
                    type(observed) not in (int, float)
                    or check["unit"] != unit_name
                    or not math.isclose(
                        observed, expected, rel_tol=0.005, abs_tol=1e-12
                    )
                ):
                    raise ValueError(
                        "Grafana rendered numeric value differs from the original metric"
                    )
                seen_metrics.add(key)
        else:
            report = shot["report"]
            if (
                report not in native_paths
                or inventory[report]["sha256"] != shot["report_sha256"]
            ):
                raise ValueError("Native browser report identity differs")
            proof = shot.get("content_checks", {})
            if (
                proof.get("matching_job") is not True
                or proof.get(native_content_checks[report]) is not True
                or proof.get("native_cli_verified") is not True
            ):
                raise ValueError("Native report content was not independently verified")
            if native_content_checks[report] != "matching_routing_idle":
                shown_stages.add(
                    next(r["stage"] for r in native if r["path"] == report)
                )
    if seen_metrics != set(expected_metrics):
        raise ValueError("Grafana did not verify every selected result metric")
    if shown_stages != {key for key, s in stages.items() if s["kind"] == "profile"}:
        raise ValueError(
            "Each capture configuration needs a representative native screenshot"
        )
    return accepted, obs


def numeric_projection(node):
    """Keep scientific numbers and booleans, never arbitrary infrastructure text."""
    if node is None or type(node) in (bool, int):
        return node
    if isinstance(node, float):
        if not math.isfinite(node):
            raise ValueError("Non-finite public measurement")
        return node
    if isinstance(node, list):
        return [numeric_projection(v) for v in node]
    if isinstance(node, dict):
        result = {
            k: numeric_projection(v)
            for k, v in node.items()
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_ .()-]{0,100}", k)
            and not re.search(
                r"host|path|url|address|secret|tokenizer|prompt|response|command|stdout|stderr",
                k,
                re.IGNORECASE,
            )
            and not isinstance(v, str)
        }
        public_choices = {
            "mode": {
                "serial",
                "workers",
                "pooled",
                "nonblocking",
                "pipeline",
                "baseline",
                "optimized",
            },
            "variant": {
                "default",
                "socket",
                "gdr-off",
                "ring",
                "tree",
                "qp1",
                "qp4",
                "enabled",
                "disabled",
                "target-only",
                "speculative",
            },
            "device": {"cpu", "cuda"},
            "dtype": {"float32", "float16", "bfloat16", "fp32", "fp16", "bf16"},
            "layout": {"flat", "hierarchical", "aggregated", "disaggregated"},
            "hook": {"allreduce", "fp16", "bf16", "powersgd"},
            "overlap": {"off", "on"},
            "router": {"round-robin", "kv"},
            "variant_order": {"baseline-first", "candidate-first"},
        }
        for key, choices in public_choices.items():
            if isinstance(node.get(key), str) and node[key] in choices:
                result[key] = node[key]
        return result
    return None


def build_public(unit, raw, receipt, root, output, source_sha256):
    accepted, obs = validate(unit, raw, receipt, root)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Public staging must be empty")
    directory(output, private=False)
    api = publisher(root)
    table = []
    artifacts = []
    for stage, names in sorted(receipt["results"].items()):
        for index, name in enumerate(names):
            result = accepted[name]
            suffix = f"-{index + 1}" if len(names) > 1 else ""
            filename = "result-" + token(stage) + suffix + ".json"
            projection = {
                "schema": "course-student-result/v1",
                "lab": unit["lab"],
                "profile": unit["profile"],
                "measurements": numeric_projection(result["measurements"]),
                "correctness": numeric_projection(result["correctness"]),
                "parameters": numeric_projection(result["experiment"]["parameters"]),
            }
            write(output / filename, projection, private=False)
            artifacts.append(
                {
                    "file": filename,
                    "sha256": digest(output / filename),
                    "original_sha256": digest(raw / name),
                    "variant": stage,
                }
            )
            for metric in obs["metrics"]:
                try:
                    values = api.extract(result["measurements"], metric["path"])
                except ValueError:
                    if metric.get("optional"):
                        continue
                    raise
                for case, value in values:
                    if value is None and metric.get("optional"):
                        continue
                    if type(value) not in (float, int) or not math.isfinite(value):
                        raise ValueError("Invalid exported metric")
                    cases = metric.get("case_values", {}).get(unit["profile"])
                    if cases is not None:
                        if (
                            not case.isdecimal()
                            or int(case) >= len(cases)
                            or len(values) != len(cases)
                        ):
                            raise ValueError(
                                "Exported cases differ from the workload profile"
                            )
                        case = cases[int(case)]
                    table.append(
                        [
                            stage + suffix,
                            metric["name"],
                            case or metric.get("case", "value"),
                            value * metric.get("scale", 1),
                            metric["unit"],
                        ]
                    )
    with (output / "summary.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["variant", "metric", "case", "value", "unit"])
        writer.writerows(table)
    for shot in receipt["screenshots"]:
        filename = token(shot["name"]) + ".png"
        if (output / filename).exists():
            raise ValueError("Duplicate stable screenshot name")
        shutil.copyfile(raw / shot["path"], output / filename)
        artifacts.append(
            {
                "file": filename,
                "sha256": digest(output / filename),
                "tool": shot["tool"],
            }
        )
    hardware = receipt["hardware"]
    if (
        set(hardware) != {"gpu_model", "gpu_count"}
        or type(hardware["gpu_count"]) is not int
        or hardware["gpu_count"] < 1
        or not re.fullmatch(r"NVIDIA [A-Za-z0-9 ._()+-]{1,60}", hardware["gpu_model"])
    ):
        raise ValueError("Provide bounded public GPU model/count metadata")
    for result in accepted.values():
        family = result["environment"].get("gpu_family")
        if family and family != hardware["gpu_model"]:
            raise ValueError("Public GPU identity differs from the original result")
    software = {}
    for result in accepted.values():
        for key in ("torch_version", "cuda_version", "python_version"):
            value = result["environment"].get(key)
            if value is not None:
                if not isinstance(value, str) or not re.fullmatch(
                    r"[0-9][A-Za-z0-9.+_-]{0,79}", value
                ):
                    raise ValueError("Invalid public software version")
                software.setdefault(key, set()).add(value)
    allowed_limits = {
        "incomplete-events",
        "separate-clock-domains",
        "representative-gui-rank",
        "diagnostic-companion",
        "vendor-display-unit",
        "unlocked-gpu-clocks",
        "cpu-scheduling-unavailable",
    }
    limits = receipt.get("limitations", [])
    if not isinstance(limits, list) or not set(limits) <= allowed_limits:
        raise ValueError(
            "Use declared public limitation codes; retain raw diagnostics privately"
        )
    manifest = {
        "schema": "course-lab-results/v1",
        "course": unit["course"],
        "lab": unit["lab"],
        "profile": unit["profile"],
        "source_sha256": source_sha256,
        "hardware": hardware,
        "software": {k: sorted(v) for k, v in software.items()},
        "limitations": sorted(set(limits)),
        "artifacts": artifacts,
        "profilers": {
            t: ("verified" if cmd else "not-applicable")
            for t, cmd in unit["recipe"]["profilers"].items()
        },
        "summary_sha256": digest(output / "summary.csv"),
    }
    write(output / "manifest.json", manifest, private=False)
    return manifest


def bundle(course):
    root = course / "reference/lab-results"
    if not root.exists():
        return None
    spec = importlib.util.spec_from_file_location(
        "run_labs_course_archives", course.parent / "tools/course_archives.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.publish_results(course)
