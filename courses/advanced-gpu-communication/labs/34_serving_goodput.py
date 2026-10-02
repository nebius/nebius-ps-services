"""Compare concurrency using fixed generated-token work and latency SLOs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

from common import add_common_args, validate_common_args, write_result
from course_evidence import allocation_gpu_family
from dynamo_experiments import MODEL_REVISION, service
from job_processes import private_folder
from vendor_capture import add_worker_prefix, validate_worker_prefix


def aiperf_tokenizer_config(model_dir):
    """Resolve AIPerf offline from the cache snapshot prepared in shared setup."""
    snapshot = model_dir.resolve()
    if (
        snapshot.name != MODEL_REVISION
        or snapshot.parent.name != "snapshots"
        or snapshot.parent.parent.name != "models--Qwen--Qwen3-8B"
        or not all(
            (snapshot / name).is_file()
            for name in ("config.json", "tokenizer.json", "tokenizer_config.json")
        )
    ):
        raise ValueError(
            "Use the shared README setup's complete pinned Qwen3-8B cache snapshot"
        )
    # AIPerf 0.12 resolves offline tokenizers as Hub IDs, even for local paths.
    return [
        "--tokenizer",
        "Qwen/Qwen3-8B",
        "--tokenizer-revision",
        MODEL_REVISION,
    ], {
        **os.environ,
        "HF_HUB_CACHE": str(snapshot.parents[2]),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }


def profiling_records(path, expected):
    """Require complete measured evidence; readiness/warm-up is not measured work."""
    try:
        rows = [
            json.loads(line) for line in path.read_text().splitlines() if line.strip()
        ]
    except OSError as error:
        raise ValueError("Missing AIPerf evidence: " + path.name) from error
    if any(
        not isinstance(row, dict) or not isinstance(row.get("metadata"), dict)
        for row in rows
    ):
        raise ValueError("Malformed per-request evidence")
    rows = [
        row for row in rows if row["metadata"].get("benchmark_phase") == "profiling"
    ]
    by_id = {}
    for row in rows:
        identity = row["metadata"].get("x_request_id")
        if (
            not isinstance(identity, str)
            or not identity
            or identity in by_id
            or row.get("error")
            or row["metadata"].get("was_cancelled")
        ):
            raise ValueError(
                "Incomplete, duplicate or unsuccessful per-request evidence"
            )
        by_id[identity] = row
    if len(by_id) != expected:
        raise ValueError("Incomplete per-request evidence")
    return by_id


def fixed_work(raw, output_tokens, seed):
    """Check actual wire controls and final server usage, never re-tokenized text."""
    if type(raw.get("status")) is not int or not 200 <= raw["status"] < 300:
        raise ValueError("Raw request has no successful HTTP status")
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Missing transmitted request payload")
    controls = {
        "model": "course-model",
        "seed": seed,
        "min_tokens": output_tokens,
        "max_completion_tokens": output_tokens,
        "ignore_eos": True,
        "n": 1,
        "stream": True,
    }
    if any(
        payload.get(key) != value or type(payload.get(key)) is not type(value)
        for key, value in controls.items()
    ):
        raise ValueError("Transmitted request changed fixed generation controls")
    stream_options = payload.get("stream_options")
    if (
        type(payload.get("temperature")) not in (int, float)
        or payload["temperature"] != 0
        or not isinstance(stream_options, dict)
        or stream_options.get("include_usage") is not True
        or any(
            payload.get(key) is not None
            for key in ("stop", "stop_token_ids", "max_tokens")
        )
        or not isinstance(payload.get("messages"), list)
        or not payload["messages"]
    ):
        raise ValueError("Transmitted request lacks prompts/usage or changes stopping")
    usage, complete = None, False
    responses = raw.get("responses")
    if not isinstance(responses, list):
        raise ValueError("Missing raw streaming responses")
    for response in responses:
        if not isinstance(response, dict) or not isinstance(
            response.get("packets"), list
        ):
            raise ValueError("Malformed raw streaming response")
        packets = response["packets"]
        if any(not isinstance(packet, dict) for packet in packets):
            raise ValueError("Malformed SSE packet")
        values = [
            packet.get("value") for packet in packets if packet.get("name") == "data"
        ]
        if any(not isinstance(value, str) for value in values):
            raise ValueError("Malformed SSE data")
        # Multiple data lines belong to one SSE message, as in the vendor parser.
        value = "\n".join(values).strip()
        if not value:
            continue
        if complete:
            raise ValueError("Trailing streaming data after completion")
        if value == "[DONE]":
            complete = True
            continue
        event = json.loads(value)
        if not isinstance(event, dict) or event.get("error"):
            raise ValueError("Unsuccessful streaming response")
        if event.get("usage"):
            usage = event["usage"]
    if not complete or not isinstance(usage, dict):
        raise ValueError("Incomplete stream or missing server usage")
    prompt_count, completion_count = (
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
    )
    if (
        type(prompt_count) is not int
        or prompt_count <= 0
        or type(completion_count) is not int
        or completion_count != output_tokens
    ):
        raise ValueError("Server token counts differ from fixed generated-token work")
    total = usage.get("total_tokens")
    if total is not None and (
        type(total) is not int or total != prompt_count + completion_count
    ):
        raise ValueError("Server total token count is inconsistent")
    details = usage.get("completion_tokens_details")
    if details is not None:
        if not isinstance(details, dict):
            raise ValueError("Malformed reasoning-token details")
        reasoning = details.get("reasoning_tokens")
        if reasoning is not None and (
            type(reasoning) is not int or not 0 <= reasoning <= completion_count
        ):
            raise ValueError("Reasoning tokens must be part of total completion work")
    return payload, prompt_count, completion_count


def summarize(folder, expected, *, output_tokens, seed):
    summary = json.loads((folder / "profile_export_aiperf.json").read_text())
    if summary.get("schema_version") != "1.4" or summary.get("was_cancelled"):
        raise ValueError("Unknown or cancelled AIPerf summary")

    def metric(name, unit, field="avg", optional=False):
        row = summary.get(name)
        if row is None and optional:
            return 0.0
        if not isinstance(row, dict) or row.get("unit") != unit:
            raise ValueError("Missing or unexpected AIPerf metric unit: " + name)
        value = row.get(field)
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            raise ValueError("Invalid AIPerf metric: " + name)
        return value

    completed = metric("request_count", "requests")
    failed = metric("error_request_count", "requests", optional=True)
    good = metric("good_request_count", "requests")
    if (
        completed != expected
        or failed != 0
        or good != int(good)
        or not 0 <= good <= completed
    ):
        raise ValueError(
            "Missing or failed requests are not an accepted performance result"
        )
    by_id = profiling_records(folder / "profile_export.jsonl", expected)
    raw_by_id = profiling_records(folder / "profile_export_raw.jsonl", expected)
    if set(raw_by_id) != set(by_id):
        raise ValueError("Raw responses do not belong to the measured requests")
    outputs = json.loads((folder / "outputs.json").read_text())
    rows = outputs.get("data", [])
    if (
        outputs.get("schema_version") != "1.0"
        or not isinstance(rows, list)
        or len(rows) != expected
        or any(not isinstance(row, dict) for row in rows)
    ):
        raise ValueError("Incomplete generated-output evidence")
    identities = [row.get("x_request_id") for row in rows]
    if (
        any(not isinstance(x, str) or not x for x in identities)
        or len(set(identities)) != expected
    ):
        raise ValueError(
            "Generated-output request identities are missing or duplicated"
        )
    if any(
        not isinstance(row.get("response_text"), str)
        or not row["response_text"].strip()
        for row in rows
    ):
        raise ValueError("Every completed request must contain generated text")
    if set(by_id) != set(identities):
        raise ValueError("Generated outputs do not belong to the successful requests")
    work, sessions = {}, set()
    for output in rows:
        record = by_id[output["x_request_id"]]
        metadata = record["metadata"]
        raw = raw_by_id[output["x_request_id"]]
        session = metadata.get("session_num")
        if (
            type(session) is not int
            or session < 0
            or session in sessions
            or type(metadata.get("turn_index")) is not int
            or metadata["turn_index"] != 0
        ):
            raise ValueError("Require distinct single-turn logical sessions")
        sessions.add(session)
        for field in (
            "session_num",
            "conversation_id",
            "turn_index",
            "request_start_ns",
            "request_end_ns",
            "was_cancelled",
        ):
            if raw["metadata"].get(field) != metadata.get(field):
                raise ValueError("Raw request identity/timestamps differ from metrics")
        payload, prompt_count, completion_count = fixed_work(raw, output_tokens, seed)
        work[session] = payload
        for field in ("session_num", "conversation_id", "turn_index"):
            expected_value = metadata.get(field)
            if field == "conversation_id":
                expected_value = expected_value or ""
            elif field == "turn_index" and expected_value is None:
                expected_value = 0
            if field not in output or output[field] != expected_value:
                raise ValueError("Output session identity differs from its request")
        start, end = metadata.get("request_start_ns"), metadata.get("request_end_ns")
        if type(start) is not int or type(end) is not int or not 0 < start < end:
            raise ValueError("Invalid per-request wall-clock timestamps")
        if (
            output.get("request_start_ns") != start
            or output.get("request_end_ns") != end
        ):
            raise ValueError("Output timestamps differ from the measured request")
        for name, unit in (
            ("time_to_first_token", "ms"),
            ("inter_token_latency", "ms"),
            ("request_latency", "ms"),
            ("input_sequence_length", "tokens"),
            ("output_sequence_length", "tokens"),
        ):
            measured = record.get("metrics", {}).get(name, {})
            value = measured.get("value")
            if (
                measured.get("unit") != unit
                or type(value) not in (int, float)
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("Missing or invalid per-request metric: " + name)
            if unit == "tokens" and value <= (
                1 if name == "output_sequence_length" else 0
            ):
                raise ValueError(
                    "Actual sequence lengths cannot establish token latency"
                )
            if output.get("metrics", {}).get(name) != value:
                raise ValueError("Output metric differs from measured request")
            if unit == "tokens" and value != (
                prompt_count if name == "input_sequence_length" else completion_count
            ):
                raise ValueError("Sequence-length metric differs from server usage")

    if sessions != set(range(expected)):
        raise ValueError("Logical request positions are incomplete")
    # Session order is stable across runs; random request IDs are not comparison labels.
    ordered = sorted(rows, key=lambda row: (row["session_num"], row["turn_index"]))
    for name in ("input_sequence_length", "output_sequence_length"):
        actual_mean = sum(row["metrics"][name] for row in ordered) / expected
        if not math.isclose(
            metric(name, "tokens"), actual_mean, rel_tol=0, abs_tol=1e-6
        ):
            raise ValueError("Summary token counts differ from per-request evidence")
    signature = hashlib.sha256(
        json.dumps(
            [row["response_text"] for row in ordered], ensure_ascii=True
        ).encode()
    ).hexdigest()
    return {
        "measurement_contract": "fixed-token-goodput-v1",
        "workload_sha256": hashlib.sha256(
            json.dumps(
                [work[row["session_num"]] for row in ordered],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode()
        ).hexdigest(),
        "requested_output_tokens": output_tokens,
        "ttft_p99_ms": metric("time_to_first_token", "ms", "p99"),
        "request_mean_itl_p99_ms": metric("inter_token_latency", "ms", "p99"),
        "requests_per_second": metric("request_throughput", "requests/sec"),
        "goodput_per_second": metric("goodput", "requests/sec"),
        "completed_requests": completed,
        "slo_passed_requests": good,
        "output_signature": signature,
        "output_tokens_mean": metric("output_sequence_length", "tokens"),
        "input_tokens_mean": metric("input_sequence_length", "tokens"),
        "input_lengths": [row["metrics"]["input_sequence_length"] for row in ordered],
        "output_lengths": [row["metrics"]["output_sequence_length"] for row in ordered],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.set_defaults(iterations=128, warmup=0)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=(4, 8, 16, 32), default=8)
    parser.add_argument("--ttft-slo-ms", type=float, default=1000)
    parser.add_argument("--itl-slo-ms", type=float, default=50)
    parser.add_argument("--capture", choices=("none", "systems"), default="none")
    add_worker_prefix(parser)
    args = parser.parse_args()
    validate_worker_prefix(args, server=True)
    if (
        args.iterations != 128
        or args.warmup != 0
        or any(
            not math.isfinite(x) or x <= 0 for x in (args.ttft_slo_ms, args.itl_slo_ms)
        )
    ):
        parser.error("Use 128 requests, zero AIPerf warm-up and positive finite SLOs")
    args.layout, args.router = "aggregated", "round-robin"
    validate_common_args(args)
    binary = os.environ.get("COURSE_AIPERF")
    if not binary or not Path(binary).is_file():
        parser.error("Source the shared README setup's COURSE_AIPERF executable")
    tokenizer_args, client_environment = aiperf_tokenizer_config(args.model_dir)
    folder = private_folder(args, "34_serving_goodput")
    output = folder / "aiperf"
    output_tokens = 32 if args.workload == "small" else 128
    with service(args, folder) as (url, processes):
        command = [
            binary,
            "profile",
            "--model",
            "course-model",
            *tokenizer_args,
            "--url",
            url,
            "--endpoint-type",
            "chat",
            "--streaming",
            "--use-server-token-count",
            "--random-seed",
            str(args.seed),
            "--num-dataset-entries",
            "128",
            "--dataset-sampling-strategy",
            "sequential",
            "--synthetic-input-tokens-mean",
            "256" if args.workload == "small" else "2048",
            "--synthetic-input-tokens-stddev",
            "0",
            "--output-tokens-mean",
            str(output_tokens),
            "--output-tokens-stddev",
            "0",
            "--extra-inputs",
            json.dumps(
                {
                    "temperature": 0,
                    "seed": args.seed,
                    "min_tokens": output_tokens,
                    "ignore_eos": True,
                    "n": 1,
                }
            ),
            "--request-count",
            "128",
            "--concurrency",
            str(args.concurrency),
            "--goodput",
            f"time_to_first_token:{args.ttft_slo_ms} inter_token_latency:{args.itl_slo_ms}",
            "--wait-for-model-timeout",
            "900",
            "--wait-for-model-mode",
            "both",
            "--request-timeout-seconds",
            "120",
            "--export-level",
            "raw",
            "--export-outputs-json",
            "--artifact-dir",
            str(output),
        ]
        client = processes.start("aiperf", command, env=client_environment)
        if client.wait(timeout=1800) != 0:
            raise ValueError("AIPerf failed; retain private client and server logs")
        processes.healthy()
    measured = summarize(output, 128, output_tokens=output_tokens, seed=args.seed)
    if args.capture != "none":
        print("Diagnostic server reports and client measurements:", folder)
        return
    print(
        write_result(
            args,
            lab_id="34_serving_goodput",
            environment={
                "gpu_family": allocation_gpu_family(),
                "allocated_gpus": 16,
                "model_revision": MODEL_REVISION,
                "aiperf_candidate": "0.12.0",
            },
            measurements=measured,
            correctness={
                "all_requests_complete": True,
                "generated_content_present": True,
                "fixed_generated_token_work": True,
                "transmitted_workload_verified": True,
            },
        )
    )


if __name__ == "__main__":
    main()
