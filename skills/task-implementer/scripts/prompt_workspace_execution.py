#!/usr/bin/env python3
"""Durable per-task planning, implementation, and fresh-session gates."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import subprocess

from prompt_workspace_core import (
    RUN_ID_RE,
    PromptWorkspaceError,
    ensure_private_dir,
    iso_seconds,
    load_json_object,
    now_utc,
    require_mode,
    required_string,
    stable_json,
    verify_workspace,
    write_atomic,
    write_exclusive,
)
from prompt_workspace_runs import (
    handoff_field,
    load_run_manifests,
    manifest_revisions,
    markdown_section,
    read_handoff_text,
    scope_lock,
    verify_run,
)
from prompt_workspace_specs import (
    DESIGN_ID_RE,
    REQUIREMENT_ID_RE,
    inspect_spec_documents,
    spec_repo_path,
)


EXECUTION_SCHEMA = "task-implementer/execution-plane-v1"
TASK_ID_RE = re.compile(r"task-([1-9][0-9]*)")
PLANE_PHASES = {"planning", "implementation", "stopped"}
ACTIVE_PHASES = {"planning", "implementation"}
RECOVERY_FIELDS = {
    "recovered_at",
    "recovery_confirmation",
    "previous_owner_session_sha256",
    "recovery_worktree_sha256",
    "recovery_head",
}
PLAN_FIELDS = (
    "Goal",
    "Plan",
    "Likely files",
    "Implementation steps",
    "Validation",
    "End-to-end validation",
    "Done criteria",
    "Rollback notes",
    "Stop conditions",
)
SPEC_PLAN_FIELDS = (
    "Requirement IDs",
    "Design ID",
    "Requirements proposal",
    "Design record",
    "Requirements envelope SHA-256",
    "Design envelope SHA-256",
)
CHECKPOINT_FIELDS = (
    "Summary",
    "Plan followed",
    "Files changed",
    "Validation",
    "End-to-end validation",
    "Code-review",
    "Commit hash",
    "Commit message",
)
SPEC_CHECKPOINT_FIELDS = (
    "Requirements SHA-256",
    "Design SHA-256",
    "Spec validation",
)
IMPLEMENTATION_EVIDENCE_FIELDS = (
    "Code-review",
    "Review fixes",
    "Commit",
    "Changed files",
    "Evidence",
    "Blocker",
)


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution clock must be timezone-aware"
        )
    return iso_seconds(value.astimezone(timezone.utc))


def session_fingerprint(session_id: str | None = None) -> str:
    """Return a non-reversible runtime session correlation fingerprint."""

    value = session_id if session_id is not None else os.environ.get("CODEX_THREAD_ID")
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise PromptWorkspaceError(
            "SESSION_ID_UNAVAILABLE",
            "a runtime Codex session identifier is required for task execution",
        )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def worktree_state(workspace: dict[str, object]) -> tuple[str, str, bool]:
    """Hash Git HEAD plus status so planning cannot overlap product edits."""

    repo_root = Path(required_string(workspace, "repo_root", "workspace manifest"))
    try:
        head_result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        status_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git is unavailable for execution-plane locking"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git status timed out during execution-plane locking"
        ) from exc
    if head_result.returncode != 0 or status_result.returncode != 0:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "Git status failed during execution-plane locking"
        )
    head = head_result.stdout.decode("ascii", errors="strict").strip()
    if re.fullmatch(r"[0-9a-f]{40,64}", head) is None:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "Git HEAD is invalid during execution-plane locking"
        )
    digest = hashlib.sha256(
        head.encode("ascii") + b"\0" + status_result.stdout
    ).hexdigest()
    return digest, head, not bool(status_result.stdout)


def parse_repo_paths(value: str, label: str) -> set[str]:
    """Parse one repo-relative path per line from a locked handoff field."""

    paths: set[str] = set()
    for raw_line in value.splitlines():
        item = raw_line.strip()
        if item.startswith("- "):
            item = item[2:].strip()
        if item.startswith("`") and item.endswith("`") and len(item) > 1:
            item = item[1:-1]
        path = Path(item)
        if (
            not item
            or path.is_absolute()
            or ".." in path.parts
            or item in paths
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", f"{label} must list unique repo-relative paths"
            )
        paths.add(item)
    if not paths:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{label} must list repo-relative paths"
        )
    return paths


def git_path_output(
    workspace: dict[str, object], arguments: list[str], description: str
) -> set[str]:
    repo_root = Path(required_string(workspace, "repo_root", "workspace manifest"))
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", f"Git could not inspect {description}"
        ) from exc
    if result.returncode != 0:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", f"Git could not inspect {description}"
        )
    try:
        items = result.stdout.decode("utf-8", errors="strict").split("\0")
    except UnicodeDecodeError as exc:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", f"{description} contains a non-UTF-8 path"
        ) from exc
    return {item for item in items if item}


def worktree_paths(workspace: dict[str, object]) -> set[str]:
    tracked = git_path_output(
        workspace,
        ["diff", "--name-only", "-z", "--no-renames", "HEAD", "--"],
        "the implementation worktree",
    )
    untracked = git_path_output(
        workspace,
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        "untracked implementation files",
    )
    return tracked | untracked


def committed_paths(
    workspace: dict[str, object], claim_head: str, commit_hash: str
) -> set[str]:
    return git_path_output(
        workspace,
        [
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            claim_head,
            commit_hash,
            "--",
        ],
        "the task checkpoint diff",
    )


def verify_checkpoint_commit(
    workspace: dict[str, object],
    commit_hash: str,
    claim_head: str,
    expected_message: str,
    allowed_paths: set[str],
    recorded_paths: set[str],
    *,
    require_current_head: bool = True,
) -> None:
    if re.fullmatch(r"[0-9a-f]{40,64}", commit_hash) is None:
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "checkpoint commit hash is invalid"
        )
    repo_root = Path(required_string(workspace, "repo_root", "workspace manifest"))
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-e", f"{commit_hash}^{{commit}}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        head_result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        ancestor_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "merge-base",
                "--is-ancestor",
                claim_head,
                commit_hash,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        count_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "rev-list",
                "--count",
                f"{claim_head}..{commit_hash}",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        current_descendant_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "merge-base",
                "--is-ancestor",
                commit_hash,
                "HEAD",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        status_result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        message_result = subprocess.run(
            ["git", "-C", str(repo_root), "show", "-s", "--format=%B", commit_hash],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git could not verify the checkpoint commit"
        ) from exc
    if result.returncode != 0:
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "checkpoint commit does not exist"
        )
    current_head = head_result.stdout.decode("ascii", errors="strict").strip()
    actual_message = message_result.stdout.decode("utf-8", errors="strict").strip()
    commit_count = count_result.stdout.decode("ascii", errors="strict").strip()
    actual_paths = committed_paths(workspace, claim_head, commit_hash)
    if (
        (require_current_head and current_head != commit_hash)
        or commit_hash == claim_head
        or head_result.returncode != 0
        or ancestor_result.returncode != 0
        or count_result.returncode != 0
        or commit_count != "1"
        or current_descendant_result.returncode != 0
        or status_result.returncode != 0
        or bool(status_result.stdout)
        or message_result.returncode != 0
        or actual_message != expected_message
        or not actual_paths
        or not actual_paths.issubset(allowed_paths)
        or actual_paths != recorded_paths
    ):
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED",
            "checkpoint commit is not the current descendant task checkpoint",
        )


def field_block(section: str, label: str) -> str:
    """Read an inline or indented Markdown handoff field."""

    matches = list(re.finditer(
        rf"(?ms)^- {re.escape(label)}:\s*(.*?)"
        r"(?=^- [A-Za-z][^:\n]*:\s*|^### |^## |\Z)",
        section,
    ))
    if not matches:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"handoff is missing {label}"
        )
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"handoff repeats {label}"
        )
    return matches[0].group(1).strip()


def meaningful(value: str) -> bool:
    normalized = re.sub(r"<!--.*?-->", "", value, flags=re.DOTALL).strip()
    return normalized.casefold() not in {"", "none", "n/a", "tbd", "todo"}


def has_field(section: str, label: str) -> bool:
    return re.search(rf"(?m)^- {re.escape(label)}:\s*", section) is not None


def requirement_ids(value: str, label: str = "Requirement IDs") -> list[str]:
    values = re.findall(r"TI-REQ-[0-9]{3,}", value)
    if not values or len(values) != len(set(values)):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{label} must contain unique TI-REQ IDs"
        )
    if any(REQUIREMENT_ID_RE.fullmatch(item) is None for item in values):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{label} contains an invalid TI-REQ ID"
        )
    return values


def design_id(value: str) -> str:
    if DESIGN_ID_RE.fullmatch(value) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "Design ID must contain one TI-DES ID"
        )
    return value


def task_sections(text: str) -> tuple[list[str], dict[str, str]]:
    queue = markdown_section(text, "Task Queue")
    matches = list(re.finditer(r"(?m)^### (task-[1-9][0-9]*)\s*$", queue))
    if not matches:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "handoff task queue is empty"
        )
    order: list[str] = []
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        task_id = match.group(1)
        if task_id in sections:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "handoff repeats a task ID"
            )
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(queue)
        order.append(task_id)
        sections[task_id] = queue[start:end]
    return order, sections


def task_statuses(sections: dict[str, str]) -> dict[str, str]:
    allowed = {"pending", "in_progress", "done", "blocked", "superseded"}
    result: dict[str, str] = {}
    for task_id, section in sections.items():
        status = field_block(section, "Status")
        if status not in allowed:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", f"{task_id} has an invalid status"
            )
        result[task_id] = status
    return result


def task_dependencies(section: str, known_tasks: set[str]) -> list[str]:
    value = field_block(section, "Depends on")
    if value.casefold() == "none":
        return []
    task_ids = re.findall(r"task-[1-9][0-9]*", value)
    residual = re.sub(r"task-[1-9][0-9]*", "", value).strip(" ,")
    if not task_ids or residual:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "task dependencies are malformed"
        )
    if any(task_id not in known_tasks for task_id in task_ids):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "task dependency is not in the queue"
        )
    return task_ids


def next_ready_task(
    order: list[str],
    sections: dict[str, str],
    statuses: dict[str, str],
) -> str | None:
    known = set(order)
    for task_id in order:
        if statuses[task_id] != "pending":
            continue
        dependencies = task_dependencies(sections[task_id], known)
        if all(statuses[dependency] == "done" for dependency in dependencies):
            return task_id
    return None


def replace_section_field(text: str, heading: str, label: str, value: str) -> str:
    section_match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", text
    )
    if section_match is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"handoff is missing the {heading} section"
        )
    section = section_match.group(1)
    updated, count = re.subn(
        rf"(?m)^- {re.escape(label)}:.*$",
        f"- {label}: {value}",
        section,
        count=1,
    )
    if count != 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"handoff is missing {label} in {heading}"
        )
    return text[: section_match.start(1)] + updated + text[section_match.end(1) :]


def replace_task_status(text: str, task_id: str, status: str) -> str:
    match = re.search(
        rf"(?ms)^### {re.escape(task_id)}\s*\n(.*?)(?=^### task-|^## |\Z)",
        text,
    )
    if match is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"handoff is missing {task_id}"
        )
    section = match.group(1)
    updated, count = re.subn(
        r"(?m)^- Status:.*$", f"- Status: {status}", section, count=1
    )
    if count != 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{task_id} is missing Status"
        )
    return text[: match.start(1)] + updated + text[match.end(1) :]


def replace_task_field_block(
    text: str, task_id: str, label: str, value: str
) -> str:
    match = re.search(
        rf"(?ms)^### {re.escape(task_id)}\s*\n(.*?)(?=^### task-|^## |\Z)",
        text,
    )
    if match is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"handoff is missing {task_id}"
        )
    section = match.group(1)
    pattern = (
        rf"(?ms)^- {re.escape(label)}:\s*.*?"
        r"(?=^- [A-Za-z][^:\n]*:\s*|^### |^## |\Z)"
    )
    updated, count = re.subn(pattern, f"- {label}: {value}\n", section, count=1)
    if count != 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{task_id} is missing {label}"
        )
    return text[: match.start(1)] + updated + text[match.end(1) :]


def execution_section(plane: dict[str, object]) -> str:
    return "\n".join(
        (
            "## Execution Plane",
            "",
            f"- Task: {plane['task_id']}",
            f"- Phase: {plane['phase']}",
            f"- Bound revision: {plane['bound_revision']}",
            f"- Plan SHA-256: {plane.get('plan_sha256') or 'none'}",
            f"- Queue SHA-256: {plane.get('queue_sha256') or 'none'}",
            f"- Checkpoint SHA-256: {plane.get('checkpoint_sha256') or 'none'}",
            f"- Worktree baseline SHA-256: {plane['worktree_baseline_sha256']}",
            f"- Claimed at: {plane['claimed_at']}",
            f"- Authorized at: {plane.get('authorized_at') or 'none'}",
            f"- Completed at: {plane.get('completed_at') or 'none'}",
            f"- Recovery count: {plane['recovery_count']}",
            f"- Stop required: {plane['stop_required']}",
            f"- Next session required: {plane['next_session_required']}",
            "",
        )
    )


def upsert_execution_section(text: str, plane: dict[str, object]) -> str:
    rendered = execution_section(plane)
    match = re.search(r"(?ms)^## Execution Plane\s*\n.*?(?=^## |\Z)", text)
    if match is not None:
        return text[: match.start()] + rendered + text[match.end() :]
    insertion = re.search(r"(?m)^## Reconciliation\s*$", text)
    if insertion is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "handoff has no execution-section insertion point"
        )
    return text[: insertion.start()] + rendered + "\n" + text[insertion.start() :]


def bind_claim_handoff(text: str, plane: dict[str, object]) -> str:
    """Repair or create the handoff side of an active execution claim."""

    task_id = str(plane["task_id"])
    _, sections = task_sections(text)
    statuses = task_statuses(sections)
    status = statuses.get(task_id)
    if status == "pending":
        text = replace_task_status(text, task_id, "in_progress")
        status = "in_progress"
    if status not in {"in_progress", "done"}:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "claimed task has an incompatible status"
        )
    if status == "in_progress":
        text = replace_section_field(text, "Run", "Current task", task_id)
        text = replace_section_field(text, "Run", "Overall status", "running")
    return upsert_execution_section(text, plane)


def execution_dir(run_dir: Path) -> Path:
    return run_dir / "execution"


def plane_path(run_dir: Path, task_id: str) -> Path:
    return execution_dir(run_dir) / f"{task_id}.json"


def validate_plane(value: dict[str, object], path: Path, run_id: str) -> dict[str, object]:
    required_keys = {
        "schema",
        "run_id",
        "task_id",
        "bound_revision",
        "phase",
        "owner_session_sha256",
        "session_history_sha256",
        "worktree_baseline_sha256",
        "claim_head",
        "plan_sha256",
        "queue_sha256",
        "checkpoint_sha256",
        "claimed_at",
        "authorized_at",
        "completed_at",
        "recovery_count",
        "stop_required",
        "next_session_required",
    }
    keys = set(value)
    if keys != required_keys and keys != required_keys | RECOVERY_FIELDS:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane fields are invalid"
        )
    if value.get("schema") != EXECUTION_SCHEMA or value.get("run_id") != run_id:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane identity is invalid"
        )
    task_id = value.get("task_id")
    phase = value.get("phase")
    if (
        not isinstance(task_id, str)
        or TASK_ID_RE.fullmatch(task_id) is None
        or path.name != f"{task_id}.json"
        or phase not in PLANE_PHASES
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane task or phase is invalid"
        )
    for key in (
        "owner_session_sha256",
        "worktree_baseline_sha256",
    ):
        if re.fullmatch(r"[0-9a-f]{64}", str(value.get(key, ""))) is None:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", f"execution plane {key} is invalid"
            )
    session_history = value.get("session_history_sha256")
    if (
        not isinstance(session_history, list)
        or not session_history
        or len(session_history) != len(set(map(str, session_history)))
        or any(
            re.fullmatch(r"[0-9a-f]{64}", str(item)) is None
            for item in session_history
        )
        or session_history[-1] != value.get("owner_session_sha256")
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane session history is invalid"
        )
    previous_owner = value.get("previous_owner_session_sha256")
    if previous_owner is not None and re.fullmatch(
        r"[0-9a-f]{64}", str(previous_owner)
    ) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "execution plane previous owner fingerprint is invalid",
        )
    if re.fullmatch(r"[0-9a-f]{40,64}", str(value.get("claim_head", ""))) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane claim HEAD is invalid"
        )
    recovery_digest = value.get("recovery_worktree_sha256")
    if recovery_digest is not None and re.fullmatch(
        r"[0-9a-f]{64}", str(recovery_digest)
    ) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane recovery worktree is invalid"
        )
    recovery_head = value.get("recovery_head")
    if recovery_head is not None and re.fullmatch(
        r"[0-9a-f]{40,64}", str(recovery_head)
    ) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane recovery HEAD is invalid"
        )
    plan_digest = value.get("plan_sha256")
    if plan_digest is not None and re.fullmatch(r"[0-9a-f]{64}", str(plan_digest)) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane plan digest is invalid"
        )
    queue_digest = value.get("queue_sha256")
    if queue_digest is not None and re.fullmatch(
        r"[0-9a-f]{64}", str(queue_digest)
    ) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane queue digest is invalid"
        )
    checkpoint_digest = value.get("checkpoint_sha256")
    if checkpoint_digest is not None and re.fullmatch(
        r"[0-9a-f]{64}", str(checkpoint_digest)
    ) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane checkpoint digest is invalid"
        )
    recovery_count = value.get("recovery_count")
    if (
        not isinstance(recovery_count, int)
        or isinstance(recovery_count, bool)
        or recovery_count < 0
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane recovery count is invalid"
        )
    has_recovery_audit = keys == required_keys | RECOVERY_FIELDS
    if (recovery_count == 0 and has_recovery_audit) or (
        recovery_count > 0 and not has_recovery_audit
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane recovery count is inconsistent"
        )
    if re.fullmatch(r"r[0-9]{4}", str(value.get("bound_revision", ""))) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane bound revision is invalid"
        )
    for key in ("stop_required", "next_session_required"):
        if value.get(key) not in {"yes", "no"}:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", f"execution plane {key} is invalid"
            )
    for key in ("claimed_at", "authorized_at", "completed_at", "recovered_at"):
        timestamp = value.get(key)
        if timestamp is None:
            continue
        try:
            parsed = datetime.fromisoformat(str(timestamp))
        except ValueError as exc:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", f"execution plane {key} is invalid"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", f"execution plane {key} has no UTC offset"
            )
    if value.get("claimed_at") is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane has no claim time"
        )
    if has_recovery_audit:
        if (
            value.get("recovered_at") is None
            or value.get("recovery_confirmation")
            != "prior-session-stopped-and-worktree-reviewed"
            or previous_owner is None
            or previous_owner not in session_history
            or recovery_digest is None
            or recovery_head is None
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "execution plane recovery audit is invalid"
            )
    if phase == "planning" and (
        plan_digest is not None
        or queue_digest is not None
        or checkpoint_digest is not None
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "planning plane cannot have locked contracts"
        )
    if phase == "planning" and (
        value.get("authorized_at") is not None or value.get("completed_at") is not None
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "planning plane has later-phase timestamps"
        )
    if phase in {"implementation", "stopped"} and (
        plan_digest is None or queue_digest is None
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "authorized plane is missing locked contracts"
        )
    if phase == "implementation" and checkpoint_digest is not None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "implementation plane has a checkpoint digest"
        )
    if phase in {"implementation", "stopped"} and value.get("authorized_at") is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "authorized plane has no authorization time"
        )
    if phase == "implementation" and value.get("completed_at") is not None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "implementation plane has a completion time"
        )
    if phase == "stopped" and value.get("completed_at") is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "stopped plane has no completion time"
        )
    if phase == "stopped" and checkpoint_digest is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "stopped plane has no checkpoint digest"
        )
    if value.get("stop_required") != "yes" or (
        phase in ACTIVE_PHASES and value.get("next_session_required") != "no"
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane stop state is inconsistent"
        )
    return value


def load_execution_planes(run_dir: Path) -> list[dict[str, object]]:
    directory = execution_dir(run_dir)
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane directory is unsafe"
        )
    require_mode(directory, 0o700, "execution plane directory")
    entries = sorted(directory.iterdir())
    if any(
        entry.name.startswith(".")
        or TASK_ID_RE.fullmatch(entry.stem) is None
        or entry.suffix != ".json"
        for entry in entries
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane directory has unexpected entries"
        )
    planes: list[dict[str, object]] = []
    for path in entries:
        if path.is_symlink() or not path.is_file():
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "execution plane path is unsafe"
            )
        require_mode(path, 0o600, "execution plane")
        planes.append(validate_plane(load_json_object(path, "execution plane"), path, run_dir.name))
    active = [plane for plane in planes if plane["phase"] in ACTIVE_PHASES]
    if len(active) > 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "run has multiple active execution planes"
        )
    return planes


def last_completed_plane(
    planes: list[dict[str, object]], text: str
) -> dict[str, object] | None:
    last_task = handoff_field(markdown_section(text, "Run"), "Last completed task")
    stopped = [plane for plane in planes if plane["phase"] == "stopped"]
    if last_task == "none":
        if stopped:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "stopped planes have no last completed task"
            )
        return None
    matches = [plane for plane in stopped if plane["task_id"] == last_task]
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "last completed task has no unique stopped plane"
        )
    return matches[0]


def require_fresh_session(
    planes: list[dict[str, object]], owner: str, run_id: str, task_id: str
) -> None:
    """Reject any session fingerprint already used for another completed task."""

    if any(
        plane["phase"] == "stopped"
        and owner in plane["session_history_sha256"]
        and (plane["run_id"], plane["task_id"]) != (run_id, task_id)
        for plane in planes
    ):
        raise PromptWorkspaceError(
            "FRESH_SESSION_REQUIRED",
            "each task must run in a Codex session unused by every other task",
        )


def write_plane(path: Path, plane: dict[str, object], *, exclusive: bool = False) -> None:
    if exclusive:
        write_exclusive(path, stable_json(plane))
    else:
        write_atomic(path, stable_json(plane))


def claim_execution_plane(
    manifest_path: Path,
    run_id: str,
    *,
    session_id: str | None = None,
    recover: bool = False,
    confirmed_recovery_worktree_sha256: str | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Claim exactly one dependency-ready task in the planning phase."""

    if RUN_ID_RE.fullmatch(run_id) is None:
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "run ID is invalid")
    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    owner = session_fingerprint(session_id)
    with scope_lock(runs_root.parent):
        verified = verify_run(workspace, run_id, None)
        if verified["steering_pending"]:
            raise PromptWorkspaceError(
                "PLAN_REQUIRED",
                "pending steering must be reconciled before claiming a task",
            )
        run_dir = runs_root / run_id
        text = read_handoff_text(run_dir)
        if text is None:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "run handoff must exist before task claim"
            )
        planes = load_execution_planes(run_dir)
        validate_execution_index(planes, text)
        scope_planes = [
            plane
            for candidate_dir, _ in load_run_manifests(runs_root)
            for plane in load_execution_planes(candidate_dir)
        ]
        active = [plane for plane in planes if plane["phase"] in ACTIVE_PHASES]
        if active:
            plane = active[0]
            validate_plane_binding(
                plane,
                text,
                verified,
                allow_pending_repair=True,
                allow_checkpointed=True,
            )
            require_fresh_session(
                scope_planes, owner, run_id, str(plane["task_id"])
            )
            if plane["owner_session_sha256"] == owner:
                repaired = bind_claim_handoff(text, plane)
                if repaired != text:
                    write_atomic(run_dir / "handoff.md", repaired.encode("utf-8"))
                return {
                    "task": plane["task_id"],
                    "phase": plane["phase"],
                    "recovered": False,
                }
            if not recover:
                raise PromptWorkspaceError(
                    "WORKSPACE_BUSY", "another session owns the active task execution plane"
                )
            current_baseline, recovery_head, _ = worktree_state(workspace)
            if confirmed_recovery_worktree_sha256 is None:
                raise PromptWorkspaceError(
                    "HUMAN_INPUT_REQUIRED",
                    "recovery requires confirmation that the prior session stopped "
                    f"and worktree {current_baseline} was reviewed",
                )
            if confirmed_recovery_worktree_sha256 != current_baseline:
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT", "recovery worktree changed after confirmation"
                )
            if (
                plane["phase"] == "planning"
                and current_baseline != plane["worktree_baseline_sha256"]
            ):
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT", "worktree changed during interrupted planning"
                )
            if plane["phase"] == "implementation":
                values = plan_values(text, str(plane["task_id"]))
                if hashlib.sha256(stable_json(values)).hexdigest() != plane["plan_sha256"]:
                    raise PromptWorkspaceError(
                        "PLAN_LOCKED", "interrupted implementation plan no longer matches"
                    )
                unexpected_paths = worktree_paths(workspace) - parse_repo_paths(
                    values["Likely files"], "Likely files"
                )
                if unexpected_paths:
                    raise PromptWorkspaceError(
                        "WORKTREE_CONFLICT",
                        "recovery worktree contains paths outside the locked task plan",
                    )
            previous_owner = str(plane["owner_session_sha256"])
            plane["owner_session_sha256"] = owner
            history = [
                item for item in plane["session_history_sha256"] if item != owner
            ]
            history.append(owner)
            plane["session_history_sha256"] = history
            plane["recovery_count"] = int(plane["recovery_count"]) + 1
            plane["recovered_at"] = utc_text(clock())
            plane["recovery_confirmation"] = (
                "prior-session-stopped-and-worktree-reviewed"
            )
            plane["previous_owner_session_sha256"] = previous_owner
            plane["recovery_worktree_sha256"] = current_baseline
            plane["recovery_head"] = recovery_head
            write_plane(plane_path(run_dir, str(plane["task_id"])), plane)
            updated = bind_claim_handoff(text, plane)
            write_atomic(run_dir / "handoff.md", updated.encode("utf-8"))
            return {
                "task": plane["task_id"],
                "phase": plane["phase"],
                "recovered": True,
            }

        for stopped_plane in (
            plane for plane in planes if plane["phase"] == "stopped"
        ):
            validate_completed_plane_history(workspace, stopped_plane, text)
        validate_current_spec_state(workspace, planes, text)
        predecessor = last_completed_plane(planes, text)
        predecessor_next: str | None = None
        if predecessor is not None:
            predecessor_evidence = validate_stopped_execution_plane(
                workspace,
                verified,
                predecessor,
                text,
            )
            predecessor_next = predecessor_evidence[3]

        order, sections = task_sections(text)
        statuses = task_statuses(sections)
        in_progress = [task for task in order if statuses[task] == "in_progress"]
        current = handoff_field(markdown_section(text, "Run"), "Current task")
        if in_progress:
            raise PromptWorkspaceError(
                "HUMAN_INPUT_REQUIRED",
                "an in-progress handoff without an execution plane requires reconstruction",
            )
        else:
            if current != "none":
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "handoff current task is inconsistent"
                )
            selected = next_ready_task(order, sections, statuses)
            if selected is None:
                if predecessor_next not in {None, "none"}:
                    raise PromptWorkspaceError(
                        "EXECUTION_STATE_INVALID",
                        "stopped predecessor and next task selection disagree",
                    )
                if any(status == "pending" for status in statuses.values()):
                    raise PromptWorkspaceError(
                        "HUMAN_INPUT_REQUIRED",
                        "pending tasks have no dependency-ready execution path",
                    )
                if any(status == "blocked" for status in statuses.values()):
                    raise PromptWorkspaceError(
                        "HUMAN_INPUT_REQUIRED", "blocked tasks require resolution"
                    )
                raise PromptWorkspaceError(
                    "ALREADY_COMPLETE", "no dependency-ready pending task remains"
                )
        if predecessor_next is not None and predecessor_next != selected:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "stopped predecessor and next task selection disagree",
            )

        require_fresh_session(scope_planes, owner, run_id, selected)

        baseline, claim_head, clean = worktree_state(workspace)
        if not clean:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "task execution requires a clean Git worktree"
            )
        directory = execution_dir(run_dir)
        ensure_private_dir(directory)
        path = plane_path(run_dir, selected)
        if path.exists() or path.is_symlink():
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "task execution plane already exists"
            )
        plane: dict[str, object] = {
            "schema": EXECUTION_SCHEMA,
            "run_id": run_id,
            "task_id": selected,
            "bound_revision": verified["revision"],
            "phase": "planning",
            "owner_session_sha256": owner,
            "session_history_sha256": [owner],
            "worktree_baseline_sha256": baseline,
            "claim_head": claim_head,
            "plan_sha256": None,
            "queue_sha256": None,
            "checkpoint_sha256": None,
            "claimed_at": utc_text(clock()),
            "authorized_at": None,
            "completed_at": None,
            "recovery_count": 0,
            "stop_required": "yes",
            "next_session_required": "no",
        }
        write_plane(path, plane, exclusive=True)
        text = bind_claim_handoff(text, plane)
        write_atomic(run_dir / "handoff.md", text.encode("utf-8"))
        return {
            "task": selected,
            "phase": "planning",
            "recovered": False,
        }


