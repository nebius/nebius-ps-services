"""Release selection, notes and immutable asset admission shared by all clients."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import re
from pathlib import Path

from .contracts import Blocked, Fragment, canonical


def next_version(previous: str, fragments: list[Fragment]) -> str:
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", previous)
    if not match or not fragments:
        raise Blocked("RELEASE_VERSION_UNRESOLVED")
    major, minor, patch = map(int, match.groups())
    bumps = {f.bump for f in fragments}
    if "major" in bumps:
        return f"{major + 1}.0.0"
    if "minor" in bumps:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def affected_projects(paths: list[str], registry: dict) -> list[str]:
    return [
        name
        for name, entry in registry["projects"].items()
        if any(
            any(fnmatch.fnmatchcase(path, p) for p in entry["include"])
            and not any(fnmatch.fnmatchcase(path, p) for p in entry["exclude"])
            for path in paths
        )
    ]


def fragments_at(root: Path, paths: list[str], project: str) -> list[Fragment]:
    fragments = []
    for name in sorted(paths):
        if not name.startswith("release-notes/") or not name.endswith(".json"):
            continue
        path = root / name
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root.resolve())
            or path.stat().st_size > 8192
        ):
            raise Blocked("RELEASE_FRAGMENT_INVALID")
        fragment = Fragment.model_validate(json.loads(path.read_text()))
        if fragment.project == project:
            fragments.append(fragment)
    if not fragments:
        raise Blocked("RELEASE_FRAGMENT_MISSING")
    return fragments


def missing_fragment_patch(root: Path, paths: list[str], registry: dict, review, pr_number: int):
    """Prepare metadata in the original PR; never invent a compatibility bump."""
    affected = affected_projects(paths, registry)
    proposed = {f.project: f for f in review.release_fragments}
    if len(proposed) != len(review.release_fragments) or set(proposed) - set(affected):
        raise Blocked("RELEASE_FRAGMENT_SCOPE_INVALID")
    patch = []
    for project in affected:
        try:
            fragments = fragments_at(root, paths, project)
        except Blocked as exc:
            if exc.code != "RELEASE_FRAGMENT_MISSING":
                raise
            fragment = proposed.get(project)
            if not fragment:
                raise Blocked("RELEASE_BUMP_UNRESOLVED") from None
            if review.compatibility == "breaking" and fragment.bump != "major":
                raise Blocked("BREAKING_CHANGE_REQUIRES_MAJOR") from None
            name = f"release-notes/pr-{pr_number}-{project}.json"
            if (root / name).exists():
                raise Blocked("RELEASE_FRAGMENT_MUST_BE_IMMUTABLE") from None
            patch.append(
                {
                    "path": name,
                    "mode": "100644",
                    "content": base64.b64encode(canonical(fragment)).decode(),
                }
            )
        else:
            if review.compatibility == "breaking" and not any(f.bump == "major" for f in fragments):
                raise Blocked("BREAKING_CHANGE_REQUIRES_MAJOR")
    return patch


def asset_manifest(directory: Path, patterns: list[str]) -> list[dict]:
    files = sorted(directory.iterdir())
    if not files or any(
        p.is_symlink() or not p.is_file() or not re.fullmatch(r"[A-Za-z0-9_.+-]+", p.name)
        for p in files
    ):
        raise Blocked("RELEASE_ASSET_INVALID")
    if any(not any(fnmatch.fnmatchcase(p.name, pattern) for pattern in patterns) for p in files):
        raise Blocked("RELEASE_ASSET_UNEXPECTED")
    if any(not any(fnmatch.fnmatchcase(p.name, pattern) for p in files) for pattern in patterns):
        raise Blocked("RELEASE_ASSET_MISSING")
    return [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
        for p in files
    ]


def verify_assets(expected: list[dict], observed: list[dict]):
    if not expected or canonical(sorted(expected, key=lambda a: a["name"])) != canonical(
        sorted(observed, key=lambda a: a["name"])
    ):
        raise Blocked("RELEASE_ASSET_MISMATCH")
