"""Compare eager and compiled execution with dtype, residency, and work fixed."""

from __future__ import annotations

import argparse
import statistics
import time
from typing import Any, Callable

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    size = 2_048 if args.profile == "smoke" else 8_192
    device_a = torch.randn((size, size), device="cuda", dtype=torch.bfloat16)
    device_b = torch.randn((size, size), device="cuda", dtype=torch.bfloat16)

    def workload(a: Any, b: Any) -> Any:
        return torch.tanh(torch.nn.functional.silu(a @ b) + 0.1)

    compiled = torch.compile(workload, fullgraph=True)
    compiled(device_a, device_b)
    torch.cuda.synchronize()

    def eager() -> Any:
        return workload(device_a, device_b)

    def compiled_candidate() -> Any:
        return compiled(device_a, device_b)

    def wall_samples(operation: Callable[[], Any]) -> list[float]:
        samples = []
        for _ in range(args.iterations):
            torch.cuda.synchronize()
            started = time.perf_counter()
            operation()
            torch.cuda.synchronize()
            samples.append((time.perf_counter() - started) * 1_000)
        return samples

    trials = []
    allclose = True
    for trial in range(3):
        operations = (
            (("eager", eager), ("compiled", compiled_candidate))
            if trial % 2 == 0
            else (("compiled", compiled_candidate), ("eager", eager))
        )
        measured: dict[str, dict[str, float]] = {}
        for label, operation in operations:
            measured[label] = {
                **{
                    f"cuda_{key}": value
                    for key, value in summarize_ms(
                        cuda_times_ms(
                            torch,
                            operation,
                            warmup=args.warmup,
                            iterations=args.iterations,
                        )
                    ).items()
                },
                **{
                    f"wall_{key}": value
                    for key, value in summarize_ms(wall_samples(operation)).items()
                },
            }
        eager_output = eager()
        compiled_output = compiled_candidate()
        trial_close = bool(
            torch.allclose(eager_output, compiled_output, rtol=1e-2, atol=1e-2)
        )
        allclose = allclose and trial_close
        eager_median = measured["eager"]["cuda_median_ms"]
        compiled_median = measured["compiled"]["cuda_median_ms"]
        trials.append(
            {
                "trial": trial + 1,
                "execution_order": [label for label, _operation in operations],
                "eager": measured["eager"],
                "compiled": measured["compiled"],
                "observed_baseline_to_compiled_ratio": round(
                    eager_median / compiled_median, 4
                ),
                "outputs_match": trial_close,
            }
        )
    if not allclose:
        raise SystemExit("Compiled capstone output failed the BF16 tolerance.")
    target = write_result(
        args,
        lab_id="09_capstone",
        environment=environment,
        measurements={
            "independent_variable": "torch.compile fullgraph versus eager",
            "controlled_factors": {
                "dtype": "bfloat16",
                "input_residency": "both inputs resident on the same H100",
                "shape": [size, size],
                "operation": "matmul -> SiLU -> add scalar -> tanh",
            },
            "trials": trials,
            "median_observed_ratio_across_trials": round(
                statistics.median(
                    trial["observed_baseline_to_compiled_ratio"] for trial in trials
                ),
                4,
            ),
            "publication_rule": (
                "report this scoped H100 observation only with profiler evidence; "
                "do not generalize it to other shapes or versions"
            ),
        },
        correctness={
            "all_three_trials_match_with_bf16_tolerance": allclose,
            "same_dtype_residency_shape_and_work": True,
        },
    )
    print(f"Completed causal optimization capstone: {target}")


if __name__ == "__main__":
    main()
