#!/usr/bin/env python3
"""Private mechanical prompt workspace for the Agentic SDLC coordinator."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
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

try:
    import fcntl
except ImportError:  # pragma: no cover - Agentic SDLC currently targets POSIX hosts.
    fcntl = None  # type: ignore[assignment]


WORKTREE_SCRIPTS = Path(__file__).resolve().parents[2] / "worktree" / "scripts"
if str(WORKTREE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WORKTREE_SCRIPTS))
from git_promotion import (  # noqa: E402
    GitPromotionError,
    common_git_dir,
    current_branch,
    ensure_promotion_branch,
    repository_root,
    resolve_remote_default,
)


WORKSPACE_SCHEMA = "agentic-sdlc/prompt-workspace-v1"
PROMPT_SCHEMA = "agentic-sdlc/prompt-v1"
BINDING_SCHEMA = "agentic-sdlc/prompt-binding-v1"
ACTIVITY_SCHEMA = "agentic-sdlc/prompt-activity-v1"
PROMOTION_SCHEMA = "agentic-sdlc/git-promotion-v1"
MAX_PROMPT_BYTES = 256 * 1024
PROMPT_ID_RE = re.compile(r"prompt-[0-9a-f]{32}\Z")
RUN_ID_RE = re.compile(r"run-[a-z0-9][a-z0-9-]{0,79}\Z")
REVISION_RE = re.compile(r"r([0-9]{4})\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
FRONTMATTER_KEYS = {"schema", "prompt_id", "title", "created_at"}
REQUIRED_SECTIONS = ("Ask", "Outcome", "Acceptance criteria", "Verification")
ALL_SECTIONS = (
    "Ask",
    "Outcome",
    "Context",
    "Constraints",
    "Acceptance criteria",
    "Verification",
    "Live Experiment Environment",
    "Non-goals",
    "References",
    "Steering",
)
TERMINAL_STATUSES = {
    "complete",
    "completed",
    "done",
    "merged",
    "superseded",
    "abandoned",
}
STEERING_DISPOSITIONS = {"applied", "blocked", "no_effect"}
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
        r"(?i)\b(password|secret|token)\b\s*[:=]\s*[\"']?[A-Za-z0-9_./+=:-]{12,}"
    ),
)


class PromptWorkspaceError(Exception):
    """Expected workspace validation or transition failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def iso_seconds(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.isoformat(timespec="seconds")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stable_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


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
            "WORKSPACE_PATH_INVALID", f"{label} is inaccessible"
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
            "WORKSPACE_PATH_INVALID", f"unsafe directory: {path}"
        )
    private_chmod(path, 0o700)


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


def write_exclusive(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    private_chmod(path, 0o600)


def load_json(path: Path, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", f"{label} is missing or unsafe"
        )
    require_mode(path, 0o600, label)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", f"{label} is invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", f"{label} must be an object"
        )
    return value


def safe_segment(value: str, fallback: str, limit: int = 60) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode().lower()
    segment = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")[:limit].rstrip("-")
    return segment or fallback


def valid_prompt_filename(value: object) -> bool:
    filename = str(value or "")
    return (
        bool(filename)
        and Path(filename).name == filename
        and filename.endswith(".md")
        and len(filename) <= 255
        and not any(
            ord(character) < 32 or ord(character) == 127 for character in filename
        )
    )


def contains_secret(text: str) -> bool:
    for line in text.splitlines() or [text]:
        if any(pattern.search(line) for pattern in SECRET_PATTERNS):
            return True
    return False


def meaningful_section(value: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL)
    without_markers = re.sub(r"(?m)^\s*[-*]\s*(?:\[[ xX]\])?\s*", "", without_comments)
    return without_markers.strip()


def project_identity(project_root: Path) -> tuple[str, str]:
    canonical = str(project_root).encode()
    digest = hashlib.sha256(canonical).hexdigest()[:12]
    return f"{safe_segment(project_root.name, 'project')}-{digest}", digest


def git_metadata(project_root: Path) -> tuple[str | None, str | None]:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, None
    if result.returncode != 0 or not result.stdout.strip():
        return None, None
    root = Path(result.stdout.strip()).resolve()
    try:
        scope = project_root.relative_to(root).as_posix() or "."
    except ValueError:
        return None, None
    return str(root), scope


