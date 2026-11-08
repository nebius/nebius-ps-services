"""Approved, staged Ubuntu runtime replacement inside the existing apply owners.

No package resolution occurs after inhibition. Partial dpkg effects roll forward
from durable, hash-bound archives; failures retain startup inhibition.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

ROOT = Path("/var/lib/nebius-vpngw/runtime-migration")
STATE = ROOT / "operation.json"
TOKEN = Path("/run/nebius-vpngw/runtime-activation.json")
POLICY = Path("/usr/sbin/policy-rc.d")
REQUIRED = (
    "charon-systemd",
    "strongswan-swanctl",
    "strongswan-pki",
    "libcharon-extra-plugins",
    "nftables",
)
LEGACY = ("strongswan", "strongswan-starter", "strongswan-charon")
UNITS = (
    "strongswan.service",
    "strongswan-starter.service",
    "strongswan-swanctl.service",
    "frr.service",
    "nebius-vpngw-agent.service",
    "nebius-vpngw-health-monitor.service",
    "nebius-vpngw-fix-routes.service",
    "nebius-vpngw-fix-routes.timer",
    "nebius-vpngw-vm-ha.service",
    "nebius-vpngw-vm-ha-rearm.service",
)
SCHEMA = "nebius-vpngw.runtime-migration.v1"
POLICY_BYTES = b"#!/bin/sh\n# nebius-vpngw approved runtime transaction\nexit 101\n"


def product_install_command(wheel: Path) -> list[str]:
    """One product distribution; dependency shadowing has a separate owner."""
    return [
        sys.executable,
        "-B",
        "-m",
        "pip",
        "install",
        "--no-index",
        "--no-deps",
        "--force-reinstall",
        "--break-system-packages",
        str(wheel),
    ]


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# Mutation execution belongs to the existing ordinary/HA operation journal.
COMMAND = None
CHECK = None


def run(args: list[str], *, timeout: int = 60, cwd: Path | None = None) -> str:
    if COMMAND is not None and cwd is None:
        if CHECK is not None:
            CHECK()
        return COMMAND(args, limit=timeout)[1]
    child = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=cwd,
        start_new_session=True,
        env={**os.environ, "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive"},
    )
    try:
        output = bytearray()
        deadline = time.monotonic() + timeout
        assert child.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("runtime package observation deadline expired")
                for key, _events in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > 4 * 1024 * 1024:
                            raise RuntimeError("runtime package observation exceeds limit")
        if child.wait(timeout=max(0.001, deadline - time.monotonic())):
            raise RuntimeError("runtime package observation failed")
        return output.decode()
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)
        if child.stdout is not None:
            child.stdout.close()


def policy_snapshot() -> dict | None:
    try:
        info = POLICY.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("existing startup policy is unsafe")
    return {
        "content": base64.b64encode(POLICY.read_bytes()).decode(),
        "mode": stat.S_IMODE(info.st_mode),
    }


def command_policy(raw: str) -> dict[str, Any]:
    """Keep executable policy, excluding systemd's per-invocation statistics."""
    result: dict[str, Any] = {}
    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            raise RuntimeError("invalid effective unit policy")
        if key.startswith("Exec"):
            records = re.findall(
                r"\{ path=([^;]+?) ; argv\[\]=(.*?) ; ignore_errors=([^;]+?) ;.*?\}", value
            )
            if value and not records:
                raise RuntimeError("unrecognized effective startup command")
            # systemctl emits repeated property lines for command arrays on
            # supported Ubuntu releases. Preserve every command in order.
            result.setdefault(key, []).extend(list(record) for record in records)
        else:
            result[key] = value
    return result


def unit_policy() -> dict:
    # Capture effective startup commands and operator masks/enabling. ActiveState
    # is deliberately not approval identity; quiescence is checked by the owner.
    return {
        name: command_policy(
            run(
                [
                    "systemctl",
                    "show",
                    "--all",
                    name,
                    "--property=LoadState",
                    "--property=UnitFileState",
                    "--property=FragmentPath",
                    "--property=DropInPaths",
                    "--property=ExecStart",
                    "--property=ExecStartPost",
                    "--property=ExecReload",
                ]
            )
        )
        for name in (*UNITS, "nebius-vpngw-vm-ha-guard.service", "nebius-vpngw-ingress.service")
    }


