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
import stat
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bounded_process import observe
from evidence_redaction import redact_text

SCHEMA_VERSION = 2
MAX_GIT_STATUS_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
MAX_MANIFEST_TOTAL = 50 * 1024 * 1024
COLLECTION_SECONDS = 30
SECTIONS = ("repository", "platform", "locale", "resource-limits", "filesystem")
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
    parser.add_argument(
        "--section",
        action="append",
        choices=SECTIONS,
        help="Section to collect; repeat. Default: repository and platform.",
    )
    parser.add_argument(
        "--tool",
        action="append",
        choices=sorted(TOOL_COMMANDS),
        default=[],
        help="Known version probe; repeat. Default: none.",
    )
    return parser.parse_args()


def run_bounded(
    argv: tuple[str, ...], cwd: Path | None = None, deadline: float | None = None
) -> dict[str, Any]:
    result = observe(argv, cwd, deadline=deadline)
    output = (result.pop("stdout") or result.pop("stderr", b"")).decode(
        "utf-8", "replace"
    )
    result.pop("stderr", None)
    result["value"] = redact_text(output.splitlines()[0][:300]) if output else None
    return result


def collect_manifests(
    root: Path, deadline: float | None = None
) -> list[dict[str, Any]]:
    manifests = []
    remaining = MAX_MANIFEST_TOTAL
    for name in MANIFEST_NAMES:
        path = root / name
        if not path.exists() and not path.is_symlink():
            continue
        item: dict[str, Any] = {"path": name, "sha256": None}
        manifests.append(item)
        if deadline is not None and time.monotonic() >= deadline:
            item["status"] = "skipped"
            continue
        try:
            if path.is_symlink():
                item["status"] = "skipped"
                continue
            flags = (
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            with os.fdopen(os.open(path, flags), "rb") as handle:
                before = os.fstat(handle.fileno())
                item["size_bytes"] = before.st_size
                if not stat.S_ISREG(before.st_mode):
                    item["status"] = "skipped"
                    continue
                if before.st_size > min(MAX_MANIFEST_BYTES, remaining):
                    item["status"] = "skipped"
                    item["reason"] = "manifest_byte_budget"
                    continue
                digest = hashlib.sha256()
                count = 0
                limit = min(MAX_MANIFEST_BYTES, remaining)
                item["status"] = "ok"
                while count < limit:
                    if deadline is not None and time.monotonic() >= deadline:
                        item["status"] = "timeout"
                        break
                    chunk = handle.read(min(65536, limit - count))
                    if not chunk:
                        break
                    digest.update(chunk)
                    count += len(chunk)
                remaining -= count
                after = os.fstat(handle.fileno())
                current = path.stat(follow_symlinks=False)

                def identity(st):
                    return (
                        st.st_dev,
                        st.st_ino,
                        st.st_size,
                        st.st_mtime_ns,
                        st.st_ctime_ns,
                    )

                if identity(before) != identity(after) or identity(after) != identity(
                    current
                ):
                    item["status"] = "changed-during-read"
                elif count != before.st_size and item["status"] == "ok":
                    item["status"] = "truncated"
                elif item["status"] == "ok":
                    item["sha256"] = digest.hexdigest()
                item["bytes_read"] = count
        except OSError:
            item["status"] = "unavailable"
    return manifests


def collect_git(root: Path, deadline: float | None = None) -> dict[str, Any]:
    detect = observe(
        ("git", "rev-parse", "--is-inside-work-tree"), root, deadline=deadline
    )
    inside = detect["stdout"].strip() == b"true" if detect["status"] == "ok" else None
    result: dict[str, Any] = {
        "is_work_tree": inside,
        "detection_available": inside is not None,
        "detection_error": None if inside is not None else detect["status"],
        "dirty": None,
        "status_available": False,
        "status_counts": None,
    }
    if not inside:
        return result
    result["head"] = run_bounded(
        ("git", "rev-parse", "--short=12", "HEAD"), root, deadline
    )
    status = observe(
        (
            "git",
            "--no-optional-locks",
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ),
        root,
        deadline=deadline,
        stdout_limit=MAX_GIT_STATUS_BYTES,
    )
    result.update(
        status=status["status"],
        status_error=None if status["status"] == "ok" else status["status"],
        status_truncated=status["truncated"],
    )
    # Git quotes embedded newlines by default. Discard a partial final record.
    records = status["stdout"].splitlines(keepends=True)
    records = [line for line in records if line.endswith(b"\n")]
    counts = {"staged": 0, "unstaged": 0, "untracked": 0}
    for line in records:
        code = line[:2]
        if code == b"??":
            counts["untracked"] += 1
        else:
            counts["staged"] += code[:1] != b" "
            counts["unstaged"] += code[1:2] != b" "
    result.update(
        status_available=status["status"] == "ok",
        status_counts=counts,
        status_records_counted=len(records),
    )
    # Incomplete evidence cannot prove a clean tree.
    result["dirty"] = (
        True if any(counts.values()) else (False if status["status"] == "ok" else None)
    )
    return result


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


def collect_tools(names: list[str], deadline: float) -> dict[str, Any]:
    def probe(name: str) -> tuple[str, dict[str, Any]]:
        if shutil.which(TOOL_COMMANDS[name][0]) is None:
            return name, {"status": "unavailable", "value": None}
        return name, run_bounded(TOOL_COMMANDS[name], deadline=deadline)

    with ThreadPoolExecutor(max_workers=4) as pool:
        return dict(pool.map(probe, dict.fromkeys(names)))


def build_evidence(
    root: Path, sections: list[str] | None = None, tools: list[str] | None = None
) -> dict[str, Any]:
    deadline = time.monotonic() + COLLECTION_SECONDS - 2
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("root is not a directory")
    selected = list(
        dict.fromkeys(sections if sections is not None else ["repository", "platform"])
    )
    if set(selected) - set(SECTIONS) or set(tools or []) - set(TOOL_COMMANDS):
        raise ValueError("unknown section or tool")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "collected_at": datetime.now(UTC).isoformat(),
        "sections": selected,
        "collection_deadline_seconds": COLLECTION_SECONDS,
    }
    for section in selected:
        key = section.replace("-", "_")
        if time.monotonic() >= deadline:
            result[key] = {"status": "skipped", "reason": "collection_deadline"}
        elif section == "repository":
            result[key] = {
                "root_id": hashlib.sha256(str(resolved).encode()).hexdigest()[:12],
                "git": collect_git(resolved, deadline),
                "manifests": collect_manifests(resolved, deadline),
            }
        elif section == "platform":
            result[key] = {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "process_bits": 64 if sys.maxsize > 2**32 else 32,
            }
        elif section == "locale":
            result[key] = {
                "preferred_encoding": locale.getpreferredencoding(False),
                "timezone": list(time.tzname),
            }
        elif section == "resource-limits":
            result[key] = collect_resource_limits()
        elif section == "filesystem":
            try:
                result[key] = {"status": "ok", **collect_filesystem_behavior()}
            except OSError:
                result[key] = {
                    "status": "unavailable",
                    "probe_scope": "system_temporary_directory",
                }
    result["tools"] = collect_tools(tools or [], deadline)
    return result


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
        worker = Path(__file__).with_name("collection_worker.py")
        result = observe(
            [
                sys.executable,
                str(worker),
                str(args.root),
                json.dumps(args.section),
                json.dumps(args.tool),
            ],
            timeout=COLLECTION_SECONDS,
        )
        if result["status"] != "ok":
            payload = {
                "schema_version": SCHEMA_VERSION,
                "status": result["status"],
                "sections": args.section or ["repository", "platform"],
                "reason": "collection_incomplete",
                "observations": None,
            }
        else:
            payload = json.loads(result["stdout"])
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
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
