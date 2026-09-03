"""Measure prefill and token-by-token decode with a real Hugging Face model."""

from __future__ import annotations

import argparse
from typing import NamedTuple

from common import (
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    add_common_args,
    load_torch,
    require_h100,
    resolve_int_override,
    summarize_ms,
    validate_common_args,
    write_result,
)


class GenerationStep(NamedTuple):
    phase: str
    input_tokens: int
    attention_tokens: int
    cache_start: int


def generation_schedule(*, prompt_tokens: int, new_tokens: int) -> list[GenerationStep]:
    if prompt_tokens < 1:
        raise ValueError("prompt_tokens must be positive")
    if new_tokens < 1:
        raise ValueError("new_tokens must be positive")
    steps = [
        GenerationStep(
            phase="prefill",
            input_tokens=prompt_tokens,
            attention_tokens=prompt_tokens,
            cache_start=0,
        )
    ]
    steps.extend(
        GenerationStep(
            phase="decode",
            input_tokens=1,
            attention_tokens=prompt_tokens + generated_index,
            cache_start=prompt_tokens + generated_index - 1,
        )
        for generated_index in range(1, new_tokens)
    )
    return steps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--new-tokens", type=int, default=None)
    args = parser.parse_args()
    validate_common_args(args)
    new_tokens = resolve_int_override(
        args.new_tokens,
        32 if args.profile == "smoke" else 128,
        option="--new-tokens",
    )
    torch = load_torch()
    environment = require_h100(torch)
    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer, DynamicCache
    except ImportError as exc:
        raise SystemExit("Install the pinned training extras: transformers.") from exc

    tokenizer = AutoTokenizer.from_pretrained(
        args.model, revision=args.revision, trust_remote_code=False
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
    base_prompt = "Explain in two sentences why GPU benchmarks need warm-up iterations."
    prompt = base_prompt if args.profile == "smoke" else " ".join([base_prompt] * 64)
    tokenized = tokenizer(prompt, return_tensors="pt").to("cuda")
    prompt_tokens = int(tokenized.input_ids.shape[1])
    schedule = generation_schedule(prompt_tokens=prompt_tokens, new_tokens=new_tokens)
    prefill_samples: list[float] = []
    decode_samples: list[float] = []

    def run_request(*, record_timings: bool) -> int:
        cache = DynamicCache(config=model.config)
        inputs = {
            "input_ids": tokenized.input_ids,
            "attention_mask": tokenized.attention_mask,
        }
        generated = []
        for step_index, step in enumerate(schedule):
            if int(inputs["input_ids"].shape[1]) != step.input_tokens:
                raise SystemExit("Generation input length diverged from its schedule.")
            if int(inputs["attention_mask"].shape[1]) != step.attention_tokens:
                raise SystemExit("Attention-mask length diverged from its schedule.")
            cache_position = torch.arange(
                step.cache_start,
                step.cache_start + step.input_tokens,
                dtype=torch.int64,
                device="cuda",
            )
            start = torch.cuda.Event(enable_timing=True) if record_timings else None
            end = torch.cuda.Event(enable_timing=True) if record_timings else None
            if start is not None:
                start.record()
            output = model(
                **inputs,
                cache_position=cache_position,
                past_key_values=cache,
                use_cache=True,
            )
            if end is not None:
                end.record()
                end.synchronize()
                sample = float(start.elapsed_time(end))
                if step.phase == "prefill":
                    prefill_samples.append(sample)
                else:
                    decode_samples.append(sample)
            cache = output.past_key_values
            if cache is None or not hasattr(cache, "get_seq_length"):
                raise SystemExit("The model did not return a supported cache object.")
            expected_cache_tokens = step.cache_start + step.input_tokens
            if int(cache.get_seq_length()) != expected_cache_tokens:
                raise SystemExit("Cache length diverged from the generation schedule.")
            next_token = output.logits[:, -1:].argmax(dim=-1)
            generated.append(next_token)
            if step_index + 1 < len(schedule):
                attention_mask = torch.cat(
                    [
                        inputs["attention_mask"],
                        inputs["attention_mask"].new_ones(
                            (inputs["attention_mask"].shape[0], 1)
                        ),
                    ],
                    dim=-1,
                )
                inputs = {
                    "input_ids": next_token,
                    "attention_mask": attention_mask,
                }
        generated_ids = torch.cat(generated, dim=-1)
        return int(generated_ids.shape[1])

    with torch.inference_mode():
        for _ in range(args.warmup):
            run_request(record_timings=False)
        torch.cuda.synchronize()
        generated_token_counts = []
        for _ in range(args.iterations):
            generated_token_counts.append(run_request(record_timings=True))
    generated_requested_token_count = all(
        count == new_tokens for count in generated_token_counts
    )
    if not generated_requested_token_count:
        raise SystemExit("Generated token count diverged from --new-tokens.")
    target = write_result(
        args,
        lab_id="09_hf_prefill_decode",
        environment=environment,
        measurements={
            "model": args.model,
            "revision": args.revision,
            "prompt_tokens": prompt_tokens,
            "requested_new_tokens_per_request": new_tokens,
            "generated_new_tokens_per_request": new_tokens,
            "decode_steps_per_request": new_tokens - 1,
            "total_generated_tokens": sum(generated_token_counts),
            "output_shape": [int(tokenized.input_ids.shape[0]), new_tokens],
            "requests": args.iterations,
            "prefill_timing": summarize_ms(prefill_samples),
            "decode_timing": (summarize_ms(decode_samples) if decode_samples else None),
        },
        correctness={
            "generated_requested_token_count": True,
            "attention_mask_advanced": True,
            "cache_position_advanced": True,
            "cache_length_advanced": True,
        },
    )
    print(f"Completed prefill/decode measurement: {target}")


if __name__ == "__main__":
    main()
