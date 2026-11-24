"""Explicit CPU/CUDA lifecycle."""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TypeVar

import torch
import torch.distributed as dist

from nebius_train.config import TrainConfig

T = TypeVar("T")


@dataclass(frozen=True)
class Runtime:
    rank: int
    local_rank: int
    world_size: int
    device: torch.device

    def sum(self, values: list[float]) -> list[float]:
        tensor = torch.tensor(values, dtype=torch.float64, device=self.device)
        if self.world_size > 1:
            dist.all_reduce(tensor)
        return tensor.tolist()

    def primary(self, operation: Callable[[], T]) -> T | None:
        """Propagate rank-zero failure before peers reach the next collective."""
        result = None
        error = None
        try:
            if self.rank == 0:
                result = operation()
        except Exception as exc:
            error = exc
        status = [type(error).__name__ if error else None]
        if self.world_size > 1:
            dist.broadcast_object_list(status, src=0)
        if error:
            raise error
        if status[0]:
            raise RuntimeError(f"Rank zero failed ({status[0]})")
        return result


@contextmanager
def distributed(config: TrainConfig) -> Iterator[Runtime]:
    keys = ("RANK", "LOCAL_RANK", "WORLD_SIZE")
    present = [key in os.environ for key in keys]
    if any(present) and not all(present):
        raise ValueError("torchrun must supply RANK, LOCAL_RANK and WORLD_SIZE together")
    rank, local_rank, size = (int(os.environ[k]) for k in keys) if all(present) else (0, 0, 1)
    if size < 1 or not 0 <= rank < size or local_rank < 0:
        raise ValueError("Invalid distributed rank environment")
    if dist.is_initialized():
        raise RuntimeError("A process group is already active")
    device = torch.device("cpu")
    if config.device == "cuda":
        if not torch.cuda.is_available() or local_rank >= torch.cuda.device_count():
            raise RuntimeError("Requested CUDA device is unavailable")
        torch.cuda.set_device(local_rank)
        device = torch.device("cuda", local_rank)
    try:
        if size > 1:
            dist.init_process_group(
                backend="nccl" if device.type == "cuda" else "gloo",
                timeout=timedelta(seconds=config.ddp_timeout_seconds),
            )
        yield Runtime(rank, local_rank, size, device)
    finally:
        if dist.is_initialized():
            dist.destroy_process_group()
