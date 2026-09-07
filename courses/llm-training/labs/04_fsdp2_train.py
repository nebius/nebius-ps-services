"""Shard tiny-transformer parameters, gradients, and optimizer state with FSDP2."""

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
        try:
            from torch.distributed.fsdp import MixedPrecisionPolicy, fully_shard
        except ImportError as exc:
            raise SystemExit(
                "This lab requires the FSDP2 fully_shard API in the pinned PyTorch release."
            ) from exc
        seed_everything(torch, args.seed)
        hidden, layers, sequence, batch = (
            (512, 6, 256, 8) if args.profile == "smoke" else (2_048, 16, 1_024, 4)
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
        policy = MixedPrecisionPolicy(
            param_dtype=torch.bfloat16,
            reduce_dtype=torch.float32,
            output_dtype=torch.bfloat16,
        )
        for block in model.blocks:
            fully_shard(block, mp_policy=policy)
        fully_shard(model, mp_policy=policy)
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
            logits = model(inputs)
            step_loss = torch.nn.functional.cross_entropy(
                logits.float().reshape(-1, vocab_size), labels.reshape(-1)
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
        reduced_loss = loss
        torch.distributed.all_reduce(reduced_loss)
        reduced_loss /= world_size
        if rank == 0:
            target = write_result(
                args,
                lab_id="04_fsdp2_train",
                environment=environment,
                measurements={
                    "world_size": world_size,
                    "slowest_rank_elapsed_ms": round(float(elapsed_ms), 4),
                    "final_mean_loss": round(float(reduced_loss), 5),
                    "max_rank_peak_allocated_mib": round(
                        float(peak_allocated) / 2**20, 2
                    ),
                },
                correctness={"finite_mean_loss": bool(torch.isfinite(reduced_loss))},
            )
            print(f"Completed two-node FSDP2 training: {target}")
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
