#!/usr/bin/env python3
"""Core filesystem and snapshot logic for private prompt workspaces."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unicodedata
import uuid


WORKSPACE_SCHEMA = "task-implementer/workspace-v2"
PROMPT_SCHEMA = "task-implementer/prompt-v2"
LEGACY_PROMPT_SCHEMA = "task-implementer/prompt-v1"
RUN_SCHEMA = "task-implementer/run-manifest-v1"
MAX_PROMPT_BYTES = 256 * 1024
TERMINAL_RUN_STATUSES = {"done", "superseded", "abandoned"}
PROMPT_ID_RE = re.compile(r"prompt-[0-9a-f]{32}\Z")
RUN_ID_RE = re.compile(r"run-[a-z0-9][a-z0-9-]{0,79}\Z")
REVISION_RE = re.compile(r"r([0-9]{4})\Z")
FRONTMATTER_KEYS = {"schema", "prompt_id", "title", "created_at"}
REQUIRED_SECTIONS = ("Ask",)
OPTIONAL_SECTIONS = (
    "Outcome",
    "Context",
    "Constraints",
    "Acceptance criteria",
    "Verification",
    "Non-goals",
    "References",
    "Clarifications",
    "Live Experiment Environment",
    "Steering",
)
RESERVED_SECTIONS = (*REQUIRED_SECTIONS, *OPTIONAL_SECTIONS)
HUB_FILENAME = "00-START-HERE.md"
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAWS_ACCESS_KEY_ID\b\s*[:=]\s*[A-Z0-9]{16,}"),
    re.compile(r"\bAWS_SECRET_ACCESS_KEY\b\s*[:=]\s*[A-Za-z0-9/+=]{30,}"),
    re.compile(r"\bGITHUB_TOKEN\b\s*[:=]\s*[A-Za-z0-9_ghopsu-]{20,}"),
    re.compile(r"\bOPENAI_API_KEY\b\s*[:=]\s*sk-[A-Za-z0-9_-]{16,}"),
    re.compile(
        r"\bNEBIUS_(?!(?:PROFILE|PROJECT_ID|AUTH_CREDENTIALS_FILE)\b)"
        r"[A-Z0-9_]*\b\s*[:=]\s*[A-Za-z0-9_./+=:-]{12,}"
    ),
    re.compile(r"\bYC_TOKEN\b\s*[:=]\s*[A-Za-z0-9_./+=:-]{12,}"),
    re.compile(r"\bKUBECONFIG\b.*(certificate-authority-data|client-key-data|token:)"),
    re.compile(
        r"(?i)\b(password|secret|token)\b\s*[:=]\s*[\"']?"
        r"[A-Za-z0-9_./+=:-]{12,}"
    ),
)


class PromptWorkspaceError(Exception):
    """Expected validation or state transition failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PromptDocument:
    path: Path
    raw: bytes
    text: str
    prompt_id: str
    title: str
    created_at: datetime
    sections: dict[str, str]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def intent_sha256(self) -> str:
        return prompt_intent_sha256(self.sections)


def now_local() -> datetime:
    return datetime.now().astimezone()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_seconds(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.isoformat(timespec="seconds")


def stable_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def private_chmod(path: Path, mode: int) -> None:
    if os.name == "posix":
        path.chmod(mode)


def require_mode(path: Path, expected: int, label: str) -> None:
    if os.name != "posix":
        return
    try:
        actual = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", f"{label} is missing or inaccessible"
        ) from exc
    if actual != expected:
        raise PromptWorkspaceError(
            "WORKSPACE_PERMISSION_INVALID",
            f"{label} mode is {oct(actual)}; expected {oct(expected)}",
        )


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", f"private directory is unsafe: {path}"
        )
    private_chmod(path, 0o700)


def write_exclusive(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    private_chmod(path, 0o600)


def write_atomic(path: Path, data: bytes) -> None:
    ensure_private_dir(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        private_chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        private_chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def load_json_object(path: Path, label: str) -> dict[str, object]:
    if path.is_symlink():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", f"{label} must not be a symlink"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PromptWorkspaceError(
            "WORKSPACE_NOT_FOUND", f"{label} is missing: {path}"
        ) from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", f"{label} is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", f"{label} must contain a JSON object"
        )
    return value


def canonical_git_root(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    if not candidate.is_dir():
        raise PromptWorkspaceError(
            "REPO_ROOT_INVALID", f"repository path is not a directory: {candidate}"
        )
    try:
        result = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git is unavailable for repository discovery"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PromptWorkspaceError(
            "REPO_ROOT_INVALID", "Git root discovery timed out"
        ) from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise PromptWorkspaceError(
            "REPO_ROOT_INVALID", f"path is not inside a Git worktree: {candidate}"
        )
    return Path(result.stdout.strip()).resolve()


def enclosing_git_storage(path: Path) -> Path | None:
    """Return an enclosing worktree or Git metadata directory."""

    candidate = path.expanduser()
    while not candidate.exists() and candidate.parent != candidate:
        candidate = candidate.parent
    candidate = candidate.resolve()
    try:
        worktree = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        if worktree.returncode == 0 and worktree.stdout.strip():
            return Path(worktree.stdout.strip()).resolve()
        git_dir = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--absolute-git-dir"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git is unavailable for storage validation"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git storage validation timed out"
        ) from exc
    if git_dir.returncode == 0 and git_dir.stdout.strip():
        return Path(git_dir.stdout.strip()).resolve()
    return None


