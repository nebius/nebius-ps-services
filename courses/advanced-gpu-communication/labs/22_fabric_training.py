"""Keep global batch fixed while changing microbatching across eight/sixteen DDP ranks."""

import argparse
import copy
from contextlib import nullcontext

from course_evidence import evidence_phase
from fabric_common import all_correct, initialize, measure
from training_common import (
    add_common_args,
    close_distributed,
    sgd_updates_match,
    validate_common_args,
    write_result,
)


def batch_geometry(global_batch, world, microbatch):
    if global_batch % world or (global_batch // world) % microbatch:
        raise ValueError(
            "Global batch must divide exactly across ranks and microbatches"
        )
    return global_batch // world, global_batch // world // microbatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--microbatch", type=int, choices=(1, 2, 4, 8), default=1)
    args = parser.parse_args()
    validate_common_args(args)
    torch, rank, world, local, env = initialize(args.seed)
    try:
        global_batch, width = (128, 512) if args.workload == "small" else (512, 2048)
        local_batch, accumulation = batch_geometry(global_batch, world, args.microbatch)
        # Identical global examples and model on every rank establish a full-batch reference.
        x = torch.randn(global_batch, width, device="cuda")
        target = torch.randn(global_batch, width, device="cuda")
        model = torch.nn.Sequential(
            torch.nn.Linear(width, width),
            torch.nn.GELU(),
            torch.nn.Linear(width, width),
        ).cuda()
        initial = {
            key: value.detach().clone() for key, value in model.state_dict().items()
        }
        reference_model = copy.deepcopy(model)
        before = tuple(p.detach().clone() for p in model.parameters())
        reference_loss = torch.nn.functional.mse_loss(reference_model(x), target)
        reference_loss.backward()
        torch.optim.SGD(
            reference_model.parameters(), lr=0.01, foreach=False, fused=False
        ).step()
        model.zero_grad(set_to_none=True)
        ddp = torch.nn.parallel.DistributedDataParallel(model, device_ids=[local])
        optimizer = torch.optim.SGD(
            ddp.parameters(), lr=0.01, foreach=False, fused=False
        )
        start = rank * local_batch
        local_x, local_y = (
            x[start : start + local_batch],
            target[start : start + local_batch],
        )
        loss_sum = torch.zeros((), device="cuda")

        def operation():
            # Controlled replay includes the same reset cost in both variants.
            model.load_state_dict(initial)
            optimizer.zero_grad(set_to_none=True)
            loss_sum.zero_()
            for index in range(accumulation):
                sync = nullcontext() if index == accumulation - 1 else ddp.no_sync()
                with sync:
                    offset = index * args.microbatch
                    with evidence_phase("forward"):
                        loss = (
                            torch.nn.functional.mse_loss(
                                ddp(local_x[offset : offset + args.microbatch]),
                                local_y[offset : offset + args.microbatch],
                            )
                            / accumulation
                        )
                    loss_sum.add_(loss.detach())
                    with evidence_phase("backward"):
                        loss.backward()
            with evidence_phase("optimizer"):
                optimizer.step()

        torch.cuda.reset_peak_memory_stats()
        times = measure(torch, operation, args.warmup, args.iterations)
        all_correct(
            torch,
            sgd_updates_match(
                torch,
                before,
                tuple(reference_model.parameters()),
                tuple(model.parameters()),
                learning_rate=0.01,
            ),
            "DDP update differs from the fixed global-batch reference",
        )
        loss = loss_sum.clone()
        torch.distributed.all_reduce(loss)
        loss /= world
        peak = torch.tensor(
            torch.cuda.max_memory_allocated(), device="cuda", dtype=torch.int64
        )
        torch.distributed.all_reduce(peak, op=torch.distributed.ReduceOp.MAX)
        if rank == 0:
            print(
                write_result(
                    args,
                    lab_id="22_fabric_training",
                    environment=env,
                    measurements={
                        "global_batch": global_batch,
                        "width": width,
                        "accumulation": accumulation,
                        "ranks": world,
                        "step": times,
                        "samples_per_second": global_batch / times["median_ms"] * 1000,
                        "peak_allocated_bytes": peak.item(),
                        "loss": loss.item(),
                    },
                    correctness={
                        "update_matches_global_reference": True,
                        "loss_matches_global_reference": bool(
                            torch.allclose(
                                loss, reference_loss.detach(), rtol=1e-4, atol=1e-5
                            )
                        ),
                    },
                )
            )
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
