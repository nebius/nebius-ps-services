"""Create labeled PyTorch and NVTX regions for system and kernel profiling."""

from __future__ import annotations

import argparse
import os

from common import (
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
    parser.add_argument("--export-trace", action="store_true")
    parser.add_argument(
        "--external-only",
        action="store_true",
        help="Run NVTX regions without nesting the PyTorch profiler.",
    )
    args = parser.parse_args()
    validate_common_args(args)
    torch = load_torch()
    environment = require_h100(torch)
    seed_everything(torch, args.seed)
    width = 2_048 if args.profile == "smoke" else 8_192
    x = torch.randn((64, width), device="cuda", dtype=torch.bfloat16)
    weight = torch.randn((width, width), device="cuda", dtype=torch.bfloat16)

    def workload() -> object:
        with torch.cuda.nvtx.range("train_step"):
            with torch.cuda.nvtx.range("projection"):
                hidden = x @ weight
            with torch.cuda.nvtx.range("activation_and_reduction"):
                return torch.nn.functional.gelu(hidden).float().square().mean()

    for _ in range(args.warmup):
        workload()
    torch.cuda.synchronize()
    profiler = None
    loss = None
    if args.external_only:
        for _ in range(args.iterations):
            loss = workload()
        torch.cuda.synchronize()
    else:
        with torch.profiler.profile(
            activities=[
                torch.profiler.ProfilerActivity.CPU,
                torch.profiler.ProfilerActivity.CUDA,
            ],
            record_shapes=True,
            profile_memory=True,
        ) as profiler:
            for _ in range(args.iterations):
                loss = workload()
                profiler.step()
    assert loss is not None
    trace_path = None
    if args.export_trace:
        if profiler is None:
            raise SystemExit(
                "--export-trace requires the PyTorch profiler; omit --external-only."
            )
        args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        trace_directory = args.output_dir / f"07-profile-run-{args.run_id}"
        try:
            trace_directory.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise SystemExit(
                f"Refusing to reuse an existing trace directory: {trace_directory}"
            ) from exc
        trace_path = trace_directory / "trace.json"
        profiler.export_chrome_trace(str(trace_path))
        os.chmod(trace_path, 0o600)
    top_cuda = (
        "Captured by the external NVIDIA profiler."
        if profiler is None
        else profiler.key_averages().table(sort_by="self_cuda_time_total", row_limit=8)
    )
    target = write_result(
        args,
        lab_id="07_profile_workload",
        environment=environment,
        measurements={
            "trace_exported": trace_path is not None,
            "top_cuda_table": top_cuda,
        },
        correctness={"finite_loss": bool(torch.isfinite(loss).item())},
    )
    print(top_cuda)
    print(f"Completed profiling workload: {target}")


if __name__ == "__main__":
    main()
