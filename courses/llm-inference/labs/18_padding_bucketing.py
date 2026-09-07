"""Compare one padded prefill batch with length-bucketed equivalent prompts."""

from __future__ import annotations

import argparse
import math

from common import (
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    add_common_args,
    cuda_times_ms,
    load_torch,
    require_h100,
    require_hf_commit_revision,
    seed_everything,
    summarize_ms,
    validate_common_args,
    write_result,
)


def validate_last_token_logits(
    torch: object,
    padded_logits: object,
    bucket_last_logits: dict[int, object],
    lengths: list[int],
) -> float:
    """Check every prompt before aggregating the last-token relative error."""
    errors = []
    for index, length in enumerate(lengths):
        padded_last = padded_logits[index, length - 1].float()
        bucket_last = bucket_last_logits[index].float()
        if not bool(
            torch.isfinite(padded_last).all() and torch.isfinite(bucket_last).all()
        ):
            raise SystemExit(f"Prompt {index} logits must be finite.")
        numerator = torch.linalg.vector_norm(padded_last - bucket_last)
        denominator = torch.linalg.vector_norm(padded_last).clamp_min(1e-12)
        if not bool(torch.isfinite(numerator) and torch.isfinite(denominator)):
            raise SystemExit(f"Prompt {index} logit norms must be finite.")
        error = float((numerator / denominator).item())
        if not math.isfinite(error) or error > 0.02:
            raise SystemExit(
                f"Equivalent prompt {index} logits diverged: relative L2={error}"
            )
        errors.append(error)
    return max(errors)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    args = parser.parse_args()
    validate_common_args(args)
    require_hf_commit_revision(args.revision)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit(
            "Activate the pinned inference mechanics environment from "
            "requirements-mechanics.txt."
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(
        args.model,
        revision=args.revision,
        trust_remote_code=False,
    )
    tokenizer.padding_side = "right"
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
    base = "Explain why equivalent GPU benchmarks preserve token counts."
    repeats = (1, 4, 12, 32) if args.profile == "smoke" else (8, 32, 96, 192)
    prompts = [" ".join([base] * repeat) for repeat in repeats]
    lengths = [
        len(tokenizer(prompt, add_special_tokens=True).input_ids) for prompt in prompts
    ]
    padded = tokenizer(prompts, padding=True, return_tensors="pt").to("cuda")
    order = sorted(range(len(prompts)), key=lengths.__getitem__)
    buckets = (order[:2], order[2:])
    bucket_inputs = [
        tokenizer(
            [prompts[index] for index in bucket],
            padding=True,
            return_tensors="pt",
        ).to("cuda")
        for bucket in buckets
    ]

    def padded_prefill() -> None:
        with torch.inference_mode():
            model(**padded, use_cache=False)

    def bucketed_prefill() -> None:
        with torch.inference_mode():
            for inputs in bucket_inputs:
                model(**inputs, use_cache=False)

    padded_samples = cuda_times_ms(
        torch,
        padded_prefill,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    bucket_samples = cuda_times_ms(
        torch,
        bucketed_prefill,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    with torch.inference_mode():
        padded_logits = model(**padded, use_cache=False).logits
        bucket_last_logits: dict[int, object] = {}
        for bucket, inputs in zip(buckets, bucket_inputs, strict=True):
            logits = model(**inputs, use_cache=False).logits
            local_lengths = inputs.attention_mask.sum(dim=1)
            for local_index, original_index in enumerate(bucket):
                bucket_last_logits[original_index] = logits[
                    local_index, int(local_lengths[local_index].item()) - 1
                ]
    max_relative_l2 = validate_last_token_logits(
        torch, padded_logits, bucket_last_logits, lengths
    )
    true_tokens = sum(lengths)
    padded_tokens = int(padded.input_ids.numel())
    bucket_tokens = sum(int(inputs.input_ids.numel()) for inputs in bucket_inputs)
    target = write_result(
        args,
        lab_id="18_padding_bucketing",
        environment=environment,
        measurements={
            "model": args.model,
            "revision": args.revision,
            "prompt_token_lengths": lengths,
            "true_prompt_tokens": true_tokens,
            "single_padded_rectangle_tokens": padded_tokens,
            "bucketed_rectangle_tokens": bucket_tokens,
            "single_batch_padding_tokens": padded_tokens - true_tokens,
            "bucketed_padding_tokens": bucket_tokens - true_tokens,
            "single_padded_batch": summarize_ms(padded_samples),
            "two_length_buckets": summarize_ms(bucket_samples),
            "maximum_last_logit_relative_l2": round(max_relative_l2, 8),
        },
        correctness={"equivalent_last_token_logits": True},
    )
    print(f"Completed padding/bucketing experiment: {target}")


if __name__ == "__main__":
    main()
