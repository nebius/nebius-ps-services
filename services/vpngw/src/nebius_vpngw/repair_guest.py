"""Package-independent guest fencing and scoped full-repair reset primitives.

The confirmed repair owner supplies exact exclusion and cloud settlement. This
module never chooses a VM, grants cloud authority, or deletes a lock inode.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

try:
    import _vpngw_repair_state as storage  # type: ignore[import-not-found]
    import _vpngw_underlay_state as underlay_state  # type: ignore[import-not-found]
except ModuleNotFoundError:
    from . import repair_state as storage
    from . import underlay_state

ROOT = Path("/var/lib/nebius-vpngw-repair")
SYSTEMD = Path("/etc/systemd/system")
PRODUCT_STATE = Path("/var/lib/nebius-vpngw")
PRODUCT_CONFIG = Path("/etc/nebius-vpngw")
BOOT = Path("/proc/sys/kernel/random/boot_id")
FORWARDING = Path("/proc/sys/net/ipv4/ip_forward")
DEPLOY_LOCK = Path("/run/lock/nebius-vpngw-deploy.lock")
ROUTING_LOCK = Path("/run/nebius-vpngw/fix-routes.lock")
POLICY = Path("/usr/sbin/policy-rc.d")
POLICY_BYTES = b"#!/bin/sh\n# nebius-vpngw explicit full repair\nexit 101\n"
BOOT_UNIT = "nebius-vpngw-repair-fence.service"
SSH_ORDERING = (
    b"[Unit]\nRequires=nebius-vpngw-ingress.service\nAfter=nebius-vpngw-ingress.service\n"
)
UNITS = (
    "nebius-vpngw-underlay.service",
    "nebius-vpngw-vm-ha-guard.service",
    "nebius-vpngw-vm-ha-rearm.service",
    "nebius-vpngw-vm-ha.service",
    "nebius-vpngw-health-monitor.service",
    "nebius-vpngw-fix-routes.timer",
    "nebius-vpngw-fix-routes.service",
    "nebius-vpngw-agent.service",
    "nebius-vpngw-ingress.service",
    "nebius-vpngw-esp4-preflight.service",
    "strongswan.service",
    "strongswan-starter.service",
    "strongswan-swanctl.service",
    "frr.service",
)
# These inodes remain in place even when the surrounding state is archived.
PRESERVED_LOCKS = (
    Path("vm-ha/rearm.lock"),
    Path("vm-ha/mtls/writer.lock"),
    Path("ordinary/operation.lock"),
)
ORDINARY_AUTHORITY = (Path("ordinary/cloud-routes"), Path("ordinary/routes"))
ORDINARY_CREDENTIALS = (Path("ordinary-route-binding.json"), Path("ordinary-route-credentials"))
GENERATED_FILES = (
    "/etc/systemd/system/ufw.service.d/override.conf",
    "/etc/netplan/99-nebius-vpngw.yaml",
    "/etc/swanctl/swanctl.conf",
    "/etc/frr/bgpd.conf",
    "/etc/frr/frr.conf",
    "/etc/frr/daemons",
    "/etc/strongswan.d/nebius-vpngw.conf",
    "/etc/strongswan.d/charon/zz-nebius-vpngw-ha.conf",
    "/usr/local/lib/nebius-vpngw-ingress.py",
    "/usr/local/bin/setup-vpngw-firewall.sh",
    "/usr/local/bin/nebius-vpngw-vm-ha-peer-firewall.sh",
    "/usr/local/bin/nebius-vpngw-esp4-preflight.sh",
    "/usr/lib/tmpfiles.d/nebius-vpngw-ufw-lock.conf",
)
GUARD = b"""import json,sys
from pathlib import Path
root=Path("/var/lib/nebius-vpngw-repair")
try:
    value=json.loads((root/"operation.json").read_bytes())
    allowed=json.loads((root/"startup.json").read_bytes())
    ok=(value["schema"]=="nebius-vpngw.repair.v1"
        and allowed["operation"]==value["id"]
        and allowed["boot"]==Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        and value["phase"] in {"installed","enrolled","prepared","originating","verified","complete"}
        and sys.argv[1] in allowed["units"])
