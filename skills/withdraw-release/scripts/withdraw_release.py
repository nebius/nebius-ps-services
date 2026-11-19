#!/usr/bin/env python3
"""Withdraw one published GitHub release. Python 3.11+, gh; no sibling imports."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

MARKER = "<!-- withdraw-release:"
START = "<!-- withdraw-release:v1 -->\n"
END = "<!-- /withdraw-release:v1 -->\n\n"
PREFIX = "WITHDRAWN — "
DEFAULT_REASON = "This version has been withdrawn by its maintainer."
WARNINGS = {
    "WITHDRAWING": "Asset removal is in progress; downloads may remain available.",
    "WITHDRAWN": "Uploaded release assets have been removed.",
    "WITHDRAWN_DOWNLOADS_REMAIN": (
        "GitHub's immutability protection prevents removing its assets, "
        "so downloads remain available."
    ),
}
SCHEMA = "withdraw-release/checkpoint-v1"


class WithdrawalError(Exception):
    """Public-safe error; never includes raw API responses or command output."""


def require(condition, message):
    if not condition:
        raise WithdrawalError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def command(args, cwd, *, data=None):
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            input=data,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WithdrawalError(
            "Command unavailable or timed out; reconcile before retrying"
        ) from exc


def git(cwd, *args):
    result = command(["git", *args], cwd)
    require(result.returncode == 0, "Cannot inspect selected Git checkout")
    return result.stdout.removesuffix("\n")


def remote_repo(remote):
    if remote.startswith("git@github.com:"):
        repo = remote[len("git@github.com:") :]
    else:
        u = urlsplit(remote)
        require(
            u.hostname == "github.com"
            and u.scheme in {"https", "ssh"}
            and not u.password
            and not u.query
            and not u.fragment
            and not u.port
            and (not u.username or (u.scheme == "ssh" and u.username == "git")),
            "Use a credential-free github.com origin or explicit --repo OWNER/REPO",
        )
        repo = u.path.lstrip("/")
    return valid_repo(repo.removesuffix(".git"))


def valid_repo(repo):
    require(
        bool(
            re.fullmatch(
                r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*", repo
            )
        ),
        "Expected --repo OWNER/REPO on github.com",
    )
    return repo


def scope(project, repo):
    shaping = {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS",
    }
    require(
        not shaping.intersection(os.environ),
        "Unset repository-shaping Git variables before withdrawal",
    )
    project = project.resolve(strict=True)
    root = Path(git(project, "rev-parse", "--show-toplevel")).resolve()
    project.relative_to(root)
    fetch = git(root, "remote", "get-url", "--all", "origin").splitlines()
    push = git(root, "remote", "get-url", "--push", "--all", "origin").splitlines()
    require(
        len(fetch) == len(push) == 1 and remote_repo(fetch[0]) == remote_repo(push[0]),
        "Origin fetch and push must identify the same GitHub repository",
    )
    origin = remote_repo(fetch[0])
    return root, project, valid_repo(repo) if repo else origin, origin


def workflow_prefix(root, project):
    """Only literal top-level env metadata; never guess from a folder/suffix."""
    candidates = set()
    for path in (root / ".github/workflows").glob("*.y*ml"):
        if path.is_symlink() or path.stat().st_size > 256_000:
            continue
        text = path.read_text()
        env_key = r"(?:env|'env'|\"env\")"
        headers = re.findall(rf"(?m)^{env_key}:", text)
        blocks = re.findall(rf"(?m)^{env_key}:[ \t]*\n((?:[ \t].*\n|\n)+)", text)
        for block in blocks:

            def values(key):
                return re.findall(
                    rf"(?m)^  (?:{key}|'{key}'|\"{key}\"):[ \t]*(.*)$", block
                )

            def literal(value):
                match = re.fullmatch(r"(['\"]?)([\w./-]+)\1", value.strip())
                return match[2] if match else None

            apps, prefixes = values("APP_DIR"), values("TAG_PREFIX")
            if not any(
                app and (root / app).resolve() == project for app in map(literal, apps)
            ):
                continue
            require(
                len(headers) == 1 and len(apps) == 1 and len(prefixes) == 1,
                "Ambiguous workflow metadata; supply the exact release tag",
            )
            prefix = literal(prefixes[0])
            require(
                prefix is not None,
                "Nonliteral workflow prefix; supply the exact release tag",
            )
            candidates.add(prefix)
    require(len(candidates) == 1, "Version is ambiguous; supply the exact release tag")
    return candidates.pop()


class GitHub:
    def __init__(self, repo, cwd):
        self.repo, self.cwd = repo, cwd

    def api(self, route="", method="GET", payload=None, *, absent=False):
        endpoint = f"repos/{self.repo}" + (f"/{route}" if route else "")
        if method == "GET":
            endpoint += (
                ("&" if "?" in endpoint else "?") + "_withdraw=" + uuid.uuid4().hex
            )
        args = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--include",
            "--method",
            method,
            "-H",
            "X-GitHub-Api-Version: 2026-03-10",
            "-H",
            "Cache-Control: no-cache",
            endpoint,
        ]
        if payload is not None:
            args += ["--input", "-"]
        result = command(
            args, self.cwd, data=json.dumps(payload) if payload is not None else None
        )
        match = re.match(r"HTTP/\S+ (\d+) [^\n]*\n", result.stdout)
        require(
            match is not None, "GitHub response status unavailable; progress retained"
        )
        status = int(match[1])
        if status == 404 and absent:
            return None
        require(
            result.returncode == 0 and 200 <= status < 300,
            f"GitHub {method} failed (HTTP {status}); inspect access and retry this invocation",
        )
        body = re.split(r"\r?\n\r?\n", result.stdout, maxsplit=1)
        require(len(body) == 2, "Malformed GitHub response")
        try:
            return json.loads(body[1]) if body[1].strip() else None
        except ValueError as exc:
            raise WithdrawalError("Malformed GitHub JSON") from exc

    def pages(self, route):
        values = []
        for page in range(1, 101):
            batch = self.api(f"{route}?per_page=100&page={page}")
            require(isinstance(batch, list), "Malformed GitHub listing")
            values.extend(batch)
            if len(batch) < 100:
                return values
        raise WithdrawalError("Listing limit exceeded; no complete inventory")

    def tag(self, name):
        ref = self.api(f"git/ref/tags/{quote(name, safe='')}")["object"]
        current, seen = ref, set()
        for _ in range(16):
            require(current["sha"] not in seen, "Cyclic tag identity")
            seen.add(current["sha"])
            if current["type"] == "commit":
                return {"object": ref["sha"], "commit": current["sha"]}
            require(current["type"] == "tag", "Tag does not identify a commit")
            current = self.api(f"git/tags/{current['sha']}")["object"]
        raise WithdrawalError("Tag nesting limit exceeded")


def resolve_release(api, requested, root, project, origin):
    require(
        requested and len(requested) <= 255 and not any(ord(c) < 32 for c in requested),
        "Invalid release selector",
    )
    releases = api.pages("releases")
    matches = [r for r in releases if r["tag_name"] == requested]
    if not matches and re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][\w.-]+)?", requested):
        require(
            api.repo.lower() == origin.lower(),
            "Cross-repository version inference is unavailable; use an exact tag",
        )
        tag = f"{workflow_prefix(root, project)}-v{requested.removeprefix('v')}"
        matches = [r for r in releases if r["tag_name"] == tag]
    require(
        len(matches) == 1,
        "Release not found or ambiguous; supply an exact published tag",
    )
    require(not matches[0]["draft"], "Draft releases are outside withdrawal scope")
    return matches[0]


def metadata(release):
    return {k: release.get(k) for k in ("name", "body", "prerelease", "draft")}


def inventory(api, release_id):
    rows = api.pages(f"releases/{release_id}/assets")
    result = {}
    for a in rows:
        require(
            type(a.get("id")) is int and a["id"] > 0 and str(a["id"]) not in result,
            "Invalid or duplicate asset identity",
        )
        require(
            a.get("state") == "uploaded",
            "An asset is still uploading; settle publication first",
        )
        result[str(a["id"])] = {
            k: a.get(k)
            for k in ("id", "name", "size", "digest", "created_at", "updated_at")
        }
    return result


def parse_notice(body):
    body = body or ""
    if MARKER not in body:
        return body, None, None
    require(
        body.startswith(START) and body.count(MARKER) == 1 and body.count(END) == 1,
        "Unknown or conflicting withdrawal marker; inspect release notes",
    )
    block, original = body[len(START) :].split(END, 1)
    for state, warning in WARNINGS.items():
        lead = f"> **WITHDRAWN: Do not install this version.**\n> {warning}\n\nReason: "
        if block.startswith(lead) and block.endswith("\n\n"):
            reason = block[len(lead) : -2]
            validate_reason(reason)
            return original, state, reason
    raise WithdrawalError("Malformed withdrawal notice; inspect release notes")


def validate_reason(reason):
    require(
        0 < len(reason) <= 500
        and not any(ord(c) < 32 for c in reason)
        and "<!--" not in reason
        and "-->" not in reason,
        "Reason must be one public-safe line of 1–500 characters without HTML comments",
    )


def notice(original, reason, state):
    return (
        START
        + "> **WITHDRAWN: Do not install this version.**\n> "
        + WARNINGS[state]
        + "\n\nReason: "
        + reason
        + "\n\n"
        + END
        + original
    )


def private_root():
    if os.environ.get("SKILLS_AGENT") == "claude" or os.environ.get(
        "CLAUDE_CONFIG_DIR"
    ):
        home = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
    else:
        home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    return home / "withdraw-release"


class Store:
    """Hashes and minimal identity only; a checkpoint is never authorization."""

    def __init__(self, root, repo, tag):
        root = root.absolute()
        for path in reversed((root, *root.parents)):
            require(not path.is_symlink(), "Private state cannot traverse symlinks")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = root.stat()
        require(
            info.st_uid == os.getuid() and not info.st_mode & 0o077,
            "Private state root must be owned by this user and mode 0700",
        )
        self.directory = root
        self.key = digest(["github.com", repo.lower(), tag])
        self.dirfd = None
        self.inode = (info.st_dev, info.st_ino)

    def check_directory(self):
        for path in (self.directory, *self.directory.parents):
            require(not path.is_symlink(), "Private state directory changed")
        info = self.directory.lstat()
        require(
            (info.st_dev, info.st_ino) == self.inode
            and info.st_uid == os.getuid()
            and not info.st_mode & 0o077,
            "Private state directory changed",
        )

    def open(self, suffix, flags):
        self.check_directory()
        fd = os.open(
            self.key + suffix,
            flags | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=self.dirfd,
        )
        info = os.fstat(fd)
        if not (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.getuid()
            and not info.st_mode & 0o077
            and info.st_nlink == 1
        ):
            os.close(fd)
            raise WithdrawalError("Unsafe checkpoint or lock file")
        return fd

    @contextlib.contextmanager
    def locked(self):
        self.check_directory()
        self.dirfd = os.open(
            self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        info = os.fstat(self.dirfd)
        try:
            require(
                (info.st_dev, info.st_ino) == self.inode,
                "Private state directory changed",
            )
            fd = self.open(".lock", os.O_CREAT | os.O_RDWR)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise WithdrawalError(
                        "Another local withdrawal is active for this release"
                    ) from exc
                yield
            finally:
                os.close(fd)
        finally:
            os.close(self.dirfd)
            self.dirfd = None

    def load(self):
        try:
            with os.fdopen(self.open(".json", os.O_RDONLY)) as stream:
                value = json.load(stream)
            require(value.get("schema") == SCHEMA, "Unsupported checkpoint schema")
            return value
        except FileNotFoundError:
            return None
        except ValueError as exc:
            raise WithdrawalError(
                "Malformed checkpoint; inspect private progress"
            ) from exc

    def save(self, value):
        self.check_directory()
        name = ".withdraw-" + uuid.uuid4().hex
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self.dirfd,
        )
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(value, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            self.check_directory()
            os.replace(
                name, self.key + ".json", src_dir_fd=self.dirfd, dst_dir_fd=self.dirfd
            )
            os.fsync(self.dirfd)
        finally:
            try:
                os.unlink(name, dir_fd=self.dirfd)
            except FileNotFoundError:
                pass


def anonymous_missing(repo, tag, name):
    # Construct only a github.com URL; never forward credentials to an asset URL.
    url = f"https://github.com/{repo}/releases/download/{quote(tag, safe='')}/{quote(name, safe='')}"
    req = Request(
        url + "?_withdraw=" + uuid.uuid4().hex,
        method="HEAD",
        headers={"Cache-Control": "no-cache"},
    )
    try:
        with urlopen(req, timeout=20):
            return False
    except HTTPError as exc:
        return exc.code in {404, 410}
    except (URLError, TimeoutError):
        return False


def identity(api, repo, release):
    require(type(release.get("immutable")) is bool, "Release immutability is unknown")
    require(not release["draft"], "Release became a draft")
    return {
        "repo_id": repo["id"],
        "release_id": release["id"],
        "tag": release["tag_name"],
        "immutable": release["immutable"],
        "tag_identity": api.tag(release["tag_name"]),
    }


def withdraw(
    api,
    selected,
    *,
    reason=None,
    dry_run=False,
    state_root=None,
    probe=anonymous_missing,
    sleep=time.sleep,
):
    repo = api.api()
    require(
        repo.get("permissions", {}).get("push") is True,
        "Repository write access is unavailable; no withdrawal effects",
    )
    release_id = selected["id"]
    release = api.api(f"releases/{release_id}")
    require(release["tag_name"] == selected["tag_name"], "Release identity changed")
    frozen = identity(api, repo, release)
    assets = inventory(api, release_id)
    original, old_state, old_reason = parse_notice(release.get("body"))
    reason = reason if reason is not None else old_reason or DEFAULT_REASON
    validate_reason(reason)
    require(
        old_reason is None or reason == old_reason,
        "Existing withdrawal reason differs; reconcile notes before changing scope",
    )
    title = release.get("name") or release["tag_name"]
    title = title if title.startswith(PREFIX) else PREFIX + title
    outcome = "WITHDRAWN_DOWNLOADS_REMAIN" if frozen["immutable"] else "WITHDRAWN"
    result = {
        "status": "DRY_RUN" if dry_run else outcome,
        "release_id": release_id,
        "tag": frozen["tag"],
        "immutable": frozen["immutable"],
        "asset_count": len(assets),
        "intended_outcome": outcome,
        "release_url": f"https://github.com/{api.repo}/releases/tag/{quote(frozen['tag'], safe='')}",
    }
    if dry_run:
        return result
    store = Store(
        state_root if state_root is not None else private_root(),
        api.repo,
        release["tag_name"],
    )
    with store.locked():
        value = store.load()
        if value is None:
            value = {
                "schema": SCHEMA,
                "identity": frozen,
                "assets": assets,
                "allowed_metadata": [digest(metadata(release))],
                "reason": digest(reason),
                "removed": [],
                "pending_delete": None,
                "complete": False,
            }
            store.save(value)
        require(
            value["identity"] == frozen and value["reason"] == digest(reason),
            "Frozen release identity or withdrawal reason changed",
        )

        def observe():
            store.check_directory()
            current_repo = api.api()
            require(
                current_repo["id"] == repo["id"]
                and current_repo.get("permissions", {}).get("push") is True,
                "Repository identity or write access changed",
            )
            current = api.api(f"releases/{release_id}")
            require(
                identity(api, current_repo, current) == frozen,
                "Release tag or protection changed",
            )
            require(
                digest(metadata(current)) in value["allowed_metadata"],
                "Concurrent release metadata edit detected; inspect before retrying",
            )
            current_assets = inventory(api, release_id)
            require(
                all(
                    k in value["assets"]
                    and v == value["assets"][k]
                    and k not in value["removed"]
                    for k, v in current_assets.items()
                ),
                "Unexpected added, replaced or restored asset; stop competing publication",
            )
            missing = set(value["assets"]) - set(current_assets)
            permitted = set(value["removed"]) | {value["pending_delete"]}
            require(missing <= permitted, "Unexpected asset removal detected")
            if frozen["immutable"]:
                require(
                    current_assets == value["assets"], "Immutable inventory changed"
                )
            return current, current_assets

        def patch(state):
            current, _ = observe()
            payload = {
                "name": title,
                "body": notice(original, reason, state),
                "prerelease": True,
                "make_latest": "false",
            }
            desired = digest(
                {
                    **metadata(current),
                    **{k: v for k, v in payload.items() if k != "make_latest"},
                }
            )
            if desired == digest(metadata(current)):
                value["allowed_metadata"] = [desired]
                store.save(value)
                return
            if desired not in value["allowed_metadata"]:
                value["allowed_metadata"].append(desired)
            store.save(
                value
            )  # Persist intended fingerprint before uncertain network effects.
            try:
                api.api(f"releases/{release_id}", "PATCH", payload)
            except WithdrawalError:
                current, _ = observe()
                require(
                    digest(metadata(current)) == desired,
                    "Metadata update not confirmed; progress retained, repeat invocation",
                )
            current, _ = observe()
            require(
                digest(metadata(current)) == desired, "Metadata postcondition failed"
            )
            value["allowed_metadata"] = [desired]
            store.save(value)

        _, remaining = observe()
        pending = value["pending_delete"]
        if pending is not None and pending not in remaining:
            if pending not in value["removed"]:
                value["removed"].append(pending)
            value["pending_delete"] = None
            store.save(value)
        if not frozen["immutable"]:
            if assets or old_state not in {"WITHDRAWN"}:
                patch("WITHDRAWING")
            # JSON key ordering is not deletion order. Reconcile the one
            # uncertain request before another asset can replace its identity.
            pending = value["pending_delete"]
            ordered_assets = ([pending] if pending is not None else []) + [
                asset_id for asset_id in value["assets"] if asset_id != pending
            ]
            for asset_id in ordered_assets:
                _, remaining = observe()
                if asset_id not in remaining:
                    if asset_id not in value["removed"]:
                        value["removed"].append(asset_id)
                    if value["pending_delete"] == asset_id:
                        value["pending_delete"] = None
                    store.save(value)
                    continue
                value["pending_delete"] = asset_id
                store.save(value)
                try:
                    api.api(f"releases/assets/{asset_id}", "DELETE")
                except WithdrawalError:
                    # Lost response or denied write: independent inventory decides, never blind retry.
                    pass
                _, remaining = observe()
                require(
                    asset_id not in remaining,
                    "Asset removal not confirmed; warning retained, repeat invocation",
                )
                value["removed"].append(asset_id)
                value["pending_delete"] = None
                store.save(value)
        patch(outcome)
        for attempt in range(4):
            current, remaining = observe()
            latest = api.api("releases/latest", absent=True)
            excluded = latest is None or latest["id"] != release_id
            missing = True
            if not frozen["immutable"]:
                require(not remaining, "Assets remain after withdrawal")
                for a in value["assets"].values():
                    if api.api(f"releases/assets/{a['id']}", absent=True) is not None:
                        missing = False
                    if not repo["private"] and not probe(
                        api.repo, frozen["tag"], a["name"]
                    ):
                        missing = False
            if excluded and missing:
                # Verify authority still readable: private 404 alone is not absence evidence.
                observe()
                value["complete"] = True
                store.save(value)
                result["asset_count"] = len(remaining)
                return result
            if attempt < 3:
                sleep(2)
        raise WithdrawalError(
            "Withdrawal metadata applied; latest/download verification pending, repeat invocation"
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release", required=True, help="Version or exact published release tag"
    )
    parser.add_argument(
        "--repo", help="github.com OWNER/REPO; defaults to verified origin"
    )
    parser.add_argument(
        "--project-dir", type=Path, default=Path.cwd(), help="Selected checkout/project"
    )
    parser.add_argument("--reason", help="One public-safe line explaining withdrawal")
    parser.add_argument(
        "--dry-run", action="store_true", help="Read-only plan; no checkpoint or writes"
    )
    args = parser.parse_args(argv)
    try:
        root, project, repo, origin = scope(args.project_dir, args.repo)
        api = GitHub(repo, root)
        selected = resolve_release(api, args.release, root, project, origin)
        result = withdraw(api, selected, reason=args.reason, dry_run=args.dry_run)
    except (WithdrawalError, OSError, ValueError, KeyError, TypeError) as exc:
        message = (
            str(exc)
            if isinstance(exc, WithdrawalError)
            else "Invalid local or remote evidence; inspect target and private progress"
        )
        print(json.dumps({"status": "INCOMPLETE", "reason": message}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