def ensure_run_promotion(
    workspace: dict[str, object], run_dir: Path
) -> dict[str, object] | None:
    git_root_value = workspace.get("git_root")
    if git_root_value is None:
        return None
    git_root = Path(str(git_root_value)).resolve()
    path = run_dir / "git-promotion.json"
    if path.exists():
        value = load_json(path, "Git promotion state")
        required = {
            "schema",
            "run_id",
            "git_root",
            "git_common_dir",
            "promotion_branch",
            "promotion_initial_head",
            "promotion_source",
            "default_remote",
            "default_branch",
            "default_ref",
            "default_head",
        }
        try:
            default = resolve_remote_default(git_root)
            observed_root = repository_root(git_root)
            observed_common = common_git_dir(git_root)
            observed_branch = current_branch(git_root)
        except GitPromotionError as exc:
            raise PromptWorkspaceError("GIT_PROMOTION_BLOCKED", str(exc)) from exc
        if (
            set(value) != required
            or value.get("schema") != PROMOTION_SCHEMA
            or value.get("run_id") != run_dir.name
            or observed_root != git_root
            or value.get("git_root") != str(observed_root)
            or value.get("git_common_dir") != str(observed_common)
            or value.get("promotion_branch") != observed_branch
            or value.get("default_remote") != default["remote"]
            or value.get("default_branch") != default["default_branch"]
            or value.get("default_ref") != default["default_ref"]
            or value.get("default_remote") != "origin"
            or value.get("promotion_source") not in {"existing", "auto-created"}
            or re.fullmatch(
                r"[0-9a-f]{40,64}", str(value.get("promotion_initial_head") or "")
            )
            is None
            or re.fullmatch(
                r"[0-9a-f]{40,64}", str(value.get("default_head") or "")
            )
            is None
        ):
            raise PromptWorkspaceError(
                "GIT_PROMOTION_BLOCKED",
                "recorded promotion branch or repository identity changed",
            )
        return value
    try:
        promotion = ensure_promotion_branch(
            git_root,
            lifecycle_id=run_dir.name,
            task_slug="sdlc",
        )
    except GitPromotionError as exc:
        raise PromptWorkspaceError("GIT_PROMOTION_BLOCKED", str(exc)) from exc
    value = {
        "schema": PROMOTION_SCHEMA,
        "run_id": run_dir.name,
        "git_root": promotion["checkout"],
        "git_common_dir": promotion["git_common_dir"],
        "promotion_branch": promotion["promotion_branch"],
        "promotion_initial_head": promotion["promotion_initial_head"],
        "promotion_source": promotion["promotion_source"],
        "default_remote": promotion["remote"],
        "default_branch": promotion["default_branch"],
        "default_ref": promotion["default_ref"],
        "default_head": promotion["default_head"],
    }
    write_atomic(path, stable_json(value))
    return value


def reject_git_private_root(codex_home: Path) -> None:
    existing = codex_home
    while not existing.exists() and existing.parent != existing:
        existing = existing.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(existing), "rev-parse", "--show-toplevel"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return
    if result.returncode == 0 and result.stdout.strip():
        git_root = Path(result.stdout.strip()).resolve()
        try:
            codex_home.resolve().relative_to(git_root)
        except ValueError:
            return
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "CODEX_HOME must be outside Git worktrees"
        )


def workspace_path(project_root: Path, codex_home: Path) -> Path:
    project_id, _ = project_identity(project_root)
    return codex_home / "sdlc-runs" / project_id / "workspace.json"


def validate_workspace(path: Path) -> dict[str, object]:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "project state directory is unsafe"
        )
    value = load_json(path, "workspace manifest")
    if value.get("schema") != WORKSPACE_SCHEMA:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", "workspace schema is invalid"
        )
    project_root = Path(str(value.get("project_root") or "")).resolve()
    expected, _ = project_identity(project_root)
    if value.get("project_id") != expected or path.parent.name != expected:
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", "workspace identity is invalid"
        )
    prompt_root = path.parent / "prompts"
    if prompt_root.is_symlink() or not prompt_root.is_dir():
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "prompt directory is unsafe"
        )
    require_mode(path.parent, 0o700, "project state directory")
    require_mode(prompt_root, 0o700, "prompt directory")
    return value


def template_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "prompt-template.md"


def validate_short_ask(value: str) -> str:
    ask = " ".join(value.split())
    if not ask or len(ask) > 160 or contains_secret(ask):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID",
            "prompt title must be non-sensitive text between 1 and 160 characters",
        )
    return ask


def render_prompt(prompt_id: str, created_at: datetime, ask_value: str) -> bytes:
    ask = validate_short_ask(ask_value)
    template = template_path().read_text(encoding="utf-8")
    replacements = {
        "{{PROMPT_ID}}": prompt_id,
        "{{TITLE_JSON}}": json.dumps(ask),
        "{{CREATED_AT}}": iso_seconds(created_at),
        "{{TITLE}}": ask,
        "{{ASK}}": ask,
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    if re.search(r"\{\{[A-Z0-9_]+\}\}", template):
        raise PromptWorkspaceError(
            "PROMPT_TEMPLATE_INVALID", "prompt template is incomplete"
        )
    return template.encode()


def allocate_prompt(
    prompt_root: Path,
    ask_value: str,
    created_at: datetime,
    *,
    id_factory=lambda: uuid.uuid4().hex,
) -> Path:
    ask = validate_short_ask(ask_value)
    prompt_id = f"prompt-{uuid.uuid4().hex}"
    if id_factory is not None:
        prompt_id = f"prompt-{id_factory()}"
    if not PROMPT_ID_RE.fullmatch(prompt_id):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "generated prompt ID is invalid"
        )
    stem = f"{created_at.strftime('%Y%m%dT%H%M%SZ')}--{safe_segment(ask, 'prompt')}"
    content = render_prompt(prompt_id, created_at, ask)
    for number in range(1, 1000):
        suffix = "" if number == 1 else f"--{number:02d}"
        path = prompt_root / f"{stem}{suffix}.md"
        try:
            write_exclusive(path, content)
        except FileExistsError:
            continue
        return path
    raise PromptWorkspaceError(
        "PROMPT_CONFLICT", "could not allocate a unique prompt filename"
    )


def create_starter(prompt_root: Path, created_at: datetime) -> Path:
    return allocate_prompt(
        prompt_root, "Describe the product or feature to build", created_at
    )


