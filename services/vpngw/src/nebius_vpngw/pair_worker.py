"""Private child entry point: run the ordinary CLI with bounded pair barriers."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from . import pair_config
from .agent.vm_ha.store import atomic_write_json
from .apply_approval import digest
from .deploy import pair_phases
from .deploy.operator_keys import PrivateFileChanged, read_private_file
from .deploy.replacement import metadata_only

ENV = "VPNGW_PAIR_CHILD_REQUEST"
SCHEMA = "nebius-vpngw.pair-child.v2"


class Child:
    def __init__(self, path: Path):
        self.path = path
        self.request = json.loads(read_private_file(path) or b"null")
        request = self.request
        if (
            set(request)
            != {
                "schema",
                "run",
                "side",
                "manifest",
                "configuration",
                "parent",
                "lock_fd",
                "approval",
                "identity",
                "profile",
                "timeout",
                "artifact",
                "repair",
                "repair_scope",
                "inspect_repair",
            }
            or request["schema"] != SCHEMA
            or request["side"] not in pair_config.SIDES
            or type(request["repair"]) is not bool
            or type(request["inspect_repair"]) is not bool
            or request["inspect_repair"]
            and not request["repair"]
            or request["repair"]
            and request["approval"] is not None
        ):
            raise ValueError("Invalid regional pair request")
        manifest, configs = pair_config.load(Path(request["manifest"]), resolved=True)
        record = manifest["deployments"][request["side"]]
        if (
            manifest["identity"] != request["identity"]
            or manifest["operator_profiles"][request["side"]] != request["profile"]
            or digest(configs[request["side"]]) != request["configuration"]
        ):
            raise RuntimeError("Pair child configuration changed")
        if request["profile"] is not None and (
            os.environ.get("NEBIUS_PROFILE") != request["profile"]
            or "NEBIUS_IAM_TOKEN" in os.environ
        ):
            raise RuntimeError("Pair child operator profile differs from its request")
        self.config = Path(request["manifest"]).parent / record["config"]
        self.status = path.with_name(request["side"] + ".status.json")
        self.deadline = time.monotonic() + request["timeout"]
        if (
            hashlib.sha256(Path(os.environ["VPNGW_AGENT_WHEEL"]).read_bytes()).hexdigest()
            != request["artifact"]
        ):
            raise RuntimeError("Pair child artifact changed")
        self.record: dict = {
            "run": request["run"],
            "side": request["side"],
            "phase": "starting",
            "receipts": {},
            "result": None,
        }
        # Holding the same open flock description prevents a new parent from
        # starting while orphaned children still hold regional owner journals.
        inherited = os.fstat(request["lock_fd"])
        lock = (self.config.parent / ".pair.lock").stat()
        if (inherited.st_dev, inherited.st_ino) != (lock.st_dev, lock.st_ino):
            raise RuntimeError("Pair child lost its parent writer exclusion")

    def save(self) -> None:
        metadata_only(self.record)
        atomic_write_json(self.status, self.record)

    def reached(self, phase: str, receipt: dict) -> None:
        if phase not in {"prepared", "originating"}:
            raise ValueError("Unknown regional pair phase")
        if receipt.get("artifact") != self.request["artifact"]:
            raise RuntimeError("Regional owner selected a different paired artifact")
        manifest, configs = pair_config.load(Path(self.request["manifest"]), resolved=True)
        if (
            digest(configs[self.request["side"]]) != self.request["configuration"]
            or manifest["operator_profiles"][self.request["side"]] != self.request["profile"]
        ):
            raise RuntimeError("Pair configuration changed during regional apply")
        self.record["receipts"][phase] = receipt
        self.record["phase"] = phase
        self.save()
        release = self.path.parent / (phase + ".release.json")
        while time.monotonic() < self.deadline:
            if os.getppid() != self.request["parent"]:
                raise RuntimeError(
                    "Pair coordinator stopped; regional owner retains recovery authority"
                )
            try:
                raw = read_private_file(release, required=False)
            except PrivateFileChanged:
                # Publication can still be settling its private file metadata.
                # Discard this read; only fresh exact evidence releases a child.
                raw = None
            if raw is not None:
                value = json.loads(raw)
                if value.get("run") != self.request["run"] or value.get("receipts", {}).get(
                    self.request["side"]
                ) != digest(receipt):
                    raise RuntimeError("Pair barrier release differs from exact child evidence")
                return
            time.sleep(0.2)
        raise RuntimeError("Pair barrier deadline expired")

    def report(self, value: dict) -> None:
        self.record["result"] = value
        self.save()


def main(path: Path) -> None:
    import typer
    from typer.main import get_command

    from .cli import app

    child = Child(path)
    token = pair_phases.CURRENT.set(child)
    code = 1
    try:
        child.save()
        arguments = ["apply", "--local-config-file", str(child.config)]
        if child.request["repair"]:
            arguments.append("--repair")
        if child.request["approval"]:
            arguments += ["--approve-disruption", child.request["approval"]]
        try:
            returned = get_command(app).main(arguments, standalone_mode=False)
            code = returned if isinstance(returned, int) else 0
        except typer.Exit as error:
            code = error.exit_code
        result = child.record["result"] or {}
        if child.request["inspect_repair"] and code == 0 and child.record.get("repair_scope"):
            child.record["phase"] = "complete"
        elif code == 0 and result.get("outcome") in {"applied", "resumed", "unchanged"}:
            from .pair_readiness import ReadinessFailure, observe

            child.record["phase"] = "verifying"
            child.save()
            deadline = min(child.deadline, time.monotonic() + 90)
            while True:
                try:
                    child.record["readiness"] = observe(child.config)
                    break
                except ReadinessFailure as error:
                    failure = error.failure.mapping()
                    child.record.setdefault("first_readiness_failure", failure)
                    child.record["readiness"] = {
                        "connection": "unknown",
                        "connectivity": "unknown",
                        "first_failure": child.record["first_readiness_failure"],
                        "latest_failure": failure,
                    }
                    child.save()
                    if time.monotonic() >= deadline or os.getppid() != child.request["parent"]:
                        raise
                    time.sleep(2)
            child.record["phase"] = "complete"
        else:
            child.record["phase"] = "failed"
            code = code or 1
    except BaseException as error:
        from .deploy.recovery import Invocation, advice

        child.record["phase"] = "failed"
        code = 130 if isinstance(error, KeyboardInterrupt) else 1
        if isinstance(error, (SystemExit, typer.Exit)):
            raw_code = error.code if isinstance(error, SystemExit) else error.exit_code
            code = raw_code if isinstance(raw_code, int) and raw_code != 0 else 1
        verification_failed = "first_readiness_failure" in child.record
        recovery = advice(
            Invocation("apply", child.config, repair=child.request["repair"]), error,
            codes=("verification-failed",) if verification_failed else (),
        )
        result = child.record.get("result") or {}
        # A completed command remains evidence for effects, not for readiness
        # after the independent verifier fails. Retain completed effect receipts.
        if verification_failed:
            result.update(
                outcome="failed", configuration="unknown", local_runtime="unknown",
                authority="unknown", connectivity="unknown", inhibition_retained=None,
                failure=child.record["first_readiness_failure"],
                latest_failure=child.record["readiness"]["latest_failure"],
                next_command=recovery.resume_command,
            )
        result["recovery"] = recovery.mapping()
        child.record["result"] = result
    finally:
        try:
            child.save()
        finally:
            pair_phases.CURRENT.reset(token)
            os.close(child.request["lock_fd"])
    raise SystemExit(code)