def plan_values(text: str, task_id: str) -> dict[str, str]:
    _, sections = task_sections(text)
    if task_id not in sections:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution task is missing from the queue"
        )
    values = {label: field_block(sections[task_id], label) for label in PLAN_FIELDS}
    missing = [label for label, value in values.items() if not meaningful(value)]
    if missing:
        raise PromptWorkspaceError(
            "PLAN_REQUIRED",
            "task plan is incomplete: " + ", ".join(missing),
        )
    parse_repo_paths(values["Likely files"], "Likely files")
    if has_field(sections[task_id], "Requirement IDs"):
        spec_values = {
            label: field_block(sections[task_id], label)
            for label in SPEC_PLAN_FIELDS
        }
        missing = [
            label for label, value in spec_values.items() if not meaningful(value)
        ]
        if missing:
            raise PromptWorkspaceError(
                "PLAN_REQUIRED",
                "task specification plan is incomplete: " + ", ".join(missing),
            )
        requirement_ids(spec_values["Requirement IDs"])
        design_id(spec_values["Design ID"])
        for label in (
            "Requirements envelope SHA-256",
            "Design envelope SHA-256",
        ):
            if re.fullmatch(r"[0-9a-f]{64}", spec_values[label]) is None:
                raise PromptWorkspaceError(
                    "PLAN_REQUIRED", f"{label} is not a valid digest"
                )
        values.update(spec_values)
    return values


