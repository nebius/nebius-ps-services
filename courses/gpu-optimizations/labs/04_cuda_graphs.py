"""Capture and replay a fixed-shape inference step with a CUDA Graph."""

from __future__ import annotations

import argparse

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
    width = 2_048 if args.profile == "smoke" else 8_192
    batch = 32 if args.profile == "smoke" else 128
    static_input = torch.randn((batch, width), device="cuda", dtype=torch.bfloat16)
    weight = torch.randn((width, width), device="cuda", dtype=torch.bfloat16)

    def eager_step() -> object:
        return torch.nn.functional.silu(static_input @ weight)

    warmup_stream = torch.cuda.Stream()
    warmup_stream.wait_stream(torch.cuda.current_stream())
    with torch.cuda.stream(warmup_stream):
        for _ in range(max(3, args.warmup)):
            eager_step()
    torch.cuda.current_stream().wait_stream(warmup_stream)
    graph = torch.cuda.CUDAGraph()
    with torch.cuda.graph(graph):
        captured_output = eager_step()

    def replay() -> object:
        graph.replay()
        return captured_output

    eager_samples = cuda_times_ms(
        torch, eager_step, warmup=args.warmup, iterations=args.iterations
    )
    graph_samples = cuda_times_ms(
        torch, replay, warmup=args.warmup, iterations=args.iterations
    )
    reference = eager_step()
    replay()
    correct = bool(torch.allclose(reference, captured_output, rtol=1e-3, atol=1e-3))
    if not correct:
        raise SystemExit("CUDA Graph replay did not match eager execution.")
    target = write_result(
        args,
        lab_id="04_cuda_graphs",
        environment=environment,
        measurements={
            "shape": [batch, width],
            "eager": summarize_ms(eager_samples),
            "cuda_graph": summarize_ms(graph_samples),
        },
        correctness={"allclose": True, "fixed_shape": True},
    )
    print(f"Completed CUDA Graph experiment: {target}")


if __name__ == "__main__":
    main()
