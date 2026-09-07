"""Record private greedy-output digests from a loopback serving-engine variant."""

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
    parser.add_argument("--target-revision", required=True)
    parser.add_argument("--draft-model", required=True)
    parser.add_argument("--draft-revision", required=True)
    parser.add_argument(
        "--variant", choices=("target-only", "speculative"), required=True
    )
    parser.add_argument("--requests", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require_hf_commit_revision(args.target_revision, option="--target-revision")
    require_hf_commit_revision(args.draft_revision, option="--draft-revision")
    parsed = urllib.parse.urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise SystemExit("This client only connects to a loopback HTTP endpoint.")
    if args.requests < 1:
        raise SystemExit("--requests must be positive")
    endpoint = args.base_url.rstrip("/") + "/v1/completions"
    response_digests = []
    for index in range(args.requests):
        payload = json.dumps(
            {
                "model": args.model,
                "prompt": (
                    f"Case {index}: explain one controlled GPU benchmark rule "
                    "in exactly two short sentences."
                ),
                "max_tokens": 64,
                "temperature": 0,
                "seed": 17,
            }
        ).encode()
        request = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.loads(response.read())
        choices = body.get("choices", [])
        if not choices or not choices[0].get("text"):
            raise SystemExit("A greedy request returned no generated text.")
        response_digests.append(
            hashlib.sha256(choices[0]["text"].encode("utf-8")).hexdigest()
        )
    result = {
        "schema": "gpu-course-result/v1",
        "lab_id": "33_speculative_engine_client",
        "run_id": resolve_run_id(),
        "measurements": {
            "variant": args.variant,
            "model": args.model,
            "target_revision": args.target_revision,
            "draft_model": args.draft_model,
            "draft_revision": args.draft_revision,
            "requests": args.requests,
            "response_digests": response_digests,
            "response_text_published": False,
        },
        "correctness": {
            "all_responses_nonempty": True,
            "greedy_equivalence_requires_launcher_pair_comparison": True,
        },
    }
    write_json_exclusive(args.output, result)
    print(f"Wrote private {args.variant} output digests: {args.output}")


if __name__ == "__main__":
    main()
