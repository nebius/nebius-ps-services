"""Bounded streamed handoff owner; no installed-product imports before repair."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import select
import signal
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

try:
    import _vpngw_ha_repair as repair  # type: ignore[import-not-found]
    import _vpngw_ordinary_operations as ops  # type: ignore[import-not-found]
    import _vpngw_ordinary_remote as remote  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from .. import ha_repair as repair
    from .. import ordinary_operations as ops
    from . import ordinary_remote as remote

MARKER = Path("/etc/nebius-vpngw/vm-ha-enabled")
STAGING = Path("/var/lib/nebius-vpngw/ha-package")
STAGED_CONFIG = Path("/etc/nebius-vpngw/vm-ha-staged")
STARTUP_HELPER = Path("/var/lib/nebius-vpngw/ordinary-startup.py")
SYSTEMD_ROOT = Path("/etc/systemd/system")
PACKAGE_FIELDS = (
    "boot_id",
    "config_sha256",
    "ha_config_digest",
    "files",
    "assets",
    "asset_modes",
    "dependencies",
    "requirements",
    "markers",
    "wheel_tags",
    "python",
    "machine",
    "package_version",
)
LIMIT = 128 * 1024 * 1024


def never_started(value: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    """Prove our publication excluded controller startup throughout its lifetime."""
    if (
        value is None
        or value["purpose"] != "ha-handoff"
        or value["state"] == "complete"
        or value["phase"] not in {"quiesce", "install", "publish"}
    ):
        return False
    binding_path = STAGING / (value["id"] + ".json")
    if not binding_path.exists():
        return False
    binding = repair.read_json(binding_path)
    if (
        binding.get("unstarted") is not True
        or any(
            binding.get(k) != value[k] for k in ("id", "owner", "config", "artifact", "predecessor")
        )
        or any(
            binding.get("ha", {}).get(k) != expected[k]
            for k in ("cluster_id", "node_id", "generation_id")
        )
    ):
        return False
    directory = STAGING / binding["package_digest"]
    plan = repair.read_json(directory / "plan.json")
    if (
        ops.digest(plan) != binding["package_digest"]
        or plan["desired"]["wheel_sha256"] != value["artifact"]
    ):
        return False
    wheel = directory / plan["desired"]["wheel_name"]
    if remote.sha(wheel.read_bytes()) != value["artifact"]:
        return False
    with zipfile.ZipFile(wheel) as archive:
        source = archive.read("nebius_vpngw/ordinary_operations.py")
    startup = (
        b"# nebius-vpngw ordinary startup admission\n"
        + source
        + b"\nif __name__ == '__main__':\n    raise SystemExit(0 if startup_allowed() else 1)\n"
    )
    if STARTUP_HELPER.read_bytes() != startup:
        return False
    condition = (
        b"[Service]\nExecCondition=/usr/bin/python3 -B /var/lib/nebius-vpngw/ordinary-startup.py\n"
    )
    for name in ops.HA_WRITERS:
        path = SYSTEMD_ROOT / (name + ".d") / "ordinary-admission.conf"
        if path.read_bytes() != condition:
            return False
    return True


def receive() -> dict[str, Any]:
    raw = sys.stdin.readline(LIMIT + 1)
    if len(raw.encode()) > LIMIT or not raw.endswith("\n"):
        raise RuntimeError("HA handoff request limit or disconnect")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("HA handoff request malformed")
    return value


def reply(value: dict[str, Any]) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def package_observation(manifest: dict[str, Any]) -> dict[str, Any]:
    observed = remote.inspect(manifest, runtime_admission=False)
    return {key: observed[key] for key in PACKAGE_FIELDS}


def inspect(expected: dict[str, Any]) -> dict[str, Any]:
    journal = ops.Journal().read()
    manager = ops.Manager(time.monotonic() + 60)
    pending = journal is not None and journal["state"] != "complete"
    if pending and (journal["purpose"] != "ha-handoff" or ops.alive(journal["owner"])):
        raise ops.OperationBlocked("ordinary_handoff_owner_unresolved")
    if pending and not ops.handoff_recoverable(journal, manager):
        raise ops.OperationBlocked("ordinary_handoff_effect_unresolved")
    if MARKER.exists():
        lock_path = repair.STATE / "apply.lock"
        lock = (
            repair.exact_lock(expected)
            if pending or lock_path.exists() or lock_path.is_symlink()
            else None
        )
        return {
            "journal": ops.digest(journal),
            "pending": pending,
            "environment": None,
            "mode": "ha",
            "lock": lock,
            "boot_id": ops.BOOT.read_text().strip(),
            "config_sha256": remote.sha(remote.CONFIG.read_bytes()),
            "unstarted": never_started(journal, expected),
        }
    if (
        journal is not None
        and journal["state"] == "complete"
        and journal["purpose"] == "ha-handoff"
    ):
        raise ops.OperationBlocked("completed_handoff_marker_missing")
    # A pending handoff can legitimately have partial HA assets. Ordinary unit
    # dependency admission cannot interpret that transition; its owner can.
    if not all(manager.stopped(name) for name in ops.HA_WRITERS):
        raise ops.OperationBlocked("ordinary_handoff_ha_writer_without_marker")
    environment = {"network": manager.network()} if pending else manager.environment()
    return {
        "journal": ops.digest(journal),
        "pending": pending,
        "environment": environment,
        "mode": "ordinary",
        "lock": None,
        "unstarted": False,
        "boot_id": ops.BOOT.read_text().strip(),
        "config_sha256": remote.sha(remote.CONFIG.read_bytes()),
    }


def stage_package(plan: dict[str, Any], payload: dict[str, Any]) -> Path:
    manifest = plan["desired"]
    directory = STAGING / ops.digest(plan)
    items = [(manifest["wheel_name"], payload["wheel"], manifest["wheel_sha256"])]
    wheels = payload["dependency_wheels"]
    if set(wheels) != set(manifest["dependency_wheels"]):
        raise RuntimeError("HA dependency set changed")
    items.extend(
        (name, content, manifest["dependency_wheels"][name]) for name, content in wheels.items()
    )
    for name, content, expected in items:
        path = directory / name
        raw = base64.b64decode(content, validate=True)
        if path.parent != directory or path.suffix != ".whl" or remote.sha(raw) != expected:
            raise RuntimeError("HA package artifact changed")
        repair.publish(path, raw)
    repair.publish(directory / "plan.json", json.dumps(plan, sort_keys=True).encode())
    return directory


def verify_package(manifest: dict[str, Any]) -> dict[str, Any]:
    current = package_observation(manifest)
    if (
        current["files"] != manifest["files"]
        or current["package_version"] != manifest["version"]
        or current["dependencies"] != manifest["expected_dependencies"]
        or current["requirements"] != manifest["expected_requirements"]
    ):
        raise RuntimeError("HA installed package does not match approval")
    # Validate imports in a fresh process only after installation. This exercises
    # the actual guard/controller dependency closure as well as metadata.
    _, output = remote.command(
        [sys.executable, "-B", "-m", "nebius_vpngw.agent.main", "--agent-capabilities"], limit=30
    )
    capability = json.loads(output)
    if capability.get("schema") != "nebius-vpngw.agent-capabilities.v1" or not isinstance(
        capability.get("features"), list
    ):
        raise RuntimeError("HA installed capabilities unverified")
    remote.command(
        [
            sys.executable,
            "-B",
            "-c",
            "import cffi,cryptography; from cryptography.hazmat.primitives.asymmetric import ec",
        ],
        limit=30,
    )
    # fsync every verified installed product file, and its directory, before boot
    # exclusion is relaxed. Dependencies are synced through their RECORD inventory.
    from importlib import metadata

    names = {"nebius-vpngw"}
    for name in manifest["dependency_wheels"]:
        names.add(name.split("-", 1)[0].replace("_", "-"))
    for name in names:
        dist = metadata.distribution(name)
        members = manifest["files"] if name == "nebius-vpngw" else dist.files
        if not members:
            raise RuntimeError("HA installed package durability inventory is missing")
        for member in members:
            path = Path(str(dist.locate_file(member)))
            if path.is_file():
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                repair.sync_directory(path.parent)
    return {
        "schema": "nebius-vpngw/vm-ha-package-v1",
        "package_version": manifest["version"],
        "artifact_sha256": manifest["wheel_sha256"],
        "capabilities": capability["features"],
    }


class Holder:
    def __init__(self, request: dict[str, Any], operation: ops.Operation):
        self.request, self.operation = request, operation
        self.plan = request.get("package")
        self.directory: Path | None = None
        self.prepared = False
        self.repairing = request["observed"].get("mode") == "ha"

    def check(self) -> None:
        value = self.operation.check()
        if value["purpose"] != "ha-handoff" or value["predecessor"] != self.request["approval"]:
            raise RuntimeError("HA handoff binding changed")

    def verify_prepared_predecessor(self) -> None:
        assert self.plan is not None
        manifest = self.plan["desired"]
        expected = json.loads(json.dumps(self.plan["predecessor"]))
        expected.update(
            files=manifest["files"],
            package_version=manifest["version"],
            dependencies=manifest["expected_dependencies"],
            requirements=manifest["expected_requirements"],
        )
        for path, item in self.request.get("owned_assets", {}).items():
            expected["assets"][path] = item["sha256"]
            expected["asset_modes"][path] = [item["mode"], 0, 0]
        if package_observation(manifest) != expected:
            raise RuntimeError("HA prepared predecessor changed")

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.check()
        if (
            self.prepared
            or self.plan is None
            or payload.get("plan_digest") != ops.digest(self.plan)
        ):
            raise RuntimeError("HA handoff package request changed")
        manifest = self.plan["desired"]
        if manifest["wheel_sha256"] != self.request["artifact"]:
            raise RuntimeError("HA handoff artifact binding changed")
        self.directory = STAGING / ops.digest(self.plan)
        if repair.read_json(self.directory / "plan.json") != self.plan:
            raise RuntimeError("HA staged package plan changed")
        self.operation.phase("install")
        remote.OPERATION = self.operation
        remote.DEADLINE = time.monotonic() + 600
        self.operation.manager.deadline = remote.DEADLINE - 60
        try:
            scope = (
                repair.writer_locks(deadline=time.monotonic() + 30)
                if self.repairing
                else remote.routing_lock()
            )
            with scope:
                self.check()
                if not all(
                    self.operation.manager.stopped(name)
                    for name in (*ops.MANAGEMENT, *(ops.HA_WRITERS if self.repairing else ()))
                ):
                    raise RuntimeError("HA package writer is not stopped")
                if self.repairing:
                    self.require_repair(payload)
                predecessor = json.loads(json.dumps(self.plan["predecessor"]))
                for path, item in self.request.get("owned_assets", {}).items():
                    predecessor["assets"][path] = item["sha256"]
                    predecessor["asset_modes"][path] = [item["mode"], 0, 0]
                if package_observation(manifest) != predecessor:
                    raise RuntimeError("HA package predecessor changed after approval")
                for name, digest in {
                    manifest["wheel_name"]: manifest["wheel_sha256"],
                    **manifest["dependency_wheels"],
                }.items():
                    repair.verified_file(self.directory / name, digest)
                dependencies = [
                    str(self.directory / name) for name in manifest["dependency_wheels"]
                ]
                if dependencies:
                    remote.command(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            remote.DEPENDENCY_INSTALL_SCRIPT,
                            *dependencies,
                        ],
                        limit=240,
                    )
                if (
                    self.plan["predecessor"]["files"] != manifest["files"]
                    or self.plan["predecessor"]["package_version"] != manifest["version"]
                ):
                    remote.command(
                        [
                            sys.executable,
                            "-B",
                            "-m",
                            "pip",
                            "install",
                            "--no-index",
                            "--no-deps",
                            "--force-reinstall",
                            "--break-system-packages",
                            str(self.directory / manifest["wheel_name"]),
                        ],
                        limit=240,
                    )
                receipt = verify_package(manifest)
                if not self.repairing:
                    self.publish_assets(manifest["ordinary_assets"])
                self.prepared = True
                self.operation.phase("publish")
                return receipt
        finally:
            remote.OPERATION = None

    def require_repair(self, payload: dict[str, Any]) -> None:
        local = repair.evidence(
            self.request["repair"], unstarted=self.request["observed"].get("unstarted", False)
        )
        peer = payload.get("peer")
        if (
            not isinstance(peer, dict)
            or peer != self.request.get("peer")
            or peer.get("lock", {}).get("operation_id") != local["lock"]["operation_id"]
            or peer.get("lock", {}).get("generation_id") != local["lock"]["generation_id"]
            or peer.get("lock", {}).get("cluster_id") != local["lock"]["cluster_id"]
            or peer.get("lock", {}).get("node_id") == local["lock"]["node_id"]
            or peer.get("forwarding") is not False
        ):
            raise RuntimeError("HA peer repair admission changed")

    def resume_repair(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Finish target repair before the lifecycle may update the untouched peer."""
        self.check()
        if not self.repairing or not self.prepared or self.plan is None:
            raise RuntimeError("HA package repair is not prepared")
        self.require_repair(payload)
        remote.OPERATION = self.operation
        remote.DEADLINE = time.monotonic() + 600
        self.operation.manager.deadline = remote.DEADLINE - 60
        try:
            with repair.writer_locks(deadline=time.monotonic() + 30):
                self.require_repair(payload)
                self.verify_prepared_predecessor()
                if (
                    remote.sha(remote.CONFIG.read_bytes())
                    != self.plan["predecessor"]["config_sha256"]
                ):
                    raise RuntimeError("HA repair configuration changed")
                verify_package(self.plan["desired"])
                self.publish_assets(self.plan["desired"]["assets"])
                repair.initialize_unstarted_checkpoint(
                    unstarted=self.request["observed"].get("unstarted", False)
                )
                self.operation.phase("activate")
            # Canonical service startup owns HA reconciliation; no lock needed by
            # its hooks is held while the manager runs those hooks.
            self.operation.reload_manager()
            self.operation.service("restart", ops.HA_GUARD)
            remote.command(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "nebius_vpngw.agent.main",
                    "--vm-ha-auto-healing-action",
                    "initialize",
                ],
                limit=60,
            )
            for name in reversed(ops.HA_WRITERS):
                self.operation.service("start", name)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                _, raw = remote.command(
                    [sys.executable, "-B", "-m", "nebius_vpngw.agent.main", "--vm-ha-status"],
                    limit=30,
                )
                status = json.loads(raw)
                expected = self.request["repair"]
                if (
                    all(
                        status.get(k) == expected[k]
                        for k in ("cluster_id", "node_id", "generation_id")
                    )
                    and status.get("data_plane_mode") == "passive"
                    and status.get("apply_locked") is True
                    and status.get("apply_operation_id") == expected["operation_id"]
                    and status.get("pending_operation_id") is None
                    and status.get("guard_boot_id") == ops.BOOT.read_text().strip()
                ):
                    repair.evidence(expected)
                    return status
                time.sleep(0.1)
            raise RuntimeError("HA repair locked-passive status was not confirmed")
        finally:
            remote.OPERATION = None

    def publish_assets(self, assets: dict[str, Any]) -> None:
        assert self.directory is not None and self.plan is not None
        manifest = self.plan["desired"]
        wheel = self.directory / manifest["wheel_name"]
        if remote.sha(wheel.read_bytes()) != manifest["wheel_sha256"]:
            raise RuntimeError("HA staged artifact changed")
        with zipfile.ZipFile(wheel) as archive:
            for path, item in assets.items():
                raw = archive.read(item["member"])
                if remote.sha(raw) != item["sha256"]:
                    raise RuntimeError("HA service asset changed")
                repair.publish(Path(path), raw, item["mode"])

    def activate(self, payload: dict[str, Any]) -> None:
        self.check()
        if not self.prepared or self.plan is None:
            raise RuntimeError("HA package preparation is incomplete")
        if self.repairing:
            self.require_repair(payload)
        manifest = self.plan["desired"]
        expected = dict(self.request["ha"], operation_id=payload["operation_id"])
        repair.exact_lock(expected)
        receipt = payload["receipt"]
        if any(receipt[k] != expected[k] for k in ("node_id", "generation_id")):
            raise RuntimeError("HA staged configuration identity changed")
        staged = STAGED_CONFIG / (expected["generation_id"] + ".yaml")
        raw = repair.verified_file(staged, receipt["staged_file_sha256"])
        credentials = Path(receipt["nebius_credentials_path"])
        repair.verified_file(credentials, receipt["nebius_credentials_sha256"])
        remote.OPERATION = self.operation
        remote.DEADLINE = time.monotonic() + 600
        self.operation.manager.deadline = remote.DEADLINE - 60
        try:
            with repair.writer_locks(deadline=time.monotonic() + 30):
                self.check()
                if not all(self.operation.manager.stopped(name) for name in ops.HA_WRITERS):
                    raise RuntimeError("HA controller started before activation")
                repair.exact_lock(expected)
                # Sync the existing exact lock and credentials before the marker.
                lock_path = repair.STATE / "apply.lock"
                repair.verified_file(lock_path, remote.sha(lock_path.read_bytes()))
                repair.verified_file(credentials, receipt["nebius_credentials_sha256"])
                self.verify_prepared_predecessor()
                verify_package(manifest)
                repair.publish(remote.CONFIG, raw)
                # Keep management Wants disabled until every fencing prerequisite
                # and the independent cold guard are durable.
                management = set(manifest["ordinary_assets"])
                self.publish_assets(
                    {p: v for p, v in manifest["assets"].items() if p not in management}
                )
                repair.persist_cold_guard(
                    getattr(repair, "_SOURCE", None) or Path(repair.__file__).read_bytes()
                )
                repair.cold_guard(state_dir=repair.STATE, boot_id=ops.BOOT.read_text().strip())
                repair.initialize_unstarted_checkpoint(unstarted=not self.repairing)
                repair.publish(MARKER, b"")
                self.publish_assets(
                    {p: v for p, v in manifest["assets"].items() if p in management}
                )
                self.operation.phase("activate")
        finally:
            remote.OPERATION = None


