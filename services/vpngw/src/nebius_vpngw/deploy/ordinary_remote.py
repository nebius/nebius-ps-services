"""Standalone stdlib transaction runner, streamed to previous-release guests.

It must not import the installed product before installation or rely on its
capabilities. Requests and receipts travel over the authenticated SSH channel.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import importlib.metadata as metadata
import io
import json
import os
import platform
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

try:
    import _vpngw_ingress as ingress  # type: ignore[import-not-found]
    import _vpngw_ordinary_operations as operations  # type: ignore[import-not-found]
    import _vpngw_ordinary_routes as routes  # type: ignore[import-not-found]
    import _vpngw_runtime_migration as migration  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from .. import ingress_policy as ingress
    from .. import ordinary_operations as operations
    from .. import ordinary_routes as routes
    from .. import runtime_migration as migration

try:
    import _vpngw_python_package_recovery as python_packages  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from .. import python_package_recovery as python_packages

migration.PYTHON_PREPARE = lambda manifest, path: python_packages.prepare_payload(
    manifest, path, command
)

CONFIG = Path("/etc/nebius-vpngw/config-resolved.yaml")
BOOT = Path("/proc/sys/kernel/random/boot_id")
LOCK = Path("/run/lock/nebius-vpngw-deploy.lock")
ROUTING_LOCK = Path("/run/nebius-vpngw/fix-routes.lock")
DEADLINE = 0.0
CHILD: subprocess.Popen | None = None
OPERATION: operations.Operation | None = None
DURABLE_PACKAGES = False


def command_environment(args: list[str]) -> dict[str, str]:
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "LC_ALL": "C",
        "DEBIAN_FRONTEND": "noninteractive",
    }
    if "pip" in args[:4] and (DURABLE_PACKAGES or "--root" in args):
        environment = {
            k: v
            for k, v in environment.items()
            if not k.startswith("PIP_") and k not in {"PYTHONPATH", "PYTHONHOME"}
        }
        environment.update(PIP_CONFIG_FILE=os.devnull, PYTHONNOUSERSITE="1")
    return environment


# Shared with HA package preparation; runs under the caller's bounded process
# group. Only selected, hash-verified wheels reach this installer. Never apply
# --ignore-installed to pip-owned packages: that leaves stale dist-info behind.
DEPENDENCY_INSTALL_SCRIPT = r"""
import importlib.metadata as metadata
import subprocess
import sys
import sysconfig
import zipfile
from email.parser import BytesParser
from pathlib import Path

