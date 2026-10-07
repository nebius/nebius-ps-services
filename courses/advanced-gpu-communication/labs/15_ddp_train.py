"""Train the tiny language model with one DDP rank on each H100 node."""

from __future__ import annotations

import argparse
import math

from course_evidence import annotated_operation
from tiny_lm import build_tiny_lm, make_language_batch
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
        "--zero-grad-fill",
        action="store_true",
        help="Zero existing gradient buffers instead of releasing them; keep all work fixed.",
    )
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_course_gpu(torch)
    rank, world_size, local_rank = init_nccl(torch)
    try:
        seed_everything(torch, args.seed)
        hidden, layers, sequence, batch = (
            (512, 4, 256, 8) if args.workload == "small" else (2_048, 12, 1_024, 4)
        )
        vocab_size = 4_096
        model = build_tiny_lm(
            torch,
            vocab_size=vocab_size,
            hidden_size=hidden,
            layers=layers,
            heads=8 if hidden == 512 else 16,
            max_sequence=sequence,
        ).to(local_rank)
        model = torch.nn.parallel.DistributedDataParallel(
            model, device_ids=[local_rank]
        )
        optimizer = torch.optim.AdamW(model.parameters(), lr=3e-4)
        forward = annotated_operation(model, "forward")
        backward = annotated_operation(lambda loss: loss.backward(), "backward")
        update = annotated_operation(optimizer.step, "optimizer")
        clear_gradients = annotated_operation(optimizer.zero_grad, "zero_grad")
        inputs, labels = make_language_batch(
            torch,
            batch_size=batch,
            sequence=sequence,
            vocab_size=vocab_size,
            device=f"cuda:{local_rank}",
            offset=rank * batch,
        )

        def train_step() -> object:
            with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                logits = forward(inputs)
                step_loss = torch.nn.functional.cross_entropy(
                    logits.reshape(-1, vocab_size), labels.reshape(-1)
                )
            backward(step_loss)
            update()
            clear_gradients(set_to_none=not args.zero_grad_fill)
            return step_loss.detach()

        for _ in range(args.warmup):
            train_step()
        tracked_before = next(model.parameters()).detach().cpu().clone()
        torch.distributed.barrier()
        torch.cuda.reset_peak_memory_stats()
        started = torch.cuda.Event(enable_timing=True)
        ended = torch.cuda.Event(enable_timing=True)
        started.record()
        loss = None
        for _ in range(args.iterations):
            loss = train_step()
        ended.record()
        ended.synchronize()
        assert loss is not None
        elapsed_ms = torch.tensor(
            float(started.elapsed_time(ended)), device=f"cuda:{local_rank}"
        )
        peak_allocated = torch.tensor(
            torch.cuda.max_memory_allocated(),
            device=f"cuda:{local_rank}",
            dtype=torch.float64,
        )
        torch.distributed.all_reduce(elapsed_ms, op=torch.distributed.ReduceOp.MAX)
        torch.distributed.all_reduce(peak_allocated, op=torch.distributed.ReduceOp.MAX)
        reduced_loss = loss.float()
        torch.distributed.all_reduce(reduced_loss)
        reduced_loss /= world_size
        tracked_after = next(model.parameters()).detach().cpu()
        update_squared = float((tracked_after - tracked_before).float().square().sum())
        update_evidence = torch.tensor(
            [update_squared, float(math.isfinite(update_squared))],
            device=local_rank,
            dtype=torch.float64,
        )
        torch.distributed.all_reduce(update_evidence)
        parameter_updated = bool(
            update_evidence[1] == world_size and update_evidence[0] > 0
        )
        if not parameter_updated:
            raise SystemExit(
                "The tracked distributed parameter did not receive a finite update."
            )
        if rank == 0:
            target = write_result(
                args,
                lab_id="15_ddp_train",
                environment=environment,
                measurements={
                    "model_shape": {
                        "hidden": hidden,
                        "layers": layers,
                        "sequence": sequence,
                        "batch": batch,
                        "vocab_size": vocab_size,
                    },
                    "precision": "bf16-forward-fp32-parameters",
                    "world_size": world_size,
                    "global_tokens": world_size * batch * sequence * args.iterations,
                    "slowest_rank_elapsed_ms": round(float(elapsed_ms), 4),
                    "final_mean_loss": round(float(reduced_loss), 5),
                    "max_rank_peak_allocated_mib": round(
                        float(peak_allocated) / 2**20, 2
                    ),
                },
                correctness={
                    "finite_mean_loss": bool(torch.isfinite(reduced_loss)),
                    "parameter_updated": parameter_updated,
                },
            )
            print(f"Completed two-node DDP training: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    annotated_operation(main, "lab_workload")()