def safe_segment(value: str, *, fallback: str, limit: int = 80) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    segment = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    segment = segment[:limit].rstrip("-")
    return segment or fallback


def prompt_slug(ask: str) -> str:
    normalized = unicodedata.normalize("NFKD", ask)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    words = re.findall(r"[a-z0-9]+", ascii_value)[:8]
    if not words:
        return "prompt"
    chosen: list[str] = []
    for word in words:
        candidate = "-".join([*chosen, word])
        if len(candidate) <= 60:
            chosen.append(word)
            continue
        if not chosen:
            return word[:60].rstrip("-") or "prompt"
        break
    return "-".join(chosen) or "prompt"


def validate_short_ask(ask: str) -> str:
    value = ask.strip()
    if not value:
        raise PromptWorkspaceError("PROMPT_INPUT_INVALID", "short ask is empty")
    if len(value) > 200:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "short ask exceeds 200 characters"
        )
    if len(value.splitlines()) != 1:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "short ask must be a single line"
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "short ask contains a control character"
        )
    if contains_secret(value):
        raise PromptWorkspaceError(
            "PROMPT_SENSITIVE_INPUT",
            "short ask appears to contain secret material",
        )
    return value


def contains_secret(text: str) -> bool:
    return any(
        pattern.search(line)
        for line in (text.splitlines() or [text])
        for pattern in SECRET_PATTERNS
    )


def repo_and_scope(repo_path: Path, scope_value: str) -> tuple[Path, Path, str]:
    root = canonical_git_root(repo_path)
    scope_candidate = Path(scope_value).expanduser()
    if scope_candidate.is_absolute():
        source_root = scope_candidate.resolve()
    else:
        source_root = (root / scope_candidate).resolve()
    if not source_root.is_dir() or not is_relative_to(source_root, root):
        raise PromptWorkspaceError(
            "SCOPE_INVALID", "scope must be an existing directory inside the Git root"
        )
    scope = source_root.relative_to(root).as_posix() or "."
    return root, source_root, scope


def workspace_git_identity(
    root: Path,
) -> tuple[Path, Path, str, str, str, str | None]:
    try:
        common_result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        worktrees_result = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        branch_result = subprocess.run(
            ["git", "-C", str(root), "symbolic-ref", "-q", "--short", "HEAD"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git identity discovery failed"
        ) from exc
    first_line = (
        worktrees_result.stdout.splitlines()[0] if worktrees_result.stdout else ""
    )
    if (
        common_result.returncode != 0
        or not common_result.stdout.strip()
        or worktrees_result.returncode != 0
        or not first_line.startswith("worktree ")
        or branch_result.returncode != 0
        or not branch_result.stdout.strip()
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace Git identity is unavailable"
        )
    common_dir = Path(common_result.stdout.strip()).resolve()
    primary = Path(first_line.removeprefix("worktree ")).resolve()
    branch = branch_result.stdout.strip()
    try:
        source_config_result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "config",
                "--local",
                "--get",
                f"branch.{branch}.worktreeSkillTaskLaneSourceRef",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        lane_config_result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "config",
                "--local",
                "--get",
                f"branch.{branch}.worktreeSkillTaskLaneId",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git lane identity discovery failed"
        ) from exc
    source_ref = (
        source_config_result.stdout.strip()
        if source_config_result.returncode == 0 and source_config_result.stdout.strip()
        else f"refs/heads/{branch}"
    )
    if not source_ref.startswith("refs/heads/") or source_ref == "refs/heads/":
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace source ref is invalid"
        )
    lane_id = (
        lane_config_result.stdout.strip()
        if lane_config_result.returncode == 0 and lane_config_result.stdout.strip()
        else None
    )
    return (
        common_dir,
        primary,
        source_ref,
        source_ref.removeprefix("refs/heads/"),
        branch,
        lane_id,
    )


def workspace_identity(
    root: Path,
    source_root: Path,
    scope: str,
    *,
    common_dir: Path | None = None,
    primary: Path | None = None,
    source_ref: str | None = None,
) -> tuple[str, str, str]:
    if common_dir is None or primary is None or source_ref is None:
        common_dir, primary, source_ref, _, _, _ = workspace_git_identity(root)
    root_hash = hashlib.sha256(f"{common_dir}\0{primary}".encode("utf-8")).hexdigest()[
        :12
    ]
    project_id = f"{safe_segment(primary.name, fallback='project')}-{root_hash}"
    scope_value = primary.name if scope == "." else scope
    scope_slug = safe_segment(scope_value, fallback="scope", limit=60)
    scope_hash = hashlib.sha256(f"{source_ref}\0{scope}".encode("utf-8")).hexdigest()[
        :8
    ]
    scope_id = f"{scope_slug}-{scope_hash}"
    return project_id, scope_id, scope_slug