def code_workspace(
    project_root: Path, project_dir: Path, manifest_path: Path
) -> dict[str, object]:
    filename = f"{safe_segment(project_root.name, 'project')}-prompts.code-workspace"
    path = project_dir / filename
    value = {
        "folders": [
            {"name": "CODE", "path": str(project_root)},
            {"name": "PROMPTS", "path": str(project_dir / "prompts")},
        ],
        "settings": {"files.exclude": {"**/.DS_Store": True}},
        "tasks": {
            "version": "2.0.0",
            "tasks": [
                {
                    "label": "Agentic SDLC: New Prompt",
                    "type": "process",
                    "command": str(Path(sys.executable).resolve()),
                    "args": [
                        str(Path(__file__).resolve()),
                        "new",
                        "--workspace",
                        str(manifest_path),
                        "--ask",
                        "${input:promptTitle}",
                        "--open",
                    ],
                    "problemMatcher": [],
                },
                {
                    "label": "Agentic SDLC: Prompt History",
                    "type": "process",
                    "command": str(Path(sys.executable).resolve()),
                    "args": [
                        str(Path(__file__).resolve()),
                        "list",
                        "--workspace",
                        str(manifest_path),
                    ],
                    "problemMatcher": [],
                },
            ],
            "inputs": [
                {
                    "id": "promptTitle",
                    "type": "promptString",
                    "description": "Short non-sensitive product or feature ask",
                }
            ],
        },
    }
    write_atomic(path, stable_json(value))
    return {"path": str(path), "value": value}


def initialize(
    project_path: Path, codex_home: Path, open_editor: bool, editor: str
) -> dict[str, object]:
    project_root = project_path.expanduser().resolve()
    if not project_root.is_dir():
        raise PromptWorkspaceError("PROJECT_ROOT_INVALID", "project folder must exist")
    codex_home = codex_home.expanduser().resolve()
    reject_git_private_root(codex_home)
    manifest_path = workspace_path(project_root, codex_home)
    project_dir = manifest_path.parent
    prompt_root = project_dir / "prompts"
    ensure_private_dir(codex_home / "sdlc-runs")
    ensure_private_dir(project_dir)
    ensure_private_dir(prompt_root)
    with prompt_lock(project_dir):
        project_id, _ = project_identity(project_root)
        git_root, scope = git_metadata(project_root)
        created_at = now_utc()
        if manifest_path.exists():
            manifest = validate_workspace(manifest_path)
            if Path(str(manifest.get("project_root"))).resolve() != project_root:
                raise PromptWorkspaceError(
                    "WORKSPACE_STATE_INVALID", "workspace project root changed"
                )
            manifest["git_root"] = git_root
            manifest["git_scope"] = scope
        else:
            manifest = {
                "schema": WORKSPACE_SCHEMA,
                "project_id": project_id,
                "project_root": str(project_root),
                "git_root": git_root,
                "git_scope": scope,
                "created_at": iso_seconds(created_at),
            }
        write_atomic(manifest_path, stable_json(manifest))
        prompts = sorted(prompt_root.glob("*.md"))
        starter_created = False
        if not prompts:
            prompts = [create_starter(prompt_root, created_at)]
            starter_created = True
        workspace = code_workspace(project_root, project_dir, manifest_path)
        prompt_metadata = prompt_rows(manifest_path, None, None)
    if open_editor:
        try:
            subprocess.Popen(
                [editor, str(workspace["path"])],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass
    return {
        "action": "initialized",
        "workspace": str(manifest_path),
        "project_root": str(project_root),
        "starter_prompt": str(prompts[0]),
        "starter_created": starter_created,
        "editor_workspace": workspace["path"],
        "prompts": prompt_metadata,
    }


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt frontmatter is missing"
        )
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt frontmatter is unterminated"
        ) from exc
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or ":" not in line:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", "prompt frontmatter is invalid"
            )
        key, raw = line.split(":", 1)
        key = key.strip()
        if key in metadata or key not in FRONTMATTER_KEYS:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", "prompt frontmatter keys are invalid"
            )
        value = raw.strip()
        if key == "title":
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise PromptWorkspaceError(
                    "PROMPT_INPUT_INVALID", "prompt title is invalid"
                ) from exc
            if not isinstance(decoded, str):
                raise PromptWorkspaceError(
                    "PROMPT_INPUT_INVALID", "prompt title must be text"
                )
            value = decoded
        metadata[key] = value
    if set(metadata) != FRONTMATTER_KEYS:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt frontmatter is incomplete"
        )
    return metadata, "\n".join(lines[end + 1 :])


def prompt_metadata(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_file():
        raise PromptWorkspaceError(
            "PROMPT_PATH_INVALID", "prompt path is missing or unsafe"
        )
    if not valid_prompt_filename(resolved.name):
        raise PromptWorkspaceError("PROMPT_PATH_INVALID", "prompt filename is invalid")
    require_mode(resolved, 0o600, "prompt file")
    raw = resolved.read_bytes()
    if len(raw) > MAX_PROMPT_BYTES:
        raise PromptWorkspaceError("PROMPT_INPUT_INVALID", "prompt exceeds 256 KiB")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt is not valid UTF-8"
        ) from exc
    metadata, body = parse_frontmatter(text)
    if contains_secret(text):
        raise PromptWorkspaceError(
            "PROMPT_SENSITIVE_INPUT",
            "prompt appears to contain secret material; remove it before intake",
        )
    if metadata["schema"] != PROMPT_SCHEMA or not PROMPT_ID_RE.fullmatch(
        metadata["prompt_id"]
    ):
        raise PromptWorkspaceError("PROMPT_INPUT_INVALID", "prompt identity is invalid")
    try:
        created_at = datetime.fromisoformat(
            metadata["created_at"].replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt timestamp is invalid"
        ) from exc
    if created_at.tzinfo is None:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt timestamp needs an offset"
        )
    return {
        "path": resolved,
        "raw": raw,
        "body": body,
        "prompt_id": metadata["prompt_id"],
        "title": metadata["title"],
        "created_at": created_at,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def parse_prompt(path: Path) -> dict[str, object]:
    document = prompt_metadata(path)
    text = document["raw"].decode("utf-8")
    body = str(document["body"])
    if re.search(r"\{\{[A-Z0-9_]+\}\}", text):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt contains unresolved template markers"
        )
    headings = list(re.finditer(r"(?m)^## ([^\n]+)\s*$", body))
    names = [match.group(1) for match in headings]
    if names != list(ALL_SECTIONS):
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt sections are missing or reordered"
        )
    sections: dict[str, str] = {}
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(body)
        sections[match.group(1)] = body[start:end].strip()
    for name in REQUIRED_SECTIONS:
        if not meaningful_section(sections[name]):
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", f"prompt section is empty: {name}"
            )
    document.pop("body", None)
    document.pop("created_at", None)
    return document


