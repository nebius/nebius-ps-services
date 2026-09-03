"""Run batched offline generation in the isolated vLLM serving environment."""

from __future__ import annotations

import argparse
import statistics
import time

from common import (
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


def aggregate_outputs(outputs: list[object], *, max_tokens: int) -> dict[str, object]:
    prompt_token_counts = []
    output_token_counts = []
    finish_reasons = []
    for output in outputs:
        prompt_token_ids = output.prompt_token_ids
        if prompt_token_ids is None or not prompt_token_ids:
            raise ValueError("Every request must expose a positive prompt token count.")
        if len(output.outputs) != 1:
            raise ValueError("Every request must contain exactly one completion.")
        completion = output.outputs[0]
        output_tokens = len(completion.token_ids)
        if output_tokens < 1 or output_tokens > max_tokens:
            raise ValueError(
                "Every request must contain a positive bounded output token count."
            )
        prompt_token_counts.append(len(prompt_token_ids))
        output_token_counts.append(output_tokens)
        finish_reasons.append(completion.finish_reason)
    return {
        "request_count": len(outputs),
        "prompt_tokens_total": sum(prompt_token_counts),
        "output_tokens_total": sum(output_token_counts),
        "prompt_tokens_per_request": prompt_token_counts,
        "output_tokens_per_request": output_token_counts,
        "finish_reasons": finish_reasons,
    }


def summarize_counts(values: list[int]) -> dict[str, int | float]:
    return {
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--max-model-len", type=int, default=2_048)
    parser.add_argument("--enforce-eager", action="store_true")
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    try:
        from vllm import LLM, SamplingParams
    except ImportError as exc:
        raise SystemExit(
            "Activate the isolated serving environment containing the pinned vLLM wheel."
        ) from exc

    base_prompts = [
        "Explain why CUDA events are useful for timing GPU kernels.",
        "Name one symptom of a memory-bound GPU workload.",
        "Explain the difference between LLM prefill and decode.",
        "Why should inference benchmarks report latency percentiles?",
    ]
    copies = 1 if args.profile == "smoke" else 4
    prompts = [
        f"{prompt} Example request {index}."
        for index, prompt in enumerate(base_prompts * copies, start=1)
    ]
    engine = LLM(
        model=args.model,
        revision=args.revision,
        tokenizer_revision=args.revision,
        dtype="bfloat16",
        max_model_len=args.max_model_len,
        gpu_memory_utilization=0.80,
        trust_remote_code=False,
        enforce_eager=args.enforce_eager,
    )
    max_tokens = 64
    sampling = SamplingParams(temperature=0.0, max_tokens=max_tokens)
    for _ in range(args.warmup):
        engine.generate(prompts, sampling)
    elapsed = 0.0
    prompt_token_counts: list[int] = []
    output_token_counts: list[int] = []
    finish_reasons: list[str | None] = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        outputs = engine.generate(prompts, sampling)
        elapsed += time.perf_counter() - started
        if len(outputs) != len(prompts):
            raise SystemExit(
                "vLLM returned a different number of results than prompts."
            )
        try:
            aggregate = aggregate_outputs(outputs, max_tokens=max_tokens)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        prompt_token_counts.extend(aggregate["prompt_tokens_per_request"])
        output_token_counts.extend(aggregate["output_tokens_per_request"])
        finish_reasons.extend(aggregate["finish_reasons"])
    requests = len(prompts) * args.iterations
    prompt_tokens_total = sum(prompt_token_counts)
    output_tokens_total = sum(output_token_counts)
    target = write_result(
        args,
        lab_id="10_vllm_offline",
        environment=environment,
        measurements={
            "model": args.model,
            "revision": args.revision,
            "requests": requests,
            "prompt_tokens_total": prompt_tokens_total,
            "output_tokens_total": output_tokens_total,
            "prompt_tokens_per_request": summarize_counts(prompt_token_counts),
            "output_tokens_per_request": summarize_counts(output_token_counts),
            "finish_reasons": finish_reasons,
            "elapsed_seconds": round(elapsed, 4),
            "output_tokens_per_second": round(output_tokens_total / elapsed, 2),
        },
        correctness={
            "one_output_per_prompt": True,
            "one_completion_per_request": True,
            "positive_prompt_token_counts": prompt_tokens_total > 0,
            "bounded_positive_output_token_counts": output_tokens_total > 0,
            "all_iterations_completed": len(output_token_counts) == requests,
        },
    )
    print(f"Completed vLLM offline inference: {target}")


if __name__ == "__main__":
    main()