def startup_states(approved: dict) -> dict[str, str]:
    result = {
        name: value.get("UnitFileState", "") for name, value in approved["unit_policy"].items()
    }
    # The canonical daemon inherits the old daemon's boot policy, not the
    # package maintainer's default for a newly installed unit.
    if approved["before"].get("strongswan-starter", [None, None]) not in (None, [None, None]):
        previous = result.get("strongswan-starter.service", "")
        if previous in {"enabled", "enabled-runtime", "disabled", "masked", "masked-runtime"}:
            result["strongswan.service"] = previous
    return result


def units_to_enable(names: list[str]) -> list[str]:
    value = state()
    previous = startup_states(value["plan"]) if value is not None else {}
    return [
        name
        for name in names
        if previous.get(name, "") not in {"disabled", "masked", "masked-runtime", "enabled-runtime"}
    ]


def enable_units(names: list[str]) -> None:
    """Used by existing activation owners; preserve operator boot policy."""
    names = units_to_enable(names)
    if names:
        run(["systemctl", "enable", *names])


def preserve_unit_policy(approved: dict) -> None:
    current = unit_policy()
    for name, previous in startup_states(approved).items():
        if name in {"strongswan-starter.service", "strongswan-swanctl.service"}:
            continue
        observed = current.get(name, {})
        if observed.get("LoadState") == "not-found" or observed.get("UnitFileState") == previous:
            continue
        if previous == "disabled":
            run(["systemctl", "disable", name])
        elif previous == "enabled-runtime":
            run(["systemctl", "disable", name])
            run(["systemctl", "enable", "--runtime", name])