def discover_workspace(
    prompt: str, project_path: Path, codex_home: Path
) -> tuple[Path, Path]:
    candidate = Path(prompt).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if candidate != resolved or candidate.is_symlink():
            raise PromptWorkspaceError(
                "PROMPT_PATH_INVALID", "prompt path is non-canonical or uses a symlink"
            )
        if resolved.parent.name != "prompts":
            raise PromptWorkspaceError(
                "PROMPT_PATH_INVALID", "absolute prompt is not managed"
            )
        manifest_path = resolved.parent.parent / "workspace.json"
        if manifest_path.parent.parent != (codex_home / "sdlc-runs").resolve():
            raise PromptWorkspaceError(
                "PROMPT_PATH_INVALID",
                "absolute prompt is outside the managed SDLC root",
            )
        validate_workspace(manifest_path)
        return manifest_path, resolved
    if candidate.name != prompt or candidate.suffix != ".md":
        raise PromptWorkspaceError(
            "PROMPT_PATH_INVALID", "use an absolute path or unique filename"
        )
    cwd = project_path.expanduser().resolve()
    matches: list[tuple[int, Path, Path]] = []
    for manifest_path in sorted((codex_home / "sdlc-runs").glob("*/workspace.json")):
        manifest = validate_workspace(manifest_path)
        root = Path(str(manifest.get("project_root"))).resolve()
        try:
            cwd.relative_to(root)
        except ValueError:
            continue
        for path in (manifest_path.parent / "prompts").glob(prompt):
            matches.append((len(root.parts), manifest_path, path))
    if not matches:
        raise PromptWorkspaceError(
            "WORKSPACE_NOT_FOUND", "run workspace init for this project first"
        )
    longest = max(item[0] for item in matches)
    narrowed = [
        (manifest, path) for depth, manifest, path in matches if depth == longest
    ]
    if len(narrowed) != 1:
        raise PromptWorkspaceError(
            "PROMPT_PATH_AMBIGUOUS", "prompt filename is not unique"
        )
    return narrowed[0]


@contextmanager
def prompt_lock(project_dir: Path):
    if fcntl is None:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "prompt workspace locking requires a POSIX host"
        )
    lock_path = project_dir / "prompt.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    private_chmod(lock_path, 0o600)
    with os.fdopen(descriptor, "r+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_status(run_dir: Path) -> str:
    for path in (run_dir / "current-state.json", run_dir / "run.json"):
        if not path.exists():
            continue
        value = load_json(path, path.name)
        status = str(value.get("status") or "").lower()
        if status:
            return status
    return "initializing"


def validate_binding(run_dir: Path) -> dict[str, object]:
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run directory is unsafe")
    binding = load_json(run_dir / "prompt.json", "prompt binding")
    if (
        binding.get("schema") != BINDING_SCHEMA
        or binding.get("run_id") != run_dir.name
        or not PROMPT_ID_RE.fullmatch(str(binding.get("prompt_id") or ""))
        or not valid_prompt_filename(binding.get("prompt_filename"))
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prompt binding identity is invalid"
        )
    revisions = binding.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prompt binding has no revisions"
        )
    for index, revision in enumerate(revisions, 1):
        if (
            not isinstance(revision, dict)
            or revision.get("revision") != f"r{index:04d}"
            or not SHA256_RE.fullmatch(str(revision.get("sha256") or ""))
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "prompt revisions are invalid"
            )
        relative = Path(str(revision.get("snapshot") or ""))
        snapshot = run_dir / relative
        if relative != Path("inputs") / f"r{index:04d}" / "prompt.md":
            raise PromptWorkspaceError("RUN_STATE_INVALID", "snapshot path is invalid")
        if snapshot.is_symlink() or not snapshot.is_file():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "snapshot is missing or unsafe"
            )
        require_mode(snapshot, 0o600, "prompt snapshot")
        raw = snapshot.read_bytes()
        if len(raw) > MAX_PROMPT_BYTES:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "prompt snapshot exceeds 256 KiB"
            )
        if hashlib.sha256(raw).hexdigest() != revision.get("sha256"):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "prompt snapshot digest changed"
            )
    return binding