def _open_requirement_ids(text: str) -> set[str]:
    try:
        section = markdown_section(text, "Specification State")
        value = field_block(section, "Open requirement IDs")
    except PromptWorkspaceError:
        return set()
    if value.strip().casefold() == "none":
        return set()
    return set(requirement_ids(value, "Open requirement IDs"))


def validate_spec_plan(
    workspace: dict[str, object],
    text: str,
    task_id: str,
    values: dict[str, str],
    claim_head: str,
) -> None:
    if "Requirement IDs" not in values:
        return
    documents = inspect_spec_documents(workspace)
    allowed = parse_repo_paths(values["Likely files"], "Likely files")
    requirements_path = spec_repo_path(workspace, "requirements")[1]
    design_path = spec_repo_path(workspace, "design")[1]
    if not {requirements_path, design_path}.issubset(allowed):
        raise PromptWorkspaceError(
            "PLAN_REQUIRED",
            "spec-aware tasks must allow both managed specification documents",
        )
    if values["Requirements envelope SHA-256"] != documents["requirements"][
        "rendered_surrounding_sha256"
    ] or values["Design envelope SHA-256"] != documents["design"][
        "rendered_surrounding_sha256"
    ]:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document envelope changed during planning"
        )
    current_requirements = set(map(str, documents["requirements"]["ids"]))
    mapped_requirements: set[str] = set()
    proposed_requirements: set[str] = set()
    committed_documents = inspect_spec_documents(workspace, commit=claim_head)
    next_design_id = str(committed_documents["next_design_id"])
    order, sections = task_sections(text)
    statuses = task_statuses(sections)
    for queued_task in order:
        if statuses[queued_task] == "superseded":
            continue
        section = sections[queued_task]
        if not has_field(section, "Requirement IDs"):
            raise PromptWorkspaceError(
                "PLAN_REQUIRED",
                f"{queued_task} has no requirement mapping",
            )
        mapped_requirements.update(requirement_ids(field_block(section, "Requirement IDs")))
        if has_field(section, "Requirements proposal"):
            proposed_requirements.update(
                re.findall(
                    r"TI-REQ-[0-9]{3,}",
                    field_block(section, "Requirements proposal"),
                )
            )
        if queued_task != task_id:
            continue
        current_design = design_id(field_block(section, "Design ID"))
        if current_design != next_design_id:
            raise PromptWorkspaceError(
                "SPEC_CONFLICT",
                f"active task must allocate the next design ID {next_design_id}",
            )
    active_requirements = {
        item
        for item, status in dict(documents["requirements"]["statuses"]).items()
        if status == "active"
    }
    proposed_new = proposed_requirements - current_requirements
    first_new_match = REQUIREMENT_ID_RE.fullmatch(
        str(committed_documents["next_requirement_id"])
    )
    if first_new_match is None:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "next requirement ID is invalid"
        )
    first_new = int(first_new_match.group(1))
    expected_new = {
        f"TI-REQ-{number:03d}"
        for number in range(first_new, first_new + len(proposed_new))
    }
    if proposed_new != expected_new:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT",
            "new requirement IDs must be allocated contiguously from "
            f"{committed_documents['next_requirement_id']}",
        )
    known_requirements = current_requirements | proposed_requirements
    unknown = mapped_requirements - known_requirements
    if unknown:
        raise PromptWorkspaceError(
            "PLAN_REQUIRED",
            "task queue maps unknown requirements: " + ", ".join(sorted(unknown)),
        )
    uncovered = (active_requirements | proposed_requirements) - mapped_requirements
    uncovered -= _open_requirement_ids(text)
    if uncovered:
        raise PromptWorkspaceError(
            "PLAN_REQUIRED",
            "requirements lack task or open-question coverage: "
            + ", ".join(sorted(uncovered)),
        )
    active_ids = requirement_ids(values["Requirement IDs"])
    current_design = design_id(values["Design ID"])
    if current_design not in values["Design record"] or any(
        item not in values["Design record"] for item in active_ids
    ):
        raise PromptWorkspaceError(
            "PLAN_REQUIRED", "active design record does not map its requirements"
        )


