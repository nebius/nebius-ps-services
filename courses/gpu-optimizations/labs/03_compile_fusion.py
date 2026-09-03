"""Compare eager pointwise work with a torch.compile fused graph."""

from __future__ import annotations

import argparse
import time

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
    elements = 16_000_000 if args.profile == "smoke" else 128_000_000
    x = torch.randn(elements, device="cuda")
    bias = torch.randn(elements, device="cuda")

    def workload(value: object, offset: object) -> object:
        hidden = value * 1.25 + offset
        gated = torch.nn.functional.silu(hidden) * hidden
        return torch.tanh(gated + 0.1)

    compiled = torch.compile(workload, fullgraph=True)
    compile_started = time.perf_counter()
    compiled(x, bias)
    torch.cuda.synchronize()
    first_call_ms = (time.perf_counter() - compile_started) * 1_000
    eager_samples = cuda_times_ms(
        torch,
        lambda: workload(x, bias),
        warmup=args.warmup,
        iterations=args.iterations,
    )
    compiled_samples = cuda_times_ms(
        torch,
        lambda: compiled(x, bias),
        warmup=args.warmup,
        iterations=args.iterations,
    )
    correct = bool(
        torch.allclose(workload(x, bias), compiled(x, bias), rtol=1e-4, atol=1e-5)
    )
    if not correct:
        raise SystemExit("Compiled and eager results diverged.")
    target = write_result(
        args,
        lab_id="03_compile_fusion",
        environment=environment,
        measurements={
            "elements": elements,
            "compiled_first_call_ms": round(first_call_ms, 4),
            "eager": summarize_ms(eager_samples),
            "compiled": summarize_ms(compiled_samples),
        },
        correctness={"allclose": True},
    )
    print(f"Completed compile experiment: {target}")


if __name__ == "__main__":
    main()