def active_run(project_dir: Path) -> tuple[Path | None, dict[str, object] | None]:
    pointer = project_dir / "active-run.json"
    if not pointer.exists():
        unfinished: list[tuple[Path, dict[str, object] | None]] = []
        for run_dir in sorted(project_dir.glob("run-*")):
            if run_dir.is_symlink() or not run_dir.is_dir():
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "run directory is unsafe"
                )
            binding_path = run_dir / "prompt.json"
            binding = validate_binding(run_dir) if binding_path.exists() else None
            if run_status(run_dir) not in TERMINAL_STATUSES:
                unfinished.append((run_dir, binding))
        if len(unfinished) > 1:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "multiple unfinished runs have no active pointer"
            )
        if unfinished:
            run_dir, binding = unfinished[0]
            if binding is None:
                raise PromptWorkspaceError(
                    "WORKFLOW_UPGRADE_REQUIRED",
                    "unfinished active run has no managed prompt binding",
                )
            write_atomic(
                project_dir / "active-run.json", stable_json({"run_id": run_dir.name})
            )
            return run_dir, binding
        return None, None
    value = load_json(pointer, "active run pointer")
    run_id = str(value.get("run_id") or "")
    if Path(run_id).name != run_id or not run_id or len(run_id) > 80:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "active run ID is invalid")
    run_dir = project_dir / run_id
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "active run directory is unsafe"
        )
    for other in sorted(project_dir.glob("run-*")):
        if other == run_dir:
            continue
        if other.is_symlink() or not other.is_dir():
            raise PromptWorkspaceError("RUN_STATE_INVALID", "run directory is unsafe")
        if run_status(other) not in TERMINAL_STATUSES:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID",
                "multiple unfinished runs conflict with the active pointer",
            )
    binding_path = run_dir / "prompt.json"
    if not binding_path.exists():
        if run_status(run_dir) in TERMINAL_STATUSES:
            return run_dir, None
        raise PromptWorkspaceError(
            "WORKFLOW_UPGRADE_REQUIRED",
            "unfinished active run has no managed prompt binding",
        )
    return run_dir, validate_binding(run_dir)


def new_run(
    project_dir: Path, document: dict[str, object], accepted_at: datetime
) -> tuple[Path, dict[str, object]]:
    run_id = (
        f"run-{accepted_at.strftime('%Y%m%dT%H%M%SZ').lower()}-{uuid.uuid4().hex[:8]}"
    )
    run_dir = project_dir / run_id
    temporary = project_dir / f".{run_id}.tmp-{uuid.uuid4().hex[:8]}"
    ensure_private_dir(temporary)
    revision_dir = temporary / "inputs" / "r0001"
    ensure_private_dir(temporary / "inputs")
    ensure_private_dir(revision_dir)
    snapshot = revision_dir / "prompt.md"
    write_exclusive(snapshot, document["raw"])
    binding = {
        "schema": BINDING_SCHEMA,
        "run_id": run_id,
        "prompt_id": document["prompt_id"],
        "prompt_filename": Path(str(document["path"])).name,
        "created_at": iso_seconds(accepted_at),
        "revisions": [
            {
                "revision": "r0001",
                "accepted_at": iso_seconds(accepted_at),
                "sha256": document["sha256"],
                "snapshot": "inputs/r0001/prompt.md",
                "steering_status": "initial",
            }
        ],
    }
    write_atomic(temporary / "prompt.json", stable_json(binding))
    temporary.rename(run_dir)
    write_atomic(project_dir / "active-run.json", stable_json({"run_id": run_id}))
    return run_dir, binding


def append_revision(
    run_dir: Path,
    binding: dict[str, object],
    document: dict[str, object],
    accepted_at: datetime,
) -> dict[str, object]:
    revisions = list(binding["revisions"])
    revision_id = f"r{len(revisions) + 1:04d}"
    revision_dir = run_dir / "inputs" / revision_id
    snapshot = revision_dir / "prompt.md"
    if revision_dir.exists():
        if revision_dir.is_symlink() or not revision_dir.is_dir():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "pending revision directory is unsafe"
            )
        require_mode(revision_dir, 0o700, "pending revision directory")
        if snapshot.is_symlink() or not snapshot.is_file():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "pending revision snapshot is unsafe"
            )
        require_mode(snapshot, 0o600, "pending revision snapshot")
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != document["sha256"]:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "pending revision snapshot conflicts with prompt"
            )
    else:
        ensure_private_dir(revision_dir)
        write_exclusive(snapshot, document["raw"])
    revisions.append(
        {
            "revision": revision_id,
            "accepted_at": iso_seconds(accepted_at),
            "sha256": document["sha256"],
            "snapshot": f"inputs/{revision_id}/prompt.md",
            "steering_status": "pending",
        }
    )
    binding["revisions"] = revisions
    write_atomic(run_dir / "prompt.json", stable_json(binding))
    return binding


def update_activity(project_dir: Path, prompt_id: str, accepted_at: datetime) -> None:
    path = project_dir / "activity.json"
    if path.exists():
        value = load_json(path, "prompt activity")
        if value.get("schema") != ACTIVITY_SCHEMA or not isinstance(
            value.get("prompts"), dict
        ):
            raise PromptWorkspaceError(
                "WORKSPACE_STATE_INVALID", "prompt activity is invalid"
            )
    else:
        value = {"schema": ACTIVITY_SCHEMA, "prompts": {}}
    prompts = dict(value["prompts"])
    accepted = iso_seconds(accepted_at)
    existing = prompts.get(prompt_id)
    if isinstance(existing, str):
        try:
            if datetime.fromisoformat(existing) > accepted_at:
                accepted = existing
        except ValueError as exc:
            raise PromptWorkspaceError(
                "WORKSPACE_STATE_INVALID", "prompt activity timestamp is invalid"
            ) from exc
    prompts[prompt_id] = accepted
    value["prompts"] = prompts
    write_atomic(path, stable_json(value))