def task_lane_identity(
    *, common_dir: Path, primary: Path, source_ref: str, scope: str
) -> str:
    """Return the Worktree-owned deterministic identity for one project lane."""

    payload = "\0".join((str(common_dir), str(primary), source_ref, scope))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def project_workspace_manifest(project_path: Path, codex_home: Path) -> Path:
    """Resolve the private workspace manifest for one exact project folder."""

    requested = project_path.expanduser().resolve()
    root, source_root, scope = repo_and_scope(requested, str(requested))
    project_id, scope_id, _ = workspace_identity(root, source_root, scope)
    return (
        codex_home.expanduser().resolve()
        / "task-implementer"
        / "projects"
        / project_id
        / "scopes"
        / scope_id
        / "workspace.json"
    )


def legacy_project_workspace_manifest(project_path: Path, codex_home: Path) -> Path:
    """Return the former primary-checkout workspace-v1 location."""

    requested = project_path.expanduser().resolve()
    root, _, scope = repo_and_scope(requested, str(requested))
    _, primary, _, _, _, _ = workspace_git_identity(root)
    root = primary
    root_hash = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    project_id = f"{safe_segment(root.name, fallback='project')}-{root_hash}"
    scope_value = root.name if scope == "." else scope
    scope_slug = safe_segment(scope_value, fallback="scope", limit=60)
    scope_hash = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:8]
    scope_id = f"{scope_slug}-{scope_hash}"
    return (
        codex_home.expanduser().resolve()
        / "task-implementer"
        / "projects"
        / project_id
        / "scopes"
        / scope_id
        / "workspace.json"
    )


def template_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "prompt-template.md"


def hub_template_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "prompt-workspace-hub.md"


def ensure_prompt_hub(prompt_root: Path) -> Path:
    hub_path = prompt_root / HUB_FILENAME
    expected = hub_template_path().read_bytes()
    if hub_path.exists() or hub_path.is_symlink():
        if hub_path.is_symlink() or not hub_path.is_file():
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID", "prompt workspace hub is unsafe"
            )
        require_mode(hub_path, 0o600, "prompt workspace hub")
        if hub_path.read_bytes() != expected:
            write_atomic(hub_path, expected)
    else:
        write_exclusive(hub_path, expected)
    return hub_path


def render_prompt(
    ask: str,
    prompt_id: str,
    created_at: datetime,
    *,
    title: str | None = None,
    draft: bool = False,
) -> bytes:
    rendered_ask = ask
    rendered_title = title or ask
    if draft:
        rendered_ask = "<!-- Required: replace this comment with your Ask. -->"
        rendered_title = title or "Untitled prompt"
    template = template_path().read_text(encoding="utf-8")
    replacements = {
        "{{PROMPT_ID}}": prompt_id,
        "{{TITLE_JSON}}": json.dumps(rendered_title, ensure_ascii=False),
        "{{CREATED_AT}}": iso_seconds(created_at),
        "{{TITLE}}": rendered_title,
        "{{ASK}}": rendered_ask,
    }
    rendered = template
    for marker, value in replacements.items():
        rendered = rendered.replace(marker, value)
    if re.search(r"\{\{[A-Z0-9_]+\}\}", rendered):
        raise PromptWorkspaceError(
            "PROMPT_TEMPLATE_INVALID", "prompt template has unresolved markers"
        )
    return rendered.encode("utf-8")


def workspace_document(
    manifest_path: Path,
    source_root: Path,
    helper_path: Path,
    python_executable: Path,
) -> dict[str, object]:
    return {
        "folders": [
            {"name": "CODE", "path": str(source_root)},
            {"name": "PROMPTS", "path": "prompts"},
        ],
        "settings": {"workbench.editor.labelFormat": "medium"},
        "tasks": {
            "version": "2.0.0",
            "tasks": [
                {
                    "label": "Task Implementer: New Prompt",
                    "type": "process",
                    "command": str(python_executable),
                    "args": [
                        str(helper_path),
                        "new",
                        "--workspace",
                        str(manifest_path),
                        "--ask",
                        "${input:promptTitle}",
                        "--open",
                    ],
                    "problemMatcher": [],
                    "group": {"kind": "build", "isDefault": True},
                },
                {
                    "label": "Task Implementer: Prompt Queue",
                    "type": "process",
                    "command": str(python_executable),
                    "args": [
                        str(helper_path),
                        "queue-list",
                        "--workspace",
                        str(manifest_path),
                    ],
                    "problemMatcher": [],
                },
                {
                    "label": "Task Implementer: Cancel Queued Prompt",
                    "type": "process",
                    "command": str(python_executable),
                    "args": [
                        str(helper_path),
                        "queue-cancel",
                        "--workspace",
                        str(manifest_path),
                        "--prompt",
                        "${input:queuedPromptFilename}",
                    ],
                    "problemMatcher": [],
                },
            ],
            "inputs": [
                {
                    "id": "promptTitle",
                    "type": "promptString",
                    "description": (
                        "Short non-sensitive ask used for title and filename"
                    ),
                },
                {
                    "id": "queuedPromptFilename",
                    "type": "promptString",
                    "description": "Exact queued prompt filename to cancel",
                },
            ],
        },
    }


