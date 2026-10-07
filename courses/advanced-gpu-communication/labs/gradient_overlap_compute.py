"""Diagnose one local Lab 19 backward kernel; never emit acceptance timing."""

from __future__ import annotations

import argparse
import json
import os

from gradient_overlap_common import (
    bucket_loss,
    bucket_sizes,
    gradient_checks,
    local_gradient_reference,
    make_bucket_seeds,
)
from training_common import load_torch, require_course_gpu, seed_everything


def require_single_process() -> None:
    for name, expected in (
        ("WORLD_SIZE", 1),
        ("LOCAL_WORLD_SIZE", 1),
        ("SLURM_NTASKS", 1),
        ("SLURM_JOB_NUM_NODES", 1),
        ("SLURM_NNODES", 1),
        ("RANK", 0),
        ("LOCAL_RANK", 0),
        ("SLURM_PROCID", 0),
        ("SLURM_LOCALID", 0),
    ):
        value = os.environ.get(name, str(expected))
        if not value.isdecimal() or int(value) != expected:
            raise SystemExit(
                f"Compute companion requires one local process: invalid {name}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=("small", "large"), default="small")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--bucket-mib", type=int, nargs="+", default=None)
    parser.add_argument("--warmup", type=int, default=3)
    args = parser.parse_args()
    if args.warmup < 0:
        parser.error("--warmup must be non-negative")
    sizes = bucket_sizes(args.workload, args.bucket_mib)
    require_single_process()
    torch = load_torch()
    require_course_gpu(torch)
    if torch.cuda.device_count() != 1:
        raise SystemExit("Compute companion requires exactly one visible H100 GPU")
    seed_everything(torch, args.seed)
    seeds = make_bucket_seeds(torch, sizes, device="cuda:0")

    def prepare():
        parameters = [seed.detach().clone().requires_grad_(True) for seed, _ in seeds]
        return parameters, bucket_loss(parameters, seeds)

    for _ in range(args.warmup):
        parameters, loss = prepare()
        loss.backward()
    parameters, loss = prepare()
    torch.cuda.synchronize()
    with torch.cuda.nvtx.range("gradient_backward"):
        loss.backward()
        torch.cuda.synchronize()
    checks = gradient_checks(
        torch,
        local_gradient_reference(torch, seeds),
        [parameter.grad for parameter in parameters],
    )
    print(
        json.dumps(
            {"acceptance_timing": False, "gradient_validation": checks}, allow_nan=False
        )
    )
    if not checks["passed"]:
        raise SystemExit(
            "Local BF16 backward failed its independent gradient reference"
        )


if __name__ == "__main__":
    main()