def validate_spec_checkpoint(
    workspace: dict[str, object],
    plane: dict[str, object],
    values: dict[str, str],
    checkpoint: dict[str, str],
) -> None:
    if "Requirement IDs" not in values:
        return
    commit = checkpoint["Commit hash"]
    before = inspect_spec_documents(workspace, commit=str(plane["claim_head"]))
    final = inspect_spec_documents(workspace, commit=commit)
    if not final["requirements"]["managed"] or not final["design"]["managed"]:
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "managed specification documents are missing"
        )
    if (
        checkpoint["Requirements SHA-256"]
        != final["requirements"]["managed_sha256"]
        or checkpoint["Design SHA-256"] != final["design"]["managed_sha256"]
    ):
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "managed specification digest is incorrect"
        )
    if (
        final["requirements"]["surrounding_sha256"]
        != before["requirements"]["rendered_surrounding_sha256"]
        or final["design"]["surrounding_sha256"]
        != before["design"]["rendered_surrounding_sha256"]
    ):
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "user-owned specification content changed"
        )
    planned_requirements = set(requirement_ids(values["Requirement IDs"]))
    final_requirements = set(map(str, final["requirements"]["ids"]))
    active_design = design_id(values["Design ID"])
    final_designs = set(map(str, final["design"]["ids"]))
    refs = set(map(str, final["design"]["requirements"].get(active_design, [])))
    prior_design_records = dict(before["design"]["record_sha256"])
    final_design_records = dict(final["design"]["record_sha256"])
    if any(
        final_design_records.get(identifier) != digest
        for identifier, digest in prior_design_records.items()
    ):
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "implemented design history changed"
        )
    if (
        not planned_requirements.issubset(final_requirements)
        or active_design not in final_designs
        or not planned_requirements.issubset(refs)
        or final["design"]["statuses"].get(active_design) != "implemented"
    ):
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "committed specification traceability is incomplete"
        )
    if before["design"]["managed_sha256"] == final["design"]["managed_sha256"]:
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "the active task did not add or update its design"
        )
    recorded = parse_repo_paths(checkpoint["Files changed"], "Files changed")
    for kind in ("requirements", "design"):
        if before[kind]["managed_sha256"] != final[kind]["managed_sha256"]:
            path = spec_repo_path(workspace, kind)[1]
            if path not in recorded:
                raise PromptWorkspaceError(
                    "CHECKPOINT_REQUIRED",
                    f"changed {kind} document is missing from checkpoint paths",
                )


