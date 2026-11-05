"""Standalone, public-key-only guest enrollment with exact projection checks.

The operator transports this source over an already authenticated pinned SSH
session. No private key or passphrase is accepted by this protocol.
"""

from __future__ import annotations

import base64
import ctypes
import fcntl
import hashlib
import json
import os
import pwd
import re
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path("/var/lib/nebius-vpngw/access")
REPLACEMENT_GUARD = Path("/etc/nebius-vpngw/replacement-guard.json")
REPLACEMENT_RELEASE = Path("/etc/nebius-vpngw/replacement-released.json")
REPLACEMENT_SYSCTL = Path("/etc/sysctl.d/99-zzzz-vpngw-replacement.conf")
BEGIN = "# BEGIN NEBIUS VPNGW OPERATORS v1\n"
END = "# END NEBIUS VPNGW OPERATORS v1\n"
LIMIT = 1024 * 1024


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _safe_path(path: Path, *, owners: set[int] | None = None) -> None:
    if not path.is_absolute():
        raise ValueError("Access projection requires absolute paths")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError("Access projection must not follow symbolic links")
        try:
            metadata = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(metadata.st_mode):
            sticky_root = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
            if (metadata.st_mode & 0o022 and not sticky_root) or (
                owners is not None and metadata.st_uid not in owners
            ):
                raise ValueError("Unsafe authorization directory ownership or write permissions")


def read_file(path: Path) -> dict[str, Any] | None:
    _safe_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_mode & 0o022:
            raise ValueError("Unsafe authorization file type or permissions")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("Authorization file exceeds the bounded projection size")

        def signature(info):
            return (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
                info.st_gid,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
            )

        if signature(before) != signature(os.fstat(fd)) or signature(before) != signature(
            path.lstat()
        ):
            raise ValueError("Authorization file changed during inspection")
        return {
            "content": data.decode("utf-8"),
            "uid": before.st_uid,
            "gid": before.st_gid,
            "mode": stat.S_IMODE(before.st_mode),
        }
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _exchange(first: Path, second: Path) -> None:
    """Atomically exchange two existing names; never remove the login file."""
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = libc.renamex_np
        function.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = function(os.fsencode(first), os.fsencode(second), 2)
    else:
        function = libc.renameat2
        function.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        result = function(-100, os.fsencode(first), -100, os.fsencode(second), 2)
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def publish(path: Path, expected: dict[str, Any] | None, desired: dict[str, Any]) -> None:
    """Crash-safe publication under the access lock with predecessor comparison.

    Native exchange keeps an existing authentication file continuously present.
    A competing non-cooperative writer is detected through the displaced file;
    its data is restored when possible and otherwise retained for inspection.
    """
    _safe_path(path)
    recovery = path.parent / ("." + path.name + ".vpngw-recovery")
    candidate = recovery / "candidate"
    if recovery.exists():
        _safe_path(recovery)
        metadata = recovery.stat()
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            raise ValueError("Unsafe authorization recovery directory")
        entries = set(recovery.iterdir())
        if not entries and read_file(path) in (expected, desired):
            recovery.rmdir()
            if read_file(path) == desired:
                return
            return publish(path, expected, desired)
        if entries != {candidate}:
            raise ValueError("Unsafe authorization recovery directory")
        active, retained = read_file(path), read_file(candidate)
        if active == desired and retained in (expected, desired):
            _fsync_directory(path.parent)
            candidate.unlink()
            recovery.rmdir()
            return
        if active != expected or retained != desired:
            raise ValueError("Authorization publication needs inspection after a competing change")
    else:
        if read_file(path) != expected:
            raise ValueError("Authorization projection changed before publication")
        fd, staging_name = tempfile.mkstemp(prefix=".vpngw-access-", dir=path.parent)
        staging = Path(staging_name)
        try:
            os.fchown(fd, desired["uid"], desired["gid"])
            os.fchmod(fd, desired["mode"])
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(desired["content"].encode())
                stream.flush()
                os.fsync(fd)
            recovery.mkdir(mode=0o700)
            os.link(staging, candidate)
            _fsync_directory(recovery)
            _fsync_directory(path.parent)
        finally:
            os.close(fd)
            staging.unlink(missing_ok=True)
    if expected is None:
        os.link(candidate, path)
    else:
        _exchange(candidate, path)
        _fsync_directory(recovery)
        _fsync_directory(path.parent)
        if read_file(candidate) != expected:
            if read_file(path) == desired:
                _exchange(candidate, path)
                _fsync_directory(path.parent)
            raise ValueError(
                "Authorization projection changed during atomic publication; recovery files retained"
            )
    _fsync_directory(path.parent)
    candidate.unlink()
    recovery.rmdir()
    _fsync_directory(path.parent)


