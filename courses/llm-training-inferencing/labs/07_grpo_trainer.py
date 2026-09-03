"""Run a few GRPO steps with a small public model and a local deterministic reward."""

from __future__ import annotations

import argparse

from common import (
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    add_common_args,
    load_torch,
    require_h100,
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
    if args.steps < 1:
        raise SystemExit("--steps must be positive")
    torch = load_torch()
    environment = require_h100(torch)
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

    def concise_reward(completions: list[str], **_: object) -> list[float]:
        return [
            1.0 if len(completion.strip().split()) <= 3 else 0.0
            for completion in completions
        ]

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
        reward_funcs=concise_reward,
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
    result = trainer.train()
    loss = result.metrics.get("train_loss")
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
            "train_loss": None if loss is None else round(float(loss), 6),
        },
        correctness={"trainer_completed": True},
    )
    print(f"Completed GRPO trainer practice: {target}")


if __name__ == "__main__":
    main()
