"""One pair writer coordinating independent existing regional apply owners."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import pair_config, pair_worker
from .agent.vm_ha.store import atomic_write_json
from .apply_approval import digest
from .deploy.operator_keys import protected_directory, publish_new, read_private_file

PHASES = {"starting": 0, "prepared": 1, "originating": 2, "verifying": 3, "complete": 4}


@contextlib.contextmanager
def writer(manifest: Path):
    pair_config.load(manifest)
    fd = os.open(manifest.parent / ".pair.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "A paired deployment or its regional child is still running"
            ) from None
        yield fd
    finally:
        os.close(fd)


def command() -> list[str]:
    return (
        [sys.executable]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "nebius_vpngw"]
    )


def environment(manifest: dict, side: str) -> dict[str, str]:
    """Select regional credentials without mutating the coordinator's environment."""
    result = dict(os.environ)
    result.pop(pair_worker.ENV, None)
    profile = manifest["operator_profiles"][side]
    if profile is not None:
        result["NEBIUS_PROFILE"] = profile
        # Explicit profile selection must not be shadowed by another operator's
        # token. The ordinary renewable CLI bearer remains the credential owner.
        result.pop("NEBIUS_IAM_TOKEN", None)
    return result


def artifact() -> Path:
    from .deploy.ssh_push import SSHPush

    # Use the existing read-only admission owner. Each child still validates its
    # own exact package closure and capabilities before installation.
    return SSHPush().resolve_vm_ha_agent_artifact().path


def release_for(run: str, phase: str, observations: dict[str, dict]) -> dict | None:
    """Completed child evidence can satisfy a barrier on unchanged reapply."""
    if set(observations) != set(pair_config.SIDES):
        return None
    receipts = {}
    for side, observation in observations.items():
        if observation.get("run") != run or observation.get("side") != side:
            raise RuntimeError("Regional pair evidence belongs to a different invocation")
        current = observation.get("phase")
        if current == "failed":
            raise RuntimeError(f"Region {side} failed; inspect its retained apply evidence")
        if current not in PHASES:
            raise RuntimeError("Malformed regional pair phase")
        if PHASES[current] < PHASES[phase]:
            return None
        if current in {"verifying", "complete"} and (observation.get("result") or {}).get(
            "outcome"
        ) not in {
            "applied",
            "resumed",
            "unchanged",
        }:
            raise RuntimeError("Regional exit lacks a verified owner result")
        receipt = observation.get("receipts", {}).get(phase)
        receipts[side] = digest(receipt) if receipt is not None else digest(observation["result"])
    return {"run": run, "receipts": receipts}


def _stop(children: dict[str, subprocess.Popen]) -> None:
    def signal_group(child: subprocess.Popen, selected: signal.Signals) -> None:
        # Exit can race poll()/wait(); a reaped child needs no further signal.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, selected)

    running = [child for child in children.values() if child.poll() is None]
    for child in running:
        signal_group(child, signal.SIGINT)
    deadline = time.monotonic() + 45
    for child in running:
        try:
            child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            signal_group(child, signal.SIGTERM)
    for child in running:
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            signal_group(child, signal.SIGKILL)
            child.wait(timeout=10)


