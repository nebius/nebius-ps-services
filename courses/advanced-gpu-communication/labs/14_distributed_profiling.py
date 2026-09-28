"""Correlate per-rank traces for independent GEMM and all-reduce work."""

import argparse
import os

from common import (
    add_common_args,
    close_distributed,
    validate_common_args,
    write_result,
)
from course_evidence import evidence_phase
from fabric_common import all_correct, initialize, measure


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    parser.add_argument("--overlap", choices=("off", "on"), default="off")
    parser.add_argument("--torch-trace", action="store_true")
    args = parser.parse_args()
    if args.torch_trace:
        if os.environ.get("COURSE_PROFILE_TOOL", "none") != "none":
            raise SystemExit("Capture PyTorch and Nsight in separate diagnostic runs.")
        os.environ["COURSE_CAPTURE"] = "1"
    validate_common_args(args)
    torch, rank, world, _local, env = initialize(args.seed)
    try:
        size = 1024 if args.profile == "small" else 4096
        elements = (4 if args.profile == "small" else 64) * 2**20 // 4
        x = torch.randn(size, size, device="cuda")
        y = torch.randn_like(x)
        reference = x @ y
        output = torch.empty_like(x)
        values = torch.empty(elements, device="cuda")

        def operation():
            values.fill_(rank + 1)
            with evidence_phase("communication"):
                work = torch.distributed.all_reduce(
                    values, async_op=args.overlap == "on"
                )
            with evidence_phase("independent_compute"):
                torch.mm(x, y, out=output)
            with evidence_phase("wait_for_collective"):
                if work is not None:
                    work.wait()

        times = measure(torch, operation, args.warmup, args.iterations)
        all_correct(
            torch,
            bool(torch.equal(values, torch.full_like(values, world * (world + 1) // 2)))
            and bool(torch.allclose(output, reference, rtol=1e-4, atol=1e-4)),
            "Overlap changed the result",
        )
        if args.torch_trace:
            folder = (
                args.output_dir
                / "14_distributed_profiling"
                / "profiles"
                / ("torch-" + args.run_id)
            )
            folder.mkdir(parents=True, mode=0o700, exist_ok=True)
            # Each rank owns a unique filename; profiling samples are never performance claims.
            trace = folder / f"rank-{rank}.json"
            with torch.profiler.profile(
                activities=[
                    torch.profiler.ProfilerActivity.CPU,
                    torch.profiler.ProfilerActivity.CUDA,
                ],
                schedule=torch.profiler.schedule(wait=1, warmup=1, active=3, repeat=1),
                on_trace_ready=lambda p: p.export_chrome_trace(str(trace)),
                record_shapes=True,
                profile_memory=True,
                with_stack=False,
            ) as profiler:
                for _ in range(5):
                    with torch.profiler.record_function("training_like_step"):
                        operation()
                        torch.cuda.synchronize()
                    profiler.step()
            print(f"Rank {rank} PyTorch trace: {trace}")
        if rank == 0:
            print(
                write_result(
                    args,
                    lab_id="14_distributed_profiling",
                    environment=env,
                    measurements={
                        "matrix_size": size,
                        "elements": elements,
                        "ranks": world,
                        "step": times,
                    },
                    correctness={
                        "collective_exact": True,
                        "matmul_matches_reference": True,
                    },
                )
            )
    finally:
        close_distributed(torch)


if __name__ == "__main__":
    main()
