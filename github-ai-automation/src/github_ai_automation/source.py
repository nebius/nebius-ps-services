"""Candidate source is data. Git metadata stays outside the agent's mount."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path

from .contracts import Blocked, Candidate, safe_path
from .sandbox import command


def snapshot(candidate: Candidate, destination: Path):
    destination.mkdir(mode=0o700, parents=True)
    metadata, source = destination / "git", destination / "source"
    source.mkdir()
    env = {
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    command(["git", "init", "--bare", str(metadata)], env=env)
    command(
        [
            "git",
            "--git-dir",
            str(metadata),
            "fetch",
            "--depth=200",
            f"https://github.com/{candidate.repository}.git",
            f"refs/pull/{candidate.pr_number}/head",
        ],
        env=env,
    )
    head = (
        command(["git", "--git-dir", str(metadata), "rev-parse", "FETCH_HEAD"], env=env)
        .decode()
        .strip()
    )
    if head != candidate.head_sha:
        raise Blocked("SOURCE_HEAD_MISMATCH")
    actual = (
        command(["git", "--git-dir", str(metadata), "rev-parse", "FETCH_HEAD^{tree}"], env=env)
        .decode()
        .strip()
    )
    if actual != candidate.tree_sha:
        raise Blocked("SOURCE_TREE_MISMATCH")
    listing = command(
        ["git", "--git-dir", str(metadata), "ls-tree", "-rz", candidate.head_sha], env=env
    )
    entries = {}
    for line in listing.split(b"\0"):
        if not line:
            continue
        header, raw_path = line.split(b"\t", 1)
        mode, kind, sha = header.decode().split()
        name = safe_path(raw_path.decode())
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise Blocked("SOURCE_LINK_OR_SUBMODULE_UNSUPPORTED")
        entries[name] = {"mode": mode, "sha": sha}
    # Checkout uses isolated empty Git configuration: no hooks, submodules or user filters.
    command(
        [
            "git",
            "--git-dir",
            str(metadata),
            "--work-tree",
            str(source),
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            candidate.head_sha,
            "--",
            ".",
        ],
        env=env,
    )
    for name, item in entries.items():
        data = (source / name).read_bytes()
        if (
            hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            != item["sha"]
        ):
            raise Blocked("SOURCE_BYTES_MISMATCH")
    command(
        [
            "git",
            "--git-dir",
            str(metadata),
            "fetch",
            "--depth=200",
            f"https://github.com/{candidate.repository}.git",
            candidate.base_sha,
        ],
        env=env,
    )
    try:
        bases = (
            command(
                [
                    "git",
                    "--git-dir",
                    str(metadata),
                    "merge-base",
                    "--all",
                    candidate.base_sha,
                    candidate.head_sha,
                ],
                env=env,
            )
            .decode()
            .splitlines()
        )
    except Blocked:
        raise Blocked("CONTRIBUTION_HISTORY_UNAVAILABLE") from None
    if len(bases) != 1:
        raise Blocked("CONTRIBUTION_HISTORY_UNAVAILABLE")
    diff = command(
        [
            "git",
            "--git-dir",
            str(metadata),
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            bases[0],
            candidate.head_sha,
        ],
        env=env,
    )
    if len(diff) > 2_000_000:
        raise Blocked("REVIEW_DIFF_TOO_LARGE")
    (destination / "diff.txt").write_bytes(diff)
    return source, entries


def baseline_context(candidate, destination, paths):
    """Read accepted documents from Git objects, never contributor-edited specs."""
    metadata = destination / "git"
    names = {"README.md", "docs/requirements.md", "docs/design.md"}
    for name in paths:
        for parent in Path(name).parents:
            if str(parent) != ".":
                names.update(
                    str(parent / suffix)
                    for suffix in ("README.md", "docs/requirements.md", "docs/design.md")
                )
    result = {}
    total = 0
    for name in sorted(names):
        item = subprocess.run(
            ["git", "--git-dir", str(metadata), "show", f"{candidate.base_sha}:{name}"],
            capture_output=True,
            timeout=30,
            env={
                "PATH": os.environ["PATH"],
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
            },
        )
        if item.returncode:
            continue
        total += len(item.stdout)
        if total > 500_000:
            raise Blocked("BASELINE_CONTEXT_TOO_LARGE")
        result[name] = item.stdout.decode(errors="replace")
    return result


def prepare_conflict_tree(candidate: Candidate, destination: Path, source: Path):
    """Export Git's merge result as data; retain no Git metadata in the agent mount."""
    metadata = destination / "git"
    env = {
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
    }
    args = ["git", "--git-dir", str(metadata)]
    result = subprocess.run(
        [
            *args,
            "-c",
            "core.hooksPath=/dev/null",
            "merge-tree",
            "--write-tree",
            candidate.head_sha,
            candidate.base_sha,
        ],
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    tree = result.stdout.splitlines()[0].decode() if result.stdout else ""
    if result.returncode not in {0, 1} or not re.fullmatch(r"[a-f0-9]{40}", tree):
        raise Blocked("CONFLICT_HISTORY_UNAVAILABLE")
    listing = command([*args, "ls-tree", "-rz", tree], env=env)
    for row in listing.split(b"\0"):
        if not row:
            continue
        header, path = row.split(b"\t", 1)
        mode, kind, _ = header.decode().split()
        safe_path(path.decode())
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise Blocked("CONFLICT_TREE_UNSUPPORTED")
    for path in sorted(source.rglob("*"), reverse=True):
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
    command(
        [
            *args,
            "--work-tree",
            str(source),
            "-c",
            "core.hooksPath=/dev/null",
            "checkout",
            tree,
            "--",
            ".",
        ],
        env=env,
    )
    return result.returncode == 1


def require_resolved_conflicts(patch):
    for item in patch:
        if item["content"] and re.search(
            rb"(?m)^(?:<{7}|={7}|>{7})(?: |$)", base64.b64decode(item["content"])
        ):
            raise Blocked("UNRESOLVED_CONFLICT_MARKERS")


def changes(source: Path, original: dict) -> list[dict]:
    current = {}
    for path in source.rglob("*"):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Blocked("PATCH_LINK_OR_SPECIAL_FILE")
        name = safe_path(str(path.relative_to(source)))
        data = path.read_bytes()
        sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        mode = "100755" if info.st_mode & stat.S_IXUSR else "100644"
        current[name] = {"sha": sha, "mode": mode, "data": data}
    delta = []
    total = 0
    for name in sorted(set(original) | set(current)):
        before, after = original.get(name), current.get(name)
        if before and after and before["sha"] == after["sha"] and before["mode"] == after["mode"]:
            continue
        if after:
            total += len(after["data"])
        if total > 512_000 or len(delta) >= 100:
            raise Blocked("PATCH_TOO_LARGE")
        delta.append(
            {
                "path": name,
                "mode": after["mode"] if after else before["mode"],
                "content": base64.b64encode(after["data"]).decode() if after else None,
            }
        )
    return delta


def export_base(candidate, destination, source):
    """Export verified regular Git blobs, without filters, from the frozen default."""
    args = ["git", "--git-dir", str(destination / "git")]
    env = {"PATH": os.environ["PATH"], "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    entries = {}
    listing = command([*args, "ls-tree", "-rz", candidate.base_sha], env=env)
    for row in listing.split(b"\0"):
        if not row:
            continue
        header, path = row.split(b"\t", 1)
        mode, kind, sha = header.decode().split()
        name = safe_path(path.decode())
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise Blocked("ADOPTION_BASE_UNSUPPORTED")
        entries[name] = {"mode": mode, "sha": sha}
    for path in sorted(source.rglob("*"), reverse=True):
        if path.is_dir() and not path.is_symlink():
            path.rmdir()
        else:
            path.unlink()
    for name, item in entries.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(command([*args, "cat-file", "blob", item["sha"]], env=env))
        path.chmod(int(item["mode"], 8) & 0o777)
    return entries


def source_entries(source):
    entries = {}
    for path in source.rglob("*"):
        if path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            raise Blocked("PATCH_LINK_OR_SPECIAL_FILE")
        data = path.read_bytes()
        entries[safe_path(str(path.relative_to(source)))] = {
            "mode": "100755" if path.stat().st_mode & stat.S_IXUSR else "100644",
            "sha": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
        }
    return entries
