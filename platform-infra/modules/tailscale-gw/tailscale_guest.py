#!/usr/bin/env python3
"""Root-only, bounded Tailscale operations. Input is JSON on stdin, never shell text."""

from __future__ import annotations

import fcntl
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

STATE = Path("/var/lib/nebius-tailscale")
MEMBER = Path("/etc/nebius-tailscale/member.json")
HOST_PUBLIC = Path("/etc/ssh/ssh_host_ed25519_key.pub")
LIMIT = 1024 * 1024


def run(args: list[str], *, timeout: int = 40) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError(
            "Guest subprocess outcome unavailable; inspect enrollment receipt"
        ) from None
    if result.returncode or len(result.stdout) > LIMIT:
        # Vendor stderr can contain authentication URLs or credentials.
        raise RuntimeError(f"Guest operation failed: {Path(args[0]).name}")
    return result.stdout


def write(path: Path, value: dict | str) -> None:
    if isinstance(value, str):
        # The exact operation filename is recoverable even after a process kill;
        # never leave anonymous plaintext temporary keys behind.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        return
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            if isinstance(value, str):
                stream.write(value)
            else:
                json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_private(path: Path, *, raw: bool = False):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or info.st_size > LIMIT
        ):
            raise RuntimeError("Unsafe guest enrollment storage")
        with os.fdopen(fd, "r", closefd=False) as stream:
            return stream.read() if raw else json.load(stream)
    finally:
        os.close(fd)


def enrollment_contract(value: dict) -> dict:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "id", "binding", "tailnet", "tags", "key_id"}
        or value["schema"] != 1
        or not re.fullmatch(r"[a-f0-9]{32}", str(value.get("id", "")))
        or not isinstance(value["binding"], dict)
    ):
        raise RuntimeError("Unsupported enrollment contract")
    member = read_private(MEMBER)
    host = " ".join(HOST_PUBLIC.read_text().split()[:2])
    if (
        not member
        or member.get("project") != value["binding"].get("project_id")
        or host != value["binding"].get("host_public_key")
    ):
        raise RuntimeError("Enrollment contract differs from guest bootstrap identity")
    return value


def enrollment_receipt(value: dict) -> tuple[Path, dict | None]:
    value = enrollment_contract(value)
    path = STATE / ("enrollment-" + value["id"] + ".json")
    saved = read_private(path)
    if saved and saved.get("contract") != value:
        raise RuntimeError("Enrollment operation was rebound")
    return path, saved


def enrollment_status(request: dict, *, fence: bool = False, unused: bool = False) -> dict:
    value = request["enrollment"]
    path, saved = enrollment_receipt(value)
    observed = observation()
    if unused and (observed.get("node_id") or (saved and saved["phase"] != "prepared")):
        raise RuntimeError("Enrollment already started; unused-key fencing refused")
    if fence or unused:
        saved = saved or {"contract": value, "phase": "prepared", "fenced": False, "node_id": ""}
        saved["fenced"] = True
        write(path, saved)
    return {"receipt": saved, "guest": observed}


def enroll(request: dict, current: dict) -> dict:
    value = request.get("enrollment")
    path, saved = enrollment_receipt(value)
    active_path = STATE / "active-enrollment.json"
    active = read_private(active_path)
    if active and active["id"] != value["id"]:
        previous = read_private(STATE / ("enrollment-" + active["id"] + ".json"))
        if not previous or not previous["fenced"] or previous["phase"] != "prepared":
            raise RuntimeError("A prior enrollment operation has not been safely fenced")
    if saved and saved["fenced"]:
        raise RuntimeError("Enrollment operation is fenced")
    if not saved:
        if current["node_id"]:
            raise RuntimeError("Node lacks an authenticated enrollment receipt")
        saved = {"contract": value, "phase": "prepared", "fenced": False, "node_id": ""}
        write(path, saved)
    write(active_path, {"id": value["id"]})
    if current["node_id"]:
        if saved["phase"] == "enrolled" and saved.get("node_id") != current["node_id"]:
            raise RuntimeError("Enrolled receipt node identity changed")
        if saved["phase"] not in {"started", "enrolled"}:
            raise RuntimeError("Node appeared before enrollment started")
    else:
        if saved["phase"] == "enrolled":
            raise RuntimeError("Enrolled identity disappeared; reauthentication forbidden")
        key_path = STATE / ("enrollment-" + value["id"] + ".key")
        stored = read_private(key_path, raw=True)
        key = request.get("auth_key", "")
        if stored:
            if key and key != stored:
                raise RuntimeError("Enrollment key changed within the same operation")
            key = stored
        elif not isinstance(key, str) or not key.startswith("tskey-auth-") or len(key) > 4096:
            raise RuntimeError("Original enrollment key unavailable; recover the operation")
        else:
            write(key_path, key)
        # Store the operation before starting tailscaled authentication. The private
        # key survives a helper interruption and is delivered through a root-only file.
        saved["phase"] = "started"
        write(path, saved)
        run(
            [
                "tailscale",
                "up",
                "--timeout=30s",
                f"--auth-key=file:{key_path}",
                "--advertise-routes=",
                "--accept-routes=false",
                "--accept-dns=false",
                "--ssh=false",
                "--snat-subnet-routes=true",
                "--netfilter-mode=on",
                "--stateful-filtering=true",
                "--advertise-tags=" + ",".join(value["tags"]),
                "--hostname=" + request["desired"]["hostname"],
            ]
        )
        current = observation()
    if current["node_id"]:
        assert_identity(current, value)
        saved["phase"] = "enrolled"
        saved["node_id"] = current["node_id"]
        write(path, saved)
        key_path = STATE / ("enrollment-" + value["id"] + ".key")
        if read_private(key_path, raw=True) is not None:
            key_path.unlink()
    return current