destinations = {Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")}
for wheel in sys.argv[1:]:
    with zipfile.ZipFile(wheel) as archive:
        entries = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(entries) != 1:
            raise RuntimeError("dependency metadata is ambiguous")
        name = BytesParser().parsebytes(archive.read(entries[0]))["Name"]
    args = [sys.executable, "-B", "-m", "pip", "install", "--no-index", "--no-deps",
            "--break-system-packages", "--disable-pip-version-check"]
    try:
        installed = metadata.distribution(name)
    except metadata.PackageNotFoundError:
        installed = None
    if installed is not None and installed.read_text("RECORD") is None:
        # Ubuntu's apt packages live outside pip's /usr/local install scheme.
        # Shadow those files without asking pip to uninstall apt-owned state.
        if Path(installed.locate_file("")).resolve() in destinations:
            raise RuntimeError("dependency has no uninstall record in the install destination")
        args.append("--ignore-installed")
    subprocess.run([*args, wheel], check=True)
"""


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value: Any) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def command(
    args: list[str], *, limit: int = 30, required: bool = True, capture_stderr: bool = False
) -> tuple[int, str]:
    global CHILD
    package_effect = (
        args[0] == "dpkg"
        and any(action in args for action in ("--remove", "--unpack", "--configure"))
    ) or ("pip" in args[:4] and "install" in args)
    if DURABLE_PACKAGES and OPERATION is not None and package_effect:
        return durable_command(args, required=required)
    remaining = min(limit, DEADLINE - (60 if OPERATION else 0) - time.monotonic())
    if remaining <= 0:
        raise RuntimeError("deadline")
    effect = OPERATION.intent("process") if OPERATION else None
    CHILD = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if capture_stderr else subprocess.DEVNULL,
        start_new_session=True,
        env=command_environment(args),
        # Projection and PID1-owned installation must create identical modes,
        # regardless of the observing SSH process's inherited mask.
        umask=0o022 if "pip" in args[:4] and "install" in args else -1,
    )
    try:
        if OPERATION is not None and effect is not None:
            OPERATION.process_started(effect, CHILD.pid)
        deadline = time.monotonic() + remaining
        output = bytearray()
        assert CHILD.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(CHILD.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(args, limit)
                for key, _events in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > 2 * 1024 * 1024:
                            raise RuntimeError("command_output_limit")
        rc = CHILD.wait(timeout=max(0.001, deadline - time.monotonic()))
    finally:
        # A timeout or interrupted parent must not leave package/network writers
        # running after the deployment lock is released.
        try:
            # The ordinary action handles TERM and joins its own child groups
            # before releasing its routing lock. Allow that cleanup to finish.
            os.killpg(CHILD.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            CHILD.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(CHILD.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        CHILD.wait(timeout=5)
        process = (
            OPERATION.value["effects"][effect].get("process", {})
            if OPERATION is not None and effect is not None
            else None
        )
        if process is not None:
            settle_deadline = min(DEADLINE, time.monotonic() + 5)
            while not operations.process_group_empty(process):
                if time.monotonic() >= settle_deadline:
                    raise RuntimeError("command_descendants_unresolved")
                time.sleep(0.02)
        CHILD = None
        if OPERATION is not None and effect is not None:
            OPERATION.update(effect, state="settled")
    if required and rc:
        raise RuntimeError("command")
    return rc, output.decode("utf-8", errors="strict").strip()


def durable_command(args: list[str], *, required: bool = True) -> tuple[int, str]:
    """Wait for an admitted owner's child and descendants, independent of SSH.

    No elapsed-time or output-volume event kills package work. Retain bounded
    diagnostics while draining pipes. Parent death leaves the native effect
    unresolved and systemd's cgroup prevents a replacement owner overlapping it.
    """
    assert OPERATION is not None
    OPERATION.check()
    from nebius_vpngw import package_worker

    if args[0] == "dpkg":
        with package_worker.native_lock("/var/lib/dpkg/lock"):
            pass
        OPERATION.check()
    index = OPERATION.intent("process")
    child = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=command_environment(args),
        umask=0o022 if "pip" in args[:4] and "install" in args else -1,
    )
    OPERATION.process_started(index, child.pid)
    output = bytearray()
    assert child.stdout is not None
    with child.stdout, selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ)
        while selector.get_map():
            for key, _events in selector.select(1):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                else:
                    output.extend(chunk[: max(0, 2 * 1024 * 1024 - len(output))])
    rc = child.wait()
    process = OPERATION.check()["effects"][index]["process"]
    from nebius_vpngw import package_worker

    while not operations.process_group_empty(process) or package_worker.children_running():
        time.sleep(0.2)
    OPERATION.update(index, state="settled")
    if required and rc:
        raise RuntimeError("package command failed after settlement")
    return rc, output.decode("utf-8", errors="strict").strip()


def agent_receipt(
    config_sha: str, boot: str, *, verify_only: bool, request_id: str | None = None
) -> dict[str, Any]:
    request_id = request_id or os.urandom(16).hex()
    request = {
        "request_id": request_id,
        "config_sha256": config_sha,
        "boot_id": boot,
        "verify_only": verify_only,
    }
    encoded = base64.b64encode(json.dumps(request).encode()).decode()
    rc, output = command(
        [sys.executable, "-B", "-m", "nebius_vpngw.agent.main", "--ordinary-apply", encoded],
        limit=300,
        required=False,
    )
    if len(output) > 8192:
        raise RuntimeError("receipt")
    value = json.loads(output)
    if (
        rc
        or not isinstance(value, dict)
        or value.get("schema") != "nebius-vpngw.ordinary-apply.v1"
        or value.get("status") not in {"applied", "unchanged"}
        or any(value.get(key) != request[key] for key in ("request_id", "config_sha256", "boot_id"))
    ):
        raise RuntimeError("receipt")
    return value


def inspect(
    manifest: dict[str, Any],
    *,
    verify_runtime: bool = True,
    runtime_admission: bool = True,
    route_locked: bool = False,
) -> dict[str, Any]:
    boot = BOOT.read_text().strip()
    config_sha = sha(CONFIG.read_bytes()) if CONFIG.exists() else None
    logical_config = None
    if config_sha:
        import yaml

        payload = yaml.safe_load(CONFIG.read_bytes())
        if isinstance(payload, dict) and isinstance(payload.get("vm_ha"), dict):
            payload["vm_ha"].pop("runtime_binding", None)
            logical_config = canonical(payload)
    try:
        dist = metadata.distribution("nebius-vpngw")
    except metadata.PackageNotFoundError:
        dist = None
    files = {}
    for name in manifest["files"]:
        path = Path(str(dist.locate_file(name))) if dist else None
        files[name] = sha(path.read_bytes()) if path and path.is_file() else None
    assets = {
        path: sha(Path(path).read_bytes()) if Path(path).is_file() else None
        for path in manifest["assets"]
    }
    asset_modes = {
        path: [
            Path(path).stat().st_mode & 0o777,
            Path(path).stat().st_uid,
            Path(path).stat().st_gid,
        ]
        if Path(path).is_file()
        else None
        for path in manifest["assets"]
    }
    dependencies = {}
    requirements = {}
    distributions_by_prefix: dict[tuple[str, str], str] = {}
    for item in metadata.distributions():
        name = re.sub(r"[-_.]+", "-", item.metadata["Name"] or "").lower()
        if name:
            key = (name, str(Path(str(item.locate_file(""))).resolve()))
            previous = distributions_by_prefix.setdefault(key, item.version)
            if previous != item.version:
                # Two versions can claim the same import files after an older
                # ignore-installed deployment. Neither enumeration order nor
                # the first metadata record proves which code Python imports.
                raise RuntimeError("conflicting installed package metadata")
        if name and name not in dependencies:
            dependencies[name] = item.version
            requirements[name] = item.requires or []
    agent_state = ""
    if runtime_admission:
        _, agent_state = command(["systemctl", "is-active", "nebius-vpngw-agent"], required=False)
    verified = False
    if config_sha and verify_runtime and runtime_admission:
        try:
            receipt = agent_receipt(config_sha, boot, verify_only=True)
            verified = receipt.get("package_identity") == manifest["package_identity"]
        except Exception:
            pass
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.tags import sys_tags

    environment = None
    if logical_config is None and runtime_admission:
        try:
            environment = operations.Manager(DEADLINE).environment()
        except Exception:
            environment = {"error": "ordinary_environment_unverified"}
    ownership = None
    if logical_config is None and runtime_admission:
        import yaml

        with contextlib.nullcontext() if route_locked else routing_lock():
            ownership = routes.RouteStore().snapshot(
                yaml.safe_load(CONFIG.read_bytes()) if CONFIG.exists() else None,
                CONFIG.parent / "last-applied.json",
                command_result,
                boot,
                proof=CONFIG.parent / "ordinary-verification.json",
                future=manifest["route_projection"],
            )
    # This read-only projection preserves the caller's approved closure before a
    # guest transaction exists. Execution still proves the original predecessor
    # and admission through the operation owner before installing anything.
    runtime_plan = (
        manifest.get("runtime_plan")
        if manifest.get("ha_parent") or manifest.get("command_approval")
        else None
    )
    if runtime_plan is not None:
        migration.validate_plan(runtime_plan)
    pending_runtime = migration.pending(manifest.get("ha_parent"))
    runtime_state = migration.state()
    completion = None
    if runtime_state and runtime_state["phase"] == "complete" and pending_runtime is None:
        completion = {
            "operation": runtime_state["operation"],
            "approval": runtime_state["manifest"].get("command_approval"),
            "manifest": canonical(
                {
                    key: value
                    for key, value in runtime_state["manifest"].items()
                    if key not in {"command_approval", "ha_parent"}
                }
            ),
        }
    return {
        "runtime": migration.native(),
        "runtime_plan": runtime_plan
        if runtime_plan is not None
        else migration.plan(manifest.get("ha_parent")),
        "runtime_pending": pending_runtime,
        "runtime_completion": completion,
        "route_ownership": ownership,
        "boot_id": boot,
        "config_sha256": config_sha,
        "ha_config_digest": logical_config,
        "files": files,
        "assets": assets,
        "asset_modes": asset_modes,
        "dependencies": dependencies,
        "agent_state": agent_state,
        "verified": verified,
        "requirements": requirements,
        "markers": default_environment(),
        "wheel_tags": [str(tag) for tag in sys_tags()],
        "python": platform.python_version(),
        "machine": platform.machine(),
        "package_version": dist.version if dist else None,
        "operation": operations.observation(operations.Manager(DEADLINE))
        if logical_config is None and runtime_admission
        else None,
        "environment": environment,
    }


def command_result(args, **kwargs):
    code, output = command(args, required=False)
    return subprocess.CompletedProcess(args, code, output, "")


def stable_predecessor(observation: dict[str, Any]) -> str:
    # Our reservation changes pending evidence, not the approved kernel/state inputs.
    observation = dict(observation)
    if observation.get("route_ownership") is not None:
        observation["route_ownership"] = {
            k: v
            for k, v in observation["route_ownership"].items()
            if k not in {"pending_digest", "pending_sources", "obligations"}
        }
    # Stopping management changes service state, never these approved inputs.
    return canonical(
        {
            key: value
            for key, value in observation.items()
            if key
            not in {
                "verified",
                "agent_state",
                "operation",
                "runtime",
                "runtime_pending",
                "runtime_completion",
            }
        }
    )


def _wait_final_settlement(operation: operations.Operation) -> None:
    """Observe admitted manager work without releasing the pending-operation guard."""
    operations.wait_quiet(operation.manager, operation.check)


def final_confirmation(
    manifest: dict[str, Any],
    current: dict[str, Any],
    request_id: str,
    operation: operations.Operation | None = None,
) -> dict[str, Any]:
    while True:
        if operation is not None:
            operation.check()
            if time.monotonic() >= operation.manager.deadline:
                raise TimeoutError("ordinary_manager_unresolved")
        receipt = agent_receipt(
            manifest["config_sha256"], current["boot_id"], verify_only=True, request_id=request_id
        )
        with routing_lock(operation) as waited:
            if waited:
                # The earlier runtime receipt predates this wait. Release the
                # lock and obtain a fresh receipt before the final snapshot.
                continue
            final = inspect(manifest, verify_runtime=False, route_locked=True)
            proof = CONFIG.parent / "ordinary-verification.json"
            if (
                not final["runtime"]["ready"]
                or final["files"] != manifest["files"]
                or final["agent_state"] != "active"
                or final["boot_id"] != current["boot_id"]
                or final["config_sha256"] != manifest["config_sha256"]
                or final["assets"]
                != {path: item["sha256"] for path, item in manifest["assets"].items()}
                or final["asset_modes"]
                != {path: [item["mode"], 0, 0] for path, item in manifest["assets"].items()}
                or final["dependencies"] != manifest["expected_dependencies"]
                or receipt.get("package_identity") != manifest["package_identity"]
                or receipt.get("render_version") != manifest["render_version"]
                or receipt.get("proof_sha256") != sha(proof.read_bytes())
            ):
                raise RuntimeError("final_artifact")
            if manifest.get("route_projection") is not None:
                import yaml

                route_store = routes.RouteStore()
                cfg = yaml.safe_load(CONFIG.read_bytes())
                route_store.verify(cfg, command_result, current["boot_id"])
                if operation is not None:
                    ledger = route_store.ledger()
                    envelope = route_store.envelope(operation.check())
                    saved_proof = routes._read(proof)
                    routes.require(
                        ledger is not None
                        and ledger["binding"] == envelope["binding"]
                        and saved_proof is not None
                        and saved_proof[1].get(routes.PROOF_KEY) == routes.digest(ledger),
                        "ordinary_route_commit_unverified",
                    )
                    route_store.verify(
                        cfg,
                        command_result,
                        current["boot_id"],
                        obligations=envelope["plan"]["obligations"],
                    )
            if operation is None:
                return receipt
            if operation.manager.quiet((*operations.MANAGEMENT, *operations.DATAPLANE)):
                if time.monotonic() >= operation.manager.deadline:
                    raise TimeoutError("ordinary_manager_unresolved")
                migration.release()
                # Timer activation was deliberately inhibited during package work.
                operation.service("start", "nebius-vpngw-fix-routes.timer")
                operation.complete()
                return receipt
        # An activated timer can dispatch its guarded maintenance job after
        # activation settles. Release the routing lock while that job exits,
        # retain ownership, then repeat every final proof before completion.
        _wait_final_settlement(operation)


@contextlib.contextmanager
def routing_lock(operation: operations.Operation | None = None):
    ROUTING_LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ROUTING_LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        waited = False
        while True:
            if operation is not None:
                operation.check()
                if time.monotonic() >= operation.manager.deadline:
                    raise TimeoutError("routing_lock_deadline")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if operation is None:
                    raise
                waited = True
                time.sleep(min(0.1, max(0, operation.manager.deadline - time.monotonic())))
        yield waited
    finally:
        os.close(fd)


def publish(path: Path, raw: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def execute(request: dict[str, Any]) -> dict[str, Any]:
    global OPERATION
    manifest = request["manifest"]
    replacement = request.get("prepare_replacement")
    replacement_guard = Path("/etc/nebius-vpngw/replacement-guard.json")
    if replacement is not None:
        if (
            not re.fullmatch(r"[a-f0-9]{32,64}", str(replacement))
            or not replacement_guard.is_file()
        ):
            return {
                "status": "failed",
                "stage": "predecessor",
                "error_code": "replacement_guard_required",
            }
        if json.loads(replacement_guard.read_text()) != {"operation": replacement}:
            return {
                "status": "failed",
                "stage": "predecessor",
                "error_code": "replacement_guard_changed",
            }
    elif replacement_guard.exists():
        return {
            "status": "failed",
            "stage": "predecessor",
            "error_code": "replacement_cutover_required",
        }
    if not request["noop"] and request.get("approval") != request["plan_digest"]:
        return {"status": "failed", "stage": "predecessor", "error_code": "approval_required"}
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    stage = "predecessor"
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = inspect(manifest)
        if canonical(current) != request["predecessor"]:
            raise RuntimeError("stale_plan")
        if request["noop"]:
            operations.require_idle()
            if not current["verified"] or current["agent_state"] != "active":
                raise RuntimeError("runtime_drift")
            receipt = final_confirmation(manifest, current, request["request_id"])
            return {"status": "unchanged", "receipt": receipt}
        if request.get("approval") != request["plan_digest"]:
            raise RuntimeError("approval")
        manager = operations.Manager(DEADLINE - 60)
        environment = manager.environment()
        if environment != current.get("environment"):
            raise RuntimeError("ordinary_environment_changed")
        network = environment["network"]
        for path, item in manifest["assets"].items():
            if "predecessors" in item and current["assets"].get(path) not in item["predecessors"]:
                raise RuntimeError("ordinary_asset_conflict")
        resume = (current.get("operation") or {}).get("id") == request["request_id"]
        if resume:
            previous_operation = operations.Journal().read()
            envelope = routes.RouteStore().envelope(previous_operation)
            if envelope["plan"] != manifest["route_plan"]:
                raise RuntimeError("route_plan_changed")
            route_plan = routes.RouteRetirementPlan(envelope["plan"])
        else:
            route_plan = routes.RouteRetirementPlan.build(
                current["route_ownership"], manifest["route_projection"]
            )
            if route_plan.payload != manifest["route_plan"]:
                raise RuntimeError("route_plan_changed")
        stage = "stage"
        migration.stage_payload(manifest, request)
        migration.begin(manifest["runtime_plan"], request["request_id"], manifest)
        wheel = base64.b64decode(request["wheel"], validate=True)
        config = base64.b64decode(request["config"], validate=True)
        if sha(wheel) != manifest["wheel_sha256"] or sha(config) != manifest["config_sha256"]:
            raise RuntimeError("artifact")
        import yaml

        desired = yaml.safe_load(config)
        if ingress.from_config(desired) != manifest["ingress_policy"]:
            raise RuntimeError("ingress_policy_identity_changed")
        previous = yaml.safe_load(CONFIG.read_bytes()) if CONFIG.exists() else None
        if (
            not isinstance(desired, dict)
            or desired.get("vm_ha") is not None
            or (isinstance(previous, dict) and previous.get("vm_ha") is not None)
            or Path("/etc/nebius-vpngw/vm-ha-enabled").exists()
        ):
            raise RuntimeError("ordinary_topology")
        with tempfile.TemporaryDirectory(prefix="nebius-vpngw-apply-") as directory:
            root = Path(directory)
            wheel_path = root / manifest["wheel_name"]
            if wheel_path.parent != root or wheel_path.suffix != ".whl":
                raise RuntimeError("artifact")
            wheel_path.write_bytes(wheel)
            dependency_paths = [
                str(migration.ROOT / migration.digest(manifest["runtime_plan"]) / "payload" / name)
                for name in manifest["dependency_wheels"]
            ]
            stage = "quiesce"
            resume = (current.get("operation") or {}).get("id") == request["request_id"]
            with routing_lock():
                fresh_routes = routes.RouteStore().snapshot(
                    previous,
                    CONFIG.parent / "last-applied.json",
                    command_result,
                    current["boot_id"],
                    proof=CONFIG.parent / "ordinary-verification.json",
                    future=manifest["route_projection"],
                )
                if fresh_routes != current["route_ownership"]:
                    raise RuntimeError("route_predecessor_changed")
                if replacement is None and not resume:
                    routes.RouteStore().prepare(
                        {
                            "id": request["request_id"],
                            "config": manifest["config_sha256"],
                            "artifact": manifest["package_identity"],
                            "predecessor": request["predecessor"],
                        },
                        route_plan,
                        approved=True,
                    )
                operations.install_startup_guard()
                OPERATION = operations.Operation.begin(
                    request=request["request_id"],
                    config=manifest["config_sha256"],
                    artifact=manifest["package_identity"],
                    predecessor=request["predecessor"],
                    manager=manager,
                    network=network,
                    previous=(current.get("operation") or {}).get("digest"),
                )
            migration.COMMAND = command
            migration.CHECK = OPERATION.check
            migration.inhibit()
            for name in (
                *operations.MANAGEMENT,
                "strongswan-starter.service",
                "strongswan-swanctl.service",
                "strongswan.service",
            ):
                if not manager.unit(name).get("missing") or not manager.stopped(name):
                    OPERATION.service("stop", name)
                if not manager.stopped(name):
                    raise RuntimeError("quiescence")
            stage = "install"
            OPERATION.phase("install")
            with routing_lock():
                quiescent = inspect(manifest, verify_runtime=False, route_locked=True)
                if not resume and stable_predecessor(quiescent) != stable_predecessor(current):
                    raise RuntimeError("identity_drift")
                if not manager.quiet((*operations.MANAGEMENT, *operations.DATAPLANE)):
                    raise RuntimeError("package_quiescence_unproven")
                migration.install()
                if dependency_paths:
                    command(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            DEPENDENCY_INSTALL_SCRIPT,
                            *dependency_paths,
                        ],
                        limit=120,
                    )
                if (
                    current["files"] != manifest["files"]
                    or current["package_version"] != manifest["version"]
                ):
                    command(
                        migration.product_install_command(wheel_path),
                        limit=120,
                    )
                stage = "publish"
                OPERATION.phase("publish")
                with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
                    for path, item in manifest["assets"].items():
                        raw = archive.read(item["member"])
                        if sha(raw) != item["sha256"]:
                            raise RuntimeError("asset")
                        if current["assets"][path] != item["sha256"] or current["asset_modes"][
                            path
                        ] != [item["mode"], 0, 0]:
                            publish(Path(path), raw, item["mode"])
                if current["config_sha256"] != manifest["config_sha256"]:
                    publish(CONFIG, config, 0o644)
                OPERATION.reload_manager()
                migration.enable_units(["nebius-vpngw-ingress.service"])
                if not manager.management_admitted():
                    raise RuntimeError("ordinary_service_scope_unknown")
            if replacement is not None:
                # Artifact/configuration staging has no dataplane or route commit.
                # The independently installed boot guard remains authoritative.
                observed = inspect(manifest, verify_runtime=False, runtime_admission=False)
                if (
                    observed["boot_id"] != current["boot_id"]
                    or observed["config_sha256"] != manifest["config_sha256"]
                    or observed["files"] != manifest["files"]
                    or observed["assets"]
                    != {path: item["sha256"] for path, item in manifest["assets"].items()}
                    or observed["asset_modes"]
                    != {path: [item["mode"], 0, 0] for path, item in manifest["assets"].items()}
                    or observed["dependencies"] != manifest["expected_dependencies"]
                    or json.loads(replacement_guard.read_text()) != {"operation": replacement}
                    or Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() != "0"
                    or Path("/proc/sys/net/ipv6/conf/all/forwarding").read_text().strip() != "0"
                    or not all(
                        manager.stopped(name)
                        for name in (
                            *operations.MANAGEMENT,
                            "strongswan-starter.service",
                            "frr.service",
                        )
                    )
                ):
                    raise RuntimeError("replacement_preparation_unverified")
                OPERATION.complete()
                return {
                    "status": "prepared",
                    "receipt": {
                        "operation": replacement,
                        "boot_id": current["boot_id"],
                        "config_sha256": manifest["config_sha256"],
                        "package_identity": manifest["package_identity"],
                        "request_id": request["request_id"],
                    },
                }
            stage = "reconcile"
            migration.activate()
            OPERATION.phase("reconcile")
            receipt = agent_receipt(
                manifest["config_sha256"],
                current["boot_id"],
                verify_only=False,
                request_id=request["request_id"],
            )
            if receipt.get("package_identity") != manifest["package_identity"]:
                raise RuntimeError("installed_artifact")
            stage = "activate"
            OPERATION.phase("activate")
            activate_management(OPERATION)
            stage = "verify"
            OPERATION.phase("verify")
            receipt = final_confirmation(manifest, current, request["request_id"], OPERATION)
            return {"status": "applied", "receipt": receipt}
    except Exception as error:
        if OPERATION is not None:
            OPERATION.manager.deadline = DEADLINE
            OPERATION.fail()
        # Closed diagnostic codes distinguish stale approval from failed local
        # verification. Never return arbitrary command/configuration output.
        code = str(error)
        if code not in {
            "stale_plan",
            "runtime_drift",
            "final_artifact",
            "receipt",
            "ordinary_environment_changed",
            "ordinary_asset_conflict",
            "route_plan_changed",
            "route_predecessor_changed",
            "identity_drift",
            "package_quiescence_unproven",
        }:
            code = "ordinary_transaction_failed"
        return {"status": "failed", "stage": stage, "error_code": code}
    finally:
        OPERATION = None
        migration.COMMAND = migration.CHECK = None
        os.close(fd)


def activate_management(operation: operations.Operation) -> None:
    active = [
        "nebius-vpngw-agent.service",
        "nebius-vpngw-fix-routes.timer",
        "nebius-vpngw-health-monitor.service",
    ]
    operation.enable(migration.units_to_enable(active))
    # The timer's package-inhibition condition remains false until final
    # confirmation releases the migration. That owner starts it after release.
    for name in active:
        if name != "nebius-vpngw-fix-routes.timer":
            operation.service("start", name)


def release_runtime(request: dict) -> dict:
    value = migration.state()
    if value is not None:
        if value["manifest"]["wheel_sha256"] != request["artifact"]:
            raise RuntimeError("runtime release artifact changed")
        migration.release()
        command(["systemctl", "start", "nebius-vpngw-fix-routes.timer"], limit=15)
    elif not migration.native()["ready"]:
        raise RuntimeError("runtime native verification failed")
    return {"status": "released"}


def main(*, deadline: float | None = None) -> None:
    global DEADLINE
    DEADLINE = time.monotonic() + 600 if deadline is None else deadline

    def expired(_signal, _frame):
        raise TimeoutError("transaction_deadline")

    signal.signal(signal.SIGALRM, expired)
    signal.signal(signal.SIGTERM, expired)
    signal.signal(signal.SIGHUP, expired)
    if deadline is None:
        signal.alarm(600)
    try:
        if time.monotonic() >= DEADLINE:
            raise TimeoutError("transaction_deadline")
        raw = sys.stdin.buffer.read(128 * 1024 * 1024 + 1)
        if len(raw) > 128 * 1024 * 1024:
            raise RuntimeError("request_limit")
        request = json.loads(raw)
        with (
            open(os.devnull, "w") as sink,
            contextlib.redirect_stdout(sink),
            contextlib.redirect_stderr(sink),
        ):
            result = (
                {
                    "status": "inspected",
                    "observation": inspect(
                        request["manifest"], runtime_admission=request["action"] == "inspect"
                    ),
                }
                if request["action"] in {"inspect", "inspect-package"}
                else {
                    "status": "staged",
                    "receipt": migration.stage_payload(request["manifest"], request),
                }
                if request["action"] == "stage-package"
                else release_runtime(request)
                if request["action"] == "release-runtime"
                else execute(request)
            )
    except Exception:
        result = {
            "status": "failed",
            "stage": "inspect",
            "error_code": "ordinary_transaction_failed",
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    raise SystemExit(1 if result["status"] == "failed" else 0)


if __name__ == "__main__":
    main(deadline=globals().get("_ordinary_bootstrap_deadline"))