def init_workspace(
    repo_path: Path,
    scope_value: str,
    codex_home: Path,
    *,
    lane: dict[str, object] | None = None,
    clock: Callable[[], datetime] = now_local,
) -> dict[str, object]:
    root, source_root, scope = repo_and_scope(repo_path, scope_value)
    if lane is None:
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "workspace-v2 requires a Worktree-owned Task Implementer lane",
        )
    common_dir = Path(required_string(lane, "common_dir", "task lane"))
    primary = Path(required_string(lane, "primary", "task lane"))
    source_ref = required_string(lane, "source_ref", "task lane")
    source_branch = required_string(lane, "source_branch", "task lane")
    lane_id = required_string(lane, "lane_id", "task lane")
    lane_branch = required_string(lane, "branch", "task lane")
    (
        actual_common_dir,
        actual_primary,
        actual_source_ref,
        actual_source_branch,
        actual_branch,
        actual_lane_id,
    ) = workspace_git_identity(root)
    if (
        str(root) != required_string(lane, "worktree", "task lane")
        or scope != required_string(lane, "scope", "task lane")
        or str(source_root) != required_string(lane, "scope_cwd", "task lane")
        or common_dir != actual_common_dir
        or primary != actual_primary
        or source_ref != actual_source_ref
        or source_branch != actual_source_branch
        or lane_branch != actual_branch
        or lane_id != actual_lane_id
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "Task Implementer lane scope is inconsistent"
        )
    home = codex_home.expanduser().resolve()
    requested_state_root = home / "task-implementer"
    if requested_state_root.is_symlink():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID",
            "task-implementer state root must not be a symlink",
        )
    state_root = requested_state_root.resolve()
    if (
        is_relative_to(state_root, root)
        or enclosing_git_storage(state_root) is not None
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID",
            "task-implementer state must be outside Git worktrees and metadata",
        )

    project_id, scope_id, scope_slug = workspace_identity(
        root,
        source_root,
        scope,
        common_dir=common_dir,
        primary=primary,
        source_ref=source_ref,
    )
    scope_dir = state_root / "projects" / project_id / "scopes" / scope_id
    prompt_root = scope_dir / "prompts"
    runs_root = scope_dir / "runs"
    manifest_path = scope_dir / "workspace.json"
    vscode_path = scope_dir / f"{scope_slug}-prompts.code-workspace"

    for directory in (
        state_root,
        state_root / "projects",
        state_root / "projects" / project_id,
        state_root / "projects" / project_id / "scopes",
        scope_dir,
        prompt_root,
        runs_root,
    ):
        ensure_private_dir(directory)

    created = clock()
    if created.tzinfo is None or created.utcoffset() is None:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", "workspace creation clock must be timezone-aware"
        )
    created_at = iso_seconds(created)
    if manifest_path.exists():
        existing = load_json_object(manifest_path, "workspace manifest")
        created = existing.get("created_at")
        if isinstance(created, str):
            created_at = created

    manifest: dict[str, object] = {
        "schema": WORKSPACE_SCHEMA,
        "project_id": project_id,
        "scope_id": scope_id,
        "repo_root": str(root),
        "primary_root": str(primary),
        "common_dir": str(common_dir),
        "source_branch": source_branch,
        "source_ref": source_ref,
        "scope": scope,
        "source_root": str(source_root),
        "lane_id": lane_id,
        "lane_name": required_string(lane, "name", "task lane"),
        "lane_branch": lane_branch,
        "lane_incarnation": lane.get("incarnation"),
        "prompt_root": str(prompt_root),
        "runs_root": str(runs_root),
        "vscode_workspace": str(vscode_path),
        "created_at": created_at,
    }
    if manifest_path.exists():
        existing = load_json_object(manifest_path, "workspace manifest")
        if existing != manifest:
            immutable_keys = {
                "schema",
                "project_id",
                "scope_id",
                "primary_root",
                "common_dir",
                "source_branch",
                "source_ref",
                "scope",
                "lane_id",
                "prompt_root",
                "runs_root",
                "vscode_workspace",
                "created_at",
            }
            old_incarnation = existing.get("lane_incarnation")
            new_incarnation = manifest.get("lane_incarnation")
            rebindable = (
                set(existing) == set(manifest)
                and all(
                    existing.get(key) == manifest.get(key) for key in immutable_keys
                )
                and isinstance(old_incarnation, int)
                and isinstance(new_incarnation, int)
                and new_incarnation > old_incarnation
            )
            if not rebindable:
                raise PromptWorkspaceError(
                    "WORKSPACE_MISMATCH",
                    "existing workspace manifest does not match the canonical checkout",
                )
            write_atomic(manifest_path, stable_json(manifest))
        private_chmod(manifest_path, 0o600)
    else:
        try:
            write_exclusive(manifest_path, stable_json(manifest))
        except FileExistsError:
            concurrent = load_json_object(manifest_path, "workspace manifest")
            manifest["created_at"] = concurrent.get("created_at")
            if concurrent != manifest:
                raise PromptWorkspaceError(
                    "WORKSPACE_MISMATCH",
                    "concurrent workspace initialization produced different state",
                )

    if vscode_path.is_symlink():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "VS Code workspace must not be a symlink"
        )
    vscode = workspace_document(
        manifest_path.resolve(),
        source_root,
        Path(__file__).resolve().with_name("prompt_workspace.py"),
        Path(sys.executable).resolve(),
    )
    vscode_bytes = stable_json(vscode)
    if not vscode_path.exists() or vscode_path.read_bytes() != vscode_bytes:
        write_atomic(vscode_path, vscode_bytes)
    else:
        private_chmod(vscode_path, 0o600)

    verified = verify_workspace(manifest_path)
    return {
        "workspace": str(manifest_path),
        "vscode_workspace": str(vscode_path),
        "prompt_root": str(prompt_root),
        "project_id": verified["project_id"],
        "scope_id": verified["scope_id"],
        "lane_id": verified["lane_id"],
        "lane_state": lane.get("lane_state"),
        "lane_status": lane.get("status"),
        "scope_cwd": str(source_root),
    }


