"""Fine-tune a public small language model with a lightweight LoRA adapter."""

from __future__ import annotations

import argparse
import math
import time

from common import (
    DEFAULT_MODEL,
    DEFAULT_REVISION,
    add_common_args,
    load_torch,
    require_h100,
    seed_everything,
    validate_common_args,
    write_result,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    try:
        from peft import LoraConfig, get_peft_model
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit(
            "Install the pinned training extras: transformers and peft."
        ) from exc

    examples = [
        "Question: Why synchronize before CPU wall-clock timing? Answer: CUDA launches are asynchronous.",
        "Question: What does the KV cache hold? Answer: Attention keys and values for processed tokens.",
        "Question: When is a kernel memory bound? Answer: When data movement limits throughput.",
        "Question: Why use BF16 on H100? Answer: It uses tensor cores while retaining a wide exponent range.",
    ]
    tokenizer = AutoTokenizer.from_pretrained(
        args.model, revision=args.revision, trust_remote_code=False
    )
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    encoded = tokenizer(
        examples,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=128,
    )
    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        revision=args.revision,
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
        trust_remote_code=False,
    ).to("cuda")
    model = get_peft_model(
        model,
        LoraConfig(
            r=8,
            lora_alpha=16,
            lora_dropout=0.05,
            bias="none",
            task_type="CAUSAL_LM",
            target_modules=["q_proj", "v_proj"],
        ),
    )
    trainable = sum(
        parameter.numel() for parameter in model.parameters() if parameter.requires_grad
    )
    total = sum(parameter.numel() for parameter in model.parameters())
    optimizer = torch.optim.AdamW(
        (parameter for parameter in model.parameters() if parameter.requires_grad),
        lr=2e-4,
    )
    input_ids = encoded["input_ids"].to("cuda")
    attention_mask = encoded["attention_mask"].to("cuda")
    labels = input_ids.masked_fill(attention_mask == 0, -100)
    losses: list[float] = []

    def train_step() -> object:
        output = model(
            input_ids=input_ids, attention_mask=attention_mask, labels=labels
        )
        output.loss.backward()
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        return output.loss.detach()

    for _ in range(args.warmup):
        train_step()
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    for _ in range(args.iterations):
        losses.append(float(train_step()))
    torch.cuda.synchronize()
    elapsed_ms = (time.perf_counter() - started) * 1_000
    if not all(math.isfinite(loss) for loss in losses):
        raise SystemExit("LoRA training produced a non-finite loss.")
    target = write_result(
        args,
        lab_id="05_lora_sft",
        environment=environment,
        measurements={
            "model": args.model,
            "revision": args.revision,
            "trainable_parameters": trainable,
            "total_parameters": total,
            "trainable_percent": round(100 * trainable / total, 4),
            "initial_loss": round(losses[0], 5),
            "final_loss": round(losses[-1], 5),
            "elapsed_ms": round(elapsed_ms, 4),
            "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 2),
        },
        correctness={
            "finite_loss": True,
            "adapter_is_parameter_efficient": trainable < total / 10,
        },
    )
    print(f"Completed LoRA SFT practice: {target}")


if __name__ == "__main__":
    main()
