#!/usr/bin/env python3
"""Adopt exact sealed lifecycle deltas into a retained integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from prompt_workspace_core import (
    PromptWorkspaceError,
    ensure_private_dir,
    iso_seconds,
    load_json_object,
    now_utc,
    required_string,
    stable_json,
    verify_workspace,
    write_atomic,
    write_exclusive,
)
from prompt_workspace_execution import load_coordinator_state, orchestration_dir
from prompt_workspace_interop import (
    acquire_interop,
    load_checkpoint_receipt,
    load_interop,
    observe_managed_state,
    prepare_checkpoint,
    record_promotion,
    release_interop,
)
from prompt_workspace_runs import scope_lock
from prompt_workspace_specs import (
    load_current_prompt_impact,
    verify_prompt_impact_plan,
)


CONTRACT_DELTA_SCHEMA = "task-implementer/contract-delta-adoption-v1"
CONTRACT_DELTA_MESSAGE = "chore(task-implementer): adopt sealed contract delta"
TERMINAL_SEAL_SCHEMA = "task-implementer/terminal-lifecycle-seal-v1"
TERMINAL_SEAL_MESSAGE = "chore(task-implementer): adopt terminal lifecycle seal"
TERMINAL_RECOVERY_SCHEMA = "task-implementer/terminal-lifecycle-recovery-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SHA_RE = re.compile(r"[0-9a-f]{40,64}")
PHASES = {
    "intent",
    "integration-committed",
    "promotion-clearing",
    "lane-cleared",
    "promoted",
}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _run_dir(workspace: dict[str, object], run_id: str) -> Path:
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    run_dir = runs_root / run_id
    if run_dir.parent != runs_root or run_dir.is_symlink() or not run_dir.is_dir():
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run directory is unsafe")
    return run_dir


def _journal_path(run_dir: Path) -> Path:
    return orchestration_dir(run_dir) / "contract-delta-adoption.json"


def _terminal_seal_path(run_dir: Path, wave_id: str) -> Path:
    if re.fullmatch(r"wave-(?:[0-9]{3}|r[0-9a-f]{8}-[0-9]{3})", wave_id) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "terminal lifecycle wave identity is invalid"
        )
    return orchestration_dir(run_dir) / "terminal-lifecycle-seals" / f"{wave_id}.json"


def _terminal_recovery_root(run_dir: Path) -> Path:
    return orchestration_dir(run_dir) / "terminal-lifecycle-recovery"


def _terminal_recovery_path(run_dir: Path) -> Path:
    return _terminal_recovery_root(run_dir) / "receipt.json"


def _terminal_recovery_run_dir(run_dir: Path) -> Path:
    return _terminal_recovery_root(run_dir) / "generation" / run_dir.name


def _git(
    repo: Path,
    arguments: list[str],
    label: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", f"Git could not {label}"
        ) from error
    if check and result.returncode != 0:
        raise PromptWorkspaceError("WORKTREE_CONFLICT", f"Git could not {label}")
    return result


def _git_text(repo: Path, arguments: list[str], label: str) -> str:
    try:
        return _git(repo, arguments, label).stdout.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", f"Git returned invalid text while trying to {label}"
        ) from error


def _git_paths(repo: Path, arguments: list[str], label: str) -> list[str]:
    raw = _git(repo, arguments, label).stdout
    try:
        return sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)
    except UnicodeDecodeError as error:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", f"Git returned an invalid path while trying to {label}"
        ) from error


def _branch(repo: Path) -> str:
    return _git_text(repo, ["branch", "--show-current"], "inspect lane branch")


def _head(repo: Path) -> str:
    return _git_text(repo, ["rev-parse", "HEAD"], "inspect Git head")


def _clean(repo: Path) -> bool:
    return not _git(repo, ["status", "--porcelain=v1", "-z"], "inspect status").stdout


def _common_dir(repo: Path) -> Path:
    return Path(
        _git_text(
            repo,
            ["rev-parse", "--path-format=absolute", "--git-common-dir"],
            "inspect Git common directory",
        )
    ).resolve()


def _linked_worktree_identity(repo: Path, worktree: Path, branch: object) -> bool:
    raw = _git(
        repo, ["worktree", "list", "--porcelain", "-z"], "list linked worktrees"
    ).stdout
    try:
        fields = raw.decode("utf-8").split("\0")
    except UnicodeDecodeError:
        return False
    records: dict[Path, dict[str, str]] = {}
    current: dict[str, str] = {}
    for field in fields:
        if not field:
            if "worktree" in current:
                records[Path(current["worktree"]).resolve()] = current
            current = {}
            continue
        key, _, value = field.partition(" ")
        current[key] = value
    if "worktree" in current:
        records[Path(current["worktree"]).resolve()] = current
    record = records.get(worktree.resolve())
    return (
        isinstance(branch, str)
        and record is not None
        and record.get("branch") == f"refs/heads/{branch}"
        and Path(
            _git_text(
                worktree,
                ["rev-parse", "--path-format=absolute", "--show-toplevel"],
                "inspect integration root",
            )
        ).resolve()
        == worktree.resolve()
        and _common_dir(worktree) == _common_dir(repo)
    )


def _contract_paths(workspace: dict[str, object]) -> dict[str, Path]:
    scope = required_string(workspace, "scope", "workspace manifest")
    project = Path(required_string(workspace, "source_root", "workspace manifest"))
    prefix = "" if scope == "." else f"{scope}/"
    return {
        f"{prefix}docs/requirements.md": project / "docs" / "requirements.md",
        f"{prefix}docs/design.md": project / "docs" / "design.md",
        f"{prefix}AGENTS.md": project / "AGENTS.md",
    }


def _validate_lane_delta(
    workspace: dict[str, object],
    expected_paths: list[str] | None = None,
    *,
    allow_empty: bool = False,
) -> tuple[list[str], dict[str, str]]:
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    allowed = _contract_paths(workspace)
    staged = _git_paths(
        repo,
        ["diff", "--cached", "--no-renames", "--name-only", "-z", "--"],
        "inspect staged contract paths",
    )
    unstaged = _git_paths(
        repo,
        ["diff", "--no-renames", "--name-only", "-z", "--"],
        "inspect contract paths",
    )
    untracked = _git_paths(
        repo,
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        "inspect untracked paths",
    )
    deleted = _git_paths(
        repo,
        ["diff", "--no-renames", "--diff-filter=D", "--name-only", "-z", "--"],
        "inspect deleted contract paths",
    )
    if (
        staged
        or untracked
        or deleted
        or (not unstaged and not allow_empty)
        or not set(unstaged) <= set(allowed)
        or (expected_paths is not None and unstaged != expected_paths)
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT",
            "sealed contract adoption requires only exact unstaged contract paths",
        )
    files: dict[str, str] = {}
    for relative in unstaged:
        path = allowed[relative]
        if path.is_symlink() or not path.is_file():
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "sealed contract path is unsafe"
            )
        files[relative] = _digest(path.read_bytes())
    return unstaged, files


def _contract_file_digests(workspace: dict[str, object]) -> dict[str, str | None]:
    files: dict[str, str | None] = {}
    for relative, path in _contract_paths(workspace).items():
        if path.is_symlink():
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "sealed contract path is unsafe"
            )
        if path.is_file():
            files[relative] = _digest(path.read_bytes())
        elif path.name == "AGENTS.md" and not path.exists():
            files[relative] = None
        else:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "sealed contract path is missing"
            )
    return files


def _lane_delta_is_subset(
    workspace: dict[str, object], expected_paths: list[str]
) -> bool:
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    allowed = _contract_paths(workspace)
    staged = _git_paths(
        repo,
        ["diff", "--cached", "--no-renames", "--name-only", "-z", "--"],
        "inspect staged contract paths",
    )
    unstaged = _git_paths(
        repo,
        ["diff", "--no-renames", "--name-only", "-z", "--"],
        "inspect contract paths",
    )
    untracked = _git_paths(
        repo,
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        "inspect untracked paths",
    )
    deleted = _git_paths(
        repo,
        ["diff", "--no-renames", "--diff-filter=D", "--name-only", "-z", "--"],
        "inspect deleted contract paths",
    )
    return (
        not staged
        and not untracked
        and not deleted
        and set(unstaged) <= set(expected_paths)
        and all(
            relative in allowed
            and allowed[relative].is_file()
            and not allowed[relative].is_symlink()
            for relative in unstaged
        )
    )


def _codex_root(workspace: dict[str, object]) -> Path:
    prompt_root = Path(
        required_string(workspace, "prompt_root", "workspace manifest")
    ).resolve()
    for parent in (prompt_root, *prompt_root.parents):
        if parent.name == "task-implementer":
            return parent.parent
    raise PromptWorkspaceError(
        "EXECUTION_STATE_INVALID", "workspace has no canonical private Codex root"
    )


def _validated_lifecycle_contract(
    workspace: dict[str, object],
    lifecycle_path: Path,
    lane_head: str,
    paths: list[str],
    files: dict[str, str],
    *,
    lifecycle_head_range: tuple[str, str] | None = None,
) -> tuple[dict[str, object], str, dict[str, str | None]]:
    if (
        not lifecycle_path.is_absolute()
        or lifecycle_path.is_symlink()
        or lifecycle_path.name != "lifecycle.json"
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "lifecycle state path is unsafe"
        )
    resolved = lifecycle_path.resolve()
    private_root = _codex_root(workspace) / "project-specs"
    try:
        resolved.relative_to(private_root.resolve())
    except ValueError as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "lifecycle state is outside project-specs"
        ) from error
    lifecycle = load_json_object(resolved, "sealed project lifecycle")
    scope = required_string(workspace, "scope", "workspace manifest")
    project = Path(required_string(workspace, "source_root", "workspace manifest"))
    prefix = "" if scope == "." else f"{scope}/"
    requirements = f"{prefix}docs/requirements.md"
    design = f"{prefix}docs/design.md"
    contract_files = _contract_file_digests(workspace)
    receipt = lifecycle.get("receipt_sha256")
    instruction_state_sha256 = lifecycle.get("project_instructions_state_sha256")
    lifecycle_head = lifecycle.get("git_head_at_prompt")
    head_matches = lifecycle_head == lane_head
    if (
        lifecycle_head_range is not None
        and isinstance(lifecycle_head, str)
        and SHA_RE.fullmatch(lifecycle_head) is not None
    ):
        minimum, maximum = lifecycle_head_range
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        head_matches = (
            _git(
                repo,
                ["merge-base", "--is-ancestor", minimum, lifecycle_head],
                "verify lifecycle lower ancestry",
                check=False,
            ).returncode
            == 0
            and _git(
                repo,
                ["merge-base", "--is-ancestor", lifecycle_head, maximum],
                "verify lifecycle upper ancestry",
                check=False,
            ).returncode
            == 0
        )
    if (
        lifecycle.get("schema") != "maintain-project-specs.lifecycle.v1"
        or lifecycle.get("phase") != "sealed"
        or lifecycle.get("project_scope") != scope
        or not head_matches
        or lifecycle.get("requirements_sha256") != contract_files.get(requirements)
        or lifecycle.get("design_sha256") != contract_files.get(design)
        or SHA256_RE.fullmatch(str(receipt or "")) is None
        or SHA256_RE.fullmatch(str(instruction_state_sha256 or "")) is None
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "sealed project lifecycle does not bind the delta",
        )
    agents = f"{prefix}AGENTS.md"
    instruction_state_path = resolved.parent / "project-instructions" / "state.json"
    instruction_state = load_json_object(
        instruction_state_path, "sealed project instructions"
    )
    raw_state = instruction_state_path.read_bytes()
    agents_path = project / "AGENTS.md"
    expected_agents_sha256 = contract_files.get(agents)
    if (
        _digest(raw_state) != instruction_state_sha256
        or instruction_state.get("schema") != "project-agent-instructions.state.v3"
        or instruction_state.get("project_root") != str(project.resolve())
        or instruction_state.get("project_scope") != scope
        or instruction_state.get("target_path") != str(agents_path.resolve())
        or instruction_state.get("target_sha256") != expected_agents_sha256
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "sealed project instructions do not bind the delta",
        )
    if any(files.get(path) != contract_files.get(path) for path in paths):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "sealed lifecycle delta bytes changed"
        )
    return lifecycle, _digest(resolved.read_bytes()), contract_files


def _validated_lifecycle(
    workspace: dict[str, object],
    lifecycle_path: Path,
    lane_head: str,
    paths: list[str],
    files: dict[str, str],
) -> tuple[dict[str, object], str]:
    lifecycle, lifecycle_sha256, _contract_files = _validated_lifecycle_contract(
        workspace, lifecycle_path, lane_head, paths, files
    )
    return lifecycle, lifecycle_sha256


def _load_wave(run_dir: Path, wave_id: str) -> dict[str, object]:
    path = orchestration_dir(run_dir) / "waves" / f"{wave_id}.json"
    value = load_json_object(path, "contract-delta wave")
    if value.get("wave_id") != wave_id or value.get("run_id") != run_dir.name:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "contract-delta wave identity is invalid"
        )
    return value


def _load_journal(run_dir: Path, *, required: bool = False) -> dict[str, object] | None:
    path = _journal_path(run_dir)
    if path.is_symlink():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "contract-delta journal path is unsafe"
        )
    if not path.exists():
        if required:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "contract-delta journal is missing"
            )
        return None
    value = load_json_object(path, "contract-delta journal")
    required_fields = {
        "schema",
        "run_id",
        "wave_id",
        "lane_head",
        "integration_base",
        "contract_head",
        "paths",
        "files_sha256",
        "lifecycle_receipt_sha256",
        "lifecycle_state_sha256",
        "impact_sha256",
        "phase",
        "promotion_target",
        "created_at",
    }
    paths = value.get("paths")
    files = value.get("files_sha256")
    if (
        set(value) != required_fields
        or value.get("schema") != CONTRACT_DELTA_SCHEMA
        or value.get("run_id") != run_dir.name
        or not isinstance(value.get("wave_id"), str)
        or SHA_RE.fullmatch(str(value.get("lane_head") or "")) is None
        or SHA_RE.fullmatch(str(value.get("integration_base") or "")) is None
        or (
            value.get("contract_head") is not None
            and SHA_RE.fullmatch(str(value["contract_head"])) is None
        )
        or not isinstance(paths, list)
        or not paths
        or paths != sorted(set(paths))
        or not isinstance(files, dict)
        or set(files) != set(paths)
        or any(SHA256_RE.fullmatch(str(item)) is None for item in files.values())
        or SHA256_RE.fullmatch(str(value.get("lifecycle_receipt_sha256") or "")) is None
        or SHA256_RE.fullmatch(str(value.get("lifecycle_state_sha256") or "")) is None
        or SHA256_RE.fullmatch(str(value.get("impact_sha256") or "")) is None
        or value.get("phase") not in PHASES
        or (
            value.get("promotion_target") is not None
            and SHA_RE.fullmatch(str(value["promotion_target"])) is None
        )
        or not isinstance(value.get("created_at"), str)
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "contract-delta journal is invalid"
        )
    return value


def _write_repo_file(path: Path, data: bytes, mode: int) -> None:
    if path.is_symlink() or not path.parent.is_dir():
        raise PromptWorkspaceError("WORKTREE_CONFLICT", "contract target is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _integration_contains(
    integration: Path, paths: list[str], files: dict[str, str]
) -> bool:
    if not _clean(integration):
        return False
    return all(
        (integration / relative).is_file()
        and not (integration / relative).is_symlink()
        and _digest((integration / relative).read_bytes()) == files[relative]
        for relative in paths
    )


def _coordinator_reconciliation_paths(
    workspace: dict[str, object],
) -> set[str]:
    scope = required_string(workspace, "scope", "workspace manifest")
    prefix = "" if scope == "." else f"{scope.rstrip('/')}/"
    return {
        f"{prefix}docs/requirements.md",
        f"{prefix}docs/design.md",
        f"{prefix}README.md",
        f"{prefix}CHANGELOG.md",
    }


def _integration_preserves_adopted_contract(
    workspace: dict[str, object],
    integration: Path,
    wave: dict[str, object],
    paths: list[str],
    files: dict[str, str],
) -> bool:
    """Accept exact adopted bytes or their one attested final reconciliation."""

    if _integration_contains(integration, paths, files):
        return True
    integrated_head = wave.get("integrated_head")
    if wave.get("status") != "promotion_pending" or not isinstance(
        integrated_head, str
    ):
        return False
    current = _head(integration)
    try:
        changed = set(
            _git_paths(
                integration,
                [
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "-z",
                    current,
                ],
                "inspect coordinator reconciliation paths",
            )
        )
        reconciled = {
            relative
            for relative in paths
            if not (integration / relative).is_file()
            or (integration / relative).is_symlink()
            or _digest((integration / relative).read_bytes()) != files[relative]
        }
        return (
            bool(reconciled)
            and _clean(integration)
            and _git_text(
                integration,
                ["rev-list", "--count", f"{integrated_head}..{current}"],
                "count coordinator reconciliation commits",
            )
            == "1"
            and _git_text(
                integration,
                ["rev-parse", f"{current}^"],
                "inspect coordinator reconciliation parent",
            )
            == integrated_head
            and changed <= _coordinator_reconciliation_paths(workspace)
            and reconciled <= changed
            and all(
                (integration / relative).is_file()
                and not (integration / relative).is_symlink()
                for relative in changed
            )
        )
    except PromptWorkspaceError:
        return False


def _integration_precommit_is_safe(integration: Path, paths: list[str]) -> bool:
    staged = _git_paths(
        integration,
        ["diff", "--cached", "--no-renames", "--name-only", "-z", "--"],
        "inspect staged integration paths",
    )
    unstaged = _git_paths(
        integration,
        ["diff", "--no-renames", "--name-only", "-z", "--"],
        "inspect integration paths",
    )
    untracked = _git_paths(
        integration,
        ["ls-files", "--others", "--exclude-standard", "-z", "--"],
        "inspect untracked integration paths",
    )
    deleted = _git_paths(
        integration,
        ["diff", "--no-renames", "--diff-filter=D", "--name-only", "-z", "--"],
        "inspect deleted integration paths",
    )
    allowed = set(paths)
    return (
        not deleted
        and set(staged) <= allowed
        and set(unstaged) <= allowed
        and set(untracked) <= allowed
    )


def _contract_commit_identity_valid(
    integration: Path,
    contract_head: str,
    journal: dict[str, object],
) -> bool:
    paths = list(journal["paths"])
    files = dict(journal["files_sha256"])
    try:
        parent = _git_text(
            integration,
            ["rev-parse", f"{contract_head}^"],
            "inspect contract commit parent",
        )
        message = _git_text(
            integration,
            ["show", "-s", "--format=%B", contract_head],
            "inspect contract commit message",
        )
        changed_paths = _git_paths(
            integration,
            [
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                contract_head,
            ],
            "inspect contract commit paths",
        )
        committed_files = {}
        for relative in paths:
            blob = _git(
                integration,
                ["show", f"{contract_head}:{relative}"],
                "inspect contract commit blob",
                check=False,
            )
            if blob.returncode != 0:
                return False
            committed_files[relative] = _digest(blob.stdout)
    except PromptWorkspaceError:
        return False
    return (
        parent == journal["integration_base"]
        and message == CONTRACT_DELTA_MESSAGE
        and changed_paths == paths
        and committed_files == files
    )


def _contract_commit_valid(
    integration: Path,
    contract_head: str,
    journal: dict[str, object],
) -> bool:
    paths = list(journal["paths"])
    files = dict(journal["files_sha256"])
    return (
        _contract_commit_identity_valid(integration, contract_head, journal)
        and _head(integration) == contract_head
        and _integration_contains(integration, paths, files)
    )


def _load_terminal_seal(
    run_dir: Path, wave_id: str, *, required: bool = False
) -> dict[str, object] | None:
    path = _terminal_seal_path(run_dir, wave_id)
    if path.is_symlink():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "terminal lifecycle seal path is unsafe"
        )
    if not path.exists():
        if required:
            raise PromptWorkspaceError(
                "LIFECYCLE_SEAL_REQUIRED",
                "the final promoted wave has no terminal lifecycle seal",
            )
        return None
    value = load_json_object(path, "terminal lifecycle seal")
    required_fields = {
        "schema",
        "run_id",
        "wave_id",
        "lane_base",
        "promotion_base",
        "integration_base",
        "contract_head",
        "paths",
        "files_sha256",
        "contract_files_sha256",
        "lifecycle_receipt_sha256",
        "lifecycle_state_sha256",
        "project_instructions_state_sha256",
        "phase",
        "promotion_target",
        "created_at",
    }
    paths = value.get("paths")
    changed_files = value.get("files_sha256")
    contract_files = value.get("contract_files_sha256")
    phase = value.get("phase")
    contract_head = value.get("contract_head")
    promotion_target = value.get("promotion_target")
    if (
        set(value) != required_fields
        or value.get("schema") != TERMINAL_SEAL_SCHEMA
        or not isinstance(value.get("run_id"), str)
        or not isinstance(value.get("wave_id"), str)
        or any(
            not isinstance(value.get(key), str)
            or SHA_RE.fullmatch(str(value[key])) is None
            for key in ("lane_base", "promotion_base", "integration_base")
        )
        or value.get("promotion_base") != value.get("integration_base")
        or not isinstance(paths, list)
        or any(not isinstance(item, str) or not item for item in paths)
        or paths != sorted(set(paths))
        or not isinstance(changed_files, dict)
        or set(changed_files) != set(paths)
        or any(
            not isinstance(item, str) or SHA256_RE.fullmatch(item) is None
            for item in changed_files.values()
        )
        or not isinstance(contract_files, dict)
        or not {key for key in contract_files if key.endswith("docs/requirements.md")}
        or not {key for key in contract_files if key.endswith("docs/design.md")}
        or any(
            item is not None
            and (not isinstance(item, str) or SHA256_RE.fullmatch(item) is None)
            for item in contract_files.values()
        )
        or any(
            not isinstance(value.get(key), str)
            or SHA256_RE.fullmatch(str(value[key])) is None
            for key in (
                "lifecycle_receipt_sha256",
                "lifecycle_state_sha256",
                "project_instructions_state_sha256",
            )
        )
        or phase
        not in {
            "intent",
            "sealed",
            "integration-committed",
            "promotion-clearing",
            "lane-cleared",
            "promoted",
        }
        or (
            contract_head is not None
            and (
                not isinstance(contract_head, str)
                or SHA_RE.fullmatch(contract_head) is None
            )
        )
        or (phase != "intent" and contract_head is None)
        or (not paths and phase not in {"sealed"})
        or (not paths and contract_head != value.get("promotion_base"))
        or (paths and phase == "sealed")
        or (
            phase in {"intent", "sealed", "integration-committed"}
            and promotion_target is not None
        )
        or (
            phase in {"promotion-clearing", "lane-cleared", "promoted"}
            and (
                not isinstance(promotion_target, str)
                or SHA_RE.fullmatch(promotion_target) is None
                or promotion_target != contract_head
            )
        )
        or not isinstance(value.get("created_at"), str)
        or not value["created_at"]
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "terminal lifecycle seal is invalid"
        )
    return value


def terminal_lifecycle_seal(
    run_dir: Path, wave_id: str, *, required: bool = False
) -> dict[str, object] | None:
    """Expose the validated terminal lifecycle receipt to owner transitions."""

    return _load_terminal_seal(run_dir, wave_id, required=required)


def _load_terminal_recovery(
    run_dir: Path, *, required: bool = False
) -> dict[str, object] | None:
    path = _terminal_recovery_path(run_dir)
    if path.is_symlink():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "terminal lifecycle recovery path is unsafe"
        )
    if not path.exists():
        if required:
            raise PromptWorkspaceError(
                "LIFECYCLE_SEAL_REQUIRED",
                "the released run has no supplemental lifecycle recovery",
            )
        return None
    value = load_json_object(path, "terminal lifecycle recovery")
    required_fields = {
        "schema",
        "run_id",
        "wave_id",
        "original_generation",
        "original_promoted_head",
        "paths",
        "files_sha256",
        "contract_files_sha256",
        "lifecycle_receipt_sha256",
        "lifecycle_state_sha256",
        "project_instructions_state_sha256",
        "phase",
        "supplemental_generation",
        "supplemental_head",
        "created_at",
    }
    paths = value.get("paths")
    files = value.get("files_sha256")
    contract_files = value.get("contract_files_sha256")
    phase = value.get("phase")
    generation = value.get("supplemental_generation")
    head = value.get("supplemental_head")
    if (
        set(value) != required_fields
        or value.get("schema") != TERMINAL_RECOVERY_SCHEMA
        or value.get("run_id") != run_dir.name
        or not isinstance(value.get("wave_id"), str)
        or not isinstance(value.get("original_generation"), int)
        or int(value["original_generation"]) < 1
        or not isinstance(value.get("original_promoted_head"), str)
        or SHA_RE.fullmatch(str(value["original_promoted_head"])) is None
        or not isinstance(paths, list)
        or any(not isinstance(item, str) or not item for item in paths)
        or paths != sorted(set(paths))
        or not isinstance(files, dict)
        or set(files) != set(paths)
        or any(
            not isinstance(item, str) or SHA256_RE.fullmatch(item) is None
            for item in files.values()
        )
        or not isinstance(contract_files, dict)
        or any(
            item is not None
            and (not isinstance(item, str) or SHA256_RE.fullmatch(item) is None)
            for item in contract_files.values()
        )
        or any(
            not isinstance(value.get(key), str)
            or SHA256_RE.fullmatch(str(value[key])) is None
            for key in (
                "lifecycle_receipt_sha256",
                "lifecycle_state_sha256",
                "project_instructions_state_sha256",
            )
        )
        or phase not in {"intent", "released"}
        or (phase == "intent" and (generation is not None or head is not None))
        or (
            phase == "released"
            and (
                not isinstance(generation, int)
                or generation <= int(value["original_generation"])
                or not isinstance(head, str)
                or SHA_RE.fullmatch(head) is None
            )
        )
        or not isinstance(value.get("created_at"), str)
        or not value["created_at"]
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "terminal lifecycle recovery is invalid"
        )
    return value


def terminal_lifecycle_recovery(
    run_dir: Path, *, required: bool = False
) -> dict[str, object] | None:
    """Expose a validated supplemental-generation recovery receipt."""

    return _load_terminal_recovery(run_dir, required=required)


def _is_final_active_wave(
    coordinator: dict[str, object], wave: dict[str, object]
) -> bool:
    indexed = [
        item.get("wave_id")
        for item in coordinator.get("waves", [])
        if isinstance(item, dict)
    ]
    return (
        bool(indexed)
        and indexed[-1] == wave.get("wave_id")
        and coordinator.get("active_wave") == wave.get("wave_id")
    )


def _contract_files_match(root: Path, contract_files: dict[str, str | None]) -> bool:
    for relative, expected in contract_files.items():
        path = root / relative
        if expected is None:
            if path.exists() or path.is_symlink():
                return False
            continue
        if (
            path.is_symlink()
            or not path.is_file()
            or _digest(path.read_bytes()) != expected
        ):
            return False
    return True


def _terminal_commit_identity_valid(
    integration: Path, journal: dict[str, object]
) -> bool:
    contract_head = journal.get("contract_head")
    if not isinstance(contract_head, str):
        return False
    paths = list(journal["paths"])
    files = dict(journal["files_sha256"])
    try:
        parent = _git_text(
            integration,
            ["rev-parse", f"{contract_head}^"],
            "inspect terminal seal parent",
        )
        message = _git_text(
            integration,
            ["show", "-s", "--format=%B", contract_head],
            "inspect terminal seal message",
        )
        changed_paths = _git_paths(
            integration,
            [
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                contract_head,
            ],
            "inspect terminal seal paths",
        )
        committed_files: dict[str, str] = {}
        for relative in paths:
            blob = _git(
                integration,
                ["show", f"{contract_head}:{relative}"],
                "inspect terminal seal blob",
                check=False,
            )
            if blob.returncode != 0:
                return False
            committed_files[relative] = _digest(blob.stdout)
    except PromptWorkspaceError:
        return False
    return (
        parent == journal.get("integration_base")
        and message == TERMINAL_SEAL_MESSAGE
        and changed_paths == paths
        and committed_files == files
    )


def _terminal_identity_matches(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
    journal: dict[str, object],
    *,
    before_promotion: bool,
) -> bool:
    indexed = [
        item.get("wave_id")
        for item in coordinator.get("waves", [])
        if isinstance(item, dict)
    ]
    active_matches = coordinator.get("active_wave") == wave.get("wave_id")
    if not before_promotion and coordinator.get("status") == "done":
        active_matches = coordinator.get("active_wave") is None
    if (
        journal.get("run_id") != run_dir.name
        or journal.get("wave_id") != wave.get("wave_id")
        or journal.get("lane_base") != wave.get("base_commit")
        or not indexed
        or indexed[-1] != wave.get("wave_id")
        or not active_matches
    ):
        return False
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    contract_files = dict(journal["contract_files_sha256"])
    allowed = _contract_paths(workspace)
    expected_head = (
        journal.get("promotion_base")
        if before_promotion
        else journal.get("contract_head")
    )
    return (
        _branch(repo) == coordinator.get("base_branch")
        and set(contract_files) == set(allowed)
        and set(journal["paths"]) <= set(allowed)
        and _head(repo) == expected_head
        and _contract_files_match(repo, contract_files)
    )


def terminal_lifecycle_seal_active(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> bool:
    """Prove the final promoted wave retains an exact terminal seal."""

    journal = _load_terminal_seal(run_dir, str(wave.get("wave_id")))
    if journal is None or wave.get("status") != "promoted":
        return False
    paths = list(journal["paths"])
    if journal.get("phase") not in {"sealed", "integration-committed"}:
        return False
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    integration = Path(str(wave.get("integration_worktree")))
    try:
        lane_paths, lane_files = _validate_lane_delta(
            workspace, paths, allow_empty=True
        )
    except PromptWorkspaceError:
        return False
    return (
        _terminal_identity_matches(
            workspace,
            run_dir,
            coordinator,
            wave,
            journal,
            before_promotion=True,
        )
        and lane_paths == paths
        and lane_files == journal.get("files_sha256")
        and integration.is_dir()
        and not integration.is_symlink()
        and _linked_worktree_identity(repo, integration, wave.get("integration_branch"))
        and _branch(integration) == wave.get("integration_branch")
        and _head(integration) == journal.get("contract_head")
        and _clean(integration)
        and _contract_files_match(integration, dict(journal["contract_files_sha256"]))
        and (not paths or _terminal_commit_identity_valid(integration, journal))
    )


def terminal_lifecycle_seal_promoted(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> bool:
    """Prove the exact terminal seal reached the clean persistent lane."""

    journal = _load_terminal_seal(run_dir, str(wave.get("wave_id")))
    if journal is None:
        return False
    paths = list(journal["paths"])
    phase = journal.get("phase")
    expected_phase = "promoted" if paths else "sealed"
    return (
        phase == expected_phase
        and wave.get("promoted_head") == journal.get("contract_head")
        and _terminal_identity_matches(
            workspace,
            run_dir,
            coordinator,
            wave,
            journal,
            before_promotion=False,
        )
        and _clean(Path(required_string(workspace, "repo_root", "workspace manifest")))
    )


def _adopt_terminal_lifecycle_seal(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
    lifecycle_state: Path,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    if wave.get("status") != "promoted" or not _is_final_active_wave(coordinator, wave):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "terminal lifecycle seal requires the final promoted wave",
        )
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    integration = Path(str(wave.get("integration_worktree")))
    promotion_base = wave.get("promoted_head")
    if (
        not isinstance(promotion_base, str)
        or SHA_RE.fullmatch(promotion_base) is None
        or integration.is_symlink()
        or not integration.is_dir()
        or _branch(repo) != coordinator.get("base_branch")
        or _head(repo) != promotion_base
        or _branch(integration) != wave.get("integration_branch")
        or _head(integration) != promotion_base
        or not _clean(integration)
        or not _linked_worktree_identity(
            repo, integration, wave.get("integration_branch")
        )
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "terminal lifecycle Git identity changed"
        )
    paths, files = _validate_lane_delta(workspace, allow_empty=True)
    lifecycle, lifecycle_state_sha256, contract_files = _validated_lifecycle_contract(
        workspace,
        lifecycle_state,
        str(wave["base_commit"]),
        paths,
        files,
        lifecycle_head_range=(str(coordinator["initial_head"]), promotion_base),
    )
    journal = _load_terminal_seal(run_dir, str(wave.get("wave_id")))
    if journal is None:
        instruction_state_sha256 = lifecycle.get("project_instructions_state_sha256")
        journal = {
            "schema": TERMINAL_SEAL_SCHEMA,
            "run_id": run_dir.name,
            "wave_id": wave["wave_id"],
            "lane_base": wave["base_commit"],
            "promotion_base": promotion_base,
            "integration_base": promotion_base,
            "contract_head": promotion_base if not paths else None,
            "paths": paths,
            "files_sha256": files,
            "contract_files_sha256": contract_files,
            "lifecycle_receipt_sha256": lifecycle["receipt_sha256"],
            "lifecycle_state_sha256": lifecycle_state_sha256,
            "project_instructions_state_sha256": instruction_state_sha256,
            "phase": "sealed" if not paths else "intent",
            "promotion_target": None,
            "created_at": iso_seconds(clock()),
        }
        path = _terminal_seal_path(run_dir, str(wave["wave_id"]))
        ensure_private_dir(path.parent)
        write_exclusive(path, stable_json(journal))
    elif (
        journal.get("wave_id") != wave.get("wave_id")
        or journal.get("lane_base") != wave.get("base_commit")
        or journal.get("promotion_base") != promotion_base
        or journal.get("paths") != paths
        or journal.get("files_sha256") != files
        or journal.get("contract_files_sha256") != contract_files
        or journal.get("lifecycle_state_sha256") != lifecycle_state_sha256
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "terminal lifecycle seal identity changed"
        )
    if paths and journal.get("phase") == "intent":
        contract_head = journal.get("contract_head")
        current_head = _head(integration)
        if contract_head is None and current_head == promotion_base:
            source_paths = _contract_paths(workspace)
            for relative in paths:
                source = source_paths[relative]
                target = integration / relative
                _write_repo_file(
                    target,
                    source.read_bytes(),
                    stat.S_IMODE(source.stat().st_mode),
                )
            _git(integration, ["add", "--", *paths], "stage terminal lifecycle seal")
            _git(
                integration,
                [
                    "-c",
                    "commit.gpgsign=false",
                    "commit",
                    "-m",
                    TERMINAL_SEAL_MESSAGE,
                    "--",
                    *paths,
                ],
                "commit terminal lifecycle seal",
            )
            contract_head = _head(integration)
        elif contract_head is None:
            contract_head = current_head
        journal["contract_head"] = contract_head
        if not _terminal_commit_identity_valid(integration, journal):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "committed terminal lifecycle seal is inconsistent"
            )
        journal["phase"] = "integration-committed"
        write_atomic(
            _terminal_seal_path(run_dir, str(wave["wave_id"])), stable_json(journal)
        )
    loaded = _load_terminal_seal(run_dir, str(wave["wave_id"]), required=True)
    assert loaded is not None
    if not terminal_lifecycle_seal_active(workspace, run_dir, coordinator, wave):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "terminal lifecycle seal is no longer exact"
        )
    return {
        "status": "terminal-sealed",
        "run_id": run_dir.name,
        "wave_id": wave["wave_id"],
        "contract_head": loaded["contract_head"],
        "paths": list(loaded["paths"]),
        "lane_state_changed": False,
    }


def terminal_lifecycle_recovery_promoted(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> bool:
    """Prove a supplemental generation repaired an already-released run."""

    receipt = _load_terminal_recovery(run_dir)
    if receipt is None or receipt.get("phase") != "released":
        return False
    if (
        coordinator.get("status") != "done"
        or coordinator.get("active_wave") is not None
        or not coordinator.get("waves")
        or coordinator["waves"][-1].get("wave_id") != wave.get("wave_id")
        or receipt.get("wave_id") != wave.get("wave_id")
        or receipt.get("original_promoted_head") != wave.get("promoted_head")
    ):
        return False
    recovery_run = _terminal_recovery_run_dir(run_dir)
    interop = load_interop(recovery_run, required=False)
    checkpoint = load_checkpoint_receipt(recovery_run, required=False)
    if interop is None or checkpoint is None:
        return False
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    try:
        observed = observe_managed_state(workspace, recovery_run, interop)
    except PromptWorkspaceError:
        return False
    lease = dict(observed["lease"])
    return (
        interop.get("released") is True
        and interop.get("generation") == receipt.get("supplemental_generation")
        and interop.get("promoted_head") == receipt.get("supplemental_head")
        and checkpoint.get("before_head") == receipt.get("original_promoted_head")
        and checkpoint.get("initial_head") == receipt.get("supplemental_head")
        and checkpoint.get("paths") == receipt.get("paths")
        and lease.get("state") == "released"
        and lease.get("outer_clean") is True
        and _branch(repo) == coordinator.get("base_branch")
        and _head(repo) == receipt.get("supplemental_head")
        and _clean(repo)
        and set(receipt["contract_files_sha256"]) == set(_contract_paths(workspace))
        and _contract_files_match(repo, dict(receipt["contract_files_sha256"]))
    )


def _recover_released_terminal_lifecycle(
    manifest_path: Path,
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    lifecycle_state: Path,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    if (
        coordinator.get("status") != "done"
        or coordinator.get("active_wave") is not None
        or not coordinator.get("waves")
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "supplemental lifecycle recovery requires a completed coordinator",
        )
    wave = _load_wave(run_dir, str(coordinator["waves"][-1]["wave_id"]))
    if wave.get("status") != "done" or wave.get("cleanup_retained"):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "supplemental lifecycle recovery requires cleaned terminal resources",
        )
    original = load_interop(run_dir)
    assert original is not None
    original_head = wave.get("promoted_head")
    if (
        original.get("released") is not True
        or original.get("promoted_head") != original_head
        or not isinstance(original_head, str)
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "supplemental lifecycle recovery requires the exact released generation",
        )
    original_observation = observe_managed_state(
        workspace, run_dir, original, allow_outer_dirty=True
    )
    resources = original_observation["lease"].get("resources")
    if not isinstance(resources, list) or any(
        not isinstance(item, dict) or item.get("state") != "absent"
        for item in resources
    ):
        raise PromptWorkspaceError(
            "CLEANUP_BLOCKED", "released generation resources are not fully absent"
        )
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    receipt = _load_terminal_recovery(run_dir)
    if receipt is None:
        if (
            _branch(repo) != coordinator.get("base_branch")
            or _head(repo) != original_head
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "released lane is not at its original promoted head",
            )
        paths, files = _validate_lane_delta(workspace, allow_empty=True)
        lifecycle, lifecycle_state_sha256, contract_files = (
            _validated_lifecycle_contract(
                workspace,
                lifecycle_state,
                str(wave["base_commit"]),
                paths,
                files,
                lifecycle_head_range=(str(coordinator["initial_head"]), original_head),
            )
        )
        receipt = {
            "schema": TERMINAL_RECOVERY_SCHEMA,
            "run_id": run_dir.name,
            "wave_id": wave["wave_id"],
            "original_generation": original["generation"],
            "original_promoted_head": original_head,
            "paths": paths,
            "files_sha256": files,
            "contract_files_sha256": contract_files,
            "lifecycle_receipt_sha256": lifecycle["receipt_sha256"],
            "lifecycle_state_sha256": lifecycle_state_sha256,
            "project_instructions_state_sha256": lifecycle[
                "project_instructions_state_sha256"
            ],
            "phase": "intent",
            "supplemental_generation": None,
            "supplemental_head": None,
            "created_at": iso_seconds(clock()),
        }
        root = _terminal_recovery_root(run_dir)
        ensure_private_dir(root)
        write_exclusive(_terminal_recovery_path(run_dir), stable_json(receipt))
    else:
        lifecycle, lifecycle_state_sha256, contract_files = (
            _validated_lifecycle_contract(
                workspace,
                lifecycle_state,
                str(wave["base_commit"]),
                [],
                {},
                lifecycle_head_range=(str(coordinator["initial_head"]), original_head),
            )
        )
        if (
            receipt.get("wave_id") != wave.get("wave_id")
            or receipt.get("original_generation") != original.get("generation")
            or receipt.get("original_promoted_head") != original_head
            or receipt.get("contract_files_sha256") != contract_files
            or receipt.get("lifecycle_receipt_sha256")
            != lifecycle.get("receipt_sha256")
            or receipt.get("lifecycle_state_sha256") != lifecycle_state_sha256
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "supplemental lifecycle recovery identity changed",
            )
    if receipt.get("phase") == "released":
        if not terminal_lifecycle_recovery_promoted(
            workspace, run_dir, coordinator, wave
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "supplemental lifecycle recovery receipt is inconsistent",
            )
        return {
            "status": "terminal-recovered",
            "run_id": run_dir.name,
            "generation": receipt["supplemental_generation"],
            "promoted_head": receipt["supplemental_head"],
            "paths": list(receipt["paths"]),
        }

    recovery_run = _terminal_recovery_run_dir(run_dir)
    ensure_private_dir(recovery_run.parent)
    ensure_private_dir(recovery_run)
    claims = [{"kind": "exact", "path": relative} for relative in receipt["paths"]]
    supplemental = load_interop(recovery_run, required=False)
    if supplemental is None:
        prepare_checkpoint(
            workspace,
            recovery_run,
            manifest_path,
            original_head,
            claims,
        )
        supplemental = acquire_interop(
            workspace,
            recovery_run,
            manifest_path,
            original_head,
            claims,
        )
    checkpoint = load_checkpoint_receipt(recovery_run)
    assert checkpoint is not None and supplemental is not None
    if (
        checkpoint.get("before_head") != original_head
        or checkpoint.get("paths") != receipt.get("paths")
        or _head(repo) != checkpoint.get("initial_head")
        or not _clean(repo)
        or not _contract_files_match(repo, dict(receipt["contract_files_sha256"]))
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "supplemental lifecycle checkpoint is inconsistent"
        )
    supplemental_head = str(checkpoint["initial_head"])
    if supplemental.get("promoted_head") != supplemental_head:
        record_promotion(workspace, recovery_run, supplemental_head)
        supplemental = load_interop(recovery_run)
        assert supplemental is not None
    release_interop(workspace, recovery_run, supplemental_head)
    supplemental = load_interop(recovery_run)
    assert supplemental is not None
    receipt["phase"] = "released"
    receipt["supplemental_generation"] = supplemental["generation"]
    receipt["supplemental_head"] = supplemental_head
    write_atomic(_terminal_recovery_path(run_dir), stable_json(receipt))
    if not terminal_lifecycle_recovery_promoted(workspace, run_dir, coordinator, wave):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "supplemental lifecycle recovery did not seal"
        )
    return {
        "status": "terminal-recovered",
        "run_id": run_dir.name,
        "generation": supplemental["generation"],
        "promoted_head": supplemental_head,
        "paths": list(receipt["paths"]),
    }


def adopt_contract_delta(
    manifest_path: Path,
    run_id: str,
    lifecycle_state: Path,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Commit one sealed selected-lane contract overlay into its integration."""

    workspace = verify_workspace(manifest_path)
    run_dir = _run_dir(workspace, run_id)
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    with scope_lock(runs_root.parent):
        coordinator = load_coordinator_state(run_dir)
        if coordinator is not None and coordinator.get("status") == "done":
            return _recover_released_terminal_lifecycle(
                manifest_path,
                workspace,
                run_dir,
                coordinator,
                lifecycle_state,
                clock,
            )
        if coordinator is None or coordinator.get("status") != "running":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "run has no active coordinator"
            )
        wave_id = coordinator.get("active_wave")
        if not isinstance(wave_id, str):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "run has no active wave"
            )
        wave = _load_wave(run_dir, wave_id)
        if wave.get("status") == "promoted":
            return _adopt_terminal_lifecycle_seal(
                workspace,
                run_dir,
                coordinator,
                wave,
                lifecycle_state,
                clock,
            )
        if wave.get("status") != "promotion_pending":
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "contract delta requires a retained promotion review",
            )
        repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
        integration = Path(str(wave.get("integration_worktree")))
        if (
            repo.is_symlink()
            or integration.is_symlink()
            or not integration.is_dir()
            or _branch(repo) != coordinator.get("base_branch")
            or _head(repo) != wave.get("base_commit")
            or _branch(integration) != wave.get("integration_branch")
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "contract-delta Git identity changed"
            )
        verify_prompt_impact_plan(
            run_dir,
            coordinator,
            Path(required_string(workspace, "source_root", "workspace manifest")),
        )
        impact_value = load_current_prompt_impact(run_dir, required=True)
        assert impact_value is not None
        impact, impact_sha256 = impact_value
        if impact.get("plan_action") != "retain_plan":
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED", "material impact cannot use contract-delta adoption"
            )

        journal = _load_journal(run_dir)
        if journal is None:
            paths, files = _validate_lane_delta(workspace)
            lifecycle, lifecycle_state_sha256 = _validated_lifecycle(
                workspace, lifecycle_state, str(wave["base_commit"]), paths, files
            )
            integration_base = str(wave.get("integrated_head"))
            if _head(integration) != integration_base or not _clean(integration):
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT", "retained integration is not clean and sealed"
                )
            journal = {
                "schema": CONTRACT_DELTA_SCHEMA,
                "run_id": run_id,
                "wave_id": wave_id,
                "lane_head": wave["base_commit"],
                "integration_base": integration_base,
                "contract_head": None,
                "paths": paths,
                "files_sha256": files,
                "lifecycle_receipt_sha256": lifecycle["receipt_sha256"],
                "lifecycle_state_sha256": lifecycle_state_sha256,
                "impact_sha256": impact_sha256,
                "phase": "intent",
                "promotion_target": None,
                "created_at": iso_seconds(clock()),
            }
            write_exclusive(_journal_path(run_dir), stable_json(journal))
        else:
            if (
                journal.get("wave_id") != wave_id
                or journal.get("lane_head") != wave.get("base_commit")
                or journal.get("impact_sha256") != impact_sha256
            ):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "contract-delta identity changed"
                )
            _validate_lane_delta(workspace, list(journal["paths"]))
            if journal["phase"] in {
                "promotion-clearing",
                "lane-cleared",
                "promoted",
            }:
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "contract-delta adoption already advanced",
                )

        paths = list(journal["paths"])
        files = dict(journal["files_sha256"])
        contract_head = journal.get("contract_head")
        if contract_head is None:
            current_head = _head(integration)
            if current_head == journal["integration_base"]:
                if not _integration_precommit_is_safe(integration, paths):
                    raise PromptWorkspaceError(
                        "WORKTREE_CONFLICT",
                        "retained integration changed during contract adoption",
                    )
                source_paths = _contract_paths(workspace)
                for relative in paths:
                    source = source_paths[relative]
                    target = integration / relative
                    mode = stat.S_IMODE(source.stat().st_mode)
                    _write_repo_file(target, source.read_bytes(), mode)
                _git(
                    integration,
                    ["add", "--", *paths],
                    "stage sealed contract delta",
                )
                _git(
                    integration,
                    [
                        "-c",
                        "commit.gpgsign=false",
                        "commit",
                        "-m",
                        CONTRACT_DELTA_MESSAGE,
                        "--",
                        *paths,
                    ],
                    "commit sealed contract delta",
                )
                contract_head = _head(integration)
            else:
                contract_head = current_head
            if not _contract_commit_valid(integration, str(contract_head), journal):
                raise PromptWorkspaceError(
                    "WORKTREE_CONFLICT", "committed contract delta is inconsistent"
                )
            journal["contract_head"] = contract_head
            journal["phase"] = "integration-committed"
            write_atomic(_journal_path(run_dir), stable_json(journal))
        if not _contract_commit_valid(integration, str(contract_head), journal):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "committed contract delta is inconsistent"
            )
        lane_paths, lane_files = _validate_lane_delta(workspace, paths)
        if lane_paths != paths or lane_files != files:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "selected-lane contract bytes changed"
            )
        return {
            "status": "adopted",
            "run_id": run_id,
            "wave_id": wave_id,
            "contract_head": contract_head,
            "paths": paths,
            "lane_state_changed": False,
        }