def required_string(value: dict[str, object], key: str, label: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", f"{label} is missing string field {key}"
        )
    return result


def verify_workspace(manifest_path: Path) -> dict[str, object]:
    requested = manifest_path.expanduser()
    if requested.is_symlink():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "workspace manifest must not be a symlink"
        )
    path = requested.resolve()
    manifest = load_json_object(path, "workspace manifest")
    if manifest.get("schema") != WORKSPACE_SCHEMA:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", "workspace schema is unsupported"
        )

    root_value = Path(required_string(manifest, "repo_root", "workspace manifest"))
    if not root_value.is_absolute() or root_value.is_symlink():
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace Git root is not canonical"
        )
    root = root_value.resolve()
    if str(root_value) != str(root):
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace Git root is not canonical"
        )
    if canonical_git_root(root) != root:
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace Git root no longer resolves canonically"
        )
    (
        common_dir,
        primary,
        source_ref,
        source_branch,
        current_branch,
        current_lane_id,
    ) = workspace_git_identity(root)
    if (
        required_string(manifest, "primary_root", "workspace manifest") != str(primary)
        or required_string(manifest, "common_dir", "workspace manifest")
        != str(common_dir)
        or required_string(manifest, "source_ref", "workspace manifest") != source_ref
        or required_string(manifest, "source_branch", "workspace manifest")
        != source_branch
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace logical repository identity changed"
        )
    lane_id = required_string(manifest, "lane_id", "workspace manifest")
    lane_name = required_string(manifest, "lane_name", "workspace manifest")
    lane_branch = required_string(manifest, "lane_branch", "workspace manifest")
    lane_incarnation = manifest.get("lane_incarnation")
    if (
        re.fullmatch(r"[0-9a-f]{32}", lane_id) is None
        or not lane_name.startswith("project-")
        or lane_branch != f"feature/{lane_name.removeprefix('project-')}"
        or lane_branch != current_branch
        or lane_id != current_lane_id
        or not isinstance(lane_incarnation, int)
        or lane_incarnation < 1
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID",
            "workspace Task Implementer lane identity is invalid",
        )
    source_value = Path(required_string(manifest, "source_root", "workspace manifest"))
    if not source_value.is_absolute() or source_value.is_symlink():
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace source root is not canonical"
        )
    source_root = source_value.resolve()
    if str(source_value) != str(source_root) or not is_relative_to(source_root, root):
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace source root is not canonical"
        )
    scope = required_string(manifest, "scope", "workspace manifest")
    expected_scope = (
        source_root.relative_to(root).as_posix() if source_root != root else "."
    )
    expected_source = (
        root if expected_scope == "." else (root / expected_scope).resolve()
    )
    if (
        scope != expected_scope
        or source_root != expected_source
        or not source_root.is_dir()
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH",
            "workspace source scope no longer matches the Git root",
        )

    project_id, scope_id, scope_slug = workspace_identity(
        root,
        source_root,
        scope,
        common_dir=common_dir,
        primary=primary,
        source_ref=source_ref,
    )
    if manifest.get("project_id") != project_id or manifest.get("scope_id") != scope_id:
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace identity does not match canonical paths"
        )

    scope_dir = path.parent
    scopes_dir = scope_dir.parent
    project_dir = scopes_dir.parent
    projects_dir = project_dir.parent
    state_root = projects_dir.parent
    if (
        scope_dir.name != scope_id
        or scopes_dir.name != "scopes"
        or project_dir.name != project_id
        or projects_dir.name != "projects"
        or state_root.name != "task-implementer"
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "workspace path does not match its generated identity"
        )
    if enclosing_git_storage(state_root) is not None:
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "private workspace is inside Git storage"
        )

    prompt_value = Path(required_string(manifest, "prompt_root", "workspace manifest"))
    runs_value = Path(required_string(manifest, "runs_root", "workspace manifest"))
    vscode_value = Path(
        required_string(manifest, "vscode_workspace", "workspace manifest")
    )
    for value, label in (
        (prompt_value, "prompt directory"),
        (runs_value, "runs directory"),
        (vscode_value, "VS Code workspace"),
    ):
        if not value.is_absolute() or value.is_symlink():
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID", f"{label} is missing or unsafe"
            )
    prompt_root = prompt_value.resolve()
    runs_root = runs_value.resolve()
    vscode_path = vscode_value.resolve()
    if any(
        str(value) != str(resolved)
        for value, resolved in (
            (prompt_value, prompt_root),
            (runs_value, runs_root),
            (vscode_value, vscode_path),
        )
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "workspace paths must be canonical"
        )
    if prompt_root != (scope_dir / "prompts").resolve():
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "prompt root is not owned by the workspace"
        )
    if runs_root != (scope_dir / "runs").resolve():
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "runs root is not owned by the workspace"
        )
    if vscode_path != scope_dir / f"{scope_slug}-prompts.code-workspace":
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH", "VS Code workspace is outside the private scope"
        )
    for directory, label in (
        (state_root, "task-implementer state directory"),
        (projects_dir, "projects directory"),
        (project_dir, "project directory"),
        (scopes_dir, "scopes directory"),
        (scope_dir, "workspace directory"),
        (prompt_root, "prompt directory"),
        (runs_root, "runs directory"),
    ):
        if directory.is_symlink() or not directory.is_dir():
            raise PromptWorkspaceError(
                "WORKSPACE_PATH_INVALID", f"{label} is missing or unsafe"
            )
        require_mode(directory, 0o700, label)
    require_mode(path, 0o600, "workspace manifest")
    require_mode(vscode_path, 0o600, "VS Code workspace")

    vscode = load_json_object(vscode_path, "VS Code workspace")
    try:
        task = vscode["tasks"]["tasks"][0]
        helper_path = Path(task["args"][0])
        python_executable = Path(task["command"])
    except (KeyError, IndexError, TypeError) as exc:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", "VS Code workspace task is invalid"
        ) from exc
    if (
        not helper_path.is_absolute()
        or helper_path.is_symlink()
        or not helper_path.is_file()
        or helper_path.name != "prompt_workspace.py"
        or not python_executable.is_absolute()
        or python_executable.is_symlink()
        or not python_executable.is_file()
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", "VS Code workspace command is unsafe"
        )
    expected_vscode = workspace_document(
        path,
        source_root,
        helper_path,
        python_executable,
    )
    if vscode != expected_vscode:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID",
            "VS Code workspace differs from the generated CODE/PROMPTS contract",
        )

    return manifest