def load_activity(project_dir: Path) -> dict[str, str]:
    path = project_dir / "activity.json"
    if not path.exists():
        return {}
    value = load_json(path, "prompt activity")
    prompts = value.get("prompts")
    if value.get("schema") != ACTIVITY_SCHEMA or not isinstance(prompts, dict):
        raise PromptWorkspaceError(
            "WORKSPACE_STATE_INVALID", "prompt activity is invalid"
        )
    result: dict[str, str] = {}
    for prompt_id, timestamp in prompts.items():
        if not PROMPT_ID_RE.fullmatch(str(prompt_id)) or not isinstance(timestamp, str):
            raise PromptWorkspaceError(
                "WORKSPACE_STATE_INVALID", "prompt activity entry is invalid"
            )
        try:
            parsed = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise PromptWorkspaceError(
                "WORKSPACE_STATE_INVALID", "prompt activity timestamp is invalid"
            ) from exc
        if parsed.tzinfo is None:
            raise PromptWorkspaceError(
                "WORKSPACE_STATE_INVALID", "prompt activity timestamp needs an offset"
            )
        result[str(prompt_id)] = timestamp
    return result


def create_prompt(
    workspace_path: Path,
    ask_value: str,
    *,
    clock=now_utc,
    id_factory=lambda: uuid.uuid4().hex,
) -> dict[str, object]:
    manifest_path = workspace_path.expanduser().resolve()
    validate_workspace(manifest_path)
    project_dir = manifest_path.parent
    prompt_root = project_dir / "prompts"
    created_at = clock()
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise PromptWorkspaceError(
            "PROMPT_INPUT_INVALID", "prompt creation clock must be timezone-aware"
        )
    with prompt_lock(project_dir):
        path = allocate_prompt(
            prompt_root, ask_value, created_at, id_factory=id_factory
        )
    document = prompt_metadata(path)
    return {
        "action": "created",
        "path": str(path),
        "title": document["title"],
        "created_at": iso_seconds(created_at),
    }


def prompt_rows(
    workspace_path: Path, query: str | None, date_value: str | None
) -> list[dict[str, object]]:
    manifest_path = workspace_path.expanduser().resolve()
    validate_workspace(manifest_path)
    project_dir = manifest_path.parent
    prompt_root = project_dir / "prompts"
    if date_value is not None:
        try:
            datetime.strptime(date_value, "%Y-%m-%d")
        except ValueError as exc:
            raise PromptWorkspaceError(
                "PROMPT_INPUT_INVALID", "--date must use YYYY-MM-DD"
            ) from exc
    activity = load_activity(project_dir)
    needle = query.casefold() if query else None
    runs: list[tuple[Path, dict[str, object]]] = []
    for run_dir in sorted(project_dir.glob("run-*")):
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise PromptWorkspaceError("RUN_STATE_INVALID", "run directory is unsafe")
        binding_path = run_dir / "prompt.json"
        if binding_path.exists():
            runs.append((run_dir, validate_binding(run_dir)))
        elif run_status(run_dir) not in TERMINAL_STATUSES:
            raise PromptWorkspaceError(
                "WORKFLOW_UPGRADE_REQUIRED",
                "unfinished active run has no managed prompt binding",
            )
    rows: list[dict[str, object]] = []
    for path in sorted(prompt_root.glob("*.md")):
        document = prompt_metadata(path)
        created = document["created_at"]
        assert isinstance(created, datetime)
        searchable = f"{document['title']} {path.name}".casefold()
        if needle and needle not in searchable:
            continue
        if date_value and created.date().isoformat() != date_value:
            continue
        prompt_runs = [
            (run_dir, binding)
            for run_dir, binding in runs
            if binding.get("prompt_id") == document["prompt_id"]
        ]
        status = "draft"
        revisions = 0
        completed = 0
        if prompt_runs:
            revisions = sum(len(binding["revisions"]) for _, binding in prompt_runs)
            completed = sum(
                run_status(run_dir) in TERMINAL_STATUSES for run_dir, _ in prompt_runs
            )
            latest_dir, latest_binding = prompt_runs[-1]
            latest_revision = latest_binding["revisions"][-1]
            if latest_revision.get("steering_status") == "pending":
                status = "steering_pending"
            else:
                status = run_status(latest_dir)
        rows.append(
            {
                "title": document["title"],
                "last_invoked_at": activity.get(
                    str(document["prompt_id"]), iso_seconds(created)
                ),
                "status": status,
                "revision_count": revisions,
                "completed_run_count": completed,
                "path": str(path.resolve()),
            }
        )
    rows.sort(
        key=lambda row: (str(row["last_invoked_at"]), str(row["path"])),
        reverse=True,
    )
    return rows


def verify_command(
    workspace_path: Path, prompt_path: Path | None, run_id: str | None
) -> dict[str, object]:
    manifest_path = workspace_path.expanduser().resolve()
    validate_workspace(manifest_path)
    project_dir = manifest_path.parent
    prompt_root = project_dir / "prompts"
    rows = prompt_rows(manifest_path, None, None)
    documents = [prompt_metadata(path) for path in sorted(prompt_root.glob("*.md"))]
    ids = [str(document["prompt_id"]) for document in documents]
    if len(ids) != len(set(ids)):
        raise PromptWorkspaceError("PROMPT_CONFLICT", "prompt ID is duplicated")
    selected: dict[str, object] | None = None
    if prompt_path is not None:
        selected = prompt_metadata(prompt_path)
        try:
            Path(str(selected["path"])).relative_to(prompt_root)
        except ValueError as exc:
            raise PromptWorkspaceError(
                "PROMPT_PATH_INVALID", "prompt is outside this workspace"
            ) from exc
    if run_id is not None:
        if not RUN_ID_RE.fullmatch(run_id):
            raise PromptWorkspaceError("RUN_STATE_INVALID", "run ID is invalid")
        run_dir = project_dir / run_id
        binding = validate_binding(run_dir)
        matches = [
            document
            for document in documents
            if document["prompt_id"] == binding["prompt_id"]
        ]
        if len(matches) != 1:
            raise PromptWorkspaceError(
                "PROMPT_CONFLICT", "bound editable prompt is missing or duplicated"
            )
        document = matches[0]
        latest = binding["revisions"][-1]
        if run_status(run_dir) not in TERMINAL_STATUSES and (
            Path(str(document["path"])).name != binding["prompt_filename"]
            or document["sha256"] != latest["sha256"]
        ):
            raise PromptWorkspaceError(
                "PROMPT_DRIFT", "editable prompt differs from the active binding"
            )
        if selected is not None and selected["prompt_id"] != binding["prompt_id"]:
            raise PromptWorkspaceError(
                "PROMPT_CONFLICT", "prompt does not own the requested run"
            )
    return {"action": "verified", "workspace": str(manifest_path), "prompts": rows}


