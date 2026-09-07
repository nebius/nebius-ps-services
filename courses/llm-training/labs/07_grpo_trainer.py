"""Run a few GRPO steps with a small public model and a local deterministic reward."""

from __future__ import annotations

import argparse
import math
import statistics

from common import (
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    add_common_args,
    load_torch,
    require_h100,
    require_hf_commit_revision,
    seed_everything,
    validate_common_args,
    write_result,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--steps", type=int, default=3)
    args = parser.parse_args()
    validate_common_args(args)
    require_hf_commit_revision(args.revision)
    if args.steps < 1:
        raise SystemExit("--steps must be positive")
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    try:
        from datasets import Dataset
        from peft import LoraConfig
        from transformers import AutoTokenizer
        from trl import GRPOConfig, GRPOTrainer
    except ImportError as exc:
        raise SystemExit(
            "Install the pinned training extras: datasets, peft, and trl."
        ) from exc

    dataset = Dataset.from_list(
        [
            {"prompt": "Reply with the single word CUDA."},
            {"prompt": "Reply with the single word tensor."},
            {"prompt": "Reply with the single word profiler."},
            {"prompt": "Reply with the single word bandwidth."},
        ]
    )

    reward_group_stddevs: list[float] = []

    def controlled_reward(completions: list[str], **_: object) -> list[float]:
        if len(completions) % 2:
            raise ValueError("The controlled exercise requires completion pairs.")
        scores = [float(index % 2) for index in range(len(completions))]
        reward_group_stddevs.extend(
            statistics.pstdev(scores[start : start + 2])
            for start in range(0, len(scores), 2)
        )
        return scores

    trainer_output = args.output_dir / f"grpo-trainer-run-{args.run_id}"
    if trainer_output.exists():
        raise SystemExit(
            f"Refusing to reuse an existing trainer output: {trainer_output}"
        )
    config = GRPOConfig(
        output_dir=str(trainer_output),
        max_steps=args.steps,
        per_device_train_batch_size=2,
        num_generations=2,
        max_completion_length=32,
        bf16=True,
        seed=args.seed,
        logging_steps=1,
        save_strategy="no",
        report_to="none",
        model_init_kwargs={
            "dtype": torch.bfloat16,
            "attn_implementation": "sdpa",
            "trust_remote_code": False,
            "revision": args.revision,
        },
    )
    tokenizer = AutoTokenizer.from_pretrained(
        args.model,
        revision=args.revision,
        trust_remote_code=False,
        padding_side="left",
    )
    trainer = GRPOTrainer(
        model=args.model,
        reward_funcs=controlled_reward,
        args=config,
        train_dataset=dataset,
        processing_class=tokenizer,
        peft_config=LoraConfig(
            r=8,
            lora_alpha=16,
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "v_proj"],
        ),
    )
    trainable_before = {
        name: parameter.detach().clone()
        for name, parameter in trainer.model.named_parameters()
        if parameter.requires_grad
    }
    observed_gradient_norms: list[float] = []

    def record_gradient(gradient: object) -> object:
        observed_gradient_norms.append(float(gradient.float().norm().item()))
        return gradient

    gradient_hooks = [
        parameter.register_hook(record_gradient)
        for parameter in trainer.model.parameters()
        if parameter.requires_grad
    ]
    result = trainer.train()
    for hook in gradient_hooks:
        hook.remove()
    loss = result.metrics.get("train_loss")
    if loss is None or not math.isfinite(float(loss)):
        raise SystemExit("GRPO did not report a finite training loss.")
    if not reward_group_stddevs or min(reward_group_stddevs) <= 0:
        raise SystemExit("GRPO reward groups contained no learning signal.")
    positive_gradient_norms = [
        value for value in observed_gradient_norms if math.isfinite(value) and value > 0
    ]
    if not positive_gradient_norms:
        raise SystemExit("GRPO produced no finite nonzero adapter gradient.")
    adapter_max_delta = max(
        float((parameter.detach() - trainable_before[name]).abs().max().item())
        for name, parameter in trainer.model.named_parameters()
        if parameter.requires_grad
    )
    if not math.isfinite(adapter_max_delta) or adapter_max_delta <= 0:
        raise SystemExit("GRPO adapter parameters did not update.")
    target = write_result(
        args,
        lab_id="07_grpo_trainer",
        environment=environment,
        measurements={
            "model": args.model,
            "revision": args.revision,
            "steps": args.steps,
            "train_runtime_seconds": round(
                float(result.metrics.get("train_runtime", 0.0)), 4
            ),
            "train_loss": round(float(loss), 6),
            "controlled_reward_rule": "alternating rank within each pair",
            "minimum_reward_group_stddev": round(min(reward_group_stddevs), 6),
            "positive_adapter_gradient_samples": len(positive_gradient_norms),
            "maximum_observed_adapter_gradient_norm": round(
                max(positive_gradient_norms), 6
            ),
            "adapter_max_parameter_delta": adapter_max_delta,
        },
        correctness={
            "trainer_completed": True,
            "finite_train_loss": True,
            "nonzero_within_group_reward_signal": True,
            "nonzero_finite_adapter_gradient": True,
            "nonzero_adapter_parameter_update": True,
        },
    )
    print(f"Completed GRPO trainer practice: {target}")


if __name__ == "__main__":
    main()
