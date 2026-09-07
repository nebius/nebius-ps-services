"""Trace compiled fusion and capture a fixed-shape CUDA Graph training step."""

from __future__ import annotations

import argparse
import time

from common import (
    add_common_args,
    cuda_times_ms,
    load_torch,
    require_h100,
    seed_everything,
    sgd_updates_match,
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
    seed_everything(torch, args.seed)
    environment = require_h100(torch)
    width = 256 if args.profile == "smoke" else 1_024
    batch = 64 if args.profile == "smoke" else 256
    x = torch.randn((batch, width), device="cuda", dtype=torch.bfloat16)
    target = torch.randn_like(x)
    base_weight = (
        torch.randn((width, width), device="cuda", dtype=torch.bfloat16) / width**0.5
    )
    base_bias = torch.zeros(width, device="cuda", dtype=torch.bfloat16)

    def expression(value: object, weight: object, bias: object) -> object:
        prediction = torch.nn.functional.gelu(torch.addmm(bias, value, weight))
        return torch.nn.functional.mse_loss(prediction.float(), target.float())

    compiled = torch.compile(expression, fullgraph=True)
    compile_started = time.perf_counter()
    compiled_loss = compiled(x, base_weight, base_bias)
    torch.cuda.synchronize()
    first_compiled_call_ms = (time.perf_counter() - compile_started) * 1_000
    eager_loss = expression(x, base_weight, base_bias)
    expression_close = bool(
        torch.allclose(eager_loss, compiled_loss, rtol=1e-2, atol=1e-2)
    )

    eager_weight = base_weight.detach().clone().requires_grad_(True)
    eager_bias = base_bias.detach().clone().requires_grad_(True)
    graph_weight = base_weight.detach().clone().requires_grad_(True)
    graph_bias = base_bias.detach().clone().requires_grad_(True)
    eager_optimizer = torch.optim.SGD(
        [eager_weight, eager_bias], lr=1e-3, foreach=False
    )
    graph_optimizer = torch.optim.SGD(
        [graph_weight, graph_bias], lr=1e-3, foreach=False
    )

    def training_step(weight: object, bias: object, optimizer: object) -> object:
        optimizer.zero_grad(set_to_none=False)
        loss = expression(x, weight, bias)
        loss.backward()
        optimizer.step()
        return loss

    warm_weight = base_weight.detach().clone().requires_grad_(True)
    warm_bias = base_bias.detach().clone().requires_grad_(True)
    warm_optimizer = torch.optim.SGD([warm_weight, warm_bias], lr=1e-3, foreach=False)
    warm_stream = torch.cuda.Stream()
    warm_stream.wait_stream(torch.cuda.current_stream())
    with torch.cuda.stream(warm_stream):
        for _ in range(max(3, args.warmup)):
            training_step(warm_weight, warm_bias, warm_optimizer)
    torch.cuda.current_stream().wait_stream(warm_stream)

    graph_weight.grad = torch.zeros_like(graph_weight)
    graph_bias.grad = torch.zeros_like(graph_bias)
    graph = torch.cuda.CUDAGraph()
    with torch.cuda.graph(graph):
        graph_loss = training_step(graph_weight, graph_bias, graph_optimizer)
    with torch.no_grad():
        graph_weight.copy_(base_weight)
        graph_bias.copy_(base_bias)
    graph_weight.grad.zero_()
    graph_bias.grad.zero_()

    reference_loss = training_step(eager_weight, eager_bias, eager_optimizer)
    graph.replay()
    torch.cuda.synchronize()
    step_close = bool(
        torch.allclose(reference_loss, graph_loss, rtol=1e-2, atol=1e-2)
        and sgd_updates_match(
            torch,
            (base_weight, base_bias),
            (eager_weight, eager_bias),
            (graph_weight, graph_bias),
            learning_rate=1e-3,
        )
    )
    if not expression_close or not step_close:
        raise SystemExit("Compiled or CUDA Graph training path failed equivalence.")

    eager_step_timing = summarize_ms(
        cuda_times_ms(
            torch,
            lambda: training_step(eager_weight, eager_bias, eager_optimizer),
            warmup=args.warmup,
            iterations=args.iterations,
        )
    )
    graph_step_timing = summarize_ms(
        cuda_times_ms(
            torch,
            graph.replay,
            warmup=args.warmup,
            iterations=args.iterations,
        )
    )
    with torch.profiler.profile(
        activities=[
            torch.profiler.ProfilerActivity.CPU,
            torch.profiler.ProfilerActivity.CUDA,
        ]
    ) as profile:
        graph.replay()
        torch.cuda.synchronize()
    dispatch = sorted(
        event.key
        for event in profile.key_averages()
        if "cuda" in event.key.lower() or "gemm" in event.key.lower()
    )[:20]

    target_path = write_result(
        args,
        lab_id="27_fused_graph_trace",
        environment=environment,
        measurements={
            "shape": [batch, width],
            "compiled_first_call_ms": round(first_compiled_call_ms, 4),
            "compile_fullgraph_required": True,
            "cuda_graph_scope": "fixed-shape forward, backward, zero-grad, and SGD update",
            "eager_training_step": eager_step_timing,
            "cuda_graph_training_step": graph_step_timing,
            "profile_dispatch_keys": dispatch,
        },
        correctness={
            "compiled_expression_close": expression_close,
            "captured_next_update_close": step_close,
        },
    )
    print(f"Wrote fusion/CUDA-Graph evidence: {target_path}")


if __name__ == "__main__":
    main()