def repair_prompt_mirror(run_dir: Path, binding: dict[str, object]) -> None:
    path = run_dir / "run.json"
    if not path.exists():
        return
    value = load_json(path, "run.json")
    latest = binding["revisions"][-1]
    prompt = value.get("prompt")
    if not isinstance(prompt, dict):
        prompt = {}
    prompt.update(
        {
            "id": binding["prompt_id"],
            "filename": binding["prompt_filename"],
            "revision": latest["revision"],
            "sha256": latest["sha256"],
            "snapshot": latest["snapshot"],
        }
    )
    value["prompt"] = prompt
    value["prompt_filename"] = binding["prompt_filename"]
    write_atomic(path, stable_json(value))


def intake(prompt: str, project_path: Path, codex_home: Path) -> dict[str, object]:
    codex_home = codex_home.expanduser().resolve()
    manifest_path, prompt_path = discover_workspace(prompt, project_path, codex_home)
    workspace = validate_workspace(manifest_path)
    project_dir = manifest_path.parent
    prompt_root = project_dir / "prompts"
    try:
        prompt_path.relative_to(prompt_root)
    except ValueError as exc:
        raise PromptWorkspaceError(
            "PROMPT_PATH_INVALID", "prompt escapes its workspace"
        ) from exc
    document = parse_prompt(prompt_path)
    accepted_at = now_utc()
    with prompt_lock(project_dir):
        current = parse_prompt(prompt_path)
        if current["sha256"] != document["sha256"]:
            raise PromptWorkspaceError(
                "PROMPT_CHANGED_DURING_INTAKE",
                "prompt changed while intake was starting",
            )
        document = current
        duplicate_ids = [
            path
            for path in prompt_root.glob("*.md")
            if path.resolve() != prompt_path
            and prompt_metadata(path)["prompt_id"] == document["prompt_id"]
        ]
        if duplicate_ids:
            raise PromptWorkspaceError("PROMPT_CONFLICT", "prompt ID is duplicated")
        run_dir, binding = active_run(project_dir)
        action = "new"
        outcome = None
        renamed = False
        if run_dir is not None and binding is not None:
            if binding.get("prompt_id") != document["prompt_id"]:
                if run_status(run_dir) not in TERMINAL_STATUSES:
                    raise PromptWorkspaceError(
                        "ACTIVE_RUN_CONFLICT", "another prompt owns the unfinished run"
                    )
                run_dir, binding = new_run(project_dir, document, accepted_at)
            else:
                latest = binding["revisions"][-1]
                bound_filename = str(binding["prompt_filename"])
                if bound_filename != prompt_path.name:
                    old_source = prompt_root / bound_filename
                    if old_source.exists() or old_source.is_symlink():
                        raise PromptWorkspaceError(
                            "PROMPT_CONFLICT",
                            "recorded and renamed prompt sources both exist",
                        )
                    if latest.get("sha256") != document["sha256"]:
                        raise PromptWorkspaceError(
                            "PROMPT_DRIFT",
                            "rename and content editing must be accepted separately",
                        )
                    binding["prompt_filename"] = prompt_path.name
                    write_atomic(run_dir / "prompt.json", stable_json(binding))
                    repair_prompt_mirror(run_dir, binding)
                    renamed = True
                same = latest.get("sha256") == document["sha256"]
                status = run_status(run_dir)
                if status in TERMINAL_STATUSES:
                    if same:
                        action = "done"
                        outcome = "ALREADY_COMPLETE"
                    else:
                        run_dir, binding = new_run(project_dir, document, accepted_at)
                elif same:
                    action = (
                        "steering"
                        if latest.get("steering_status") == "pending"
                        else "resume"
                    )
                else:
                    binding = append_revision(run_dir, binding, document, accepted_at)
                    action = "steering"
        elif run_dir is not None:
            run_dir, binding = new_run(project_dir, document, accepted_at)
        else:
            run_dir, binding = new_run(project_dir, document, accepted_at)
        update_activity(project_dir, str(document["prompt_id"]), accepted_at)
        latest = binding["revisions"][-1]
    promotion = (
        None
        if outcome == "ALREADY_COMPLETE"
        else ensure_run_promotion(workspace, run_dir)
    )
    result = {
        "action": action,
        "project_id": workspace["project_id"],
        "project_root": workspace["project_root"],
        "prompt": str(prompt_path),
        "prompt_filename": prompt_path.name,
        "run_id": run_dir.name,
        "revision": latest["revision"],
        "sha256": latest["sha256"],
        "snapshot": str(run_dir / str(latest["snapshot"])),
        "next_recommended_skill": "sdlc-auto-steering"
        if action == "steering"
        else "sdlc-start",
    }
    if renamed:
        result["renamed"] = True
    if outcome:
        result["outcome"] = outcome
    if promotion is not None:
        result["promotion_branch"] = promotion["promotion_branch"]
        result["default_branch"] = promotion["default_branch"]
    return result