def main() -> None:
    def expired(_signum, _frame):
        raise TimeoutError("HA handoff deadline")

    for signum in (signal.SIGALRM, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, expired)
    signal.alarm(630)
    request = receive()
    remote.DEADLINE = time.monotonic() + 600
    if request["action"] == "inspect":
        reply(inspect(request.get("ha", {})))
        return
    if request["action"] == "repair-evidence":
        reply(repair.evidence(request["repair"]))
        return
    lock = os.open(remote.LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    operation = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        observed = inspect(request["ha"])
        if observed != request["observed"]:
            raise RuntimeError("HA handoff predecessor changed")
        old = ops.Journal().read()
        if old and old["state"] != "complete" and old["config"] != request["binding"]:
            raise RuntimeError("HA handoff target changed")
        manager = ops.Manager(time.monotonic() + 180)
        if request.get("package") is not None:
            package = request["package"]
            if (
                package["desired"]["wheel_sha256"] != request["artifact"]
                or package_observation(package["desired"]) != package["predecessor"]
            ):
                raise RuntimeError("HA handoff package approval changed before reservation")
            directory = stage_package(package, request["staged_package"])
            if observed["mode"] == "ordinary":
                # Unit-file writes are inert for currently running writers. Make
                # their dependency-free restart form durable before exclusion:
                # a reboot must not pull dataplane Wants around ExecCondition.
                assets = package["desired"]["ordinary_assets"]
                with zipfile.ZipFile(directory / package["desired"]["wheel_name"]) as archive:
                    for path, item in assets.items():
                        raw = archive.read(item["member"])
                        if remote.sha(raw) != item["sha256"]:
                            raise RuntimeError("HA handoff ordinary asset changed")
                        repair.publish(Path(path), raw, item["mode"])
                request["owned_assets"] = assets
        repairing = observed["mode"] == "ha"
        ops.install_startup_guard(ha_repair=True)
        if repairing:
            repair.persist_cold_guard(
                getattr(repair, "_SOURCE", None) or Path(repair.__file__).read_bytes()
            )
        with remote.routing_lock():
            new = dict(
                schema=ops.SCHEMA,
                purpose="ha-handoff",
                id=os.urandom(16).hex(),
                owner=ops.identity(),
                config=request["binding"],
                artifact=request["artifact"],
                predecessor=request["approval"],
                state="active",
                phase="quiesce",
                effects=[],
                network=manager.network(),
            )
            if request.get("package") is not None:
                durable = {k: new[k] for k in ("id", "owner", "config", "artifact", "predecessor")}
                durable.update(
                    ha=request["ha"],
                    package_digest=ops.digest(request["package"]),
                    unstarted=not repairing or observed.get("unstarted", False),
                )
                repair.publish(STAGING / (new["id"] + ".json"), json.dumps(durable).encode())
            ops.Journal().write(new, expected=ops.digest(old))
            operation = ops.Operation(new, manager)
        for name in (*(ops.HA_WRITERS if repairing else ()), *ops.MANAGEMENT):
            if not manager.unit(name).get("missing"):
                operation.service("stop", name, timeout=45)
            if not manager.stopped(name):
                raise RuntimeError("HA handoff writer did not stop")
        if repairing:
            with repair.writer_locks(deadline=time.monotonic() + 30):
                repair.exact_lock(request["repair"])
                repair.cold_guard(state_dir=repair.STATE, boot_id=ops.BOOT.read_text().strip())
                repair.evidence(request["repair"], unstarted=observed.get("unstarted", False))
        holder = Holder(request, operation)
        reply({"ready": True})
        deadline = time.monotonic() + 3600
        while True:
            signal.alarm(max(1, int(deadline - time.monotonic())))
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
                raise RuntimeError("HA handoff deadline")
            action = receive()
            signal.alarm(min(600, max(1, int(deadline - time.monotonic()))))
            if action["action"] == "prepare-package":
                reply(holder.prepare(action))
            elif action["action"] == "publish-activation":
                holder.activate(action)
                reply({"published": True})
            elif action["action"] == "resume-repair":
                reply(holder.resume_repair(action))
            elif action["action"] == "complete":
                if (
                    not holder.prepared
                    or not MARKER.exists()
                    or operation.value["phase"] not in {"activate", "verify"}
                ):
                    raise RuntimeError("HA handoff activation is incomplete")
                repair.exact_lock(dict(request["ha"], operation_id=action["operation"]))
                with remote.routing_lock():
                    operation.complete()
                reply({"complete": True})
                return
            else:
                raise RuntimeError("HA handoff action invalid")
    finally:
        if operation is not None:
            operation.fail()
        os.close(lock)