def validate_current_spec_state(
    workspace: dict[str, object],
    planes: list[dict[str, object]],
    text: str,
) -> None:
    """Reject committed drift inside the last checkpointed managed regions."""

    predecessor = last_completed_plane(planes, text)
    if predecessor is None:
        inspect_spec_documents(workspace)
        return
    task_id = str(predecessor["task_id"])
    checkpoint = checkpoint_for_task(text, task_id)
    if "Requirements SHA-256" not in checkpoint:
        inspect_spec_documents(workspace)
        return
    current = inspect_spec_documents(workspace)
    if (
        current["requirements"]["managed_sha256"]
        != checkpoint["Requirements SHA-256"]
        or current["design"]["managed_sha256"] != checkpoint["Design SHA-256"]
    ):
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "managed specification regions changed outside the workflow"
        )


def normalized_queue_digest(text: str, task_id: str) -> str:
    """Hash the queue while treating the active task's terminal flip as stable."""

    _, sections = task_sections(text)
    statuses = task_statuses(sections)
    if statuses.get(task_id) not in {"in_progress", "done"}:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution task is not active or checkpointed"
        )
    normalized = replace_task_status(text, task_id, "active")
    for label in IMPLEMENTATION_EVIDENCE_FIELDS:
        normalized = replace_task_field_block(
            normalized,
            task_id,
            label,
            "<implementation-evidence>",
        )
    return hashlib.sha256(
        markdown_section(normalized, "Task Queue").encode("utf-8")
    ).hexdigest()


