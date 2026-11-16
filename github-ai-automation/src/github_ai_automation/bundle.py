"""Copy a reviewed, explicit skill closure without registering personal hooks."""

import hashlib
import json
import shutil
from pathlib import Path

from .contracts import Blocked, digest


def package_skills(repository: Path, manifest: Path, destination: Path) -> str:
    spec = json.loads(manifest.read_text())
    if (
        set(spec) != {"schema_version", "skills", "hooks", "requires"}
        or spec["schema_version"] != 1
        or spec["hooks"]
    ):
        raise Blocked("SKILL_BUNDLE_INVALID")
    names = set(spec["skills"])
    if len(names) != len(spec["skills"]) or any(
        owner not in names or not set(dependencies) <= names
        for owner, dependencies in spec["requires"].items()
    ):
        raise Blocked("SKILL_DEPENDENCY_MISSING")
    if destination.exists():
        raise Blocked("SKILL_DESTINATION_EXISTS")
    destination.mkdir(mode=0o755, parents=True)
    hashes = {}
    for name in spec["skills"]:
        if not name or "/" in name or name.startswith("."):
            raise Blocked("SKILL_NAME_INVALID")
        root = repository / "skills" / name
        if root.is_symlink() or not (root / "SKILL.md").is_file():
            raise Blocked("SKILL_DEPENDENCY_MISSING")
        for path in sorted(root.rglob("*")):
            if any(
                p in {"__pycache__", ".venv", ".git", ".pytest_cache", ".ruff_cache", "hooks"}
                for p in path.parts
            ):
                continue
            if path.name in {"hooks.json", "hooks.json.template"}:
                continue
            if path.is_symlink():
                raise Blocked("SKILL_SYMLINK_REJECTED")
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            target = destination / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
            target.chmod(mode)
            hashes[f"{name}/{relative}"] = {
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "mode": mode,
            }
    return digest(hashes)
