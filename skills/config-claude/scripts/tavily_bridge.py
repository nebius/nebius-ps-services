#!/usr/bin/env python3
"""Prepare/check an opt-in Tavily bridge. Never writes native client configuration.

Canonical owner: config-codex. config-claude ships a byte-identical mirror.
Check does not execute dependencies, contact a server or read OAuth credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


VERSION = "0.15.2"
ENDPOINT = "https://mcp.tavily.com/mcp/"
PORTS = {"codex": 37411, "claude": 37412}
ASSETS = Path(__file__).resolve().parents[1] / "assets/tavily-bridge"
RECEIPT = "installation.json"


class BridgeError(ValueError):
    """A bounded, public-safe diagnostic, never a raw subprocess error."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BridgeError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_json(data: bytes) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON member")
            result[key] = value
        return result

    try:
        value = json.loads(data, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError) as error:
        raise BridgeError("invalid JSON document") from error
    require(isinstance(value, dict), "JSON object required")
    return value


def ancestors(path: Path) -> None:
    require(path.is_absolute() and ".." not in path.parts, "absolute canonical path required")
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        require(stat.S_ISDIR(info.st_mode), "unsafe directory or symlink")
        require(info.st_uid in {0, os.getuid()}, "foreign directory owner")
        # Permit root-owned sticky temporary roots; never a writable private subtree.
        sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
        require(not info.st_mode & 0o022 or sticky_root, "writable directory ancestry")


def read_regular(path: Path, *, private: bool = False) -> bytes:
    ancestors(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), "unsafe file")
        require(info.st_size <= 8 * 1024 * 1024, "file exceeds inspection limit")
        require(not info.st_mode & 0o022, "writable file")
        if private:
            require(stat.S_IMODE(info.st_mode) == 0o600, "private file must have mode 0600")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(fd)


def private_directory(path: Path, *, create: bool = False) -> None:
    ancestors(path)
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "bridge directory must be owned by the user with mode 0700")


def executable(path: Path) -> str:
    require(path.is_absolute(), "executable path must be absolute")
    # Package-manager symlinks are legitimate for executables, never for state.
    resolved = path.resolve(strict=True)
    # The stored path is executed later, so protect its lexical ancestry too:
    # a safe target does not make a replaceable symlink in a public directory safe.
    for parent in dict.fromkeys((*path.parents, *resolved.parents)):
        info = parent.stat()
        require(info.st_uid in {0, os.getuid()} and not info.st_mode & 0o002,
                "untrusted executable ancestry")
    # Homebrew's user-owned Cellar/lib can be admin-group writable. This
    # exception is confined to the explicitly selected existing executable;
    # private state and package integrity checks retain the stricter policy.
    info = resolved.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid in {0, os.getuid()}
            and not info.st_mode & 0o022 and os.access(resolved, os.X_OK), "unsafe executable")
    return str(path)


def bundle() -> tuple[dict[str, bytes], str]:
    files = {name: read_regular(ASSETS / name) for name in ("package.json", "package-lock.json", "callback-readiness.patch.json")}
    manifest = object_json(files["package.json"])
    lock = object_json(files["package-lock.json"])
    require(manifest.get("dependencies") == {"mcp-remote": VERSION}, "unexpected package pin")
    require(lock.get("lockfileVersion") == 3, "lockfile v3 required")
    packages = lock.get("packages", {})
    require(packages.get("", {}).get("dependencies") == {"mcp-remote": VERSION}, "lock pin differs")
    require(packages.get("node_modules/mcp-remote", {}).get("version") == VERSION, "bridge version differs")
    for name, item in packages.items():
        if name:
            require(item.get("resolved", "").startswith("https://registry.npmjs.org/")
                    and item.get("integrity", "").startswith("sha512-"), "untrusted locked dependency")
    return files, digest(b"".join(files[name] for name in sorted(files)))



def apply_runtime_patch(runtime: Path, raw: bytes) -> None:
    """Apply the reviewed callback-readiness fix only to the exact pinned artifact."""
    document = object_json(raw)
    patches = document.get("patches")
    require(document.get("schema") == 1 and isinstance(patches, list)
            and len(patches) == 2, "invalid patch bundle")
    allowed = {"node_modules/mcp-remote/dist/proxy.js",
               "node_modules/mcp-remote/dist/chunk-UZ6Y4QJH.js"}
    for patch in patches:
        require(isinstance(patch, dict) and patch.get("target") in allowed, "unexpected patch target")
        allowed.remove(patch["target"])
        target = runtime / patch["target"]
        original = read_regular(target)
        require(digest(original) == patch.get("sha256_before"), "patch preimage differs")
        before, after = patch.get("before"), patch.get("after")
        require(isinstance(before, str) and bool(before) and isinstance(after, str), "invalid patch")
        require(original.count(before.encode()) == 1, "patch anchor differs")
        updated = original.replace(before.encode(), after.encode())
        require(digest(updated) == patch.get("sha256_after"), "patch postimage differs")
        target.write_bytes(updated)