def unlink(path: Path) -> None:
    path.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise RuntimeError("runtime transaction parent is unsafe")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("runtime transaction path is unsafe")
    fd, name = tempfile.mkstemp(prefix=".runtime-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def save(value: dict) -> None:
    write(STATE, json.dumps(value, sort_keys=True).encode())


def state() -> dict | None:
    try:
        info = STATE.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise RuntimeError("runtime transaction state is unsafe")
    value = json.loads(STATE.read_text())
    if (
        not isinstance(value, dict)
        or value.get("schema") != SCHEMA
        or value.get("phase")
        not in {
            "prepared",
            "inhibited",
            "packages",
            "installed",
            "activating",
            "releasing",
            "complete",
        }
        or value.get("plan_digest") != digest(value.get("plan"))
        or not re.fullmatch(r"[a-f0-9]{32}", value.get("operation", ""))
    ):
        raise RuntimeError("runtime transaction identity is invalid")
    validate_plan(value["plan"])
    return value


def packages() -> dict[str, list[str]]:
    output = run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${db:Status-Status}\\n"])
    result = {}
    for line in output.splitlines():
        name, version, status = line.split("\t")
        if status != "not-installed":
            result[name] = [version, status]
    return result


def native() -> dict[str, Any]:
    current = packages()
    result: dict[str, Any] = {
        "packages": {k: current.get(k) for k in (*REQUIRED, *LEGACY)},
        "ready": False,
    }
    try:
        effective = run(
            [
                "systemctl",
                "show",
                "--all",
                "strongswan.service",
                "--property=ExecStart",
                "--property=ExecStartPost",
                "--property=ExecReload",
                "--property=ActiveState",
            ]
        )
        result["unit"] = effective
        policy = command_policy(effective)
        pid = run(
            ["systemctl", "show", "strongswan.service", "--property=MainPID", "--value"]
        ).strip()
        executable = os.readlink(f"/proc/{int(pid)}/exe") if int(pid) > 0 else ""
        result["executable"] = executable
        result["ready"] = bool(
            all(current.get(k, [None, None])[1] == "installed" for k in REQUIRED)
            and all(
                current.get(k, [None, None])[1] not in {"installed", "unpacked", "half-configured"}
                for k in LEGACY
            )
            and executable == "/usr/sbin/charon-systemd"
            and policy.get("ActiveState") == "active"
            and policy.get("ExecStart")
            == [["/usr/sbin/charon-systemd", "/usr/sbin/charon-systemd", "no"]]
            and policy.get("ExecReload", []) == []
            and policy.get("ExecStartPost")
            == [["/usr/bin/chmod", "/usr/bin/chmod 0600 /run/charon.vici", "no"]]
            and stat.S_ISSOCK(Path("/run/charon.vici").stat().st_mode)
            and Path("/run/charon.vici").stat().st_uid == 0
            and stat.S_IMODE(Path("/run/charon.vici").stat().st_mode) == 0o600
        )
    except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
        result["unit"] = result.get("unit", "unavailable")
    return result


@contextlib.contextmanager
def apt_indexes() -> Iterator[list[str]]:
    """Resolve current signed repositories without changing system APT state.

    A long-lived gateway's cached package versions may have disappeared from
    its mirrors. All index/cache/log writes stay in this temporary workspace;
    global update hooks must not perform unrelated effects during a preview.
    """
    with tempfile.TemporaryDirectory(prefix="vpngw-apt-indexes-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o755)  # Public metadata; retain APT's _apt sandbox.
        (directory / "lists").mkdir(mode=0o755)
        (directory / "log").mkdir(mode=0o755)
        config = directory / "apt.conf"
        config.write_text(
            "#clear APT::Update::Pre-Invoke;\n"
            "#clear APT::Update::Post-Invoke;\n"
            "#clear APT::Update::Post-Invoke-Success;\n"
            f'Dir::State::lists "{directory / "lists"}";\n'
            f'Dir::Log "{directory / "log"}";\n'
            'Dir::Cache::pkgcache "";\n'
            'Dir::Cache::srcpkgcache "";\n'
            'APT::Update::Error-Mode "any";\n'
            'Acquire::Languages "none";\n'
            'Acquire::AllowInsecureRepositories "false";\n'
            'Acquire::AllowDowngradeToInsecureRepositories "false";\n'
            'APT::Get::AllowUnauthenticated "false";\n'
        )
        options = ["-c", str(config)]
        run(["apt-get", *options, "update", "-qq"], timeout=120, cwd=directory)
        yield options


def plan(parent: str | None = None) -> dict:
    existing = state()
    if existing is not None and pending(parent) is not None:
        return existing["plan"]
    before = packages()
    if run(["dpkg", "--audit"]).strip():
        raise RuntimeError("unowned incomplete package work requires reconciliation")
    installed = all(before.get(k, [None, None])[1] == "installed" for k in REQUIRED)
    old = [k for k in LEGACY if before.get(k, [None, None])[1] == "installed"]
    install: dict[str, str] = {}
    remove = []
    archives: dict[str, dict] = {}
    if not installed or old:
        with apt_indexes() as options:
            simulated = run(
                [
                    "apt-get",
                    *options,
                    "-s",
                    "--no-install-recommends",
                    "install",
                    *REQUIRED,
                    *(k + "-" for k in old),
                ]
            )
            for line in simulated.splitlines():
                if line.startswith("Inst "):
                    match = re.match(r"Inst ([A-Za-z0-9:+.-]+)(?: \[[^]]+\])? \(([^ ]+)", line)
                    if match is None:
                        raise RuntimeError("package closure cannot be parsed")
                    install[match[1]] = match[2]
                elif line.startswith("Remv "):
                    name = line.split()[1]
                    if name not in LEGACY:
                        raise RuntimeError("runtime closure removes an unrelated package")
                    remove.append(name)
            for name, version in sorted(install.items()):
                output = run(
                    ["apt-get", *options, "--print-uris", "download", name + "=" + version]
                )
                rows = [shlex.split(line) for line in output.splitlines() if line.startswith("'")]
                if len(rows) != 1 or len(rows[0]) != 4:
                    raise RuntimeError("exact package archive is unavailable")
                _uri, filename, size, _legacy_checksum = rows[0]
                # apt-get --print-uris advertises an MD5 compatibility field. Pin the
                # SHA256 from the same exact authenticated APT package record.
                records = run(["apt-cache", *options, "show", name + "=" + version])
                hashes = set()
                for paragraph in records.strip().split("\n\n"):
                    fields = dict(
                        line.split(": ", 1)
                        for line in paragraph.splitlines()
                        if ": " in line and not line.startswith(" ")
                    )
                    if (
                        fields.get("Package") == name.split(":")[0]
                        and fields.get("Version") == version
                        and fields.get("Size") == size
                        and "Filename" in fields
                    ):
                        hashes.add(fields.get("SHA256", ""))
                if (
                    Path(filename).name != filename
                    or not filename.endswith(".deb")
                    or len(hashes) != 1
                    or not all(re.fullmatch(r"[a-f0-9]{64}", h) for h in hashes)
                ):
                    raise RuntimeError("package archive lacks an unambiguous approved SHA256")
                checksum = hashes.pop()
                archives[filename] = {
                    "package": name,
                    "version": version,
                    "size": int(size),
                    "sha256": checksum,
                }
    desired = {k: before[k] for k in REQUIRED if k in before}
    desired.update({k: [v, "installed"] for k, v in install.items()})
    policy = unit_policy()
    if any(v.get("UnitFileState") in {"masked", "masked-runtime"} for v in policy.values()):
        raise RuntimeError("operator service masks inhibit runtime migration")
    return {
        "unit_policy": policy,
        "startup_policy": policy_snapshot(),
        "schema": SCHEMA,
        "before": {k: before.get(k) for k in sorted(set(desired) | set(remove))},
        "install": install,
        "remove": sorted(remove),
        "archives": archives,
        "desired": desired,
    }


def validate_plan(approved: dict) -> None:
    if (
        not isinstance(approved, dict)
        or set(approved)
        != {
            "schema",
            "before",
            "install",
            "remove",
            "archives",
            "desired",
            "unit_policy",
            "startup_policy",
        }
        or approved["schema"] != SCHEMA
        or not set(approved["remove"]).issubset(LEGACY)
        or not all(
            isinstance(approved[k], dict)
            for k in ("before", "install", "archives", "desired", "unit_policy")
        )
    ):
        raise RuntimeError("runtime plan schema is invalid")
    selected = {}
    for name, item in approved["archives"].items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.endswith(".deb")
            or not isinstance(item, dict)
            or set(item) != {"package", "version", "size", "sha256"}
            or type(item["size"]) is not int
            or item["size"] <= 0
            or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
            or item["package"] in selected
        ):
            raise RuntimeError("runtime archive identity is invalid")
        selected[item["package"]] = item["version"]
    if selected != approved["install"] or not set(REQUIRED).issubset(approved["desired"]):
        raise RuntimeError("runtime closure is incomplete")


def verify_staged(approved: dict) -> Path:
    validate_plan(approved)
    directory = ROOT / digest(approved)
    for name, item in approved["archives"].items():
        path = directory / name
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size != item["size"]
                or hashlib.sha256(stream.read()).hexdigest() != item["sha256"]
            ):
                raise RuntimeError("staged runtime archive identity changed")
            os.fsync(stream.fileno())
    return directory