def verify_workspace_for_removal(
    manifest_path: Path, project_path: Path
) -> dict[str, object]:
    """Verify stable workspace identity even after its lane path is absent."""

    requested_manifest = manifest_path.expanduser()
    if requested_manifest.is_symlink():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "workspace manifest must not be a symlink"
        )
    path = requested_manifest.resolve()
    manifest = load_json_object(path, "workspace manifest")
    if manifest.get("schema") != WORKSPACE_SCHEMA:
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "workspace remove requires workspace-v2 lane state",
        )
    requested = project_path.expanduser().resolve()
    root, source_root, scope = repo_and_scope(requested, str(requested))
    common_dir, primary, source_ref, source_branch, _, _ = workspace_git_identity(root)
    project_id, scope_id, scope_slug = workspace_identity(
        root,
        source_root,
        scope,
        common_dir=common_dir,
        primary=primary,
        source_ref=source_ref,
    )
    scope_dir = path.parent
    scopes_dir = scope_dir.parent
    project_dir = scopes_dir.parent
    projects_dir = project_dir.parent
    state_root = projects_dir.parent
    lane_id = required_string(manifest, "lane_id", "workspace manifest")
    lane_name = required_string(manifest, "lane_name", "workspace manifest")
    lane_branch = required_string(manifest, "lane_branch", "workspace manifest")
    lane_incarnation = manifest.get("lane_incarnation")
    expected_lane_id = task_lane_identity(
        common_dir=common_dir,
        primary=primary,
        source_ref=source_ref,
        scope=scope,
    )
    repo_value = Path(required_string(manifest, "repo_root", "workspace manifest"))
    source_value = Path(required_string(manifest, "source_root", "workspace manifest"))
    expected_source = repo_value if scope == "." else repo_value / scope
    expected_values = {
        "project_id": project_id,
        "scope_id": scope_id,
        "primary_root": str(primary),
        "common_dir": str(common_dir),
        "source_branch": source_branch,
        "source_ref": source_ref,
        "scope": scope,
        "prompt_root": str(path.parent / "prompts"),
        "runs_root": str(path.parent / "runs"),
        "vscode_workspace": str(path.parent / f"{scope_slug}-prompts.code-workspace"),
    }
    if (
        path.name != "workspace.json"
        or scope_dir.name != scope_id
        or scopes_dir.name != "scopes"
        or project_dir.name != project_id
        or projects_dir.name != "projects"
        or state_root.name != "task-implementer"
        or any(manifest.get(key) != value for key, value in expected_values.items())
        or lane_id != expected_lane_id
        or not repo_value.is_absolute()
        or repo_value.is_symlink()
        or repo_value.resolve() != repo_value
        or not source_value.is_absolute()
        or source_value.is_symlink()
        or source_value.resolve() != source_value
        or source_value != expected_source
        or not lane_name.startswith("project-ti-")
        or not lane_name.endswith(f"-{lane_id[:8]}-{lane_incarnation}")
        or lane_branch != f"feature/{lane_name.removeprefix('project-')}"
        or not isinstance(lane_incarnation, int)
        or lane_incarnation < 1
    ):
        raise PromptWorkspaceError(
            "WORKSPACE_MISMATCH",
            "workspace removal identity does not match the exact project lane",
        )
    return manifest


