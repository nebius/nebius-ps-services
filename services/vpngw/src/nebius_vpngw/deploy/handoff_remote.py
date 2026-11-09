"""Bounded streamed handoff owner; no installed-product imports before repair."""

from __future__ import annotations

import fcntl
import functools
import hashlib
import json
import os
import select
import signal
import stat
import subprocess
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
    "runtime_plan",
)


def package_exclusion(function):
    """Hold dpkg's native frontend lock for one prepared package phase only."""

    @functools.wraps(function)
    def run(*args, **kwargs):
        if not remote.DURABLE_PACKAGES:
            return function(*args, **kwargs)
        from nebius_vpngw import package_worker

        with package_worker.native_lock("/var/lib/dpkg/lock-frontend"):
            # Direct dpkg writers need not hold the frontend lock. Observe their
            # backend exclusion too, then let dpkg take its own native lock.
            with package_worker.native_lock("/var/lib/dpkg/lock"):
                pass
            previous = os.environ.get("DPKG_FRONTEND_LOCKED")
            try:
                os.environ["DPKG_FRONTEND_LOCKED"] = "1"
                return function(*args, **kwargs)
            finally:
                if previous is None:
                    os.environ.pop("DPKG_FRONTEND_LOCKED", None)
                else:
                    os.environ["DPKG_FRONTEND_LOCKED"] = previous

    return run


LIMIT = 128 * 1024 * 1024


def runtime_routes() -> dict:
    # This is the canonical source reader in the streamed bundle, so an
    # interrupted package install cannot replace it with a partial import.
    from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority

    return RouteAuthority().snapshot()


def reserve_runtime_routes(request: dict) -> None:
    from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority

    expected = request["observed"].get("route_runtime")
    if expected is None or expected["authority"] is None:
        if runtime_routes()["authority"] is not None:
            raise RuntimeError("Ordinary route writer appeared during HA handoff")
        return
    # The migration's immutable target identity survives the reservation write
    # even if the ordinary operation journal has not yet been published.
    holder = ops.digest(["ordinary-to-ha", request["binding"]])
    current = RouteAuthority().reserve(holder, ops.digest(expected))
    if (
        current["journal"] != expected["journal"]
        or current["authority"]["binding"] != expected["authority"]["binding"]
    ):
        raise RuntimeError("Ordinary route receipts changed during HA handoff")


def verify_runtime_routes(request: dict) -> None:
    expected = request["observed"].get("route_runtime")
    current = runtime_routes()
    if expected is None or expected["authority"] is None:
        if current["authority"] is not None:
            raise RuntimeError("Unreviewed runtime route writer during HA handoff")
        return
    authority = current["authority"]
    if (
        authority is None
        or authority["writer"] != "workstation"
        or authority["holder"] != ops.digest(["ordinary-to-ha", request["binding"]])
        or authority["binding"] != expected["authority"]["binding"]
        or current["journal"] != expected["journal"]
    ):
        raise RuntimeError("Ordinary runtime route writer is not excluded during HA handoff")


def retire_runtime_routes(request: dict) -> None:
    """Finish the exact migration reservation while HA remains fenced."""
    from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority

    verify_runtime_routes(request)
    current = runtime_routes()
    if current["authority"] is not None:
        RouteAuthority().retire(
            ops.digest(["ordinary-to-ha", request["binding"]]),
            hashlib.sha256(remote.CONFIG.read_bytes()).hexdigest(),
            current["journal"],
        )


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


def inspect(expected: dict[str, Any], *, inhibited: bool = False) -> dict[str, Any]:
    journal = ops.Journal().read()
    manager = ops.Manager(time.monotonic() + 60)
    pending = journal is not None and journal["state"] != "complete"
    routes = runtime_routes()
    route_transaction = routes["journal"]["transaction"] if routes["journal"] else None
    if route_transaction and route_transaction["phase"] != "complete":
        raise ops.OperationBlocked("ordinary_runtime_route_operation_unresolved")
    if pending and (journal["purpose"] != "ha-handoff" or ops.alive(journal["owner"])):
        raise ops.OperationBlocked("ordinary_handoff_owner_unresolved")
    if pending and not ops.handoff_recoverable(journal, manager, inhibited_quiescence=inhibited):
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
            "route_runtime": routes,
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
        "route_runtime": routes,
    }


