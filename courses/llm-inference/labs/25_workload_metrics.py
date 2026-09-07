"""Measure input projection and recurrent operator work, not language-model tokens."""

from __future__ import annotations

import argparse
import math

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


def operator_rates(
    batch: int, steps: int, sequence_ms: float, joined_ms: float
) -> dict[str, float]:
    """Count independent row updates, never label synthetic work as generated tokens."""
    if (
        batch < 1
        or steps < 1
        or any(
            not math.isfinite(value) or value <= 0 for value in (sequence_ms, joined_ms)
        )
    ):
        raise ValueError(
            "operator counts and measured durations must be positive and finite"
        )
    return {
        "mean_recurrent_iteration_ms": sequence_ms / steps,
        "recurrent_row_updates_per_second": batch * steps * 1000 / sequence_ms,
        "joined_row_updates_per_second": batch * steps * 1000 / joined_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Batch rows of synthetic operator work, not concurrent service requests.",
    )
    args = parser.parse_args()
    validate_common_args(args)
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be positive")
    torch = load_torch()
    seed_everything(torch, args.seed)
    environment = require_h100(torch)
    hidden = 256 if args.profile == "smoke" else 1_024
    weight = torch.randn((hidden, hidden), device="cuda", dtype=torch.bfloat16)

    def incremental_peak(operation: object) -> int:
        torch.cuda.empty_cache()
        baseline = int(torch.cuda.memory_allocated())
        torch.cuda.reset_peak_memory_stats()
        operation()
        torch.cuda.synchronize()
        return int(torch.cuda.max_memory_allocated()) - baseline

    rows = []
    for isl, osl in ((128, 32), (2_048, 32), (128, 512), (2_048, 512)):
        prompt = torch.randn(
            (args.concurrency, isl, hidden), device="cuda", dtype=torch.bfloat16
        )

        def project_prompt() -> object:
            return prompt @ weight

        projected_state = project_prompt()[:, -1, :].contiguous()

        def one_recurrent_step() -> object:
            return torch.tanh(projected_state @ weight)

        def recurrent_sequence() -> object:
            state = projected_state
            for _ in range(osl):
                state = torch.tanh(state @ weight)
            return state

        def joined_workload() -> object:
            state = project_prompt()[:, -1, :].contiguous()
            for _ in range(osl):
                state = torch.tanh(state @ weight)
            return state

        projection_timing = summarize_ms(
            cuda_times_ms(
                torch, project_prompt, warmup=args.warmup, iterations=args.iterations
            )
        )
        recurrent_step_timing = summarize_ms(
            cuda_times_ms(
                torch,
                one_recurrent_step,
                warmup=args.warmup,
                iterations=args.iterations,
            )
        )
        recurrent_timing = summarize_ms(
            cuda_times_ms(
                torch,
                recurrent_sequence,
                warmup=args.warmup,
                iterations=args.iterations,
            )
        )
        joined_timing = summarize_ms(
            cuda_times_ms(
                torch, joined_workload, warmup=args.warmup, iterations=args.iterations
            )
        )
        observed = recurrent_sequence()
        finite = bool(torch.isfinite(observed).all())
        if not finite:
            raise SystemExit(
                f"Recurrent operator work produced non-finite output for {isl=}, {osl=}."
            )
        rows.append(
            {
                "input_length": isl,
                "recurrent_iterations": osl,
                "batch_rows": args.concurrency,
                "projected_input_rows": args.concurrency * isl,
                "recurrent_row_updates": args.concurrency * osl,
                "input_projection": projection_timing,
                "one_recurrent_step": recurrent_step_timing,
                "recurrent_sequence": recurrent_timing,
                "joined_projection_and_recurrence": joined_timing,
                **operator_rates(
                    args.concurrency,
                    osl,
                    recurrent_timing["median_ms"],
                    joined_timing["median_ms"],
                ),
                "projection_incremental_peak_bytes": incremental_peak(project_prompt),
                "recurrent_incremental_peak_bytes": incremental_peak(
                    recurrent_sequence
                ),
                "finite": finite,
            }
        )
    target_path = write_result(
        args,
        lab_id="25_workload_metrics",
        environment=environment,
        measurements={
            "metric_boundary": "synthetic device operators: dense input projection followed by recurrent tanh matmuls; no attention, KV cache, vocabulary head, sampling, or generated tokens",
            "concurrency_scope": "batch rows, not concurrent service requests",
            "live_service_metrics_owner": "vLLM/AIPerf serving labs",
            "workload_cells": rows,
        },
        correctness={
            "four_cells_executed": len(rows) == 4,
            "all_declared_operator_work_counted": all(
                row["projected_input_rows"] == row["batch_rows"] * row["input_length"]
                and row["recurrent_row_updates"]
                == row["batch_rows"] * row["recurrent_iterations"]
                for row in rows
            ),
        },
    )
    print(f"Wrote workload-matrix evidence: {target_path}")


if __name__ == "__main__":
    main()
