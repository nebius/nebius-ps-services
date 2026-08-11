"""Project, layered Codex configuration, instructions, and evidence discovery."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any, Optional

from .contracts import GENERATED_MARKER_BYTES
from .contracts import MANIFEST_SCHEMA
from .contracts import MAX_BODY_BYTES
from .contracts import RENDERER_VERSION
from .contracts import RUNTIME_CONFIG_SCHEMA
from .contracts import SPEC_RECEIPT_SCHEMA
from .contracts import ProjectInstructionsError
from .contracts import _canonical_json
from .contracts import _inside
from .contracts import _lstat_optional
from .contracts import _parse_generated
from .contracts import _read_regular
from .contracts import _sha256_bytes
from .contracts import _valid_sha256


DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024
DEFAULT_PROJECT_ROOT_MARKERS = [".git"]
CONFIG_KEYS = {
    "project_doc_fallback_filenames",
    "project_doc_max_bytes",
    "project_root_markers",
}
PROFILE_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")
RECOVERY_NAMES = (
    ".AGENTS.md.project-agent-instructions.lock",
    ".AGENTS.md.project-agent-instructions.backup",
)


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
    relative = _git_relative(git_root, path, "decision evidence")
    try:
        result = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", "--", relative],
            cwd=git_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "could not validate decision evidence ignore status"
        ) from error
    if result.returncode in {0, 1}:
        return result.returncode == 0
    raise ProjectInstructionsError(
        "UNSAFE_TARGET", "could not validate decision evidence ignore status"
    )


def _git_tracked(git_root: Path, path: Path) -> bool:
    relative = _git_relative(git_root, path, "decision evidence")
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative],
            cwd=git_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "could not validate decision evidence tracking"
        ) from error
    return result.returncode == 0


def _git_relative(git_root: Path, path: Path, label: str) -> str:
    try:
        return path.resolve().relative_to(git_root.resolve()).as_posix()
    except ValueError as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} escaped the Git worktree"
        ) from error


def _project_identity(project_root: Path) -> tuple[Path, Path, str]:
    supplied = Path(os.path.abspath(project_root.expanduser()))
    if not supplied.is_dir():
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "selected project root must be an existing directory"
        )
    resolved = supplied.resolve()
    git_root = Path(
        _git_text(resolved, ["rev-parse", "--show-toplevel"], "resolve Git root")
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


def _validate_spec_receipt(
    receipt_path: Path,
    owner: str,
    project_root: Path,
    git_root: Path,
    project_scope: str,
    requirements_path: Path,
    design_path: Path,
) -> tuple[dict[str, object], str]:
    if _inside(receipt_path, git_root):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "spec validation receipt must stay outside Git"
        )
    metadata = _lstat_optional(receipt_path)
    if metadata is None or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "spec validation receipt must use mode 0600"
        )
    raw = _read_regular(receipt_path, "spec validation receipt")
    try:
        receipt: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectInstructionsError(
            "SPEC_VALIDATION_REQUIRED", "spec validation receipt is invalid"
        ) from error
    required = {
        "schema",
        "owner",
        "status",
        "project_root",
        "git_root",
        "project_scope",
        "git_head",
        "validator",
        "validator_version",
        "requirements",
        "design",
        "traceability_sha256",
    }
    expected_requirements = {
        "path": _relative_project_path(project_root, requirements_path),
        "sha256": _sha256_bytes(_read_regular(requirements_path, "requirements")),
    }
    expected_design = {
        "path": _relative_project_path(project_root, design_path),
        "sha256": _sha256_bytes(_read_regular(design_path, "design")),
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != required
        or receipt.get("schema") != SPEC_RECEIPT_SCHEMA
        or receipt.get("owner") != owner
        or receipt.get("status") != "current"
        or receipt.get("project_root") != str(project_root)
        or receipt.get("git_root") != str(git_root)
        or receipt.get("project_scope") != project_scope
        or receipt.get("git_head")
        != _git_text(git_root, ["rev-parse", "HEAD"], "resolve spec receipt HEAD")
        or not isinstance(receipt.get("validator"), str)
        or not receipt.get("validator")
        or type(receipt.get("validator_version")) is not int
        or int(receipt["validator_version"]) < 1
        or receipt.get("requirements") != expected_requirements
        or receipt.get("design") != expected_design
        or not _valid_sha256(receipt.get("traceability_sha256"))
    ):
        raise ProjectInstructionsError(
            "SPEC_VALIDATION_REQUIRED",
            "spec validation receipt does not prove the selected specs",
        )
    skills_root = Path(__file__).resolve().parents[3]
    validator = (
        skills_root / "maintain-project-specs" / "scripts" / "validate_project_specs.py"
    )
    try:
        completed = subprocess.run(
            [sys.executable, str(validator), "--project-root", str(project_root)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        authoritative: Any = json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        raise ProjectInstructionsError(
            "SPEC_VALIDATION_REQUIRED", "owner spec validation could not run"
        ) from error
    if completed.returncode != 0 or authoritative != receipt:
        raise ProjectInstructionsError(
            "SPEC_VALIDATION_REQUIRED",
            "spec validation receipt is not the current owner-issued receipt",
        )
    return receipt, _sha256_bytes(raw)


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


def _load_toml(path: Path, label: str) -> tuple[dict[str, object], bytes]:
    raw = _read_regular(path, label)
    parser = _toml_parser()
    try:
        value = parser.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, parser.TOMLDecodeError) as error:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", f"{label} is invalid"
        ) from error
    if not isinstance(value, dict):
        raise ProjectInstructionsError("UNSAFE_TARGET", f"{label} is invalid")
    return value, raw


def _load_runtime_config(
    path: Path, git_root: Path
) -> tuple[dict[str, object], dict[str, str]]:
    resolved = Path(os.path.abspath(path.expanduser()))
    if _inside(resolved, git_root):
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "runtime config declaration must stay outside Git"
        )
    metadata = _lstat_optional(resolved)
    if metadata is None or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "runtime config declaration must use mode 0600"
        )
    raw = _read_regular(resolved, "runtime config declaration")
    try:
        value: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProjectInstructionsError(
            "DISCOVERY_CONTEXT_UNVERIFIED", "runtime config declaration is invalid"
        ) from error
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "profile", "overrides"}
        or value.get("schema") != RUNTIME_CONFIG_SCHEMA
        or (
            value.get("profile") is not None
            and not isinstance(value.get("profile"), str)
        )
        or not isinstance(value.get("overrides"), dict)
        or any(key not in CONFIG_KEYS for key in value["overrides"])
    ):
        raise ProjectInstructionsError(
            "DISCOVERY_CONTEXT_UNVERIFIED", "runtime config declaration is invalid"
        )
    return value, {"path": str(resolved), "sha256": _sha256_bytes(raw)}


def _apply_settings(settings: dict[str, object], source: dict[str, object]) -> None:
    for key in CONFIG_KEYS:
        if key in source:
            settings[key] = source[key]


def _valid_config_filename(value: object) -> bool:
    return (
        isinstance(value, str)
        and value not in {"", ".", ".."}
        and Path(value).name == value
        and "/" not in value
        and "\\" not in value
        and all(character.isprintable() for character in value)
    )


def _validated_settings(
    settings: dict[str, object],
) -> tuple[list[str], int, list[str]]:
    raw_fallbacks = settings.get("project_doc_fallback_filenames", [])
    if not isinstance(raw_fallbacks, list):
        raise ProjectInstructionsError(
            "DISCOVERY_CONTEXT_UNVERIFIED", "fallback filenames are invalid"
        )
    fallbacks: list[str] = []
    for item in raw_fallbacks:
        if (
            not _valid_config_filename(item)
            or item in {"AGENTS.md", "AGENTS.override.md"}
            or item in fallbacks
        ):
            raise ProjectInstructionsError(
                "DISCOVERY_CONTEXT_UNVERIFIED", "fallback filenames are invalid"
            )
        fallbacks.append(item)
    limit = settings.get("project_doc_max_bytes", DEFAULT_PROJECT_DOC_MAX_BYTES)
    if type(limit) is not int or not 1 <= int(limit) <= 1024 * 1024:
        raise ProjectInstructionsError(
            "DISCOVERY_CONTEXT_UNVERIFIED", "project document byte limit is invalid"
        )
    raw_markers = settings.get("project_root_markers", DEFAULT_PROJECT_ROOT_MARKERS)
    if not isinstance(raw_markers, list):
        raise ProjectInstructionsError(
            "DISCOVERY_CONTEXT_UNVERIFIED", "project root markers are invalid"
        )
    markers: list[str] = []
    for item in raw_markers:
        if not _valid_config_filename(item) or item in markers:
            raise ProjectInstructionsError(
                "DISCOVERY_CONTEXT_UNVERIFIED", "project root markers are invalid"
            )
        markers.append(item)
    return fallbacks, int(limit), markers


def _trusted(user_config: dict[str, object], git_root: Path) -> bool:
    projects = user_config.get("projects", {})
    if not isinstance(projects, dict):
        return False
    record = projects.get(str(git_root))
    return isinstance(record, dict) and record.get("trust_level") == "trusted"


def _codex_settings(
    codex_home: Path,
    git_root: Path,
    project_root: Path,
    runtime_path: Path,
) -> dict[str, object]:
    settings: dict[str, object] = {
        "project_doc_fallback_filenames": [],
        "project_doc_max_bytes": DEFAULT_PROJECT_DOC_MAX_BYTES,
        "project_root_markers": list(DEFAULT_PROJECT_ROOT_MARKERS),
    }
    sources: list[dict[str, str]] = []
    user_config: dict[str, object] = {}
    user_path = codex_home / "config.toml"
    if _lstat_optional(user_path) is not None:
        user_config, raw = _load_toml(user_path, "Codex user config")
        _apply_settings(settings, user_config)
        sources.append({"path": str(user_path.resolve()), "sha256": _sha256_bytes(raw)})
    runtime, runtime_source = _load_runtime_config(runtime_path, git_root)
    profile = runtime["profile"]
    if profile is not None:
        if PROFILE_NAME_RE.fullmatch(profile) is None:
            raise ProjectInstructionsError(
                "DISCOVERY_CONTEXT_UNVERIFIED", "active Codex profile name is invalid"
            )
        profile_path = codex_home / f"{profile}.config.toml"
        if _lstat_optional(profile_path) is None:
            raise ProjectInstructionsError(
                "DISCOVERY_CONTEXT_UNVERIFIED", "active Codex profile is missing"
            )
        profile_config, raw = _load_toml(profile_path, "Codex profile config")
        _apply_settings(settings, profile_config)
        sources.append(
            {"path": str(profile_path.resolve()), "sha256": _sha256_bytes(raw)}
        )
    if _trusted(user_config, git_root):
        current = git_root
        directories = [current]
        relative = project_root.relative_to(git_root)
        for part in relative.parts:
            current = current / part
            directories.append(current)
        for directory in directories:
            config_directory = directory / ".codex"
            directory_metadata = _lstat_optional(config_directory)
            if directory_metadata is None:
                continue
            if stat.S_ISLNK(directory_metadata.st_mode) or not stat.S_ISDIR(
                directory_metadata.st_mode
            ):
                raise ProjectInstructionsError(
                    "DISCOVERY_CONTEXT_UNVERIFIED",
                    "Codex project config directory is unsafe",
                )
            config_path = config_directory / "config.toml"
            if _lstat_optional(config_path) is None:
                continue
            if not _inside(config_path.resolve(), directory):
                raise ProjectInstructionsError(
                    "DISCOVERY_CONTEXT_UNVERIFIED",
                    "Codex project config escaped its directory",
                )
            config, raw = _load_toml(config_path, "Codex project config")
            _apply_settings(settings, config)
            sources.append(
                {"path": str(config_path.resolve()), "sha256": _sha256_bytes(raw)}
            )
    _apply_settings(settings, dict(runtime["overrides"]))
    sources.append(runtime_source)
    fallbacks, limit, markers = _validated_settings(settings)
    if (
        project_root != git_root
        and markers
        and not any((project_root / marker).exists() for marker in markers)
    ):
        raise ProjectInstructionsError(
            "DISCOVERY_CONTEXT_UNVERIFIED",
            "selected project root has none of the effective root markers",
        )
    payload: dict[str, object] = {
        "fallback_filenames": fallbacks,
        "project_doc_max_bytes": limit,
        "project_root_markers": markers,
        "sources": sources,
        "runtime_config_sha256": runtime_source["sha256"],
    }
    payload["sha256"] = _sha256_bytes(_canonical_json(payload))
    return payload


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
        "project_relative_path": None,
    }
    if _inside(path, project_root):
        entry["project_relative_path"] = _relative_project_path(project_root, path)
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
        if _lstat_optional(path) is None:
            continue
        if _read_regular(path, f"instruction file {name}").strip():
            return _instruction_entry(
                path, scope=scope, kind=kind, project_root=project_root
            )
    return None


def _ensure_no_recovery(
    project_root: Path,
    permitted_backup_sha256: Optional[str] = None,
) -> None:
    lock_path = project_root / RECOVERY_NAMES[0]
    backup_path = project_root / RECOVERY_NAMES[1]
    if _lstat_optional(lock_path) is not None:
        raise ProjectInstructionsError(
            "RECOVERY_REQUIRED",
            "project AGENTS.md has a lock or backup artifact requiring recovery",
        )
    backup = _lstat_optional(backup_path)
    if backup is None:
        return
    if (
        permitted_backup_sha256 is None
        or stat.S_ISLNK(backup.st_mode)
        or not stat.S_ISREG(backup.st_mode)
        or _sha256_bytes(_read_regular(backup_path, "transition backup"))
        != permitted_backup_sha256
    ):
        raise ProjectInstructionsError(
            "RECOVERY_REQUIRED",
            "project AGENTS.md has a lock or backup artifact requiring recovery",
        )


def _target_record(
    project_root: Path,
    active_project_instruction: Optional[dict[str, object]],
) -> dict[str, object]:
    target = project_root / "AGENTS.md"
    parent = project_root.lstat()
    metadata = _lstat_optional(target)
    marker_version: Optional[int] = None
    manifest_digest: Optional[str] = None
    decision_digest: Optional[str] = None
    body_digest: Optional[str] = None
    managed_prefix_bytes = 0
    managed_prefix_digest: Optional[str] = None
    if metadata is None:
        file_status = "missing"
        digest = None
    else:
        content = _read_regular(target, "project AGENTS.md")
        digest = _sha256_bytes(content)
        parsed = _parse_generated(content)
        if parsed is None:
            file_status = "human-owned"
            managed_prefix_bytes = len(content)
            managed_prefix_digest = digest
        else:
            marker_version = int(parsed["version"])
            manifest_digest = parsed["manifest_sha256"]  # type: ignore[assignment]
            decision_digest = parsed["decision_sha256"]  # type: ignore[assignment]
            body_digest = str(parsed["body_sha256"])
            managed_prefix_bytes = int(parsed["prefix_bytes"])
            managed_prefix_digest = str(parsed["prefix_sha256"])
            if marker_version in {1, 2}:
                file_status = "legacy"
            elif parsed["body_sha256"] == parsed["actual_body_sha256"]:
                file_status = "managed"
            else:
                file_status = "human-edited"
    active_path = (
        str(active_project_instruction["path"])
        if active_project_instruction is not None
        else None
    )
    return {
        "path": str(target.resolve()),
        "file_status": file_status,
        "sha256": digest,
        "marker_version": marker_version,
        "manifest_sha256": manifest_digest,
        "decision_sha256": decision_digest,
        "body_sha256": body_digest,
        "managed_prefix_bytes": managed_prefix_bytes,
        "managed_prefix_sha256": managed_prefix_digest,
        "active_path": active_path,
        "active_kind": (
            active_project_instruction["kind"]
            if active_project_instruction is not None
            else None
        ),
        "parent_device": parent.st_dev,
        "parent_inode": parent.st_ino,
    }


def _instruction_chain(
    project_root: Path,
    git_root: Path,
    codex_home: Path,
    fallbacks: list[str],
) -> tuple[
    list[dict[str, object]], list[dict[str, object]], Optional[dict[str, object]]
]:
    global_entries: list[dict[str, object]] = []
    global_entry = _first_nonempty_instruction(
        codex_home,
        [("AGENTS.override.md", "global-override"), ("AGENTS.md", "global")],
        scope="global",
        project_root=project_root,
    )
    if global_entry is not None:
        global_entries.append(global_entry)
    relative = project_root.relative_to(git_root)
    directories = [git_root]
    current = git_root
    for part in relative.parts:
        current = current / part
        directories.append(current)
    ancestors: list[dict[str, object]] = []
    active: Optional[dict[str, object]] = None
    names = [
        ("AGENTS.override.md", "project-override"),
        ("AGENTS.md", "project-agents"),
        *((name, "project-fallback") for name in fallbacks),
    ]
    for directory in directories:
        entry = _first_nonempty_instruction(
            directory, names, scope="project", project_root=project_root
        )
        if entry is None:
            continue
        if directory == project_root:
            active = entry
        else:
            ancestors.append(entry)
    return global_entries, ancestors, active


def _require_tracked_project_instructions(
    git_root: Path, entries: list[dict[str, object]]
) -> None:
    for entry in entries:
        path = Path(str(entry["path"]))
        if _git_ignored(git_root, path) or not _git_tracked(git_root, path):
            raise ProjectInstructionsError(
                "DISCOVERY_CONTEXT_UNVERIFIED",
                "project instruction files must be tracked and non-ignored",
            )


def _generated_body_capacity(
    configured_limit: int,
    ancestors: list[dict[str, object]],
    target: dict[str, object],
) -> int:
    inherited_bytes = sum(int(entry["bytes"]) for entry in ancestors)
    prefix_bytes = int(target["managed_prefix_bytes"])
    separator_bytes = 2 if prefix_bytes else 0
    return min(
        MAX_BODY_BYTES,
        max(
            0,
            configured_limit
            - inherited_bytes
            - prefix_bytes
            - separator_bytes
            - GENERATED_MARKER_BYTES,
        ),
    )


def _manifest(
    project_root_value: Path,
    owner: str,
    requirements_value: str,
    design_value: str,
    codex_home_value: Optional[Path],
    spec_receipt_path: Path,
    runtime_config_path: Path,
    permitted_backup_sha256: Optional[str] = None,
) -> dict[str, object]:
    project_root, git_root, project_scope = _project_identity(project_root_value)
    target_path = project_root / "AGENTS.md"
    if _git_ignored(git_root, target_path):
        raise ProjectInstructionsError(
            "DISCOVERY_CONTEXT_UNVERIFIED",
            "selected project AGENTS.md is ignored by Git",
        )
    _ensure_no_recovery(project_root, permitted_backup_sha256)
    requirements_path = _resolve_project_file(
        project_root, requirements_value, "requirements"
    )
    design_path = _resolve_project_file(project_root, design_value, "design")
    _, receipt_digest = _validate_spec_receipt(
        Path(os.path.abspath(spec_receipt_path.expanduser())),
        owner,
        project_root,
        git_root,
        project_scope,
        requirements_path,
        design_path,
    )
    codex_home = (
        codex_home_value.expanduser().resolve()
        if codex_home_value is not None
        else Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().resolve()
    )
    config = _codex_settings(codex_home, git_root, project_root, runtime_config_path)
    fallbacks = list(config["fallback_filenames"])
    global_entries, ancestors, active = _instruction_chain(
        project_root, git_root, codex_home, fallbacks
    )
    target = _target_record(project_root, active)
    tracked_entries = list(ancestors)
    if active is not None and (
        Path(str(active["path"])) != target_path.resolve()
        or target["file_status"] != "managed"
    ):
        tracked_entries.append(active)
    _require_tracked_project_instructions(git_root, tracked_entries)
    receipt_path = Path(os.path.abspath(spec_receipt_path.expanduser()))
    payload: dict[str, object] = {
        "schema": MANIFEST_SCHEMA,
        "renderer_version": RENDERER_VERSION,
        "project_root": str(project_root),
        "git_root": str(git_root),
        "project_scope": project_scope,
        "project_name": project_root.name,
        "spec_owner": owner,
        "requirements": {
            "path": _relative_project_path(project_root, requirements_path),
            "sha256": _sha256_bytes(_read_regular(requirements_path, "requirements")),
        },
        "design": {
            "path": _relative_project_path(project_root, design_path),
            "sha256": _sha256_bytes(_read_regular(design_path, "design")),
        },
        "spec_receipt": {"path": str(receipt_path), "sha256": receipt_digest},
        "codex_home": str(codex_home),
        "config_context": config,
        "generated_body_max_bytes": _generated_body_capacity(
            int(config["project_doc_max_bytes"]), ancestors, target
        ),
        "global_instructions": global_entries,
        "ancestor_project_instructions": ancestors,
        "active_project_instruction": active,
        "target": target,
    }
    payload["manifest_sha256"] = _sha256_bytes(_canonical_json(payload))
    return payload


def _validate_evidence(
    decision: dict[str, object], manifest: dict[str, object]
) -> list[dict[str, str]]:
    raw_evidence = decision.get("evidence")
    if not isinstance(raw_evidence, list) or not raw_evidence:
        raise ProjectInstructionsError(
            "UNSAFE_TARGET", "decision evidence must be non-empty"
        )
    project_root = Path(str(manifest["project_root"]))
    git_root = Path(str(manifest["git_root"]))
    evidence: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw_evidence:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "locator"}:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence entry is invalid"
            )
        relative = item.get("path")
        digest = item.get("sha256")
        locator = item.get("locator")
        if (
            not isinstance(relative, str)
            or not relative
            or relative in seen
            or not _valid_sha256(digest)
            or not isinstance(locator, str)
            or locator != locator.strip()
            or not locator
            or "\n" in locator
            or len(locator.encode("utf-8")) > 160
        ):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence identity is invalid"
            )
        path = _resolve_project_file(project_root, relative, "decision evidence")
        if _relative_project_path(project_root, path) != relative:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence path is not canonical"
            )
        if _git_ignored(git_root, path) or not _git_tracked(git_root, path):
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence must be tracked"
            )
        raw = _read_regular(path, "decision evidence")
        if len(raw) > 1024 * 1024 or _sha256_bytes(raw) != digest:
            raise ProjectInstructionsError(
                "CONCURRENT_MODIFICATION", "decision evidence changed"
            )
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence must be UTF-8"
            ) from error
        if locator not in text:
            raise ProjectInstructionsError(
                "UNSAFE_TARGET", "decision evidence locator is absent"
            )
        seen.add(relative)
        evidence.append({"path": relative, "sha256": str(digest), "locator": locator})
    return evidence
