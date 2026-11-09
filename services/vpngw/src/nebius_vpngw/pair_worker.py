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
from .deploy.operator_keys import read_private_file
from .deploy.replacement import metadata_only

ENV = "VPNGW_PAIR_CHILD_REQUEST"
SCHEMA = "nebius-vpngw.pair-child.v1"


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
            }
            or request["schema"] != SCHEMA
            or request["side"] not in pair_config.SIDES
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
            raw = read_private_file(release, required=False)
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
        if child.request["approval"]:
            arguments += ["--approve-disruption", child.request["approval"]]
        try:
            get_command(app).main(arguments, standalone_mode=False)
            code = 0
        except typer.Exit as error:
            code = error.exit_code
        result = child.record["result"] or {}
        if code == 0 and result.get("outcome") in {"applied", "resumed", "unchanged"}:
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
                        raise RuntimeError(
                            "Paired connection readiness was not independently verified: "
                            + child.record["first_readiness_failure"]["phase"]
                            + "/"
                            + child.record["first_readiness_failure"]["predicate"]
                        ) from None
                    time.sleep(2)
            child.record["phase"] = "complete"
        else:
            child.record["phase"] = "failed"
            code = 1
    except BaseException:
        child.record["phase"] = "failed"
        raise
    finally:
        child.save()
        pair_phases.CURRENT.reset(token)
        os.close(child.request["lock_fd"])
    raise SystemExit(code)
