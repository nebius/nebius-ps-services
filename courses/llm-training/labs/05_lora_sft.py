"""Fine-tune a small public language model with a lightweight LoRA adapter."""

from __future__ import annotations

import argparse
import hashlib
import math
import time

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
    add_common_args(parser)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    args = parser.parse_args()
    validate_common_args(args)
    require_hf_commit_revision(args.revision)
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
    gradient_norms: list[float] = []
    adapter_before = {
        name: parameter.detach().clone()
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    }

    held_out = tokenizer(
        "Question: Why record immutable model revisions? Answer:",
        return_tensors="pt",
    )
    held_out = {name: value.to("cuda") for name, value in held_out.items()}

    def held_out_evidence() -> tuple[int, str]:
        model.eval()
        with torch.no_grad():
            logits = model(**held_out).logits[:, -1, :].float().cpu()
        model.train()
        return (
            int(logits.argmax(dim=-1).item()),
            hashlib.sha256(logits.numpy().tobytes()).hexdigest(),
        )

    held_out_before = held_out_evidence()

    def train_step() -> tuple[object, float]:
        optimizer.zero_grad(set_to_none=True)
        output = model(
            input_ids=input_ids, attention_mask=attention_mask, labels=labels
        )
        output.loss.backward()
        gradient_norm = math.sqrt(
            sum(
                float(parameter.grad.float().square().sum().item())
                for parameter in model.parameters()
                if parameter.requires_grad and parameter.grad is not None
            )
        )
        optimizer.step()
        return output.loss.detach(), gradient_norm

    for _ in range(args.warmup):
        train_step()
    torch.cuda.synchronize()
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    for _ in range(args.iterations):
        loss, gradient_norm = train_step()
        losses.append(float(loss))
        gradient_norms.append(gradient_norm)
    torch.cuda.synchronize()
    elapsed_ms = (time.perf_counter() - started) * 1_000
    if not all(math.isfinite(loss) for loss in losses):
        raise SystemExit("LoRA training produced a non-finite loss.")
    if not all(math.isfinite(value) and value > 0 for value in gradient_norms):
        raise SystemExit("LoRA adapter gradients were zero or non-finite.")
    adapter_max_delta = max(
        float((parameter.detach() - adapter_before[name]).abs().max().item())
        for name, parameter in model.named_parameters()
        if parameter.requires_grad
    )
    if not math.isfinite(adapter_max_delta) or adapter_max_delta <= 0:
        raise SystemExit("LoRA adapter parameters did not update.")
    held_out_after = held_out_evidence()
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
            "gradient_norm_min": round(min(gradient_norms), 6),
            "gradient_norm_max": round(max(gradient_norms), 6),
            "adapter_max_parameter_delta": adapter_max_delta,
            "held_out_next_token_before": held_out_before[0],
            "held_out_next_token_after": held_out_after[0],
            "held_out_logits_digest_before": held_out_before[1],
            "held_out_logits_digest_after": held_out_after[1],
            "held_out_prompt_published": False,
            "elapsed_ms": round(elapsed_ms, 4),
            "peak_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 2),
        },
        correctness={
            "finite_loss": True,
            "nonzero_finite_adapter_gradients": True,
            "nonzero_adapter_parameter_update": True,
            "bounded_held_out_evaluation_recorded": True,
            "adapter_is_parameter_efficient": trainable < total / 10,
        },
    )
    print(f"Completed LoRA SFT practice: {target}")


if __name__ == "__main__":
    main()
