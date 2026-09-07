"""Model lane utilization, then measure Triton grid tails with estimated residency."""

from __future__ import annotations

import argparse

from common import (
    add_common_args,
    cuda_times_ms,
    load_torch,
    require_h100,
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
    def tail_probe(output, work: tl.constexpr):
        program = tl.program_id(axis=0)
        value = program.to(tl.float32) * 0.0001
        for iteration in tl.range(0, work):
            value = value * 1.000001 + (iteration + 1) * 0.0000001
        tl.store(output + program, value)
else:
    tail_probe = None


def wave_model(blocks: int, slots: int) -> dict[str, float | int]:
    """Return full waves and final-wave utilization for known resident slots."""
    if blocks < 1 or slots < 1:
        raise ValueError("blocks and slots must be positive")
    waves = (blocks + slots - 1) // slots
    tail_blocks = blocks - (waves - 1) * slots
    return {
        "blocks": blocks,
        "waves": waves,
        "tail_blocks": tail_blocks,
        "tail_utilization": round(tail_blocks / slots, 4),
    }


def lane_work_model(iterations: list[int]) -> dict[str, float | int]:
    """Model a common loop body with per-lane exit; not a GPU measurement.

    One warp issues until its longest-running lane exits. Branch overhead,
    memory latency, compiler predication, and grouping cost are not modeled.
    Distinct branch bodies require summing paths, not taking this maximum.
    """
    if not iterations or any(value < 0 for value in iterations) or not sum(iterations):
        raise ValueError("lane work must be nonnegative with some useful work")
    issued = sum(max(iterations[i : i + 32]) for i in range(0, len(iterations), 32))
    useful = sum(iterations)
    return {
        "warps": (len(iterations) + 31) // 32,
        "useful_lane_iterations": useful,
        "issued_warp_iterations": issued,
        "modeled_lane_utilization": useful / (32 * issued),
    }


def tail_reference(torch: object, blocks: int, work: int) -> object:
    """Evaluate the kernel formula for every program on the CPU in FP32."""
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
    if triton is None or tail_probe is None:
        raise SystemExit(
            "This lab requires the Triton package from the course environment."
        )
    properties = torch.cuda.get_device_properties(0)
    mixed_lanes = lane_work_model([20, 4] * 32)
    grouped_lanes = lane_work_model([20] * 32 + [4] * 32)
    num_warps = 16
    threads = num_warps * 32
    work = 256 if args.profile == "smoke" else 2_048
    probe_output = torch.empty(1, device="cuda", dtype=torch.float32)
    compiled = tail_probe[(1,)](probe_output, work=work, num_warps=num_warps)
    torch.cuda.synchronize()
    registers = int(getattr(compiled, "n_regs", 0))
    metadata = getattr(compiled, "metadata", None)
    shared_bytes = int(getattr(metadata, "shared", 0)) if metadata else 0
    limits = [properties.max_threads_per_multi_processor // threads]
    register_capacity = int(getattr(properties, "regs_per_multiprocessor", 0))
    if registers and register_capacity:
        limits.append(register_capacity // (registers * threads))
    shared_capacity = int(getattr(properties, "shared_memory_per_multiprocessor", 0))
    if shared_bytes and shared_capacity:
        limits.append(shared_capacity // shared_bytes)
    architectural_block_limit = int(
        getattr(properties, "max_blocks_per_multi_processor", 0)
    )
    if architectural_block_limit:
        limits.append(architectural_block_limit)
    blocks_per_sm = min(limit for limit in limits if limit > 0)
    resident_slots = properties.multi_processor_count * blocks_per_sm
    output = torch.empty(2 * resident_slots + 1, device="cuda", dtype=torch.float32)
    cases = []
    all_cases_correct = True
    for blocks in (
        resident_slots,
        resident_slots + 1,
        2 * resident_slots,
        2 * resident_slots + 1,
    ):

        def operation(blocks: int = blocks) -> None:
            tail_probe[(blocks,)](output, work=work, num_warps=num_warps)

        timing = summarize_ms(
            cuda_times_ms(
                torch,
                operation,
                warmup=args.warmup,
                iterations=args.iterations,
            )
        )
        output.fill_(float("nan"))
        operation()
        torch.cuda.synchronize()
        observed = output[:blocks].cpu()
        expected = tail_reference(torch, blocks, work)
        finite = bool(torch.isfinite(observed).all())
        correct = bool(
            finite and torch.allclose(observed, expected, rtol=1e-5, atol=1e-6)
        )
        all_cases_correct = all_cases_correct and correct
        if not correct:
            raise SystemExit(
                f"Tail probe failed sentinel/reference validation for {blocks=}"
            )
        cases.append(
            {
                "model": wave_model(blocks, resident_slots),
                "timing": timing,
                "max_absolute_error": float((observed - expected).abs().max()),
            }
        )
    target = write_result(
        args,
        lab_id="11_scheduler_tail",
        environment=environment,
        measurements={
            "sm_count": properties.multi_processor_count,
            "warps_per_block": num_warps,
            "registers_per_thread": registers,
            "shared_bytes_per_block": shared_bytes,
            "derived_blocks_per_sm": blocks_per_sm,
            "resident_block_slots": resident_slots,
            "cases": cases,
            "lane_work_model": {
                "mixed": mixed_lanes,
                "grouped": grouped_lanes,
                "scope": (
                    "analytical common-loop model, not measured branch efficiency; "
                    "tail_probe has uniform work; Custom Kernels Lab 06 measures "
                    "real divergent/grouped kernels including packing cost"
                ),
            },
            "profiler_confirmation_required": True,
        },
        correctness={
            "all_grid_programs_completed_and_match_reference": all_cases_correct,
            "lane_comparison_preserves_useful_work": mixed_lanes[
                "useful_lane_iterations"
            ]
            == grouped_lanes["useful_lane_iterations"],
        },
    )
    print(f"Wrote scheduler-tail evidence: {target}")


if __name__ == "__main__":
    main()
