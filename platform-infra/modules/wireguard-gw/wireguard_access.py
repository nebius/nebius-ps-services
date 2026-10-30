#!/usr/bin/env python3
"""Root-only WireGuard host bootstrap and serialized login rotation.

Only public first-boot inputs are embedded in cloud-init. Network failures never
fall back to a generated host key. Exceptions are deliberately not printed.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path("/etc/nebius-wireguard/access.json")
STATE = Path("/var/lib/nebius-wireguard/access")
HOST = Path("/etc/ssh/cxcli_wireguard_host_ed25519_key")
KEY = re.compile(r"ssh-ed25519 [A-Za-z0-9+/]+={0,2}")


def atomic(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".wg-access-")
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Redirect rejected")


def fetch(url: str, headers: dict[str, str]) -> dict:
    # No proxy, redirect, shell process or response logging can expose the token.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        url, headers={**headers, "User-Agent": "nebius-cxcli-wireguard/1"}
    )
    with opener.open(request, timeout=20) as response:
        body = response.read(32769)
    if len(body) > 32768:
        raise ValueError("Oversized payload")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("Malformed payload")
    return value


def public(path: Path) -> str:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_nlink != 1
        or info.st_mode & 0o077
    ):
        raise ValueError("Unsafe host key")
    result = subprocess.run(
        ["ssh-keygen", "-y", "-P", "", "-f", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    key = " ".join(result.stdout.split()[:2])
    if not KEY.fullmatch(key):
        raise ValueError("Invalid host key")
    return key


def authorized(config: dict) -> Path:
    # Keep authorization root-owned so only an explicit sudo operation can change it.
    return Path("/etc/ssh/cxcli-wireguard-authorized-keys")


def bootstrap(config: dict) -> None:
    marker = STATE / "generation"
    if marker.exists():
        if marker.read_text() != config["generation"] or public(HOST) != config["host_public_key"]:
            raise ValueError("Persisted identity mismatch")
        if not authorized(config).is_file():
            raise ValueError("Missing persisted authorization")
        return
    if HOST.exists():
        # Recover interruption after writing the key; never fetch/replace a different identity.
        if public(HOST) != config["host_public_key"]:
            raise ValueError("Uncommitted host key mismatch")
    else:
        identity = fetch("http://metadata.nebius.internal/v1/iam/sa", {"Metadata": "true"})
        if identity.get("service_account_id") != config["service_account_id"]:
            raise ValueError("VM identity mismatch")
        token = identity["token"]["access_token"]
        query = urllib.parse.urlencode(
            {"secretId": config["host_secret_id"], "versionId": config["host_version_id"]}
        )
        payload = fetch(
            "https://api.nebius.cloud/mysterybox/v1/payloads?" + query,
            {"Authorization": "Bearer " + token},
        )
        if payload.get("versionId") != config["host_version_id"]:
            raise ValueError("Host version mismatch")
        entries = payload["data"]
        values = {}
        for entry in entries:
            if entry["key"] in values:
                raise ValueError("Duplicate payload key")
            values[entry["key"]] = (
                entry.get("stringValue")
                if "stringValue" in entry
                else base64.b64decode(entry["binaryValue"], validate=True).decode("ascii")
            )
        if (
            set(values) != {"ed25519_private", "ed25519_public"}
            or values["ed25519_public"] != config["host_public_key"]
        ):
            raise ValueError("Host payload mismatch")
        with tempfile.TemporaryDirectory(dir=STATE) as directory:
            candidate = Path(directory) / "host"
            atomic(candidate, values["ed25519_private"])
            if public(candidate) != config["host_public_key"]:
                raise ValueError("Host pair mismatch")
            atomic(HOST, values["ed25519_private"])
    # No SSH unit may start until the service succeeds. Authorization is the last step.
    atomic(HOST.with_suffix(".pub"), config["host_public_key"] + "\n", 0o644)
    atomic(authorized(config), config["login_public_key"] + "\n", 0o644)
    atomic(marker, config["generation"])


def rotate(config: dict, command: str, request: dict) -> None:
    path = STATE / "rotation.json"
    prior = json.loads(path.read_text()) if path.exists() else {}
    operation, candidate = request["operation"], request["public_key"]
    if not re.fullmatch(r"[a-f0-9-]{36}", operation) or not KEY.fullmatch(candidate):
        raise ValueError("Invalid rotation")
    if operation in prior.get("completed", []):
        raise ValueError("Superseded rotation cannot be replayed")
    if prior and prior["operation"] != operation and prior["phase"] != "complete":
        raise ValueError("Another rotation owns guest maintenance")
    if not prior or prior["operation"] != operation:
        if command != "stage":
            raise ValueError("Rotation was not staged")
        completed = [*prior.get("completed", []), *([prior["operation"]] if prior else [])]
        prior = {
            "operation": operation,
            "public_key": candidate,
            "phase": "staging",
            "completed": completed,
        }
        atomic(path, json.dumps(prior))
    if prior["public_key"] != candidate:
        raise ValueError("Rotation candidate changed")
    keys = authorized(config).read_text().splitlines()
    if command == "stage":
        if prior["phase"] in {"retired", "complete"}:
            if keys != [candidate]:
                raise ValueError("Retired authorization drifted")
            return
        if candidate not in keys:
            atomic(authorized(config), "\n".join([*keys, candidate]) + "\n", 0o644)
        prior["phase"] = "staged"
    elif command == "retire":
        if candidate not in keys:
            raise ValueError("Candidate login not staged")
        atomic(authorized(config), candidate + "\n", 0o644)
        prior["phase"] = "retired"
    elif command == "finish":
        if keys != [candidate] or prior["phase"] not in {"retired", "complete"}:
            raise ValueError("Old key retirement not proven")
        prior["phase"] = "complete"
    else:
        raise ValueError("Invalid rotation command")
    atomic(path, json.dumps(prior))


def main() -> None:
    if os.geteuid() != 0:
        raise PermissionError
    config = json.loads(CONFIG.read_text())
    if not KEY.fullmatch(config["host_public_key"]) or not KEY.fullmatch(
        config["login_public_key"]
    ):
        raise ValueError("Invalid public bootstrap")
    pwd.getpwnam(config["ssh_user"])
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / "maintenance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        command = sys.argv[1]
        if command == "bootstrap":
            bootstrap(config)
        elif command == "setup-complete":
            bootstrap(config)
            atomic(STATE / "setup-complete", config["generation"])
        elif command in {"verify", "inspect"}:
            if (STATE / "generation").read_text() != config["generation"] or public(HOST) != config[
                "host_public_key"
            ]:
                raise ValueError("Persisted identity mismatch")
            if command == "inspect":
                path = STATE / "rotation.json"
                rotation = json.loads(path.read_text()) if path.exists() else {}
                print(
                    json.dumps(
                        {
                            "generation": config["generation"],
                            "rotation": rotation.get("operation", ""),
                            "phase": rotation.get("phase", ""),
                        }
                    )
                )
                return
            if (STATE / "setup-complete").read_text() != config["generation"]:
                raise ValueError("Full WireGuard setup is incomplete")
            subprocess.run(
                ["systemctl", "is-active", "--quiet", "wg-quick@wg0.service"],
                check=True,
                capture_output=True,
                timeout=10,
            )
            if not Path("/usr/local/sbin/nebius-wireguard-client").is_file():
                raise ValueError("WG helper missing")
            print(json.dumps({"generation": config["generation"], "host_public_key": public(HOST)}))
        else:
            raw = sys.stdin.read(4097)
            if len(raw) > 4096:
                raise ValueError("Oversized rotation")
            rotate(config, command, json.loads(raw))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(
            "WireGuard access operation failed; administrator login remains gated until identity is valid",
            file=sys.stderr,
        )
        sys.exit(1)
