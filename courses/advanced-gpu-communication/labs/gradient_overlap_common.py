"""BF16 bucket arithmetic and scale-aware checks for Lab 19 and its diagnostic."""

from __future__ import annotations

from typing import Any

GRADIENT_RTOL = 1e-2
GRADIENT_ATOL_FRACTION = 1e-2


def bucket_sizes(profile: str, override: list[int] | None) -> list[int]:
    sizes = (
        override
        if override is not None
        else ([8, 24] if profile == "small" else [64, 192])
    )
    if len(sizes) < 2 or any(size < 1 for size in sizes):
        raise SystemExit("--bucket-mib requires at least two positive sizes")
    return sizes


def make_bucket_seeds(
    torch: Any, sizes: list[int], *, device: str
) -> list[tuple[Any, Any]]:
    pairs = []
    for size_mib in sizes:
        elements = size_mib * 2**20 // 2
        parameter = torch.randn(elements, device=device, dtype=torch.bfloat16)
        values = torch.linspace(0.5, 1.5, elements, device=device, dtype=torch.bfloat16)
        pairs.append((parameter, values))
    return pairs


def bucket_loss(parameters: list[Any], seeds: list[tuple[Any, Any]]) -> Any:
    return sum(
        (parameter * values).float().square().mean()
        for parameter, (_seed, values) in zip(parameters, seeds, strict=True)
    )


def local_gradient_reference(torch: Any, seeds: list[tuple[Any, Any]]) -> list[Any]:
    """Differentiate the actual BF16 product and backward casts without autograd."""
    references = []
    with torch.no_grad():
        for parameter, values in seeds:
            product = (parameter * values).float()
            mean_gradient = (
                torch.ones((), device=product.device, dtype=torch.float32)
                / product.numel()
            )
            product_gradient = (2 * product * mean_gradient).to(torch.bfloat16)
            references.append(product_gradient * values)
    return references


def gradient_checks(torch: Any, reference: Any, observed: Any) -> dict[str, Any]:
    """Return local failure, never raise for malformed gradient bucket structure."""
    checks: dict[str, Any] = {
        "passed": False,
        "all_finite": False,
        "rtol": GRADIENT_RTOL,
        "atol_fraction_of_reference_max": GRADIENT_ATOL_FRACTION,
        "buckets": [],
    }
    if (
        not isinstance(reference, (list, tuple))
        or not isinstance(observed, (list, tuple))
        or not reference
        or len(reference) != len(observed)
    ):
        checks["reason"] = "nonempty matching bucket lists required"
        return checks
    all_finite = True
    for expected, actual in zip(reference, observed, strict=True):
        row: dict[str, Any] = {"passed": False}
        checks["buckets"].append(row)
        if (
            not torch.is_tensor(expected)
            or not torch.is_tensor(actual)
            or expected.shape != actual.shape
            or expected.device != actual.device
            or expected.dtype != torch.bfloat16
            or actual.dtype != torch.bfloat16
            or expected.numel() == 0
        ):
            row["reason"] = "matching nonempty BF16 tensors required"
            all_finite = False
            continue
        row["elements"] = expected.numel()
        if not bool(torch.isfinite(expected).all() and torch.isfinite(actual).all()):
            row["reason"] = "nonfinite gradient"
            all_finite = False
            continue
        expected_fp32, actual_fp32 = expected.float(), actual.float()
        scale = float(expected_fp32.abs().max().item())
        error = float((actual_fp32 - expected_fp32).abs().max().item())
        row.update(
            reference_max_abs=scale,
            max_abs_error=error,
            max_normalized_abs_error=error / scale if scale else None,
            passed=(
                bool(torch.equal(actual_fp32, expected_fp32))
                if scale == 0
                else bool(
                    torch.allclose(
                        actual_fp32,
                        expected_fp32,
                        rtol=GRADIENT_RTOL,
                        atol=GRADIENT_ATOL_FRACTION * scale,
                    )
                )
            ),
        )
    checks["all_finite"] = all_finite
    checks["passed"] = all(row["passed"] for row in checks["buckets"])
    return checks


def require_gradient_consensus(
    torch: Any, checks: dict[str, Any], *, device: str
) -> None:
    verdict = torch.tensor(int(checks["passed"]), device=device, dtype=torch.int32)
    torch.distributed.all_reduce(verdict, op=torch.distributed.ReduceOp.MIN)
    if not bool(verdict.item()):
        raise SystemExit(
            "Gradient buckets failed equivalence or finite checks on a rank."
        )