def stage(approved: dict) -> Path:
    validate_plan(approved)
    directory = ROOT / digest(approved)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    missing = [
        item for name, item in approved["archives"].items() if not (directory / name).exists()
    ]
    if missing:
        if (ROOT / "inhibited").exists():
            raise RuntimeError("inhibited operation has lost its staged archive")
        with apt_indexes() as options:
            for item in missing:
                run(
                    ["apt-get", *options, "download", item["package"] + "=" + item["version"]],
                    timeout=120,
                    cwd=directory,
                )
    verify_staged(approved)
    write(directory / "plan.json", json.dumps(approved, sort_keys=True).encode())
    return directory


def stage_payload(manifest: dict, payload: dict) -> dict:
    directory = stage(manifest["runtime_plan"]) / "payload"
    items = {manifest["wheel_name"]: manifest["wheel_sha256"], **manifest["dependency_wheels"]}
    supplied = {manifest["wheel_name"]: payload.get("wheel")}
    dependencies = payload.get("dependency_wheels", {})
    if isinstance(dependencies, list):
        dependencies = {item["name"]: item["content"] for item in dependencies}
    supplied.update(dependencies)
    for name, expected in items.items():
        path = directory / name
        if path.parent != directory or not name.endswith(".whl"):
            raise RuntimeError("invalid staged product filename")
        if supplied.get(name) is not None:
            encoded = supplied[name]
            assert encoded is not None
            raw = base64.b64decode(encoded, validate=True)
            if hashlib.sha256(raw).hexdigest() != expected:
                raise RuntimeError("staged product artifact changed")
            write(path, raw)
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError("staged product artifact is missing or changed")
    write(directory / "manifest.json", json.dumps(manifest, sort_keys=True).encode())
    return {"manifest": digest(manifest), "runtime_plan": digest(manifest["runtime_plan"])}


def pending(parent: str | None = None) -> dict | None:
    value = state()
    if value is None:
        return None
    parent_pending = parent is not None and value["manifest"].get("ha_parent") == parent
    if value["phase"] == "complete" and not parent_pending:
        try:
            journal = json.loads(Path("/var/lib/nebius-vpngw/ordinary/operation.json").read_text())
        except FileNotFoundError:
            return None
        if journal.get("state") == "complete" or journal.get("id") != value["operation"]:
            return None
    return {"operation": value["operation"], "manifest": value["manifest"], "phase": value["phase"]}