def forget_key(request: dict) -> dict:
    path, saved = enrollment_receipt(request["enrollment"])
    if not saved or not saved["fenced"]:
        raise RuntimeError("Fence enrollment before removing revoked key material")
    key_path = STATE / ("enrollment-" + saved["contract"]["id"] + ".key")
    if read_private(key_path, raw=True) is not None:
        key_path.unlink()
    return {"status": "KEY_REMOVED"}


def observation() -> dict:
    current = json.loads(run(["tailscale", "status", "--json", "--peers=false"]))
    prefs = json.loads(run(["tailscale", "debug", "prefs"]))
    own = current.get("Self") or {}
    tailnet = current.get("CurrentTailnet") or {}
    return {
        "schema": 1,
        "backend": current.get("BackendState", "Unknown"),
        "node_id": own.get("ID", ""),
        "tailnet": tailnet.get("Name", ""),
        "dns_suffix": tailnet.get("MagicDNSSuffix", ""),
        "tags": sorted(own.get("Tags") or []),
        "online": current.get("BackendState") == "Running" and own.get("Online", False),
        "routes": sorted(prefs.get("AdvertiseRoutes") or []),
        "accept_routes": prefs.get("RouteAll", False),
        "accept_dns": prefs.get("CorpDNS", False),
        "snat": not prefs.get("NoSNAT", False),
        "tailscale_ssh": prefs.get("RunSSH", False),
        "stateful_filtering": prefs.get("NoStatefulFiltering") in {False, "false"},
        "netfilter": prefs.get("NetfilterMode") == 2,
        "approved_routes": sorted(
            p for p in (own.get("AllowedIPs") or []) if p in (prefs.get("AdvertiseRoutes") or [])
        ),
        "expired": own.get("Expired", False),
        "version": current.get("Version", "").split("-")[0],
        "key_expiry": own.get("KeyExpiry"),
        "forwarding": Path("/proc/sys/net/ipv4/ip_forward").read_text().strip() == "1",
    }


def assert_identity(observed: dict, desired: dict) -> None:
    if observed["tailnet"] != desired["tailnet"]:
        raise RuntimeError(
            "Wrong tailnet: no routes were advertised; administrator action required"
        )
    if not observed["node_id"] or sorted(observed["tags"]) != sorted(desired["tags"]):
        raise RuntimeError("Tailscale node identity/tags differ from the enrollment contract")


