#!/usr/bin/env python3
"""Finalize, validate, inspect, and safely apply scaffold plan bundles."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows remains validate-only.
    fcntl = None  # type: ignore[assignment]


SCHEMA_VERSION = 2
APP_STACK_HANDOFF_SCHEMA_VERSION = 2
CANDIDATE_MANIFEST_SCHEMA_VERSION = 1
SUPPORTED_PLATFORMS = {"darwin", "linux"}
SUPPORTED_OWNERS = {
    "container",
    "frontend-project",
    "github-workflows",
    "gitignore",
    "helmchart",
    "python-project",
    "scaffold-project",
    "shell-scripting",
    "terraform",
}
STATUSES = {"required", "conditional", "deferred", "rejected"}
ACTIONS = {"create", "semantic_merge", "unchanged"}
HEX_DIGEST_LENGTH = 64
DEFAULT_RESERVED_NAMES = {"agents.md", "agents.override.md"}
FORBIDDEN_REPOSITORY_CONTROL_ROOTS = frozenset({".git", ".hg", ".svn"})
MATERIALIZATION_OWNER_CONTRACTS = {
    "frontend-project": ({"typescript"}, {"react-vite"}),
    "helmchart": ({"yaml"}, {"helm"}),
    "python-project": ({"python"}, None),
    "shell-scripting": ({"bash", "shell"}, {None}),
    "terraform": ({"hcl"}, {"terraform"}),
}
APP_STACK_COMPONENT_CLASSES = {
    "application",
    "external-service",
    "frontend",
    "infrastructure",
}
FRONTEND_CAPABILITY_BASELINES = {
    "routing": "none",
    "styling": "plain-css",
    "testing": "vitest",
    "public-environment": "vite-public-environment",
    "lint": "none",
    "format": "none",
}
SEMANTIC_MERGE_BASENAMES = {".gitignore", "Makefile", "README.md"}
UNRESOLVED_PLACEHOLDER = re.compile(rb"\{\{[A-Z][A-Z0-9_]*\}\}")
SAFE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SAFE_GITIGNORE_RULES = {
    ".AppleDouble",
    ".DS_Store",
    ".LSOverride",
    ".Spotlight-V100/",
    ".Trashes/",
    "._*",
    ".cache/",
    ".env",
    ".env.*",
    ".fseventsd/",
    ".gradle/",
    ".history/",
    ".ipynb_checkpoints/",
    ".mypy_cache/",
    ".next/",
    ".nox/",
    ".npm/",
    ".nuxt/",
    ".pnpm-store/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".terraform/",
    ".tox/",
    ".venv/",
    ".vscode/*",
    ".yarn/",
    "*.code-workspace",
    "*.log",
    "*.swo",
    "*.swp",
    "*.test",
    "*.tfstate",
    "*.tfstate.*",
    "*~",
    "!.env.example",
    "!.vscode/extensions.json",
    "!.vscode/launch.json",
    "!.vscode/settings.json",
    "!.vscode/tasks.json",
    "Icon?",
    "__pycache__/",
    "build/",
    "coverage.out",
    "coverage/",
    "crash.log",
    "dist/",
    "node_modules/",
    "out/",
    "target/",
    "temp/",
    "tmp/",
    "venv/",
}
OWNER_ARTIFACT_SUFFIXES = {
    "frontend-project": {
        ".css",
        ".html",
        ".json",
        ".md",
        ".scss",
        ".svg",
        ".ts",
        ".tsx",
    },
    "helmchart": {".json", ".md", ".tpl", ".txt", ".yaml", ".yml"},
    "python-project": {
        ".cfg",
        ".ini",
        ".md",
        ".py",
        ".pyi",
        ".rst",
        ".service",
        ".timer",
        ".toml",
        ".txt",
    },
    "shell-scripting": {".sh"},
    "terraform": {".hcl", ".json", ".md", ".tf", ".tfvars"},
}


class ScaffoldError(RuntimeError):
    """A safe, user-actionable scaffold failure."""


def _error(message: str) -> None:
    raise ScaffoldError(message)


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _error(f"{label} must be an array")
    return value


def _require_string(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        _error(f"{label} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        _error(f"{label} must use Unicode NFC normalization")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{label} must be a boolean")
    return value


def _require_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _error(f"{label} must be an integer")
    return value


def _require_exact_keys(
    value: dict[str, Any],
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    label: str,
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - value.keys())
    unknown = sorted(value.keys() - allowed)
    if missing:
        _error(f"{label} is missing required fields: {', '.join(missing)}")
    if unknown:
        _error(f"{label} contains unknown fields: {', '.join(unknown)}")


def _validate_technology_data(value: Any, label: str) -> dict[str, Any]:
    technology = _require_dict(value, label)
    _require_exact_keys(
        technology,
        required={
            "name",
            "language",
            "framework",
            "profile",
            "runtime",
            "package_manager",
            "versions",
        },
        label=label,
    )
    _require_string(technology["name"], f"{label}.name")
    for field in (
        "language",
        "framework",
        "profile",
        "runtime",
        "package_manager",
    ):
        if technology[field] is not None:
            _require_string(technology[field], f"{label}.{field}")
    versions = _require_dict(technology["versions"], f"{label}.versions")
    for key, version in versions.items():
        _require_string(key, f"{label}.versions key")
        _require_string(version, f"{label}.versions.{key}")
    return technology


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == HEX_DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise ScaffoldError(f"cannot open regular file for digest: {path}") from error
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode):
            _error(f"digest source must be a regular file: {path}")
        return _sha256_fd(descriptor)
    finally:
        os.close(descriptor)


def _sha256_fd(file_descriptor: int) -> str:
    digest = hashlib.sha256()
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    while chunk := os.read(file_descriptor, 1024 * 1024):
        digest.update(chunk)
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _parse_json_bytes(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScaffoldError(f"{label} must be UTF-8 JSON") from error

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ScaffoldError(f"{label} contains duplicate object key: {key}")
            result[key] = value
        return result

    def reject_non_standard_number(value: str) -> None:
        raise ScaffoldError(f"{label} contains non-standard numeric literal: {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_standard_number,
        )
    except json.JSONDecodeError as error:
        raise ScaffoldError(f"{label} is invalid JSON: {error}") from error


def _write_private_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        payload = _pretty_bytes(value)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory_descriptor = os.open(
            path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        if temporary.exists():
            temporary.unlink()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    return _require_dict(
        _parse_json_bytes(_read_private_regular_file(path, label), label),
        label,
    )


def _normalize_relative_path(value: Any, label: str) -> str:
    raw = _require_string(value, label)
    if any(ord(character) < 32 or ord(character) == 127 for character in raw):
        _error(f"{label} contains an ASCII control character")
    if "\\" in raw:
        _error(f"{label} contains an unsafe path character")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw.startswith("/") or raw.endswith("/"):
        _error(f"{label} must be a normalized repository-relative path")
    if any(part in {"", ".", ".."} for part in path.parts):
        _error(f"{label} must not contain empty, dot, or parent segments")
    normalized = path.as_posix()
    if normalized != raw:
        _error(f"{label} must already be normalized")
    return normalized


def _validate_owner_artifact_path(
    owner: str,
    path: str,
    label: str,
    materialization_unit: dict[str, Any] | None,
) -> None:
    artifact = PurePosixPath(path)
    name = artifact.name
    suffix = artifact.suffix.casefold()
    unit_root = (
        materialization_unit["path"] if materialization_unit is not None else None
    )
    owner_unit_root = None
    if materialization_unit is not None and materialization_unit["owner"] == owner:
        owner_unit_root = materialization_unit["path"]

    allowed = False
    if owner in OWNER_ARTIFACT_SUFFIXES:
        allowed = suffix in OWNER_ARTIFACT_SUFFIXES[owner]
        if owner == "frontend-project":
            allowed = allowed or name in {
                ".env.example",
                ".npmrc",
                ".nvmrc",
                ".prettierignore",
            }
        elif owner == "helmchart":
            allowed = allowed or name == ".helmignore"
        elif owner == "python-project":
            allowed = allowed or name in {
                ".pre-commit-config.yaml",
                ".python-version",
                "py.typed",
            }
            if name == ".pre-commit-config.yaml":
                allowed = path == name
            elif name == "Makefile":
                allowed = (
                    owner_unit_root is not None
                    and path == f"{owner_unit_root}/Makefile"
                )
        elif owner == "terraform":
            if name in {".gitignore", "Makefile"}:
                allowed = (
                    owner_unit_root is not None and path == f"{owner_unit_root}/{name}"
                )
            elif name == "terraform.tfvars.example":
                allowed = owner_unit_root is not None
    elif owner == "container":
        scoped_root_file = artifact.parent == PurePosixPath(".") or (
            unit_root is not None and artifact.parent == PurePosixPath(unit_root)
        )
        allowed = (
            name == ".dockerignore"
            or name == "Dockerfile"
            or name.startswith("Dockerfile.")
            or name == "Containerfile"
            or name.startswith("Containerfile.")
            or (
                scoped_root_file
                and name
                in {
                    "compose.yaml",
                    "compose.override.yaml",
                    "compose.production.yaml",
                    "compose.test.yaml",
                    "docker-bake.hcl",
                    "docker-bake.json",
                }
            )
        )
    elif owner == "github-workflows":
        allowed = path.startswith(".github/workflows/") and suffix in {".yaml", ".yml"}
    elif owner == "gitignore":
        allowed = path == ".gitignore"
    elif owner == "scaffold-project":
        allowed = path in {"README.md", "Makefile", "docs/scaffold-plan.json"}

    if not allowed:
        _error(f"{label} is outside the positive artifact contract for {owner}: {path}")


def _validate_path_set(paths: list[str], reserved_paths: set[str]) -> None:
    folded: dict[str, str] = {}
    exact = set(paths)
    for path in paths:
        key = path.casefold()
        if key in folded and folded[key] != path:
            _error(f"case-folding path collision: {folded[key]} and {path}")
        folded[key] = path
        root = PurePosixPath(path).parts[0].casefold()
        if root in FORBIDDEN_REPOSITORY_CONTROL_ROOTS:
            _error(f"repository control paths are reserved: {path}")
        if PurePosixPath(path).name.casefold() in DEFAULT_RESERVED_NAMES:
            _error(f"instruction file paths are reserved: {path}")
        if path.casefold() in {reserved.casefold() for reserved in reserved_paths}:
            _error(f"configured path is reserved: {path}")
    for path in paths:
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            ancestor = PurePosixPath(*parts[:index]).as_posix()
            if ancestor in exact:
                _error(f"path ancestor conflict: {ancestor} and {path}")


def _mode_string(mode: int) -> str:
    return f"{stat.S_IMODE(mode):04o}"


def _parse_mode(value: Any, label: str) -> int:
    raw = _require_string(value, label)
    if raw not in {"0644", "0755"}:
        _error(f"{label} must be 0644 or 0755")
    return int(raw, 8)


def _ensure_regular_lstat(path: Path, label: str) -> os.stat_result:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise ScaffoldError(f"cannot inspect {label} {path}: {error}") from error
    if not stat.S_ISREG(result.st_mode):
        _error(f"{label} must be a regular file: {path}")
    return result


def _regular_file_identity_and_digest(
    path: Path,
    label: str,
) -> tuple[os.stat_result, str]:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise ScaffoldError(f"cannot safely open {label}: {path}") from error
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode):
            _error(f"{label} must be a regular file: {path}")
        return result, _sha256_fd(descriptor)
    finally:
        os.close(descriptor)


def _read_regular_file_bytes(path: Path, label: str) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise ScaffoldError(f"cannot safely open {label}: {path}") from error
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode):
            _error(f"{label} must be a regular file: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_private_regular_file(path: Path, label: str) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise ScaffoldError(f"cannot safely open {label}: {path}") from error
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode) or _mode_string(result.st_mode) != "0600":
            _error(f"{label} must be a 0600 regular file: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _ensure_private_parent_chain(bundle: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(bundle)
    except ValueError:
        _error(f"private path escapes its bundle: {parent}")
    current = bundle
    for part in relative.parts:
        current /= part
        try:
            result = os.lstat(current)
        except OSError as error:
            raise ScaffoldError(
                f"private directory is unavailable: {current}"
            ) from error
        if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode):
            _error(f"private path parent must be a real directory: {current}")
        if _mode_string(result.st_mode) != "0700":
            _error(f"private directory permissions must be 0700: {current}")


def _private_subdirectory(bundle: Path, name: str, *, create: bool) -> Path:
    path = bundle / name
    if create:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise ScaffoldError(f"cannot create private directory: {path}") from error
    _ensure_private_parent_chain(bundle, path)
    return path


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _git_worktree_ancestor(path: Path) -> Path | None:
    for ancestor in (path, *path.parents):
        marker = ancestor / ".git"
        if marker.exists() or marker.is_symlink():
            return ancestor
    return None


def _private_bundle_path(
    bundle: Path,
    target: Path,
    *,
    create: bool,
) -> Path:
    expanded = Path(os.path.abspath(os.path.expanduser(str(bundle))))
    if expanded == target or _path_is_within(expanded, target):
        _error("private bundle must be outside the scaffold target")
    worktree = _git_worktree_ancestor(expanded)
    if worktree is not None:
        _error(f"private bundle must be outside a Git worktree: {worktree}")
    if create:
        expanded.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        result = os.lstat(expanded)
    except OSError as error:
        raise ScaffoldError(
            f"private bundle directory is unavailable: {expanded}"
        ) from error
    if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode):
        _error(f"private bundle must be a real directory, not a symlink: {expanded}")
    canonical = expanded.resolve(strict=True)
    if canonical == target or _path_is_within(canonical, target):
        _error("private bundle must be outside the scaffold target")
    worktree = _git_worktree_ancestor(canonical)
    if worktree is not None:
        _error(f"private bundle must be outside a Git worktree: {worktree}")
    if create:
        os.chmod(canonical, 0o700)
    elif _mode_string(result.st_mode) != "0700":
        _error(f"private bundle permissions must be 0700: {canonical}")
    return canonical


def _canonical_target(target: Path) -> tuple[Path, dict[str, Any]]:
    expanded = Path(os.path.abspath(os.path.expanduser(str(target))))
    parent = expanded.parent
    try:
        parent_result = os.lstat(parent)
    except OSError as error:
        raise ScaffoldError(f"target parent must already exist: {parent}") from error
    if not stat.S_ISDIR(parent_result.st_mode) or stat.S_ISLNK(parent_result.st_mode):
        _error(f"target parent must be a real directory, not a symlink: {parent}")
    if expanded.exists() or expanded.is_symlink():
        result = os.lstat(expanded)
        if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode):
            _error(f"target must be a real directory or absent: {expanded}")
        canonical = expanded.resolve(strict=True)
        state = {
            "state": "directory",
            "device": result.st_dev,
            "inode": result.st_ino,
            "mode": _mode_string(result.st_mode),
            "parent_device": parent_result.st_dev,
            "parent_inode": parent_result.st_ino,
        }
    else:
        canonical = parent.resolve(strict=True) / expanded.name
        state = {
            "state": "absent",
            "parent_device": parent_result.st_dev,
            "parent_inode": parent_result.st_ino,
        }
    return canonical, state


def _validate_common_draft(draft: dict[str, Any]) -> None:
    _require_exact_keys(
        draft,
        required={
            "schema_version",
            "project",
            "capabilities",
            "materialization_units",
            "runtime_units",
            "external_services",
            "candidate_sets",
            "operations",
            "validations",
            "execution",
            "safety",
        },
        label="manifest draft",
    )
    if draft["schema_version"] != SCHEMA_VERSION:
        _error(f"unsupported schema_version: {draft['schema_version']}")

    project = _require_dict(draft["project"], "project")
    _require_exact_keys(
        project,
        required={"name", "repository_shape", "architecture"},
        label="project",
    )
    _require_string(project["name"], "project.name")
    if project["repository_shape"] not in {
        "single-component",
        "modular-monolith",
        "multi-component",
    }:
        _error("project.repository_shape is invalid")
    architecture = _require_dict(project["architecture"], "project.architecture")
    _require_exact_keys(
        architecture,
        required={"approval", "approval_reference", "handoff", "sources"},
        label="project.architecture",
    )
    if architecture["approval"] not in {"direct-user", "approved-artifact"}:
        _error("project.architecture.approval is invalid")
    _require_string(
        architecture["approval_reference"], "project.architecture.approval_reference"
    )
    sources = _require_list(architecture["sources"], "project.architecture.sources")
    if architecture["approval"] == "approved-artifact" and not sources:
        _error("approved-artifact architecture requires at least one source")
    for index, source_value in enumerate(sources):
        source = _require_dict(source_value, f"project.architecture.sources[{index}]")
        _require_exact_keys(
            source,
            required={"path", "sha256"},
            label=f"project.architecture.sources[{index}]",
        )
        _require_string(source["path"], f"project.architecture.sources[{index}].path")
        if not _is_digest(source["sha256"]):
            _error(f"project.architecture.sources[{index}].sha256 is invalid")
    handoff = architecture["handoff"]
    if handoff is not None:
        if architecture["approval"] != "approved-artifact":
            _error("project.architecture.handoff requires approved-artifact approval")
        handoff = _require_dict(handoff, "project.architecture.handoff")
        _require_exact_keys(
            handoff,
            required={"schema_version", "path", "sha256"},
            label="project.architecture.handoff",
        )
        if handoff["schema_version"] != APP_STACK_HANDOFF_SCHEMA_VERSION:
            _error("project.architecture.handoff.schema_version is unsupported")
        _require_string(handoff["path"], "project.architecture.handoff.path")
        if not _is_digest(handoff["sha256"]):
            _error("project.architecture.handoff.sha256 is invalid")
        if not any(
            source["path"] == handoff["path"] and source["sha256"] == handoff["sha256"]
            for source in sources
        ):
            _error("project.architecture.handoff must match an architecture source")

    capabilities = _require_list(draft["capabilities"], "capabilities")
    capability_ids: set[str] = set()
    capability_by_id: dict[str, dict[str, Any]] = {}
    required_unit_ids: set[str] = set()
    for index, capability_value in enumerate(capabilities):
        capability = _require_dict(capability_value, f"capabilities[{index}]")
        _require_exact_keys(
            capability,
            required={
                "id",
                "kind",
                "status",
                "materialization_unit_ids",
                "trigger",
            },
            optional={"technology"},
            label=f"capabilities[{index}]",
        )
        capability_id = _require_string(capability["id"], f"capabilities[{index}].id")
        if capability_id in capability_ids:
            _error(f"duplicate capability id: {capability_id}")
        capability_ids.add(capability_id)
        capability_by_id[capability_id] = capability
        _require_string(capability["kind"], f"capabilities[{index}].kind")
        status_value = capability["status"]
        if status_value not in STATUSES:
            _error(f"capabilities[{index}].status is invalid")
        unit_ids = _require_list(
            capability["materialization_unit_ids"],
            f"capabilities[{index}].materialization_unit_ids",
        )
        for unit_id in unit_ids:
            required_unit_ids.add(
                _require_string(
                    unit_id, f"capabilities[{index}].materialization_unit_ids[]"
                )
            )
        trigger = capability["trigger"]
        if status_value == "required" and not unit_ids:
            _error(f"required capability {capability_id} must materialize a unit")
        if status_value != "required" and unit_ids:
            _error(f"non-required capability {capability_id} cannot materialize units")
        if status_value == "conditional":
            _require_string(trigger, f"capabilities[{index}].trigger")
        elif trigger is not None:
            _error(f"capabilities[{index}].trigger must be null unless conditional")
        if "technology" in capability:
            _validate_technology_data(
                capability["technology"],
                f"capabilities[{index}].technology",
            )

    units = _require_list(draft["materialization_units"], "materialization_units")
    unit_ids: set[str] = set()
    unit_by_id: dict[str, dict[str, Any]] = {}
    unit_paths: list[str] = []
    for index, unit_value in enumerate(units):
        unit = _require_dict(unit_value, f"materialization_units[{index}]")
        _require_exact_keys(
            unit,
            required={
                "id",
                "kind",
                "path",
                "language",
                "framework",
                "owner",
                "invocation_scope",
            },
            optional={"technologies"},
            label=f"materialization_units[{index}]",
        )
        unit_id = _require_string(unit["id"], f"materialization_units[{index}].id")
        if unit_id in unit_ids:
            _error(f"duplicate materialization unit id: {unit_id}")
        unit_ids.add(unit_id)
        unit_by_id[unit_id] = unit
        _require_string(unit["kind"], f"materialization_units[{index}].kind")
        unit_path = _normalize_relative_path(
            unit["path"], f"materialization_units[{index}].path"
        )
        unit_paths.append(unit_path)
        for field in ("language", "framework"):
            value = unit[field]
            if value is not None:
                _require_string(value, f"materialization_units[{index}].{field}")
        owner_contract = MATERIALIZATION_OWNER_CONTRACTS.get(unit["owner"])
        if owner_contract is None:
            _error(f"unsupported materialization owner: {unit['owner']}")
        languages, frameworks = owner_contract
        if unit["language"] not in languages:
            _error(
                f"materialization unit {unit_id} language is not supported by "
                f"{unit['owner']}"
            )
        if frameworks is not None and unit["framework"] not in frameworks:
            _error(
                f"materialization unit {unit_id} framework is not supported by "
                f"{unit['owner']}"
            )
        if unit["invocation_scope"] != "coordinated-candidate":
            _error("materialization units must use coordinated-candidate scope")
        if "technologies" in unit:
            technologies = _require_list(
                unit["technologies"],
                f"materialization_units[{index}].technologies",
            )
            if not technologies:
                _error(f"materialization_units[{index}].technologies must not be empty")
            normalized_technologies = [
                _require_string(
                    technology,
                    f"materialization_units[{index}].technologies[]",
                )
                for technology in technologies
            ]
            if len(set(normalized_technologies)) != len(normalized_technologies):
                _error(f"materialization_units[{index}].technologies must be unique")
    if not unit_ids:
        _error("at least one required materialization unit is required")
    _validate_path_set(unit_paths, set())
    if required_unit_ids != unit_ids:
        missing = sorted(required_unit_ids - unit_ids)
        orphaned = sorted(unit_ids - required_unit_ids)
        _error(
            "materialization unit references do not match required capabilities; "
            f"missing={missing}, orphaned={orphaned}"
        )

    runtime_units = _require_list(draft["runtime_units"], "runtime_units")
    runtime_ids: set[str] = set()
    for index, runtime_value in enumerate(runtime_units):
        runtime = _require_dict(runtime_value, f"runtime_units[{index}]")
        _require_exact_keys(
            runtime,
            required={
                "id",
                "kind",
                "capability_id",
                "materialization_unit_id",
                "runtime",
            },
            label=f"runtime_units[{index}]",
        )
        runtime_id = _require_string(runtime["id"], f"runtime_units[{index}].id")
        if runtime_id in runtime_ids:
            _error(f"duplicate runtime unit id: {runtime_id}")
        runtime_ids.add(runtime_id)
        _require_string(runtime["kind"], f"runtime_units[{index}].kind")
        capability_id = _require_string(
            runtime["capability_id"],
            f"runtime_units[{index}].capability_id",
        )
        if capability_id not in capability_ids:
            _error(f"runtime unit {runtime_id} references an unknown capability")
        if runtime["materialization_unit_id"] not in unit_ids:
            _error(
                f"runtime unit {runtime_id} references an unknown materialization unit"
            )
        if (
            runtime["materialization_unit_id"]
            not in capability_by_id[capability_id]["materialization_unit_ids"]
        ):
            _error(
                f"runtime unit {runtime_id} is not bound to capability "
                f"{capability_id}'s materialization units"
            )
        _require_string(runtime["runtime"], f"runtime_units[{index}].runtime")

    services = _require_list(draft["external_services"], "external_services")
    service_ids: set[str] = set()
    for index, service_value in enumerate(services):
        service = _require_dict(service_value, f"external_services[{index}]")
        _require_exact_keys(
            service,
            required={
                "id",
                "kind",
                "technology",
                "status",
                "materialization",
                "trigger",
            },
            label=f"external_services[{index}]",
        )
        service_id = _require_string(service["id"], f"external_services[{index}].id")
        if service_id in service_ids:
            _error(f"duplicate external service id: {service_id}")
        service_ids.add(service_id)
        _require_string(service["kind"], f"external_services[{index}].kind")
        _require_string(service["technology"], f"external_services[{index}].technology")
        if service["status"] not in STATUSES:
            _error(f"external_services[{index}].status is invalid")
        if service["materialization"] not in {"configuration-only", "none"}:
            _error(f"external_services[{index}].materialization is invalid")
        if service["status"] == "conditional":
            _require_string(service["trigger"], f"external_services[{index}].trigger")
        elif service["trigger"] is not None:
            _error(
                f"external_services[{index}].trigger must be null unless conditional"
            )

    candidate_sets = _require_list(draft["candidate_sets"], "candidate_sets")
    candidate_set_by_id: dict[str, dict[str, Any]] = {}
    candidate_manifest_paths: set[str] = set()
    for index, candidate_set_value in enumerate(candidate_sets):
        candidate_set = _require_dict(candidate_set_value, f"candidate_sets[{index}]")
        _require_exact_keys(
            candidate_set,
            required={
                "id",
                "owner",
                "materialization_unit_id",
                "profile",
                "input_sha256",
                "manifest",
                "manifest_sha256",
                "operation_paths",
                "validation_ids",
            },
            label=f"candidate_sets[{index}]",
        )
        candidate_set_id = _require_string(
            candidate_set["id"], f"candidate_sets[{index}].id"
        )
        if SAFE_IDENTIFIER.fullmatch(candidate_set_id) is None:
            _error(f"candidate_sets[{index}].id is not a safe identifier")
        if candidate_set_id in candidate_set_by_id:
            _error(f"duplicate candidate set id: {candidate_set_id}")
        candidate_set_by_id[candidate_set_id] = candidate_set
        owner = candidate_set["owner"]
        if owner not in SUPPORTED_OWNERS:
            _error(f"candidate_sets[{index}].owner is unsupported")
        unit_id = candidate_set["materialization_unit_id"]
        if unit_id is not None:
            unit_id = _require_string(
                unit_id, f"candidate_sets[{index}].materialization_unit_id"
            )
            if unit_id not in unit_by_id:
                _error(
                    f"candidate_sets[{index}] references an unknown "
                    "materialization unit"
                )
        if owner == "frontend-project":
            if unit_id is None or unit_by_id[unit_id]["owner"] != "frontend-project":
                _error(
                    "frontend-project candidate sets must bind to a "
                    "frontend-project materialization unit"
                )
            if candidate_set["profile"] != "react-vite":
                _error("frontend-project candidate sets must use react-vite profile")
        _require_string(candidate_set["profile"], f"candidate_sets[{index}].profile")
        for field in ("input_sha256", "manifest_sha256"):
            if not _is_digest(candidate_set[field]):
                _error(f"candidate_sets[{index}].{field} is invalid")
        manifest_path = _normalize_relative_path(
            candidate_set["manifest"], f"candidate_sets[{index}].manifest"
        )
        expected_manifest = f"candidates/{owner}/{candidate_set_id}/manifest.json"
        if manifest_path != expected_manifest:
            _error(f"candidate_sets[{index}].manifest must be {expected_manifest}")
        if manifest_path in candidate_manifest_paths:
            _error(f"duplicate candidate manifest path: {manifest_path}")
        candidate_manifest_paths.add(manifest_path)
        operation_paths = [
            _normalize_relative_path(path, f"candidate_sets[{index}].operation_paths[]")
            for path in _require_list(
                candidate_set["operation_paths"],
                f"candidate_sets[{index}].operation_paths",
            )
        ]
        if not operation_paths or len(operation_paths) != len(set(operation_paths)):
            _error(
                f"candidate_sets[{index}].operation_paths must be non-empty and unique"
            )
        validation_ids = [
            _require_string(value, f"candidate_sets[{index}].validation_ids[]")
            for value in _require_list(
                candidate_set["validation_ids"],
                f"candidate_sets[{index}].validation_ids",
            )
        ]
        if not validation_ids or len(validation_ids) != len(set(validation_ids)):
            _error(
                f"candidate_sets[{index}].validation_ids must be non-empty and unique"
            )

    validations = _require_list(draft["validations"], "validations")
    validation_by_id: dict[str, dict[str, Any]] = {}
    for index, validation_value in enumerate(validations):
        validation = _require_dict(validation_value, f"validations[{index}]")
        _require_exact_keys(
            validation,
            required={
                "id",
                "owner",
                "materialization_unit_id",
                "candidate_set_id",
                "phase",
                "command",
                "network_required",
                "status",
            },
            label=f"validations[{index}]",
        )
        validation_id = _require_string(validation["id"], f"validations[{index}].id")
        if validation_id in validation_by_id:
            _error(f"duplicate validation id: {validation_id}")
        validation_by_id[validation_id] = validation
        if validation["owner"] not in SUPPORTED_OWNERS:
            _error(f"validations[{index}].owner is unsupported")
        candidate_set_id = _require_string(
            validation["candidate_set_id"],
            f"validations[{index}].candidate_set_id",
        )
        candidate_set = candidate_set_by_id.get(candidate_set_id)
        if candidate_set is None:
            _error(f"validations[{index}] references an unknown candidate set")
        unit_id = validation["materialization_unit_id"]
        if unit_id is not None:
            _require_string(unit_id, f"validations[{index}].materialization_unit_id")
        if (
            validation["owner"] != candidate_set["owner"]
            or unit_id != candidate_set["materialization_unit_id"]
        ):
            _error(f"validations[{index}] binding does not match its candidate set")
        if validation["phase"] not in {"candidate", "post-apply"}:
            _error(f"validations[{index}].phase is invalid")
        _require_string(validation["command"], f"validations[{index}].command")
        _require_bool(
            validation["network_required"],
            f"validations[{index}].network_required",
        )
        if validation["status"] not in {"passed", "pending", "not-run", "failed"}:
            _error(f"validations[{index}].status is invalid")
        if validation["phase"] == "candidate" and (
            validation["status"] != "passed" or validation["network_required"]
        ):
            _error("candidate-phase validations must pass without network access")
    for candidate_set_id, candidate_set in candidate_set_by_id.items():
        expected_ids = set(candidate_set["validation_ids"])
        actual_ids = {
            validation_id
            for validation_id, validation in validation_by_id.items()
            if validation["candidate_set_id"] == candidate_set_id
        }
        if expected_ids != actual_ids:
            _error(f"candidate set {candidate_set_id} validation bindings do not match")
        if not any(
            validation_by_id[validation_id]["phase"] == "candidate"
            and validation_by_id[validation_id]["status"] == "passed"
            for validation_id in expected_ids
        ):
            _error(
                f"candidate set {candidate_set_id} requires a passed "
                "candidate-phase validation"
            )

    execution = _require_dict(draft["execution"], "execution")
    execution_keys = {
        "allow_apply",
        "initialize_git",
        "install_dependencies",
        "network_access",
        "provision_services",
        "deploy",
    }
    _require_exact_keys(execution, required=execution_keys, label="execution")
    for key in execution_keys:
        _require_bool(execution[key], f"execution.{key}")
    if not execution["allow_apply"]:
        _error("execution.allow_apply must be true for an apply-capable bundle")
    for forbidden in execution_keys - {"allow_apply"}:
        if execution[forbidden]:
            _error(f"execution.{forbidden} must remain false")

    safety = _require_dict(draft["safety"], "safety")
    _require_exact_keys(safety, required={"reserved_paths"}, label="safety")
    reserved_paths = _require_list(safety["reserved_paths"], "safety.reserved_paths")
    normalized_reserved = {
        _normalize_relative_path(path, "safety.reserved_paths[]")
        for path in reserved_paths
    }

    operations = _require_list(draft["operations"], "operations")
    operation_paths: list[str] = []
    unit_operation_counts = {unit_id: 0 for unit_id in unit_ids}
    unit_owner_operation_counts = {unit_id: 0 for unit_id in unit_ids}
    for index, operation_value in enumerate(operations):
        operation = _require_dict(operation_value, f"operations[{index}]")
        _require_exact_keys(
            operation,
            required={
                "path",
                "action",
                "owner",
                "materialization_unit_id",
                "candidate",
                "mode",
                "candidate_set_id",
            },
            label=f"operations[{index}]",
        )
        path = _normalize_relative_path(operation["path"], f"operations[{index}].path")
        operation_paths.append(path)
        if operation["action"] not in ACTIONS:
            _error(f"operations[{index}].action is invalid")
        if operation["owner"] not in SUPPORTED_OWNERS:
            _error(f"operations[{index}].owner is unsupported")
        containing_units = [
            unit_id
            for unit_id, unit in unit_by_id.items()
            if path.startswith(f"{unit['path']}/")
        ]
        materialization_unit_id = operation["materialization_unit_id"]
        materialization_unit = None
        if materialization_unit_id is None:
            if containing_units:
                _error(
                    f"operations[{index}] must bind to materialization unit "
                    f"{containing_units[0]}"
                )
        else:
            materialization_unit_id = _require_string(
                materialization_unit_id,
                f"operations[{index}].materialization_unit_id",
            )
            unit = unit_by_id.get(materialization_unit_id)
            if unit is None:
                _error(
                    f"operations[{index}] references an unknown materialization unit"
                )
            if containing_units != [materialization_unit_id]:
                _error(
                    f"operations[{index}].path is outside materialization unit "
                    f"{materialization_unit_id}"
                )
            materialization_unit = unit
            unit_operation_counts[materialization_unit_id] += 1
            if operation["owner"] == unit["owner"]:
                unit_owner_operation_counts[materialization_unit_id] += 1
        if operation["owner"] == "frontend-project" and (
            materialization_unit is None
            or materialization_unit["owner"] != "frontend-project"
        ):
            _error(
                "frontend-project operations must stay inside their "
                "frontend-project materialization unit"
            )
        _validate_owner_artifact_path(
            operation["owner"],
            path,
            f"operations[{index}].path",
            materialization_unit,
        )
        candidate = _normalize_relative_path(
            operation["candidate"], f"operations[{index}].candidate"
        )
        if not candidate.startswith("candidates/"):
            _error(f"operations[{index}].candidate must be under candidates/")
        _parse_mode(operation["mode"], f"operations[{index}].mode")
        candidate_set_id = _require_string(
            operation["candidate_set_id"],
            f"operations[{index}].candidate_set_id",
        )
        candidate_set = candidate_set_by_id.get(candidate_set_id)
        if candidate_set is None:
            _error(f"operations[{index}] references an unknown candidate set")
        if (
            operation["owner"] != candidate_set["owner"]
            or materialization_unit_id != candidate_set["materialization_unit_id"]
        ):
            _error(f"operations[{index}] binding does not match its candidate set")
        if path not in candidate_set["operation_paths"]:
            _error(
                f"operations[{index}].path is not declared by candidate set "
                f"{candidate_set_id}"
            )
        manifest_parent = PurePosixPath(candidate_set["manifest"]).parent
        if not candidate.startswith(f"{manifest_parent.as_posix()}/files/"):
            _error(
                f"operations[{index}].candidate is outside candidate set "
                f"{candidate_set_id}"
            )
    if len(operation_paths) != len(set(operation_paths)):
        _error("operation paths must be unique")
    _validate_path_set(operation_paths, normalized_reserved)
    for candidate_set_id, candidate_set in candidate_set_by_id.items():
        actual_paths = {
            operation["path"]
            for operation in operations
            if operation["candidate_set_id"] == candidate_set_id
        }
        if actual_paths != set(candidate_set["operation_paths"]):
            _error(f"candidate set {candidate_set_id} operation bindings do not match")
    for unit_id in sorted(unit_ids):
        if unit_operation_counts[unit_id] == 0:
            _error(f"materialization unit {unit_id} has no file operations")
        if unit_owner_operation_counts[unit_id] == 0:
            _error(
                f"materialization unit {unit_id} has no operation owned by "
                f"{unit_by_id[unit_id]['owner']}"
            )


def _validate_app_stack_handoff_data(value: Any) -> None:
    handoff = _require_dict(value, "app-stack handoff")
    _require_exact_keys(
        handoff,
        required={"schema_version", "decision_id", "components"},
        label="app-stack handoff",
    )
    if handoff["schema_version"] != APP_STACK_HANDOFF_SCHEMA_VERSION:
        _error("app-stack handoff schema_version is unsupported")
    _require_string(handoff["decision_id"], "app-stack handoff.decision_id")
    components = _require_list(handoff["components"], "app-stack handoff.components")
    component_ids: set[str] = set()
    for index, component_value in enumerate(components):
        component = _require_dict(
            component_value, f"app-stack handoff.components[{index}]"
        )
        _require_exact_keys(
            component,
            required={
                "id",
                "component_class",
                "kind",
                "status",
                "technology",
                "capabilities",
                "constraints",
                "validation_expectations",
                "revisit_trigger",
            },
            label=f"app-stack handoff.components[{index}]",
        )
        component_id = _require_string(
            component["id"], f"app-stack handoff.components[{index}].id"
        )
        if component_id in component_ids:
            _error(f"duplicate app-stack handoff component id: {component_id}")
        component_ids.add(component_id)
        if component["component_class"] not in APP_STACK_COMPONENT_CLASSES:
            _error(f"app-stack handoff.components[{index}].component_class is invalid")
        _require_string(
            component["kind"], f"app-stack handoff.components[{index}].kind"
        )
        if component["status"] not in STATUSES:
            _error(f"app-stack handoff.components[{index}].status is invalid")
        _validate_technology_data(
            component["technology"],
            f"app-stack handoff.components[{index}].technology",
        )
        capability_ids: set[str] = set()
        for capability_index, capability_value in enumerate(
            _require_list(
                component["capabilities"],
                f"app-stack handoff.components[{index}].capabilities",
            )
        ):
            capability = _require_dict(
                capability_value,
                (
                    f"app-stack handoff.components[{index}]"
                    f".capabilities[{capability_index}]"
                ),
            )
            label = (
                f"app-stack handoff.components[{index}]"
                f".capabilities[{capability_index}]"
            )
            _require_exact_keys(
                capability,
                required={"id", "status", "selection", "revisit_trigger"},
                label=label,
            )
            capability_id = _require_string(capability["id"], f"{label}.id")
            if capability_id in capability_ids:
                _error(f"duplicate app-stack handoff capability id: {capability_id}")
            capability_ids.add(capability_id)
            if capability["status"] not in STATUSES:
                _error(f"{label}.status is invalid")
            if capability["selection"] is not None:
                _require_string(capability["selection"], f"{label}.selection")
            if capability["revisit_trigger"] is not None:
                _require_string(
                    capability["revisit_trigger"], f"{label}.revisit_trigger"
                )
        for field in ("constraints", "validation_expectations"):
            for item_index, item in enumerate(
                _require_list(
                    component[field],
                    f"app-stack handoff.components[{index}].{field}",
                )
            ):
                _require_string(
                    item,
                    (f"app-stack handoff.components[{index}].{field}[{item_index}]"),
                )
        if component["revisit_trigger"] is not None:
            _require_string(
                component["revisit_trigger"],
                f"app-stack handoff.components[{index}].revisit_trigger",
            )


def _validate_architecture_handoff(
    architecture: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    handoff = architecture["handoff"]
    if handoff is None:
        return None, None
    requested = Path(os.path.abspath(os.path.expanduser(handoff["path"])))
    canonical = requested.resolve(strict=True)
    matching_sources = [
        source
        for source in architecture["sources"]
        if Path(source["path"]) == canonical and source["sha256"] == handoff["sha256"]
    ]
    if len(matching_sources) != 1:
        _error("architecture handoff must match one finalized architecture source")
    payload = _read_regular_file_bytes(canonical, "app-stack handoff")
    if _sha256_bytes(payload) != handoff["sha256"]:
        _error("app-stack handoff digest changed")
    value = _parse_json_bytes(payload, "app-stack handoff")
    _validate_app_stack_handoff_data(value)
    return (
        {
            "schema_version": APP_STACK_HANDOFF_SCHEMA_VERSION,
            "path": str(canonical),
            "sha256": handoff["sha256"],
        },
        value,
    )


def _validate_candidate_manifest_record(
    value: Any,
    *,
    label: str,
) -> dict[str, Any]:
    validation = _require_dict(value, label)
    _require_exact_keys(
        validation,
        required={
            "id",
            "owner",
            "materialization_unit_id",
            "candidate_set_id",
            "phase",
            "command",
            "network_required",
            "status",
        },
        label=label,
    )
    for field in ("id", "owner", "candidate_set_id", "command"):
        _require_string(validation[field], f"{label}.{field}")
    if validation["materialization_unit_id"] is not None:
        _require_string(
            validation["materialization_unit_id"],
            f"{label}.materialization_unit_id",
        )
    if validation["phase"] not in {"candidate", "post-apply"}:
        _error(f"{label}.phase is invalid")
    _require_bool(validation["network_required"], f"{label}.network_required")
    if validation["status"] not in {"passed", "pending", "not-run", "failed"}:
        _error(f"{label}.status is invalid")
    return validation


def _validate_candidate_manifests(
    bundle: Path,
    document: dict[str, Any],
    *,
    finalized: bool,
) -> dict[str, dict[str, Any]]:
    operations_by_set: dict[str, dict[str, dict[str, Any]]] = {}
    for operation in document["operations"]:
        operations_by_set.setdefault(operation["candidate_set_id"], {})[
            operation["path"]
        ] = operation
    validations_by_set: dict[str, list[dict[str, Any]]] = {}
    for validation in document["validations"]:
        validations_by_set.setdefault(validation["candidate_set_id"], []).append(
            validation
        )

    inputs_by_set: dict[str, dict[str, Any]] = {}
    for index, candidate_set in enumerate(document["candidate_sets"]):
        candidate_set_id = candidate_set["id"]
        manifest_path = bundle.joinpath(*PurePosixPath(candidate_set["manifest"]).parts)
        _ensure_private_parent_chain(bundle, manifest_path.parent)
        manifest_bytes = _read_private_regular_file(
            manifest_path, f"candidate_sets[{index}] manifest"
        )
        if _sha256_bytes(manifest_bytes) != candidate_set["manifest_sha256"]:
            _error(f"candidate set {candidate_set_id} manifest digest mismatch")
        manifest = _require_dict(
            _parse_json_bytes(
                manifest_bytes,
                f"candidate set {candidate_set_id} manifest",
            ),
            f"candidate set {candidate_set_id} manifest",
        )
        _require_exact_keys(
            manifest,
            required={
                "schema_version",
                "candidate_set_id",
                "owner",
                "materialization_unit_id",
                "profile",
                "input_sha256",
                "inputs",
                "files",
                "validations",
            },
            label=f"candidate set {candidate_set_id} manifest",
        )
        if manifest["schema_version"] != CANDIDATE_MANIFEST_SCHEMA_VERSION:
            _error(f"candidate set {candidate_set_id} manifest schema is unsupported")
        for field in (
            "candidate_set_id",
            "owner",
            "materialization_unit_id",
            "profile",
            "input_sha256",
        ):
            if (
                manifest[field]
                != candidate_set["id" if field == "candidate_set_id" else field]
            ):
                _error(f"candidate set {candidate_set_id} manifest {field} mismatch")

        inputs = _require_dict(
            manifest["inputs"],
            f"candidate set {candidate_set_id} inputs",
        )
        if _sha256_bytes(_canonical_bytes(inputs)) != manifest["input_sha256"]:
            _error(f"candidate set {candidate_set_id} input digest mismatch")
        inputs_by_set[candidate_set_id] = inputs

        operations = operations_by_set.get(candidate_set_id, {})
        seen_paths: set[str] = set()
        seen_candidates: set[str] = set()
        manifest_parent = PurePosixPath(candidate_set["manifest"]).parent
        for file_index, file_value in enumerate(
            _require_list(manifest["files"], f"candidate set {candidate_set_id} files")
        ):
            label = f"candidate set {candidate_set_id} files[{file_index}]"
            file = _require_dict(file_value, label)
            _require_exact_keys(
                file,
                required={"path", "candidate", "mode", "sha256"},
                label=label,
            )
            path = _normalize_relative_path(file["path"], f"{label}.path")
            candidate_relative = _normalize_relative_path(
                file["candidate"], f"{label}.candidate"
            )
            if not candidate_relative.startswith("files/"):
                _error(f"{label}.candidate must be under files/")
            candidate = (manifest_parent / PurePosixPath(candidate_relative)).as_posix()
            _parse_mode(file["mode"], f"{label}.mode")
            if not _is_digest(file["sha256"]):
                _error(f"{label}.sha256 is invalid")
            if path in seen_paths or candidate in seen_candidates:
                _error(f"candidate set {candidate_set_id} file bindings are duplicate")
            seen_paths.add(path)
            seen_candidates.add(candidate)
            operation = operations.get(path)
            if operation is None:
                _error(
                    f"candidate set {candidate_set_id} manifest contains "
                    f"unbound path {path}"
                )
            if finalized:
                if (
                    operation["payload_sha256"] != file["sha256"]
                    or operation["after"]["mode"] != file["mode"]
                ):
                    _error(
                        f"candidate set {candidate_set_id} finalized file "
                        f"binding mismatch: {path}"
                    )
            else:
                if (
                    operation["candidate"] != candidate
                    or operation["mode"] != file["mode"]
                ):
                    _error(
                        f"candidate set {candidate_set_id} draft file "
                        f"binding mismatch: {path}"
                    )
                candidate_path = bundle.joinpath(*PurePosixPath(candidate).parts)
                _ensure_private_parent_chain(bundle, candidate_path.parent)
                candidate_bytes = _read_private_regular_file(
                    candidate_path, f"candidate set {candidate_set_id} file"
                )
                if _sha256_bytes(candidate_bytes) != file["sha256"]:
                    _error(
                        f"candidate set {candidate_set_id} candidate digest "
                        f"mismatch: {path}"
                    )
        if seen_paths != set(candidate_set["operation_paths"]):
            _error(
                f"candidate set {candidate_set_id} manifest operation paths mismatch"
            )

        manifest_validations = [
            _validate_candidate_manifest_record(
                value,
                label=(
                    f"candidate set {candidate_set_id} validations[{validation_index}]"
                ),
            )
            for validation_index, value in enumerate(
                _require_list(
                    manifest["validations"],
                    f"candidate set {candidate_set_id} validations",
                )
            )
        ]
        if sorted(manifest_validations, key=lambda item: item["id"]) != sorted(
            validations_by_set.get(candidate_set_id, []),
            key=lambda item: item["id"],
        ):
            _error(f"candidate set {candidate_set_id} manifest validations mismatch")
    return inputs_by_set


def _validate_app_stack_bindings(
    document: dict[str, Any],
    handoff: dict[str, Any] | None,
    inputs_by_set: dict[str, dict[str, Any]],
) -> None:
    if handoff is None:
        return

    components = {component["id"]: component for component in handoff["components"]}
    scaffold_capabilities = {
        capability["id"]: capability for capability in document["capabilities"]
    }
    external_services = {
        service["id"]: service for service in document["external_services"]
    }
    materialization_units = {
        unit["id"]: unit for unit in document["materialization_units"]
    }
    runtime_units = document["runtime_units"]
    for component_id, component in components.items():
        expected_status = component["status"]
        technology = component["technology"]
        if component["component_class"] == "external-service":
            actual = external_services.get(component_id)
            if expected_status == "required" and (
                actual is None or actual["status"] != "required"
            ):
                _error(
                    f"required app-stack component {component_id} is not bound "
                    "to a required external service"
                )
            if actual is not None and actual["status"] != expected_status:
                _error(f"app-stack component status mismatch for {component_id}")
            if actual is not None and actual["kind"] != component["kind"]:
                _error(f"app-stack component kind mismatch for {component_id}")
            if actual is not None and actual["technology"] != technology["name"]:
                _error(
                    f"app-stack technology mismatch for {component_id}: "
                    "external service selection differs"
                )
            continue

        actual = scaffold_capabilities.get(component_id)
        if expected_status == "required" and (
            actual is None
            or actual["status"] != "required"
            or not actual["materialization_unit_ids"]
        ):
            _error(
                f"required app-stack component {component_id} is not bound "
                "to a required scaffold capability"
            )
        if actual is not None and actual["status"] != expected_status:
            _error(f"app-stack component status mismatch for {component_id}")
        if actual is None:
            continue
        if actual["kind"] != component["kind"]:
            _error(f"app-stack technology mismatch for {component_id}: kind differs")
        if actual.get("technology") != technology:
            _error(
                f"app-stack technology mismatch for {component_id}: "
                "scaffold capability selection differs"
            )
        if component["component_class"] != "frontend" and component["capabilities"]:
            _error(
                "non-frontend capability selections are unsupported for "
                f"app-stack component {component_id}"
            )
        if expected_status != "required":
            continue
        bound_units = [
            materialization_units[unit_id]
            for unit_id in actual["materialization_unit_ids"]
            if unit_id in materialization_units
        ]
        if len(bound_units) != len(actual["materialization_unit_ids"]):
            _error(
                f"app-stack technology mismatch for {component_id}: "
                "a bound materialization unit is missing"
            )
        technology_units = [
            unit
            for unit in bound_units
            if technology["name"] in unit.get("technologies", [])
        ]
        if len(technology_units) != len(bound_units):
            _error(
                f"app-stack technology mismatch for {component_id}: "
                "every bound materialization unit must declare the selected technology"
            )
        if technology["language"] is not None and any(
            unit.get("language") != technology["language"] for unit in technology_units
        ):
            _error(
                f"app-stack technology mismatch for {component_id}: "
                "materialization language differs"
            )
        component_runtimes = [
            runtime
            for runtime in runtime_units
            if runtime.get("capability_id") == component_id
        ]
        if technology["runtime"] is None:
            runtime_mismatch = bool(component_runtimes)
        else:
            runtime_mismatch = not component_runtimes or any(
                runtime["runtime"] != technology["runtime"]
                for runtime in component_runtimes
            )
        if runtime_mismatch:
            _error(
                f"app-stack technology mismatch for {component_id}: "
                "runtime selection differs"
            )

    approved_frontend: dict[str, dict[str, Any]] = {}
    for component_id, component in components.items():
        technology = component["technology"]
        if (
            component["status"] != "required"
            or component["component_class"] != "frontend"
        ):
            continue
        if (
            technology["language"] != "typescript"
            or technology["framework"] != "react"
            or technology["profile"] != "react-vite"
        ):
            _error(
                f"unsupported required frontend component {component_id}; "
                "frontend-project supports TypeScript, React, and react-vite"
            )
        approved_frontend[component_id] = component
    actual_frontend: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for candidate_set in document["candidate_sets"]:
        if candidate_set["owner"] != "frontend-project":
            continue
        inputs = _require_dict(
            inputs_by_set[candidate_set["id"]],
            f"candidate set {candidate_set['id']} inputs",
        )
        component_id = _require_string(
            inputs.get("component_id"),
            f"candidate set {candidate_set['id']} inputs.component_id",
        )
        if component_id in actual_frontend:
            _error(f"duplicate frontend candidate component: {component_id}")
        actual_frontend[component_id] = (candidate_set, inputs)

    if set(actual_frontend) != set(approved_frontend):
        _error(
            "frontend candidates do not match required app-stack components; "
            f"missing={sorted(set(approved_frontend) - set(actual_frontend))}, "
            f"unexpected={sorted(set(actual_frontend) - set(approved_frontend))}"
        )

    for component_id, component in approved_frontend.items():
        candidate_set, inputs = actual_frontend[component_id]
        capability = scaffold_capabilities.get(component_id)
        unit_id = candidate_set["materialization_unit_id"]
        if (
            capability is None
            or capability["status"] != "required"
            or unit_id not in capability["materialization_unit_ids"]
        ):
            _error(
                f"approved frontend component {component_id} is not bound to "
                "its required scaffold capability"
            )
        if (
            inputs.get("candidate_set_id") != candidate_set["id"]
            or inputs.get("materialization_unit_id") != unit_id
            or inputs.get("profile") != "react-vite"
        ):
            _error(
                f"approved frontend component {component_id} candidate identity "
                "does not match its normalized inputs"
            )

        technology = component["technology"]
        package = _require_dict(
            inputs.get("package"),
            f"candidate set {candidate_set['id']} inputs.package",
        )
        if package.get("manager") != technology["package_manager"]:
            _error(f"approved frontend package manager mismatch for {component_id}")
        if inputs.get("versions") != technology["versions"]:
            _error(f"approved frontend versions mismatch for {component_id}")
        candidate_capabilities = _require_dict(
            inputs.get("capabilities"),
            f"candidate set {candidate_set['id']} inputs.capabilities",
        )
        routing = _require_dict(
            candidate_capabilities.get("routing"),
            f"candidate set {candidate_set['id']} inputs.capabilities.routing",
        )
        lint = _require_dict(
            candidate_capabilities.get("lint"),
            f"candidate set {candidate_set['id']} inputs.capabilities.lint",
        )
        formatting = _require_dict(
            candidate_capabilities.get("format"),
            f"candidate set {candidate_set['id']} inputs.capabilities.format",
        )
        public_environment = _require_dict(
            candidate_capabilities.get("public_environment"),
            (
                f"candidate set {candidate_set['id']} "
                "inputs.capabilities.public_environment"
            ),
        )
        _require_list(
            public_environment.get("variables"),
            (
                f"candidate set {candidate_set['id']} "
                "inputs.capabilities.public_environment.variables"
            ),
        )
        actual_selections = {
            "routing": _require_string(
                routing.get("profile"),
                (
                    f"candidate set {candidate_set['id']} "
                    "inputs.capabilities.routing.profile"
                ),
            ),
            "styling": _require_string(
                candidate_capabilities.get("styling"),
                (f"candidate set {candidate_set['id']} inputs.capabilities.styling"),
            ),
            "testing": _require_string(
                candidate_capabilities.get("testing"),
                (f"candidate set {candidate_set['id']} inputs.capabilities.testing"),
            ),
            "public-environment": "vite-public-environment",
            "lint": _require_string(
                lint.get("profile"),
                (
                    f"candidate set {candidate_set['id']} "
                    "inputs.capabilities.lint.profile"
                ),
            ),
            "format": _require_string(
                formatting.get("profile"),
                (
                    f"candidate set {candidate_set['id']} "
                    "inputs.capabilities.format.profile"
                ),
            ),
        }
        unknown_capabilities = sorted(
            {
                item["id"]
                for item in component["capabilities"]
                if item["id"] not in FRONTEND_CAPABILITY_BASELINES
            }
        )
        if unknown_capabilities:
            _error(
                f"unsupported frontend capability for {component_id}: "
                + ", ".join(unknown_capabilities)
            )
        declared_capabilities = {item["id"]: item for item in component["capabilities"]}
        for capability_id, capability in declared_capabilities.items():
            actual = actual_selections[capability_id]
            if capability["status"] == "required":
                if capability["selection"] != actual:
                    _error(
                        "approved frontend capability mismatch for "
                        f"{component_id}: {capability_id}"
                    )
            elif actual != FRONTEND_CAPABILITY_BASELINES[capability_id]:
                _error(
                    "approved frontend capability mismatch for "
                    f"{component_id}: {capability_id} is not required"
                )
        for capability_id in ("routing", "lint", "format"):
            if (
                actual_selections[capability_id]
                != FRONTEND_CAPABILITY_BASELINES[capability_id]
                and capability_id not in declared_capabilities
            ):
                _error(
                    "approved frontend capability mismatch for "
                    f"{component_id}: {capability_id} was not approved"
                )
        if technology["runtime"] not in {
            runtime["runtime"]
            for runtime in runtime_units
            if runtime["materialization_unit_id"] == unit_id
        }:
            _error(f"approved frontend runtime mismatch for {component_id}")


def _validate_architecture_sources(
    architecture: dict[str, Any],
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for index, source in enumerate(architecture["sources"]):
        source_path = Path(os.path.abspath(os.path.expanduser(source["path"])))
        initial_result = _ensure_regular_lstat(
            source_path, f"project.architecture.sources[{index}]"
        )
        canonical_path = source_path.resolve(strict=True)
        result, actual = _regular_file_identity_and_digest(
            canonical_path,
            f"project.architecture.sources[{index}]",
        )
        if (
            result.st_dev != initial_result.st_dev
            or result.st_ino != initial_result.st_ino
            or _mode_string(result.st_mode) != _mode_string(initial_result.st_mode)
        ):
            _error(f"architecture source changed while finalizing: {source_path}")
        if actual != source["sha256"]:
            _error(f"architecture source digest changed: {source_path}")
        normalized.append(
            {
                "path": str(canonical_path),
                "sha256": actual,
                "device": result.st_dev,
                "inode": result.st_ino,
                "mode": _mode_string(result.st_mode),
            }
        )
    return normalized


def _validate_architecture_operation_disjoint(
    target: Path,
    architecture_sources: list[dict[str, Any]],
    operations: list[dict[str, Any]],
) -> None:
    operation_targets = {
        target.joinpath(*PurePosixPath(operation["path"]).parts)
        for operation in operations
    }
    for source in architecture_sources:
        source_path = Path(source["path"])
        if source_path in operation_targets:
            _error(f"architecture source overlaps an operation target: {source_path}")


def _inspect_file(path: Path) -> dict[str, Any]:
    try:
        path_result = os.lstat(path)
    except FileNotFoundError:
        return {"state": "absent"}
    if not stat.S_ISREG(path_result.st_mode):
        _error(f"target path is not a regular file: {path}")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        return {"state": "absent"}
    except OSError as error:
        raise ScaffoldError(f"cannot safely open target path: {path}") from error
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode):
            _error(f"target path is not a regular file: {path}")
        return {
            "state": "present",
            "sha256": _sha256_fd(descriptor),
            "file_type": "regular",
            "mode": _mode_string(result.st_mode),
            "size": result.st_size,
            "device": result.st_dev,
            "inode": result.st_ino,
        }
    finally:
        os.close(descriptor)


def _read_bound_regular_file(path: Path, expected: dict[str, Any]) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise ScaffoldError(f"cannot safely read target path: {path}") from error
    try:
        result = os.fstat(descriptor)
        if (
            not stat.S_ISREG(result.st_mode)
            or result.st_dev != expected["device"]
            or result.st_ino != expected["inode"]
            or _mode_string(result.st_mode) != expected["mode"]
            or result.st_size != expected["size"]
            or _sha256_fd(descriptor) != expected["sha256"]
        ):
            _error(f"target changed while validating semantic merge: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validate_semantic_merge_bytes(
    operation_path: str,
    before: dict[str, Any],
    original_bytes: bytes,
    candidate_bytes: bytes,
    candidate_mode: str,
) -> None:
    if PurePosixPath(operation_path).name not in SEMANTIC_MERGE_BASENAMES:
        _error(
            "semantic_merge is supported only for .gitignore, Makefile, "
            f"and README.md files: {operation_path}"
        )
    if candidate_mode != before["mode"]:
        _error(f"semantic_merge cannot change file mode: {operation_path}")
    if (
        len(original_bytes) != before["size"]
        or _sha256_bytes(original_bytes) != before["sha256"]
    ):
        _error(
            f"semantic_merge original bytes do not match before state: {operation_path}"
        )
    if len(candidate_bytes) <= len(original_bytes) or not candidate_bytes.startswith(
        original_bytes
    ):
        _error(
            "semantic_merge must preserve the complete original bytes and append "
            f"an exact non-empty suffix: {operation_path}"
        )
    suffix = candidate_bytes[len(original_bytes) :]
    if (
        original_bytes
        and not original_bytes.endswith(b"\n")
        and not suffix.startswith(b"\n")
    ):
        _error(f"semantic_merge suffix must start on a new line: {operation_path}")
    try:
        suffix_text = suffix.decode("utf-8")
        original_text = original_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ScaffoldError(
            f"semantic_merge supports UTF-8 text only: {operation_path}"
        ) from error
    basename = PurePosixPath(operation_path).name
    if basename == ".gitignore":
        existing_rules = {
            line.strip()
            for line in original_text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        appended_rules = [
            line.strip()
            for line in suffix_text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        unsupported = sorted(set(appended_rules) - SAFE_GITIGNORE_RULES)
        duplicate = sorted(set(appended_rules) & existing_rules)
        if unsupported:
            _error(
                "semantic_merge contains unsupported .gitignore rules: "
                + ", ".join(unsupported)
            )
        if duplicate or len(appended_rules) != len(set(appended_rules)):
            _error(
                "semantic_merge contains duplicate .gitignore rules: "
                + ", ".join(duplicate or appended_rules)
            )
        return
    marker_prefix = "<!--" if basename == "README.md" else "#"
    pattern = re.compile(
        rf"\s*{re.escape(marker_prefix)} scaffold-project:begin:"
        r"([a-z0-9][a-z0-9._-]*)"
        + (r" -->" if basename == "README.md" else "")
        + r".*"
        + re.escape(marker_prefix)
        + r" scaffold-project:end:\1"
        + (r" -->" if basename == "README.md" else "")
        + r"\s*",
        re.DOTALL,
    )
    match = pattern.fullmatch(suffix_text)
    if (
        suffix_text.count("scaffold-project:begin:") != 1
        or suffix_text.count("scaffold-project:end:") != 1
        or match is None
    ):
        _error(
            f"semantic_merge requires one matching scaffold-project marker block: "
            f"{operation_path}"
        )
    marker_id = match.group(1)
    marker_suffix = " -->" if basename == "README.md" else ""
    begin_marker = f"{marker_prefix} scaffold-project:begin:{marker_id}{marker_suffix}"
    end_marker = f"{marker_prefix} scaffold-project:end:{marker_id}{marker_suffix}"
    if begin_marker in original_text or end_marker in original_text:
        _error(
            f"semantic_merge marker identity already exists: "
            f"{operation_path}: {marker_id}"
        )


def _validate_semantic_merge_candidate(
    operation_path: str,
    target_path: Path,
    before: dict[str, Any],
    candidate_bytes: bytes,
    candidate_mode: str,
) -> None:
    original_bytes = _read_bound_regular_file(target_path, before)
    _validate_semantic_merge_bytes(
        operation_path,
        before,
        original_bytes,
        candidate_bytes,
        candidate_mode,
    )


def _collect_directory_preconditions(
    target: Path, target_state: dict[str, Any], operation_paths: list[str]
) -> list[dict[str, Any]]:
    wanted: set[str] = {"."}
    for operation_path in operation_paths:
        parts = PurePosixPath(operation_path).parts[:-1]
        for index in range(1, len(parts) + 1):
            wanted.add(PurePosixPath(*parts[:index]).as_posix())
    conditions: list[dict[str, Any]] = []
    if target_state["state"] == "absent":
        return [{"path": path, "state": "absent"} for path in sorted(wanted)]
    for relative in sorted(wanted):
        path = target if relative == "." else target / relative
        try:
            result = os.lstat(path)
        except FileNotFoundError:
            conditions.append({"path": relative, "state": "absent"})
            continue
        if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode):
            _error(f"planned parent is not a real directory: {path}")
        conditions.append(
            {
                "path": relative,
                "state": "directory",
                "device": result.st_dev,
                "inode": result.st_ino,
                "mode": _mode_string(result.st_mode),
            }
        )
    return conditions


def finalize_bundle(target: Path, bundle: Path) -> dict[str, Any]:
    canonical_target, target_state = _canonical_target(target)
    bundle = _private_bundle_path(bundle, canonical_target, create=True)
    if (bundle / "journal.json").exists():
        _error("cannot finalize a bundle that already has an apply journal")
    draft_result = _ensure_regular_lstat(
        bundle / "manifest.draft.json",
        "manifest draft",
    )
    if _mode_string(draft_result.st_mode) != "0600":
        _error(
            f"manifest draft permissions must be 0600: {bundle / 'manifest.draft.json'}"
        )
    draft = _load_json(bundle / "manifest.draft.json", "manifest draft")
    _validate_common_draft(draft)
    candidate_inputs = _validate_candidate_manifests(
        bundle,
        draft,
        finalized=False,
    )
    architecture = dict(draft["project"]["architecture"])
    architecture["sources"] = _validate_architecture_sources(architecture)
    architecture["handoff"], handoff_data = _validate_architecture_handoff(architecture)
    _validate_app_stack_bindings(draft, handoff_data, candidate_inputs)
    _validate_architecture_operation_disjoint(
        canonical_target,
        architecture["sources"],
        draft["operations"],
    )
    project = dict(draft["project"])
    project["architecture"] = architecture
    directory_preconditions = _collect_directory_preconditions(
        canonical_target,
        target_state,
        [operation["path"] for operation in draft["operations"]],
    )

    payload_directory = _private_subdirectory(bundle, "payloads", create=True)
    finalized_operations: list[dict[str, Any]] = []
    for operation in sorted(draft["operations"], key=lambda item: item["path"]):
        candidate_path = bundle / operation["candidate"]
        _ensure_private_parent_chain(bundle, candidate_path.parent)
        candidate_bytes = _read_private_regular_file(candidate_path, "candidate")
        if UNRESOLVED_PLACEHOLDER.search(candidate_bytes):
            _error(f"candidate contains an unresolved placeholder: {candidate_path}")
        payload_digest = _sha256_bytes(candidate_bytes)
        payload_path = payload_directory / payload_digest
        if payload_path.exists():
            payload_result = _ensure_regular_lstat(payload_path, "payload")
            if _mode_string(payload_result.st_mode) != "0600":
                _error(f"payload permissions must be 0600: {payload_path}")
            if _sha256_path(payload_path) != payload_digest:
                _error(f"existing payload is corrupt: {payload_path}")
        else:
            descriptor = os.open(
                payload_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    stream.write(candidate_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(payload_path, 0o600)
            finally:
                os.close(descriptor)
        target_path = canonical_target / operation["path"]
        before = _inspect_file(target_path)
        after = {
            "sha256": payload_digest,
            "file_type": "regular",
            "mode": operation["mode"],
        }
        action = operation["action"]
        if action == "create" and before["state"] != "absent":
            _error(
                f"create operation collides with an existing path: {operation['path']}"
            )
        if action == "semantic_merge":
            if before["state"] != "present":
                _error(f"semantic_merge requires an existing file: {operation['path']}")
            if (
                before["sha256"] == payload_digest
                and before["mode"] == operation["mode"]
            ):
                _error(
                    f"semantic_merge is unnecessary; use unchanged: {operation['path']}"
                )
            _validate_semantic_merge_candidate(
                operation["path"],
                target_path,
                before,
                candidate_bytes,
                operation["mode"],
            )
        if action == "unchanged":
            if (
                before["state"] != "present"
                or before["sha256"] != payload_digest
                or before["mode"] != operation["mode"]
            ):
                _error(
                    f"unchanged operation does not match the target: {operation['path']}"
                )
        finalized_operations.append(
            {
                "path": operation["path"],
                "action": action,
                "owner": operation["owner"],
                "materialization_unit_id": operation["materialization_unit_id"],
                "candidate_set_id": operation["candidate_set_id"],
                "payload_sha256": payload_digest,
                "before": before,
                "after": after,
            }
        )

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "project": project,
        "target": {
            "path": str(canonical_target),
            "precondition": target_state,
        },
        "capabilities": draft["capabilities"],
        "materialization_units": draft["materialization_units"],
        "runtime_units": draft["runtime_units"],
        "external_services": draft["external_services"],
        "candidate_sets": draft["candidate_sets"],
        "directory_preconditions": directory_preconditions,
        "operations": finalized_operations,
        "validations": draft["validations"],
        "execution": draft["execution"],
        "safety": draft["safety"],
    }
    manifest["bundle_digest"] = _sha256_bytes(_canonical_bytes(manifest))
    _write_private_json(bundle / "manifest.json", manifest)
    result = validate_bundle(canonical_target, bundle)
    result["action"] = "finalize"
    return result


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    _require_exact_keys(
        manifest,
        required={
            "schema_version",
            "project",
            "target",
            "capabilities",
            "materialization_units",
            "runtime_units",
            "external_services",
            "candidate_sets",
            "directory_preconditions",
            "operations",
            "validations",
            "execution",
            "safety",
            "bundle_digest",
        },
        label="manifest",
    )
    if manifest["schema_version"] != SCHEMA_VERSION:
        _error(f"unsupported schema_version: {manifest['schema_version']}")
    if not _is_digest(manifest["bundle_digest"]):
        _error("manifest.bundle_digest is invalid")
    digest_input = dict(manifest)
    expected = digest_input.pop("bundle_digest")
    actual = _sha256_bytes(_canonical_bytes(digest_input))
    if actual != expected:
        _error("manifest bundle digest mismatch")

    project = _require_dict(manifest["project"], "project")
    _require_exact_keys(
        project,
        required={"name", "repository_shape", "architecture"},
        label="project",
    )
    architecture = _require_dict(project["architecture"], "project.architecture")
    _require_exact_keys(
        architecture,
        required={"approval", "approval_reference", "handoff", "sources"},
        label="project.architecture",
    )

    candidate_manifest_by_id: dict[str, str] = {}
    for index, candidate_set_value in enumerate(
        _require_list(manifest["candidate_sets"], "candidate_sets")
    ):
        candidate_set = _require_dict(candidate_set_value, f"candidate_sets[{index}]")
        candidate_set_id = _require_string(
            candidate_set.get("id"), f"candidate_sets[{index}].id"
        )
        candidate_manifest_by_id[candidate_set_id] = _normalize_relative_path(
            candidate_set.get("manifest"),
            f"candidate_sets[{index}].manifest",
        )
    projected_operations: list[dict[str, Any]] = []
    for index, operation_value in enumerate(
        _require_list(manifest["operations"], "operations")
    ):
        operation = _require_dict(operation_value, f"operations[{index}]")
        candidate_set_id = _require_string(
            operation.get("candidate_set_id"),
            f"operations[{index}].candidate_set_id",
        )
        candidate_manifest = candidate_manifest_by_id.get(candidate_set_id)
        if candidate_manifest is None:
            _error(f"operations[{index}] references an unknown candidate set")
        projected_operations.append(
            {
                "path": operation.get("path"),
                "action": operation.get("action"),
                "owner": operation.get("owner"),
                "materialization_unit_id": operation.get("materialization_unit_id"),
                "candidate_set_id": candidate_set_id,
                "candidate": (
                    PurePosixPath(candidate_manifest).parent / "files" / str(index)
                ).as_posix(),
                "mode": _require_dict(
                    operation.get("after"), f"operations[{index}].after"
                ).get("mode"),
            }
        )
    draft_projection = {
        "schema_version": manifest["schema_version"],
        "project": {
            "name": manifest["project"]["name"],
            "repository_shape": manifest["project"]["repository_shape"],
            "architecture": {
                "approval": manifest["project"]["architecture"]["approval"],
                "approval_reference": manifest["project"]["architecture"][
                    "approval_reference"
                ],
                "handoff": manifest["project"]["architecture"]["handoff"],
                "sources": [
                    {"path": source["path"], "sha256": source["sha256"]}
                    for source in manifest["project"]["architecture"]["sources"]
                ],
            },
        },
        "capabilities": manifest["capabilities"],
        "materialization_units": manifest["materialization_units"],
        "runtime_units": manifest["runtime_units"],
        "external_services": manifest["external_services"],
        "candidate_sets": manifest["candidate_sets"],
        "operations": projected_operations,
        "validations": manifest["validations"],
        "execution": manifest["execution"],
        "safety": manifest["safety"],
    }
    _validate_common_draft(draft_projection)
    for index, source_value in enumerate(
        manifest["project"]["architecture"]["sources"]
    ):
        source = _require_dict(source_value, f"architecture source {index}")
        _require_exact_keys(
            source,
            required={"path", "sha256", "device", "inode", "mode"},
            label=f"architecture source {index}",
        )
        _require_int(source["device"], f"architecture source {index}.device")
        _require_int(source["inode"], f"architecture source {index}.inode")
    target = _require_dict(manifest["target"], "target")
    _require_exact_keys(target, required={"path", "precondition"}, label="target")
    target_path = Path(_require_string(target["path"], "target.path"))
    if not target_path.is_absolute():
        _error("target.path must be absolute in the private executable manifest")
    precondition = _require_dict(target["precondition"], "target.precondition")
    if precondition.get("state") == "absent":
        _require_exact_keys(
            precondition,
            required={"state", "parent_device", "parent_inode"},
            label="target.precondition",
        )
        _require_int(precondition["parent_device"], "target.precondition.parent_device")
        _require_int(precondition["parent_inode"], "target.precondition.parent_inode")
    elif precondition.get("state") == "directory":
        _require_exact_keys(
            precondition,
            required={
                "state",
                "device",
                "inode",
                "mode",
                "parent_device",
                "parent_inode",
            },
            label="target.precondition",
        )
        for key in ("device", "inode", "parent_device", "parent_inode"):
            _require_int(precondition[key], f"target.precondition.{key}")
    else:
        _error("target.precondition.state is invalid")

    directory_conditions = _require_list(
        manifest["directory_preconditions"], "directory_preconditions"
    )
    directory_paths: list[str] = []
    for index, condition_value in enumerate(directory_conditions):
        condition = _require_dict(condition_value, f"directory_preconditions[{index}]")
        if condition.get("path") == ".":
            path = "."
        else:
            path = _normalize_relative_path(
                condition.get("path"), f"directory_preconditions[{index}].path"
            )
        directory_paths.append(path)
        if condition.get("state") == "absent":
            _require_exact_keys(
                condition,
                required={"path", "state"},
                label=f"directory_preconditions[{index}]",
            )
        elif condition.get("state") == "directory":
            _require_exact_keys(
                condition,
                required={"path", "state", "device", "inode", "mode"},
                label=f"directory_preconditions[{index}]",
            )
            _require_int(
                condition["device"], f"directory_preconditions[{index}].device"
            )
            _require_int(condition["inode"], f"directory_preconditions[{index}].inode")
        else:
            _error(f"directory_preconditions[{index}].state is invalid")
    if len(directory_paths) != len(set(directory_paths)):
        _error("directory precondition paths must be unique")

    operation_paths: list[str] = []
    for index, operation_value in enumerate(manifest["operations"]):
        operation = _require_dict(operation_value, f"operations[{index}]")
        _require_exact_keys(
            operation,
            required={
                "path",
                "action",
                "owner",
                "materialization_unit_id",
                "candidate_set_id",
                "payload_sha256",
                "before",
                "after",
            },
            label=f"operations[{index}]",
        )
        path = _normalize_relative_path(operation["path"], f"operations[{index}].path")
        operation_paths.append(path)
        if operation["action"] not in ACTIONS:
            _error(f"operations[{index}].action is invalid")
        if operation["owner"] not in SUPPORTED_OWNERS:
            _error(f"operations[{index}].owner is unsupported")
        if not _is_digest(operation["payload_sha256"]):
            _error(f"operations[{index}].payload_sha256 is invalid")
        before = _require_dict(operation["before"], f"operations[{index}].before")
        if before.get("state") == "absent":
            _require_exact_keys(
                before,
                required={"state"},
                label=f"operations[{index}].before",
            )
        elif before.get("state") == "present":
            _require_exact_keys(
                before,
                required={
                    "state",
                    "sha256",
                    "file_type",
                    "mode",
                    "size",
                    "device",
                    "inode",
                },
                label=f"operations[{index}].before",
            )
            if not _is_digest(before["sha256"]) or before["file_type"] != "regular":
                _error(f"operations[{index}].before is invalid")
            _parse_mode(before["mode"], f"operations[{index}].before.mode")
            size = _require_int(before["size"], f"operations[{index}].before.size")
            if size < 0:
                _error(f"operations[{index}].before.size must not be negative")
            _require_int(before["device"], f"operations[{index}].before.device")
            _require_int(before["inode"], f"operations[{index}].before.inode")
        else:
            _error(f"operations[{index}].before.state is invalid")
        after = _require_dict(operation["after"], f"operations[{index}].after")
        _require_exact_keys(
            after,
            required={"sha256", "file_type", "mode"},
            label=f"operations[{index}].after",
        )
        if (
            after["sha256"] != operation["payload_sha256"]
            or after["file_type"] != "regular"
        ):
            _error(f"operations[{index}].after is invalid")
        _parse_mode(after["mode"], f"operations[{index}].after.mode")
        action = operation["action"]
        if action == "create" and before["state"] != "absent":
            _error(f"operations[{index}] create requires absent before state")
        if action == "semantic_merge":
            if before["state"] != "present":
                _error(
                    f"operations[{index}] semantic_merge requires present before state"
                )
            if after["mode"] != before["mode"] or after["sha256"] == before["sha256"]:
                _error(f"operations[{index}] semantic_merge relation is invalid")
        if action == "unchanged" and (
            before["state"] != "present"
            or after["sha256"] != before["sha256"]
            or after["mode"] != before["mode"]
        ):
            _error(f"operations[{index}] unchanged relation is invalid")
    if operation_paths != sorted(operation_paths):
        _error("operations must be sorted by normalized path")
    if len(operation_paths) != len(set(operation_paths)):
        _error("operation paths must be unique")
    reserved = {
        _normalize_relative_path(path, "safety.reserved_paths[]")
        for path in manifest["safety"]["reserved_paths"]
    }
    _validate_path_set(operation_paths, reserved)


def _validate_payloads(bundle: Path, manifest: dict[str, Any]) -> None:
    _private_subdirectory(bundle, "payloads", create=False)
    for operation in manifest["operations"]:
        payload = bundle / "payloads" / operation["payload_sha256"]
        result = _ensure_regular_lstat(payload, "payload")
        if _mode_string(result.st_mode) != "0600":
            _error(f"payload permissions must be 0600: {payload}")
        if _sha256_path(payload) != operation["payload_sha256"]:
            _error(f"payload digest mismatch: {payload}")


def _validate_operation_payload_relations(
    bundle: Path, manifest: dict[str, Any]
) -> None:
    for operation in manifest["operations"]:
        if operation["action"] != "semantic_merge":
            continue
        payload = _read_payload(bundle, operation["payload_sha256"])
        before = operation["before"]
        original_size = before["size"]
        original_bytes = payload[:original_size]
        _validate_semantic_merge_bytes(
            operation["path"],
            before,
            original_bytes,
            payload,
            operation["after"]["mode"],
        )


def _validate_architecture_manifest(manifest: dict[str, Any]) -> None:
    for source in manifest["project"]["architecture"]["sources"]:
        path = Path(source["path"])
        result, digest = _regular_file_identity_and_digest(
            path,
            "architecture source",
        )
        if (
            result.st_dev != source["device"]
            or result.st_ino != source["inode"]
            or _mode_string(result.st_mode) != source["mode"]
            or digest != source["sha256"]
        ):
            _error(f"architecture source changed after finalization: {path}")


def _load_validated_bundle(
    target: Path, bundle: Path
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    supplied_target, _ = _canonical_target(target)
    bundle = _private_bundle_path(bundle, supplied_target, create=False)
    manifest_result = _ensure_regular_lstat(bundle / "manifest.json", "manifest")
    if _mode_string(manifest_result.st_mode) != "0600":
        _error(f"manifest permissions must be 0600: {bundle / 'manifest.json'}")
    manifest = _load_json(bundle / "manifest.json", "manifest")
    _validate_manifest_shape(manifest)
    expected_target = Path(manifest["target"]["path"])
    if supplied_target != expected_target:
        _error(
            f"target does not match finalized bundle: {supplied_target} != {expected_target}"
        )
    _validate_payloads(bundle, manifest)
    candidate_inputs = _validate_candidate_manifests(
        bundle,
        manifest,
        finalized=True,
    )
    _validate_operation_payload_relations(bundle, manifest)
    _validate_architecture_manifest(manifest)
    _, handoff_data = _validate_architecture_handoff(
        manifest["project"]["architecture"]
    )
    _validate_app_stack_bindings(
        manifest,
        handoff_data,
        candidate_inputs,
    )
    result = {
        "ok": True,
        "bundle_digest": manifest["bundle_digest"],
        "operation_count": len(manifest["operations"]),
        "target": str(expected_target),
    }
    return result, manifest, bundle


def validate_bundle(target: Path, bundle: Path) -> dict[str, Any]:
    result, _, _ = _load_validated_bundle(target, bundle)
    return result


def _condition_matches(path: Path, condition: dict[str, Any]) -> bool:
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return condition["state"] == "absent"
    if condition["state"] == "absent":
        return False
    if condition["state"] == "directory":
        return (
            stat.S_ISDIR(result.st_mode)
            and not stat.S_ISLNK(result.st_mode)
            and result.st_dev == condition["device"]
            and result.st_ino == condition["inode"]
            and _mode_string(result.st_mode) == condition["mode"]
        )
    if condition["state"] == "present":
        return (
            stat.S_ISREG(result.st_mode)
            and result.st_dev == condition["device"]
            and result.st_ino == condition["inode"]
            and _mode_string(result.st_mode) == condition["mode"]
            and _sha256_path(path) == condition["sha256"]
        )
    return False


def _after_matches(path: Path, after: dict[str, Any]) -> bool:
    try:
        result = os.lstat(path)
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(result.st_mode)
        and not stat.S_ISLNK(result.st_mode)
        and _mode_string(result.st_mode) == after["mode"]
        and _sha256_path(path) == after["sha256"]
    )


def _operation_state(target: Path, operation: dict[str, Any]) -> str:
    path = target / operation["path"]
    if _after_matches(path, operation["after"]):
        return "after"
    if _condition_matches(path, operation["before"]):
        return "before"
    return "conflict"


def status_bundle(target: Path, bundle: Path) -> tuple[dict[str, Any], int]:
    validation, manifest, bundle = _load_validated_bundle(target, bundle)
    canonical_target = Path(manifest["target"]["path"])
    resume_journal = _load_resume_journal(bundle, manifest)
    created_directories = _created_directory_map(resume_journal)
    _verify_target_precondition(
        canonical_target,
        manifest["target"]["precondition"],
        created_directories,
    )
    _verify_directory_preconditions(
        canonical_target,
        manifest,
        created_directories,
    )
    states = [
        {
            "path": operation["path"],
            "action": operation["action"],
            "state": _operation_state(canonical_target, operation),
        }
        for operation in manifest["operations"]
    ]
    counts = {
        state: sum(item["state"] == state for item in states)
        for state in ("before", "after", "conflict")
    }
    result = {**validation, "action": "status", "counts": counts, "operations": states}
    return result, 2 if counts["conflict"] else 0


def _directory_identity(result: os.stat_result, path: str) -> dict[str, Any]:
    return {
        "path": path,
        "device": result.st_dev,
        "inode": result.st_ino,
        "mode": _mode_string(result.st_mode),
    }


def _directory_identity_matches(
    result: os.stat_result,
    expected: dict[str, Any],
) -> bool:
    return (
        stat.S_ISDIR(result.st_mode)
        and not stat.S_ISLNK(result.st_mode)
        and result.st_dev == expected["device"]
        and result.st_ino == expected["inode"]
        and _mode_string(result.st_mode) == expected["mode"]
    )


def _verify_target_precondition(
    target: Path,
    precondition: dict[str, Any],
    created_directories: dict[str, dict[str, Any]],
) -> None:
    parent_result = os.lstat(target.parent)
    if (
        parent_result.st_dev != precondition["parent_device"]
        or parent_result.st_ino != precondition["parent_inode"]
    ):
        _error("target parent identity changed")
    if precondition["state"] == "absent":
        try:
            result = os.lstat(target)
        except FileNotFoundError:
            if "." in created_directories:
                _error("journaled target directory disappeared")
            return
        expected = created_directories.get(".")
        if expected is None:
            _error("target appeared after finalization")
        if not _directory_identity_matches(result, expected):
            _error("journaled target directory identity changed")
        return
    result = os.lstat(target)
    if (
        not stat.S_ISDIR(result.st_mode)
        or stat.S_ISLNK(result.st_mode)
        or result.st_dev != precondition["device"]
        or result.st_ino != precondition["inode"]
        or _mode_string(result.st_mode) != precondition["mode"]
    ):
        _error("target directory identity changed")


def _verify_directory_preconditions(
    target: Path,
    manifest: dict[str, Any],
    created_directories: dict[str, dict[str, Any]],
) -> None:
    absent_condition_paths = {
        condition["path"]
        for condition in manifest["directory_preconditions"]
        if condition["state"] == "absent"
    }
    unknown = sorted(created_directories.keys() - absent_condition_paths)
    if unknown:
        _error(f"apply journal contains unknown directories: {', '.join(unknown)}")
    for condition in manifest["directory_preconditions"]:
        relative = condition["path"]
        path = target if relative == "." else target / relative
        try:
            result = os.lstat(path)
        except FileNotFoundError:
            if condition["state"] == "directory":
                _error(f"planned directory disappeared: {relative}")
            if relative in created_directories:
                _error(f"journaled directory disappeared: {relative}")
            continue
        if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode):
            _error(f"planned parent is not a real directory: {relative}")
        if condition["state"] == "absent":
            expected = created_directories.get(relative)
            if expected is None:
                _error(f"planned directory appeared after finalization: {relative}")
            if not _directory_identity_matches(result, expected):
                _error(f"journaled directory identity changed: {relative}")
            continue
        if (
            result.st_dev != condition["device"]
            or result.st_ino != condition["inode"]
            or _mode_string(result.st_mode) != condition["mode"]
        ):
            _error(f"planned directory identity changed: {relative}")


def _preflight(
    target: Path,
    manifest: dict[str, Any],
    created_directories: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    precondition = manifest["target"]["precondition"]
    _verify_target_precondition(target, precondition, created_directories)
    _verify_directory_preconditions(
        target,
        manifest,
        created_directories,
    )
    states: list[dict[str, Any]] = []
    for operation in manifest["operations"]:
        state = _operation_state(target, operation)
        states.append({"path": operation["path"], "state": state})
    conflicts = [item["path"] for item in states if item["state"] == "conflict"]
    if conflicts:
        _error(f"preflight conflicts block all writes: {', '.join(conflicts)}")
    return states


def _load_resume_journal(
    bundle: Path,
    manifest: dict[str, Any],
) -> dict[str, Any] | None:
    journal_path = bundle / "journal.json"
    try:
        result = os.lstat(journal_path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(result.st_mode) or stat.S_ISLNK(result.st_mode):
        _error(f"apply journal must be a regular file: {journal_path}")
    if _mode_string(result.st_mode) != "0600":
        _error(f"apply journal permissions must be 0600: {journal_path}")
    journal = _load_json(journal_path, "apply journal")
    _require_exact_keys(
        journal,
        required={
            "schema_version",
            "bundle_digest",
            "target",
            "status",
            "operations",
            "created_directories",
        },
        label="apply journal",
    )
    if (
        journal["schema_version"] != SCHEMA_VERSION
        or journal["bundle_digest"] != manifest["bundle_digest"]
        or journal["target"] != manifest["target"]["path"]
        or journal["status"] not in {"applying", "partial", "complete"}
    ):
        _error("apply journal does not authorize resuming this bundle")
    journal_operations = _require_list(
        journal["operations"], "apply journal.operations"
    )
    if len(journal_operations) != len(manifest["operations"]):
        _error("apply journal operation count does not match the bundle")
    for index, item_value in enumerate(journal_operations):
        item = _require_dict(item_value, f"apply journal.operations[{index}]")
        _require_exact_keys(
            item,
            required={"path", "state"},
            label=f"apply journal.operations[{index}]",
        )
        if item["path"] != manifest["operations"][index]["path"] or item[
            "state"
        ] not in {
            "pending",
            "already-applied",
            "applied",
            "unchanged",
            "conflict",
        }:
            _error(f"apply journal operation {index} is invalid")
    created_values = _require_list(
        journal["created_directories"],
        "apply journal.created_directories",
    )
    seen: set[str] = set()
    for index, entry_value in enumerate(created_values):
        entry = _require_dict(
            entry_value,
            f"apply journal.created_directories[{index}]",
        )
        _require_exact_keys(
            entry,
            required={"path", "device", "inode", "mode"},
            label=f"apply journal.created_directories[{index}]",
        )
        path = (
            "."
            if entry["path"] == "."
            else _normalize_relative_path(
                entry["path"],
                f"apply journal.created_directories[{index}].path",
            )
        )
        if path in seen:
            _error(f"duplicate journaled directory: {path}")
        seen.add(path)
        _require_int(entry["device"], f"journal directory {path}.device")
        _require_int(entry["inode"], f"journal directory {path}.inode")
        if entry["mode"] != "0755":
            _error(f"journal directory {path}.mode must be 0755")
    return journal


def _created_directory_map(
    journal: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if journal is None:
        return {}
    return {entry["path"]: entry for entry in journal["created_directories"]}


def _rename_directory_noreplace(
    parent_descriptor: int,
    source_name: str,
    target_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
        flags = 0x00000004  # RENAME_EXCL
    else:
        function = getattr(libc, "renameat2", None)
        flags = 0x00000001  # RENAME_NOREPLACE
    if function is None:
        _error("atomic no-replace directory publication is unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        parent_descriptor,
        os.fsencode(source_name),
        parent_descriptor,
        os.fsencode(target_name),
        flags,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ScaffoldError("target appeared after preflight")
    if error_number in {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP}:
        _error("filesystem does not support atomic no-replace directory publication")
    raise ScaffoldError(
        f"cannot safely publish target directory: {os.strerror(error_number)}"
    )


def _open_root(
    target: Path,
    precondition: dict[str, Any],
    expected_created_root: dict[str, Any] | None,
) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if precondition["state"] == "absent" and expected_created_root is None:
        parent_descriptor = os.open(
            target.parent, os.O_RDONLY | os.O_DIRECTORY | nofollow
        )
        staging_name: str | None = None
        descriptor: int | None = None
        try:
            parent_result = os.fstat(parent_descriptor)
            if (
                parent_result.st_dev != precondition["parent_device"]
                or parent_result.st_ino != precondition["parent_inode"]
            ):
                _error("target parent identity changed before creation")
            staging_name = f".scaffold-project-{secrets.token_hex(12)}.directory"
            try:
                os.mkdir(staging_name, mode=0o755, dir_fd=parent_descriptor)
            except FileExistsError as error:
                raise ScaffoldError(
                    "private staging directory unexpectedly exists"
                ) from error
            descriptor = os.open(
                staging_name,
                os.O_RDONLY | os.O_DIRECTORY | nofollow,
                dir_fd=parent_descriptor,
            )
            staged_result = os.fstat(descriptor)
            if not stat.S_ISDIR(staged_result.st_mode):
                _error("private staging path is not a directory")
            os.fsync(descriptor)
            _rename_directory_noreplace(
                parent_descriptor,
                staging_name,
                target.name,
            )
            staging_name = None
            os.fsync(parent_descriptor)
            return descriptor
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            if staging_name is not None:
                try:
                    os.rmdir(staging_name, dir_fd=parent_descriptor)
                except FileNotFoundError:
                    pass
            raise
        finally:
            os.close(parent_descriptor)
    try:
        descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY | nofollow)
    except OSError as error:
        raise ScaffoldError(f"cannot safely open target directory: {target}") from error
    result = os.fstat(descriptor)
    if precondition["state"] == "directory":
        identity_matches = _directory_identity_matches(result, precondition)
    elif expected_created_root is not None:
        identity_matches = _directory_identity_matches(
            result,
            expected_created_root,
        )
    else:
        identity_matches = False
    if not identity_matches:
        os.close(descriptor)
        _error("target directory identity changed")
    return descriptor


def _verify_open_root_path(root_descriptor: int, target: Path) -> None:
    descriptor_result = os.fstat(root_descriptor)
    try:
        path_result = os.lstat(target)
    except OSError as error:
        raise ScaffoldError("target directory identity changed") from error
    if (
        not stat.S_ISDIR(path_result.st_mode)
        or stat.S_ISLNK(path_result.st_mode)
        or path_result.st_dev != descriptor_result.st_dev
        or path_result.st_ino != descriptor_result.st_ino
        or _mode_string(path_result.st_mode) != _mode_string(descriptor_result.st_mode)
    ):
        _error("target directory identity changed")


def _directory_condition_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        condition["path"]: condition
        for condition in manifest["directory_preconditions"]
    }


def _open_parent_directory(
    root_descriptor: int,
    operation_path: str,
    directory_conditions: dict[str, dict[str, Any]],
    created_directories: dict[str, dict[str, Any]],
    record_created_directory: Callable[[str, os.stat_result], None],
) -> tuple[int, str]:
    parts = list(PurePosixPath(operation_path).parts)
    filename = parts.pop()
    current_descriptor = os.dup(root_descriptor)
    current_path_parts: list[str] = []
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        for part in parts:
            current_path_parts.append(part)
            relative = PurePosixPath(*current_path_parts).as_posix()
            condition = directory_conditions.get(relative)
            if condition is None:
                _error(f"missing directory precondition for {relative}")
            if condition["state"] == "absent" and relative not in created_directories:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=current_descriptor)
                except FileExistsError as error:
                    raise ScaffoldError(
                        f"directory appeared during apply: {relative}"
                    ) from error
                os.fsync(current_descriptor)
            next_descriptor = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | nofollow,
                dir_fd=current_descriptor,
            )
            result = os.fstat(next_descriptor)
            if condition["state"] == "directory":
                if not _directory_identity_matches(result, condition):
                    os.close(next_descriptor)
                    _error(f"directory identity changed during apply: {relative}")
            else:
                expected = created_directories.get(relative)
                if expected is None:
                    os.fchmod(next_descriptor, 0o755)
                    result = os.fstat(next_descriptor)
                    try:
                        record_created_directory(relative, result)
                    except Exception:
                        os.close(next_descriptor)
                        raise
                elif not _directory_identity_matches(result, expected):
                    os.close(next_descriptor)
                    _error(f"journaled directory identity changed: {relative}")
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        return current_descriptor, filename
    except Exception:
        os.close(current_descriptor)
        raise


def _read_payload(bundle: Path, digest: str) -> bytes:
    payload_path = bundle / "payloads" / digest
    try:
        descriptor = os.open(
            payload_path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise ScaffoldError(f"cannot safely open payload: {payload_path}") from error
    try:
        result = os.fstat(descriptor)
        if (
            not stat.S_ISREG(result.st_mode)
            or _mode_string(result.st_mode) != "0600"
            or _sha256_fd(descriptor) != digest
        ):
            _error(f"payload changed during apply: {payload_path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_create(
    parent_descriptor: int, filename: str, payload: bytes, mode: int
) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    temporary = f".scaffold-project-{secrets.token_hex(12)}.tmp"
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
        mode,
        dir_fd=parent_descriptor,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, mode)
        try:
            os.link(
                temporary,
                filename,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise ScaffoldError(f"target appeared during create: {filename}") from error
        os.unlink(temporary, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass


def _write_semantic_merge(
    parent_descriptor: int,
    filename: str,
    operation_path: str,
    payload: bytes,
    mode: int,
    before: dict[str, Any],
    bundle: Path,
) -> None:
    if fcntl is None:  # pragma: no cover - apply is blocked before this on Windows.
        _error("semantic merge apply requires POSIX file locking")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    target_descriptor = os.open(
        filename,
        os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0),
        dir_fd=parent_descriptor,
    )
    temporary = f".scaffold-project-{secrets.token_hex(12)}.tmp"
    try:
        fcntl.flock(target_descriptor, fcntl.LOCK_EX)
        target_result = os.fstat(target_descriptor)
        if (
            not stat.S_ISREG(target_result.st_mode)
            or target_result.st_dev != before["device"]
            or target_result.st_ino != before["inode"]
            or _mode_string(target_result.st_mode) != before["mode"]
            or target_result.st_size != before["size"]
            or _sha256_fd(target_descriptor) != before["sha256"]
        ):
            _error(f"semantic merge precondition changed: {filename}")
        path_result = os.stat(filename, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            path_result.st_dev != target_result.st_dev
            or path_result.st_ino != target_result.st_ino
        ):
            _error(f"semantic merge path identity changed: {filename}")

        os.lseek(target_descriptor, 0, os.SEEK_SET)
        original_chunks: list[bytes] = []
        while chunk := os.read(target_descriptor, 1024 * 1024):
            original_chunks.append(chunk)
        original_bytes = b"".join(original_chunks)
        _validate_semantic_merge_bytes(
            operation_path,
            before,
            original_bytes,
            payload,
            _mode_string(mode),
        )

        backup_directory = _private_subdirectory(bundle, "backups", create=True)
        backup_path = backup_directory / before["sha256"]
        try:
            backup_result = os.lstat(backup_path)
        except FileNotFoundError:
            os.lseek(target_descriptor, 0, os.SEEK_SET)
            chunks: list[bytes] = []
            while chunk := os.read(target_descriptor, 1024 * 1024):
                chunks.append(chunk)
            descriptor = os.open(
                backup_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
            )
            try:
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    for chunk in chunks:
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                os.close(descriptor)
            backup_directory_descriptor = os.open(
                backup_directory,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(backup_directory_descriptor)
            finally:
                os.close(backup_directory_descriptor)
            backup_result = _ensure_regular_lstat(backup_path, "semantic merge backup")
        if (
            not stat.S_ISREG(backup_result.st_mode)
            or stat.S_ISLNK(backup_result.st_mode)
            or _mode_string(backup_result.st_mode) != "0600"
        ):
            _error(f"semantic merge backup must be a 0600 regular file: {backup_path}")
        if _sha256_path(backup_path) != before["sha256"]:
            _error(f"semantic merge backup is corrupt: {backup_path}")

        temporary_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
            mode,
            dir_fd=parent_descriptor,
        )
        try:
            with os.fdopen(temporary_descriptor, "wb", closefd=False) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.fchmod(temporary_descriptor, mode)
            final_result = os.stat(
                filename, dir_fd=parent_descriptor, follow_symlinks=False
            )
            if (
                final_result.st_dev != target_result.st_dev
                or final_result.st_ino != target_result.st_ino
                or _sha256_fd(target_descriptor) != before["sha256"]
            ):
                _error(
                    f"semantic merge target changed immediately before replace: {filename}"
                )
            os.replace(
                temporary,
                filename,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
        finally:
            os.close(temporary_descriptor)
    finally:
        try:
            fcntl.flock(target_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(target_descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass


def _acquire_bundle_apply_lock(bundle: Path) -> int:
    if fcntl is None:  # pragma: no cover - apply is blocked before this on Windows.
        _error("apply locking requires POSIX file locking")
    lock_path = bundle / "apply.lock"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | nofollow,
            0o600,
        )
    except OSError as error:
        raise ScaffoldError(f"cannot safely open apply lock: {lock_path}") from error
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode) or _mode_string(result.st_mode) != "0600":
            _error(f"apply lock must be a 0600 regular file: {lock_path}")
        path_result = os.stat(lock_path, follow_symlinks=False)
        if path_result.st_dev != result.st_dev or path_result.st_ino != result.st_ino:
            _error(f"apply lock path identity changed: {lock_path}")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ScaffoldError(
                f"another scaffold apply is already in progress: {bundle}"
            ) from error
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def apply_bundle(
    target: Path,
    bundle: Path,
    expected_digest: str,
    *,
    before_operation: Callable[[int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if sys.platform not in SUPPORTED_PLATFORMS:
        _error("apply is supported only on macOS and Linux")
    supplied_target, _ = _canonical_target(target)
    canonical_bundle = _private_bundle_path(bundle, supplied_target, create=False)
    lock_descriptor = _acquire_bundle_apply_lock(canonical_bundle)
    try:
        return _apply_bundle_locked(
            target,
            canonical_bundle,
            expected_digest,
            before_operation=before_operation,
        )
    finally:
        if fcntl is not None:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        os.close(lock_descriptor)


def _apply_bundle_locked(
    target: Path,
    bundle: Path,
    expected_digest: str,
    *,
    before_operation: Callable[[int, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    validation, manifest, bundle = _load_validated_bundle(target, bundle)
    if expected_digest != validation["bundle_digest"]:
        _error("expected digest does not match the finalized bundle")
    canonical_target = Path(manifest["target"]["path"])
    resume_journal = _load_resume_journal(bundle, manifest)
    created_directories = _created_directory_map(resume_journal)
    states = _preflight(
        canonical_target,
        manifest,
        created_directories,
    )
    state_by_path = {item["path"]: item["state"] for item in states}
    journal = {
        "schema_version": SCHEMA_VERSION,
        "bundle_digest": manifest["bundle_digest"],
        "target": str(canonical_target),
        "status": "applying",
        "created_directories": [
            created_directories[path] for path in sorted(created_directories)
        ],
        "operations": [
            {
                "path": operation["path"],
                "state": (
                    "already-applied"
                    if state_by_path[operation["path"]] == "after"
                    else "pending"
                ),
            }
            for operation in manifest["operations"]
        ],
    }
    _write_private_json(bundle / "journal.json", journal)

    def record_created_directory(relative: str, result: os.stat_result) -> None:
        entry = _directory_identity(result, relative)
        created_directories[relative] = entry
        journal["created_directories"] = [
            created_directories[path] for path in sorted(created_directories)
        ]
        _write_private_json(bundle / "journal.json", journal)

    root_descriptor = _open_root(
        canonical_target,
        manifest["target"]["precondition"],
        created_directories.get("."),
    )
    try:
        _verify_open_root_path(root_descriptor, canonical_target)
        root_result = os.fstat(root_descriptor)
        if manifest["target"]["precondition"]["state"] == "absent":
            expected_root = created_directories.get(".")
            if expected_root is None:
                os.fchmod(root_descriptor, 0o755)
                root_result = os.fstat(root_descriptor)
                record_created_directory(".", root_result)
    except Exception:
        os.close(root_descriptor)
        raise
    directory_conditions = _directory_condition_map(manifest)
    try:
        for index, operation in enumerate(manifest["operations"]):
            if before_operation is not None:
                before_operation(index, operation)
            _verify_open_root_path(root_descriptor, canonical_target)
            _validate_architecture_manifest(manifest)
            current_state = _operation_state(canonical_target, operation)
            if current_state == "after":
                journal["operations"][index]["state"] = "already-applied"
                _write_private_json(bundle / "journal.json", journal)
                continue
            if current_state != "before":
                journal["operations"][index]["state"] = "conflict"
                journal["status"] = "partial"
                _write_private_json(bundle / "journal.json", journal)
                _error(f"target drifted during apply: {operation['path']}")
            parent_descriptor, filename = _open_parent_directory(
                root_descriptor,
                operation["path"],
                directory_conditions,
                created_directories,
                record_created_directory,
            )
            try:
                payload = _read_payload(bundle, operation["payload_sha256"])
                mode = _parse_mode(operation["after"]["mode"], "operation.after.mode")
                _validate_architecture_manifest(manifest)
                if operation["action"] == "create":
                    _write_create(parent_descriptor, filename, payload, mode)
                elif operation["action"] == "semantic_merge":
                    _write_semantic_merge(
                        parent_descriptor,
                        filename,
                        operation["path"],
                        payload,
                        mode,
                        operation["before"],
                        bundle,
                    )
                elif operation["action"] == "unchanged":
                    pass
            finally:
                os.close(parent_descriptor)
            if not _after_matches(
                canonical_target / operation["path"], operation["after"]
            ):
                journal["operations"][index]["state"] = "conflict"
                journal["status"] = "partial"
                _write_private_json(bundle / "journal.json", journal)
                _error(f"post-write verification failed: {operation['path']}")
            journal["operations"][index]["state"] = (
                "unchanged" if operation["action"] == "unchanged" else "applied"
            )
            _write_private_json(bundle / "journal.json", journal)
    except Exception:
        if journal["status"] == "applying":
            journal["status"] = "partial"
            _write_private_json(bundle / "journal.json", journal)
        raise
    finally:
        os.close(root_descriptor)
    journal["status"] = "complete"
    _write_private_json(bundle / "journal.json", journal)
    return {
        **validation,
        "action": "apply",
        "status": "complete",
        "applied": sum(item["state"] == "applied" for item in journal["operations"]),
        "unchanged": sum(
            item["state"] in {"unchanged", "already-applied"}
            for item in journal["operations"]
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Finalize, validate, inspect, or apply a scaffold plan bundle."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("finalize", "validate", "status"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--target", required=True, type=Path)
        subparser.add_argument("--bundle", required=True, type=Path)
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--target", required=True, type=Path)
    apply_parser.add_argument("--bundle", required=True, type=Path)
    apply_parser.add_argument("--expected-digest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "finalize":
            result = finalize_bundle(arguments.target, arguments.bundle)
            exit_code = 0
        elif arguments.command == "validate":
            result = {
                **validate_bundle(arguments.target, arguments.bundle),
                "action": "validate",
            }
            exit_code = 0
        elif arguments.command == "status":
            result, exit_code = status_bundle(arguments.target, arguments.bundle)
        else:
            if not _is_digest(arguments.expected_digest):
                _error("--expected-digest must be a lowercase SHA-256 digest")
            result = apply_bundle(
                arguments.target,
                arguments.bundle,
                arguments.expected_digest,
            )
            exit_code = 0
    except ScaffoldError as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    except OSError as error:
        print(
            json.dumps(
                {"ok": False, "error": f"filesystem error: {error}"},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