def apply(manifest_path: Path, *, approvals: dict[str, str | None], timeout: int = 1800) -> dict:
    manifest_path = manifest_path.expanduser().absolute()
    if not 300 <= timeout <= 7200:
        raise ValueError("Pair timeout must be between 300 and 7200 seconds")
    with writer(manifest_path) as lock_fd:
        manifest, configs = pair_config.load(manifest_path, resolved=True)
        identity = {
            "pair": manifest["identity"],
            "configurations": {side: digest(configs[side]) for side in pair_config.SIDES},
            "operator_profiles": manifest["operator_profiles"],
        }
        selected = artifact()
        wheel = selected.read_bytes()
        wheel_digest = hashlib.sha256(wheel).hexdigest()
        identity["artifact"] = wheel_digest
        state_path = manifest_path.parent / ".pair-apply.json"
        saved = read_private_file(state_path, required=False)
        previous = json.loads(saved) if saved is not None else None
        if previous and previous["phase"] != "complete" and previous["identity"] != identity:
            raise RuntimeError(
                "Interrupted pair apply requires its exact configurations and artifact"
            )
        run = uuid.uuid4().hex
        directory = manifest_path.parent / ".pair-runs" / run
        protected_directory(directory)
        pinned = directory / selected.name
        publish_new(pinned, wheel)
        state = {
            "schema": "nebius-vpngw.pair-apply.v1",
            "identity": identity,
            "run": run,
            "phase": "pending",
            "previous": previous["run"] if previous else None,
            "children": {},
        }
        atomic_write_json(state_path, state)
        children: dict[str, subprocess.Popen] = {}
        try:
            for side in pair_config.SIDES:
                request = {
                    "schema": pair_worker.SCHEMA,
                    "run": run,
                    "side": side,
                    "manifest": str(manifest_path),
                    "configuration": identity["configurations"][side],
                    "identity": manifest["identity"],
                    "profile": manifest["operator_profiles"][side],
                    "parent": os.getpid(),
                    "lock_fd": lock_fd,
                    "approval": approvals.get(side),
                    "timeout": timeout,
                    "artifact": wheel_digest,
                }
                path = directory / (side + ".request.json")
                publish_new(path, (json.dumps(request, sort_keys=True) + "\n").encode())
                child_environment = environment(manifest, side)
                child_environment.update(
                    {
                        pair_worker.ENV: str(path),
                        "PYTHONUNBUFFERED": "1",
                        "VPNGW_AGENT_WHEEL": str(pinned),
                    }
                )
                fd = os.open(
                    directory / (side + ".log"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(fd, "wb") as output:
                    children[side] = subprocess.Popen(
                        command(),
                        env=child_environment,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        pass_fds=(lock_fd,),
                        start_new_session=True,
                    )
            deadline = time.monotonic() + timeout
            released = set()
            shown: dict[str, str] = {}
            while True:
                observations = {}
                for side, child in children.items():
                    from .deploy.operator_keys import PrivateFileChanged

                    try:
                        raw = read_private_file(directory / (side + ".status.json"), required=False)
                    except PrivateFileChanged:
                        # Child status is atomically replaced during progress.
                        # Discard this observation; it cannot release a barrier.
                        raw = None
                    if raw is not None:
                        observations[side] = json.loads(raw)
                    code = child.poll()
                    if code == 0:
                        # Poll may observe exit after the earlier progress read.
                        # This child's terminal writer is now quiescent.
                        observations[side] = json.loads(
                            read_private_file(directory / (side + ".status.json")) or b"null"
                        )
                    if code is not None and (
                        code != 0 or observations.get(side, {}).get("phase") != "complete"
                    ):
                        raise RuntimeError(
                            f"Region {side} did not complete; inspect {directory / (side + '.log')}"
                        )
                phases = {side: item["phase"] for side, item in observations.items()}
                if phases != shown:
                    print(
                        "Pair phases: "
                        + ", ".join(
                            f"{side}={phases.get(side, 'starting')}" for side in pair_config.SIDES
                        ),
                        flush=True,
                    )
                    shown = phases
                    state["children"] = observations
                    atomic_write_json(state_path, state)
                for phase in ("prepared", "originating"):
                    if phase in released:
                        continue
                    release = release_for(run, phase, observations)
                    if release is not None:
                        publish_new(
                            directory / (phase + ".release.json"),
                            (json.dumps(release, sort_keys=True) + "\n").encode(),
                        )
                        released.add(phase)
                if len(observations) == 2 and all(child.poll() == 0 for child in children.values()):
                    # Require the terminal records reread after process exit,
                    # rather than a progress receipt captured before exit.
                    if any(item.get("phase") != "complete" for item in observations.values()):
                        continue
                    if any(
                        observation.get("run") != run
                        or observation.get("phase") != "complete"
                        or observation.get("readiness", {}).get("connection") != "ready"
                        for observation in observations.values()
                    ):
                        raise RuntimeError(
                            "Regional completion lacks independent connection readiness"
                        )
                    state["phase"] = "complete"
                    state["children"] = observations
                    atomic_write_json(state_path, state)
                    return state
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Pair apply deadline expired; regional journals retain resume authority"
                    )
                time.sleep(0.2)
        finally:
            _stop(children)
