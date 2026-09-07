"""Compare an unfused composition with a maintained PyTorch library primitive."""

from __future__ import annotations

import argparse
from typing import Any

from common import (
    add_common_args,
    cuda_times_ms,
    load_torch,
    require_h100,
    seed_everything,
    summarize_ms,
    validate_common_args,
    write_result,
)


def reference_checks(
    torch: Any, x: Any, weight: Any, bias: Any, outputs: dict[str, Any]
) -> tuple[dict[str, bool], dict[str, Any]]:
    """Check both BF16 paths against FP64 math with an intermediate-rounding budget."""
    product = x.double() @ weight.double()
    reference = torch.relu(product + bias.double())
    rtol, atol = 1e-2, 1e-2
    unit_roundoff = torch.finfo(torch.bfloat16).eps / 2
    # The composition rounds the matmul before bias addition. Propagate that
    # error through the final BF16 rounding; ReLU cannot amplify absolute error.
    rounding_allowance = unit_roundoff * (1 + unit_roundoff) * product.abs()
    budget = atol + rtol * reference.abs() + rounding_allowance
    if not bool(torch.isfinite(reference).all() and torch.isfinite(budget).all()):
        raise SystemExit("Non-finite FP64 reference or BF16 error budget")
    checks = {}
    errors = {}
    for name, output in outputs.items():
        if (
            output.shape != reference.shape
            or output.dtype != torch.bfloat16
            or not bool(torch.isfinite(output).all())
            or not bool((output >= 0).all())
        ):
            raise SystemExit(f"Invalid BF16 ReLU output: {name}")
        error = (output.double() - reference).abs()
        within_budget = bool((error <= budget).all())
        if not within_budget:
            raise SystemExit(f"FP64 reference error budget exceeded: {name}")
        checks[f"{name}_matches_reference"] = within_budget
        errors[name] = {
            "max_abs_error": float(error.max()),
            "max_error_budget_fraction": float((error / budget).max()),
        }
    return checks, {
        "reference_dtype": "float64",
        "rtol": rtol,
        "atol": atol,
        "bf16_unit_roundoff": unit_roundoff,
        "max_intermediate_rounding_allowance": float(rounding_allowance.max()),
        "errors": errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    size = 512 if args.profile == "smoke" else 4096
    x = torch.randn(size, size, device="cuda", dtype=torch.bfloat16)
    weight = torch.randn_like(x)
    bias = torch.randn(size, device="cuda", dtype=torch.bfloat16)

    def composed() -> object:
        return torch.relu(x @ weight + bias)

    def library_path() -> object:
        return torch.relu(torch.addmm(bias, x, weight))

    checks, numerics = reference_checks(
        torch,
        x,
        weight,
        bias,
        {"composed": composed(), "library_addmm": library_path()},
    )
    rows = {
        name: summarize_ms(
            cuda_times_ms(
                torch, operation, warmup=args.warmup, iterations=args.iterations
            )
        )
        for name, operation in (("composed", composed), ("library_addmm", library_path))
    }
    target = write_result(
        args,
        lab_id="16_library_first_decision",
        environment=environment,
        measurements={
            "shape": [size, size],
            "numerics": numerics,
            "timing": rows,
            "decision_order": [
                "framework configuration",
                "compiler",
                "maintained library",
                "custom kernel",
            ],
        },
        correctness=checks,
    )
    print(f"Wrote library-first evidence: {target}")


if __name__ == "__main__":
    main()
