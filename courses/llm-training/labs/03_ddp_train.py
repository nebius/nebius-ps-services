"""Train the tiny language model with one DDP rank on each H100 node."""

from __future__ import annotations

import argparse

from common import (
    add_common_args,
    close_distributed,
    init_nccl,
    load_torch,
    require_h100,
    seed_everything,
    validate_common_args,
    write_result,
)
from tiny_lm import build_tiny_lm, make_language_batch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    rank, world_size, local_rank = init_nccl(torch)
    try:
        seed_everything(torch, args.seed)
        hidden, layers, sequence, batch = (
            (512, 4, 256, 8) if args.profile == "smoke" else (2_048, 12, 1_024, 4)
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
                logits = model(inputs)
                step_loss = torch.nn.functional.cross_entropy(
                    logits.reshape(-1, vocab_size), labels.reshape(-1)
                )
            step_loss.backward()
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
            return step_loss.detach()

        for _ in range(args.warmup):
            train_step()
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
        if rank == 0:
            target = write_result(
                args,
                lab_id="03_ddp_train",
                environment=environment,
                measurements={
                    "world_size": world_size,
                    "global_tokens": world_size * batch * sequence * args.iterations,
                    "slowest_rank_elapsed_ms": round(float(elapsed_ms), 4),
                    "final_mean_loss": round(float(reduced_loss), 5),
                    "max_rank_peak_allocated_mib": round(
                        float(peak_allocated) / 2**20, 2
                    ),
                },
                correctness={"finite_mean_loss": bool(torch.isfinite(reduced_loss))},
            )
            print(f"Completed two-node DDP training: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