def _active_contract_delta_journal(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> dict[str, object] | None:
    """Return the journal only while its retained correction remains attested."""

    journal = _load_journal(run_dir)
    if journal is None or journal.get("phase") != "integration-committed":
        return None
    if (
        journal.get("wave_id") != wave.get("wave_id")
        or journal.get("lane_head") != wave.get("base_commit")
        or coordinator.get("active_wave") != wave.get("wave_id")
    ):
        return None
    impact_value = load_current_prompt_impact(run_dir, required=True)
    assert impact_value is not None
    if impact_value[1] != journal.get("impact_sha256"):
        return None
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    integration = Path(str(wave.get("integration_worktree")))
    try:
        paths, files = _validate_lane_delta(workspace, list(journal["paths"]))
    except PromptWorkspaceError:
        return None
    contract_head = str(journal.get("contract_head"))
    active = (
        _branch(repo) == coordinator.get("base_branch")
        and _head(repo) == journal.get("lane_head")
        and files == journal.get("files_sha256")
        and paths == journal.get("paths")
        and integration.is_dir()
        and not integration.is_symlink()
        and _linked_worktree_identity(repo, integration, wave.get("integration_branch"))
        and _branch(integration) == wave.get("integration_branch")
        and _contract_commit_identity_valid(integration, contract_head, journal)
        and _git(
            integration,
            ["merge-base", "--is-ancestor", contract_head, "HEAD"],
            "verify contract integration ancestry",
            check=False,
        ).returncode
        == 0
        and _integration_preserves_adopted_contract(
            workspace, integration, wave, paths, files
        )
    )
    return journal if active else None


def contract_delta_active(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> bool:
    """Prove the only dirty-lane shape admitted during retained corrections."""

    return (
        _active_contract_delta_journal(workspace, run_dir, coordinator, wave)
        is not None
    )


def active_contract_delta_head(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> str | None:
    """Return the exact adopted head while the owner journal remains active."""

    journal = _active_contract_delta_journal(workspace, run_dir, coordinator, wave)
    if journal is None:
        return None
    return str(journal["contract_head"])


def _journal_identity_matches(
    journal: dict[str, object],
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> bool:
    return (
        journal.get("wave_id") == wave.get("wave_id")
        and journal.get("lane_head") == wave.get("base_commit")
        and coordinator.get("active_wave") == wave.get("wave_id")
    )


def _completed_journal_precedes_active_wave(
    workspace: dict[str, object],
    run_dir: Path,
    journal: dict[str, object],
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> bool:
    """Prove a promoted adoption journal belongs to an earlier completed wave."""

    journal_wave_id = journal.get("wave_id")
    active_wave_id = wave.get("wave_id")
    target = journal.get("promotion_target")
    active_base = wave.get("base_commit")
    wave_ids = [
        item.get("wave_id")
        for item in coordinator.get("waves", [])
        if isinstance(item, dict)
    ]
    if (
        journal.get("phase") != "promoted"
        or not isinstance(journal_wave_id, str)
        or not isinstance(active_wave_id, str)
        or journal_wave_id == active_wave_id
        or coordinator.get("active_wave") != active_wave_id
        or not isinstance(target, str)
        or SHA_RE.fullmatch(target) is None
        or not isinstance(active_base, str)
        or SHA_RE.fullmatch(active_base) is None
        or journal_wave_id not in wave_ids
        or active_wave_id not in wave_ids
        or wave_ids.index(journal_wave_id) >= wave_ids.index(active_wave_id)
    ):
        return False
    completed_wave = _load_wave(run_dir, journal_wave_id)
    if (
        completed_wave.get("status") != "done"
        or completed_wave.get("promoted_head") != target
    ):
        return False
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    return (
        _git(
            repo,
            ["merge-base", "--is-ancestor", target, active_base],
            "verify completed contract promotion ancestry",
            check=False,
        ).returncode
        == 0
    )


def _restore_lane_overlay(
    workspace: dict[str, object],
    integration: Path,
    journal: dict[str, object],
) -> None:
    paths = list(journal["paths"])
    files = dict(journal["files_sha256"])
    contract_head = str(journal["contract_head"])
    if not _contract_commit_identity_valid(integration, contract_head, journal):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "retained integration lost the sealed contract bytes"
        )
    lane_paths = _contract_paths(workspace)
    for relative in paths:
        source = _git(
            integration,
            ["show", f"{contract_head}:{relative}"],
            "read sealed contract bytes",
            check=False,
        )
        if source.returncode != 0 or _digest(source.stdout) != files[relative]:
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "retained integration lost the sealed contract bytes",
            )
        target = lane_paths[relative]
        if target.is_symlink() or not target.is_file():
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "sealed contract target is unsafe"
            )
        _write_repo_file(target, source.stdout, stat.S_IMODE(target.stat().st_mode))
    restored_paths, restored_files = _validate_lane_delta(workspace, paths)
    if restored_paths != paths or restored_files != files:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "sealed contract overlay could not be restored"
        )


def recover_contract_delta_promotion(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> bool:
    """Restore or complete an interrupted temporary lane-clean promotion phase."""

    journal = _load_journal(run_dir)
    if journal is None:
        return False
    if journal.get("wave_id") != wave.get(
        "wave_id"
    ) and _completed_journal_precedes_active_wave(
        workspace, run_dir, journal, coordinator, wave
    ):
        return False
    if not _journal_identity_matches(journal, coordinator, wave):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "contract-delta promotion identity changed"
        )
    phase = str(journal["phase"])
    if phase in {"intent", "integration-committed"}:
        return False
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    integration = Path(str(wave.get("integration_worktree")))
    if integration.is_symlink() or not integration.is_dir():
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "contract-delta integration disappeared"
        )
    target = journal.get("promotion_target")
    if not isinstance(target, str):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "contract-delta promotion target is missing"
        )
    lane_head = _head(repo)
    if lane_head == target and _clean(repo):
        journal["phase"] = "promoted"
        write_atomic(_journal_path(run_dir), stable_json(journal))
        return True
    if lane_head != journal["lane_head"]:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane changed during contract promotion"
        )
    if not _lane_delta_is_subset(workspace, list(journal["paths"])):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane contract bytes changed"
        )
    _restore_lane_overlay(workspace, integration, journal)
    journal["phase"] = "integration-committed"
    journal["promotion_target"] = None
    write_atomic(_journal_path(run_dir), stable_json(journal))
    return True


