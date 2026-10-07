"""Private experiment provenance and opt-in NVTX; copied into each course's labs."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

STARTED = time.time()
PARAMETERS: dict[str, Any] = {}


def gpu_family(name: str) -> str:
    """Record the observed full Hopper device, independently of teaching titles."""
    match = re.search(r"\b(H100|H200)\b", name)
    if "MIG" in name.upper() or not match:
        raise ValueError(f"Expected a full H100 or H200; detected {name!r}")
    return "NVIDIA " + match[1]


def allocation_gpu_family() -> str:
    """Observe every full GPU in a one- or two-worker fabric allocation."""
    nodes = int(os.environ.get("SLURM_JOB_NUM_NODES", "0"))
    if not os.environ.get("SLURM_JOB_ID") or nodes not in (1, 2):
        raise ValueError("Require a one- or two-worker fabric allocation")
    output = subprocess.check_output(
        [
            "srun",
            f"--nodes={nodes}",
            f"--ntasks={nodes}",
            "--ntasks-per-node=1",
            "--gpus-per-task=8",
            "nvidia-smi",
            "--query-gpu=name,compute_cap",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        timeout=60,
    )
    rows = [line.split(",") for line in output.splitlines() if line.strip()]
    if len(rows) != nodes * 8 or any(
        len(row) != 2 or row[1].strip() != "9.0" for row in rows
    ):
        raise ValueError("Require observed SM90 identity for every allocated GPU")
    families = {gpu_family(row[0].strip()) for row in rows}
    if len(families) != 1:
        raise ValueError("Mixed GPU families are unsupported")
    return families.pop()


def annotated_operation(operation: Any, name: str) -> Any:
    """The clean path returns the original callable, without a timed wrapper."""
    if os.environ.get("COURSE_CAPTURE", "0") != "1":
        return operation

    def captured(*args: Any, **kwargs: Any) -> Any:
        with evidence_phase(name):
            return operation(*args, **kwargs)

    return captured


def begin_experiment(args: Any) -> None:
    """Keep only scalar experiment controls, never paths, credentials or URLs."""
    global STARTED, PARAMETERS
    STARTED = time.time()
    private = {
        "token",
        "api_key",
        "secret",
        "password",
        "base_url",
        "endpoint",
        "output",
        "output_dir",
        "run_id",
    }
    PARAMETERS = {}
    for key, value in vars(args).items():
        if key in private or key.endswith(
            ("_path", "_dir", "_url", "_secret", "_password")
        ):
            continue
        if (
            isinstance(value, list)
            and len(value) <= 128
            and all(type(v) in (int, float, bool) for v in value)
        ):
            PARAMETERS[key] = value.copy()
            continue
        if type(value) not in (int, float, bool, str, type(None)):
            continue
        if key in {"model", "revision", "checkpoint"} or (
            isinstance(value, str) and (len(value) > 64 or "/" in value or ":" in value)
        ):
            PARAMETERS[key + "_sha256"] = hashlib.sha256(
                str(value).encode()
            ).hexdigest()
        else:
            stored_key = (
                "profile"
                if key == "workload" and value in ("small", "large")
                else key
            )
            PARAMETERS[stored_key] = value


def enrich_result(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema") != "gpu-course-result/v1":
        return payload
    result = dict(payload)
    result.setdefault("profile", os.environ.get("COURSE_WORKLOAD", "small"))
    result.setdefault("seed", None)
    result.setdefault(
        "environment",
        {"runtime_identity": os.environ.get("COURSE_RUNTIME_ID", "unqualified")},
    )
    code = Path(__file__).parent
    source = code / (str(result.get("lab_id", "")) + ".py")
    parameters = dict(PARAMETERS)
    if source.is_file():
        helpers = sorted(p for p in code.glob("*.py") if not p.name[0].isdigit())
        digest = hashlib.sha256()
        for path in [source, *helpers]:
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        parameters["implementation_sha256"] = digest.hexdigest()
    layout = os.environ.get("COURSE_SERVER_LAYOUT")
    if layout is not None:
        if layout not in {"tp8-pp2", "tp16-pp1", "ep16"}:
            raise ValueError("Unknown qualified server layout")
        parameters["server_layout"] = layout
        parameters["server_world_size"] = 16
    result["experiment"] = {
        "started_unix_seconds": STARTED,
        "ended_unix_seconds": time.time(),
        "instrumented": os.environ.get("COURSE_CAPTURE", "0") == "1"
        or os.environ.get("COURSE_PROFILE_TOOL", "none") != "none",
        "parameters": {
            **parameters,
            "world_size": int(os.environ.get("WORLD_SIZE", "1")),
        },
        "slurm_job_id": int(os.environ.get("SLURM_JOB_ID", "0")),
        "rank": int(os.environ.get("RANK", os.environ.get("SLURM_PROCID", "0"))),
    }
    return result


@contextmanager
def evidence_phase(name: str):
    """Host submission region; it does not synchronize or measure GPU execution."""
    if os.environ.get("COURSE_CAPTURE", "0") != "1":
        yield
        return
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("NVTX capture requires a CUDA workload")
    torch.cuda.nvtx.range_push(name)
    try:
        yield
    finally:
        torch.cuda.nvtx.range_pop()


def atomic_result(path: Path, document: str) -> None:
    """Publish a complete immutable file; never expose a half-written JSON result."""
    import secrets

    job = os.environ.get("COURSE_JOB_DIR")
    if job and (not path.resolve().is_relative_to(Path(job).resolve()) or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError("Job evidence must stay inside its own regular output tree")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.parent / ("." + path.name + "." + secrets.token_hex(6))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise SystemExit(
                f"Refusing to overwrite an existing artifact: {path}"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)
