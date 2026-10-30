"""Trusted PR destination intake and non-force Git transport primitives.

The transaction owner supplies its lock-aware Git runner and journals effects.
This module never commits, changes origin, creates a PR or grants authority.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile


class PublicationError(Exception):
    """Sanitized publication failure; remote output is never an error message."""


class PushRejected(PublicationError):
    """Trusted pre-push witness proves no ref update was submitted."""


def require(value, message):
    if not value:
        raise PublicationError(message)


def api(path):
    try:
        result = subprocess.run(
            ["gh", "api", "--hostname", "github.com", path],
            capture_output=True, timeout=30, check=False,
        )
        require(result.returncode == 0 and len(result.stdout) <= 2 * 1024 * 1024,
                "GitHub identity or permission read unavailable")
        value = json.loads(result.stdout)
        require(isinstance(value, dict), "Invalid GitHub identity response")
        return value
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise PublicationError("GitHub identity or permission read unavailable") from error


def repository_url(name):
    require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name),
            "Invalid GitHub repository")
    return f"https://github.com/{name}.git"


def _text(root, git, *args):
    try:
        result = git(root, args, check=False)
    except (OSError, RuntimeError) as error:
        raise PublicationError("Git publication identity read unavailable") from error
    require(result.returncode == 0, "Git publication identity read failed")
    return result.stdout.decode().strip()


def resolved_transport(root, git, url):
    rules = git(root, ("config", "--null", "--get-regexp", r"^url\..*\.pushinsteadof$"), check=False)
    require(rules.returncode in (0, 1) and len(rules.stdout) <= 2 * 1024 * 1024,
            "Git push URL configuration unavailable")

    def resolve(value, push=False):
        if push:
            matches = []
            for entry in rules.stdout.decode().split("\0"):
                if not entry:
                    continue
                key, separator, prefix = entry.partition("\n")
                require(separator and key.startswith("url.") and key.endswith(".pushinsteadof"),
                        "Invalid Git push URL configuration")
                if value.startswith(prefix):
                    matches.append((len(prefix), key[4:-len(".pushinsteadof")] + value[len(prefix):]))
            if matches:
                length = max(item[0] for item in matches)
                destinations = {item[1] for item in matches if item[0] == length}
                require(len(destinations) == 1, "Ambiguous Git push URL configuration")
                value = destinations.pop()
        return _text(root, git, "ls-remote", "--get-url", value)

    values = {"fetch": resolve(url), "push": resolve(url, push=True)}
    for direction, value in values.items():
        require(resolve(value, push=direction == "push") == value,
                "PR transport redirects outside its repository or is unstable")
    return values


def transport(root, git, repository):
    values = resolved_transport(root, git, repository_url(repository))
    allowed = {repository_url(repository), f"git@github.com:{repository}.git"}
    require(all(value in allowed for value in values.values()), "PR transport redirects outside its repository")
    return values


def origin_destination(root, git):
    fetch = _text(root, git, "remote", "get-url", "--all", "origin")
    push = _text(root, git, "remote", "get-url", "--push", "--all", "origin")
    require(len(fetch.splitlines()) == 1 and len(push.splitlines()) == 1,
            "PR publication requires one fetch and one push destination")

    def repository(value):
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", value)
        return match[1].removesuffix(".git").lower() if match else value

    require(repository(fetch) == repository(push), "Origin fetch and push repositories differ")
    require(resolved_transport(root, git, fetch)["fetch"] == fetch
            and resolved_transport(root, git, push) == {"fetch": push, "push": push},
            "Origin destination resolution is unstable")
    return push


def identity(pr):
    require(isinstance(pr.get("head"), dict) and isinstance(pr.get("base"), dict),
            "PR identity unavailable")
    head, base = pr["head"], pr["base"]
    require(isinstance(head.get("repo"), dict) and isinstance(base.get("repo"), dict),
            "PR repository unavailable")
    result = {
        "repository": base["repo"].get("full_name"),
        "base_repository_id": base["repo"].get("id"),
        "number": pr.get("number"),
        "base_ref": base.get("ref"),
        "head_repository": head["repo"].get("full_name"),
        "head_repository_id": head["repo"].get("id"),
        "head_ref": head.get("ref"),
    }
    for field in ("base_repository_id", "head_repository_id", "number"):
        require(type(result[field]) is int and result[field] > 0, "Invalid PR numeric identity")
    repository_url(result["repository"])
    repository_url(result["head_repository"])
    for field in ("base_ref", "head_ref"):
        require(isinstance(result[field], str) and 0 < len(result[field]) <= 255,
                "Invalid PR branch identity")
    return result


def validate_binding(binding, root, git):
    keys = {"repository", "base_repository_id", "number", "base_ref", "head_repository",
            "head_repository_id", "head_ref", "fetch", "push"}
    require(isinstance(binding, dict) and set(binding) == keys, "Invalid PR target binding")
    for field in ("base_repository_id", "head_repository_id", "number"):
        require(type(binding[field]) is int and binding[field] > 0, "Invalid PR numeric identity")
    for field in ("repository", "head_repository"):
        repository_url(binding[field])
    for field in ("base_ref", "head_ref"):
        require(isinstance(binding[field], str) and not git(root, ("check-ref-format", "refs/heads/" + binding[field]), check=False).returncode,
                "Invalid PR branch identity")
    for direction in ("fetch", "push"):
        require(binding[direction] in {repository_url(binding["head_repository"]), f"git@github.com:{binding['head_repository']}.git"},
                "Invalid PR transport identity")


def snapshot(binding, root, git, *, writable=False):
    validate_binding(binding, root, git)
    pr = api(f"repos/{binding['repository']}/pulls/{binding['number']}")
    require(identity(pr) == {k: binding[k] for k in identity(pr)}, "PR identity changed")
    require(transport(root, git, binding["head_repository"]) == {k: binding[k] for k in ("fetch", "push")},
            "PR transport changed")
    base = api(f"repos/{binding['repository']}")
    require(base.get("id") == binding["base_repository_id"],
            "PR base repository identity changed")
    if writable:
        require(pr.get("state") == "open" and pr.get("merged") is not True, "PR is not open")
        head = api(f"repos/{binding['head_repository']}")
        require(head.get("id") == binding["head_repository_id"], "PR head repository changed")
        direct = head.get("permissions", {}).get("push") is True
        maintainer = pr.get("maintainer_can_modify") is True and base.get("permissions", {}).get("push") is True
        require(direct or maintainer, "Contributor branch updates are not permitted")
    head_sha = pr["head"].get("sha")
    require(isinstance(head_sha, str) and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", head_sha), "Invalid PR head")
    return head_sha


def intake(root, git, selector, base_ref):
    require(isinstance(selector, dict) and set(selector) == {"repository", "number"}, "Invalid PR selector")
    repository_url(selector["repository"])
    require(type(selector["number"]) is int and selector["number"] > 0, "Invalid PR number")
    pr = api(f"repos/{selector['repository']}/pulls/{selector['number']}")
    require(pr.get("state") == "open" and pr.get("merged") is not True, "PR is not open")
    binding = identity(pr)
    require(binding["repository"] == selector["repository"] and binding["number"] == selector["number"]
            and binding["base_ref"] == base_ref, "PR selector or base mismatch")
    origin = _text(root, git, "remote", "get-url", "--all", "origin")
    require(origin in {repository_url(binding["repository"]), f"git@github.com:{binding['repository']}.git"},
            "Origin is not the PR base repository")
    binding.update(transport(root, git, binding["head_repository"]))
    head = snapshot(binding, root, git)
    # Object-only fetch: no local ref or shared FETCH_HEAD is used as authority.
    result = git(root, ("fetch", "--no-tags", "--no-write-fetch-head", binding["fetch"], head), check=False)
    require(result.returncode == 0 and _text(root, git, "rev-parse", f"{head}^{{commit}}") == head,
            "Exact contributor commit is unavailable")
    require(remote_head(root, git, binding["fetch"], "refs/heads/" + binding["head_ref"]) == head,
            "Contributor head changed during intake")
    return binding, head


def remote_head(root, git, destination, ref):
    output = _text(root, git, "ls-remote", "--refs", destination, ref)
    if not output:
        return None
    lines = output.splitlines()
    require(len(lines) == 1, "Ambiguous remote branch")
    parts = lines[0].split("\t")
    require(len(parts) == 2 and parts[1] == ref and re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", parts[0]),
            "Invalid remote branch evidence")
    return parts[0]


def push(root, git, destination, ref, expected, head):
    """Compare the server advertisement in pre-push, without force semantics."""
    with tempfile.TemporaryDirectory(prefix="skills-pr-push-") as directory:
        hooks = Path(directory)
        hook = hooks / "pre-push"
        witness = hooks / "rejected.json"
        program = f'''import sys
from pathlib import Path
rows = [line.split() for line in sys.stdin]
expected = {expected!r}
valid = (len(rows) == 1 and len(rows[0]) == 4
         and rows[0][1] == {head!r} and rows[0][2] == {ref!r}
         and rows[0][3] == (expected or "0" * len(rows[0][1])))
if not valid:
    Path({str(witness)!r}).write_text("rejected-before-submission")
sys.exit(0 if valid else 1)
'''
        guard = hooks / "guard.py"
        guard.write_text(program)
        hook.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(guard))}\n")
        hook.chmod(0o700)
        # The fixed trusted hook replaces candidate-controlled push hooks only.
        # Commit validation continues through the normal transaction hook path.
        result = git(root, ("-c", f"core.hooksPath={hooks}", "-c", "push.followTags=false",
                           "push", "--porcelain", "--no-force", "--verify",
                           "--recurse-submodules=no", destination, f"{head}:{ref}"), check=False)
        if result.returncode != 0 and witness.is_file() and witness.read_text() == "rejected-before-submission":
            raise PushRejected("Contributor branch changed before submission")
        return result.returncode == 0
