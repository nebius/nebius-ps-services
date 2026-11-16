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
from .deploy.recovery import Invocation, RegionalFailure, WriterBusy, advice

PHASES = {"starting": 0, "prepared": 1, "originating": 2, "verifying": 3, "complete": 4}


@contextlib.contextmanager
def writer(manifest: Path):
    pair_config.load(manifest)
    fd = os.open(manifest.parent / ".pair.lock", os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WriterBusy(
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
            raise RegionalFailure(side, observation.get("result"))
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


def _inspect_repairs(
    directory: Path, requests: dict[str, dict], manifest: dict, pinned: Path, lock_fd: int
) -> dict:
    """Inspect both exact regional scopes under their selected auth profiles."""
    protected_directory(directory)
    children: dict[str, subprocess.Popen] = {}
    try:
        for side, request in requests.items():
            path = directory / (side + ".request.json")
            publish_new(path, (json.dumps({**request, "inspect_repair": True}) + "\n").encode())
            child_env = environment(manifest, side)
            child_env.update(
                {
                    pair_worker.ENV: str(path),
                    "VPNGW_AGENT_WHEEL": str(pinned),
                    "PYTHONUNBUFFERED": "1",
                }
            )
            fd = os.open(directory / (side + ".log"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                children[side] = subprocess.Popen(
                    command(),
                    env=child_env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    pass_fds=(lock_fd,),
                    start_new_session=True,
                )
        deadline = time.monotonic() + 300
        while any(child.poll() is None for child in children.values()):
            for side, child in children.items():
                if child.poll() not in {None, 0}:
                    value = json.loads(read_private_file(
                        directory / (side + ".status.json"), required=False
                    ) or b"null")
                    exact = (
                        isinstance(value, dict)
                        and value.get("run") == requests[side]["run"]
                        and value.get("side") == side
                    )
                    raise RegionalFailure(side, value.get("result") if exact else None)
            if time.monotonic() >= deadline:
                raise TimeoutError("Regional repair inspection timed out before confirmation")
            time.sleep(0.2)
        scopes = {}
        for side, child in children.items():
            value = json.loads(read_private_file(directory / (side + ".status.json")) or b"null")
            if (
                child.returncode
                or not isinstance(value, dict)
                or value.get("run") != requests[side]["run"]
                or value.get("side") != side
                or value.get("phase") != "complete"
                or not value.get("repair_scope")
            ):
                raise RuntimeError(f"Region {side} has no exact repair scope; inspect {directory}")
            scopes[side] = value["repair_scope"]
        return scopes
    finally:
        _stop(children)


def apply(
    manifest_path: Path,
    *,
    approvals: dict[str, str | None],
    timeout: int = 1800,
    repair: bool = False,
) -> dict:
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
        if repair:
            identity["repair"] = True
        state_path = manifest_path.parent / ".pair-apply.json"
        saved = read_private_file(state_path, required=False)
        previous = json.loads(saved) if saved is not None else None
        if (
            previous
            and previous["phase"] != "complete"
            and previous["identity"] != identity
            and not repair
        ):
            raise RuntimeError(
                "Interrupted pair apply requires its exact configurations and artifact"
            )
        run = uuid.uuid4().hex
        directory = manifest_path.parent / ".pair-runs" / run
        protected_directory(directory)
        pinned = directory / selected.name
        publish_new(pinned, wheel)
        requests = {
            side: {
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
                "repair": repair,
                "repair_scope": None,
                "inspect_repair": False,
            }
            for side in pair_config.SIDES
        }
        if repair:
            import typer

            if any(approvals.values()):
                raise ValueError("Pair repair cannot include disruption digests")
            scopes = _inspect_repairs(directory / "inspection", requests, manifest, pinned, lock_fd)
            typer.echo(
                "Full repair will interrupt VPN traffic and rebuild VPNGW packages, configuration and managed node identity in both regions."
            )
            for side, scope in scopes.items():
                for target in scope["targets"]:
                    typer.echo(
                        f"Region {side}: {target['name']}, retained VM {target['compute']}, disk {target['disk']}"
                    )
            typer.echo(
                "Ubuntu, VM/disk/IP identities, SSH access and external peer credentials are retained. Recovery may exceed five minutes."
            )
            if not typer.confirm("Proceed with disruptive repair of both regions?", default=False):
                return {"phase": "cancelled"}
            for side in requests:
                requests[side]["repair_scope"] = scopes[side]
            if saved is not None:
                publish_new(directory / "previous-pair.json", saved)
        state: dict = {
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
                request = requests[side]
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
                    if code is not None:
                        # Poll may observe exit after the earlier progress read.
                        # This child's terminal writer is now quiescent.
                        terminal = read_private_file(directory / (side + ".status.json"), required=False)
                        if terminal is not None:
                            value = json.loads(terminal)
                            if not isinstance(value, dict):
                                raise RegionalFailure(side)
                            observations[side] = value
                    if code is not None and (
                        code != 0 or (observations.get(side) or {}).get("phase") != "complete"
                    ):
                        observed = observations.get(side) or {}
                        if observed.get("run") == run and observed.get("side") == side:
                            state["children"][side] = observed
                            raise RegionalFailure(side, observed.get("result"))
                        raise RegionalFailure(side)
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
                    raise TimeoutError(
                        "Pair apply deadline expired; regional journals retain resume authority"
                    )
                time.sleep(0.2)
        except (Exception, KeyboardInterrupt) as error:
            state["recovery"] = advice(Invocation("pair apply", manifest_path, repair=repair), error).mapping()
            atomic_write_json(state_path, state)
            raise
        finally:
            _stop(children)
