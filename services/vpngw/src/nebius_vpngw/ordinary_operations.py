"""Durable admission for ordinary gateway mutations (stdlib, also streamed over SSH).

Process locks serialize cooperating writers. This journal retains their logical
exclusion when PID 1 or networkd outlives a caller. It is not an HA authority or a
queue: an interrupted operation is never replayed without a new deployment plan.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

JOURNAL = Path("/var/lib/nebius-vpngw/ordinary/operation.json")
BOOT = Path("/proc/sys/kernel/random/boot_id")
ROUTING_LOCK = Path("/run/nebius-vpngw/fix-routes.lock")
SCHEMA = "nebius-vpngw.ordinary-operation.v1"
MANAGEMENT = (
    "nebius-vpngw-fix-routes.timer",
    "nebius-vpngw-health-monitor.service",
    "nebius-vpngw-agent.service",
    "nebius-vpngw-fix-routes.service",
)
HA_WRITERS = ("nebius-vpngw-vm-ha-rearm.service", "nebius-vpngw-vm-ha.service")
HA_GUARD = "nebius-vpngw-vm-ha-guard.service"
DATAPLANE = (
    "strongswan-starter.service",
    "frr.service",
    "ufw.service",
    "nebius-vpngw-esp4-preflight.service",
    "systemd-networkd.service",
    "netplan-ovs-cleanup.service",
)
PHASES = {"quiesce", "install", "publish", "reconcile", "activate", "verify"}
Runner = Callable[..., subprocess.CompletedProcess]


def streamed_bootstrap() -> str:
    """Load the exact helper source without importing an older guest package."""
    source = base64.b64encode(Path(__file__).read_bytes()).decode()
    return (
        "import base64,sys,types;"
        "_ops=types.ModuleType('_vpngw_ordinary_operations');"
        "sys.modules[_ops.__name__]=_ops;"
        f"_ops._SOURCE=base64.b64decode({source!r});"
        "exec(compile(_ops._SOURCE,'<ordinary-operations>','exec'),_ops.__dict__);"
    )


class OperationBlocked(RuntimeError):
    """Safe, closed diagnostic; never includes command or configuration contents."""


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def identity(pid: int | None = None) -> dict[str, Any]:
    pid = os.getpid() if pid is None else pid
    # comm may contain spaces and parentheses; field 22 follows its final ')'.
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "start": fields[19], "boot": BOOT.read_text().strip()}


def alive(value: dict[str, Any]) -> bool:
    try:
        return identity(value["pid"]) == value
    except FileNotFoundError:
        return False


def _owner(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"pid", "start", "boot"}
        and type(value["pid"]) is int
        and value["pid"] > 0
        and isinstance(value["start"], str)
        and value["start"].isdigit()
        and isinstance(value["boot"], str)
        and re.fullmatch(r"[a-f0-9-]{36}", value["boot"]) is not None
    )


def _hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


class Journal:
    def __init__(self, path: Path | None = None):
        self.path = JOURNAL if path is None else path

    def read(self) -> dict[str, Any] | None:
        try:
            parent = self.path.parent.lstat()
            if (
                not stat.S_ISDIR(parent.st_mode)
                or parent.st_uid != os.geteuid()
                or parent.st_mode & 0o077
            ):
                raise OperationBlocked("ordinary_operation_store_unsafe")
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise OperationBlocked("ordinary_operation_store_unsafe") from error
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                raise OperationBlocked("ordinary_operation_store_unsafe")
            raw = os.read(fd, 1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError
            value = json.loads(raw)
            if (
                not isinstance(value, dict)
                or set(value)
                != {
                    "schema",
                    "id",
                    "owner",
                    "config",
                    "artifact",
                    "predecessor",
                    "state",
                    "phase",
                    "effects",
                    "network",
                    "purpose",
                }
                or value["purpose"] not in {"ordinary", "ha-handoff"}
                or value["schema"] != SCHEMA
                or re.fullmatch(r"[a-f0-9]{32}", value["id"]) is None
                or not _owner(value["owner"])
                or not all(_hash(value[k]) for k in ("config", "artifact", "predecessor"))
                or value["state"] not in {"active", "unresolved", "complete"}
                or value["phase"] not in PHASES
                or not isinstance(value["effects"], list)
                or len(value["effects"]) > 4096
                or not isinstance(value["network"], dict)
            ):
                raise ValueError
            for effect in value["effects"]:
                if (
                    not isinstance(effect, dict)
                    or effect.get("kind") not in {"process", "unit", "reload", "enable", "netplan"}
                    or effect.get("state") not in {"intent", "accepted", "settled"}
                    or not _owner(effect.get("writer"))
                ):
                    raise ValueError
                extra = set(effect) - {"kind", "state", "writer"}
                kind = effect["kind"]
                if kind == "process":
                    if extra - {"process"} or (
                        "process" in effect and not _owner(effect["process"])
                    ):
                        raise ValueError
                    if effect["state"] == "accepted" and "process" not in effect:
                        raise ValueError
                elif kind == "unit":
                    if (
                        extra - {"unit", "action", "jobs"}
                        or effect.get("unit")
                        not in (
                            *MANAGEMENT,
                            *DATAPLANE,
                            *((*HA_WRITERS, HA_GUARD) if value["purpose"] == "ha-handoff" else ()),
                        )
                        or effect.get("action") not in {"start", "stop", "restart", "reload"}
                    ):
                        raise ValueError
                    jobs = effect.get("jobs")
                    if effect["state"] != "intent" and not jobs:
                        raise ValueError
                    if jobs is not None and (
                        not isinstance(jobs, list)
                        or any(
                            not isinstance(row, list)
                            or len(row) != 5
                            or type(row[0]) is not int
                            or row[0] <= 0
                            or not all(isinstance(item, str) for item in row[1:])
                            for row in jobs
                        )
                    ):
                        raise ValueError
                elif kind == "netplan":
                    if extra != {"network"} or not isinstance(effect["network"], dict):
                        raise ValueError
                elif kind == "enable":
                    if (
                        extra != {"units"}
                        or not isinstance(effect["units"], list)
                        or any(name not in MANAGEMENT for name in effect["units"])
                    ):
                        raise ValueError
                elif extra:
                    raise ValueError
            if value["state"] == "complete" and any(
                e["state"] != "settled" for e in value["effects"]
            ):
                raise ValueError
            return value
        except (ValueError, TypeError, KeyError, OSError) as error:
            raise OperationBlocked("ordinary_operation_corrupt") from error
        finally:
            os.close(fd)

    def write(self, value: dict[str, Any], *, expected: str | None = None) -> None:
        import fcntl

        missing = []
        directory = self.path.parent
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700, exist_ok=True)
            parent_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        self.read()  # validate the existing path and parent before replacement
        lock = os.open(
            self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            info = os.fstat(lock)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                raise OperationBlocked("ordinary_operation_store_unsafe")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            current = self.read()
            if expected is not None and digest(current) != expected:
                raise OperationBlocked("ordinary_operation_owner_changed")
            self._write(value)
        finally:
            os.close(lock)

    def _write(self, value: dict[str, Any]) -> None:
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        name = ".operation-" + os.urandom(12).hex()
        try:
            fd = os.open(
                name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            try:
                raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
                if len(raw) > 1024 * 1024:
                    raise OperationBlocked("ordinary_operation_limit")
                with os.fdopen(fd, "wb", closefd=False) as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(name, self.path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=directory)
            os.close(directory)


def startup_allowed() -> bool:
    """Protect old management services across reboot during a partial upgrade."""
    try:
        value = Journal().read()
        if value is None or value["state"] == "complete":
            return True
        # HA materialization publishes this existing marker only after package
        # preparation and its canonical apply-lock installation. HA admission
        # and fencing continue to be enforced by the HA agent itself.
        if value["purpose"] == "ha-handoff" and Path("/etc/nebius-vpngw/vm-ha-enabled").exists():
            return value["phase"] in {"activate", "verify"}
        if value["purpose"] != "ordinary":
            return False
        import importlib.util

        spec = importlib.util.find_spec("nebius_vpngw")
        if spec is None or spec.origin is None:
            return False
        root = Path(spec.origin).parent
        actual = digest(
            {
                path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(root.rglob("*"))
                if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
            }
        )
        # Only the exact guard-aware artifact may start and wait on the journal.
        return (root / "ordinary_operations.py").is_file() and actual == value["artifact"]
    except Exception:
        return False


def install_startup_guard(*, ha_repair: bool = False) -> None:
    """Durably install an inert precondition before publishing the first guard.

    This changes no running service and performs no manager operation. PID 1
    will read it on reboot; the transaction's later daemon reload loads it for
    normal startup. Without this precondition an old enabled agent could ignore
    a pending journal after reboot, before its replacement is installed.
    """
    source = globals().get("_SOURCE") or Path(__file__).read_bytes()
    helper = Path("/var/lib/nebius-vpngw/ordinary-startup.py")
    raw = (
        b"# nebius-vpngw ordinary startup admission\n"
        + source
        + b"\nif __name__ == '__main__':\n    raise SystemExit(0 if startup_allowed() else 1)\n"
    )
    publications = [(helper, raw, 0o600)]
    condition = (
        b"[Service]\nExecCondition=/usr/bin/python3 -B /var/lib/nebius-vpngw/ordinary-startup.py\n"
    )
    for name in (*MANAGEMENT, *(HA_WRITERS if ha_repair else ())):
        if name.endswith(".service"):
            publications.append(
                (
                    Path("/etc/systemd/system") / (name + ".d") / "ordinary-admission.conf",
                    condition,
                    0o644,
                )
            )
    for path, content, mode in publications:
        for directory in (path.parent, *path.parent.parents):
            if directory.exists():
                info = directory.lstat()
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.geteuid()
                    or info.st_mode & 0o022
                ):
                    raise OperationBlocked("ordinary_startup_guard_unsafe")
        if path.exists():
            info = path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != mode
            ):
                raise OperationBlocked("ordinary_startup_guard_unsafe")
        if path.exists() and path.read_bytes() == content:
            continue
        if path.exists() and not path.read_bytes().startswith(
            b"# nebius-vpngw ordinary startup admission\n" if path == helper else condition
        ):
            raise OperationBlocked("ordinary_startup_guard_conflict")
        missing = []
        directory = path.parent
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o755, exist_ok=True)
            fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        fd, temporary = tempfile.mkstemp(prefix=".ordinary-", dir=path.parent)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(fd)
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)


def require_idle() -> None:
    value = Journal().read()
    if value is not None and value["state"] != "complete":
        raise OperationBlocked(
            "ordinary_operation_in_progress"
            if alive(value["owner"])
            else "ordinary_operation_unresolved"
        )


@contextlib.contextmanager
def mutation_lock() -> Iterator[None]:
    """Guest-side admission, including callers streamed to older guest packages."""
    import fcntl

    ROUTING_LOCK.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    fd = os.open(ROUTING_LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_idle()
        yield
    finally:
        os.close(fd)


def bounded(args: list[str], *, timeout: float, **kwargs: Any) -> subprocess.CompletedProcess:
    """Bound observation helpers; no shell, unlimited pipes, or orphan clients."""
    if timeout <= 0:
        raise TimeoutError("ordinary_operation_deadline")
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            args, stdout=stdout, stderr=stderr, start_new_session=True, **kwargs
        )
        try:
            process.wait(timeout=timeout)
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        output = []
        for stream in (stdout, stderr):
            stream.seek(0)
            raw = stream.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise OperationBlocked("ordinary_operation_output_limit")
            output.append(raw.decode("utf-8"))
        return subprocess.CompletedProcess(args, process.returncode, *output)


class Manager:
    def __init__(self, deadline: float, runner: Runner = bounded):
        self.deadline, self.runner = deadline, runner

    def command(self, args: list[str], *, limit: float = 10) -> str:
        result = self.runner(args, timeout=min(limit, self.deadline - time.monotonic()))
        if result.returncode:
            raise OperationBlocked("ordinary_manager_observation_failed")
        return str(result.stdout).strip()

    def call(self, destination: str, path: str, interface: str, method: str, *args: str) -> Any:
        output = self.command(
            [
                "busctl",
                "--system",
                "--json=short",
                "--timeout=10s",
                "call",
                destination,
                path,
                interface,
                method,
                *args,
            ]
        )
        if method == "Reload" and interface == "org.freedesktop.systemd1.Manager" and not output:
            return {"type": "", "data": []}
        value = json.loads(output)
        if not isinstance(value, dict) or set(value) != {"type", "data"}:
            raise OperationBlocked("ordinary_manager_response_invalid")
        return value

    def system(self, method: str, *args: str) -> Any:
        return self.call(
            "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            method,
            *args,
        )

    def properties(self, destination: str, path: str, interface: str) -> dict[str, Any]:
        value = self.call(
            destination, path, "org.freedesktop.DBus.Properties", "GetAll", "s", interface
        )
        if (
            value["type"] != "a{sv}"
            or len(value["data"]) != 1
            or not isinstance(value["data"][0], dict)
        ):
            raise OperationBlocked("ordinary_manager_response_invalid")
        return {key: item["data"] for key, item in value["data"][0].items()}

    def unit(self, name: str) -> dict[str, Any]:
        # Loading metadata does not enqueue service jobs.
        # ListUnitsByNames only covers loaded units; absence there cannot prove
        # that an inactive failure handler has no executable definition.
        loaded = self.system("LoadUnit", "s", name)
        if loaded["type"] != "o" or len(loaded["data"]) != 1:
            raise OperationBlocked("ordinary_manager_response_invalid")
        path = loaded["data"][0]
        if not isinstance(path, str) or not path.startswith("/"):
            raise OperationBlocked("ordinary_manager_response_invalid")
        value = self.properties("org.freedesktop.systemd1", path, "org.freedesktop.systemd1.Unit")
        if name.endswith(".service"):
            value.update(
                self.properties(
                    "org.freedesktop.systemd1", path, "org.freedesktop.systemd1.Service"
                )
            )
        value["missing"] = value.get("LoadState") == "not-found"
        return value

    def jobs(self) -> list[Any]:
        value = self.system("ListJobs")
        if value["type"] != "a(usssoo)":
            raise OperationBlocked("ordinary_manager_response_invalid")
        return value["data"][0]

    def quiet(
        self,
        names: list[str] | tuple[str, ...],
        *,
        allow_management_restart_wait: bool = False,
    ) -> bool:
        if any(row[1] in names for row in self.jobs()):
            return False
        for name in names:
            unit = self.unit(name)
            restart_wait = (
                allow_management_restart_wait
                and name in MANAGEMENT
                and unit.get("ActiveState") == "activating"
                and unit.get("SubState") == "auto-restart"
                and unit.get("MainPID") == 0
            )
            if (
                unit.get("Job", [None])[0] != 0
                or unit.get("ControlPID", 0) != 0
                or (
                    unit.get("ActiveState") not in {"active", "inactive", "failed"}
                    and not restart_wait
                )
            ):
                return False
        # Manager.Reload is a synchronous D-Bus barrier, not a property.
        return True

    def preflight(self) -> None:
        # Exact API presence instead of an OS/version-only rejection.
        xml = self.command(
            [
                "busctl",
                "--system",
                "--xml-interface",
                "introspect",
                "org.freedesktop.systemd1",
                "/org/freedesktop/systemd1",
                "org.freedesktop.systemd1.Manager",
            ]
        )
        # Admission can plan stopping a known management service between its
        # restart attempts. This is never settlement: execution still journals
        # the stop and proves processes, cgroups and jobs gone before installing.
        if 'name="EnqueueUnitJob"' not in xml or not self.quiet(
            (*MANAGEMENT, *DATAPLANE), allow_management_restart_wait=True
        ):
            raise OperationBlocked("ordinary_manager_not_ready")

    def environment(self) -> dict[str, Any]:
        self.preflight()
        files = {}
        missing_failure_units = set()
        for name in (*MANAGEMENT, *DATAPLANE):
            unit = self.unit(name)
            if any(unit.get(key) for key in ("OnSuccess", "Upholds")):
                raise OperationBlocked("ordinary_service_scope_unknown")
            for failure_name in unit.get("OnFailure", []):
                failure = self.unit(failure_name)
                # FRR ships an optional heartbeat handler that Ubuntu does not
                # install. Only authoritative not-found metadata is inert;
                # loaded, masked, malformed or unsettled handlers remain closed.
                if (
                    failure.get("LoadState") != "not-found"
                    or failure.get("ActiveState") != "inactive"
                    or failure.get("SubState") != "dead"
                    or failure.get("MainPID") != 0
                    or failure.get("ControlPID") != 0
                    or failure.get("Job", [None])[0] != 0
                    or failure.get("FragmentPath")
                    or failure.get("DropInPaths")
                    or failure.get("ControlGroup")
                ):
                    raise OperationBlocked("ordinary_service_scope_unknown")
                missing_failure_units.add(failure_name)
            for text in [unit.get("FragmentPath"), *unit.get("DropInPaths", [])]:
                if not text:
                    continue
                path = Path(text)
                raw = path.read_bytes()
                # This reserved startup precondition is durably installed
                # before reservation and may be loaded lazily by PID 1. It has
                # no dependency effects; installation validates its contents.
                if path.name != "ordinary-admission.conf" or name not in MANAGEMENT:
                    files[str(path)] = hashlib.sha256(raw).hexdigest()
                known = text == "/etc/systemd/system/nebius-vpngw-agent.service.d/override.conf"
                if text in unit.get("DropInPaths", []) and not known and name in MANAGEMENT:
                    if re.search(
                        rb"(?m)^\s*(Wants|Requires|BindsTo|Upholds|OnFailure|OnSuccess)\s*=\s*\S",
                        raw,
                    ):
                        raise OperationBlocked("ordinary_service_scope_unknown")
        return {
            "network": self.network(),
            "unit_files": files,
            "missing_failure_units": sorted(missing_failure_units),
        }

    def management_admitted(self) -> bool:
        return all(
            not set(self.unit(name).get("Wants", []) + self.unit(name).get("Requires", []))
            & set(DATAPLANE)
            for name in MANAGEMENT
        )

    def stopped(self, name: str) -> bool:
        unit = self.unit(name)
        if (
            unit.get("ActiveState") not in {"inactive", "failed"}
            or unit.get("MainPID", 0)
            or unit.get("ControlPID", 0)
            or unit.get("Job", [None])[0] != 0
        ):
            return False
        group = unit.get("ControlGroup")
        if group:
            root = Path("/sys/fs/cgroup") / str(group).lstrip("/")
            for path in root.rglob("cgroup.procs"):
                if path.read_text().strip():
                    return False
        return True

    def unit_finished(self, effect: dict[str, Any]) -> bool:
        names = [effect["unit"], *[row[2] for row in effect.get("jobs", [])]]
        if not self.quiet(names):
            return False
        if effect["action"] == "stop":
            return self.stopped(effect["unit"])
        return True

    def unit_settled(self, effect: dict[str, Any]) -> bool:
        if not self.unit_finished(effect):
            return False
        if effect["action"] == "stop":
            return True
        unit = self.unit(effect["unit"])
        if unit.get("ActiveState") != "active":
            return False
        result = "ReloadResult" if effect["action"] == "reload" else "Result"
        return not effect["unit"].endswith(".service") or unit.get(result) == "success"

    def network(self) -> dict[str, Any]:
        # PyYAML is an existing image bootstrap dependency, not an agent import.
        raw = self.command(
            [
                "python3",
                "-B",
                "-c",
                "import json,subprocess,yaml; print(json.dumps(yaml.safe_load(subprocess.check_output(['netplan','get']))))",
            ]
        )
        cfg = json.loads(raw).get("network", {})
        if (
            not isinstance(cfg, dict)
            or set(cfg) - {"version", "renderer", "ethernets"}
            or cfg.get("renderer", "networkd") != "networkd"
        ):
            raise OperationBlocked("ordinary_network_scope_unknown")
        for ethernet in cfg.get("ethernets", {}).values():
            if (
                not isinstance(ethernet, dict)
                or ethernet.get("renderer", "networkd") != "networkd"
                or set(ethernet)
                & {"openvswitch", "virtual-function-count", "embedded-switch-mode", "link"}
            ):
                raise OperationBlocked("ordinary_network_scope_unknown")
        inputs = {}
        for directory in ("/lib/netplan", "/etc/netplan", "/run/netplan"):
            for path in sorted(Path(directory).glob("*.yaml")):
                if path.is_symlink():
                    raise OperationBlocked("ordinary_network_scope_unknown")
                inputs[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        rows = self.call(
            "org.freedesktop.network1",
            "/org/freedesktop/network1",
            "org.freedesktop.network1.Manager",
            "ListLinks",
        )["data"][0]
        links = []
        for index, name, path in rows:
            props = self.properties(
                "org.freedesktop.network1", path, "org.freedesktop.network1.Link"
            )
            state = props.get("AdministrativeState")
            if state == "unmanaged" or name == "lo":
                continue
            if name != "eth0" or state != "configured":
                raise OperationBlocked("ordinary_network_not_settled")
            addresses = json.loads(self.command(["ip", "-j", "address", "show", "dev", name]))
            routes = json.loads(self.command(["ip", "-j", "route", "show", "default", "dev", name]))
            link = addresses[0]
            links.append(
                {
                    "index": index,
                    "name": name,
                    "mac": link["address"],
                    "addresses": sorted(
                        a["local"] for a in link["addr_info"] if a["scope"] == "global"
                    ),
                    "defaults": sorted(
                        [r.get("dst"), r.get("gateway"), r.get("dev")] for r in routes
                    ),
                }
            )
        if len(links) != 1 or not links[0]["addresses"]:
            raise OperationBlocked("ordinary_network_scope_unknown")
        # Admit only definitions bound to the supported physical interface.
        # A dormant or wildcard second-NIC definition is unsafe before apply.
        for name, ethernet in cfg.get("ethernets", {}).items():
            match = ethernet.get("match", {})
            if (
                ethernet.get("set-name", "eth0") != "eth0"
                or not isinstance(match, dict)
                or set(match) - {"name", "macaddress"}
                or (not match and name != "eth0")
                or (match.get("name", "eth0") != "eth0")
                or str(match.get("macaddress", links[0]["mac"])).lower() != links[0]["mac"].lower()
            ):
                raise OperationBlocked("ordinary_network_scope_unknown")
        return {"inputs": inputs, "links": links}


class Operation:
    """A root operation or a restricted synchronous child; never a retry queue."""

    def __init__(
        self,
        value: dict[str, Any],
        manager: Manager,
        journal: Journal | None = None,
        *,
        child: bool = False,
    ):
        self.value, self.manager = value, manager
        self.journal = Journal() if journal is None else journal
        self.child = child
        self.child_phase = value["phase"]
        self._observed = digest(value)

    @classmethod
    def begin(
        cls,
        *,
        request: str,
        config: str,
        artifact: str,
        predecessor: str,
        manager: Manager,
        network: dict[str, Any],
        previous: str | None = None,
        journal: Journal | None = None,
        purpose: str = "ordinary",
    ) -> Operation:
        journal = Journal() if journal is None else journal
        old = journal.read()
        if old and old["state"] != "complete":
            if previous != digest(old) or not recoverable(old, manager):
                raise OperationBlocked("ordinary_operation_unresolved")
        value = {
            "schema": SCHEMA,
            "purpose": purpose,
            "id": request,
            "owner": identity(),
            "config": config,
            "artifact": artifact,
            "predecessor": predecessor,
            "state": "active",
            "phase": "quiesce",
            "effects": [],
            "network": network,
        }
        journal.write(value, expected=digest(old))
        return cls(value, manager, journal)

    @classmethod
    def join(cls, request: dict[str, Any], artifact: str, manager: Manager) -> Operation:
        value = Journal().read()
        if (
            value is None
            or value["purpose"] != "ordinary"
            or value["id"] != request["request_id"]
            or value["config"] != request["config_sha256"]
            or value["artifact"] != artifact
            or value["owner"]["boot"] != request["boot_id"]
        ):
            raise OperationBlocked("ordinary_operation_child_rejected")
        result = cls(value, manager, child=True)
        result.check()
        return result

    def check(self) -> dict[str, Any]:
        value = self.journal.read()
        if (
            value is None
            or any(
                value[k] != self.value[k]
                for k in ("id", "owner", "config", "artifact", "predecessor")
            )
            or value["state"] != "active"
            or not alive(value["owner"])
            or (
                self.child
                and (
                    value["phase"] != self.child_phase
                    or value["phase"] not in {"reconcile", "verify"}
                )
            )
        ):
            raise OperationBlocked("ordinary_operation_owner_changed")
        self._observed = digest(value)
        return value

    def save(self, value: dict[str, Any]) -> None:
        self.journal.write(value, expected=self._observed)
        self.value = value
        self._observed = digest(value)

    def phase(self, phase: str) -> None:
        value = self.check()
        if self.child or phase not in PHASES:
            raise OperationBlocked("ordinary_operation_phase_invalid")
        value["phase"] = phase
        self.save(value)

    def intent(self, kind: str, **fields: Any) -> int:
        value = self.check()
        effect = {"kind": kind, "state": "intent", "writer": identity(), **fields}
        value["effects"].append(effect)
        self.save(value)
        return len(value["effects"]) - 1

    def update(self, index: int, **fields: Any) -> None:
        value = self.check()
        value["effects"][index].update(fields)
        self.save(value)

    def process_started(self, index: int, pid: int) -> None:
        self.update(index, process=identity(pid), state="accepted")

    def service(self, action: str, name: str, *, timeout: float = 45) -> None:
        if action not in {"start", "stop", "restart", "reload"} or name not in (
            *MANAGEMENT,
            *DATAPLANE,
            *((*HA_WRITERS, HA_GUARD) if self.value["purpose"] == "ha-handoff" else ()),
        ):
            raise OperationBlocked("ordinary_service_scope_unknown")
        index = self.intent("unit", unit=name, action=action)
        result = self.manager.system("EnqueueUnitJob", "sss", name, action, "fail")
        if result["type"] != "uososa(uosos)" or len(result["data"]) != 6:
            raise OperationBlocked("ordinary_manager_response_invalid")
        job, path, unit, unit_path, kind, affected = result["data"]
        if unit != name or kind not in ({"restart", "start"} if action == "restart" else {action}):
            raise OperationBlocked("ordinary_manager_response_invalid")
        self.update(index, jobs=[[job, path, unit, unit_path, kind], *affected], state="accepted")
        end = min(self.manager.deadline, time.monotonic() + timeout)
        while time.monotonic() < end:
            self.check()
            effect = self.value["effects"][index]
            if self.manager.unit_finished(effect):
                succeeded = self.manager.unit_settled(effect)
                self.update(index, state="settled")
                if not succeeded:
                    raise OperationBlocked("ordinary_service_failed")
                return
            time.sleep(min(0.1, max(0, end - time.monotonic())))
        raise TimeoutError("ordinary_service_unresolved")

    def reload_manager(self) -> None:
        index = self.intent("reload")
        self.manager.system("Reload")
        if not self.manager.quiet((*MANAGEMENT, *DATAPLANE)):
            raise OperationBlocked("ordinary_manager_not_settled")
        self.update(index, state="settled")

    def enable(self, names: list[str]) -> None:
        index = self.intent("enable", units=names)
        self.manager.system("EnableUnitFiles", "asbb", str(len(names)), *names, "false", "false")
        self.update(index, state="settled")

    def before_netplan(self) -> int:
        network = self.manager.network()
        old = self.value["network"]
        own = "/etc/netplan/99-nebius-vpngw.yaml"
        if network["links"] != old["links"] or {
            k: v for k, v in network["inputs"].items() if k != own
        } != {k: v for k, v in old["inputs"].items() if k != own}:
            raise OperationBlocked("ordinary_network_inputs_changed")
        return self.intent("netplan", network=network)

    def after_netplan(self, index: int) -> None:
        # The process group has been joined before this observation; networkd
        # can still be configuring links after both command and PID1 jobs exit.
        while time.monotonic() < self.manager.deadline:
            self.check()
            try:
                network = self.manager.network()
                expected = self.value["effects"][index]["network"]
                if network == expected and self.manager.quiet(DATAPLANE):
                    self.update(index, state="settled")
                    return
            except OperationBlocked:
                pass
            time.sleep(0.1)
        raise TimeoutError("ordinary_network_unresolved")

    def complete(self) -> None:
        value = self.check()
        if self.child or any(e["state"] != "settled" for e in value["effects"]):
            raise OperationBlocked("ordinary_operation_unresolved")
        value["state"] = "complete"
        try:
            self.save(value)
        except Exception:
            # Keep admission conservative if publication/fsync failed. The
            # original durable active record also survives a pre-replace crash.
            value["state"] = "unresolved"
            self.journal.write(value)
            raise

    def fail(self) -> None:
        value = self.journal.read()
        if value and value["id"] == self.value["id"] and value["state"] != "complete":
            expected = digest(value)
            value["state"] = "unresolved"
            self.journal.write(value, expected=expected)


def process_group_empty(process: dict[str, Any]) -> bool:
    """Prove no writer remains, including descendants after the leader exits."""
    if not _owner(process):
        return False
    if process["boot"] != BOOT.read_text().strip():
        return True
    try:
        os.killpg(process["pid"], 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    if not Path("/proc/self/stat").exists():
        return False
    # A zombie cannot write; init may reap it after our cleanup deadline.
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(") ", 1)[1].split()
            if int(fields[2]) == process["pid"] and fields[0] != "Z":
                return False
        except FileNotFoundError:
            continue
        except (OSError, ValueError, IndexError):
            return False
    return True


def handoff_recoverable(value: dict[str, Any], manager: Manager) -> bool:
    """Handoff recovery has its own admission, never ordinary recovery authority."""
    if value["purpose"] != "ha-handoff" or alive(value["owner"]):
        return False
    new_boot = value["owner"]["boot"] != BOOT.read_text().strip()
    for effect in value["effects"]:
        if alive(effect["writer"]):
            return False
        if effect["kind"] == "process":
            if not new_boot and not process_group_empty(effect.get("process", {})):
                return False
        elif effect["kind"] == "unit":
            if (
                not new_boot
                and effect["state"] != "settled"
                and (effect["state"] == "intent" or not manager.unit_finished(effect))
            ):
                return False
        elif effect["kind"] == "reload":
            # Reload is synchronous. A same-boot lost acknowledgement remains
            # ambiguous; a settled barrier need not be repeated during inspection.
            if effect["state"] != "settled" and not new_boot:
                return False
        else:
            # The handoff owns no network or other external effects.
            return False
    return manager.quiet((*MANAGEMENT, *HA_WRITERS, *DATAPLANE))


def recoverable(value: dict[str, Any], manager: Manager) -> bool:
    """Read-only settlement; never clears evidence or resubmits previous work."""
    if value["purpose"] != "ordinary" or alive(value["owner"]):
        return False
    boot_changed = value["owner"]["boot"] != BOOT.read_text().strip()
    for effect in value["effects"]:
        if alive(effect["writer"]):
            return False
        if effect["state"] == "settled":
            continue
        if boot_changed:
            continue  # old IDs cannot be reused; fresh manager/network proof below
        if effect["kind"] == "process":
            process = effect.get("process")
            if not _owner(process) or alive(process):
                return False
            # A leader disappearing does not prove its process group is empty.
            try:
                os.killpg(process["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                return False
        elif effect["kind"] == "unit" and effect["state"] == "accepted":
            if not manager.unit_finished(effect):
                return False
        elif effect["kind"] == "netplan" and effect["state"] == "accepted":
            if manager.network() != effect["network"]:
                return False
        else:
            return False  # lost acknowledgement: do not guess transaction identity
    return manager.quiet((*MANAGEMENT, *DATAPLANE)) and bool(manager.network())


def observation(manager: Manager) -> dict[str, Any] | None:
    value = Journal().read()
    if value is None or value["state"] == "complete":
        return None
    ready = False
    try:
        ready = recoverable(value, manager)
    except Exception:
        pass
    return {
        "digest": digest(value),
        "recoverable": ready,
        "status": "review_required" if ready else "unresolved",
    }