def finish_publication(
    path: Path, expected: dict[str, Any] | None, desired: dict[str, Any]
) -> None:
    # Also recover a process death after atomic publication but before cleanup.
    if read_file(path) != desired or (path.parent / ("." + path.name + ".vpngw-recovery")).exists():
        publish(path, expected, desired)


def _public_key(value: str) -> tuple[str, str]:
    fields = value.strip().split()
    if len(fields) != 2 or fields[0] != "ssh-ed25519" or "\n" in value.strip():
        raise ValueError("Enrollment requires one plain Ed25519 public key")
    try:
        blob = base64.b64decode(fields[1], validate=True)
        kind_length = struct.unpack(">I", blob[:4])[0]
        offset = 4 + kind_length
        key_length = struct.unpack(">I", blob[offset : offset + 4])[0]
        if blob[4:offset] != b"ssh-ed25519" or key_length != 32 or len(blob) != offset + 4 + 32:
            raise ValueError
    except (ValueError, struct.error) as error:
        raise ValueError("Enrollment public key is malformed") from error
    return " ".join(fields), "SHA256:" + base64.b64encode(
        hashlib.sha256(blob).digest()
    ).decode().rstrip("=")


def _block(operators: dict[str, dict[str, str]]) -> str:
    if not operators:
        return ""
    lines = []
    fingerprints = set()
    for name, entry in sorted(operators.items()):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name):
            raise ValueError("Invalid operator name")
        public, fingerprint = _public_key(entry["public_key"])
        if (
            entry != {"public_key": public, "fingerprint": fingerprint}
            or fingerprint in fingerprints
        ):
            raise ValueError("Operator roster has inconsistent or duplicate identities")
        fingerprints.add(fingerprint)
        lines.append(f"{public} vpngw-operator:{name}\n")
    return BEGIN + "".join(lines) + END


def _replace_block(content: str, operators: dict[str, dict[str, str]]) -> str:
    if content.count(BEGIN) != content.count(END) or content.count(BEGIN) > 1:
        raise ValueError("Managed authorization block is malformed")
    if BEGIN in content:
        start = content.index(BEGIN)
        end = content.index(END) + len(END)
        if start >= end:
            raise ValueError("Managed authorization block is malformed")
        return content[:start] + _block(operators) + content[end:]
    separator = "" if not content or content.endswith("\n") else "\n"
    return content + separator + _block(operators)


def _sshd_configuration_arguments(proc: Path = Path("/proc")) -> list[str]:
    """Recover the serving listener's config flags through this SSH process tree.

    A nonstandard listener that cannot be identified is unsupported, rather than
    silently inspecting a different daemon configuration.
    """
    pid = os.getppid()
    for _ in range(24):
        if pid <= 1:
            break
        directory = proc / str(pid)
        raw = (directory / "cmdline").read_bytes()
        if len(raw) > 65536:
            raise ValueError("Cannot resolve the serving SSH daemon")
        parts = [part.decode() for part in raw.split(b"\0") if part]
        if len(parts) == 1 and parts[0].startswith("sshd: ") and " [listener]" in parts[0]:
            parts = shlex.split(parts[0][6:].split(" [listener]", 1)[0])
        if parts and parts[0] == "/usr/sbin/sshd":
            arguments: list[str] = []
            index = 1
            while index < len(parts):
                value = parts[index]
                if value in {"-D", "-e", "-4", "-6", "-i", "-q"}:
                    index += 1
                    continue
                if value in {"-f", "-o", "-p", "-h", "-E", "-g", "-u"}:
                    if index + 1 == len(parts):
                        raise ValueError("Cannot resolve the serving SSH daemon options")
                    if value in {"-f", "-o"}:
                        arguments.extend((value, parts[index + 1]))
                    index += 2
                    continue
                if value.startswith(("-f", "-o")) and len(value) > 2:
                    arguments.append(value)
                    index += 1
                    continue
                raise ValueError("Unsupported serving SSH daemon options")
            return arguments
        status = (directory / "status").read_text()
        parents = [line.split()[1] for line in status.splitlines() if line.startswith("PPid:")]
        if len(parents) != 1 or not parents[0].isdigit():
            break
        pid = int(parents[0])
    raise ValueError("Cannot resolve the serving SSH daemon configuration")