def prepare_contract_delta_promotion(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
    target: str,
) -> bool:
    """Journal and temporarily clear an exact adopted overlay for fast-forward."""

    if not contract_delta_active(workspace, run_dir, coordinator, wave):
        return False
    journal = _load_journal(run_dir, required=True)
    assert journal is not None
    integration = Path(str(wave["integration_worktree"]))
    if _head(integration) != target or not _integration_preserves_adopted_contract(
        workspace,
        integration,
        wave,
        list(journal["paths"]),
        dict(journal["files_sha256"]),
    ):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "promotion target lost the sealed contract bytes"
        )
    journal["phase"] = "promotion-clearing"
    journal["promotion_target"] = target
    write_atomic(_journal_path(run_dir), stable_json(journal))
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    try:
        _git(
            repo,
            ["restore", "--source=HEAD", "--worktree", "--", *journal["paths"]],
            "temporarily clear the sealed contract overlay",
        )
        if _head(repo) != journal["lane_head"] or not _clean(repo):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "persistent lane did not clear exactly for promotion",
            )
    except PromptWorkspaceError:
        recover_contract_delta_promotion(workspace, run_dir, coordinator, wave)
        raise
    journal["phase"] = "lane-cleared"
    write_atomic(_journal_path(run_dir), stable_json(journal))
    return True


