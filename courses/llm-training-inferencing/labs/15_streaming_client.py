"""Measure TTFT, inter-chunk gaps, and E2E latency from a loopback stream."""

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


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--requests", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise SystemExit("This teaching client only connects to loopback HTTP.")
    if args.requests < 1 or args.concurrency < 1:
        raise SystemExit("--requests and --concurrency must be positive")
    run_id = resolve_run_id()
    endpoint = args.base_url.rstrip("/") + "/v1/completions"

    def send(index: int) -> dict[str, object]:
        payload = json.dumps(
            {
                "model": args.model,
                "prompt": f"Request {index}: give two rules for a valid GPU benchmark.",
                "max_tokens": 48,
                "temperature": 0,
                "stream": True,
            }
        ).encode()
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        first_content = None
        prior_content = None
        gaps = []
        characters = 0
        with urllib.request.urlopen(request, timeout=120) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                chunk = json.loads(line[6:])
                text = chunk.get("choices", [{}])[0].get("text", "")
                if not text:
                    continue
                now = time.perf_counter()
                if first_content is None:
                    first_content = now
                if prior_content is not None:
                    gaps.append(now - prior_content)
                prior_content = now
                characters += len(text)
        finished = time.perf_counter()
        if first_content is None:
            raise RuntimeError("The stream completed without a content chunk.")
        return {
            "ttft_s": first_content - started,
            "e2e_s": finished - started,
            "inter_chunk_gaps_s": gaps,
            "characters": characters,
        }

    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        results = list(executor.map(send, range(args.requests)))
    wall_seconds = time.perf_counter() - wall_started
    ttft = [float(result["ttft_s"]) for result in results]
    e2e = [float(result["e2e_s"]) for result in results]
    gaps = [float(gap) for result in results for gap in result["inter_chunk_gaps_s"]]
    payload = {
        "schema": "gpu-course-result/v1",
        "lab_id": "15_streaming_client",
        "run_id": run_id,
        "measurements": {
            "model": args.model,
            "revision": args.revision,
            "requests": args.requests,
            "concurrency": args.concurrency,
            "median_ttft_ms": round(statistics.median(ttft) * 1_000, 3),
            "p90_ttft_ms": round(percentile(ttft, 0.9) * 1_000, 3),
            "median_e2e_ms": round(statistics.median(e2e) * 1_000, 3),
            "p90_e2e_ms": round(percentile(e2e, 0.9) * 1_000, 3),
            "median_inter_chunk_gap_ms": (
                round(statistics.median(gaps) * 1_000, 3) if gaps else None
            ),
            "request_throughput_per_second": round(args.requests / wall_seconds, 3),
            "received_characters": sum(int(result["characters"]) for result in results),
        },
        "correctness": {
            "all_streams_produced_content": len(results) == args.requests,
            "note": "An HTTP chunk is not guaranteed to equal one model token.",
        },
    }
    output = args.output or Path(f"results/15_streaming_client-run-{run_id}.json")
    write_json_exclusive(output, payload)
    print(f"Completed streaming benchmark: {output}")


if __name__ == "__main__":
    main()