def steering_resolve(
    workspace: Path, run_id: str, revision_id: str, disposition: str
) -> dict[str, object]:
    manifest_path = workspace.expanduser().resolve()
    validate_workspace(manifest_path)
    if not RUN_ID_RE.fullmatch(run_id) or not REVISION_RE.fullmatch(revision_id):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run or revision ID is invalid")
    if disposition not in STEERING_DISPOSITIONS:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering disposition is invalid"
        )
    project_dir = manifest_path.parent
    run_dir = project_dir / run_id
    with prompt_lock(project_dir):
        binding = validate_binding(run_dir)
        revisions = list(binding["revisions"])
        target = next(
            (item for item in revisions if item.get("revision") == revision_id), None
        )
        if target is None or target.get("steering_status") != "pending":
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering revision is not pending"
            )
        target["steering_status"] = disposition
        target["resolved_at"] = iso_seconds(now_utc())
        binding["revisions"] = revisions
        write_atomic(run_dir / "prompt.json", stable_json(binding))
    return {
        "action": "steering_resolved",
        "run_id": run_id,
        "revision": revision_id,
        "disposition": disposition,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Private Agentic SDLC prompt intake helper."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    init_parser = subparsers.add_parser(
        "init", help="Internal: initialize one prompt workspace."
    )
    init_parser.add_argument("project_path", nargs="?", type=Path, default=Path.cwd())
    init_parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    )
    init_parser.add_argument("--no-open", action="store_true")
    init_parser.add_argument(
        "--editor", default=os.environ.get("SDLC_PROMPT_EDITOR", "code")
    )
    init_parser.add_argument("--json", action="store_true")
    intake_parser = subparsers.add_parser(
        "intake", help="Internal: bind or resume one prompt."
    )
    intake_parser.add_argument("prompt")
    intake_parser.add_argument("--project-path", type=Path, default=Path.cwd())
    intake_parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    )
    intake_parser.add_argument("--json", action="store_true")
    resolve_parser = subparsers.add_parser(
        "steering-resolve", help="Internal: resolve one prompt revision."
    )
    resolve_parser.add_argument("--workspace", required=True, type=Path)
    resolve_parser.add_argument("--run-id", required=True)
    resolve_parser.add_argument("--revision", required=True)
    resolve_parser.add_argument(
        "--disposition", required=True, choices=sorted(STEERING_DISPOSITIONS)
    )
    resolve_parser.add_argument("--json", action="store_true")
    new_parser = subparsers.add_parser(
        "new", help="Internal: create one managed prompt for the editor task."
    )
    new_parser.add_argument("--workspace", required=True, type=Path)
    new_parser.add_argument("--ask", required=True)
    new_parser.add_argument("--open", action="store_true")
    new_parser.add_argument(
        "--editor", default=os.environ.get("SDLC_PROMPT_EDITOR", "code")
    )
    new_parser.add_argument("--json", action="store_true")
    list_parser = subparsers.add_parser(
        "list", help="Internal: list managed prompt metadata without bodies."
    )
    list_parser.add_argument("--workspace", required=True, type=Path)
    list_parser.add_argument("--query")
    list_parser.add_argument("--date")
    list_parser.add_argument("--json", action="store_true")
    verify_parser = subparsers.add_parser(
        "verify", help="Internal: validate prompt workspace and binding state."
    )
    verify_parser.add_argument("--workspace", required=True, type=Path)
    verify_parser.add_argument("--prompt", type=Path)
    verify_parser.add_argument("--run-id")
    verify_parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def emit(value: object, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, sort_keys=True))
    elif isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                print(
                    "\t".join(
                        re.sub(r"[\x00-\x1f\x7f]", " ", str(row.get(key) or "-"))
                        for key in ("last_invoked_at", "status", "title", "path")
                    )
                )
    elif isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")


def public_result(command: str, result: dict[str, object]) -> dict[str, object]:
    if command == "intake":
        keys = (
            "action",
            "prompt",
            "prompt_filename",
            "revision",
            "next_recommended_skill",
            "outcome",
        )
        return {key: result[key] for key in keys if key in result}
    if command == "steering-resolve":
        keys = ("action", "revision", "disposition")
        return {key: result[key] for key in keys if key in result}
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        if args.command == "init":
            result = initialize(
                args.project_path, args.codex_home, not args.no_open, args.editor
            )
        elif args.command == "intake":
            result = intake(args.prompt, args.project_path, args.codex_home)
        elif args.command == "steering-resolve":
            result = steering_resolve(
                args.workspace, args.run_id, args.revision, args.disposition
            )
        elif args.command == "new":
            result = create_prompt(args.workspace, args.ask)
            if args.open:
                try:
                    subprocess.Popen(
                        [args.editor, "--reuse-window", "--goto", str(result["path"])],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                except OSError:
                    print(
                        f"WARN editor executable is unavailable; open manually: {result['path']}",
                        file=sys.stderr,
                    )
        elif args.command == "list":
            result = prompt_rows(args.workspace, args.query, args.date)
        else:
            result = verify_command(args.workspace, args.prompt, args.run_id)
        emit(public_result(args.command, result), args.json)
        return 0
    except PromptWorkspaceError as exc:
        error = {"error": exc.code, "message": exc.message}
        if getattr(args, "json", False):
            print(json.dumps(error, sort_keys=True))
        else:
            print(f"ERROR {exc.code}: {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
