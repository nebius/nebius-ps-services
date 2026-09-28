"""Compare serialized and gradient-ready bucket all-reduce schedules."""

from __future__ import annotations

import argparse
import math
import statistics
import time
from typing import Any

from course_evidence import annotated_operation
from gradient_overlap_common import (
    bucket_loss,
    bucket_sizes,
    gradient_checks,
    make_bucket_seeds,
    require_gradient_consensus,
)
from training_common import (
    add_common_args,
    close_distributed,
    init_nccl,
    load_torch,
    require_course_gpu,
    seed_everything,
    validate_common_args,
    write_result,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument(
        "--bucket-mib",
        type=int,
        nargs="+",
        default=None,
        help="Two or more BF16 gradient bucket sizes in MiB.",
    )
    args = parser.parse_args()
    validate_common_args(args)
    bucket_mib = bucket_sizes(args.profile, args.bucket_mib)
    torch = load_torch()
    environment = require_course_gpu(torch)
    seed_everything(torch, args.seed)
    rank, world_size, local_rank = init_nccl(torch)
    try:
        device = f"cuda:{local_rank}"

        def run_once(
            seeds: list[tuple[Any, Any]], *, overlap: bool
        ) -> tuple[float, float | None, list[int], list[Any]]:
            parameters = [
                parameter.detach().clone().requires_grad_(True)
                for parameter, _input in seeds
            ]
            pending: list[Any] = []
            ready_order: list[int] = []
            handles = []
            step_started = time.perf_counter()

            def make_hook(index: int):
                def hook(parameter: Any) -> None:
                    ready_order.append(index)
                    if overlap:
                        pending.append(
                            torch.distributed.all_reduce(parameter.grad, async_op=True)
                        )

                return hook

            for index, parameter in enumerate(parameters):
                handles.append(
                    parameter.register_post_accumulate_grad_hook(make_hook(index))
                )
            loss = bucket_loss(parameters, seeds)
            loss.backward()
            if overlap:
                for work in pending:
                    work.wait()
                isolated_collective_device_ms = None
            else:
                collective_started = torch.cuda.Event(enable_timing=True)
                collective_finished = torch.cuda.Event(enable_timing=True)
                collective_started.record()
                for parameter in parameters:
                    torch.distributed.all_reduce(parameter.grad)
                collective_finished.record()
                collective_finished.synchronize()
                isolated_collective_device_ms = float(
                    collective_started.elapsed_time(collective_finished)
                )
            for parameter in parameters:
                parameter.grad.div_(world_size)
            for handle in handles:
                handle.remove()
            torch.cuda.synchronize()
            elapsed = torch.tensor(
                (time.perf_counter() - step_started) * 1_000,
                device=device,
                dtype=torch.float64,
            )
            torch.distributed.all_reduce(elapsed, op=torch.distributed.ReduceOp.MAX)
            return (
                float(elapsed.item()),
                isolated_collective_device_ms,
                ready_order,
                [parameter.grad.detach().clone() for parameter in parameters],
            )

        schedule_results: dict[str, Any] = {}
        all_gradients_match = True
        all_finite = True
        schedules = {
            "ascending": sorted(bucket_mib),
            "descending": sorted(bucket_mib, reverse=True),
        }
        for schedule_name, schedule in schedules.items():
            seeds = make_bucket_seeds(torch, schedule, device=device)
            serialized_reference = run_once(seeds, overlap=False)
            overlapped_reference = run_once(seeds, overlap=True)
            checks = gradient_checks(
                torch, serialized_reference[3], overlapped_reference[3]
            )
            require_gradient_consensus(torch, checks, device=device)
            gradients_match = checks["passed"]
            finite = checks["all_finite"]
            all_gradients_match = all_gradients_match and gradients_match
            all_finite = all_finite and finite
            for _ in range(args.warmup):
                run_once(seeds, overlap=False)
                run_once(seeds, overlap=True)
            serialized_steps: list[float] = []
            serialized_collective_device: list[float] = []
            overlapped_steps: list[float] = []
            observed_ready_order: list[int] = []
            for _ in range(args.iterations):
                serialized = run_once(seeds, overlap=False)
                overlapped = run_once(seeds, overlap=True)
                serialized_steps.append(serialized[0])
                if serialized[1] is None:
                    raise SystemExit("Serialized collective timing was not recorded.")
                serialized_collective_device.append(serialized[1])
                overlapped_steps.append(overlapped[0])
                observed_ready_order = overlapped[2]
            serialized_median = statistics.median(serialized_steps)
            overlapped_median = statistics.median(overlapped_steps)
            schedule_results[schedule_name] = {
                "bucket_mib_in_forward_construction_order": schedule,
                "observed_gradient_ready_bucket_indices": observed_ready_order,
                "serialized_step_median_ms": round(serialized_median, 4),
                "serialized_collective_cuda_event_median_ms": round(
                    statistics.median(serialized_collective_device), 4
                ),
                "overlapped_step_median_ms": round(overlapped_median, 4),
                "serialized_to_overlap_ratio": round(
                    serialized_median / overlapped_median, 4
                ),
                "overlap_timeline_claim": (
                    "pending Nsight Systems confirmation; the step ratio alone "
                    "does not prove exposed communication"
                ),
                "gradients_match": gradients_match,
                "gradient_validation": checks,
            }
        if rank == 0:
            target = write_result(
                args,
                lab_id="19_gradient_overlap",
                environment={**environment, "rank_count": world_size},
                measurements={
                    "schedules": schedule_results,
                    "timing_scope": "slowest rank; forward, backward, all-reduce, and wait",
                    "interpretation_boundary": (
                        "a ratio is an observation, not a published speedup, until "
                        "independent H100 runs and profiler ranges confirm overlap"
                    ),
                },
                correctness={
                    "serialized_and_overlapped_gradients_match": all_gradients_match,
                    "all_gradients_finite": all_finite,
                    "all_ratios_finite": all(
                        math.isfinite(result["serialized_to_overlap_ratio"])
                        for result in schedule_results.values()
                    ),
                },
            )
            print(f"Completed gradient-bucket overlap experiment: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    annotated_operation(main, "lab_workload")()
