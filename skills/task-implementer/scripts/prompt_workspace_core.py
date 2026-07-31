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


WORKSPACE_SCHEMA = "task-implementer/workspace-v1"
PROMPT_SCHEMA = "task-implementer/prompt-v1"
RUN_SCHEMA = "task-implementer/run-manifest-v1"
MAX_PROMPT_BYTES = 256 * 1024
TERMINAL_RUN_STATUSES = {"done", "superseded", "abandoned"}
PROMPT_ID_RE = re.compile(r"prompt-[0-9a-f]{32}\Z")
RUN_ID_RE = re.compile(r"run-[a-z0-9][a-z0-9-]{0,79}\Z")
REVISION_RE = re.compile(r"r([0-9]{4})\Z")
FRONTMATTER_KEYS = {"schema", "prompt_id", "title", "created_at"}
REQUIRED_SECTIONS = ("Ask", "Outcome", "Acceptance criteria", "Verification")
ALL_SECTIONS = (
    "Ask",
    "Outcome",
    "Context",
    "Constraints",
    "Acceptance criteria",
    "Verification",
    "Non-goals",
    "References",
)
OPTIONAL_SECTIONS = ("Steering",)


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
    return value


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


def workspace_identity(
    root: Path, source_root: Path, scope: str
) -> tuple[str, str, str]:
    root_hash = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    project_id = f"{safe_segment(root.name, fallback='project')}-{root_hash}"
    scope_value = root.name if scope == "." else scope
    scope_slug = safe_segment(scope_value, fallback="scope", limit=60)
    scope_hash = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:8]
    scope_id = f"{scope_slug}-{scope_hash}"
    return project_id, scope_id, scope_slug


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


def template_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "prompt-template.md"


def render_prompt(ask: str, prompt_id: str, created_at: datetime) -> bytes:
    template = template_path().read_text(encoding="utf-8")
    replacements = {
        "{{PROMPT_ID}}": prompt_id,
        "{{TITLE_JSON}}": json.dumps(ask, ensure_ascii=False),
        "{{CREATED_AT}}": iso_seconds(created_at),
        "{{TITLE}}": ask,
        "{{ASK}}": ask,
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
                }
            ],
            "inputs": [
                {
                    "id": "promptTitle",
                    "type": "promptString",
                    "description": (
                        "Short non-sensitive ask used for title and filename"
                    ),
                }
            ],
        },
    }


def init_workspace(
    repo_path: Path,
    scope_value: str,
    codex_home: Path,
    *,
    clock: Callable[[], datetime] = now_local,
) -> dict[str, object]:
    root, source_root, scope = repo_and_scope(repo_path, scope_value)
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

    project_id, scope_id, scope_slug = workspace_identity(root, source_root, scope)
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
        "scope": scope,
        "source_root": str(source_root),
        "prompt_root": str(prompt_root),
        "runs_root": str(runs_root),
        "vscode_workspace": str(vscode_path),
        "created_at": created_at,
    }
    if manifest_path.exists():
        existing = load_json_object(manifest_path, "workspace manifest")
        if existing != manifest:
            raise PromptWorkspaceError(
                "WORKSPACE_MISMATCH",
                "existing workspace manifest does not match the canonical checkout",
            )
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

    project_id, scope_id, scope_slug = workspace_identity(root, source_root, scope)
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


def parse_sections(lines: list[str], start: int) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[start:]:
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading in sections:
                raise PromptWorkspaceError(
                    "PROMPT_INPUT_INVALID", f"prompt repeats section: {heading}"
                )
            if heading not in {*ALL_SECTIONS, *OPTIONAL_SECTIONS}:
                current = None
                continue
            sections[heading] = []
            current = heading
            continue
        if current is not None:
            sections[current].append(line)
    missing = [heading for heading in ALL_SECTIONS if heading not in sections]
    if missing:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID",
            f"prompt is missing sections: {', '.join(missing)}",
        )
    result = {
        heading: "\n".join(content).strip() for heading, content in sections.items()
    }
    for heading in OPTIONAL_SECTIONS:
        result.setdefault(heading, "")
    return result


def meaningful_section(value: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    without_markers = re.sub(r"(?m)^\s*[-*]\s*(?:\[[ xX]\])?\s*", "", without_comments)
    return without_markers.strip()


def read_prompt(
    path: Path, prompt_root: Path, *, require_content: bool
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
    lines = text.splitlines()
    frontmatter, body_start = parse_frontmatter(lines)
    if frontmatter["schema"] != PROMPT_SCHEMA:
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
    sections = parse_sections(lines, body_start)
    if require_content:
        for heading in REQUIRED_SECTIONS:
            if not meaningful_section(sections[heading]):
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
        if candidate.is_symlink():
            raise PromptWorkspaceError(
                "PROMPT_PATH_INVALID", "prompt directory contains a symlink"
            )
        other = read_prompt(candidate, prompt_root, require_content=False)
        if other.prompt_id == document.prompt_id:
            matches.append(other.path)
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
) -> dict[str, object]:
    manifest = verify_workspace(manifest_path)
    prompt_root = Path(required_string(manifest, "prompt_root", "workspace manifest"))
    ask = validate_short_ask(ask_value)
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
    content = render_prompt(ask, prompt_id, created_at)
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
