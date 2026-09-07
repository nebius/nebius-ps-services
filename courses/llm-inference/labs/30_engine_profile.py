"""Probe an OpenAI-compatible inference engine and measure a bounded request."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request

from common import add_common_args, validate_common_args, write_result


def request_bytes(url: str, payload: dict[str, object] | None = None) -> bytes:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def request_json(
    url: str, payload: dict[str, object] | None = None
) -> dict[str, object]:
    body = request_bytes(url, payload)
    return json.loads(body) if body else {}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--server-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument(
        "--protocol",
        choices=("openai", "triton"),
        default="openai",
        help="Use OpenAI-compatible v1 or Triton v2 generate endpoints.",
    )
    parser.add_argument(
        "--triton-max-token-field",
        choices=("sampling_param_max_tokens", "max_tokens"),
        default=None,
        help="Match the field declared by the pinned TensorRT-LLM model repository.",
    )
    parser.add_argument(
        "--triton-repository-profile",
        choices=("llmapi", "inflight_batcher"),
        default=None,
        help="Declare the reviewed TensorRT-LLM repository schema.",
    )
    args = parser.parse_args()
    validate_common_args(args)
    if args.max_tokens < 1:
        raise SystemExit("--max-tokens must be positive")
    server_url = args.server_url.rstrip("/")
    model_count: int | None = None
    if args.protocol == "openai":
        models = request_json(f"{server_url}/v1/models")
        model_count = len(models.get("data", []))
    else:
        if (
            args.triton_repository_profile is None
            or args.triton_max_token_field is None
        ):
            raise SystemExit(
                "Triton requires an explicit repository profile and max-token field."
            )
        valid_profile = (
            args.triton_repository_profile == "llmapi"
            and args.model == "tensorrt_llm"
            and args.triton_max_token_field == "sampling_param_max_tokens"
        ) or (
            args.triton_repository_profile == "inflight_batcher"
            and args.model in {"ensemble", "tensorrt_llm_bls"}
            and args.triton_max_token_field == "max_tokens"
        )
        if not valid_profile:
            raise SystemExit(
                "Triton profile/model/token-field combination is not a supported "
                "course contract."
            )
        model_path = urllib.parse.quote(args.model, safe="")
        request_bytes(f"{server_url}/v2/health/ready")
        request_bytes(f"{server_url}/v2/models/{model_path}/ready")
    start = time.perf_counter()
    if args.protocol == "openai":
        response = request_json(
            f"{server_url}/v1/completions",
            {
                "model": args.model,
                "prompt": "Explain one benefit of GPU memory coalescing.",
                "max_tokens": args.max_tokens,
                "temperature": 0,
            },
        )
        choices = response.get("choices", [])
        response_valid = bool(
            choices and isinstance(choices[0].get("text"), str) and choices[0]["text"]
        )
    else:
        response = request_json(
            f"{server_url}/v2/models/{model_path}/generate",
            {
                "text_input": "Explain one benefit of GPU memory coalescing.",
                args.triton_max_token_field: args.max_tokens,
            },
        )
        response_valid = bool(response.get("text_output"))
    elapsed_ms = (time.perf_counter() - start) * 1000
    if not response_valid:
        raise SystemExit(f"{args.protocol} response did not contain generated text.")
    usage = response.get("usage", {})
    target = write_result(
        args,
        lab_id="30_engine_profile",
        environment={
            "client": "standard-library HTTP",
            "engine_runtime": "server-owned",
        },
        measurements={
            "protocol": args.protocol,
            "model_count": model_count,
            "triton_max_token_field": (
                args.triton_max_token_field if args.protocol == "triton" else None
            ),
            "triton_repository_profile": (
                args.triton_repository_profile if args.protocol == "triton" else None
            ),
            "elapsed_ms": round(elapsed_ms, 3),
            "usage": usage,
            "response_text_published": False,
            "trials_required_for_public_claim": 3,
        },
        correctness={"response_has_generated_text": True},
    )
    print(f"Wrote engine-client evidence: {target}")


if __name__ == "__main__":
    main()