def resolve_account(username: str, connection: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,63}", username):
        raise ValueError("Invalid management account")
    account = pwd.getpwnam(username)
    fields = connection.split()
    if len(fields) != 4:
        raise ValueError("Effective sshd policy requires the authenticated connection context")
    import ipaddress

    source, destination = str(ipaddress.ip_address(fields[0])), str(ipaddress.ip_address(fields[2]))
    ports = [int(fields[1]), int(fields[3])]
    if any(port < 1 or port > 65535 for port in ports):
        raise ValueError("Invalid authenticated connection ports")
    result = subprocess.run(
        [
            "/usr/sbin/sshd",
            *_sshd_configuration_arguments(),
            "-T",
            "-C",
            f"user={username},host={source},addr={source},laddr={destination},lport={ports[1]}",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode:
        raise ValueError("Cannot resolve effective sshd authorization policy")
    settings = dict(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
    if (
        settings.get("authorizedkeyscommand", "none") != "none"
        or settings.get("pubkeyauthentication") != "yes"
    ):
        raise ValueError("External SSH key authorization is unsupported for automated enrollment")
    if settings.get("usedns") != "no":
        raise ValueError(
            "Unsupported DNS-based SSH policy; automated enrollment requires UseDNS no"
        )
    files = shlex.split(settings.get("authorizedkeysfile", ""))
    if not files or files == ["none"]:
        raise ValueError("Effective sshd policy provides no authorized_keys file")
    paths = []
    for value in files:
        value = (
            value.replace("%%", "\0")
            .replace("%h", account.pw_dir)
            .replace("%u", username)
            .replace("%U", str(account.pw_uid))
            .replace("\0", "%")
        )
        if "%" in value:
            raise ValueError("Unsupported token in effective authorized_keys path")
        path = Path(value)
        if not path.is_absolute():
            path = Path(account.pw_dir) / path
        _safe_path(path)
        if not path.is_relative_to(Path(account.pw_dir)):
            raise ValueError(
                "Automated enrollment requires authorized_keys files within the management home"
            )
        paths.append(str(path))
    if len(set(paths)) != len(paths):
        raise ValueError("Duplicate effective authorization paths")
    return {
        "username": username,
        "home": account.pw_dir,
        "uid": account.pw_uid,
        "gid": account.pw_gid,
        "shell": account.pw_shell,
        "paths": paths,
    }


def inspect_projection(account: dict[str, Any], compute_id: str) -> dict[str, Any]:
    for path in account["paths"]:
        _safe_path(Path(path), owners={0, account["uid"]})
    files = {path: read_file(Path(path)) for path in account["paths"]}
    roster_file = read_file(ROOT / "operators.json")
    roster = {} if roster_file is None else json.loads(roster_file["content"])
    if roster and (
        set(roster) != {"schema", "username", "operators"}
        or roster["schema"] != 1
        or roster["username"] != account["username"]
    ):
        raise ValueError("Operator roster is bound to a different account or schema")
    operators = roster.get("operators", {})
    target = account["paths"][0]
    content = (files[target] or {}).get("content", "")
    expected = _block(operators)
    if expected:
        if expected not in content or content.count(BEGIN) != 1 or content.count(END) != 1:
            raise ValueError("Managed authorized_keys block and operator roster differ")
    elif BEGIN in content or END in content:
        raise ValueError("Managed authorization block has no public roster")
    for path, file in files.items():
        if file and file["uid"] not in {0, account["uid"]}:
            raise ValueError("Authorization projection has an unexpected file owner")
        if path != target and file and (BEGIN in file["content"] or END in file["content"]):
            raise ValueError("Managed authorization block exists in an unexpected file")
    projection = {
        "schema": 1,
        "account": account,
        "compute_id": compute_id,
        "files": files,
        "operators": operators,
    }
    return {"projection": projection, "digest": digest(projection)}


def _state_file(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "uid": os.getuid(),
        "gid": os.getgid(),
        "mode": 0o600,
        "content": json.dumps(content, sort_keys=True, separators=(",", ":")) + "\n",
    }


def enroll(
    account: dict[str, Any], compute_id: str, expected_digest: str, name: str, public_key: str
) -> dict[str, Any]:
    """Caller holds the access lock; retry completes the exact persisted intent."""
    public, fingerprint = _public_key(public_key)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}", name):
        raise ValueError("Invalid operator name")
    intent = digest(
        {"account": account, "compute_id": compute_id, "name": name, "public_key": public}
    )
    journal_path = ROOT / "enrollment.json"
    journal_file = read_file(journal_path)
    journal = None if journal_file is None else json.loads(journal_file["content"])
    if journal is not None and journal["intent"] != intent:
        raise ValueError("A different operator enrollment needs recovery first")
    if journal is None:
        observed = inspect_projection(account, compute_id)
        if observed["digest"] != expected_digest:
            raise ValueError("Authorization projection changed after preflight")
        projection = observed["projection"]
        operators = dict(projection["operators"])
        entry = {"public_key": public, "fingerprint": fingerprint}
        if name in operators and operators[name] != entry:
            raise ValueError("Operator name already belongs to a different key")
        # Preserving a restricted customer entry is insufficient if the same
        # identity is also granted unrestricted access through our managed file.
        key_tokens = r"[ \t]+".join(re.escape(token) for token in public.split())
        key_pattern = re.compile(r"(?:^|\s)" + key_tokens + r"(?:\s|$)")
        for file in projection["files"].values():
            if file is None:
                continue
            unmanaged = (
                file["content"].replace(_block(projection["operators"]), "")
                if projection["operators"]
                else file["content"]
            )
            if any(
                key_pattern.search(line)
                for line in unmanaged.splitlines()
                if not line.lstrip().startswith("#")
            ):
                raise ValueError(
                    "Operator key already exists in customer authorization; refusing to bypass its restrictions"
                )
        operators[name] = entry
        target = Path(account["paths"][0])
        before = projection["files"][str(target)]
        desired = dict(
            before or {"uid": account["uid"], "gid": account["gid"], "mode": 0o600, "content": ""}
        )
        desired["content"] = _replace_block(desired["content"], operators)
        desired_roster = _state_file(
            {"schema": 1, "username": account["username"], "operators": operators}
        )
        journal = {
            "intent": intent,
            "account": account,
            "compute_id": compute_id,
            "name": name,
            "fingerprint": fingerprint,
            "target": str(target),
            "before": before,
            "after": desired,
            "roster_before": read_file(ROOT / "operators.json"),
            "roster_after": desired_roster,
            "other_files": {
                path: file for path, file in projection["files"].items() if path != str(target)
            },
        }
        publish(journal_path, None, _state_file(journal))
    finish_publication(journal_path, None, _state_file(journal))
    target = Path(journal["target"])
    if str(target) != account["paths"][0] or any(
        read_file(Path(path)) != expected for path, expected in journal["other_files"].items()
    ):
        raise ValueError("Customer authorization changed during enrollment")
    if not target.parent.exists():
        if target.parent.parent != Path(account["home"]):
            raise ValueError("Missing custom authorization directory requires operator preparation")
        target.parent.mkdir(mode=0o700)
        os.chown(target.parent, account["uid"], account["gid"])
    finish_publication(target, journal["before"], journal["after"])
    roster_path = ROOT / "operators.json"
    finish_publication(roster_path, journal["roster_before"], journal["roster_after"])
    result = inspect_projection(account, compute_id)
    if read_file(journal_path) != _state_file(journal):
        raise ValueError("Enrollment journal changed before completion")
    journal_path.unlink()
    _fsync_directory(ROOT)
    return result


def inspect_pending(account: dict[str, Any], compute_id: str) -> dict[str, Any]:
    record = read_file(ROOT / "enrollment.json")
    if record is None:
        return inspect_projection(account, compute_id)
    journal = json.loads(record["content"])
    if journal["account"] != account or journal["compute_id"] != compute_id:
        raise ValueError("Enrollment journal is bound to another account or Compute")
    target = journal["target"]
    files = {path: read_file(Path(path)) for path in account["paths"]}
    if files[target] not in (journal["before"], journal["after"]):
        raise ValueError("Authorization changed after interrupted enrollment")
    if any(files[path] != expected for path, expected in journal["other_files"].items()):
        raise ValueError("Customer authorization changed after interrupted enrollment")
    roster = read_file(ROOT / "operators.json")
    if roster not in (journal["roster_before"], journal["roster_after"]):
        raise ValueError("Operator roster changed after interrupted enrollment")
    operators = json.loads(journal["roster_after"]["content"])["operators"]
    _block(operators)
    projection = {
        "schema": 1,
        "account": account,
        "compute_id": compute_id,
        "files": files,
        "operators": operators,
    }
    return {
        "projection": projection,
        "digest": digest(projection),
        "pending": {"name": journal["name"], "fingerprint": journal["fingerprint"]},
    }


def replacement_access(
    account: dict[str, Any], compute_id: str, request: dict[str, Any]
) -> dict[str, Any]:
    operation = request.get("operation")
    if not isinstance(operation, str) or not re.fullmatch(r"[a-f0-9]{32,64}", operation):
        raise ValueError("Invalid replacement operation")
    path = ROOT / "replacement.json"
    marker = read_file(path)
    wanted = _state_file({"operation": operation})
    if marker is not None and marker != wanted:
        raise ValueError("A different replacement owns operator authorization")
    if read_file(ROOT / "enrollment.json") is not None:
        raise ValueError("Enrollment must complete before replacement")
    result = inspect_projection(account, compute_id)
    if request["action"] == "reserve-replacement":
        if result["digest"] != request.get("expected_digest"):
            raise ValueError("Authorization changed before replacement reservation")
        finish_publication(path, None, wanted)
    elif marker is None and not (
        request["action"] in {"inspect-replacement", "release-replacement"}
        and read_file(REPLACEMENT_RELEASE) == wanted
    ):
        raise ValueError("Replacement has no guest authorization reservation")
    elif request["action"] == "activate-replacement":
        if result["digest"] != request.get("expected_digest"):
            raise ValueError("Authorization changed before replacement activation")
        guard = read_file(REPLACEMENT_GUARD)
        released = read_file(REPLACEMENT_RELEASE)
        if guard is None and released != wanted:
            raise ValueError("Replacement boot guard is missing without its release receipt")
        if guard is not None and (
            guard["uid"] != 0
            or guard["mode"] != 0o600
            or json.loads(guard["content"]) != {"operation": operation}
        ):
            raise ValueError("Replacement boot guard identity changed")
        if released not in (None, wanted):
            raise ValueError("Replacement boot release belongs to another operation")
        sysctl = read_file(REPLACEMENT_SYSCTL)
        if sysctl is not None and (
            sysctl["uid"] != 0
            or sysctl["content"] != "net.ipv4.ip_forward=0\nnet.ipv6.conf.all.forwarding=0\n"
        ):
            raise ValueError("Replacement forwarding guard changed")
        finish_publication(REPLACEMENT_RELEASE, None, wanted)
        if sysctl is not None:
            REPLACEMENT_SYSCTL.unlink()
            _fsync_directory(REPLACEMENT_SYSCTL.parent)
        if guard is not None:
            REPLACEMENT_GUARD.unlink()
            _fsync_directory(REPLACEMENT_GUARD.parent)
    elif request["action"] == "release-replacement":
        if marker is not None:
            path.unlink()
            _fsync_directory(ROOT)
    result["reservation"] = operation
    return result


def main() -> None:
    request = json.loads(sys.stdin.buffer.read(LIMIT + 1))
    if not isinstance(request, dict) or request.get("action") not in {
        "inspect",
        "enroll",
        "reserve-replacement",
        "inspect-replacement",
        "release-replacement",
        "activate-replacement",
    }:
        raise ValueError("Invalid access request")
    account = resolve_account(request["username"], request["connection"])
    compute_id = request["compute_id"]
    if not isinstance(compute_id, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,128}", compute_id):
        raise ValueError("Invalid Compute binding")
    _safe_path(ROOT)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = ROOT.stat()
    if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
        raise ValueError("Unsafe guest access state directory")
    fd = os.open(ROOT / "access.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        lock_metadata = os.fstat(fd)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != os.getuid()
            or lock_metadata.st_mode & 0o077
        ):
            raise ValueError("Unsafe guest access lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if request["action"].endswith("-replacement"):
            result = replacement_access(account, compute_id, request)
        elif (ROOT / "replacement.json").exists():
            raise ValueError("VM replacement is in progress; operator enrollment is blocked")
        elif request["action"] == "enroll":
            result = enroll(
                account,
                compute_id,
                request["expected_digest"],
                request["name"],
                request["public_key"],
            )
        else:
            result = inspect_pending(account, compute_id)
        print(json.dumps(result, sort_keys=True))
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Native errors may include file contents; expose only our fixed diagnoses.
        if type(error) is ValueError and str(error).startswith(
            (
                "Access",
                "Authorization",
                "Unsafe",
                "Enrollment",
                "Effective",
                "External",
                "Automated",
                "Operator",
                "Managed",
                "Customer",
                "VM replacement",
                "A different",
                "Invalid",
                "Cannot",
                "Missing",
                "Unsupported",
                "Duplicate",
            )
        ):
            print(str(error), file=sys.stderr)
        else:
            print(
                "Operator access operation failed; inspect the protected guest journal",
                file=sys.stderr,
            )
        raise SystemExit(1) from None
