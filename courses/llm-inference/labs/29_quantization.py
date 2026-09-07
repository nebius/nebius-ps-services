"""Compare BF16 with symmetric INT8 weight and KV-cache mechanics."""

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
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    seed_everything(torch, args.seed)
    environment = require_h100(torch)
    width = 512 if args.profile == "smoke" else 4096
    x = torch.randn((64, width), device="cuda", dtype=torch.bfloat16)
    weight = torch.randn((width, width), device="cuda", dtype=torch.bfloat16)
    weight_scale = weight.abs().max().clamp_min(1e-12) / 127
    quantized_weight = torch.clamp((weight / weight_scale).round(), -127, 127).to(
        torch.int8
    )
    layers = 8 if args.profile == "smoke" else 32
    sequence = 512 if args.profile == "smoke" else 2_048
    kv = torch.randn((layers, 2, 8, sequence, 64), device="cuda", dtype=torch.bfloat16)
    kv_scale = kv.abs().max().clamp_min(1e-12) / 127
    quantized_kv = torch.clamp((kv / kv_scale).round(), -127, 127).to(torch.int8)

    def baseline() -> object:
        return x @ weight

    def candidate() -> object:
        return x @ (quantized_weight.to(torch.bfloat16) * weight_scale)

    def kv_dequantize() -> object:
        return quantized_kv.to(torch.bfloat16) * kv_scale

    reference = baseline()
    observed = candidate()
    relative_l2 = float(
        (observed.float() - reference.float()).norm() / reference.float().norm()
    )
    kv_observed = kv_dequantize()
    kv_relative_l2 = float(
        (kv_observed.float() - kv.float()).norm() / kv.float().norm()
    )
    if relative_l2 >= 0.02 or kv_relative_l2 >= 0.02:
        raise SystemExit("INT8 mechanics failed the declared 0.02 relative-L2 gate.")
    timing = {
        name: summarize_ms(
            cuda_times_ms(
                torch, operation, warmup=args.warmup, iterations=args.iterations
            )
        )
        for name, operation in (
            ("bf16", baseline),
            ("int8_weight_dequantize_then_matmul", candidate),
        )
    }
    kv_timing = summarize_ms(
        cuda_times_ms(
            torch,
            kv_dequantize,
            warmup=args.warmup,
            iterations=args.iterations,
        )
    )
    target = write_result(
        args,
        lab_id="29_quantization",
        environment=environment,
        measurements={
            "shape": [width, width],
            "bf16_weight_bytes": weight.numel() * weight.element_size(),
            "int8_weight_and_scale_bytes": quantized_weight.numel()
            * quantized_weight.element_size()
            + weight_scale.numel() * weight_scale.element_size(),
            "bf16_kv_bytes": kv.numel() * kv.element_size(),
            "int8_kv_and_scale_bytes": quantized_kv.numel()
            * quantized_kv.element_size()
            + kv_scale.numel() * kv_scale.element_size(),
            "weight_relative_l2_error": relative_l2,
            "kv_relative_l2_error": kv_relative_l2,
            "weight_timing": timing,
            "kv_dequantization_timing": kv_timing,
            "memory_scope": "logical payload; both BF16 and INT8 copies remain resident for this A/B",
            "kernel_claim": "mechanics only; engine-supported weight and KV kernels are required for production latency claims",
        },
        correctness={
            "finite": bool(torch.isfinite(observed).all()),
            "weight_quality_gate": relative_l2 < 0.02,
            "kv_quality_gate": kv_relative_l2 < 0.02,
        },
    )
    print(f"Wrote quantization evidence: {target}")


if __name__ == "__main__":
    main()