def restore_contract_delta_after_failed_promotion(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> None:
    """Restore exact selected-lane bytes when fast-forward does not complete."""

    recover_contract_delta_promotion(workspace, run_dir, coordinator, wave)


def complete_contract_delta_promotion(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
    target: str,
) -> None:
    """Seal the adoption journal after the persistent lane reaches its target."""

    journal = _load_journal(run_dir)
    if journal is None:
        return
    if journal.get("wave_id") != wave.get(
        "wave_id"
    ) and _completed_journal_precedes_active_wave(
        workspace, run_dir, journal, coordinator, wave
    ):
        return
    if (
        journal.get("phase") not in {"lane-cleared", "promoted"}
        or journal.get("promotion_target") != target
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "contract-delta promotion journal is inconsistent",
        )
    journal["phase"] = "promoted"
    write_atomic(_journal_path(run_dir), stable_json(journal))


def _restore_terminal_lane_overlay(
    workspace: dict[str, object],
    integration: Path,
    journal: dict[str, object],
) -> None:
    if not _terminal_commit_identity_valid(integration, journal):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "retained integration lost the terminal seal bytes"
        )
    contract_head = str(journal["contract_head"])
    lane_paths = _contract_paths(workspace)
    for relative in journal["paths"]:
        source = _git(
            integration,
            ["show", f"{contract_head}:{relative}"],
            "read terminal seal bytes",
            check=False,
        )
        if (
            source.returncode != 0
            or _digest(source.stdout) != journal["files_sha256"][relative]
        ):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "retained integration lost the terminal seal bytes",
            )
        target = lane_paths[relative]
        if target.is_symlink() or not target.is_file():
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "terminal lifecycle target is unsafe"
            )
        _write_repo_file(target, source.stdout, stat.S_IMODE(target.stat().st_mode))
    restored_paths, restored_files = _validate_lane_delta(
        workspace, list(journal["paths"])
    )
    if restored_paths != journal["paths"] or restored_files != journal["files_sha256"]:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "terminal lifecycle overlay could not be restored"
        )