except (OSError,ValueError,KeyError,TypeError,IndexError):
    ok=False
raise SystemExit(0 if ok else 1)
"""
BOOT_GUARD = b"""import json
from pathlib import Path
root=Path("/var/lib/nebius-vpngw-repair")
try:
    value=json.loads((root/"operation.json").read_bytes())
    done=value["schema"]=="nebius-vpngw.repair.v1" and value["phase"]=="complete" and not (root/"inhibited").exists()
except (OSError,ValueError,KeyError,TypeError):
    done=False
if not done:
    for name in ("/proc/sys/net/ipv4/ip_forward","/proc/sys/net/ipv6/conf/all/forwarding"):
        path=Path(name)
        if path.exists():
            path.write_text("0\\n")
            if path.read_text().strip()!="0":
                raise RuntimeError("Repair boot forwarding fence did not persist")
"""
BOOT_SERVICE = b"""[Unit]
Description=VPN gateway repair forwarding fence
DefaultDependencies=no
After=local-fs.target systemd-sysctl.service
Before=network-pre.target
Wants=network-pre.target

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -B /var/lib/nebius-vpngw-repair/boot.py
RemainAfterExit=yes

[Install]
WantedBy=sysinit.target
"""


def service_guard() -> bytes:
    return (
        f"[Unit]\nRequires={BOOT_UNIT}\nAfter={BOOT_UNIT}\n"
        "[Service]\nExecCondition=/usr/bin/python3 -B "
        "/var/lib/nebius-vpngw-repair/startup.py %n\n"
    ).encode()


def command(
    args: list[str], *, limit: float = 60, required: bool = True, capture_stderr: bool = False
) -> tuple[int, str]:
    result = subprocess.run(
        args,
        capture_output=True,
        timeout=limit,
        env={
            **os.environ,
            "LC_ALL": "C",
            "DEBIAN_FRONTEND": "noninteractive",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    output = result.stdout + (result.stderr if capture_stderr else b"")
    if len(output) > storage.LIMIT:
        raise RuntimeError("Repair command output exceeds limit")
    if required and result.returncode:
        raise RuntimeError("Repair guest command did not complete")
    return result.returncode, output.decode("utf-8", errors="strict")


def publish_file(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    """Only caller-selected product files; no symlink following or broad removal."""
    for part in (path.parent, *path.parent.parents):
        if part.is_symlink():
            raise RuntimeError("Repair file parent contains a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if path.is_symlink():
        raise RuntimeError("Repair file is a symlink")
    temporary = path.with_name("." + path.name + ".repair-" + os.urandom(8).hex())
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        storage.sync(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def lock(path: Path = DEPLOY_LOCK, *, private_parent: bool = False) -> Iterator[None]:
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            raise RuntimeError("Repair lock parent contains a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700 if private_parent else 0o777)
    parent_fd = None
    fd = None
    try:
        if private_parent:
            parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            parent_info = os.fstat(parent_fd)
            if parent_info.st_uid != os.geteuid() or parent_info.st_mode & 0o022:
                raise RuntimeError("Repair private lock directory is unsafe")
        fd = os.open(
            path.name if parent_fd is not None else path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise RuntimeError("Repair deployment lock is unsafe")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("An existing guest deployment writer is still settling") from error
        if parent_fd is not None:
            # Explicit reconstruction may repair an owned readable directory,
            # after excluding its writer, without replacing the canonical inode.
            os.fchmod(parent_fd, 0o700)
            os.fsync(parent_fd)
        yield
    finally:
        if fd is not None:
            os.close(fd)
        if parent_fd is not None:
            os.close(parent_fd)


def archive_directory(path: Path) -> None:
    """Make only owned repair archives private, including interrupted creation."""
    relative = path.relative_to(ROOT / "archives")
    if ".." in relative.parts:
        raise RuntimeError("Repair archive directory is outside its owned storage")
    storage.directory(ROOT)
    current = ROOT
    for part in ("archives", *relative.parts):
        current = current / part
        current.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise RuntimeError("Repair archive directory is not safely owned")
            os.fchmod(fd, 0o700)
            os.fsync(fd)
        finally:
            os.close(fd)
        storage.sync(current.parent)


def retire_ssh_ordering(operation: str) -> None:
    """Retire only the exact product dependency before archiving its service."""
    selected = []
    for unit in ("ssh.service", "ssh.socket"):
        path = SYSTEMD / (unit + ".d") / "40-vpngw-ingress.conf"
        if any(parent.is_symlink() for parent in path.parents):
            raise RuntimeError("Repair requires review of modified SSH ingress ordering")
        if not path.exists() and not path.is_symlink():
            continue
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or path.read_bytes() != SSH_ORDERING
        ):
            raise RuntimeError("Repair requires review of modified SSH ingress ordering")
        selected.append((unit, path))
    for unit, path in selected:
        archive = ROOT / "archives" / operation / "ssh-ordering" / unit
        archive_directory(archive.parent)
        if archive.exists() or archive.is_symlink():
            if archive.is_symlink() or archive.read_bytes() != SSH_ORDERING:
                raise RuntimeError("Repair SSH ordering archive changed")
            path.unlink()
        else:
            os.rename(path, archive)
        storage.sync(path.parent)
        storage.sync(archive.parent)


def install_guard(operation: str, *, preserve_services: bool = False, run=command) -> None:
    value = storage.Journal(ROOT).load()
    if (
        value is None
        or value["id"] != operation
        or value["phase"] == "complete"
        and not preserve_services
    ):
        raise RuntimeError("Repair guard requires the confirmed guest operation")
    retire_ssh_ordering(operation)
    publish_file(ROOT / "startup.py", GUARD, mode=0o600)
    # Inhibit before stopping services; a concurrent restart must fail closed.
    storage.publish(
        ROOT / "startup.json",
        {"operation": operation, "boot": BOOT.read_text().strip(), "units": []},
    )
    publish_file(ROOT / "boot.py", BOOT_GUARD, mode=0o600)
    publish_file(SYSTEMD / BOOT_UNIT, BOOT_SERVICE)
    for unit in UNITS:
        # Explicit repair owns this service footprint, including damaged masks
        # and overrides. Retain opaque originals outside the reset footprint.
        archive = ROOT / "archives" / operation / "systemd" / unit
        path = SYSTEMD / unit
        storage.directory(archive)
        if not preserve_services and (path.exists() or path.is_symlink()):
            original = archive / "unit"
            if original.exists() or original.is_symlink():
                raise RuntimeError("Repair service original changed during guard installation")
            os.rename(path, original)
            storage.sync(SYSTEMD)
            storage.sync(archive)
        dropins = SYSTEMD / (unit + ".d")
        if not preserve_services:
            archive_tree(dropins, archive / "dropins", preserve=(Path("00-vpngw-repair.conf"),))
        guard = (
            b"[Unit]\nConditionPathExists=!/var/lib/nebius-vpngw-repair/inhibited\n"
            if unit.endswith(".timer")
            else service_guard()
        )
        publish_file(dropins / "00-vpngw-repair.conf", guard)
    publish_file(ROOT / "inhibited", b"inhibited\n", mode=0o600)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", BOOT_UNIT])
    run(["systemctl", "restart", BOOT_UNIT])
    fence(run=run)


def verify_guard(operation: str, *, run=command) -> None:
    value = storage.Journal(ROOT).load()
    if value is None or value["id"] != operation:
        raise RuntimeError("Repair boot protection belongs to another operation")
    expected = {
        ROOT / "startup.py": GUARD,
        ROOT / "boot.py": BOOT_GUARD,
        SYSTEMD / BOOT_UNIT: BOOT_SERVICE,
    }
    expected.update(
        {
            SYSTEMD / (unit + ".d") / "00-vpngw-repair.conf": service_guard()
            for unit in UNITS
            if not unit.endswith(".timer")
        }
    )
    if any(path.is_symlink() or path.read_bytes() != raw for path, raw in expected.items()):
        raise RuntimeError("Independent repair boot protection changed")
    _code, enabled = run(["systemctl", "is-enabled", BOOT_UNIT])
    if enabled.strip() != "enabled":
        raise RuntimeError("Independent repair boot protection is not persistent")
    try:
        from_module = importlib.import_module("_vpngw_runtime_migration")
    except ModuleNotFoundError:
        from_module = importlib.import_module(".runtime_migration", __package__)
    for unit in UNITS:
        if unit.endswith(".timer"):
            continue
        _code, raw = run(
            [
                "systemctl",
                "show",
                "--all",
                unit,
                "--property=Id",
                "--property=LoadState",
                "--property=ExecCondition",
            ]
        )
        effective = from_module.command_policy(raw)
        if effective.get("LoadState") == "not-found":
            continue
        # %n expands to systemd's canonical Id even when queried through an
        # installed alias, such as strongswan-swanctl.service. The canonical
        # target must still belong to the exact product service footprint.
        canonical = effective.get("Id")
        if canonical not in UNITS or not canonical.endswith(".service"):
            raise RuntimeError("Effective repair service inhibition changed")
        expected_condition = [
            "/usr/bin/python3",
            "/usr/bin/python3 -B /var/lib/nebius-vpngw-repair/startup.py " + canonical,
            "no",
        ]
        if expected_condition not in effective.get("ExecCondition", []):
            raise RuntimeError("Effective repair service inhibition changed")


def inhibit_package_starts(operation: str) -> None:
    path = ROOT / "package-policy.json"
    raw = storage.read(path)
    if raw is None:
        original = None
        if POLICY.exists() or POLICY.is_symlink():
            info = POLICY.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o022
            ):
                raise RuntimeError("Existing package startup policy is not safely owned")
            data = POLICY.read_bytes()
            # A retired product transaction must not leave its global policy
            # permanently installed after the owning state is archived.
            if data != b"#!/bin/sh\n# nebius-vpngw approved runtime transaction\nexit 101\n":
                original = {
                    "content": base64.b64encode(data).decode(),
                    "mode": stat.S_IMODE(info.st_mode),
                }
            else:
                # Recover the unrelated pre-existing policy before retiring the
                # normal transaction's state. No old lifecycle authority is used.
                try:
                    predecessor = json.loads(
                        storage.read(PRODUCT_STATE / "runtime-migration/operation.json") or b"null"
                    )
                    saved = predecessor.get("policy") if isinstance(predecessor, dict) else None
                    if saved is not None:
                        if (
                            set(saved) != {"content", "mode"}
                            or type(saved["mode"]) is not int
                            or saved["mode"] & ~0o755
                        ):
                            raise ValueError("invalid old startup policy")
                        base64.b64decode(saved["content"], validate=True)
                        original = saved
                except (OSError, ValueError, TypeError, RuntimeError):
                    # Malformed old bytes are preserved by reset. They cannot
                    # grant execution or prevent replacement of our own sentinel.
                    original = None
        storage.publish(path, {"operation": operation, "original": original})
    else:
        if json.loads(raw)["operation"] != operation:
            raise RuntimeError("Package startup policy belongs to another repair")
    publish_file(POLICY, POLICY_BYTES, mode=0o755)


def restore_package_policy(operation: str) -> None:
    value = json.loads(storage.read(ROOT / "package-policy.json") or b"null")
    if not value or value["operation"] != operation:
        raise RuntimeError("Package startup policy has no matching repair owner")
    original = value["original"]
    current = POLICY.read_bytes() if POLICY.exists() else None
    restored = base64.b64decode(original["content"], validate=True) if original else None
    if current not in (POLICY_BYTES, restored):
        raise RuntimeError("Package startup policy changed outside repair")
    if original:
        assert restored is not None
        publish_file(POLICY, restored, mode=original["mode"])
    else:
        POLICY.unlink(missing_ok=True)
        storage.sync(POLICY.parent)


def defer_route_timer(*, run=command) -> bool:
    """Leave the timer to repair completion while its exact guard owns startup."""
    path = SYSTEMD / "nebius-vpngw-fix-routes.timer.d/00-vpngw-repair.conf"
    if not path.exists() and not path.is_symlink():
        return False
    value = storage.Journal(ROOT).load()
    raw = storage.read(ROOT / "startup.json")
    allowed = json.loads(raw) if raw is not None else None
    if (
        path.is_symlink()
        or path.read_bytes()
        != b"[Unit]\nConditionPathExists=!/var/lib/nebius-vpngw-repair/inhibited\n"
        or value is None
        or value["intent"]["ha"]
        or value["phase"] not in {"installed", "enrolled", "prepared", "originating", "verified"}
        or not isinstance(allowed, dict)
        or allowed.get("operation") != value["id"]
        or allowed.get("boot") != BOOT.read_text().strip()
        or not isinstance(allowed.get("units"), list)
        or not {"nebius-vpngw-fix-routes.timer", "nebius-vpngw-fix-routes.service"}.issubset(
            allowed["units"]
        )
        or storage.read(ROOT / "inhibited") != b"inhibited\n"
    ):
        raise RuntimeError("Repair timer completion authority is unverified")
    verify_guard(value["id"], run=run)
    return True


def finish(operation: str, *, run=command) -> None:
    value = storage.Journal(ROOT).load()
    if value is None or value["id"] != operation or value["phase"] != "complete":
        raise RuntimeError("Repair boot guard cleanup requires exact completion")
    restore_package_policy(operation)
    for unit in UNITS:
        path = SYSTEMD / (unit + ".d") / "00-vpngw-repair.conf"
        expected = (
            b"[Unit]\nConditionPathExists=!/var/lib/nebius-vpngw-repair/inhibited\n"
            if unit.endswith(".timer")
            else service_guard()
        )
        if path.exists() or path.is_symlink():
            if path.is_symlink() or path.read_bytes() != expected:
                raise RuntimeError("Repair guard changed before cleanup")
            path.unlink()
            storage.sync(path.parent)
    path = SYSTEMD / BOOT_UNIT
    if path.exists():
        if path.is_symlink() or path.read_bytes() != BOOT_SERVICE:
            raise RuntimeError("Repair boot unit changed before cleanup")
        run(["systemctl", "disable", BOOT_UNIT])
        path.unlink()
        storage.sync(SYSTEMD)
    (ROOT / "inhibited").unlink(missing_ok=True)
    storage.sync(ROOT)
    run(["systemctl", "daemon-reload"])
    if service_state("nebius-vpngw-fix-routes.timer", run=run)["LoadState"] != "not-found":
        run(["systemctl", "start", "nebius-vpngw-fix-routes.timer"])
        run(["systemctl", "is-active", "--quiet", "nebius-vpngw-fix-routes.timer"])


def fence(*, run=command) -> None:
    run(["sysctl", "-w", "net.ipv4.ip_forward=0"])
    if Path("/proc/sys/net/ipv6/conf/all/forwarding").exists():
        run(["sysctl", "-w", "net.ipv6.conf.all.forwarding=0"])
    if FORWARDING.read_text().strip() != "0":
        raise RuntimeError("Repair forwarding fence was not established")


def service_state(unit: str, *, run=command) -> dict:
    _code, output = run(
        [
            "systemctl",
            "show",
            unit,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=MainPID",
            "--property=ControlPID",
            "--property=Job",
            "--property=ControlGroup",
        ]
    )
    result = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    required = {"LoadState", "ActiveState", "Job"}
    if not unit.endswith(".timer"):
        required |= {"MainPID", "ControlPID", "ControlGroup"}
    if not required.issubset(result):
        raise RuntimeError("Repair service observation is incomplete")
    if unit.endswith(".timer"):
        # Timers have activation state and queued jobs, not a process or cgroup.
        # Their triggered service is separately present in the owned footprint.
        result.update(MainPID="0", ControlPID="0", ControlGroup="")
    return result


def prior_package_units(*, run=command) -> tuple[str, ...]:
    _code, output = run(
        [
            "systemctl",
            "list-units",
            "--all",
            "--full",
            "--plain",
            "--no-legend",
            "nebius-vpngw-package-*.service",
        ]
    )
    names = []
    for line in output.splitlines():
        if not line.strip():
            continue
        name = line.split()[0]
        if not re.fullmatch(r"nebius-vpngw-package-[a-f0-9]{64}-[1-9][0-9]*\.service", name):
            raise RuntimeError("Prior product package worker identity is invalid")
        names.append(name)
    return tuple(sorted(set(names)))


def verify_quiescent(*, run=command) -> None:
    if FORWARDING.read_text().strip() != "0":
        raise RuntimeError("Repair requires forwarding disabled")
    for unit in (*UNITS, *prior_package_units(run=run)):
        value = service_state(unit, run=run)
        if (
            value["MainPID"] != "0"
            or value["ControlPID"] != "0"
            or value["Job"]
            or value["ActiveState"] not in {"inactive", "failed"}
        ):
            raise RuntimeError("A product service is still settling")
        cgroup = value["ControlGroup"]
        if cgroup:
            path = Path("/sys/fs/cgroup") / cgroup.lstrip("/")
            if ".." in Path(cgroup).parts:
                raise RuntimeError("Repair service cgroup is invalid")
            if path.exists() and any(
                item.read_text().strip() for item in path.rglob("cgroup.procs")
            ):
                raise RuntimeError("A product service descendant is still running")


def stop_writers(*, run=command, timeout: float = 120) -> None:
    # Guard installation archives managed unit overrides. A running service can
    # therefore use systemd's normal 90-second stop timeout after daemon-reload.
    # Allow that shutdown to finish within the existing guest request deadline;
    # never proceed without the process, job and descendant checks below.
    existing = []
    for unit in (*UNITS, *prior_package_units(run=run)):
        value = service_state(unit, run=run)
        if (
            value["LoadState"] != "not-found"
            or value["ActiveState"] not in {"inactive", "failed"}
            or value["MainPID"] != "0"
            or value["ControlPID"] != "0"
            or value["Job"]
        ):
            existing.append(unit)
    if existing:
        run(["systemctl", "--no-block", "stop", *existing])
    deadline = time.monotonic() + timeout
    while True:
        try:
            verify_quiescent(run=run)
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def archive_tree(source: Path, destination: Path, *, preserve: tuple[Path, ...] = ()) -> None:
    """Move opaque product bytes; retained lock ancestors keep their inodes."""
    if not source.exists() and not source.is_symlink():
        return
    if source.is_symlink() or not source.is_dir():
        raise RuntimeError("Repair reset root is not a product directory")
    storage.directory(destination)

    def move(path: Path) -> None:
        relative = path.relative_to(source)
        if relative in preserve:
            return
        containing_lock = any(relative in lock.parents for lock in preserve)
        if containing_lock:
            if path.is_symlink() or not path.is_dir():
                raise RuntimeError("Repair lock ancestor is unsafe")
            for child in sorted(path.iterdir()):
                move(child)
            return
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.exists() or target.is_symlink():
            raise RuntimeError("Repair archive conflicts with an existing snapshot")
        os.rename(path, target)
        storage.sync(path.parent)
        storage.sync(target.parent)

    for path in sorted(source.iterdir()):
        move(path)


def retain_underlay_ownership(operation: str) -> None:
    """Under writer exclusion, retain exact ownership, never prior readiness."""
    value = storage.Journal(ROOT).load()
    if value is None or value["id"] != operation:
        raise RuntimeError("Underlay ownership requires the exact repair operation")
    path = PRODUCT_STATE / "underlay.json"
    archive = ROOT / "archives" / operation / "state" / "underlay.json"
    try:
        try:
            raw = underlay_state.read(path)
            restored = False
        except FileNotFoundError:
            try:
                raw = underlay_state.read(archive)
            except FileNotFoundError:
                underlay_state.verify_files(None, network_root=underlay_state.NETWORK_ROOT)
                return  # A genuinely fresh predecessor has no owned underlay state.
            restored = True
        record = underlay_state.decode(raw, network_root=underlay_state.NETWORK_ROOT)
        underlay_state.verify_files(record, network_root=underlay_state.NETWORK_ROOT)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(
            "Repair underlay ownership is missing or conflicts with retained files"
        ) from error
    # Record newly observed bytes, not a nonexistent predecessor content manifest.
    receipt = {
        "operation": operation,
        "intent": storage.digest(value["intent"]),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    storage.publish(ROOT / "underlay-ownership.json", receipt)
    if restored:
        publish_file(path, raw, mode=0o644)


def reset(operation: str, *, run=command) -> dict:
    journal = storage.Journal(ROOT)
    value = journal.load()
    if value is None or value["id"] != operation or value["phase"] != "staged":
        raise RuntimeError("Repair reset requires staged archives and exact operation")
    verify_quiescent(run=run)
    verify_guard(operation, run=run)
    archive = ROOT / "archives" / operation
    # Old writers cannot re-enter via a newly created inode after reset. The
    # outer durable worker holds DEPLOY_LOCK; retain and acquire the product's
    # remaining writer locks in place while moving their opaque state.
    with contextlib.ExitStack() as held:
        held.enter_context(lock(ROUTING_LOCK))
        for relative in PRESERVED_LOCKS:
            held.enter_context(
                lock(
                    PRODUCT_STATE / relative,
                    private_parent=relative
                    in {Path("vm-ha/mtls/writer.lock"), Path("ordinary/operation.lock")},
                )
            )
        retain_underlay_ownership(operation)
        ordinary = not value["intent"]["ha"]
        archive_tree(
            PRODUCT_CONFIG, archive / "config", preserve=ORDINARY_CREDENTIALS if ordinary else ()
        )
        archive_tree(
            PRODUCT_STATE,
            archive / "state",
            preserve=(
                *PRESERVED_LOCKS,
                Path("underlay.json"),
                *(ORDINARY_AUTHORITY if ordinary else ()),
            ),
        )
        for filename in GENERATED_FILES:
            source = Path(filename)
            if not source.exists() and not source.is_symlink():
                continue
            if any(parent.is_symlink() for parent in source.parents):
                raise RuntimeError("Repair generated asset parent is a symlink")
            destination = archive / "generated" / source.relative_to("/")
            storage.directory(destination.parent)
            if destination.exists() or destination.is_symlink():
                raise RuntimeError("Repair generated asset archive changed")
            os.rename(source, destination)
            storage.sync(source.parent)
            storage.sync(destination.parent)
    receipt = {
        "boot": BOOT.read_text().strip(),
        "guard": hashlib.sha256(GUARD).hexdigest(),
        "operation": operation,
    }
    storage.publish(ROOT / "reset.json", receipt)
    return receipt
