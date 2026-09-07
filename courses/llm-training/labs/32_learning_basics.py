"""Observe one learned weight, autograd, an update and held-out inference."""

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


def run_experiment(torch: Any, device: str) -> dict[str, Any]:
    inputs = torch.tensor([1.0, 2.0], device=device)
    targets = 2 * inputs
    weight = torch.nn.Parameter(torch.tensor(0.0, device=device))
    optimizer = torch.optim.SGD([weight], lr=0.1)
    loss_before = float(((weight * inputs - targets) ** 2).mean().detach())
    first_gradient = None
    for step in range(30):
        optimizer.zero_grad(set_to_none=True)
        predictions = weight * inputs
        loss = ((predictions - targets) ** 2).mean()
        loss.backward()
        if step == 0:
            first_gradient = float(weight.grad)
        optimizer.step()
    optimizer.zero_grad(set_to_none=True)
    trained = weight.detach().clone()
    with torch.inference_mode():
        loss_after = float(((weight * inputs - targets) ** 2).mean())
        held_out = weight * torch.tensor(3.0, device=device)
    torch.testing.assert_close(
        weight, torch.tensor(2.0, device=device), rtol=1e-5, atol=1e-6
    )
    torch.testing.assert_close(
        held_out, torch.tensor(6.0, device=device), rtol=1e-5, atol=1e-6
    )
    if not torch.equal(weight.detach(), trained) or weight.grad is not None:
        raise AssertionError("Inference changed parameters or created gradients")
    return {
        "initial_weight": 0.0,
        "first_gradient": first_gradient,
        "final_weight": float(trained),
        "loss_before": loss_before,
        "loss_after": loss_after,
        "held_out_input": 3.0,
        "held_out_prediction": float(held_out),
        "weight_after_inference": float(weight.detach()),
        "updates": 30,
        "scope": "scalar FP32 mechanics; no transformer quality or performance claim",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, include_measurement=False)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = (
        require_h100(torch)
        if args.device == "cuda"
        else {"device": "cpu", "torch_version": torch.__version__}
    )
    measurements = run_experiment(torch, args.device)
    target = write_result(
        args,
        lab_id="32_learning_basics",
        environment=environment,
        measurements=measurements,
        correctness={
            "learned_expected_weight": True,
            "held_out_prediction_matches": True,
            "inference_preserves_parameters": True,
        },
    )
    print(f"Completed learning-mechanics experiment: {target}")


if __name__ == "__main__":
    main()
