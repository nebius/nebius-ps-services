"""Measure real DDP buckets and communication hooks against full-batch FP32 SGD."""

from __future__ import annotations

import argparse
import copy
import math
import time
from typing import Any

from common import (
    add_common_args,
    close_distributed,
    init_nccl,
    load_torch,
    require_h100,
    seed_everything,
    summarize_ms,
    validate_common_args,
    write_result,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.set_defaults(warmup=4)
    parser.add_argument("--bucket-cap-mb", type=float, default=1.0)
    parser.add_argument(
        "--hook", choices=("allreduce", "fp16", "bf16", "powersgd"), default="allreduce"
    )
    parser.add_argument("--power-rank", type=int, default=1)
    parser.add_argument("--power-start", type=int, default=2)
    args = parser.parse_args()
    if not math.isfinite(args.bucket_cap_mb) or not 0.01 <= args.bucket_cap_mb <= 1024:
        parser.error("--bucket-cap-mb must be finite and 0.01..1024")
    if not 1 <= args.power_rank <= 32 or args.power_start < 2:
        parser.error("--power-rank must be 1..32 and --power-start at least 2")
    if args.warmup < 2 or args.iterations < 2 or args.warmup + args.iterations > 200:
        parser.error("Use at least 2 warmup and 2 measured steps, at most 200 total")
    if args.hook == "powersgd" and args.warmup <= args.power_start:
        parser.error("Warmup must include at least one PowerSGD-compressed step")
    return args


def relative_l2(torch: Any, observed: list[Any], reference: list[Any]) -> float:
    if not observed or len(observed) != len(reference):
        raise ValueError("Missing gradient or parameter reference")
    error = 0.0
    scale = 0.0
    for actual, expected in zip(observed, reference, strict=True):
        if actual is None or expected is None or actual.shape != expected.shape:
            raise ValueError("Missing gradient or mismatched reference shape")
        if not bool(torch.isfinite(actual).all() and torch.isfinite(expected).all()):
            raise ValueError("Nonfinite gradient or parameter reference")
        error += float((actual.double() - expected.double()).square().sum())
        scale += float(expected.double().square().sum())
    result = math.sqrt(error / max(scale, 1e-30))
    if not math.isfinite(result):
        raise ValueError("Nonfinite relative error")
    return result


def verify_update(
    torch: Any, before: list[Any], parameters: list[Any], lr: float
) -> None:
    if any(
        p.grad is None or not bool(torch.isfinite(p.grad).all()) for p in parameters
    ):
        raise ValueError("Missing or nonfinite gradient before SGD")
    expected = [
        b.clone().add_(p.grad, alpha=-lr)
        for b, p in zip(before, parameters, strict=True)
    ]
    if not all(torch.equal(p, e) for p, e in zip(parameters, expected, strict=True)):
        raise ValueError("SGD update differs from its gradient reference")
    if not any(not torch.equal(p, b) for p, b in zip(parameters, before, strict=True)):
        raise ValueError("SGD did not update any parameter")


def checked_sgd(torch: Any, model: Any, optimizer: Any, lr: float) -> None:
    parameters = list(model.parameters())
    before = [p.detach().clone() for p in parameters]
    optimizer.step()
    verify_update(torch, before, parameters, lr)


def make_hook(
    torch: Any, args: argparse.Namespace, bucket_log: list[dict[str, int]]
) -> tuple[Any, Any]:
    from torch.distributed.algorithms.ddp_comm_hooks import default_hooks
    from torch.distributed.algorithms.ddp_comm_hooks.powerSGD_hook import (
        PowerSGDState,
        powerSGD_hook,
    )

    state = torch.distributed.group.WORLD
    if args.hook == "powersgd":
        state = PowerSGDState(
            state,
            matrix_approximation_rank=args.power_rank,
            start_powerSGD_iter=args.power_start,
            use_error_feedback=True,
            warm_start=True,
            random_seed=args.seed,
            orthogonalization_epsilon=1e-8,
        )
        selected = powerSGD_hook
    else:
        selected = {
            "allreduce": default_hooks.allreduce_hook,
            "fp16": default_hooks.fp16_compress_hook,
            "bf16": default_hooks.bf16_compress_hook,
        }[args.hook]

    # No annotations: DDP validates hook annotations against concrete Torch types.
    def observed_hook(state, bucket):
        bucket_log.append(
            {
                "index": bucket.index(),
                "uncompressed_bytes": bucket.buffer().numel()
                * bucket.buffer().element_size(),
            }
        )
        with torch.cuda.nvtx.range("ddp_bucket_submit"):
            return selected(state, bucket)

    return state, observed_hook


def main() -> None:
    args = parse_args()
    validate_common_args(args)
    torch = load_torch()
    rank, world_size, local_rank = init_nccl(torch)
    try:
        environment = require_h100(torch)
        seed_everything(torch, args.seed)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.set_num_threads(1)
        device = torch.device("cuda", local_rank)
        width = 256 if args.profile == "smoke" else 1024
        model = torch.nn.Sequential(
            *[
                layer
                for _ in range(4)
                for layer in (torch.nn.Linear(width, width), torch.nn.Tanh())
            ]
        ).to(device)
        reference = copy.deepcopy(model)
        ddp = torch.nn.parallel.DistributedDataParallel(
            model,
            device_ids=[local_rank],
            bucket_cap_mb=args.bucket_cap_mb,
        )
        bucket_log: list[dict[str, int]] = []
        state, hook = make_hook(torch, args, bucket_log)
        ddp.register_comm_hook(state, hook)
        lr = 0.05
        optimizer = torch.optim.SGD(ddp.parameters(), lr=lr, foreach=False)
        reference_optimizer = torch.optim.SGD(
            reference.parameters(), lr=lr, foreach=False
        )
        generator = torch.Generator(device=device).manual_seed(args.seed + 1)
        inputs = torch.randn(16 * world_size, width, generator=generator, device=device)
        targets = torch.tanh(inputs * 0.2)
        local_inputs = inputs.chunk(world_size)[rank]
        local_targets = targets.chunk(world_size)[rank]
        loss_fn = torch.nn.MSELoss()
        records = []
        timings = []
        total_steps = args.warmup + args.iterations
        for step in range(total_steps):
            bucket_log.clear()
            candidate_before = [p.detach().clone() for p in model.parameters()]
            torch.distributed.barrier()
            torch.cuda.synchronize()
            started = time.perf_counter()
            with torch.cuda.nvtx.range(
                "ddp_warmup_step" if step < args.warmup else "ddp_measured_step"
            ):
                optimizer.zero_grad(set_to_none=True)
                loss = loss_fn(ddp(local_inputs), local_targets)
                loss.backward()
                # Acceptance timing includes SGD but excludes expensive oracle checks.
                optimizer.step()
                torch.cuda.synchronize()
            elapsed = (time.perf_counter() - started) * 1000
            max_elapsed = torch.tensor(elapsed, device=device, dtype=torch.float64)
            torch.distributed.all_reduce(max_elapsed, op=torch.distributed.ReduceOp.MAX)
            if step >= args.warmup:
                timings.append(float(max_elapsed.item()))

            with torch.cuda.nvtx.range("full_batch_reference"):
                verify_update(torch, candidate_before, list(model.parameters()), lr)
                reference_optimizer.zero_grad(set_to_none=True)
                reference_loss = loss_fn(reference(inputs), targets)
                reference_loss.backward()
                ref_parameters = list(reference.parameters())
                parameters = list(model.parameters())
                gradient_error = relative_l2(
                    torch,
                    [p.grad for p in parameters],
                    [p.grad for p in ref_parameters],
                )
                checked_sgd(torch, reference, reference_optimizer, lr)
                parameter_error = relative_l2(torch, parameters, ref_parameters)
                if args.hook == "allreduce":
                    for p, expected in zip(parameters, ref_parameters, strict=True):
                        torch.testing.assert_close(
                            p.grad, expected.grad, rtol=1e-5, atol=1e-6
                        )
                        torch.testing.assert_close(p, expected, rtol=1e-5, atol=1e-6)
                average_loss = loss.detach().clone()
                torch.distributed.all_reduce(average_loss)
                average_loss.div_(world_size)
                if not bool(torch.isfinite(average_loss)):
                    raise ValueError("Nonfinite candidate loss")
                records.append(
                    {
                        "step": step,
                        "warmup": step < args.warmup,
                        "candidate_global_loss": float(average_loss.item()),
                        "reference_global_loss": float(reference_loss.item()),
                        "gradient_trajectory_relative_l2": gradient_error,
                        "parameter_trajectory_relative_l2": parameter_error,
                        "observed_buckets": list(bucket_log),
                    }
                )
        if not timings or any(not math.isfinite(t) or t <= 0 for t in timings):
            raise ValueError("Invalid DDP step timing")
        if not all(record["observed_buckets"] for record in records):
            raise ValueError("No observed DDP bucket communication")
        if rank == 0:
            target = write_result(
                args,
                lab_id="33_ddp_buckets",
                environment={
                    **environment,
                    "rank_count": world_size,
                    "nccl_version": list(torch.cuda.nccl.version()),
                },
                measurements={
                    "hook": args.hook,
                    "bucket_cap_mb": args.bucket_cap_mb,
                    "power_rank": args.power_rank,
                    "power_start": args.power_start,
                    "step": summarize_ms(timings),
                    "slowest_rank_step_samples_ms": timings,
                    "trajectory": records,
                    "timing_scope": "forward, mean loss, backward/hook and SGD; rank barrier and full-batch oracle excluded",
                    "quality_status": "short synthetic trajectory only; task convergence and compression acceptance pending",
                    "bucket_boundary": "observed uncompressed payload, not wire bytes; cap is not an exact bucket size",
                },
                correctness={
                    "finite_gradients_parameters_and_losses": True,
                    "candidate_and_reference_sgd_checked": True,
                    "fp32_reference_agreement": "passed"
                    if args.hook == "allreduce"
                    else "lossy; errors reported, not equivalence",
                },
            )
            print(f"Completed DDP bucket experiment: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
