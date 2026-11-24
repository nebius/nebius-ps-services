"""Epoch checkpoints with restricted loading and RNG recovery."""

import os
import random
import tempfile
from pathlib import Path

import numpy as np
import torch
import torch.distributed as dist

from nebius_train.ml.distributed import Runtime


def seed_all(seed: int, deterministic: bool) -> None:
    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(deterministic)
    torch.backends.cudnn.benchmark = not deterministic
    torch.backends.cudnn.deterministic = deterministic


def rng_state() -> dict:
    np_state = np.random.get_state()
    return {
        "python": random.getstate(),
        "torch": torch.get_rng_state(),
        "numpy": [np_state[0], np_state[1].tolist(), *np_state[2:]],
        "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }


def restore_rng(state: dict) -> None:
    random.setstate(state["python"])
    torch.set_rng_state(state["torch"])
    np_state = state["numpy"]
    np.random.set_state((np_state[0], np.asarray(np_state[1], dtype=np.uint32), *np_state[2:]))
    if state["cuda"]:
        torch.cuda.set_rng_state_all(state["cuda"])


def save_checkpoint(
    path: Path,
    *,
    model,
    optimizer,
    scheduler,
    scaler,
    next_epoch: int,
    identity: dict,
    runtime: Runtime,
) -> None:
    local = rng_state()
    states = [local]
    if runtime.world_size > 1:
        states = [None] * runtime.world_size
        dist.all_gather_object(states, local)

    def save():
        fd, name = tempfile.mkstemp(prefix=".checkpoint.", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                torch.save(
                    {
                        "schema": 1,
                        "identity": identity,
                        "next_epoch": next_epoch,
                        "model": model.state_dict(),
                        "optimizer": optimizer.state_dict(),
                        "scheduler": scheduler.state_dict(),
                        "scaler": scaler.state_dict(),
                        "rng": states,
                    },
                    handle,
                )
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    runtime.primary(save)


def load_checkpoint(
    path: Path, *, model, optimizer, scheduler, scaler, identity: dict, runtime: Runtime
) -> int:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Resume requires a regular run-local checkpoint")
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if checkpoint.get("schema") != 1 or checkpoint.get("identity") != identity:
        raise ValueError("Checkpoint does not match this training plan and input artifacts")
    if len(checkpoint["rng"]) != runtime.world_size:
        raise ValueError("Resume requires the original worker count")
    model.load_state_dict(checkpoint["model"])
    optimizer.load_state_dict(checkpoint["optimizer"])
    scheduler.load_state_dict(checkpoint["scheduler"])
    scaler.load_state_dict(checkpoint["scaler"])
    restore_rng(checkpoint["rng"][runtime.rank])
    return int(checkpoint["next_epoch"])