def parse_frontmatter(lines: list[str]) -> tuple[dict[str, str], int]:
    if not lines or lines[0] != "---":
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt must start with scalar frontmatter"
        )
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt frontmatter is not closed"
        ) from exc
    values: dict[str, str] = {}
    for line in lines[1:closing]:
        if not line or line.lstrip().startswith("#") or ":" not in line:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID",
                "prompt frontmatter must use simple key: value lines",
            )
        key, raw_value = line.split(":", 1)
        key = key.strip()
        if key in values:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", f"prompt frontmatter repeats key {key}"
            )
        if key not in FRONTMATTER_KEYS:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", f"prompt frontmatter key is unsupported: {key}"
            )
        value = raw_value.strip()
        if value.startswith('"'):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise PromptWorkspaceError(
                    "PROMPT_INPUT_INVALID",
                    f"prompt frontmatter value is invalid: {key}",
                ) from exc
            if not isinstance(decoded, str):
                raise PromptWorkspaceError(
                    "PROMPT_INPUT_INVALID",
                    f"prompt frontmatter value must be text: {key}",
                )
            value = decoded
        if not value:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", f"prompt frontmatter value is empty: {key}"
            )
        values[key] = value
    if set(values) != FRONTMATTER_KEYS:
        missing = sorted(FRONTMATTER_KEYS - set(values))
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID",
            f"prompt frontmatter is missing required keys: {', '.join(missing)}",
        )
    return values, closing + 1


def parse_sections(
    lines: list[str],
    start: int,
    *,
    required_sections: tuple[str, ...] = REQUIRED_SECTIONS,
) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    fence_character: str | None = None
    fence_length = 0
    for line in lines[start:]:
        fence = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence_character is not None:
            if current is not None:
                sections[current].append(line)
            if (
                fence is not None
                and fence.group(1)[0] == fence_character
                and len(fence.group(1)) >= fence_length
                and not fence.group(2).strip()
            ):
                fence_character = None
                fence_length = 0
            continue
        if fence is not None:
            marker = fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            if current is not None:
                sections[current].append(line)
            continue
        if line.startswith("## "):
            heading = line[3:].strip()
            if not heading:
                raise PromptWorkspaceError(
                    "PROMPT_INPUT_INVALID", "prompt contains an empty section heading"
                )
            if heading in sections:
                raise PromptWorkspaceError(
                    "PROMPT_INPUT_INVALID", f"prompt repeats section: {heading}"
                )
            sections[heading] = []
            current = heading
            continue
        if current is not None:
            sections[current].append(line)
    missing = [heading for heading in required_sections if heading not in sections]
    if missing:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID",
            f"prompt is missing sections: {', '.join(missing)}",
        )
    return {
        heading: "\n".join(content).strip() for heading, content in sections.items()
    }


def meaningful_section(value: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    without_markers = re.sub(r"(?m)^\s*[-*]\s*(?:\[[ xX]\])?\s*", "", without_comments)
    return without_markers.strip()


def normalize_intent_content(value: str) -> str:
    """Normalize prose formatting while preserving fenced-code semantics."""

    parts: list[str] = []
    prose: list[str] = []
    fence_character: str | None = None
    fence_length = 0

    def flush_prose() -> None:
        if not prose:
            return
        without_comments = re.sub(r"<!--.*?-->", "", "\n".join(prose), flags=re.DOTALL)
        normalized = re.sub(r"\s+", " ", without_comments).strip()
        if normalized:
            parts.append(f"prose:{normalized}")
        prose.clear()

    for line in value.splitlines():
        fence = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
        if fence_character is None:
            if fence is None:
                prose.append(line)
                continue
            flush_prose()
            marker = fence.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            info = re.sub(r"\s+", " ", fence.group(2)).strip()
            parts.append(f"fence:{fence_character}:{info}")
            continue
        if (
            fence is not None
            and fence.group(1)[0] == fence_character
            and len(fence.group(1)) >= fence_length
            and not fence.group(2).strip()
        ):
            parts.append("end-fence")
            fence_character = None
            fence_length = 0
            continue
        parts.append(f"code:{line}")
    flush_prose()
    return "\n".join(parts)


def prompt_intent_sha256(sections: dict[str, str]) -> str:
    normalized: list[tuple[str, str]] = []
    for heading, value in sections.items():
        content = normalize_intent_content(value)
        if content:
            normalized.append((heading, content))
    normalized.sort(key=lambda item: item[0].casefold())
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_prompt(
    path: Path,
    prompt_root: Path,
    *,
    require_content: bool,
    allow_legacy: bool = False,
) -> PromptDocument:
    requested = path.expanduser()
    if requested.is_symlink():
        raise PromptWorkspaceError(
            "PROMPT_PATH_INVALID", "prompt file must not be a symlink"
        )
    try:
        canonical = requested.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PromptWorkspaceError(
            "PROMPT_PATH_INVALID", "prompt file is missing"
        ) from exc
    if canonical.parent != prompt_root.resolve() or canonical.suffix.lower() != ".md":
        raise PromptWorkspaceError(
            "PROMPT_PATH_INVALID", "prompt must be a direct Markdown child of prompts/"
        )
    require_mode(canonical, 0o600, "prompt file")
    size = canonical.stat().st_size
    if size > MAX_PROMPT_BYTES:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", f"prompt exceeds {MAX_PROMPT_BYTES} bytes"
        )
    raw = canonical.read_bytes()
    if b"\x00" in raw:
        raise PromptWorkspaceError("PROMPT_INPUT_INVALID", "prompt contains a NUL byte")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt is not valid UTF-8"
        ) from exc
    if re.search(r"\{\{[A-Z0-9_]+\}\}", text):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt contains unresolved template markers"
        )
    if contains_secret(text):
        raise PromptWorkspaceError(
            "PROMPT_SENSITIVE_INPUT",
            "prompt appears to contain secret material; remove it before intake",
        )
    lines = text.splitlines()
    frontmatter, body_start = parse_frontmatter(lines)
    legacy = frontmatter["schema"] == LEGACY_PROMPT_SCHEMA
    if legacy and not allow_legacy:
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "prompt-v1 is read-only history; create a new prompt-v2 file",
        )
    if frontmatter["schema"] != PROMPT_SCHEMA and not (allow_legacy and legacy):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt schema is unsupported"
        )
    prompt_id = frontmatter["prompt_id"]
    if not PROMPT_ID_RE.fullmatch(prompt_id):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt_id does not match the generated format"
        )
    title = frontmatter["title"].strip()
    if (
        not title
        or len(title) > 200
        or any(ord(character) < 32 or ord(character) == 127 for character in title)
    ):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt title is empty, unsafe, or too long"
        )
    try:
        created_at = datetime.fromisoformat(frontmatter["created_at"])
    except ValueError as exc:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "created_at is not a valid ISO-8601 timestamp"
        ) from exc
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "created_at must include a UTC offset"
        )
    sections = parse_sections(
        lines,
        body_start,
        required_sections=() if legacy else REQUIRED_SECTIONS,
    )
    if require_content and not legacy:
        for heading in REQUIRED_SECTIONS:
            if not meaningful_section(sections.get(heading, "")):
                raise PromptWorkspaceError(
                    "PROMPT_INPUT_INVALID",
                    f"required prompt section is empty: {heading}",
                )
    return PromptDocument(
        path=canonical,
        raw=raw,
        text=text,
        prompt_id=prompt_id,
        title=title,
        created_at=created_at,
        sections=sections,
    )


