"""Compare balanced and skewed concurrent GPU task sets with approximately matched work."""

from __future__ import annotations

import argparse
import math
import statistics

from common import (
    add_common_args,
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
    def uniform_tail_probe(output, work: tl.constexpr):
        program = tl.program_id(axis=0)
        value = program.to(tl.float32) * 0.0001
        for iteration in tl.range(0, work):
            value = value * 1.000001 + (iteration + 1) * 0.0000001
        tl.store(output + program, value)
else:
    uniform_tail_probe = None


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[
        max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    ]


def distribution_ms(values: list[float]) -> dict[str, float]:
    return {
        "min_ms": round(min(values), 4),
        "p50_ms": round(statistics.median(values), 4),
        "p90_ms": round(percentile(values, 0.9), 4),
        "p99_ms": round(percentile(values, 0.99), 4),
        "max_ms": round(max(values), 4),
    }


def wave_model(blocks: int, resident_slots: int) -> dict[str, float | int]:
    waves = (blocks + resident_slots - 1) // resident_slots
    tail_blocks = blocks - (waves - 1) * resident_slots
    return {
        "blocks": blocks,
        "waves": waves,
        "tail_blocks": tail_blocks,
        "tail_utilization": round(tail_blocks / resident_slots, 4),
    }


def tail_reference(torch: object, blocks: int, work: int) -> object:
    """Evaluate the Triton program's per-program result on the CPU in FP32."""
    values = torch.arange(blocks, dtype=torch.float32) * 0.0001
    for iteration in range(work):
        values = values * 1.000001 + (iteration + 1) * 0.0000001
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    if triton is None or uniform_tail_probe is None:
        raise SystemExit("This lab requires the environment's PyTorch Triton compiler.")
    seed_everything(torch, args.seed)
    base = 256 if args.profile == "smoke" else 1_024
    balanced_widths = [base] * 8
    skewed_widths = [3 * base // 2] + [7 * base // 8] * 7

    def build_case(widths: list[int]) -> tuple[list[object], list[object]]:
        left = [
            torch.randn((width, width), device="cuda", dtype=torch.bfloat16)
            for width in widths
        ]
        right = [torch.randn_like(value) for value in left]
        return left, right

    def measure(widths: list[int]) -> dict[str, object]:
        left, right = build_case(widths)
        streams = [torch.cuda.Stream() for _ in widths]

        def run_once() -> tuple[float, list[float], list[object]]:
            start = torch.cuda.Event(enable_timing=True)
            finish = torch.cuda.Event(enable_timing=True)
            begins = [torch.cuda.Event(enable_timing=True) for _ in widths]
            ends = [torch.cuda.Event(enable_timing=True) for _ in widths]
            current = torch.cuda.current_stream()
            start.record(current)
            outputs = []
            for stream, begin, end, lhs, rhs in zip(
                streams, begins, ends, left, right, strict=True
            ):
                stream.wait_event(start)
                with torch.cuda.stream(stream):
                    begin.record(stream)
                    outputs.append(lhs @ rhs)
                    end.record(stream)
            for end in ends:
                current.wait_event(end)
            finish.record(current)
            finish.synchronize()
            return (
                float(start.elapsed_time(finish)),
                [
                    float(begin.elapsed_time(end))
                    for begin, end in zip(begins, ends, strict=True)
                ],
                outputs,
            )

        for _ in range(args.warmup):
            run_once()
        makespans: list[float] = []
        task_samples: list[float] = []
        outputs: list[object] = []
        for _ in range(args.iterations):
            makespan, tasks, outputs = run_once()
            makespans.append(makespan)
            task_samples.extend(tasks)
        finite = all(bool(torch.isfinite(output).all()) for output in outputs)
        if not finite:
            raise SystemExit(
                "A scheduled matrix multiplication produced non-finite output."
            )
        return {
            "widths": widths,
            "modeled_gemm_flops": 2 * sum(width**3 for width in widths),
            "makespan": summarize_ms(makespans),
            "task_duration": summarize_ms(task_samples),
            "makespan_distribution": distribution_ms(makespans),
            "task_duration_distribution": distribution_ms(task_samples),
            "median_task_to_max_task_ratio": round(
                statistics.median(task_samples) / max(task_samples), 4
            ),
        }

    balanced = measure(balanced_widths)
    skewed = measure(skewed_widths)
    work_ratio = skewed["modeled_gemm_flops"] / balanced["modeled_gemm_flops"]
    if abs(work_ratio - 1.0) > 0.02:
        raise SystemExit(
            "The balanced and skewed cases differ by more than two percent in "
            "modeled GEMM work; adjust the workload before interpreting timing."
        )
    properties = torch.cuda.get_device_properties(0)
    num_warps = 16
    threads = num_warps * 32
    tail_work = 256 if args.profile == "smoke" else 2_048
    compiled = uniform_tail_probe[(1,)](
        torch.empty(1, device="cuda"), work=tail_work, num_warps=num_warps
    )
    torch.cuda.synchronize()
    registers = int(getattr(compiled, "n_regs", 0))
    metadata = getattr(compiled, "metadata", None)
    shared_bytes = int(getattr(metadata, "shared", 0)) if metadata else 0
    occupancy_limits = [properties.max_threads_per_multi_processor // threads]
    register_capacity = int(getattr(properties, "regs_per_multiprocessor", 0))
    if registers and register_capacity:
        occupancy_limits.append(register_capacity // (registers * threads))
    shared_capacity = int(getattr(properties, "shared_memory_per_multiprocessor", 0))
    if shared_bytes and shared_capacity:
        occupancy_limits.append(shared_capacity // shared_bytes)
    architectural_block_limit = int(
        getattr(properties, "max_blocks_per_multi_processor", 0)
    )
    if architectural_block_limit:
        occupancy_limits.append(architectural_block_limit)
    blocks_per_sm = min(limit for limit in occupancy_limits if limit > 0)
    resident_slots = properties.multi_processor_count * blocks_per_sm
    tail_output = torch.empty(2 * resident_slots + 1, device="cuda")
    partial_wave_cases = []
    all_partial_wave_programs_correct = True
    for blocks in (
        resident_slots,
        resident_slots + 1,
        2 * resident_slots,
        2 * resident_slots + 1,
    ):

        def launch(blocks: int = blocks) -> None:
            uniform_tail_probe[(blocks,)](
                tail_output, work=tail_work, num_warps=num_warps
            )

        for _ in range(args.warmup):
            launch()
        torch.cuda.synchronize()
        samples: list[float] = []
        for _ in range(args.iterations):
            started = torch.cuda.Event(enable_timing=True)
            finished = torch.cuda.Event(enable_timing=True)
            started.record()
            launch()
            finished.record()
            finished.synchronize()
            samples.append(float(started.elapsed_time(finished)))
        tail_output.fill_(float("nan"))
        launch()
        torch.cuda.synchronize()
        observed = tail_output[:blocks].cpu()
        expected = tail_reference(torch, blocks, tail_work)
        correct = bool(
            torch.isfinite(observed).all()
            and torch.allclose(observed, expected, rtol=1e-5, atol=1e-6)
        )
        all_partial_wave_programs_correct = (
            all_partial_wave_programs_correct and correct
        )
        if not correct:
            raise SystemExit(
                f"Partial-wave probe failed sentinel/reference validation: {blocks=}"
            )
        partial_wave_cases.append(
            {
                "model": wave_model(blocks, resident_slots),
                "completion_distribution": distribution_ms(samples),
                "max_absolute_error": float((observed - expected).abs().max()),
            }
        )

    target = write_result(
        args,
        lab_id="15_tail_load_balance",
        environment=environment,
        measurements={
            "balanced": balanced,
            "skewed": skewed,
            "skewed_to_balanced_modeled_work_ratio": round(work_ratio, 4),
            "schedule": "eight independent CUDA streams with one task per stream",
            "partial_wave_probe": {
                "sm_count": properties.multi_processor_count,
                "warps_per_block": num_warps,
                "registers_per_thread": registers,
                "shared_bytes_per_block": shared_bytes,
                "derived_blocks_per_sm": blocks_per_sm,
                "resident_block_slots": resident_slots,
                "cases": partial_wave_cases,
            },
            "profiler_confirmation_required": True,
        },
        correctness={
            "matched_work_within_two_percent": abs(work_ratio - 1.0) <= 0.02,
            "all_outputs_finite": True,
            "all_partial_wave_programs_completed_and_match_reference": (
                all_partial_wave_programs_correct
            ),
        },
    )
    print(f"Wrote load-balance evidence: {target}")


if __name__ == "__main__":
    main()
