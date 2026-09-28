"""Small real Megatron Bridge training with explicit communication controls."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import statistics
import time
from pathlib import Path

from common import (
    add_common_args,
    require_course_gpu,
    validate_common_args,
    write_result,
)


def make_config(args, folder, kind):
    from megatron.bridge.models import GPTModelProvider
    from megatron.bridge.recipes.utils.optimizer_utils import (
        distributed_fused_adam_with_cosine_annealing,
    )
    from megatron.bridge.training.comm_overlap import CommOverlapConfig
    from megatron.bridge.training.config import (
        CheckpointConfig,
        ConfigContainer,
        DistributedDataParallelConfig,
        DistributedInitConfig,
        GPTDatasetConfig,
        LoggerConfig,
        RNGConfig,
        TokenizerConfig,
        TrainingConfig,
        ValidationConfig,
    )

    length = 2048 if args.profile == "small" else 16384
    steps = args.warmup + args.iterations
    overlap = kind == "overlap" and args.overlap == "on"
    optimizer, scheduler = distributed_fused_adam_with_cosine_annealing(
        lr_warmup_iters=2, lr_decay_iters=steps, max_lr=1e-4, min_lr=1e-5
    )
    model = GPTModelProvider(
        num_layers=4,
        hidden_size=512,
        ffn_hidden_size=2048,
        num_attention_heads=8,
        num_query_groups=8,
        vocab_size=8192,
        seq_length=length,
        tensor_model_parallel_size=1,
        pipeline_model_parallel_size=1,
        context_parallel_size=16 if kind == "context" else 1,
        hidden_dropout=0.0,
        attention_dropout=0.0,
        position_embedding_type="rope",
        cross_entropy_loss_fusion=False,
    )
    if kind == "context":
        model.cp_comm_type = "a2a+p2p" if args.layout == "hierarchical" else "p2p"
        model.hierarchical_context_parallel_sizes = (
            [8, 2] if args.layout == "hierarchical" else None
        )
    return ConfigContainer(
        model=model,
        train=TrainingConfig(
            train_iters=steps, global_batch_size=16, micro_batch_size=1
        ),
        validation=ValidationConfig(eval_interval=steps + 1, eval_iters=0),
        optimizer=optimizer,
        scheduler=scheduler,
        ddp=DistributedDataParallelConfig(
            use_distributed_optimizer=True,
            grad_reduce_in_fp32=True,
            check_for_nan_in_grad=True,
            overlap_grad_reduce=overlap,
            overlap_param_gather=False,
        ),
        comm_overlap=CommOverlapConfig(
            tp_comm_overlap=False,
            overlap_grad_reduce=overlap,
            overlap_param_gather=False,
        ),
        dataset=GPTDatasetConfig(
            dataloader_type="single",
            seq_length=length,
            random_seed=args.seed,
            reset_attention_mask=False,
            reset_position_ids=False,
            eod_mask_loss=False,
            blend=None,
            blend_per_split=None,
            split="9999,8,2",
            num_dataset_builder_threads=1,
            create_attention_mask=False,
            skip_getting_attention_mask_from_dataset=True,
        ),
        tokenizer=TokenizerConfig(
            tokenizer_type="NullTokenizer",
            vocab_size=8192,
            use_tokenizer_vocab_size=True,
        ),
        logger=LoggerConfig(
            log_interval=1,
            tensorboard_log_interval=1,
            tensorboard_dir=str(folder / "tensorboard"),
            log_timers_to_tensorboard=True,
        ),
        checkpoint=CheckpointConfig(
            save=str(folder / "checkpoints"),
            load=None,
            save_interval=steps,
            ckpt_format="torch_dist",
            async_save=False,
            fully_parallel_save=True,
        ),
        rng=RNGConfig(seed=args.seed),
        dist=DistributedInitConfig(use_decentralized_pg=False),
        mixed_precision="bf16_mixed",
    )


def compare_weights(torch, actual, reference, *, atol, rtol):
    if actual.keys() != reference.keys():
        raise ValueError("Reference parameter names differ")
    maximum = 0.0
    for name, value in actual.items():
        expected = reference[name]
        if (
            value.shape != expected.shape
            or not torch.isfinite(value).all()
            or not torch.isfinite(expected).all()
        ):
            raise ValueError("Reference shape or finite-value check failed")
        maximum = max(maximum, float((value.float() - expected.float()).abs().max()))
        if not torch.allclose(value.float(), expected.float(), atol=atol, rtol=rtol):
            raise ValueError(
                "Full final parameters exceed the declared BF16 comparison tolerance"
            )
    return maximum


def run(kind):
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.set_defaults(warmup=5, iterations=20)
    parser.add_argument("--overlap", choices=("off", "on"), default="off")
    parser.add_argument("--layout", choices=("flat", "hierarchical"), default="flat")
    parser.add_argument("--reference-only", action="store_true")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--atol", type=float, default=0.005)
    parser.add_argument("--rtol", type=float, default=0.05)
    args = parser.parse_args()
    if not args.reference_only and (
        args.reference is None or not args.reference.is_file()
    ):
        parser.error(
            "Create a same-workload --reference-only run first, then pass its final-weights.pt via --reference"
        )
    if args.reference_only and (args.overlap != "off" or args.layout != "flat"):
        parser.error("Reference must use the unchanged baseline controls")
    if args.warmup < 2 or any(
        not math.isfinite(x) or x < 0 for x in (args.atol, args.rtol)
    ):
        parser.error("Use at least two warm-up steps and finite nonnegative tolerances")
    if (kind == "overlap" and args.layout != "flat") or (
        kind == "context" and args.overlap != "off"
    ):
        parser.error("Change only the control belonging to this experiment")
    if (
        int(os.environ.get("WORLD_SIZE", "0")) != 16
        or os.environ.get("LOCAL_WORLD_SIZE") != "8"
    ):
        parser.error(
            "Use fabric.sbatch with the prepared Bridge Python and torchrun on two eight-H100 workers"
        )
    validate_common_args(args)
    import torch
    from megatron.bridge.training.callbacks import Callback
    from megatron.bridge.training.gpt_step import forward_step
    from megatron.bridge.training.pretrain import pretrain

    local = int(os.environ["LOCAL_RANK"])
    rank = int(os.environ["RANK"])
    torch.cuda.set_device(local)
    env = {**require_course_gpu(torch), "bridge_candidate": "0.6.0", "rank_count": 16}
    lab = "30_megatron_overlap" if kind == "overlap" else "31_context_parallel"
    folder = (args.output_dir / lab / ("vendor-" + args.run_id)).resolve()
    if rank == 0:
        folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    states = []
    weights = {}
    trace_enabled = os.environ.get("COURSE_PROFILE_TOOL", "none") == "nsys"

    class Evidence(Callback):
        def on_train_step_start(self, context):
            if trace_enabled:
                torch.cuda.nvtx.range_push("bridge_training_step")

        def on_train_step_end(self, context):
            if trace_enabled:
                torch.cuda.nvtx.range_pop()
            # Loss tensors are materialized after training; no added timed host synchronization.
            states.append((context.loss_dict, context.grad_norm, context.skipped_iter))

        def on_train_end(self, context):
            if rank == 0:
                weights.update(
                    {
                        name: value.detach().cpu()
                        for name, value in context.model[0].named_parameters()
                    }
                )
                torch.save(
                    {
                        "weights": weights,
                        "profile": args.profile,
                        "seed": args.seed,
                        "steps": args.warmup + args.iterations,
                        "kind": kind,
                    },
                    folder / "final-weights.pt",
                )

    pretrain(make_config(args, folder, kind), forward_step, callbacks=[Evidence()])
    if rank != 0:
        return
    losses = [float(loss["lm loss"].detach().cpu()) for loss, _, _ in states]
    norms = [float(norm) for _, norm, _ in states]
    if (
        len(losses) != args.warmup + args.iterations
        or any(skipped for _, _, skipped in states)
        or not all(math.isfinite(x) for x in losses + norms)
    ):
        raise ValueError("Training has missing, skipped or nonfinite steps")
    if args.reference_only:
        print("Reference weights:", folder / "final-weights.pt")
        return
    reference = torch.load(args.reference, map_location="cpu", weights_only=True)
    if any(
        reference[key] != value
        for key, value in {
            "profile": args.profile,
            "seed": args.seed,
            "steps": args.warmup + args.iterations,
            "kind": kind,
        }.items()
    ):
        raise ValueError("Reference was trained with a different workload")
    maximum = compare_weights(
        torch, weights, reference["weights"], atol=args.atol, rtol=args.rtol
    )
    from tensorboard.backend.event_processing.event_accumulator import EventAccumulator

    events = EventAccumulator(str(folder / "tensorboard"), size_guidance={"scalars": 0})
    deadline = time.monotonic() + 30
    samples = []
    while time.monotonic() < deadline:
        events.Reload()
        if "iteration-time" in events.Tags().get("scalars", []):
            samples = [
                entry.value
                for entry in events.Scalars("iteration-time")
                if entry.step > args.warmup
            ]
            if len(samples) >= args.iterations:
                break
        time.sleep(0.5)
    if len(samples) != args.iterations or not all(
        math.isfinite(x) and x > 0 for x in samples
    ):
        raise ValueError(
            "Missing complete vendor iteration-time scalars; do not substitute console submission time"
        )
    length = 2048 if args.profile == "small" else 16384
    print(
        write_result(
            args,
            lab_id=lab,
            environment=env,
            measurements={
                "step_seconds": statistics.median(samples),
                "tokens_per_second": 16 * length / statistics.mean(samples),
                "step_samples_seconds": samples,
                "losses": losses,
                "parameter_max_abs_error": maximum,
                "tokens_per_step": 16 * length,
                "reference_sha256": hashlib.sha256(
                    args.reference.read_bytes()
                ).hexdigest(),
            },
            correctness={
                "no_skipped_nonfinite_steps": True,
                "all_parameters_match_reference": True,
            },
        )
    )


if __name__ == "__main__":
    raise SystemExit("Use the numbered Megatron or context-parallel lab entry")