def recover_terminal_lifecycle_promotion(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> bool:
    """Restore or complete an interrupted terminal-seal fast-forward."""

    journal = _load_terminal_seal(run_dir, str(wave.get("wave_id")))
    if journal is None or not journal.get("paths"):
        return False
    if journal.get("wave_id") != wave.get("wave_id") or not _is_final_active_wave(
        coordinator, wave
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "terminal lifecycle promotion identity changed"
        )
    phase = str(journal["phase"])
    if phase in {"intent", "integration-committed", "promoted"}:
        return False
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    integration = Path(str(wave.get("integration_worktree")))
    if integration.is_symlink() or not integration.is_dir():
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "terminal lifecycle integration disappeared"
        )
    target = str(journal["contract_head"])
    lane_head = _head(repo)
    if lane_head == target and _clean(repo):
        journal["phase"] = "promoted"
        write_atomic(
            _terminal_seal_path(run_dir, str(wave["wave_id"])), stable_json(journal)
        )
        return True
    if lane_head != journal["promotion_base"]:
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane changed during terminal promotion"
        )
    if not _lane_delta_is_subset(workspace, list(journal["paths"])):
        raise PromptWorkspaceError(
            "WORKTREE_CONFLICT", "persistent lane terminal bytes changed"
        )
    _restore_terminal_lane_overlay(workspace, integration, journal)
    journal["phase"] = "integration-committed"
    journal["promotion_target"] = None
    write_atomic(
        _terminal_seal_path(run_dir, str(wave["wave_id"])), stable_json(journal)
    )
    return True


