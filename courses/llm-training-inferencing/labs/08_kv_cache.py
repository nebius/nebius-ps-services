"""Measure cached decoding and prove that the cache contains keys and values."""

from __future__ import annotations

import argparse

from common import (
    add_common_args,
    cuda_times_ms,
    load_torch,
    require_h100,
    seed_everything,
    summarize_ms,
    validate_common_args,
    write_result,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.set_defaults(warmup=1, iterations=3)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)

    batch, heads, head_dim = 1, 16, 64
    prompt, generated = (256, 64) if args.profile == "smoke" else (2_048, 256)
    hidden = heads * head_dim
    qkv = torch.nn.Linear(
        hidden, 3 * hidden, bias=False, device="cuda", dtype=torch.bfloat16
    )
    values = torch.randn(
        (batch, prompt + generated, hidden), device="cuda", dtype=torch.bfloat16
    )

    def project(tokens: object) -> tuple[object, object, object]:
        projected = qkv(tokens).view(batch, tokens.shape[1], 3, heads, head_dim)
        query, key, value = projected.unbind(dim=2)
        return query.transpose(1, 2), key.transpose(1, 2), value.transpose(1, 2)

    def full(prefix_length: int) -> object:
        query, key, value = project(values[:, :prefix_length])
        return torch.nn.functional.scaled_dot_product_attention(
            query, key, value, is_causal=True
        )[:, :, -1]

    def build_prompt_cache() -> tuple[object, object, object]:
        query, key, value = project(values[:, :prompt])
        output = torch.nn.functional.scaled_dot_product_attention(
            query, key, value, is_causal=True
        )[:, :, -1]
        return output, key, value

    full_prompt = full(prompt)
    cached_prompt, key_cache, value_cache = build_prompt_cache()
    max_error = float((full_prompt - cached_prompt).abs().max())
    if max_error > 2e-2:
        raise SystemExit(f"Cached and full prompt outputs diverged: {max_error:.6f}")

    def recompute_decode() -> object:
        output = None
        for step in range(generated):
            output = full(prompt + step + 1)
        return output

    def cached_decode() -> object:
        key = key_cache
        value = value_cache
        output = None
        for step in range(generated):
            query, next_key, next_value = project(
                values[:, prompt + step : prompt + step + 1]
            )
            key = torch.cat((key, next_key), dim=2)
            value = torch.cat((value, next_value), dim=2)
            output = torch.nn.functional.scaled_dot_product_attention(
                query, key, value, is_causal=False
            )
        return output

    recomputed_final = recompute_decode()
    cached_final = cached_decode().squeeze(2)
    decode_error = float((recomputed_final - cached_final).abs().max())
    if decode_error > 2e-2:
        raise SystemExit(
            f"Cached and recomputed decode outputs diverged: {decode_error:.6f}"
        )
    recompute = summarize_ms(
        cuda_times_ms(
            torch,
            recompute_decode,
            warmup=args.warmup,
            iterations=args.iterations,
        )
    )
    cached = summarize_ms(
        cuda_times_ms(
            torch,
            cached_decode,
            warmup=args.warmup,
            iterations=args.iterations,
        )
    )
    cache_bytes = 2 * batch * heads * (prompt + generated) * head_dim * 2
    target = write_result(
        args,
        lab_id="08_kv_cache",
        environment=environment,
        measurements={
            "prompt_tokens": prompt,
            "generated_tokens": generated,
            "recompute_decode": recompute,
            "cached_decode": cached,
            "key_value_cache_mib": round(cache_bytes / 2**20, 3),
            "prompt_max_absolute_error": round(max_error, 7),
            "decode_max_absolute_error": round(decode_error, 7),
        },
        correctness={
            "cached_prompt_matches_full_attention": True,
            "cached_decode_matches_recomputation": True,
        },
    )
    print(f"Completed KV-cache experiment: {target}")


if __name__ == "__main__":
    main()