def validate_plane_binding(
    plane: dict[str, object],
    text: str,
    verified: dict[str, object],
    *,
    allow_pending_repair: bool = False,
    allow_checkpointed: bool = False,
) -> str:
    """Cross-check execution ownership against the bound run and handoff."""

    if plane["bound_revision"] != verified["revision"]:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane revision is not the bound revision"
        )
    task_id = str(plane["task_id"])
    _, sections = task_sections(text)
    statuses = task_statuses(sections)
    status = statuses.get(task_id)
    run = markdown_section(text, "Run")
    current = handoff_field(run, "Current task")
    overall = handoff_field(run, "Overall status")
    if status == "in_progress":
        if current != task_id or overall != "running":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "active task and Run binding disagree"
            )
    elif (
        status == "pending"
        and allow_pending_repair
        and plane["phase"] == "planning"
    ):
        if current != "none" or overall != "prepared":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "pending claim repair is inconsistent"
            )
    elif status == "done" and allow_checkpointed and plane["phase"] in {
        "implementation",
        "stopped",
    }:
        if (
            current != "none"
            or handoff_field(run, "Last completed task") != task_id
            or overall not in {"prepared", "blocked", "done"}
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "checkpointed task and Run binding disagree"
            )
    else:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution plane and task status disagree"
        )
    queue_sha256 = plane.get("queue_sha256")
    if queue_sha256 is not None and normalized_queue_digest(text, task_id) != queue_sha256:
        raise PromptWorkspaceError(
            "PLAN_LOCKED", "task queue changed after implementation authorization"
        )
    return str(status)


