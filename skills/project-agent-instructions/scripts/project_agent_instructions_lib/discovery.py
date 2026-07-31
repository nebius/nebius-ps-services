"""Project, Codex configuration, instruction-chain, and evidence discovery."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import re
import stat
import subprocess
from typing import Any, Optional

from .contracts import AGENTIC_DESIGN_SCHEMA
from .contracts import AGENTIC_REQUIREMENTS_SCHEMA
from .contracts import GENERATED_MARKER_BYTES
from .contracts import MANIFEST_SCHEMA
from .contracts import MAX_BODY_BYTES
from .contracts import RENDERER_VERSION
from .contracts import TASK_DESIGN_MARKER
from .contracts import TASK_REQUIREMENTS_MARKER
from .contracts import ProjectInstructionsError
from .contracts import _canonical_json
from .contracts import _inside
from .contracts import _lstat_optional
from .contracts import _parse_generated
from .contracts import _read_regular
from .contracts import _sha256_bytes


def _git_text(cwd: Path, arguments: list[str], label: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProjectInstructionsError("UNSAFE_TARGET", f"could not {label}") from error
    if result.returncode != 0:
        raise ProjectInstructionsError("UNSAFE_TARGET", f"could not {label}")
    return result.stdout.strip()


def _git_ignored(git_root: Path, path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(git_root.resolve()).as_posix()
    except ValueError as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "decision evidence escaped the Git worktree"
        ) from error
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", relative],
            cwd=git_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            "could not validate decision evidence ignore status",
        ) from error
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise ProjectInstructionsError(
        "UNSAFE_TARGET", "could not validate decision evidence ignore status"
    )


def _project_identity(project_root: Path) -> tuple[Path, Path, str]:
    supplied = Path(os.path.abspath(project_root.expanduser()))
    if not supplied.is_dir():
        raise ProjectInstructionsError(
            "UNSAFE_TARGET",
            "selected project root must be an existing directory",
        )
    resolved = supplied.resolve()
    git_root = Path(
        _git_text(
            resolved,
            ["rev-parse", "--show-toplevel"],
            "resolve Git root",
        )
    ).resolve()
    try:
        scope_path = resolved.relative_to(git_root)
    except ValueError as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "selected project root must be inside its Git root"
        ) from error
    current = git_root
    for part in scope_path.parts:
        current = current / part
        metadata = _lstat_optional(current)
        if (
            metadata is None
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET",
                "selected project scope contains an unsafe path component",
            )
    scope = "." if scope_path == Path(".") else scope_path.as_posix()
    return resolved, git_root, scope


def _resolve_project_file(project_root: Path, value: str, label: str) -> Path:
    supplied = Path(value).expanduser()
    path = supplied if supplied.is_absolute() else project_root / supplied
    path = Path(os.path.abspath(path))
    if not _inside(path, project_root):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} must stay inside the selected project"
        )
    _read_regular(path, label)
    return path.resolve()


def _relative_project_path(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "project evidence escaped the selected project"
        ) from error


def _validate_spec_owner(
    owner: str, requirements_bytes: bytes, design_bytes: bytes
) -> None:
    try:
        requirements = requirements_bytes.decode("utf-8")
        design = design_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ProjectInstructionsError(
            "PREREQUISITE_MISSING", "requirements and design must be UTF-8"
        ) from error
    documents = (requirements, design)
    if owner == "task-implementer":
        valid = (
            TASK_REQUIREMENTS_MARKER in requirements
            and TASK_DESIGN_MARKER in design
            and all(
                AGENTIC_REQUIREMENTS_SCHEMA not in document
                and AGENTIC_DESIGN_SCHEMA not in document
                for document in documents
            )
        )
    elif owner == "agentic-sdlc":
        valid = (
            AGENTIC_REQUIREMENTS_SCHEMA in requirements
            and AGENTIC_DESIGN_SCHEMA in design
            and all(
                TASK_REQUIREMENTS_MARKER not in document
                and TASK_DESIGN_MARKER not in document
                for document in documents
            )
        )
    else:
        valid = False
    if not valid:
        raise ProjectInstructionsError(
            "SPEC_OWNER_CONFLICT",
            "requirements and design do not match the declared workflow owner",
        )


def _toml_parser() -> Any:
    for module_name in ("tomllib", "tomli"):
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
    raise ProjectInstructionsError(
        "PREREQUISITE_MISSING",
        "reading Codex config requires Python 3.11+ or the tomli package",
    )


def _codex_settings(codex_home: Path) -> tuple[list[str], int]:
    config_path = codex_home / "config.toml"
    metadata = _lstat_optional(config_path)
    if metadata is None:
        return [], 32 * 1024
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "Codex config must be a regular non-symlink file"
        )
    parser = _toml_parser()
    try:
        with config_path.open("rb") as handle:
            config = parser.load(handle)
    except (OSError, parser.TOMLDecodeError) as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "Codex config could not be parsed"
        ) from error
    raw_fallbacks = config.get("project_doc_fallback_filenames", [])
    fallbacks: list[str] = []
    if isinstance(raw_fallbacks, list):
        for item in raw_fallbacks:
            if (
                isinstance(item, str)
                and item
                and Path(item).name == item
                and item not in {"AGENTS.md", "AGENTS.override.md"}
                and item not in fallbacks
            ):
                fallbacks.append(item)
    configured_limit = config.get("project_doc_max_bytes", 32 * 1024)
    limit = configured_limit if isinstance(configured_limit, int) else 32 * 1024
    return fallbacks, max(1, limit)


def _instruction_entry(
    path: Path, *, scope: str, kind: str, project_root: Path
) -> dict[str, object]:
    content = _read_regular(path, f"instruction file {path.name}")
    entry: dict[str, object] = {
        "path": str(path.resolve()),
        "scope": scope,
        "kind": kind,
        "sha256": _sha256_bytes(content),
        "bytes": len(content),
    }
    if _inside(path, project_root):
        entry["project_relative_path"] = _relative_project_path(project_root, path)
    else:
        entry["project_relative_path"] = None
    return entry


def _first_nonempty_instruction(
    directory: Path,
    names: list[tuple[str, str]],
    *,
    scope: str,
    project_root: Path,
) -> Optional[dict[str, object]]:
    for name, kind in names:
        path = directory / name
        metadata = _lstat_optional(path)
        if metadata is None:
            continue
        content = _read_regular(path, f"instruction file {name}")
        if content.strip():
            return _instruction_entry(
                path,
                scope=scope,
                kind=kind,
                project_root=project_root,
            )
    return None


def _target_record(
    project_root: Path,
    active_project_instruction: Optional[dict[str, object]],
) -> dict[str, object]:
    target = project_root / "AGENTS.md"
    metadata = _lstat_optional(target)
    if metadata is None:
        file_status = "missing"
        digest = None
        body_digest = None
    else:
        content = _read_regular(target, "project AGENTS.md")
        digest = _sha256_bytes(content)
        parsed = _parse_generated(content)
        if parsed is None:
            file_status = "human-owned"
            body_digest = None
        else:
            recorded, actual = parsed
            file_status = "generated" if recorded == actual else "human-edited"
            body_digest = recorded
    active_path = (
        str(active_project_instruction["path"])
        if active_project_instruction is not None
        else None
    )
    return {
        "path": str(target.resolve()),
        "file_status": file_status,
        "sha256": digest,
        "body_sha256": body_digest,
        "active_path": active_path,
        "active_kind": (
            active_project_instruction["kind"]
            if active_project_instruction is not None
            else None
        ),
    }


def _instruction_chain(
    project_root: Path,
    git_root: Path,
    codex_home: Path,
    fallbacks: list[str],
) -> tuple[list[dict[str, object]], Optional[dict[str, object]]]:
    inherited: list[dict[str, object]] = []
    global_entry = _first_nonempty_instruction(
        codex_home,
        [
            ("AGENTS.override.md", "global-override"),
            ("AGENTS.md", "global"),
        ],
        scope="global",
        project_root=project_root,
    )
    if global_entry is not None:
        inherited.append(global_entry)
    relative = project_root.relative_to(git_root)
    directories = [git_root]
    current = git_root
    for part in relative.parts:
        current = current / part
        directories.append(current)
    active_project: Optional[dict[str, object]] = None
    candidate_names = [
        ("AGENTS.override.md", "project-override"),
        ("AGENTS.md", "project-agents"),
        *((name, "project-fallback") for name in fallbacks),
    ]
    for directory in directories:
        entry = _first_nonempty_instruction(
            directory,
            candidate_names,
            scope="project",
            project_root=project_root,
        )
        if entry is None:
            continue
        if directory == project_root:
            active_project = entry
        else:
            inherited.append(entry)
    return inherited, active_project


def _generated_body_capacity(
    configured_limit: int,
    inherited: list[dict[str, object]],
) -> int:
    ancestor_project_bytes = sum(
        int(entry["bytes"]) for entry in inherited if entry.get("scope") == "project"
    )
    target_file_capacity = max(0, configured_limit - ancestor_project_bytes)
    return min(
        MAX_BODY_BYTES,
        max(0, target_file_capacity - GENERATED_MARKER_BYTES),
    )


def _manifest(
    project_root_value: Path,
    owner: str,
    requirements_value: str,
    design_value: str,
    codex_home_value: Optional[Path],
) -> dict[str, object]:
    project_root, git_root, project_scope = _project_identity(project_root_value)
    requirements_path = _resolve_project_file(
        project_root, requirements_value, "requirements"
    )
    design_path = _resolve_project_file(project_root, design_value, "design")
    requirements_bytes = _read_regular(requirements_path, "requirements")
    design_bytes = _read_regular(design_path, "design")
    _validate_spec_owner(owner, requirements_bytes, design_bytes)
    codex_home = (
        codex_home_value.expanduser().resolve()
        if codex_home_value is not None
        else Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    )
    fallbacks, configured_limit = _codex_settings(codex_home)
    inherited, active_project = _instruction_chain(
        project_root, git_root, codex_home, fallbacks
    )
    payload: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "renderer_version": RENDERER_VERSION,
        "project_root": str(project_root),
        "git_root": str(git_root),
        "project_scope": project_scope,
        "spec_owner": owner,
        "requirements": {
            "path": _relative_project_path(project_root, requirements_path),
            "sha256": _sha256_bytes(requirements_bytes),
        },
        "design": {
            "path": _relative_project_path(project_root, design_path),
            "sha256": _sha256_bytes(design_bytes),
        },
        "codex_home": str(codex_home),
        "fallback_filenames": fallbacks,
        "configured_project_doc_max_bytes": configured_limit,
        "generated_body_max_bytes": _generated_body_capacity(
            configured_limit, inherited
        ),
        "inherited_instructions": inherited,
        "active_project_instruction": active_project,
        "target": _target_record(project_root, active_project),
    }
    payload["manifest_sha256"] = _sha256_bytes(_canonical_json(payload))
    return payload


def _validate_evidence(
    decision: dict[str, object], manifest: dict[str, object]
) -> list[dict[str, str]]:
    raw_evidence = decision.get("evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "decision evidence must be a non-empty list"
        )
    project_root = Path(str(manifest["project_root"]))
    git_root = Path(str(manifest["git_root"]))
    evidence: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_evidence:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence entry is invalid"
            )
        relative = item.get("path")
        digest = item.get("sha256")
        if (
            not isinstance(relative, str)
            or not relative
            or relative in seen
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence identity is invalid"
            )
        path = _resolve_project_file(project_root, relative, "decision evidence")
        canonical_relative = _relative_project_path(project_root, path)
        if canonical_relative != relative:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence path is not canonical"
            )
        if _git_ignored(git_root, path):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence must not be Git-ignored"
            )
        actual = _sha256_bytes(_read_regular(path, "decision evidence"))
        if actual != digest:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION", "decision evidence changed"
            )
        seen.add(relative)
        evidence.append({"path": relative, "sha256": digest})
    return evidence