def begin(approved: dict, operation: str, manifest: dict) -> None:
    existing = state()
    if existing is not None and (
        existing["phase"] != "complete" or existing["operation"] == operation
    ):
        if (
            existing["plan"] != approved
            or existing["manifest"] != manifest
            or existing["operation"] != operation
        ):
            raise RuntimeError("interrupted runtime transaction differs from approved artifact")
        return
    if not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise RuntimeError("invalid runtime operation identity")
    current = packages()
    if {k: current.get(k) for k in approved["before"]} != approved["before"]:
        raise RuntimeError("runtime package predecessor changed")
    if policy_snapshot() != approved["startup_policy"] or unit_policy() != approved["unit_policy"]:
        raise RuntimeError("runtime startup policy predecessor changed")
    if any(value in {"masked", "masked-runtime"} for value in startup_states(approved).values()):
        # Publishing a canonical unit must never replace an operator /dev/null
        # mask. Stop before inhibition or package effects; maintenance remains.
        raise RuntimeError("operator service masks inhibit runtime migration")
    verify_staged(approved)
    policy = approved["startup_policy"]
    value = {
        "schema": SCHEMA,
        "operation": operation,
        "plan": approved,
        "plan_digest": digest(approved),
        "manifest": manifest,
        "phase": "prepared",
        "policy": policy,
    }
    save(value)


def inhibit() -> None:
    value = state()
    if value is None:
        raise RuntimeError("runtime transaction has no approval")
    if CHECK is None:
        raise RuntimeError("runtime inhibition requires a canonical operation owner")
    CHECK()
    current_policy = policy_snapshot()
    transaction_policy = {"content": base64.b64encode(POLICY_BYTES).decode(), "mode": 0o755}
    if current_policy not in (value["policy"], transaction_policy):
        raise RuntimeError("package startup policy changed")
    # Persist intent first. A crash while publishing any subset remains resumable.
    value["phase"] = "inhibited"
    save(value)
    source = Path(__file__).read_bytes() if "_SOURCE" not in globals() else globals()["_SOURCE"]
    write(
        ROOT / "startup.py",
        source
        + b"\nif __name__ == '__main__':\n    raise SystemExit(0 if startup_allowed() else 1)\n",
    )
    for unit in UNITS:
        if unit.endswith(".timer"):
            content = (
                "[Unit]\nConditionPathExists=!/var/lib/nebius-vpngw/runtime-migration/inhibited\n"
            )
        else:
            content = "[Service]\nExecCondition=/usr/bin/python3 -B /var/lib/nebius-vpngw/runtime-migration/startup.py\n"
        path = Path("/etc/systemd/system") / (unit + ".d") / "20-runtime-admission.conf"
        if path.exists() and path.read_bytes() != content.encode():
            raise RuntimeError("runtime startup admission conflicts with existing policy")
        write(path, content.encode(), 0o644)
    write(ROOT / "inhibited", b"inhibited\n")
    write(POLICY, POLICY_BYTES, 0o755)
    run(["systemctl", "daemon-reload"])


