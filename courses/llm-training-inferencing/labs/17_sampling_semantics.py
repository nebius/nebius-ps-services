"""Compare greedy and seeded stochastic generation under fixed token budgets."""

from __future__ import annotations

import argparse
import time
from typing import Any

from common import (
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
    validate_common_args,
    write_result,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--max-new-tokens", type=int, default=32)
    args = parser.parse_args()
    validate_common_args(args)
    if args.max_new_tokens < 1:
        raise SystemExit("--max-new-tokens must be positive")
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit("Install the pinned training environment.") from exc

    tokenizer = AutoTokenizer.from_pretrained(
        args.model,
        revision=args.revision,
        trust_remote_code=False,
    )
    model = (
        AutoModelForCausalLM.from_pretrained(
            args.model,
            revision=args.revision,
            dtype=torch.bfloat16,
            attn_implementation="sdpa",
            trust_remote_code=False,
        )
        .to("cuda")
        .eval()
    )
    encoded = tokenizer(
        "Explain one reason to record output-token counts in a benchmark.",
        return_tensors="pt",
    ).to("cuda")
    prompt_tokens = int(encoded.input_ids.shape[1])

    def generate(**settings: Any) -> tuple[list[int], float]:
        torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            output = model.generate(
                **encoded,
                max_new_tokens=args.max_new_tokens,
                pad_token_id=tokenizer.eos_token_id,
                **settings,
            )
        torch.cuda.synchronize()
        return output[0, prompt_tokens:].tolist(), time.perf_counter() - started

    for _ in range(args.warmup):
        generate(do_sample=False)
    greedy_a, greedy_seconds = generate(do_sample=False)
    greedy_b, _ = generate(do_sample=False)
    seed_everything(torch, args.seed)
    sampled_a, sampled_seconds = generate(
        do_sample=True,
        temperature=0.7,
        top_k=40,
        top_p=0.9,
    )
    seed_everything(torch, args.seed)
    sampled_b, _ = generate(
        do_sample=True,
        temperature=0.7,
        top_k=40,
        top_p=0.9,
    )
    greedy_repeatable = greedy_a == greedy_b
    seeded_sampling_repeatable = sampled_a == sampled_b
    nonempty = bool(greedy_a and sampled_a)
    if not (greedy_repeatable and seeded_sampling_repeatable and nonempty):
        raise SystemExit("Generation repeatability or non-empty output gate failed.")
    target = write_result(
        args,
        lab_id="17_sampling_semantics",
        environment=environment,
        measurements={
            "model": args.model,
            "revision": args.revision,
            "prompt_tokens": prompt_tokens,
            "maximum_new_tokens": args.max_new_tokens,
            "greedy_output_tokens": len(greedy_a),
            "seeded_sample_output_tokens": len(sampled_a),
            "greedy_seconds": round(greedy_seconds, 4),
            "seeded_sample_seconds": round(sampled_seconds, 4),
            "greedy_token_ids": greedy_a,
            "seeded_sample_token_ids": sampled_a,
        },
        correctness={
            "greedy_repeatable": greedy_repeatable,
            "seeded_sampling_repeatable": seeded_sampling_repeatable,
            "both_outputs_nonempty": nonempty,
        },
    )
    print(f"Completed sampling-semantics experiment: {target}")


if __name__ == "__main__":
    main()