def stage_package(plan: dict[str, Any], payload: dict[str, Any]) -> Path:
    manifest = plan["desired"]
    remote.migration.stage_payload(manifest, payload)
    directory = STAGING / ops.digest(plan)
    staged = remote.migration.ROOT / remote.migration.digest(manifest["runtime_plan"]) / "payload"
    items = {manifest["wheel_name"]: manifest["wheel_sha256"], **manifest["dependency_wheels"]}
    for name, expected in items.items():
        path = directory / name
        raw = (staged / name).read_bytes()
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
        self.activation = None
        self.repairing = request["observed"].get("mode") == "ha"
        if self.plan is not None:
            saved = repair.read_json(STAGING / (operation.value["id"] + ".json"))
            self.activation = saved.get("activation")
            if saved.get("prepared") is not None:
                self.directory = STAGING / ops.digest(self.plan)
                self.prepared = True

    def check(self) -> None:
        value = self.operation.check()
        if value["purpose"] != "ha-handoff" or value["predecessor"] != self.request["approval"]:
            raise RuntimeError("HA handoff binding changed")
        verify_runtime_routes(self.request)

    def verify_prepared_predecessor(self) -> None:
        assert self.plan is not None
        manifest = self.plan["desired"]
        expected = json.loads(json.dumps(self.plan["predecessor"]))
        if self.request.get("resuming"):
            expected["boot_id"] = self.operation.check()["owner"]["boot"]
        expected.update(
            files=manifest["files"],
            package_version=manifest["version"],
            dependencies=manifest["expected_dependencies"],
            requirements=manifest["expected_requirements"],
        )
        for path, item in self.request.get("owned_assets", {}).items():
            expected["assets"][path] = item["sha256"]
            expected["asset_modes"][path] = [item["mode"], 0, 0]
        current = package_observation(manifest)
        if self.activation is not None:
            receipt = self.activation["receipt"]
            raw = repair.verified_file(
                STAGED_CONFIG / (self.request["ha"]["generation_id"] + ".yaml"),
                receipt["staged_file_sha256"],
            )
            import yaml

            config = yaml.safe_load(raw)
            config["vm_ha"].pop("runtime_binding", None)
            if current["config_sha256"] not in {
                expected["config_sha256"],
                receipt["staged_file_sha256"],
            }:
                raise RuntimeError("HA published configuration changed")
            if current["ha_config_digest"] not in {
                expected["ha_config_digest"],
                remote.canonical(config),
            }:
                raise RuntimeError("HA published configuration identity changed")
            expected.update(
                config_sha256=current["config_sha256"], ha_config_digest=current["ha_config_digest"]
            )
            for path, item in manifest["assets"].items():
                if current["assets"][path] not in {
                    expected["assets"][path],
                    item["sha256"],
                } or current["asset_modes"][path] not in [
                    expected["asset_modes"][path],
                    [item["mode"], 0, 0],
                ]:
                    raise RuntimeError("HA publication contains an unowned asset")
                expected["assets"][path] = current["assets"][path]
                expected["asset_modes"][path] = current["asset_modes"][path]
        if current != expected:
            raise RuntimeError("HA prepared predecessor changed")

    def convert_checkpoint(self, *, preflight: bool = False) -> None:
        """Caller holds all HA writer locks and has verified the exact package."""
        if not self.repairing:
            return  # First-start activation owns initialization, after cold guard.
        from nebius_vpngw.checkpoint_upgrade import conversion_preflight, convert_checkpoint

        assert self.plan is not None
        expected = dict(self.request["repair"], host_id=self.request["repair"]["compute_id"])
        action = conversion_preflight if preflight else convert_checkpoint
        action(
            expected=expected,
            artifact=self.request["artifact"],
            state_dir=repair.STATE,
            repair=repair,
            boot_id=self.operation.check()["owner"]["boot"],
            unstarted=self.request["observed"].get("unstarted", False),
        )

    @package_exclusion
    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.check()
        if self.plan is None or payload.get("plan_digest") != ops.digest(self.plan):
            raise RuntimeError("HA handoff package request changed")
        if self.prepared:
            return self.verify_prepared(payload)
        manifest = self.plan["desired"]
        if manifest["wheel_sha256"] != self.request["artifact"]:
            raise RuntimeError("HA handoff artifact binding changed")
        self.directory = STAGING / ops.digest(self.plan)
        if repair.read_json(self.directory / "plan.json") != self.plan:
            raise RuntimeError("HA staged package plan changed")
        self.operation.phase("install")
        remote.OPERATION = self.operation
        remote.DEADLINE = float("inf") if remote.DURABLE_PACKAGES else time.monotonic() + 600
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
                    self.convert_checkpoint(preflight=True)
                predecessor = json.loads(json.dumps(self.plan["predecessor"]))
                for path, item in self.request.get("owned_assets", {}).items():
                    predecessor["assets"][path] = item["sha256"]
                    predecessor["asset_modes"][path] = [item["mode"], 0, 0]
                current = package_observation(manifest)
                if not self.request.get("resuming"):
                    if current != predecessor:
                        raise RuntimeError("HA package predecessor changed after approval")
                else:
                    # Native and Python owners validate their prospective exact
                    # write sets. Identity/config/architecture cannot change.
                    if current["boot_id"] != self.operation.check()["owner"]["boot"]:
                        raise RuntimeError("HA resumed package boot authority changed")
                    for key in (
                        "config_sha256",
                        "ha_config_digest",
                        "python",
                        "machine",
                        "wheel_tags",
                        "runtime_plan",
                    ):
                        if current[key] != predecessor[key]:
                            raise RuntimeError("HA resumed package identity changed")
                    remote.migration.verify_inhibited()
                for name, digest in {
                    manifest["wheel_name"]: manifest["wheel_sha256"],
                    **manifest["dependency_wheels"],
                }.items():
                    repair.verified_file(self.directory / name, digest)
                remote.migration.COMMAND = remote.command
                remote.migration.CHECK = self.operation.check
                remote.migration.inhibit()
                for unit in (
                    "strongswan-starter.service",
                    "strongswan-swanctl.service",
                    "strongswan.service",
                ):
                    if not self.operation.manager.unit(unit).get(
                        "missing"
                    ) or not self.operation.manager.stopped(unit):
                        self.operation.service("stop", unit)
                    if not self.operation.manager.stopped(unit):
                        raise RuntimeError("daemon package writer is not quiescent")
                remote.migration.install()
                payload_directory = (
                    remote.migration.verify_staged(manifest["runtime_plan"]) / "payload"
                )
                remote.python_packages.install_payload(
                    manifest, payload_directory, remote.command, self.operation.check
                )
                receipt = verify_package(manifest)
                self.convert_checkpoint()
                if not self.repairing:
                    self.publish_assets(manifest["ordinary_assets"])
                self.prepared = True
                binding_path = STAGING / (self.operation.value["id"] + ".json")
                binding = repair.read_json(binding_path)
                binding["prepared"] = receipt
                repair.publish(binding_path, json.dumps(binding, sort_keys=True).encode())
                self.operation.phase("publish")
                return receipt
        finally:
            remote.OPERATION = None
            remote.migration.COMMAND = remote.migration.CHECK = None

    def require_repair(self, payload: dict[str, Any]) -> None:
        local = repair.evidence(
            self.request["repair"], unstarted=self.request["observed"].get("unstarted", False)
        )
        peer = payload.get("peer")
        if (
            not isinstance(peer, dict)
            or not repair.peer_checkpoint_transition(
                self.request.get("peer"), peer, self.request["artifact"]
            )
            or peer.get("lock", {}).get("operation_id") != local["lock"]["operation_id"]
            or peer.get("lock", {}).get("generation_id") != local["lock"]["generation_id"]
            or peer.get("lock", {}).get("cluster_id") != local["lock"]["cluster_id"]
            or peer.get("lock", {}).get("node_id") == local["lock"]["node_id"]
            or peer.get("forwarding") is not False
        ):
            raise RuntimeError("HA peer repair admission changed")

    def verify_prepared(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Keep old-config writers stopped until canonical desired activation."""
        self.check()
        if not self.prepared or self.plan is None:
            raise RuntimeError("HA package repair is not prepared")
        with repair.writer_locks(deadline=time.monotonic() + 30):
            if self.repairing:
                self.require_repair(payload)
            if not all(self.operation.manager.stopped(name) for name in ops.HA_WRITERS):
                raise RuntimeError("HA package repair writer is not stopped")
            self.verify_prepared_predecessor()
            receipt = verify_package(self.plan["desired"])
            self.convert_checkpoint(preflight=True)
            return receipt

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
            # Package repair froze mTLS before installation. Canonical activation
            # follows managed mTLS preparation, so reprove the fence/lock identity
            # independently instead of requiring obsolete mTLS bytes.
            local = repair.fencing_evidence(
                self.request["repair"], unstarted=self.request["observed"].get("unstarted", False)
            )
            peer = payload.get("activation_peer")
            if (
                not isinstance(peer, dict)
                or peer.get("lock") != self.request["peer"]["lock"]
                or peer.get("boot_id") != self.request["peer"]["boot_id"]
                or peer.get("guard_boot_id") != peer.get("boot_id")
                or peer.get("forwarding") is not False
                or peer["lock"]["node_id"] == local["lock"]["node_id"]
            ):
                raise RuntimeError("HA activation peer fencing changed")
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
        publication = {k: payload[k] for k in ("receipt", "operation_id")}
        binding_path = STAGING / (self.operation.value["id"] + ".json")
        binding = repair.read_json(binding_path)
        if binding.get("activation") is not None and binding["activation"] != publication:
            raise RuntimeError("HA activation publication changed")
        remote.OPERATION = self.operation
        remote.DEADLINE = float("inf") if remote.DURABLE_PACKAGES else time.monotonic() + 600
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
                self.convert_checkpoint()
                binding["activation"] = publication
                repair.publish(binding_path, json.dumps(binding, sort_keys=True).encode())
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
                remote.command([sys.executable, "-B", "-m", "nebius_vpngw.boot_policy"], limit=30)
                repair.initialize_unstarted_checkpoint(unstarted=not self.repairing)
                repair.publish(MARKER, b"")
                self.publish_assets(
                    {p: v for p, v in manifest["assets"].items() if p in management}
                )
                remote.migration.activate_ha(expected)
                self.operation.phase("activate")
                self.activation = publication
        finally:
            remote.OPERATION = None
            remote.migration.COMMAND = remote.migration.CHECK = None


def initialize_unstarted_writer_layout() -> None:
    """Create only canonical lock storage, never controller or identity authority."""
    for directory in (repair.STATE.parent, repair.STATE):
        if directory == repair.STATE:
            directory.mkdir(mode=0o755, exist_ok=True)
        metadata = directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o022
        ):
            raise RuntimeError("HA package writer directory is unsafe")
    if any(
        path.exists() or path.is_symlink()
        for path in (
            MARKER,
            repair.STATE / "status.json",
            repair.STATE / "controller-checkpoint.json",
        )
    ):
        raise RuntimeError("HA package cannot initialize a previously started writer layout")
    from nebius_vpngw.agent.vm_ha.mtls import ManagedMTLSStore

    # The mTLS owner creates its private directories and empty writer lock.
    # Credentials, certificates, checkpoints and activation remain with their owners.
    ManagedMTLSStore(repair.STATE / "mtls")
    repair.sync_directory(repair.STATE)
    repair.sync_directory(repair.STATE.parent)


@package_exclusion
def prepare_guarded(request: dict) -> dict:
    """Package effects for retained/fresh HA use the same canonical journal."""
    manifest = request["package"]["desired"]
    expected = request.get("expected")
    manager = ops.Manager(float("inf") if remote.DURABLE_PACKAGES else time.monotonic() + 540)
    lock = os.open(remote.LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    operation = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = package_observation(manifest)
        pending = remote.migration.pending(manifest.get("ha_parent"))
        if pending is None and current != request["package"]["predecessor"]:
            raise RuntimeError("HA package predecessor changed")
        if pending is not None:
            if pending["manifest"] != manifest or any(
                current[key] != request["package"]["predecessor"][key]
                for key in (
                    "config_sha256",
                    "ha_config_digest",
                    "python",
                    "machine",
                    "wheel_tags",
                    "runtime_plan",
                )
            ):
                raise RuntimeError("HA resumed package authority changed")
        if MARKER.exists():
            if expected is None:
                raise RuntimeError("HA package lacks exact apply lock")
            repair.exact_lock(expected)
            if repair.FORWARDING.read_text().strip() != "0":
                raise RuntimeError("HA package requires independent forwarding fence")
        elif (repair.STATE / "status.json").exists():
            raise RuntimeError("HA startup authority is ambiguous")
        directory = remote.migration.verify_staged(manifest["runtime_plan"]) / "payload"
        if repair.read_json(directory / "manifest.json") != manifest:
            raise RuntimeError("HA product staging is unproven")
        old = ops.Journal().read()
        identity = pending["operation"] if pending else os.urandom(16).hex()
        remote.migration.begin(manifest["runtime_plan"], identity, manifest)
        ops.install_startup_guard(ha_repair=True)
        resuming = old is not None and old["state"] != "complete"
        if resuming and (pending is None or pending["phase"] != "prepared"):
            remote.migration.verify_inhibited()
        operation = ops.Operation.begin(
            request=identity,
            config=manifest["config_sha256"],
            artifact=manifest["package_identity"],
            predecessor=ops.digest(request["package"]),
            manager=manager,
            network=manager.network(),
            previous=ops.digest(old),
            purpose="ha-handoff",
            resume_inhibited_quiescence=resuming,
        )
        remote.OPERATION = operation
        remote.migration.COMMAND, remote.migration.CHECK = remote.command, operation.check
        remote.migration.inhibit()
        for unit in (
            *ops.HA_WRITERS,
            *ops.MANAGEMENT,
            "strongswan-starter.service",
            "strongswan-swanctl.service",
            "strongswan.service",
        ):
            if not manager.unit(unit).get("missing") or not manager.stopped(unit):
                operation.service("stop", unit)
            if not manager.stopped(unit):
                raise RuntimeError("HA package writer is not stopped")
        if not MARKER.exists():
            initialize_unstarted_writer_layout()
        with repair.writer_locks(deadline=time.monotonic() + 30):
            if expected is not None and MARKER.exists():
                repair.exact_lock(expected)
                repair.cold_guard(state_dir=repair.STATE, boot_id=ops.BOOT.read_text().strip())
            operation.phase("install")
            remote.migration.install()
            remote.python_packages.install_payload(
                manifest, directory, remote.command, operation.check
            )
            receipt = verify_package(manifest)
            operation.phase("publish")
            with zipfile.ZipFile(directory / manifest["wheel_name"]) as archive:
                for path, item in manifest["assets"].items():
                    raw = archive.read(item["member"])
                    if remote.sha(raw) != item["sha256"]:
                        raise RuntimeError("HA asset identity changed")
                    repair.publish(Path(path), raw, item["mode"])
            operation.reload_manager()
            # Runtime inhibition remains durable until exact guarded activation.
            operation.complete()
            return receipt
    finally:
        if operation is not None:
            operation.fail()
        remote.OPERATION = None
        remote.migration.COMMAND = remote.migration.CHECK = None
        os.close(lock)


def corrective_children() -> dict:
    runtime = remote.migration.state()
    ordinary = ops.Journal().read()
    path = repair.STATE / "checkpoint-conversion.json"
    conversion = repair.read_json(path) if path.exists() else None
    if runtime is None or ordinary is None:
        raise RuntimeError("corrective release has no completed package history")
    return {
        "runtime": {
            "phase": runtime["phase"],
            "parent": runtime["manifest"].get("ha_parent"),
            "artifact": runtime["manifest"]["wheel_sha256"],
            "operation": runtime["operation"],
            "digest": ops.digest(runtime),
        },
        "ordinary": {
            "state": ordinary["state"],
            "id": ordinary["id"],
            "digest": ops.digest(ordinary),
        },
        "conversion": None
        if conversion is None
        else {"phase": conversion["phase"], "digest": ops.digest(conversion)},
    }


def corrective_inspect(request: dict) -> dict:
    operation = request["operation"]
    if len(operation) != 64 or any(c not in "0123456789abcdef" for c in operation):
        raise ValueError("corrective operation identity is invalid")
    path = repair.STATE / "corrective" / (operation + ".json")
    return {
        "children": corrective_children(),
        "reservation": repair.read_json(path) if path.exists() else None,
    }


def corrective_fence(expected: dict) -> None:
    """Retained apply admission preserves terminal restoration/transfer history."""
    from nebius_vpngw.checkpoint_upgrade import inspect_checkpoint

    repair.exact_lock(expected)
    guard = repair.read_json(repair.STATE / "guard.json")
    if (
        guard.get("schema") != repair.STATUS_SCHEMA
        or guard.get("guard_boot_id") != ops.BOOT.read_text().strip()
        or guard.get("data_plane_mode") not in {"blocked", "passive"}
        or repair.FORWARDING.read_text().strip() != "0"
    ):
        raise RuntimeError("corrective current-boot forwarding fence is absent")
    # The caller holds the canonical rearm writer lock. This existing owner
    # validates terminal restoration receipts without deleting their history.
    # The streamed bootstrap deliberately isolates installed imports. Invoke the
    # installed admission entrypoint in a separate bounded, observation-only process.
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "nebius_vpngw.agent.main",
            "--vm-ha-apply-lock-quiescent",
            str(repair.STATE / "apply.lock"),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("canonical apply quiescence admission failed")
    checkpoint = inspect_checkpoint(repair.read_json(repair.STATE / "controller-checkpoint.json"))
    if (
        checkpoint.pending_action is not None
        or checkpoint.transfer_continuity is not None
        or checkpoint.repair_attempt is not None
    ):
        raise RuntimeError("corrective controller continuity is unresolved")


def reserve_corrective(request: dict) -> dict:
    """Persist startup exclusion and replace one old lock; never install packages."""
    from nebius_vpngw.apply_lock import encode

    manifest = request["package"]["desired"]
    operation_id = request["operation"]
    if manifest.get("ha_parent") != operation_id:
        raise ValueError("corrective package parent differs")
    # Inspection validates path identity before constructing any output path.
    corrective_inspect(request)
    path = repair.STATE / "corrective" / (operation_id + ".json")
    key = {k: request[k] for k in ("operation", "previous_lock", "children")}
    key["manifest"] = ops.digest(manifest)
    manager = ops.Manager(float("inf") if remote.DURABLE_PACKAGES else time.monotonic() + 540)
    descriptor = os.open(remote.LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    operation = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        saved = repair.read_json(path) if path.exists() else None
        if saved is None:
            if corrective_children() != request["children"]:
                raise RuntimeError("corrective completed predecessor changed")
            children = request["children"]
            if not (
                children["runtime"]["phase"] == "complete"
                and children["ordinary"]["state"] == "complete"
                and children["runtime"]["operation"] == children["ordinary"]["id"]
                and (
                    children["conversion"] is None or children["conversion"]["phase"] == "completed"
                )
            ):
                raise RuntimeError("corrective package work is incomplete")
            repair.exact_lock(request["previous_lock"])
            with repair.writer_locks(deadline=time.monotonic() + 30):
                corrective_fence(request["previous_lock"])
            if repair.FORWARDING.read_text().strip() != "0":
                raise RuntimeError("corrective member is forwarding")
            if package_observation(manifest) != request["package"]["predecessor"]:
                raise RuntimeError("corrective package predecessor changed")
            directory = remote.migration.verify_staged(manifest["runtime_plan"]) / "payload"
            if repair.read_json(directory / "manifest.json") != manifest:
                raise RuntimeError("corrective staged manifest differs")
            # Keep complete raw child records before either canonical journal changes.
            for name, value in (
                ("runtime", remote.migration.state()),
                ("ordinary", ops.Journal().read()),
            ):
                raw = json.dumps(value, sort_keys=True).encode()
                target = path.with_name(operation_id + "-" + name + ".json")
                if target.exists() and repair.read_json(target) != value:
                    raise RuntimeError("corrective journal archive differs")
                if not target.exists():
                    repair.publish(target, raw)
            saved = {"key": key, "child": os.urandom(16).hex(), "phase": "reserved"}
            repair.publish(path, json.dumps(saved, sort_keys=True).encode())
        elif saved["key"] != key:
            raise RuntimeError("corrective reservation authority changed")
        previous = request["previous_lock"]
        new_lock = {**previous, "operation_id": operation_id}
        actual = repair.exact_lock({**previous, "operation_id": None})
        if actual not in (previous, new_lock):
            raise RuntimeError("corrective lock belongs to another operation")
        if saved["phase"] == "quiescent":
            if actual != new_lock or remote.migration.state()["operation"] != saved["child"]:
                raise RuntimeError("corrective reservation postcondition changed")
            with repair.writer_locks(deadline=time.monotonic() + 30):
                corrective_fence(new_lock)
            return saved
        remote.migration.begin(manifest["runtime_plan"], saved["child"], manifest)
        ops.install_startup_guard(ha_repair=True)
        old = ops.Journal().read()
        operation = ops.Operation.begin(
            request=saved["child"],
            config=manifest["config_sha256"],
            artifact=manifest["package_identity"],
            predecessor=ops.digest(key),
            manager=manager,
            network=manager.network(),
            previous=ops.digest(old),
            purpose="ha-handoff",
            resume_inhibited_quiescence=remote.migration.state()["phase"] == "inhibited",
        )
        remote.OPERATION = operation
        remote.migration.COMMAND, remote.migration.CHECK = remote.command, operation.check
        remote.migration.inhibit()
        for unit in (
            *ops.HA_WRITERS,
            *ops.MANAGEMENT,
            "strongswan-starter.service",
            "strongswan-swanctl.service",
            "strongswan.service",
        ):
            if not manager.unit(unit).get("missing") or not manager.stopped(unit):
                operation.service("stop", unit)
            if not manager.stopped(unit):
                raise RuntimeError("corrective writer is not stopped")
        with repair.writer_locks(deadline=time.monotonic() + 30):
            repair.exact_lock(actual)
            corrective_fence(actual)
            if repair.FORWARDING.read_text().strip() != "0":
                raise RuntimeError("corrective forwarding fence changed")
            repair.cold_guard(state_dir=repair.STATE, boot_id=ops.BOOT.read_text().strip())
            repair.publish(repair.STATE / "apply.lock", encode(new_lock))
            saved["phase"] = "quiescent"
            repair.publish(path, json.dumps(saved, sort_keys=True).encode())
        return saved
    finally:
        if operation is not None:
            operation.fail()
        remote.OPERATION = None
        remote.migration.COMMAND = remote.migration.CHECK = None
        os.close(descriptor)


def completed_receipt(request: dict, action: dict) -> dict | None:
    """Resolve a lost final ACK from canonical completion, never by replay."""
    value = ops.Journal().read()
    if not (
        action.get("action") == "complete"
        and request.get("action") == "reserve"
        and value is not None
        and value["state"] == "complete"
        and value["purpose"] == "ha-handoff"
        and value["config"] == request["binding"]
        and value["artifact"] == request["artifact"]
        and value["predecessor"] == request["approval"]
    ):
        return None
    binding = repair.read_json(STAGING / (value["id"] + ".json"))
    publication = binding.get("activation")
    if (
        binding.get("package_digest") != ops.digest(request["package"])
        or publication is None
        or publication["operation_id"] != action["operation"]
        or not MARKER.exists()
    ):
        raise RuntimeError("HA completed handoff binding changed")
    repair.exact_lock(dict(request["ha"], operation_id=action["operation"]))
    repair.verified_file(remote.CONFIG, publication["receipt"]["staged_file_sha256"])
    verify_package(request["package"]["desired"])
    return {"complete": True}


def main(*, durable: bool = False) -> None:
    def expired(_signum, _frame):
        raise TimeoutError("HA handoff deadline")

    if not durable:
        for signum in (signal.SIGALRM, signal.SIGTERM, signal.SIGHUP):
            signal.signal(signum, expired)
        signal.alarm(630)
    request = receive()
    remote.DURABLE_PACKAGES = durable
    remote.DEADLINE = float("inf") if durable else time.monotonic() + 600
    if request["action"].startswith("worker-"):
        from nebius_vpngw import package_worker

        reply(package_worker.control(request))
        return
    if request["action"] == "corrective-inspect":
        reply(corrective_inspect(request))
        return
    if request["action"] == "corrective-reserve":
        reply(reserve_corrective(request))
        return
    if request["action"] == "prepare-guarded":
        reply(prepare_guarded(request))
        return
    if request["action"] == "inspect":
        reply(inspect(request.get("ha", {})))
        return
    if request["action"] == "repair-evidence":
        reply(repair.evidence(request["repair"]))
        return
    if request["action"] == "activation-evidence":
        reply(repair.fencing_evidence(request["repair"]))
        return
    lock = os.open(remote.LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    operation = None
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        old = ops.Journal().read()
        pending_package = remote.migration.pending(
            (request.get("package") or {}).get("desired", {}).get("ha_parent")
        )
        resuming = bool(
            old
            and old["purpose"] == "ha-handoff"
            and old["config"] == request["binding"]
            and old["artifact"] == request["artifact"]
            and old["predecessor"] == request["approval"]
            and pending_package
            and (old["state"] != "complete" or pending_package["phase"] == "activating")
            and pending_package["operation"] == old["id"]
            and pending_package["manifest"] == (request.get("package") or {}).get("desired")
        )
        if resuming and pending_package["phase"] == "activating":
            saved = repair.read_json(STAGING / (old["id"] + ".json"))
            activation = saved.get("activation")
            if activation is None:
                raise RuntimeError("HA activation has no exact publication receipt")
            repair.fencing_evidence(dict(request["ha"], operation_id=activation["operation_id"]))
        observed = inspect(request["ha"], inhibited=resuming)
        request["resuming"] = resuming
        if observed != request["observed"] and not resuming:
            raise RuntimeError("HA handoff predecessor changed")
        if resuming and pending_package["phase"] not in {"prepared", "activating"}:
            remote.migration.verify_inhibited()
        if old and old["state"] != "complete" and old["config"] != request["binding"]:
            raise RuntimeError("HA handoff target changed")
        with remote.routing_lock():
            reserve_runtime_routes(request)
        manager = ops.Manager(float("inf") if durable else time.monotonic() + 180)
        parent = (request.get("package") or {}).get("desired", {}).get("ha_parent")
        operation_id = (remote.migration.pending(parent) or {}).get("operation") or (
            old["id"] if old and old["state"] != "complete" else os.urandom(16).hex()
        )
        if request.get("package") is not None:
            package = request["package"]
            if package["desired"]["wheel_sha256"] != request["artifact"] or (
                not resuming and package_observation(package["desired"]) != package["predecessor"]
            ):
                raise RuntimeError("HA handoff package approval changed before reservation")
            directory = stage_package(package, request["staged_package"])
            remote.migration.begin(
                package["desired"]["runtime_plan"], operation_id, package["desired"]
            )
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
                id=operation_id,
                owner=ops.identity(),
                config=request["binding"],
                artifact=request["artifact"],
                predecessor=request["approval"],
                state="active",
                phase="quiesce",
                effects=[],
                network=manager.network(),
            )
            if old and old["state"] != "complete":
                if (
                    not ops.handoff_recoverable(old, manager, inhibited_quiescence=resuming)
                    or old["artifact"] != request["artifact"]
                ):
                    raise RuntimeError("HA package operation remains unresolved")
                new.update(
                    effects=[dict(effect, state="settled") for effect in old["effects"]],
                    predecessor=old["predecessor"],
                )
            if request.get("package") is not None:
                binding = {k: new[k] for k in ("id", "owner", "config", "artifact", "predecessor")}
                binding.update(
                    ha=request["ha"],
                    package_digest=ops.digest(request["package"]),
                    unstarted=not repairing or observed.get("unstarted", False),
                )
                saved_path = STAGING / (new["id"] + ".json")
                if resuming:
                    saved = repair.read_json(saved_path)
                    if saved.get("package_digest") != binding["package_digest"]:
                        raise RuntimeError("HA handoff prepared binding changed")
                    if "prepared" in saved:
                        binding["prepared"] = saved["prepared"]
                    if "activation" in saved:
                        binding["activation"] = saved["activation"]
                repair.publish(saved_path, json.dumps(binding).encode())
            ops.Journal().write(new, expected=ops.digest(old))
            operation = ops.Operation(new, manager)
        if request.get("package") is not None:
            remote.OPERATION = operation
            remote.migration.COMMAND, remote.migration.CHECK = remote.command, operation.check
            remote.migration.inhibit()
            remote.OPERATION = None
            remote.migration.COMMAND = remote.migration.CHECK = None
        for name in (*(ops.HA_WRITERS if repairing else ()), *ops.MANAGEMENT):
            if not manager.unit(name).get("missing") or not manager.stopped(name):
                operation.service("stop", name, timeout=45)
            if not manager.stopped(name):
                raise RuntimeError("HA handoff writer did not stop")
        if repairing and request.get("repair") is not None:
            with repair.writer_locks(deadline=time.monotonic() + 30):
                repair.exact_lock(request["repair"])
                repair.cold_guard(state_dir=repair.STATE, boot_id=ops.BOOT.read_text().strip())
                repair.evidence(request["repair"], unstarted=observed.get("unstarted", False))
        holder = Holder(request, operation)
        reply({"ready": True})
        deadline = time.monotonic() + 3600
        while True:
            if not durable:
                signal.alarm(max(1, int(deadline - time.monotonic())))
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
                    raise RuntimeError("HA handoff deadline")
            action = receive()
            if not durable:
                signal.alarm(min(600, max(1, int(deadline - time.monotonic()))))
            if action["action"] == "prepare-package":
                reply(holder.prepare(action))
            elif action["action"] == "publish-activation":
                holder.activate(action)
                reply({"published": True})
            elif action["action"] == "verify-prepared":
                reply(holder.verify_prepared(action))
            elif action["action"] == "complete":
                if (
                    not holder.prepared
                    or not MARKER.exists()
                    or operation.value["phase"] not in {"activate", "verify"}
                ):
                    raise RuntimeError("HA handoff activation is incomplete")
                repair.exact_lock(dict(request["ha"], operation_id=action["operation"]))
                with remote.routing_lock():
                    retire_runtime_routes(request)
                    operation.complete()
                reply({"complete": True})
                return
            else:
                raise RuntimeError("HA handoff action invalid")
    finally:
        remote.OPERATION = None
        remote.migration.COMMAND = remote.migration.CHECK = None
        if operation is not None:
            operation.fail()
        os.close(lock)
