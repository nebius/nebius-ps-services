"""Shared, dependency-light helpers for the GPU Fundamentals labs."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import secrets
import statistics
from pathlib import Path
from typing import IO, Any, Callable


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--profile",
        choices=("smoke", "h100"),
        default="smoke",
        help="Use short correctness sizes or H100-oriented benchmark sizes.",
    )
    parser.add_argument("--seed", type=int, default=17, help="Random seed.")
    parser.add_argument("--warmup", type=int, default=5, help="Untimed warmup steps.")
    parser.add_argument("--iterations", type=int, default=20, help="Timed steps.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results"),
        help="Directory for sanitized JSON results.",
    )


def validate_common_args(args: argparse.Namespace) -> None:
    if args.warmup < 0:
        raise SystemExit("--warmup must be non-negative")
    if args.iterations < 1:
        raise SystemExit("--iterations must be positive")
    os.umask(0o077)
    args.run_id = resolve_run_id(getattr(args, "run_id", None))


def resolve_run_id(value: str | None = None) -> str:
    run_id = value if value is not None else os.environ.get("COURSE_RUN_ID")
    if run_id is None:
        run_id = secrets.token_hex(6)
    if re.fullmatch(r"[0-9a-f]{12}", run_id) is None:
        raise SystemExit("COURSE_RUN_ID must be exactly 12 lowercase hex characters")
    return run_id


def resolve_int_override(
    value: int | None,
    default: int,
    *,
    option: str,
    minimum: int = 1,
) -> int:
    resolved = default if value is None else value
    if resolved < minimum:
        raise SystemExit(f"{option} must be at least {minimum}")
    return resolved


def load_torch() -> Any:
    try:
        import torch
    except ImportError as exc:
        raise SystemExit(
            "PyTorch is required. Follow the course environment setup before running this lab."
        ) from exc
    return torch


def seed_everything(torch: Any, seed: int) -> None:
    random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def require_h100(torch: Any) -> dict[str, Any]:
    if not torch.cuda.is_available():
        raise SystemExit(
            "CUDA is unavailable; this course requires an NVIDIA H100 GPU."
        )
    index = torch.cuda.current_device()
    props = torch.cuda.get_device_properties(index)
    name = props.name
    if (
        "H100" not in name
        or "MIG" in name.upper()
        or (props.major, props.minor) != (9, 0)
    ):
        raise SystemExit(
            f"Expected one full H100 (SM90); detected {name!r} with "
            f"compute capability {props.major}.{props.minor}."
        )
    return {
        "gpu_family": "NVIDIA H100",
        "compute_capability": "9.0",
        "memory_gib": round(props.total_memory / 2**30, 1),
        "torch_version": torch.__version__,
        "cuda_version": torch.version.cuda,
    }


def cuda_times_ms(
    torch: Any,
    operation: Callable[[], Any],
    *,
    warmup: int,
    iterations: int,
) -> list[float]:
    for _ in range(warmup):
        operation()
    torch.cuda.synchronize()
    samples: list[float] = []
    for _ in range(iterations):
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        operation()
        end.record()
        end.synchronize()
        samples.append(float(start.elapsed_time(end)))
    return samples


def summarize_ms(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    p90_index = max(0, min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1))
    return {
        "median_ms": round(statistics.median(ordered), 4),
        "min_ms": round(ordered[0], 4),
        "p90_ms": round(ordered[p90_index], 4),
    }


def write_result(
    args: argparse.Namespace,
    *,
    lab_id: str,
    environment: dict[str, Any],
    measurements: dict[str, Any],
    correctness: dict[str, Any],
) -> Path:
    failed_checks = sorted(key for key, value in correctness.items() if value is False)
    if failed_checks:
        raise SystemExit("Failed correctness checks: " + ", ".join(failed_checks))
    run_id = resolve_run_id(getattr(args, "run_id", None))
    target = args.output_dir / f"{lab_id}-run-{run_id}.json"
    payload = {
        "schema": "gpu-course-result/v1",
        "lab_id": lab_id,
        "profile": args.profile,
        "run_id": run_id,
        "seed": args.seed,
        "environment": environment,
        "measurements": measurements,
        "correctness": correctness,
    }
    write_json_exclusive(target, payload)
    return target


def open_private_exclusive(target: Path, *, binary: bool = False) -> IO[Any]:
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError as exc:
        raise SystemExit(
            f"Refusing to overwrite an existing artifact: {target}"
        ) from exc
    if binary:
        return os.fdopen(descriptor, "wb")
    return os.fdopen(descriptor, "w", encoding="utf-8")


def write_json_exclusive(target: Path, payload: dict[str, Any]) -> Path:
    document = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    with open_private_exclusive(target) as stream:
        stream.write(document)
    return target


def configure_slurm_distributed_env() -> tuple[int, int, int]:
    rank = int(os.environ.get("RANK", os.environ.get("SLURM_PROCID", "0")))
    world_size = int(os.environ.get("WORLD_SIZE", os.environ.get("SLURM_NTASKS", "1")))
    local_rank = int(os.environ.get("LOCAL_RANK", os.environ.get("SLURM_LOCALID", "0")))
    os.environ.setdefault("RANK", str(rank))
    os.environ.setdefault("WORLD_SIZE", str(world_size))
    os.environ.setdefault("LOCAL_RANK", str(local_rank))
    return rank, world_size, local_rank


def init_nccl(torch: Any, *, expected_world_size: int = 2) -> tuple[int, int, int]:
    rank, world_size, local_rank = configure_slurm_distributed_env()
    if world_size != expected_world_size:
        raise SystemExit(
            f"Expected {expected_world_size} distributed ranks; received {world_size}. "
            "Use slurm/two_node.sbatch."
        )
    for required in ("MASTER_ADDR", "MASTER_PORT"):
        if not os.environ.get(required):
            raise SystemExit(f"{required} is missing; use the supplied Slurm launcher.")
    torch.cuda.set_device(local_rank)
    torch.distributed.init_process_group(backend="nccl", init_method="env://")
    return rank, world_size, local_rank


def close_distributed(torch: Any) -> None:
    if torch.distributed.is_available() and torch.distributed.is_initialized():
        torch.distributed.destroy_process_group()
