"""Compare repeated-prefix and unique-prefix requests against local vLLM."""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

from common import resolve_run_id, write_json_exclusive

DEFAULT_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", default="Qwen/Qwen2.5-0.5B-Instruct")
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--requests-per-cohort", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parsed = urllib.parse.urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise SystemExit("This teaching client only connects to loopback HTTP.")
    if args.requests_per_cohort < 2:
        raise SystemExit("--requests-per-cohort must be at least two")
    run_id = resolve_run_id()
    endpoint = args.base_url.rstrip("/") + "/v1/completions"
    shared_prefix = "GPU evidence requires a fixed workload. " * 96

    def send(prompt: str) -> tuple[float, int, int]:
        body = json.dumps(
            {
                "model": args.model,
                "prompt": prompt,
                "max_tokens": 16,
                "temperature": 0,
            }
        ).encode()
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read())
        elapsed = time.perf_counter() - started
        usage = payload.get("usage", {})
        return (
            elapsed,
            int(usage.get("prompt_tokens", 0)),
            int(usage.get("completion_tokens", 0)),
        )

    send(shared_prefix + " Warm the shared prefix.")
    repeated = [
        send(shared_prefix + f" Explain measurement rule {index}.")
        for index in range(args.requests_per_cohort)
    ]
    unique = [
        send(
            f"Unique request marker {index}; do not reuse this prefix. "
            + ("GPU evidence requires a fixed workload. " * 95)
            + f"Explain measurement rule {index}."
        )
        for index in range(args.requests_per_cohort)
    ]
    if not all(item[2] > 0 for item in repeated + unique):
        raise SystemExit("At least one request returned no generated tokens.")
    repeated_prompt_median = statistics.median(item[1] for item in repeated)
    unique_prompt_median = statistics.median(item[1] for item in unique)
    prompt_ratio = repeated_prompt_median / max(unique_prompt_median, 1)
    if not 0.8 <= prompt_ratio <= 1.2:
        raise SystemExit(
            "Repeated and unique prompt cohorts differ by more than 20% in tokens."
        )

    def cohort(values: list[tuple[float, int, int]]) -> dict[str, float]:
        return {
            "median_e2e_ms": round(
                statistics.median(item[0] for item in values) * 1_000, 3
            ),
            "median_prompt_tokens": statistics.median(item[1] for item in values),
            "completion_tokens": sum(item[2] for item in values),
        }

    payload = {
        "schema": "gpu-course-result/v1",
        "lab_id": "20_prefix_cache_client",
        "run_id": run_id,
        "measurements": {
            "model": args.model,
            "revision": args.revision,
            "requests_per_cohort": args.requests_per_cohort,
            "repeated_prefix": cohort(repeated),
            "unique_prefix": cohort(unique),
            "prompt_token_ratio": round(prompt_ratio, 4),
        },
        "correctness": {
            "all_responses_nonempty": True,
            "prompt_token_cohorts_comparable": True,
        },
    }
    write_json_exclusive(args.output, payload)
    print(f"Completed prefix-cache client experiment: {args.output}")


if __name__ == "__main__":
    main()
