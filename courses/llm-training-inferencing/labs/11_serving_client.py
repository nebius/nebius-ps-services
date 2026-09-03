"""Benchmark a local OpenAI-compatible completion endpoint with concurrent requests."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import resolve_run_id, write_json_exclusive

DEFAULT_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--requests", type=int, default=16)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise SystemExit(
            "For safety this teaching client only connects to a loopback HTTP endpoint."
        )
    if args.requests < 1 or args.concurrency < 1:
        raise SystemExit("--requests and --concurrency must be positive")
    run_id = resolve_run_id()

    endpoint = args.base_url.rstrip("/") + "/v1/completions"

    def send(index: int) -> tuple[float, int]:
        payload = json.dumps(
            {
                "model": args.model,
                "prompt": f"Request {index}: explain one GPU benchmarking rule in one sentence.",
                "max_tokens": 32,
                "temperature": 0,
            }
        ).encode()
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read())
        latency = time.perf_counter() - started
        return latency, int(body.get("usage", {}).get("completion_tokens", 0))

    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        results = list(executor.map(send, range(args.requests)))
    wall_seconds = time.perf_counter() - wall_started
    latencies = sorted(latency for latency, _ in results)
    completion_tokens = sum(tokens for _, tokens in results)
    all_responses_nonempty = all(tokens > 0 for _, tokens in results)
    if len(results) != args.requests or not all_responses_nonempty:
        raise SystemExit("At least one serving response had no generated tokens.")
    p90 = latencies[max(0, math.ceil(0.9 * len(latencies)) - 1)]
    payload = {
        "schema": "gpu-course-result/v1",
        "lab_id": "11_serving_client",
        "run_id": run_id,
        "measurements": {
            "model": args.model,
            "revision": args.revision,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "median_e2e_ms": round(statistics.median(latencies) * 1_000, 3),
            "p90_e2e_ms": round(p90 * 1_000, 3),
            "request_throughput_per_second": round(args.requests / wall_seconds, 3),
            "output_tokens_per_second": round(completion_tokens / wall_seconds, 3),
        },
        "correctness": {
            "all_requests_completed": True,
            "all_responses_nonempty": True,
        },
    }
    output = args.output or Path(f"results/11_serving_client-run-{run_id}.json")
    write_json_exclusive(output, payload)
    print(f"Completed local serving benchmark: {output}")


if __name__ == "__main__":
    main()
