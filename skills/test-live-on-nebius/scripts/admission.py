"""Bind reviewed helpers and the interpreter environment to one run."""

import hashlib
import importlib.metadata
import os
import platform
import sys
from pathlib import Path

from live_state import StateError, safe_path


def file_digest(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise StateError("DEPENDENCY_CHANGED")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def runtime_identity():
    executable = Path(sys.executable).resolve()
    return {
        "executable": str(executable),
        "executable_sha256": file_digest(executable),
        "python": platform.python_version(),
        "packages": sorted(
            [d.metadata["Name"], d.version] for d in importlib.metadata.distributions()
        ),
    }


def bind_dependencies(plan):
    """Host-declared imports augment, never replace, the mandatory helper set."""
    scripts = Path(__file__).resolve().parent
    skills = scripts.parents[1]
    paths = set(scripts.glob("*.py"))
    paths.update((skills / "nebius/assets/sdk").rglob("*.py"))
    paths.add(skills / "nebius/scripts/inspect_quotas.py")
    paths.update(
        skills / "global-context-management/scripts" / name
        for name in ("agent_runtime.py", "git_environment.py")
    )
    paths.add(Path(plan["adapter"]))
    declared = plan.get("dependency_sha256", {})
    if not isinstance(declared, dict):
        raise StateError("DEPENDENCY_MANIFEST")
    for path, expected in declared.items():
        if not Path(path).is_absolute() or file_digest(path) != expected:
            raise StateError("DEPENDENCY_CHANGED")
        paths.add(Path(path))
    plan["dependency_sha256"] = {str(p): file_digest(p) for p in sorted(paths)}
    plan["runtime_identity"] = runtime_identity()


def verify_dependencies(plan):
    manifest = plan.get("dependency_sha256")
    if not isinstance(manifest, dict) or not manifest:
        raise StateError("DEPENDENCY_MANIFEST_REQUIRED")
    for path, expected in manifest.items():
        if not Path(path).is_absolute() or file_digest(path) != expected:
            raise StateError("DEPENDENCY_CHANGED")
    if plan.get("runtime_identity") != runtime_identity():
        raise StateError("RUNTIME_CHANGED")


def retain_dependencies(journal):
    """Keep exact bytes for explicit recovery; never restore shared files implicitly."""
    directory = journal.directory / "admitted-files"
    directory.mkdir(mode=0o700, exist_ok=True)
    safe_path(directory, directory=True)
    for path, expected in journal.data["plan"]["dependency_sha256"].items():
        source = Path(path).read_bytes()
        if hashlib.sha256(source).hexdigest() != expected:
            raise StateError("DEPENDENCY_CHANGED")
        target = directory / expected
        if target.exists():
            safe_path(target)
            if file_digest(target) != expected:
                raise StateError("SNAPSHOT_CHANGED")
        else:
            with target.open("xb") as stream:
                target.chmod(0o600)
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