def authorize_execution_plane(
    manifest_path: Path,
    run_id: str,
    *,
    session_id: str | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Lock the completed plan and authorize product implementation."""

    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    owner = session_fingerprint(session_id)
    with scope_lock(runs_root.parent):
        verified = verify_run(workspace, run_id, None)
        if verified["steering_pending"]:
            raise PromptWorkspaceError(
                "PLAN_REQUIRED",
                "pending steering must be reconciled and resolved before authorization",
            )
        run_dir = runs_root / run_id
        planes = load_execution_planes(run_dir)
        active = [plane for plane in planes if plane["phase"] in ACTIVE_PHASES]
        if len(active) != 1:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "exactly one claimed task is required"
            )
        plane = active[0]
        if plane["owner_session_sha256"] != owner:
            raise PromptWorkspaceError(
                "WORKSPACE_BUSY", "another session owns the active task execution plane"
            )
        text = read_handoff_text(run_dir)
        if text is None:
            raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "handoff is missing")
        validate_execution_index(planes, text)
        validate_plane_binding(
            plane,
            text,
            verified,
            allow_checkpointed=plane["phase"] == "implementation",
        )
        values = plan_values(text, str(plane["task_id"]))
        validate_spec_plan(
            workspace,
            text,
            str(plane["task_id"]),
            values,
            str(plane["claim_head"]),
        )
        digest = hashlib.sha256(stable_json(values)).hexdigest()
        queue_digest = normalized_queue_digest(text, str(plane["task_id"]))
        if plane["phase"] == "implementation":
            if plane["plan_sha256"] != digest or plane["queue_sha256"] != queue_digest:
                raise PromptWorkspaceError(
                    "PLAN_LOCKED", "authorized task contract changed after locking"
                )
            repaired = upsert_execution_section(text, plane)
            if repaired != text:
                write_atomic(run_dir / "handoff.md", repaired.encode("utf-8"))
            return {
                "task": plane["task_id"],
                "phase": "implementation",
                "plan_sha256": digest,
                "authorized": True,
            }
        current_baseline, _, _ = worktree_state(workspace)
        if current_baseline != plane["worktree_baseline_sha256"]:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "product files changed before plan authorization"
            )
        plane["phase"] = "implementation"
        plane["plan_sha256"] = digest
        plane["queue_sha256"] = queue_digest
        plane["authorized_at"] = utc_text(clock())
        updated = upsert_execution_section(text, plane)
        write_atomic(run_dir / "handoff.md", updated.encode("utf-8"))
        write_plane(plane_path(run_dir, str(plane["task_id"])), plane)
        return {
            "task": plane["task_id"],
            "phase": "implementation",
            "plan_sha256": digest,
            "authorized": True,
        }


def rebind_planning_execution_plane(
    manifest_path: Path,
    run_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, object]:
    """Rebind one clean same-session planning plane for prompt steering."""

    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    owner = session_fingerprint(session_id)
    with scope_lock(runs_root.parent):
        verified = verify_run(workspace, run_id, None)
        if not verified["steering_pending"]:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "planning rebind requires pending steering"
            )
        run_dir = runs_root / run_id
        planes = load_execution_planes(run_dir)
        active = [plane for plane in planes if plane["phase"] in ACTIVE_PHASES]
        if len(active) != 1 or active[0]["phase"] != "planning":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "planning rebind requires one planning plane"
            )
        plane = active[0]
        if plane["owner_session_sha256"] != owner:
            raise PromptWorkspaceError(
                "WORKSPACE_BUSY", "another session owns the planning execution plane"
            )
        if plane["plan_sha256"] is not None or plane["queue_sha256"] is not None:
            raise PromptWorkspaceError(
                "PLAN_LOCKED", "an authorized execution plane cannot be rebound"
            )
        baseline, head, clean = worktree_state(workspace)
        if (
            not clean
            or baseline != plane["worktree_baseline_sha256"]
            or head != plane["claim_head"]
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "worktree changed during steering replan"
            )
        text = read_handoff_text(run_dir)
        if text is None:
            raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "handoff is missing")
        validate_execution_index(planes, text)
        task_id = str(plane["task_id"])
        manifests = [
            manifest
            for candidate, manifest in load_run_manifests(runs_root)
            if candidate.name == run_id
        ]
        if len(manifests) != 1:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "planning rebind run manifest is missing"
            )
        revisions = manifest_revisions(manifests[0])
        latest = revisions[-1]
        latest_revision = str(latest["revision"])
        known_revisions = {str(item["revision"]) for item in revisions}
        plane_revision = str(plane["bound_revision"])
        handoff_revision = str(verified["revision"])
        if (
            plane_revision not in known_revisions
            or handoff_revision not in known_revisions
            or (
                plane_revision != handoff_revision
                and latest_revision not in {plane_revision, handoff_revision}
            )
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "planning rebind has an irreconcilable partial transition",
            )
        binding = dict(verified)
        binding["revision"] = plane_revision
        validate_plane_binding(plane, text, binding)
        if plane_revision != latest_revision:
            previous_revision = plane_revision
        elif handoff_revision != latest_revision:
            previous_revision = handoff_revision
        else:
            previous_revision = field_block(
                markdown_section(text, "Reconciliation"),
                "Previous bound revision",
            )
            if previous_revision not in known_revisions:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "completed planning rebind has no valid previous revision",
                )
        text = replace_section_field(text, "Run", "Bound revision", latest_revision)
        text = replace_section_field(
            text, "Run", "Bound SHA-256", str(latest["sha256"])
        )
        text = replace_section_field(
            text,
            "Run",
            "Bound snapshot path",
            str(run_dir / str(latest["snapshot"])),
        )
        text = replace_task_field_block(
            text, task_id, "Source revision", latest_revision
        )
        clear_fields = list(PLAN_FIELDS)
        _, task_map = task_sections(text)
        if has_field(task_map[task_id], "Requirement IDs"):
            clear_fields.extend(SPEC_PLAN_FIELDS)
        for label in clear_fields:
            text = replace_task_field_block(text, task_id, label, "")
        text = replace_section_field(text, "Reconciliation", "State", "proposed")
        text = replace_section_field(
            text,
            "Reconciliation",
            "Previous bound revision",
            previous_revision,
        )
        text = replace_section_field(
            text, "Reconciliation", "Current bound revision", latest_revision
        )
        text = replace_section_field(
            text,
            "Reconciliation",
            "Summary",
            "pending same-task steering replan",
        )
        plane["bound_revision"] = latest_revision
        updated = upsert_execution_section(text, plane)
        write_atomic(run_dir / "handoff.md", updated.encode("utf-8"))
        write_plane(plane_path(run_dir, task_id), plane)
        rebound = verify_run(workspace, run_id, None)
        rebound_planes = load_execution_planes(run_dir)
        validate_execution_index(rebound_planes, updated)
        validate_plane_binding(plane, updated, rebound)
        return {
            "task": task_id,
            "phase": "planning",
            "bound_revision": latest_revision,
            "replan_required": True,
        }


def checkpoint_sections(text: str) -> dict[str, tuple[str, str]]:
    _, tasks = task_sections(text)
    statuses = task_statuses(tasks)
    checkpoints = markdown_section(text, "Checkpoints")
    matches = list(
        re.finditer(r"(?m)^### (checkpoint-[1-9][0-9]*)\s*$", checkpoints)
    )
    checkpoint_ids: set[str] = set()
    by_task: dict[str, tuple[str, str]] = {}
    for index, match in enumerate(matches):
        checkpoint_id = match.group(1)
        if checkpoint_id in checkpoint_ids:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "handoff repeats a checkpoint ID"
            )
        checkpoint_ids.add(checkpoint_id)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(checkpoints)
        section = checkpoints[start:end]
        completed_task = field_block(section, "Completed task")
        if not completed_task:
            continue
        if (
            TASK_ID_RE.fullmatch(completed_task) is None
            or completed_task not in tasks
            or statuses[completed_task] not in {"in_progress", "done"}
            or completed_task in by_task
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "handoff checkpoint task index is invalid"
            )
        by_task[completed_task] = (checkpoint_id, section)
    return by_task


def validate_execution_index(
    planes: list[dict[str, object]], text: str
) -> None:
    """Enforce a bijection between completed tasks, checkpoints, and planes."""

    _, tasks = task_sections(text)
    statuses = task_statuses(tasks)
    checkpoints = checkpoint_sections(text)
    by_task = {str(plane["task_id"]): plane for plane in planes}
    if len(by_task) != len(planes):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "execution planes repeat a task"
        )
    active = [plane for plane in planes if plane["phase"] in ACTIVE_PHASES]
    active_plane = active[0] if active else None
    active_task = str(active_plane["task_id"]) if active_plane is not None else None
    stopped_tasks = {
        str(plane["task_id"]) for plane in planes if plane["phase"] == "stopped"
    }
    done_tasks = {task_id for task_id, status in statuses.items() if status == "done"}

    for task_id, plane in by_task.items():
        status = statuses.get(task_id)
        phase = plane["phase"]
        if (
            (phase == "stopped" and status != "done")
            or (phase == "planning" and status not in {"pending", "in_progress"})
            or (phase == "implementation" and status not in {"in_progress", "done"})
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "execution plane and task index disagree"
            )

    active_done = (
        {active_task}
        if active_plane is not None
        and active_plane["phase"] == "implementation"
        and active_task is not None
        and statuses.get(active_task) == "done"
        else set()
    )
    if done_tasks != stopped_tasks | active_done:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "done tasks and stopped planes are inconsistent"
        )
    checkpoint_tasks = set(checkpoints)
    if not stopped_tasks.issubset(checkpoint_tasks):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "stopped plane checkpoint is missing"
        )
    nonstopped_checkpoints = checkpoint_tasks - stopped_tasks
    if nonstopped_checkpoints:
        if (
            active_plane is None
            or active_plane["phase"] != "implementation"
            or nonstopped_checkpoints != {active_task}
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "checkpoint has no matching stopped or implementation plane",
            )


def checkpoint_for_task(text: str, task_id: str) -> dict[str, str]:
    indexed = checkpoint_sections(text)
    if task_id not in indexed:
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "handoff has no checkpoint for the active task"
        )
    _, section = indexed[task_id]
    values = {label: field_block(section, label) for label in CHECKPOINT_FIELDS}
    missing = [label for label, value in values.items() if not meaningful(value)]
    if missing:
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED",
            "task checkpoint is incomplete: " + ", ".join(missing),
        )
    values["Bound revision"] = field_block(section, "Bound revision")
    values["Next task"] = field_block(section, "Next task")
    _, tasks = task_sections(text)
    if has_field(tasks[task_id], "Requirement IDs"):
        spec_values = {
            label: field_block(section, label) for label in SPEC_CHECKPOINT_FIELDS
        }
        missing = [
            label for label, value in spec_values.items() if not meaningful(value)
        ]
        if missing:
            raise PromptWorkspaceError(
                "CHECKPOINT_REQUIRED",
                "task specification checkpoint is incomplete: "
                + ", ".join(missing),
            )
        for label in ("Requirements SHA-256", "Design SHA-256"):
            if re.fullmatch(r"[0-9a-f]{64}", spec_values[label]) is None:
                raise PromptWorkspaceError(
                    "CHECKPOINT_REQUIRED", f"{label} is not a valid digest"
                )
        values.update(spec_values)
    return values


def completed_checkpoint_digest(text: str, task_id: str) -> str:
    _, task_map = task_sections(text)
    statuses = task_statuses(task_map)
    if statuses.get(task_id) != "done":
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "checkpoint digest requires a completed task"
        )
    indexed = checkpoint_sections(text)
    if task_id not in indexed:
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "completed task checkpoint is missing"
        )
    checkpoint_id, checkpoint_section = indexed[task_id]
    return hashlib.sha256(
        stable_json(
            {
                "task_id": task_id,
                "task_section": task_map[task_id],
                "checkpoint_id": checkpoint_id,
                "checkpoint_section": checkpoint_section,
            }
        )
    ).hexdigest()


def validate_reconciliation_override(
    text: str,
    verified: dict[str, object],
    predecessor: str,
    historical_next: str,
    selected_next: str,
) -> None:
    """Require an explicit revision-bound override for changed next-task advice."""

    reconciliation = markdown_section(text, "Reconciliation")
    value = field_block(reconciliation, "Next-task overrides")
    expected = (
        f"{predecessor} | {historical_next} -> {selected_next} | "
        f"{verified['revision']} | {verified['sha256']}"
    )
    entries = [
        line.strip()[2:].strip()
        for line in value.splitlines()
        if line.strip().startswith("- ")
    ]
    if entries.count(expected) != 1:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "changed next-task selection lacks a unique revision-bound override",
        )


def validate_completed_plane_history(
    workspace: dict[str, object],
    plane: dict[str, object],
    text: str,
) -> None:
    """Verify immutable completed-task evidence for every historical plane."""

    task_id = str(plane["task_id"])
    if plane["phase"] != "stopped":
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "completed execution plane is not stopped"
        )
    values = plan_values(text, task_id)
    if (
        hashlib.sha256(stable_json(values)).hexdigest() != plane["plan_sha256"]
        or plane.get("checkpoint_sha256") != completed_checkpoint_digest(text, task_id)
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "completed task or checkpoint evidence changed"
        )
    checkpoint = checkpoint_for_task(text, task_id)
    if checkpoint["Bound revision"] != plane["bound_revision"]:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "completed checkpoint revision changed"
        )
    verify_checkpoint_commit(
        workspace,
        checkpoint["Commit hash"],
        str(plane["claim_head"]),
        checkpoint["Commit message"],
        parse_repo_paths(values["Likely files"], "Likely files"),
        parse_repo_paths(checkpoint["Files changed"], "Files changed"),
        require_current_head=False,
    )
    validate_spec_checkpoint(workspace, plane, values, checkpoint)


def validate_checkpoint_evidence(
    workspace: dict[str, object],
    verified: dict[str, object],
    plane: dict[str, object],
    text: str,
    *,
    historical_stopped: bool = False,
) -> tuple[dict[str, str], dict[str, str], str, str, bool, str, str]:
    """Validate the complete commit, queue, handoff, and stop-boundary evidence."""

    task_id = str(plane["task_id"])
    if historical_stopped:
        _, task_map = task_sections(text)
        statuses = task_statuses(task_map)
        status = statuses.get(task_id)
        run = markdown_section(text, "Run")
        if (
            plane["phase"] != "stopped"
            or status != "done"
            or handoff_field(run, "Current task") != "none"
            or handoff_field(run, "Last completed task") != task_id
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "historical checkpoint binding is inconsistent"
            )
    else:
        status = validate_plane_binding(
            plane,
            text,
            verified,
            allow_checkpointed=True,
        )
    values = plan_values(text, task_id)
    contract_changed = (
        hashlib.sha256(stable_json(values)).hexdigest() != plane["plan_sha256"]
    )
    if not historical_stopped:
        contract_changed = contract_changed or (
            normalized_queue_digest(text, task_id) != plane["queue_sha256"]
        )
    if contract_changed:
        raise PromptWorkspaceError(
            "PLAN_LOCKED", "task contract changed after implementation authorization"
        )
    if plane["phase"] == "stopped" and (
        plane.get("checkpoint_sha256") != completed_checkpoint_digest(text, task_id)
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "completed checkpoint digest changed"
        )
    checkpoint = checkpoint_for_task(text, task_id)
    if checkpoint["Bound revision"] != plane["bound_revision"] or (
        not historical_stopped
        and checkpoint["Bound revision"] != verified["revision"]
    ):
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "checkpoint bound revision is incorrect"
        )
    verify_checkpoint_commit(
        workspace,
        checkpoint["Commit hash"],
        str(plane["claim_head"]),
        checkpoint["Commit message"],
        parse_repo_paths(values["Likely files"], "Likely files"),
        parse_repo_paths(checkpoint["Files changed"], "Files changed"),
    )
    validate_spec_checkpoint(workspace, plane, values, checkpoint)

    order, sections = task_sections(text)
    statuses = task_statuses(sections)
    if status == "in_progress":
        statuses[task_id] = "done"
    next_task = next_ready_task(order, sections, statuses)
    expected_next = next_task or "none"
    unfinished = any(item in {"pending", "blocked"} for item in statuses.values())
    overall = "prepared" if next_task is not None else (
        "blocked" if unfinished else "done"
    )

    session = markdown_section(text, "Session Handoff")
    if (
        handoff_field(session, "Current session action")
        != "stop after saving this handoff"
        or handoff_field(session, "Do not continue in current session") != "yes"
        or not meaningful(handoff_field(session, "Next session mechanism"))
        or handoff_field(session, "Next task") != expected_next
    ):
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "handoff stop boundary or next task is invalid"
        )
    if not historical_stopped and checkpoint["Next task"] != expected_next:
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "checkpoint next task does not match the queue"
        )
    if historical_stopped and checkpoint["Next task"] != expected_next:
        validate_reconciliation_override(
            text,
            verified,
            task_id,
            checkpoint["Next task"],
            expected_next,
        )
    next_prompt = markdown_section(text, "Next Session Prompt")
    if (
        "$task-implementer run " not in next_prompt
        or "<same-prompt-path-or-unique-filename>" in next_prompt
        or "$task-implementer continue" in next_prompt
        or str(verified["source_path"]) not in next_prompt
    ):
        raise PromptWorkspaceError(
            "CHECKPOINT_REQUIRED", "next-session command is missing or unresolved"
        )
    if status == "done":
        run = markdown_section(text, "Run")
        if (
            handoff_field(run, "Last commit") != checkpoint["Commit hash"]
            or handoff_field(run, "Overall status") != overall
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "completed Run checkpoint is inconsistent"
            )
    return checkpoint, statuses, task_id, expected_next, unfinished, overall, status


def validate_stopped_execution_plane(
    workspace: dict[str, object],
    verified: dict[str, object],
    plane: dict[str, object],
    text: str,
) -> tuple[dict[str, str], dict[str, str], str, str, bool, str, str]:
    if plane["phase"] != "stopped":
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "predecessor execution plane is not stopped"
        )
    historical = plane["bound_revision"] != verified["revision"]
    evidence = validate_checkpoint_evidence(
        workspace,
        verified,
        plane,
        text,
        historical_stopped=historical,
    )
    unfinished = evidence[4]
    if (
        plane["stop_required"] != "yes"
        or (
            not historical
            and plane["next_session_required"] != ("yes" if unfinished else "no")
        )
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "stopped execution plane is inconsistent"
        )
    return evidence


def checkpoint_execution_plane(
    manifest_path: Path,
    run_id: str,
    *,
    session_id: str | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Checkpoint exactly one task and persist the mandatory session stop."""

    workspace = verify_workspace(manifest_path)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    owner = session_fingerprint(session_id)
    with scope_lock(runs_root.parent):
        verified = verify_run(workspace, run_id, None)
        run_dir = runs_root / run_id
        planes = load_execution_planes(run_dir)
        text = read_handoff_text(run_dir)
        if text is None:
            raise PromptWorkspaceError("EXECUTION_STATE_INVALID", "handoff is missing")
        validate_execution_index(planes, text)
        for stopped_plane in (
            candidate for candidate in planes if candidate["phase"] == "stopped"
        ):
            validate_completed_plane_history(workspace, stopped_plane, text)
        active = [plane for plane in planes if plane["phase"] in ACTIVE_PHASES]
        if len(active) != 1:
            stopped = last_completed_plane(planes, text)
            if stopped is not None and stopped["owner_session_sha256"] == owner:
                validate_stopped_execution_plane(workspace, verified, stopped, text)
                return {
                    "task": stopped["task_id"],
                    "phase": "stopped",
                    "idempotent": True,
                }
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "no active execution plane can checkpoint"
            )
        plane = active[0]
        if plane["owner_session_sha256"] != owner:
            raise PromptWorkspaceError(
                "WORKSPACE_BUSY", "another session owns the active task execution plane"
            )
        if plane["phase"] != "implementation":
            raise PromptWorkspaceError(
                "PLAN_REQUIRED", "implementation must be plan-authorized first"
            )
        (
            checkpoint,
            _statuses,
            task_id,
            expected_next,
            unfinished,
            overall,
            status,
        ) = validate_checkpoint_evidence(workspace, verified, plane, text)
        if status == "in_progress":
            text = replace_task_status(text, task_id, "done")
        text = replace_section_field(text, "Run", "Current task", "none")
        text = replace_section_field(text, "Run", "Last completed task", task_id)
        text = replace_section_field(text, "Run", "Last commit", checkpoint["Commit hash"])
        text = replace_section_field(text, "Run", "Overall status", overall)

        plane["phase"] = "stopped"
        plane["completed_at"] = utc_text(clock())
        plane["stop_required"] = "yes"
        plane["next_session_required"] = "yes" if unfinished else "no"
        plane["checkpoint_sha256"] = completed_checkpoint_digest(text, task_id)
        text = upsert_execution_section(text, plane)
        write_atomic(run_dir / "handoff.md", text.encode("utf-8"))
        write_plane(plane_path(run_dir, task_id), plane)
        return {
            "task": task_id,
            "phase": "stopped",
            "next_task": expected_next,
            "next_session_required": bool(unfinished),
            "idempotent": False,
        }
