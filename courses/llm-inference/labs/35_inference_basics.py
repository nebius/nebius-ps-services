"""Trace a fixed four-token autoregressive model without downloads or an engine."""

from __future__ import annotations

import argparse
from typing import Any

from common import (
    add_common_args,
    load_torch,
    require_h100,
    validate_common_args,
    write_result,
)


def run_experiment(torch: Any, device: str, max_new_tokens: int) -> dict[str, Any]:
    if max_new_tokens < 1:
        raise ValueError("max_new_tokens must be positive")
    vocabulary = ["I", "like", "GPUs", "<eos>"]
    # A fixed bigram score table, not a transformer or a KV-cache implementation.
    model = torch.nn.Embedding(4, 4, device=device)
    with torch.no_grad():
        model.weight.copy_(
            torch.tensor(
                [
                    [-2.0, 4.0, -2.0, -2.0],
                    [-2.0, -2.0, 4.0, -2.0],
                    [-2.0, -2.0, -2.0, 4.0],
                    [-2.0, -2.0, -2.0, 4.0],
                ],
                device=device,
            )
        )
    model.eval()
    before = model.weight.detach().clone()
    current = 0
    generated = []
    steps = []
    stop_reason = "length"
    with torch.inference_mode():
        for _ in range(max_new_tokens):
            logits = model(torch.tensor(current, device=device))
            probabilities = logits.softmax(dim=-1)
            chosen = int(probabilities.argmax())
            steps.append(
                {
                    "input_token": vocabulary[current],
                    "logits": logits.cpu().tolist(),
                    "probabilities": probabilities.cpu().tolist(),
                    "chosen_token": vocabulary[chosen],
                }
            )
            generated.append(vocabulary[chosen])
            if chosen == 3:
                stop_reason = "eos"
                break
            current = chosen
    unchanged = bool(torch.equal(model.weight.detach(), before))
    gradients_created = any(
        parameter.grad is not None for parameter in model.parameters()
    )
    expected = ["like", "GPUs", "<eos>"][:max_new_tokens]
    if generated != expected or not unchanged or gradients_created:
        raise AssertionError("Fixed-parameter generation contract failed")
    return {
        "prompt_tokens": ["I"],
        "generated_tokens": generated,
        "steps": steps,
        "stop_reason": stop_reason,
        "parameters_unchanged": unchanged,
        "gradients_created": gradients_created,
        "scope": "fixed bigram mechanics; no attention, KV cache, TTFT or serving benchmark",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--max-new-tokens", type=int, default=5)
    args = parser.parse_args()
    if args.max_new_tokens < 1:
        parser.error("--max-new-tokens must be positive")
    validate_common_args(args)
    torch = load_torch()
    environment = (
        require_h100(torch)
        if args.device == "cuda"
        else {"device": "cpu", "torch_version": torch.__version__}
    )
    measurements = run_experiment(torch, args.device, args.max_new_tokens)
    target = write_result(
        args,
        lab_id="35_inference_basics",
        environment=environment,
        measurements=measurements,
        correctness={
            "expected_token_sequence": True,
            "parameters_unchanged": True,
            "no_gradients": True,
        },
    )
    print(f"Completed inference-mechanics experiment: {target}")


if __name__ == "__main__":
    main()
