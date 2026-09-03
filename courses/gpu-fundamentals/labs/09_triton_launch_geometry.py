"""Write and sweep a small Python Triton kernel to expose launch geometry."""

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

try:
    import triton
    import triton.language as tl
except ImportError:
    triton = None
    tl = None


if triton is not None and tl is not None:

    @triton.jit
    def scale_kernel(
        source,
        target,
        element_count,
        scale,
        BLOCK_SIZE: tl.constexpr,
    ):
        program = tl.program_id(axis=0)
        offsets = program * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
        mask = offsets < element_count
        values = tl.load(source + offsets, mask=mask)
        tl.store(target + offsets, values * scale, mask=mask)
else:
    scale_kernel = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    if triton is None or scale_kernel is None:
        raise SystemExit(
            "This lab needs the Triton package supplied with a compatible "
            "Linux CUDA PyTorch environment."
        )

    element_count = 8_000_003 if args.profile == "smoke" else 64_000_003
    source = torch.randn(element_count, device="cuda", dtype=torch.float32)
    target = torch.empty_like(source)
    reference = source * 1.25
    measurements: dict[str, object] = {"elements": element_count, "sweeps": {}}
    correctness: dict[str, bool] = {}
    warps_per_program = 4
    for block_size in (128, 256, 512):
        grid = (triton.cdiv(element_count, block_size),)

        def launch() -> None:
            scale_kernel[grid](
                source,
                target,
                element_count,
                1.25,
                BLOCK_SIZE=block_size,
                num_warps=warps_per_program,
            )

        samples = cuda_times_ms(
            torch,
            launch,
            warmup=args.warmup,
            iterations=args.iterations,
        )
        launch()
        torch.cuda.synchronize()
        correct = bool(torch.equal(target, reference))
        if not correct:
            raise SystemExit(f"BLOCK_SIZE={block_size} produced an incorrect result.")
        summary = summarize_ms(samples)
        median_seconds = summary["median_ms"] / 1_000
        logical_bytes = 2 * element_count * source.element_size()
        measurements["sweeps"][str(block_size)] = {
            "programs": triton.cdiv(element_count, block_size),
            "elements_per_program": block_size,
            "covered_element_lanes": triton.cdiv(element_count, block_size)
            * block_size,
            "masked_tail_elements": (
                triton.cdiv(element_count, block_size) * block_size - element_count
            ),
            "warps_per_program": warps_per_program,
            "cuda_threads_per_program": warps_per_program * 32,
            "time": summary,
            "logical_gib_per_second": round(logical_bytes / median_seconds / 2**30, 3),
        }
        correctness[f"block_{block_size}_exact"] = True
    target_path = write_result(
        args,
        lab_id="09_triton_launch_geometry",
        environment=environment,
        measurements=measurements,
        correctness=correctness,
    )
    print(f"Completed Triton launch-geometry sweep: {target_path}")


if __name__ == "__main__":
    main()