def prepare_terminal_lifecycle_promotion(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> dict[str, str] | None:
    """Journal and clear the exact final lifecycle overlay for promotion."""

    if not terminal_lifecycle_seal_active(workspace, run_dir, coordinator, wave):
        return None
    journal = _load_terminal_seal(run_dir, str(wave.get("wave_id")), required=True)
    assert journal is not None
    if not journal["paths"]:
        return None
    target = str(journal["contract_head"])
    journal["phase"] = "promotion-clearing"
    journal["promotion_target"] = target
    write_atomic(
        _terminal_seal_path(run_dir, str(wave["wave_id"])), stable_json(journal)
    )
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    try:
        _git(
            repo,
            ["restore", "--source=HEAD", "--worktree", "--", *journal["paths"]],
            "temporarily clear the terminal lifecycle overlay",
        )
        if _head(repo) != journal["promotion_base"] or not _clean(repo):
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT",
                "persistent lane did not clear exactly for terminal promotion",
            )
    except PromptWorkspaceError:
        recover_terminal_lifecycle_promotion(workspace, run_dir, coordinator, wave)
        raise
    journal["phase"] = "lane-cleared"
    write_atomic(
        _terminal_seal_path(run_dir, str(wave["wave_id"])), stable_json(journal)
    )
    return {"base": str(journal["promotion_base"]), "target": target}


def restore_terminal_lifecycle_after_failed_promotion(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    wave: dict[str, object],
) -> None:
    recover_terminal_lifecycle_promotion(workspace, run_dir, coordinator, wave)


def complete_terminal_lifecycle_promotion(
    run_dir: Path, wave: dict[str, object], target: str
) -> None:
    journal = _load_terminal_seal(run_dir, str(wave.get("wave_id")), required=True)
    assert journal is not None
    if (
        journal.get("wave_id") != wave.get("wave_id")
        or journal.get("phase") not in {"lane-cleared", "promoted"}
        or journal.get("promotion_target") != target
        or journal.get("contract_head") != target
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "terminal lifecycle promotion journal is inconsistent",
        )
    journal["phase"] = "promoted"
    write_atomic(
        _terminal_seal_path(run_dir, str(wave["wave_id"])), stable_json(journal)
    )


def contract_delta_journal(run_dir: Path) -> dict[str, object] | None:
    """Expose validated private adoption state to owning transition code."""

    return _load_journal(run_dir)