def paths(home: Path) -> tuple[Path, Path, Path]:
    _, bundle_hash = bundle()
    root = home / "mcp/tavily"
    return root, root / "runtime" / f"{VERSION}-{bundle_hash[:12]}", root / "auth"


def auth_permissions(auth: Path) -> None:
    private_directory(auth)
    private_directory(auth / "mcp-remote-v1")
    # Only lstat credential entries; never open, hash or print their contents/names.
    for directory, dirs, files in os.walk(auth, followlinks=False):
        private_directory(Path(directory))
        for name in dirs:
            private_directory(Path(directory) / name)
        for name in files:
            info = (Path(directory) / name).lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600, "unsafe credential file permissions")


def inventory(runtime: Path) -> dict[str, str]:
    result = {}
    for directory, dirs, files in os.walk(runtime, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            relative = str(path.relative_to(runtime))
            if relative == RECEIPT:
                continue
            info = path.lstat()
            require(info.st_uid == os.getuid(), "foreign runtime owner")
            if stat.S_ISLNK(info.st_mode):
                target = os.readlink(path)
                require(not os.path.isabs(target) and path.resolve().is_relative_to(runtime),
                        "runtime symlink escapes installation")
                result[relative] = "link:" + target
            elif stat.S_ISDIR(info.st_mode):
                require(not info.st_mode & 0o022, "writable runtime directory")
            else:
                result[relative] = digest(read_regular(path))
    return result


def check(home: Path, agent: str) -> dict:
    require(agent in PORTS, "unknown agent")
    ancestors(home)
    root, runtime, auth = paths(home)
    for directory in (root, root / "runtime", runtime):
        private_directory(directory)
    receipt = object_json(read_regular(runtime / RECEIPT, private=True))
    require(receipt.get("schema") == 1 and receipt.get("agent") == agent
            and receipt.get("home_sha256") == digest(str(home).encode()), "installation binding differs")
    require(receipt.get("version") == VERSION and receipt.get("bundle_sha256") == bundle()[1],
            "installation pin differs")
    port = receipt.get("callback_port")
    require(type(port) is int and 1024 <= port <= 65535, "invalid callback port")
    executable(Path(receipt.get("node", "")))
    expected_files, _ = bundle()
    for name, expected in expected_files.items():
        require(read_regular(runtime / name) == expected, "runtime manifests differ")
    require(inventory(runtime) == receipt.get("files"), "runtime integrity differs")
    require((runtime / "node_modules/mcp-remote/dist/proxy.js").is_file(), "bridge entry point missing")
    auth_permissions(auth)
    return receipt


def configuration(home: Path, agent: str) -> dict:
    receipt = check(home, agent)
    _, runtime, auth = paths(home)
    value = {"command": receipt["node"], "args": [
        str(runtime / "node_modules/mcp-remote/dist/proxy.js"), ENDPOINT,
        str(receipt["callback_port"]), "--transport", "http-only", "--auth-timeout", "300", "--silent"],
        "env": {"MCP_REMOTE_CONFIG_DIR": str(auth)}}
    if agent == "codex":
        value.update(startup_timeout_sec=360.0, tool_timeout_sec=360.0)
    else:
        value["type"] = "stdio"
    return value


def numeric_at_least(value, floor: float) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
        return floor <= number < float("inf")
    except (TypeError, ValueError, OverflowError):
        return False


def validate_registration(home: Path, agent: str, actual: dict, settings: dict | None = None) -> None:
    expected = configuration(home, agent)
    require(isinstance(actual, dict), "Tavily registration missing")
    for key in ("command", "args", "env"):
        require(actual.get(key) == expected[key], "Tavily bridge wiring differs")
    require(not any(key in actual for key in ("url", "headers", "http_headers", "env_http_headers",
                                             "bearer_token_env_var", "oauth")),
            "HTTP authentication remnants in stdio entry")
    if agent == "codex":
        require(actual.get("enabled", True) is True, "selected Tavily bridge is disabled or invalid")
        require(all(type(actual.get(key)) in (int, float) for key in ("startup_timeout_sec", "tool_timeout_sec"))
                and numeric_at_least(actual.get("startup_timeout_sec"), 360)
                and numeric_at_least(actual.get("tool_timeout_sec"), 360), "Tavily timeout below auth window")
    else:
        require(actual.get("type", "stdio") == "stdio", "stdio transport required")
        env = (settings or {}).get("env", {})
        require(isinstance(env, dict) and numeric_at_least(env.get("MCP_TIMEOUT"), 360000),
                "Claude global MCP startup maximum below 360000 milliseconds")
        tool_limit = actual.get("timeout", env.get("MCP_TOOL_TIMEOUT", 100000000))
        require("timeout" not in actual or type(actual["timeout"]) in (int, float),
                "Claude per-server timeout must be numeric")
        require(numeric_at_least(tool_limit, 360000), "Claude Tavily tool timeout below auth window")


def prepare(home: Path, agent: str, node: Path, npm: Path, port: int | None = None) -> bool:
    require(agent in PORTS, "unknown agent")
    ancestors(home)
    require(home.is_dir(), "create the selected native home before bridge setup")
    require(not any((parent / ".git").exists() for parent in (home, *home.parents)),
            "bridge state must be outside Git")
    root, runtime, auth = paths(home)
    if runtime.exists() or runtime.is_symlink():
        receipt = check(home, agent)
        require(receipt["node"] == str(node) and (port is None or receipt["callback_port"] == port),
                "existing bridge binding conflicts; inspect before replacement")
        return False
    if port is None:
        require(home == Path.home() / f".{agent}", "custom agent homes require an explicit distinct callback port")
        port = PORTS[agent]
    require(type(port) is int and 1024 <= port <= 65535, "callback port must be 1024-65535")
    node_command = executable(node)
    npm_command = str(Path(executable(npm)).resolve())
    try:
        version = subprocess.run([node_command, "--version"], capture_output=True, text=True, check=True, timeout=10)
        parts = tuple(int(item) for item in version.stdout.strip().removeprefix("v").split("."))
        require(parts >= (22, 20, 0), "Node 22.20 or newer is required")
    except (subprocess.SubprocessError, ValueError) as error:
        raise BridgeError("supported Node executable required") from error
    for directory in (home / "mcp", root, root / "runtime"):
        private_directory(directory, create=True)
    lock = root / "prepare.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    stage = None
    try:
        require(not runtime.exists(), "runtime appeared concurrently")
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=root / "runtime"))
        files, bundle_hash = bundle()
        for name, data in files.items():
            target = stage / name
            target.write_bytes(data)
            target.chmod(0o600)
        env = dict(os.environ, PATH=str(node.parent) + os.pathsep + os.environ.get("PATH", ""))
        result = subprocess.run([node_command, npm_command, "ci", "--ignore-scripts", "--omit=dev",
                                 "--no-audit", "--no-fund", "--registry=https://registry.npmjs.org"],
                                cwd=stage, env=env, capture_output=True, timeout=240)
        require(result.returncode == 0, "dependency installation failed; native config unchanged")
        apply_runtime_patch(stage, files["callback-readiness.patch.json"])
        receipt = {"schema": 1, "agent": agent, "home_sha256": digest(str(home).encode()),
                   "version": VERSION, "bundle_sha256": bundle_hash, "node": node_command,
                   "callback_port": port, "files": inventory(stage)}
        target = stage / RECEIPT
        target.write_text(json.dumps(receipt, indent=2) + "\n")
        target.chmod(0o600)
        private_directory(auth, create=True)
        private_directory(auth / "mcp-remote-v1", create=True)
        auth_permissions(auth)
        require(not runtime.exists(), "runtime appeared concurrently")
        stage.rename(runtime)
        stage = None
        check(home, agent)
        return True
    finally:
        if stage is not None:
            # Only the fresh, unpublished directory allocated by this invocation.
            shutil.rmtree(stage)
        lock.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("action", choices=("prepare", "check"))
    parser.add_argument("--agent", choices=tuple(PORTS), required=True)
    parser.add_argument("--agent-home", type=Path, required=True)
    parser.add_argument("--node", type=Path, help="Absolute existing Node executable; required for prepare.")
    parser.add_argument("--npm", type=Path, help="Absolute existing npm CLI; required for prepare.")
    parser.add_argument("--callback-port", type=int, help="Distinct port; required for a new custom-home binding.")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            require(args.node is not None and args.npm is not None, "prepare requires --node and --npm")
            changed = prepare(args.agent_home, args.agent, args.node, args.npm, args.callback_port)
            print(json.dumps({"status": "PREPARED", "changed": changed, "authentication": "NOT_RUN"}))
        else:
            check(args.agent_home, args.agent)
            print('{"status":"STATIC_PASS","authentication":"NOT_RUN"}')
        return 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, BridgeError) else "unavailable or unsafe bridge path/process"
        print(json.dumps({"status": "NOT_ALIGNED", "reason": reason, "authentication": "NOT_RUN"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