def resolve_prompt_reference(
    manifest_path: Path,
    prompt_reference: str | Path,
    *,
    require_content: bool,
) -> PromptDocument:
    """Resolve an absolute prompt path or one flat-workspace filename."""

    manifest = verify_workspace(manifest_path)
    prompt_root = Path(required_string(manifest, "prompt_root", "workspace manifest"))
    reference = Path(prompt_reference).expanduser()
    if reference.is_absolute():
        candidate = reference
    else:
        if len(reference.parts) != 1 or reference.name in {"", ".", ".."}:
            raise PromptWorkspaceError(
                "PROMPT_PATH_INVALID",
                "prompt reference must be an absolute path or one prompt filename",
            )
        candidate = prompt_root / reference.name
    document = read_prompt(candidate, prompt_root, require_content=require_content)
    ensure_unique_prompt_id(document, prompt_root)
    return document


def ensure_unique_prompt_id(document: PromptDocument, prompt_root: Path) -> None:
    matches: list[Path] = []
    for candidate in sorted(prompt_root.glob("*.md")):
        if candidate.name == HUB_FILENAME:
            continue
        if candidate.is_symlink():
            continue
        try:
            raw = candidate.read_bytes()
            if len(raw) > MAX_PROMPT_BYTES or b"\x00" in raw:
                continue
            metadata, _ = parse_frontmatter(raw.decode("utf-8").splitlines())
        except (OSError, UnicodeDecodeError, PromptWorkspaceError):
            continue
        if (
            metadata.get("schema") == PROMPT_SCHEMA
            and metadata.get("prompt_id") == document.prompt_id
        ):
            matches.append(candidate.resolve())
    if len(matches) != 1 or matches[0] != document.path:
        raise PromptWorkspaceError(
            "PROMPT_CONFLICT", "prompt_id is duplicated within this prompt workspace"
        )


def create_prompt(
    manifest_path: Path,
    ask_value: str,
    *,
    clock: Callable[[], datetime] = now_local,
    id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    draft: bool = False,
) -> dict[str, object]:
    manifest = verify_workspace(manifest_path)
    prompt_root = Path(required_string(manifest, "prompt_root", "workspace manifest"))
    ask = validate_short_ask(ask_value) if not draft else "Untitled prompt"
    created_at = clock()
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt creation clock must be timezone-aware"
        )
    prompt_id = f"prompt-{id_factory()}"
    if not PROMPT_ID_RE.fullmatch(prompt_id):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "generated prompt ID has an invalid format"
        )
    prefix = created_at.strftime("%Y-%m-%d_%H%M")
    stem = f"{prefix}--{prompt_slug(ask)}"
    content = render_prompt(
        ask,
        prompt_id,
        created_at,
        title="Untitled prompt" if draft else None,
        draft=draft,
    )
    for number in range(1, 1000):
        suffix = "" if number == 1 else f"--{number:02d}"
        prompt_path = prompt_root / f"{stem}{suffix}.md"
        try:
            write_exclusive(prompt_path, content)
        except FileExistsError:
            continue
        return {
            "path": str(prompt_path),
            "prompt_id": prompt_id,
            "title": ask,
            "created_at": iso_seconds(created_at),
        }
    raise PromptWorkspaceError(
        "PROMPT_CONFLICT", "could not allocate a unique prompt filename"
    )
