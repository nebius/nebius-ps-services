"""Hash private greedy outputs before accepting a serving-policy comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

from common import require_hf_commit_revision, resolve_run_id, write_json_exclusive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument(
        "--workload", choices=("prefix-cache", "chunked-prefill"), required=True
    )
    parser.add_argument("--variant", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require_hf_commit_revision(args.revision)
    parsed = urllib.parse.urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise SystemExit("This client only connects to a loopback HTTP endpoint.")
    if args.workload == "prefix-cache":
        shared = "A controlled cache comparison preserves exact tokens. " * 96
        prompts = [f"{shared} Case {index}." for index in range(4)]
    else:
        shared = "A long prompt exercises bounded prefill scheduling. " * 128
        prompts = [f"{shared} Case {index}." for index in range(4)]
    endpoint = args.base_url.rstrip("/") + "/v1/completions"
    digests = []
    for prompt in prompts:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(
                {
                    "model": args.model,
                    "prompt": prompt,
                    "max_tokens": 32,
                    "temperature": 0,
                    "seed": 17,
                }
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read())
        choices = body.get("choices", [])
        if not choices or not choices[0].get("text"):
            raise SystemExit("A greedy equivalence request returned no text.")
        digests.append(hashlib.sha256(choices[0]["text"].encode()).hexdigest())
    write_json_exclusive(
        args.output,
        {
            "schema": "gpu-course-result/v1",
            "lab_id": "34_policy_equivalence_client",
            "run_id": resolve_run_id(),
            "measurements": {
                "model": args.model,
                "revision": args.revision,
                "workload": args.workload,
                "variant": args.variant,
                "response_digests": digests,
                "response_text_published": False,
            },
            "correctness": {"all_greedy_responses_nonempty": True},
        },
    )
    print(f"Wrote private {args.workload}/{args.variant} output digests: {args.output}")


if __name__ == "__main__":
    main()
