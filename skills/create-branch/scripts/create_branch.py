#!/usr/bin/env python3
"""Create a local branch from origin's freshly fetched default, preserving work."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import string
import subprocess
import sys
from urllib.parse import urlsplit


class Blocked(RuntimeError):
    """The operation cannot safely continue; completed effects are retained."""


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(cwd), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        check=False,
    )
    if check and result.returncode:
        # Git diagnostics can contain credential-bearing remote URLs. Keep them private.
        raise Blocked(f"git {args[0]} failed (exit {result.returncode})")
    return result


def output(result: subprocess.CompletedProcess) -> str:
    # Git terminates these records with one newline; whitespace can belong to paths.
    return result.stdout.decode("utf-8", errors="surrogateescape").removesuffix("\n")


def conflicts(name: str, occupied: set[str]) -> bool:
    """Also avoid namespace and case-only collisions on portable filesystems."""
    candidate = name.casefold()
    return any(
        candidate == other.casefold()
        or candidate.startswith(other.casefold() + "/")
        or other.casefold().startswith(candidate + "/")
        for other in occupied
    )


def repository_slug(url: str) -> str:
    path = urlsplit(url).path if "://" in url else url.rsplit(":", 1)[-1]
    name = path.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        raise Blocked("Cannot derive a repository name; supply --branch explicitly")
    return slug


class Workflow:
    def __init__(self, cwd: Path):
        self.phase = "preflight"
        self.root = cwd
        for variable in (
            "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE",
        ):
            if os.environ.get(variable):
                raise Blocked(f"Explicit {variable} environment override is unsupported")
        self.root = Path(output(git(cwd, "rev-parse", "--show-toplevel"))).resolve()
        self.git_dir = Path(output(git(self.root, "rev-parse", "--absolute-git-dir")))
        self.previous_ref, self.previous_sha = self.identity()
        self.expected_ref, self.expected_sha = self.previous_ref, self.previous_sha
        self.origin = output(git(self.root, "remote", "get-url", "--all", "origin"))
        if not self.origin or len(self.origin.splitlines()) != 1:
            raise Blocked("Exactly one fetch URL for origin is required")
        self.default = ""
        self.target = ""
        self.name = ""
        self.default_before: str | None = None

    def identity(self) -> tuple[str, str]:
        ref = output(git(self.root, "symbolic-ref", "--quiet", "HEAD"))
        if not ref.startswith("refs/heads/"):
            raise Blocked("An attached local branch is required")
        return ref, output(git(self.root, "rev-parse", "--verify", "HEAD^{commit}"))

    def ref_value(self, ref: str) -> str | None:
        symbolic = git(self.root, "symbolic-ref", "--quiet", ref, check=False)
        if symbolic.returncode == 0:
            raise Blocked("An affected branch or tracking ref is symbolic")
        if symbolic.returncode != 1:
            raise Blocked("Cannot inspect affected ref identity")
        found = git(self.root, "show-ref", "--verify", "--quiet", ref, check=False)
        if found.returncode == 1:
            return None
        if found.returncode:
            raise Blocked("Cannot inspect affected ref")
        return output(git(self.root, "rev-parse", "--verify", ref + "^{commit}"))

    def validate_name(self, name: str) -> None:
        checked = git(self.root, "check-ref-format", "--branch", name, check=False)
        if checked.returncode or output(checked) != name or name in {"HEAD", "@", "-"}:
            raise Blocked("Invalid literal branch name")

    def local_names(self) -> set[str]:
        return set(output(git(self.root, "for-each-ref", "--format=%(refname:strip=2)",
                              "refs/heads/")).splitlines())

    def guard(self, protected: set[str]) -> None:
        if self.identity() != (self.expected_ref, self.expected_sha):
            raise Blocked("Checkout identity changed; another Git writer may be active")
        if output(git(self.root, "remote", "get-url", "--all", "origin")) != self.origin:
            raise Blocked("Origin changed during the operation")
        status = git(self.root, "status", "--porcelain=v1", "-z",
                     "--untracked-files=all", "--ignore-submodules=none").stdout
        if status:
            raise Blocked("Checkout is dirty; review and commit your work before rerunning")
        entries = git(self.root, "ls-files", "-v", "-z").stdout.split(b"\0")
        if any(entry[:1] == b"S" or entry[:1].islower() for entry in entries if entry):
            raise Blocked("Index flags hide local work; review assume-unchanged/skip-worktree entries")
        for marker in (
            "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "REBASE_HEAD",
            "rebase-merge", "rebase-apply", "sequencer", "BISECT_START",
            "index.lock", "HEAD.lock", "packed-refs.lock", "config.lock",
        ):
            if os.path.lexists(self.git_dir / marker):
                raise Blocked(f"Pending Git operation or lock: {marker}")
        for ref in protected:
            if os.path.lexists(self.git_dir / (ref + ".lock")):
                raise Blocked("An affected branch has a Git lock")
        raw = git(self.root, "worktree", "list", "--porcelain", "-z").stdout
        records = []
        for record in raw.split(b"\0\0"):
            if not record:
                continue
            fields = {}
            for field in record.split(b"\0"):
                if not field:
                    continue
                key, _, value = field.partition(b" ")
                fields[key] = os.fsdecode(value)
            if b"worktree" not in fields:
                raise Blocked("Cannot interpret worktree registrations")
            records.append(fields)
        if not records or Path(records[0][b"worktree"]).resolve() != self.root:
            raise Blocked("Run from the ordinary primary checkout, not a linked worktree")
        for record in records[1:]:
            if record.get(b"branch") in protected:
                raise Blocked("An affected branch is registered in another worktree")

    def discover(self) -> set[str]:
        advertised = output(git(self.root, "ls-remote", "--symref", "origin",
                                "HEAD", "refs/heads/*"))
        defaults = []
        heads = set()
        for line in advertised.splitlines():
            value, separator, ref = line.partition("\t")
            if not separator:
                raise Blocked("Cannot interpret advertised remote refs")
            if ref == "HEAD" and value.startswith("ref: refs/heads/"):
                defaults.append(value.removeprefix("ref: refs/heads/"))
            elif ref.startswith("refs/heads/"):
                if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value):
                    raise Blocked("Invalid advertised commit")
                heads.add(ref.removeprefix("refs/heads/"))
        if len(defaults) != 1 or defaults[0] not in heads:
            raise Blocked("Origin must advertise one existing symbolic default branch")
        self.default = defaults[0]
        self.validate_name(self.default)
        return heads

    def check_merge_options(self) -> None:
        result = git(self.root, "config", "--get-all",
                     f"branch.{self.default}.mergeOptions", check=False)
        if result.returncode not in (0, 1):
            raise Blocked("Cannot inspect default-branch merge options")
        safe = {"--ff", "--ff-only", "--no-edit", "--no-autostash",
                "--no-overwrite-ignore", "--no-rerere-autoupdate"}
        try:
            options = shlex.split(output(result))
        except ValueError as error:
            raise Blocked("Invalid default-branch merge options") from error
        if any(option not in safe for option in options):
            raise Blocked("Default-branch merge options conflict with safe fast-forwarding")

    def check_refs(self, expected_default: str | None) -> None:
        if self.ref_value(f"refs/heads/{self.default}") != expected_default:
            raise Blocked("Local default changed during the operation")
        if self.previous_ref != f"refs/heads/{self.default}":
            if self.ref_value(self.previous_ref) != self.previous_sha:
                raise Blocked("Previous feature branch changed during the operation")

    def default_tracking_missing(self) -> bool:
        result = git(self.root, "config", "--get-all", "remote.origin.fetch", check=False)
        if result.returncode not in (0, 1):
            raise Blocked("Cannot inspect origin tracking mappings")
        source = f"refs/heads/{self.default}"
        destination = f"refs/remotes/origin/{self.default}"
        present = False
        for refspec in output(result).splitlines():
            left, _, right = refspec.lstrip("+").partition(":")
            pattern = left.removeprefix("^")
            match = re.fullmatch(re.escape(pattern).replace(r"\*", "(.*)"), source)
            if left.startswith("^"):
                if match:
                    raise Blocked("Origin explicitly excludes its default branch from tracking")
                continue
            if match:
                mapped = right.replace("*", match[1]) if "*" in right else right
                present |= mapped == destination
            reverse = re.fullmatch(re.escape(right).replace(r"\*", "(.*)"), destination)
            if right and reverse:
                mapped_source = left.replace("*", reverse[1]) if "*" in left else left
                if mapped_source != source:
                    raise Blocked("Origin tracking destination conflicts with its default branch")
        return not present

    def run(self, branch: str | None) -> dict:
        self.guard({self.previous_ref})
        if branch is not None:
            self.validate_name(branch)
        self.phase = "discover"
        remote_names = self.discover()
        default_ref = f"refs/heads/{self.default}"
        protected = {self.previous_ref, default_ref}
        self.guard(protected)
        occupied = self.local_names() | remote_names
        if branch is not None:
            if conflicts(branch, occupied):
                raise Blocked("Requested branch name is already occupied or conflicts with a ref")
            self.name = branch
        else:
            prefix = repository_slug(self.origin)
            alphabet = string.ascii_lowercase + string.digits
            for _ in range(10):
                candidate = prefix + "-" + "".join(secrets.choice(alphabet) for _ in range(8))
                self.validate_name(candidate)
                if not conflicts(candidate, occupied):
                    self.name = candidate
                    break
            if not self.name:
                raise Blocked("Generated names collided ten times; supply --branch")
        protected.add(f"refs/heads/{self.name}")
        self.default_before = self.ref_value(default_ref)
        tracking = f"refs/remotes/origin/{self.default}"
        self.ref_value(tracking)
        if self.default_before is None:
            self.default_tracking_missing()  # Reject exclusions/collisions before effects.
        self.check_merge_options()
        self.phase = "fetch"
        git(self.root, "fetch", "--no-tags", "--no-prune", "--no-recurse-submodules",
            "--refmap=", "origin", f"refs/heads/{self.default}:{tracking}")
        self.target = output(git(self.root, "rev-parse", "--verify", tracking + "^{commit}"))
        if self.default_before and self.default_before != self.target:
            ancestor = git(self.root, "merge-base", "--is-ancestor",
                           self.default_before, self.target, check=False)
            if ancestor.returncode:
                raise Blocked("Local default is ahead, diverged, or has insufficient history")
        self.guard(protected)
        self.check_refs(self.default_before)
        if conflicts(self.name, self.local_names()):
            raise Blocked("New branch name became occupied")
        self.phase = "update_default"
        if self.default_before is None:
            if self.default_tracking_missing():
                git(self.root, "remote", "set-branches", "--add", "origin", self.default)
            git(self.root, "switch", "--no-overwrite-ignore", "--no-guess", "--no-track",
                "-c", self.default, self.target)
            self.expected_ref, self.expected_sha = default_ref, self.target
            self.guard(protected)
            git(self.root, "branch", "--set-upstream-to=" + tracking, self.default)
        else:
            if self.expected_ref != default_ref:
                git(self.root, "switch", "--no-overwrite-ignore", "--no-guess", self.default)
                self.expected_ref, self.expected_sha = default_ref, self.default_before
            self.guard(protected)
            self.check_refs(self.default_before)
            self.check_merge_options()
            if self.default_before != self.target:
                git(self.root, "merge", "--ff-only", "--no-autostash",
                    "--no-overwrite-ignore", self.target)
                self.expected_sha = self.target
        self.guard(protected)
        self.check_refs(self.target)
        if conflicts(self.name, self.local_names()):
            raise Blocked("New branch name became occupied")
        self.phase = "create_branch"
        git(self.root, "switch", "--no-overwrite-ignore", "--no-guess", "--no-track",
            "-c", self.name, self.target)
        self.expected_ref, self.expected_sha = f"refs/heads/{self.name}", self.target
        self.phase = "verify"
        self.guard(protected)
        self.check_refs(self.target)
        upstream = output(git(self.root, "for-each-ref", "--format=%(upstream)", self.expected_ref))
        if upstream:
            raise Blocked("The new branch unexpectedly has upstream tracking")
        self.phase = "complete"
        return {"status": "created", "branch": self.name, "default_branch": self.default,
                "fetched_commit": self.target, "head": self.expected_sha,
                "previous_branch": self.previous_ref.removeprefix("refs/heads/"),
                "previous_head": self.previous_sha, "clean": True,
                "default_update": "created" if self.default_before is None else (
                    "unchanged" if self.default_before == self.target else "fast-forwarded")}

    def observed_state(self) -> dict:
        try:
            ref, head = self.identity()
            return {"branch": ref.removeprefix("refs/heads/"), "head": head}
        except (Blocked, OSError):
            return {"branch": None, "head": None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", help="literal new branch name; otherwise generate a unique name")
    arguments = parser.parse_args(argv)
    workflow = None
    try:
        workflow = Workflow(Path.cwd())
        result = workflow.run(arguments.branch)
    except (Blocked, OSError, KeyboardInterrupt) as error:
        result = {"status": "blocked", "reason": str(error) or "Interrupted; inspect current Git state",
                  "phase": workflow.phase if workflow else "preflight",
                  "observed": workflow.observed_state() if workflow else {},
                  "effects_retained": True}
        print(json.dumps(result, ensure_ascii=True))
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