def reconcile(request: dict) -> dict:
    desired = request["desired"]
    if set(desired) != {"tailnet", "tags", "routes", "version", "hostname"}:
        raise ValueError("Unsupported desired settings")
    if not re.fullmatch(r"[A-Za-z0-9.@_-]+", desired["tailnet"]):
        raise ValueError("Invalid tailnet identity")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", desired["hostname"]):
        raise ValueError("Invalid hostname")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", desired["version"]):
        raise ValueError("Expected an exact package version")
    if not desired["tags"] or any(
        not re.fullmatch(r"tag:[a-zA-Z][a-zA-Z0-9-]*", tag) for tag in desired["tags"]
    ):
        raise ValueError("Tagged server identities are required")
    for route in desired["routes"]:
        network = ipaddress.IPv4Network(route, strict=True)
        if (
            network.prefixlen == 0
            or not network.is_private
            or network.is_loopback
            or network.is_link_local
        ):
            raise ValueError("Only private destination prefixes are supported")
    current = observation()
    owner_path = STATE / "owner.json"
    owner = read_private(owner_path)
    if owner and (owner["tailnet"] != desired["tailnet"] or owner["node_id"] != current["node_id"]):
        raise RuntimeError("Persisted identity changed; automatic re-enrollment is forbidden")
    if (STATE / "retired.json").exists():
        raise RuntimeError("Guest is fenced for retirement")
    if request.get("enrollment"):
        if request["enrollment"].get("tailnet") != desired["tailnet"] or request["enrollment"].get(
            "tags"
        ) != sorted(desired["tags"]):
            raise RuntimeError("Desired enrollment identity changed")
        current = enroll(request, current)
        if not current["node_id"]:
            return {
                **current,
                "status": "PENDING",
                "reason": "Enrollment started; resume same operation",
            }
    elif not owner:
        assert_identity(current, desired)
        raise RuntimeError("Node lacks a persisted owner; automatic adoption forbidden")
    assert_identity(current, desired)
    if not owner:
        write(owner_path, {"tailnet": current["tailnet"], "node_id": current["node_id"]})
    if current["backend"] != "Running":
        return {
            **current,
            "status": "PENDING",
            "reason": "Device approval, signature or reauthentication required",
        }
    if current["version"] != desired["version"]:
        run(["apt-get", "update"], timeout=180)
        run(
            [
                "apt-get",
                "install",
                "-y",
                "--allow-change-held-packages",
                "tailscale=" + desired["version"],
            ],
            timeout=300,
        )
        run(["apt-mark", "hold", "tailscale"])
    run(
        [
            "tailscale",
            "set",
            "--advertise-routes=" + ",".join(desired["routes"]),
            "--accept-routes=false",
            "--accept-dns=false",
            "--ssh=false",
            "--snat-subnet-routes=true",
            "--netfilter-mode=on",
            "--stateful-filtering=true",
        ]
    )
    result = observation()
    assert_identity(result, desired)
    if result["routes"] != sorted(desired["routes"]) or not result["forwarding"]:
        raise RuntimeError("Guest routing did not converge")
    return {**result, "status": "READY" if result["online"] else "PENDING"}


def main(request: dict) -> dict:
    if os.geteuid() != 0:
        raise RuntimeError("Root privileges required")
    operation = request.get("operation")
    if operation == "status":
        return observation()
    if operation == "trial-status":
        state = run(
            ["systemctl", "show", "tailscaled", "--property=ActiveState", "--value"]
        ).strip()
        return {"active": state == "active", "state": state}
    if operation not in {
        "reconcile",
        "fault",
        "restore",
        "withdraw",
        "enrollment-status",
        "enrollment-fence",
        "enrollment-fence-unused",
        "enrollment-forget-key",
        "retire",
    }:
        raise ValueError("Unknown operation")
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = STATE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError("Unsafe guest state directory")
    fd = os.open(STATE / "operation.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if operation in {"enrollment-status", "enrollment-fence", "enrollment-fence-unused"}:
            return enrollment_status(
                request,
                fence=operation == "enrollment-fence",
                unused=operation == "enrollment-fence-unused",
            )
        if operation == "enrollment-forget-key":
            return forget_key(request)
        if operation == "retire":
            write(STATE / "retired.json", {"schema": 1})
            return observation()
        if operation == "reconcile":
            return reconcile(request)
        if operation == "withdraw":
            run(["tailscale", "set", "--advertise-routes="])
            return observation()
        if operation == "restore":
            run(["systemctl", "start", "tailscaled"])
            return {"status": "RESTORED"}
        token = request.get("token", "")
        duration = request.get("duration", 90)
        if (
            not re.fullmatch(r"[a-f0-9]{32}", token)
            or type(duration) is not int
            or not 15 <= duration <= 120
        ):
            raise ValueError("Invalid bounded failover trial")
        run(["systemctl", "is-active", "--quiet", "tailscaled"])
        # Guest-owned timer survives a lost CLI, SSH connection or operator cancellation.
        run(
            [
                "systemd-run",
                "--unit=cxcli-ts-restore-" + token,
                f"--on-active={duration}s",
                "/bin/systemctl",
                "start",
                "tailscaled",
            ]
        )
        run(["systemctl", "stop", "tailscaled"])
        return {"status": "FAULT_INJECTED", "restoration_seconds": duration}


if __name__ == "__main__":
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("Request too large")
        print(json.dumps(main(json.loads(raw))))
    except Exception as error:
        # Never serialize exceptions raised by vendor tools or JSON payloads.
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "reason": str(error)
                    if type(error) in {RuntimeError, ValueError}
                    else type(error).__name__,
                }
            )
        )
        sys.exit(1)