def startup_allowed() -> bool:
    try:
        if not (ROOT / "inhibited").exists():
            return True
        value = state()
        token = json.loads(TOKEN.read_text())
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if value is None or token["operation"] != value["operation"] or token["boot"] != boot:
            return False
        if token.get("ha_lock") is not None:
            lock = json.loads(Path("/var/lib/nebius-vpngw/vm-ha/apply.lock").read_text())
            guard = json.loads(Path("/var/lib/nebius-vpngw/vm-ha/guard.json").read_text())
            return (
                lock == token["ha_lock"]
                and guard.get("guard_boot_id") == boot
                and guard.get("data_plane_mode") in {"blocked", "passive"}
                and Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "0"
                and Path("/etc/nebius-vpngw/vm-ha-enabled").exists()
                and value["phase"] == "activating"
            )
        fields = Path(f"/proc/{token['pid']}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[19] == token["start"] and value["phase"] == "activating"
    except (OSError, ValueError, KeyError, IndexError):
        return False


def verify_inhibited() -> None:
    if (
        not (ROOT / "inhibited").exists()
        or POLICY.read_bytes() != POLICY_BYTES
        or startup_allowed()
    ):
        raise RuntimeError("package starts are not inhibited")
    expected = [
        "/usr/bin/python3",
        "/usr/bin/python3 -B /var/lib/nebius-vpngw/runtime-migration/startup.py",
        "no",
    ]
    for unit in UNITS:
        if unit.endswith(".timer"):
            continue
        policy = command_policy(
            run(
                [
                    "systemctl",
                    "show",
                    "--all",
                    unit,
                    "--property=LoadState",
                    "--property=ExecCondition",
                ]
            )
        )
        if policy.get("LoadState") != "not-found" and expected not in policy.get(
            "ExecCondition", []
        ):
            raise RuntimeError("effective service startup inhibition is missing")


def install() -> None:
    value = state()
    if value is None or not (ROOT / "inhibited").exists():
        raise RuntimeError("runtime package replacement is not inhibited")
    approved = value["plan"]
    if COMMAND is None or CHECK is None:
        raise RuntimeError("package effects require the canonical operation owner")
    CHECK()
    verify_inhibited()
    directory = verify_staged(approved)
    value["phase"] = "packages"
    save(value)
    current = packages()
    remove = [
        name
        for name in approved["remove"]
        if current.get(name, [None, None])[1] not in {None, "not-installed", "config-files"}
    ]
    if remove:
        run(["dpkg", "--remove", *remove], timeout=120)
    if approved["archives"]:
        run(
            [
                "dpkg",
                "--unpack",
                "--force-confold",
                *(str(directory / name) for name in approved["archives"]),
            ],
            timeout=120,
        )
        run(["dpkg", "--configure", "--force-confold", *approved["install"]], timeout=120)
    verify_packages(approved)
    run(["systemctl", "daemon-reload"])
    verify_inhibited()
    preserve_unit_policy(approved)
    value["phase"] = "installed"
    save(value)


def verify_packages(approved: dict) -> None:
    current = packages()
    if any(current.get(k) != value for k, value in approved["desired"].items()) or any(
        current.get(k, [None, None])[1] not in {None, "config-files", "not-installed"}
        for k in approved["remove"]
    ):
        raise RuntimeError("runtime package postconditions are incomplete")


def activate() -> None:
    value = state()
    if value is None:
        raise RuntimeError("runtime activation has no approved operation")
    verify_packages(value["plan"])
    value["phase"] = "activating"
    save(value)
    fields = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()
    write(
        TOKEN,
        json.dumps(
            {
                "operation": value["operation"],
                "pid": os.getpid(),
                "start": fields[19],
                "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            }
        ).encode(),
    )


def activate_ha(expected: dict) -> None:
    """Hand startup admission to the existing exact locked HA activation."""
    value = state()
    if value is None or value["phase"] == "complete":
        return
    root = Path("/var/lib/nebius-vpngw/vm-ha")
    lock = json.loads((root / "apply.lock").read_text())
    guard = json.loads((root / "guard.json").read_text())
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if (
        any(
            lock.get(k) != expected[k]
            for k in ("cluster_id", "node_id", "generation_id", "operation_id")
        )
        or guard.get("guard_boot_id") != boot
        or guard.get("data_plane_mode") not in {"blocked", "passive"}
        or Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() != "0"
        or not Path("/etc/nebius-vpngw/vm-ha-enabled").exists()
    ):
        raise RuntimeError("HA runtime activation authority is unproven")
    verify_packages(value["plan"])
    value["phase"] = "activating"
    save(value)
    write(
        TOKEN, json.dumps({"operation": value["operation"], "boot": boot, "ha_lock": lock}).encode()
    )


def release() -> None:
    value = state()
    if value is not None and value["phase"] == "complete":
        return
    if value is None or not native()["ready"]:
        raise RuntimeError("runtime native verification has not completed")
    verify_packages(value["plan"])
    value["phase"] = "releasing"
    save(value)
    previous = value["policy"]
    transaction_policy = {"content": base64.b64encode(POLICY_BYTES).decode(), "mode": 0o755}
    if policy_snapshot() not in (transaction_policy, previous):
        raise RuntimeError("package startup policy changed during migration")
    if previous is None:
        unlink(POLICY)
    else:
        write(POLICY, base64.b64decode(previous["content"], validate=True), previous["mode"])
    unlink(ROOT / "inhibited")
    unlink(TOKEN)
    value["phase"] = "complete"
    save(value)


def streamed_bootstrap() -> str:
    source = base64.b64encode(Path(__file__).read_bytes()).decode()
    return (
        "import base64,sys,types;_m=types.ModuleType('_vpngw_runtime_migration');"
        "sys.modules[_m.__name__]=_m;"
        f"_m._SOURCE=base64.b64decode({source!r});"
        "exec(compile(_m._SOURCE,'<runtime-migration>','exec'),_m.__dict__);\n"
    )
