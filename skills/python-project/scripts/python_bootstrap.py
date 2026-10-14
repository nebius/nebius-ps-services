"""Project-owned Python setup after the offline scaffold executor completes."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import tomllib

# The CLI owns this module's import path; direct tests load the sibling explicitly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from python_project import PythonProjectError, digest, read_bytes, safe_path


def scaffold_module():
    source = (
        Path(__file__).resolve().parents[2]
        / "scaffold-project/scripts/scaffold_project.py"
    )
    spec = importlib.util.spec_from_file_location("python_scaffold_executor", source)
    if spec is None or spec.loader is None:
        raise PythonProjectError("scaffold-project dependency is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def environment(project: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONHOME",
        "UV_PROJECT_ENVIRONMENT",
        "UV_WORKING_DIRECTORY",
        "UV_WORKING_DIR",
        "UV_PROJECT",
        "UV_NO_PROJECT",
        "UV_FROZEN",
        "UV_NO_SYNC",
        "UV_PYTHON",
        "UV_SYSTEM_PYTHON",
        "UV_ENV_FILE",
        "PYTEST_ADDOPTS",
    ):
        env.pop(key, None)
    env["UV_PROJECT_ENVIRONMENT"] = str(project / ".venv")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def reject_parent_workspace(project: Path) -> None:
    """Core standalone resolution cannot own an ancestor workspace's lock."""
    for parent in project.parents:
        metadata_path = safe_path(parent / "pyproject.toml")
        if metadata_path.exists():
            metadata = tomllib.loads(read_bytes(metadata_path).decode())
            if "workspace" in metadata.get("tool", {}).get("uv", {}):
                raise PythonProjectError(
                    "parent uv workspace is unsupported by core bootstrap; "
                    "use an explicitly designed workspace integration"
                )


def check_generated_outputs(project: Path, inputs: dict) -> None:
    """Build/test tools may write only ordinary files in their known outputs."""
    files = ["uv.lock", f"src/{inputs['package_name']}/_version.py"]
    directories = [
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
        f"src/{inputs['project_name'].replace('-', '_')}.egg-info",
    ]

    def inspect(path: Path, expected_directory: bool | None = None) -> bool:
        safe_path(path)
        if not path.exists():
            return False
        info = path.lstat()
        directory = stat.S_ISDIR(info.st_mode)
        regular = stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        if not (directory or regular) or (
            expected_directory is not None and directory != expected_directory
        ):
            raise PythonProjectError("unsafe generated output target")
        return directory

    for relative in files:
        inspect(project / relative, False)
    for relative in directories:
        path = project / relative
        if inspect(path, True):
            for parent, dirs, names in os.walk(path, followlinks=False):
                for name in (*dirs, *names):
                    inspect(Path(parent) / name)


def run(argv: list[str], project: Path, env: dict[str, str]) -> str:
    result = subprocess.run(
        argv, cwd=project, env=env, capture_output=True, text=True, timeout=300
    )
    if result.returncode:
        # Tool output may contain private indexes or credentials; never echo it.
        raise PythonProjectError(f"{' '.join(argv)} failed (exit {result.returncode})")
    return result.stdout.strip()


def bootstrap(target: Path, bundle: Path, candidate_set: str) -> dict:
    executor = scaffold_module()
    try:
        target, _ = executor._canonical_target(target)
        with executor.target_lock(target):
            return _bootstrap_locked(executor, target, bundle, candidate_set)
    except (
        OSError,
        ValueError,
        subprocess.TimeoutExpired,
        executor.ScaffoldError,
    ) as error:
        raise PythonProjectError(str(error)) from error


def _bootstrap_locked(executor, target: Path, bundle: Path, candidate_set: str) -> dict:
    validation, manifest, bundle = executor._load_validated_bundle(target, bundle)
    candidate = next(
        (
            item
            for item in manifest["candidate_sets"]
            if item["id"] == candidate_set and item["owner"] == "python-project"
        ),
        None,
    )
    if candidate is None:
        raise PythonProjectError("unknown Python candidate set")
    inputs = executor._validate_python_candidates(bundle / candidate["manifest"])
    project = safe_path(target / inputs["component_root"])
    identity = project.stat()
    receipt_path = bundle / f"readiness-{candidate_set}.json"
    receipt = dict(
        schema_version=1,
        status="applied-not-ready",
        bundle_digest=validation["bundle_digest"],
        input_sha256=candidate["input_sha256"],
        checks=[],
        lock_sha256=None,
    )

    def check_source():
        status, code = executor.status_bundle(target, bundle)
        if code or any(item["state"] != "after" for item in status["operations"]):
            raise PythonProjectError(
                "scaffold is not fully applied or authored files drifted"
            )
        safe_path(project)
        current = project.stat()
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            raise PythonProjectError("project directory identity changed")
        for name in ("uv.lock", ".venv"):
            path = safe_path(project / name)
            if path.exists() and (
                not path.is_dir() if name == ".venv" else not path.is_file()
            ):
                raise PythonProjectError("invalid dependency output target")
        check_generated_outputs(project, inputs)

    executor._write_private_json(receipt_path, receipt)
    try:
        check_source()
        reject_parent_workspace(project)
        metadata_bytes = read_bytes(project / "pyproject.toml")
        receipt["metadata_sha256"] = digest(metadata_bytes)
        metadata = tomllib.loads(metadata_bytes.decode())
        # Files referenced by packaging metadata must exist after root integration.
        readme = metadata["project"].get("readme")
        if not isinstance(readme, str) or readme != "README.md":
            raise PythonProjectError("unsupported project readme reference")
        read_bytes(project / readme)
        env = environment(project)
        receipt["uv_version"] = run(["uv", "self", "version"], project, env)
        lock = project / "uv.lock"
        commands = []
        if not lock.exists():
            commands.append(["uv", "lock"])
        commands += [
            ["uv", "lock", "--check"],
            ["uv", "sync", "--locked"],
            ["uv", "run", "--locked", "ruff", "check", "src", "tests", "scripts"],
            ["uv", "run", "--locked", "pytest"],
            ["uv", "run", "--locked", "python", "scripts/smoke-wheel.py"],
        ]
        for command in commands:
            check_source()
            reject_parent_workspace(project)
            run(command, project, env)
            receipt["checks"].append(dict(command=command, status="passed"))
            executor._write_private_json(receipt_path, receipt)
        check_source()
        receipt["lock_sha256"] = hashlib.sha256(read_bytes(lock)).hexdigest()
        receipt["python_version"] = run(
            ["uv", "run", "--locked", "python", "--version"], project, env
        )
        receipt["platform_checks"] = (
            {"systemd": "not-run (service activation is outside scaffolding)"}
            if "systemd" in inputs["profiles"]
            else {}
        )
        receipt["status"] = "ready"
        receipt["checked_at"] = int(time.time())
        executor._write_private_json(receipt_path, receipt)
        return dict(ok=True, status="ready", receipt=str(receipt_path))
    except (
        OSError,
        ValueError,
        subprocess.TimeoutExpired,
        executor.ScaffoldError,
    ) as error:
        receipt["status"] = "applied-not-ready"
        # No raw subprocess output, indexes or credentials in the readiness receipt.
        receipt["failure"] = type(error).__name__
        executor._write_private_json(receipt_path, receipt)
        raise PythonProjectError(
            f"scaffold applied but Python is not ready: {error}"
        ) from error
