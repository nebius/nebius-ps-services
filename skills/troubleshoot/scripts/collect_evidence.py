#!/usr/bin/env python3
"""Collect bounded, read-only local repository and environment evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

from evidence_redaction import redact_text

SCHEMA_VERSION = 1
MAX_GIT_STATUS_RECORDS = 100000
MANIFEST_NAMES = (
    "Cargo.lock",
    "Cargo.toml",
    "Gemfile.lock",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
)
TOOL_COMMANDS = {
    "bash": ("bash", "--version"),
    "docker": ("docker", "--version"),
    "git": ("git", "--version"),
    "go": ("go", "version"),
    "helm": ("helm", "version", "--short"),
    "java": ("java", "-version"),
    "kubectl": ("kubectl", "version", "--client=true"),
    "node": ("node", "--version"),
    "python3": ("python3", "--version"),
    "rustc": ("rustc", "--version"),
    "terraform": ("terraform", "version"),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Collect safe local repository and environment identity as JSON. "
            "The collector does not include source contents, Git remotes, or "
            "environment variable values."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository or working-directory root to inspect (default: cwd).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write JSON atomically to this file instead of stdout.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Indent JSON output for human reading.",
    )
    return parser.parse_args()


def run_bounded(argv: tuple[str, ...], cwd: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr).strip()
    if not output:
        return None
    return redact_text(output.splitlines()[0][:300])


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_manifests(root: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for name in MANIFEST_NAMES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            continue
        try:
            manifests.append(
                {
                    "path": name,
                    "size_bytes": path.stat().st_size,
                    "sha256": hash_file(path),
                }
            )
        except OSError:
            manifests.append({"path": name, "error": "unreadable"})
    return manifests


def detect_git_work_tree(root: Path) -> tuple[bool | None, str | None]:
    try:
        result = subprocess.run(
            ("git", "rev-parse", "--is-inside-work-tree"),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None, "git_work_tree_detection_timeout"
    except OSError:
        return None, "git_work_tree_detection_unavailable"
    if result.returncode != 0:
        return None, "git_work_tree_detection_failed"
    value = result.stdout.strip()
    if value == "true":
        return True, None
    if value == "false":
        return False, None
    return None, "git_work_tree_detection_unexpected"


def drain_git_status(stream: BinaryIO, state: dict[str, Any]) -> None:
    try:
        while line := stream.readline():
            state["records_seen"] += 1
            if state["records_counted"] >= MAX_GIT_STATUS_RECORDS:
                state["truncated"] = True
                continue
            state["records_counted"] += 1
            status = line[:2]
            if status == b"??":
                state["untracked"] += 1
                continue
            if status[:1] not in (b" ", b""):
                state["staged"] += 1
            if status[1:2] not in (b" ", b""):
                state["unstaged"] += 1
    except (OSError, ValueError):
        state["stream_error"] = True


def collect_git_status(root: Path) -> tuple[dict[str, Any] | None, str | None]:
    state: dict[str, Any] = {
        "records_seen": 0,
        "records_counted": 0,
        "staged": 0,
        "unstaged": 0,
        "untracked": 0,
        "truncated": False,
        "stream_error": False,
    }
    process: subprocess.Popen[bytes] | None = None
    thread: threading.Thread | None = None
    try:
        process = subprocess.Popen(
            ("git", "status", "--porcelain=v1", "--untracked-files=all"),
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        thread = threading.Thread(
            target=drain_git_status,
            args=(process.stdout, state),
            daemon=True,
        )
        thread.start()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
            return None, "git_status_timeout"
        thread.join(timeout=2)
        if thread.is_alive() or state["stream_error"]:
            return None, "git_status_stream_failed"
        if process.returncode != 0:
            return None, "git_status_failed"
    except OSError:
        return None, "git_status_unavailable"
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=2)
        if process is not None and process.stdout is not None:
            process.stdout.close()
        if thread is not None:
            thread.join(timeout=1)
    return state, None


def collect_git(root: Path) -> dict[str, Any]:
    inside, detection_error = detect_git_work_tree(root)
    if inside is None:
        return {
            "is_work_tree": None,
            "detection_available": False,
            "detection_error": detection_error,
            "dirty": None,
            "status_available": False,
            "status_error": "git_status_not_attempted",
            "status_counts": None,
        }
    if not inside:
        return {
            "is_work_tree": False,
            "detection_available": True,
            "detection_error": None,
        }

    head = run_bounded(("git", "rev-parse", "--short=12", "HEAD"), root)
    status, status_error = collect_git_status(root)

    if status_error is not None:
        return {
            "is_work_tree": True,
            "detection_available": True,
            "detection_error": None,
            "head": head,
            "dirty": None,
            "status_available": False,
            "status_error": status_error,
            "status_counts": None,
        }

    assert status is not None
    return {
        "is_work_tree": True,
        "detection_available": True,
        "detection_error": None,
        "head": head,
        "dirty": bool(status["records_seen"]),
        "status_available": True,
        "status_error": None,
        "status_records_seen": status["records_seen"],
        "status_records_counted": status["records_counted"],
        "status_truncated": status["truncated"],
        "status_counts": {
            "staged": status["staged"],
            "unstaged": status["unstaged"],
            "untracked": status["untracked"],
        },
    }


def collect_resource_limits() -> dict[str, Any]:
    try:
        import resource
    except ImportError:
        return {"supported": False}

    names = (
        "RLIMIT_AS",
        "RLIMIT_CORE",
        "RLIMIT_CPU",
        "RLIMIT_DATA",
        "RLIMIT_FSIZE",
        "RLIMIT_NOFILE",
        "RLIMIT_NPROC",
        "RLIMIT_STACK",
    )
    limits: dict[str, Any] = {"supported": True}
    for name in names:
        identifier = getattr(resource, name, None)
        if identifier is None:
            continue
        try:
            soft, hard = resource.getrlimit(identifier)
        except (OSError, ValueError):
            continue
        infinity = resource.RLIM_INFINITY
        limits[name] = {
            "soft": "infinity" if soft == infinity else soft,
            "hard": "infinity" if hard == infinity else hard,
        }
    return limits


def collect_filesystem_behavior() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="troubleshoot-case-") as temp_dir:
        directory = Path(temp_dir)
        lower = directory / "casecheck"
        upper = directory / "CASECHECK"
        lower.write_text("x", encoding="utf-8")
        case_sensitive = not upper.exists()
        symlink_supported = True
        try:
            (directory / "link").symlink_to(lower.name)
        except OSError:
            symlink_supported = False
    return {
        "probe_scope": "system_temporary_directory",
        "case_sensitive": case_sensitive,
        "symlink_supported": symlink_supported,
    }


def collect_tools() -> dict[str, Any]:
    tools: dict[str, Any] = {}
    for name, argv in TOOL_COMMANDS.items():
        executable = shutil.which(argv[0])
        if executable is None:
            tools[name] = {"available": False}
            continue
        tools[name] = {
            "available": True,
            "version": run_bounded(argv),
        }
    return tools


def collect_translation_status() -> bool | None:
    if platform.system() != "Darwin":
        return None
    value = run_bounded(("sysctl", "-in", "sysctl.proc_translated"))
    if value == "1":
        return True
    if value == "0":
        return False
    return None


def build_evidence(root: Path) -> dict[str, Any]:
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("root is not a directory")

    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": datetime.now(UTC).isoformat(),
        "repository": {
            "root_id": hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:12],
            "git": collect_git(resolved),
            "manifests": collect_manifests(resolved),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "process_bits": platform.architecture()[0],
            "translated_process": collect_translation_status(),
        },
        "locale": {
            "preferred_encoding": locale.getpreferredencoding(False),
            "timezone": list(time.tzname),
        },
        "filesystem": collect_filesystem_behavior(),
        "resource_limits": collect_resource_limits(),
        "tools": collect_tools(),
    }


def render_json(payload: dict[str, Any], pretty: bool) -> str:
    return (
        json.dumps(
            payload,
            indent=2 if pretty else None,
            sort_keys=True,
            ensure_ascii=True,
        )
        + "\n"
    )


def write_atomic(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError("refusing symlink output path")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("output parent is not a directory")

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> int:
    args = parse_args()
    try:
        payload = build_evidence(args.root)
        content = render_json(payload, args.pretty)
        if args.output is None:
            sys.stdout.write(content)
        else:
            write_atomic(args.output, content)
    except (ValueError, json.JSONDecodeError) as error:
        print(f"collect_evidence.py: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("collect_evidence.py: input or output I/O failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
