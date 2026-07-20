#!/usr/bin/env python3
"""Private lifecycle helper for the Agentic SDLC three-tier live scenario.

This module does not run SDLC phases. The sdlc-workflow-test skill owns phase
orchestration; this helper owns fail-closed local state, sanitized reporting,
and exact cleanup of resources recorded by a successful ownership check.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

try:
    import fcntl
except ModuleNotFoundError:  # pragma: no cover - the live profile is POSIX-only
    fcntl = None  # type: ignore[assignment]

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import parse_qs, urlsplit
import uuid

import three_tier_browser
from three_tier_reporting import report_text
from three_tier_semantics import (
    REQUIRED_GUI_STEPS as REQUIRED_GUI_STEPS,
    REQUIRED_SDLC_PHASES,
    RESULTS_SCHEMA as RESULTS_SCHEMA,
    SCENARIO,
    SemanticEvidenceError,
    summarize_semantic_results,
    validate_semantic_results,
)


ROOT_MARKER = ".sdlc-workflow-test-root.json"
ROOT_SCHEMA = "agentic-sdlc/verification-root-v1"
LIFECYCLE_SCHEMA = "agentic-sdlc/three-tier-lifecycle-v3"
RUN_MARKER = ".agentic-sdlc-three-tier-run.json"
PROJECT_MARKER = ".sdlc-workflow-test-project.json"
OWNERSHIP_LABEL = "sdlc-workflow-test.verification-id"
COMPOSE_LABEL = "com.docker.compose.project"
PHASE_STATUSES = {"PASS", "PARTIAL", "FAIL", "NOT_RUN"}
FINAL_STATUSES = {"PASS", "PARTIAL", "FAIL"}
COMPUTER_USE_STAGES = {"capability-discovery", "evaluate-readiness", "uat-readiness"}
COMPUTER_USE_OUTCOMES = {"PASS", "ENVIRONMENT_DEFECT", "FAIL"}
PHASE_RESULT_SCHEMA = "agentic-sdlc/phase-result-v4"
ALLOWED_PHASES = set(REQUIRED_SDLC_PHASES) | {
    "sdlc-classify-failure",
    "sdlc-gui-test",
    "sdlc-tui-test",
}
PHASE_REQUIRED_ASSERTIONS = {
    phase: [f"{phase}:completed", "git-identity-bound"] for phase in ALLOWED_PHASES
}
RESOURCE_KINDS = ("containers", "networks", "volumes", "images")
GENERATION_GUARDED_COMMANDS = {
    "record-phase",
    "record-validation",
    "record-computer-use",
    "record-runtime",
    "launch-browser",
    "close-browser",
    "prepare-images",
    "record-browser",
    "record-git",
    "run-compose",
    "finish",
    "resume",
    "assert-active",
}
PUBLIC_BASE_IMAGES = (
    "python:3.12.10-slim-bookworm",
    "postgres:17.5-alpine",
)
COMPOSE_ACTIONS = {
    "build",
    "exec",
    "port",
    "ps",
    "restart",
    "start",
    "stop",
    "up",
}
SECRET_MATERIAL_PATTERNS = (
    r"(?i)(?:password|token|secret|api[_-]?key)"
    r"\s*[:=]\s*\S+",
    r"(?i)(?:^|\s)--?(?:password|token|secret|api[_-]?key)"
    r"(?:=|\s+)\S+",
    r"(?i)authorization\s*:\s*(?:bearer|basic)\s+\S+",
    r"(?i)(?:^|\s)(?:-u|--user)(?:=|\s+)\S+:\S+",
    r"(?i)://[^/\s:@]+:[^@\s/]+@",
)


class LifecycleError(RuntimeError):
    """A safe, user-facing lifecycle failure."""


def contains_secret_material(value: str) -> bool:
    return any(re.search(pattern, value) for pattern in SECRET_MATERIAL_PATTERNS)


def valid_git_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) in range(40, 65)
        and all(character in "0123456789abcdef" for character in value)
    )


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path.parent, 0o700)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LifecycleError(f"Lifecycle JSON must be a regular file: {path}")
    if path.stat().st_size > 1024 * 1024:
        raise LifecycleError(f"Lifecycle JSON exceeds the 1 MiB limit: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LifecycleError(f"Invalid lifecycle JSON at {path}: {error}") from error
    if not isinstance(value, dict):
        raise LifecycleError(f"Lifecycle JSON must be an object: {path}")
    return value


def reject_symlinks(path: Path, *, stop_at: Path | None = None) -> None:
    current = path.absolute()
    boundary = stop_at.absolute() if stop_at is not None else None
    while True:
        if current.is_symlink():
            raise LifecycleError(f"Symlinked lifecycle path is not allowed: {current}")
        if boundary is not None and current == boundary:
            return
        if current.parent == current:
            return
        current = current.parent


def verification_root(value: Path, *, create: bool) -> Path:
    requested = value.expanduser().absolute()
    if requested in {
        Path(requested.anchor),
        Path.home().absolute(),
        Path.cwd().absolute(),
    }:
        raise LifecycleError("Verification root is too broad for lifecycle ownership.")
    reject_symlinks(requested)
    if requested.exists() and not requested.is_dir():
        raise LifecycleError(f"Verification root is not a directory: {requested}")
    marker = requested / ROOT_MARKER
    if requested.exists():
        if not marker.is_file() or marker.is_symlink():
            raise LifecycleError(
                "Existing verification root is not owned by sdlc-workflow-test."
            )
        if read_json(marker) != {"schema": ROOT_SCHEMA}:
            raise LifecycleError("Verification-root ownership marker is invalid.")
    elif create:
        requested.mkdir(parents=True, mode=0o700)
        private_json(marker, {"schema": ROOT_SCHEMA})
    else:
        raise LifecycleError("NO_ACTIVE_THREE_TIER_APPLICATION")
    return requested.resolve(strict=True)


def paths(root: Path) -> dict[str, Path]:
    base = root / "three-tier-live"
    return {
        "base": base,
        "active": base / "active.json",
        "lock": base / ".lifecycle.lock",
        "runs": base / "runs",
        "reports": base / "reports",
        "archive": base / "lifecycle",
    }


@contextmanager
def lifecycle_lock(root: Path):
    """Serialize create replacement and standalone destroy for one owned root."""
    if fcntl is None:
        raise LifecycleError("Three-tier lifecycle locking requires a POSIX host.")
    lifecycle_paths = paths(root)
    base = lifecycle_paths["base"]
    reject_symlinks(base, stop_at=root)
    base.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock_path = lifecycle_paths["lock"]
    reject_symlinks(lock_path, stop_at=root)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise LifecycleError(f"Could not open the lifecycle lock: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise LifecycleError("Lifecycle lock must be one regular file.")
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def command(args: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LifecycleError(f"Could not run {args[0]}: {error}") from error


def require_command(args: list[str], label: str) -> str:
    result = command(args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise LifecycleError(f"{label} preflight failed: {detail or 'command failed'}")
    return result.stdout.strip()


def detect_browser() -> tuple[str, str]:
    executable = three_tier_browser.CHROME_EXECUTABLE
    if executable.is_file() and not executable.is_symlink():
        return "chrome", three_tier_browser.BROWSER_NAME
    raise LifecycleError("Google Chrome is not installed at the canonical path.")


def load_active(root: Path) -> tuple[Path, dict[str, Any]]:
    active_path = paths(root)["active"]
    if not active_path.exists():
        raise LifecycleError("NO_ACTIVE_THREE_TIER_APPLICATION")
    if active_path.is_symlink():
        raise LifecycleError("Active lifecycle pointer is symlinked.")
    state = read_json(active_path)
    validate_state(root, state)
    return active_path, state


def assert_active_generation(root: Path, expected_verification_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", expected_verification_id):
        raise LifecycleError("Expected verification ID is invalid.")
    _, state = load_active(root)
    if state["verification_id"] != expected_verification_id:
        raise LifecycleError(
            "STALE_THREE_TIER_GENERATION: this workflow was superseded by a "
            "newer --create invocation; stop without further mutation."
        )


def validate_state(root: Path, state: dict[str, Any]) -> None:
    if state.get("schema") != LIFECYCLE_SCHEMA or state.get("scenario") != SCENARIO:
        raise LifecycleError("Active three-tier lifecycle schema/profile is invalid.")
    verification_id = state.get("verification_id")
    if not isinstance(verification_id, str) or not re.fullmatch(
        r"[0-9a-f]{32}", verification_id
    ):
        raise LifecycleError("Lifecycle verification ID is invalid.")
    expected_run = (paths(root)["runs"] / verification_id).resolve(strict=False)
    recorded_run = Path(str(state.get("run_root", ""))).expanduser().absolute()
    if recorded_run != expected_run:
        raise LifecycleError("Lifecycle run root does not match the owned location.")
    expected_paths = {
        "verification_root": root,
        "project_root": expected_run / "project",
        "private_root": expected_run / "private",
        "evidence_root": expected_run / "evidence",
        "report_path": paths(root)["reports"] / verification_id / "report.md",
    }
    for key, expected in expected_paths.items():
        if Path(str(state.get(key, ""))).expanduser().absolute() != expected:
            raise LifecycleError(f"Lifecycle {key} does not match the owned location.")
        reject_symlinks(expected, stop_at=root)
    reject_symlinks(recorded_run, stop_at=root)
    marker = recorded_run / RUN_MARKER
    if not marker.is_file() or marker.is_symlink():
        raise LifecycleError("Owned three-tier run marker is missing.")
    expected_marker = {
        "schema": LIFECYCLE_SCHEMA,
        "verification_id": verification_id,
    }
    if read_json(marker) != expected_marker:
        raise LifecycleError("Owned three-tier run marker is invalid.")
    project_marker = expected_run / "project" / PROJECT_MARKER
    expected_project_marker = {
        "schema": LIFECYCLE_SCHEMA,
        "scenario": SCENARIO,
        "verification_id": verification_id,
    }
    reject_symlinks(project_marker, stop_at=recorded_run)
    if not project_marker.is_file() or project_marker.is_symlink():
        raise LifecycleError("Owned three-tier project marker is missing.")
    if read_json(project_marker) != expected_project_marker:
        raise LifecycleError("Owned three-tier project marker is invalid.")
    compose_project = state.get("compose_project")
    if not isinstance(compose_project, str) or compose_project != (
        f"sdlc-workflow-test-{verification_id[:12]}"
    ):
        raise LifecycleError("Compose project ownership identity is invalid.")
    resources = state.get("resources")
    if not isinstance(resources, dict) or set(resources) != set(RESOURCE_KINDS):
        raise LifecycleError("Lifecycle Docker inventory shape is invalid.")
    for kind, identifiers in resources.items():
        if (
            not isinstance(identifiers, list)
            or any(
                not isinstance(identifier, str)
                or not resource_identifier_valid(identifier)
                for identifier in identifiers
            )
            or len(set(identifiers)) != len(identifiers)
        ):
            raise LifecycleError(f"Lifecycle {kind} inventory is invalid.")
    browser_tab = state.get("browser_tab")
    if not isinstance(browser_tab, dict) or set(browser_tab) != {
        "title",
        "url",
        "closed",
    }:
        raise LifecycleError("Lifecycle browser-tab identity is invalid.")
    if not isinstance(browser_tab["closed"], bool):
        raise LifecycleError("Lifecycle browser-tab closed state is invalid.")
    try:
        three_tier_browser.validate_state(
            state.get("browser_instance"), verification_id
        )
    except three_tier_browser.BrowserOwnershipError as error:
        raise LifecycleError(str(error)) from error
    validations = state.get("validations", [])
    if not isinstance(validations, list) or any(
        not isinstance(validation, dict)
        or set(validation) != {"command", "status", "summary", "recorded_at"}
        or not isinstance(validation["command"], str)
        or not validation["command"]
        or len(validation["command"]) > 500
        or validation["status"] not in PHASE_STATUSES
        or not isinstance(validation["summary"], str)
        or len(validation["summary"]) > 500
        or not isinstance(validation["recorded_at"], str)
        for validation in validations
    ):
        raise LifecycleError("Lifecycle validation records are invalid.")
    attempts = state.get("computer_use_attempts")
    if not isinstance(attempts, list) or any(
        not isinstance(attempt, dict)
        or set(attempt)
        != {
            "stage",
            "outcome",
            "action_attempted",
            "response",
            "lock_state",
            "window_visible",
            "window_frontmost",
            "current_space",
            "dedicated_instance",
            "recorded_at",
        }
        or attempt["stage"] not in COMPUTER_USE_STAGES
        or attempt["outcome"] not in COMPUTER_USE_OUTCOMES
        or not isinstance(attempt["action_attempted"], bool)
        or attempt["response"] not in {"success", "error", "timeout"}
        or any(
            attempt[key] not in {"yes", "no", "unknown"}
            for key in (
                "lock_state",
                "window_visible",
                "window_frontmost",
                "current_space",
                "dedicated_instance",
            )
        )
        or not isinstance(attempt["recorded_at"], str)
        for attempt in attempts
    ):
        raise LifecycleError("Lifecycle Computer Use attempt records are invalid.")
    cleanup = state.get("cleanup")
    if (
        not isinstance(cleanup, dict)
        or set(cleanup) != {"status", "removed", "already_absent", "remaining"}
        or cleanup["status"] not in {
            "NOT_RUN",
            "RUNNING",
            "PASS",
            "FAIL",
            "KEPT",
        }
        or any(
            not isinstance(cleanup[key], list)
            or any(not isinstance(item, str) for item in cleanup[key])
            or len(set(cleanup[key])) != len(cleanup[key])
            for key in ("removed", "already_absent", "remaining")
        )
    ):
        raise LifecycleError("Lifecycle cleanup ledger is invalid.")


def prepare(root_value: Path) -> dict[str, Any]:
    root = verification_root(root_value, create=True)
    lifecycle_paths = paths(root)
    reject_symlinks(lifecycle_paths["base"], stop_at=root)
    for key in ("lock", "runs", "reports", "archive"):
        reject_symlinks(lifecycle_paths[key], stop_at=root)
    docker_version = require_command(
        ["docker", "version", "--format", "{{.Server.Version}}"], "Docker Engine"
    )
    compose_version = require_command(
        ["docker", "compose", "version", "--short"], "Docker Compose"
    )
    git_version = require_command(["git", "--version"], "Git")
    browser_key, browser_name = detect_browser()
    with lifecycle_lock(root):
        if lifecycle_paths["active"].is_symlink():
            raise LifecycleError("Active lifecycle pointer is symlinked.")
        if lifecycle_paths["active"].exists():
            result, _ = _destroy_active(root)
            if result != "DESTROYED":
                raise LifecycleError(
                    "ACTIVE_THREE_TIER_REPLACEMENT_FAILED: the previous owned "
                    "environment was not fully destroyed."
                )
        runs_root = lifecycle_paths["runs"]
        if runs_root.exists() and any(runs_root.iterdir()):
            raise LifecycleError(
                "ORPHANED_THREE_TIER_RUN: repair or safely destroy the existing owned "
                "run before creating another application."
            )
        verification_id = uuid.uuid4().hex
        run_root = lifecycle_paths["runs"] / verification_id
        for directory in (
            run_root / "project",
            run_root / "private" / "codex-home",
            run_root / "evidence",
            lifecycle_paths["reports"] / verification_id,
        ):
            directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        lifecycle_paths["archive"].mkdir(parents=True, mode=0o700, exist_ok=True)
        private_json(
            run_root / RUN_MARKER,
            {"schema": LIFECYCLE_SCHEMA, "verification_id": verification_id},
        )
        (run_root / "project" / PROJECT_MARKER).write_text(
            json.dumps(
                {
                    "schema": LIFECYCLE_SCHEMA,
                    "scenario": SCENARIO,
                    "verification_id": verification_id,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        state: dict[str, Any] = {
            "schema": LIFECYCLE_SCHEMA,
            "scenario": SCENARIO,
            "verification_id": verification_id,
            "status": "PREPARED",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "verification_root": str(root),
            "run_root": str(run_root),
            "project_root": str(run_root / "project"),
            "private_root": str(run_root / "private"),
            "evidence_root": str(run_root / "evidence"),
            "report_path": str(
                lifecycle_paths["reports"] / verification_id / "report.md"
            ),
            "compose_project": f"sdlc-workflow-test-{verification_id[:12]}",
            "environment": {
                "docker_engine": docker_version,
                "docker_compose": compose_version,
                "git": git_version,
                "browser": browser_key,
                "browser_name": browser_name,
                "computer_use": "PENDING_AGENT_PREFLIGHT",
            },
            "git": {"baseline_sha": None, "promoted_sha": None},
            "endpoints": {},
            "resources": {kind: [] for kind in RESOURCE_KINDS},
            "browser_tab": {"title": None, "url": None, "closed": False},
            "browser_instance": three_tier_browser.initial_state(verification_id),
            "phases": [],
            "validations": [],
            "computer_use_attempts": [],
            "semantic_summary": None,
            "cleanup": {
                "status": "NOT_RUN",
                "removed": [],
                "already_absent": [],
                "remaining": [],
            },
        }
        private_json(lifecycle_paths["active"], state)
        write_report(root, state)
        return state


def update_state(root: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    private_json(paths(root)["active"], state)
    write_report(root, state)


def validate_phase_pass_artifact(
    state: dict[str, Any], phase: str, evidence: list[str]
) -> None:
    run_root = Path(state["run_root"])
    expected = f"evidence/phases/{phase}.json"
    if evidence != [expected]:
        raise LifecycleError(
            f"Passing phase {phase} requires its one canonical JSON result."
        )
    artifact = run_root / expected
    reject_symlinks(artifact, stop_at=run_root)
    if (
        not artifact.is_file()
        or artifact.stat().st_size > 1024 * 1024
        or artifact.stat().st_nlink != 1
        or (os.name == "posix" and artifact.stat().st_mode & 0o077)
    ):
        raise LifecycleError(f"Phase result is unavailable or unsafe: {phase}")
    try:
        result = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise LifecycleError(f"Phase result is invalid JSON: {phase}") from error
    if (
        not isinstance(result, dict)
        or set(result)
        != {
            "schema",
            "phase",
            "status",
            "verification_id",
            "baseline_sha",
            "recorded_head",
            "assertions",
        }
        or result.get("schema") != PHASE_RESULT_SCHEMA
        or result.get("phase") != phase
        or result.get("status") != "PASS"
        or result.get("verification_id") != state["verification_id"]
        or result.get("baseline_sha") != state["git"].get("baseline_sha")
        or result.get("assertions") != PHASE_REQUIRED_ASSERTIONS[phase]
    ):
        raise LifecycleError(f"Phase semantic result is invalid: {phase}")
    baseline_sha = result.get("baseline_sha")
    recorded_head = result.get("recorded_head")
    if not valid_git_sha(baseline_sha) or not valid_git_sha(recorded_head):
        raise LifecycleError(f"Phase Git identity is invalid: {phase}")
    project_root = Path(state["project_root"])
    promoted_sha = state["git"].get("promoted_sha")
    if promoted_sha is None:
        current_head = require_command(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            "Phase Git identity",
        )
        if current_head != recorded_head:
            raise LifecycleError(f"Phase Git identity is stale: {phase}")
    ancestry_checks = [(baseline_sha, recorded_head, "baseline")]
    if promoted_sha is not None:
        ancestry_checks.append((recorded_head, promoted_sha, "promoted"))
    for ancestor, descendant, label in ancestry_checks:
        ancestry = command(
            [
                "git",
                "-C",
                str(project_root),
                "merge-base",
                "--is-ancestor",
                ancestor,
                descendant,
            ]
        )
        if ancestry.returncode != 0:
            raise LifecycleError(
                f"Phase Git identity is not an ancestor of the {label} identity: {phase}"
            )


def record_phase(
    root_value: Path, phase: str, status: str, summary: str, evidence: list[str]
) -> dict[str, Any]:
    if status not in PHASE_STATUSES:
        raise LifecycleError(f"Unsupported phase status: {status}")
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    if phase not in ALLOWED_PHASES or any(character in phase for character in "\r\n"):
        raise LifecycleError("Phase name is invalid.")
    if contains_secret_material(summary):
        raise LifecycleError("Phase summary appears to contain secret material.")
    run_root = Path(state["run_root"])
    normalized_evidence: list[str] = []
    for item in evidence:
        raw_candidate = run_root / item
        reject_symlinks(raw_candidate, stop_at=run_root)
        candidate = raw_candidate.resolve(strict=False)
        if not candidate.is_relative_to(run_root) or not candidate.is_file():
            raise LifecycleError(
                f"Phase evidence is missing or outside the run: {item}"
            )
        reject_symlinks(candidate, stop_at=run_root)
        normalized_evidence.append(candidate.relative_to(run_root).as_posix())
    if status == "PASS":
        validate_phase_pass_artifact(state, phase, normalized_evidence)
    entry = {
        "phase": phase,
        "status": status,
        "summary": summary.replace("\n", " ")[:500],
        "evidence": normalized_evidence,
        "recorded_at": utc_now(),
    }
    state["phases"] = [
        existing for existing in state["phases"] if existing.get("phase") != phase
    ] + [entry]
    if phase == "sdlc-uat-tests" and status in {"PASS", "PARTIAL", "FAIL"}:
        state["environment"]["computer_use"] = status
    state["status"] = "RUNNING"
    update_state(root, state)
    return state


def record_validation(
    root_value: Path, command_text: str, status: str, summary: str
) -> dict[str, Any]:
    if status not in PHASE_STATUSES:
        raise LifecycleError(f"Unsupported validation status: {status}")
    if (
        not command_text
        or any(character in command_text for character in "\r\n")
        or any(character in summary for character in "\r\n")
    ):
        raise LifecycleError("Validation command and summary must be single-line text.")
    if contains_secret_material(command_text) or contains_secret_material(summary):
        raise LifecycleError("Validation record appears to contain secret material.")
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    normalized_command = command_text[:500]
    entry = {
        "command": normalized_command,
        "status": status,
        "summary": summary[:500],
        "recorded_at": utc_now(),
    }
    state["validations"] = [
        validation
        for validation in state.setdefault("validations", [])
        if validation.get("command") != normalized_command
    ] + [entry]
    update_state(root, state)
    return state


def record_computer_use(
    root_value: Path,
    *,
    stage: str,
    outcome: str,
    action_attempted: bool,
    response: str,
    lock_state: str,
    window_visible: str,
    window_frontmost: str,
    current_space: str,
    window_marker: str,
) -> dict[str, Any]:
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    if any(item["response"] == "timeout" for item in state["computer_use_attempts"]):
        raise LifecycleError(
            "Computer Use service is unhealthy; no later attempt is allowed."
        )
    if any(item["stage"] == stage for item in state["computer_use_attempts"]):
        raise LifecycleError("Computer Use stage already has a terminal record.")
    expected_marker = state["browser_instance"]["window_marker"]
    dedicated_instance = "yes" if window_marker == expected_marker else "no"
    if action_attempted and dedicated_instance != "yes":
        raise LifecycleError(
            "Computer Use actions require the exact dedicated Chrome marker."
        )
    if outcome == "PASS" and (
        response != "success"
        or lock_state != "no"
        or window_visible != "yes"
        or window_frontmost != "yes"
        or current_space != "yes"
        or dedicated_instance != "yes"
    ):
        raise LifecycleError(
            "Computer Use PASS requires a successful unlocked, visible, "
            "frontmost, current-Space capture."
        )
    if dedicated_instance == "yes":
        try:
            three_tier_browser.assert_owned_running(
                Path(state["run_root"]),
                state["verification_id"],
                state["browser_instance"],
            )
        except three_tier_browser.BrowserOwnershipError as error:
            raise LifecycleError(str(error)) from error
    if outcome == "ENVIRONMENT_DEFECT" and action_attempted:
        raise LifecycleError(
            "Computer Use environment defects must be recorded before any action."
        )
    if outcome == "FAIL" and not action_attempted:
        raise LifecycleError(
            "Computer Use action failure requires an attempted action."
        )
    entry = {
        "stage": stage,
        "outcome": outcome,
        "action_attempted": action_attempted,
        "response": response,
        "lock_state": lock_state,
        "window_visible": window_visible,
        "window_frontmost": window_frontmost,
        "current_space": current_space,
        "dedicated_instance": dedicated_instance,
        "recorded_at": utc_now(),
    }
    candidate = dict(state)
    candidate["computer_use_attempts"] = [*state["computer_use_attempts"], entry]
    validate_state(root, candidate)
    state.update(candidate)
    state["environment"]["computer_use"] = (
        "FAIL"
        if any(
            attempt["outcome"] != "PASS" for attempt in state["computer_use_attempts"]
        )
        else "PASS"
    )
    update_state(root, state)
    return state


def resource_identifier_valid(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value))


def validate_loopback_url(value: str, label: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise LifecycleError(f"{label} must be an HTTP loopback URL.")
    if parsed.port is None:
        raise LifecycleError(f"{label} must include the resolved dynamic host port.")


def record_runtime(
    root_value: Path,
    *,
    web_url: str,
    api_url: str,
    health_url: str,
    database_endpoint: str,
    web_container: str,
    database_container: str,
    networks: list[str],
    volumes: list[str],
    images: list[str],
) -> dict[str, Any]:
    for label, value in (
        ("web URL", web_url),
        ("API URL", api_url),
        ("health URL", health_url),
    ):
        validate_loopback_url(value, label)
    if (
        not database_endpoint.startswith("db:")
        or any(
            value in database_endpoint
            for value in ("localhost", "127.0.0.1", "0.0.0.0", "@", "?", "#")
        )
        or any(character.isspace() for character in database_endpoint)
    ):
        raise LifecycleError(
            "Database endpoint must be Compose-internal and not host-published."
        )
    containers = [web_container, database_container]
    supplied = {
        "containers": containers,
        "networks": networks,
        "volumes": volumes,
        "images": images,
    }
    if (
        len(containers) != 2
        or len(networks) != 1
        or len(volumes) != 1
        or len(images) != 1
    ):
        raise LifecycleError(
            "Runtime inventory must contain exactly two containers, one network, "
            "one database volume, and one built web image."
        )
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    inspected: dict[str, list[dict[str, Any]]] = {}
    for kind, identifiers in supplied.items():
        if len(set(identifiers)) != len(identifiers) or any(
            not resource_identifier_valid(identifier) for identifier in identifiers
        ):
            raise LifecycleError(f"Invalid {kind} resource identifier.")
        inspected[kind] = [
            assert_resource_owned(kind, identifier, state)
            for identifier in identifiers
        ]
        canonical_ids = [item["canonical_id"] for item in inspected[kind]]
        if len(set(canonical_ids)) != len(canonical_ids):
            raise LifecycleError(
                f"Runtime {kind} aliases resolve to a duplicate canonical identity."
            )
    web_labels = inspected["containers"][0]["labels"]
    database_labels = inspected["containers"][1]["labels"]
    if web_labels is None or web_labels.get("com.docker.compose.service") != "web":
        raise LifecycleError("Web container must be the Compose web service.")
    if (
        database_labels is None
        or database_labels.get("com.docker.compose.service") != "db"
    ):
        raise LifecycleError("Database container must be the Compose db service.")
    assert_port_isolation(web_container, database_container, urlsplit(web_url).port)
    state["endpoints"] = {
        "web": web_url,
        "api": api_url,
        "health": health_url,
        "database": database_endpoint,
    }
    state["resources"] = {
        kind: [resource["canonical_id"] for resource in inspected[kind]]
        for kind in RESOURCE_KINDS
    }
    state["status"] = "RUNNING"
    update_state(root, state)
    return state


def docker_not_found(kind: str, identifier: str, detail: str) -> bool:
    escaped = re.escape(identifier.lower())
    patterns = {
        "containers": rf"(?:^|:\s*)no such container:?\s*{escaped}\s*$",
        "networks": rf"(?:^|:\s*)network\s+{escaped}\s+not found\s*$",
        "volumes": rf"(?:^|:\s*)(?:get\s+{escaped}:\s*)?no such volume\s*$",
        "images": rf"(?:^|:\s*)no such image:?\s*{escaped}\s*$",
    }
    return re.search(patterns[kind], detail.lower()) is not None


def inspect_resource(kind: str, identifier: str) -> dict[str, Any] | None:
    docker_kind = {
        "containers": "container",
        "networks": "network",
        "volumes": "volume",
        "images": "image",
    }[kind]
    result = command(["docker", docker_kind, "inspect", identifier])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if docker_not_found(kind, identifier, detail):
            return None
        raise LifecycleError(
            f"Could not inspect {kind} resource {identifier}: "
            f"{detail or 'command failed'}"
        )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LifecycleError(
            f"Could not parse identity for {kind} {identifier}."
        ) from error
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(
        payload[0], dict
    ):
        raise LifecycleError(f"Invalid identity for {kind} {identifier}.")
    resource = payload[0]
    canonical = resource.get("Name" if kind == "volumes" else "Id")
    if kind in {"containers", "images"}:
        config = resource.get("Config")
        if not isinstance(config, dict):
            raise LifecycleError(f"Invalid configuration for {kind} {identifier}.")
        labels = config.get("Labels")
    else:
        labels = resource.get("Labels")
    if labels is None:
        labels = {}
    if not isinstance(labels, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
    ):
        raise LifecycleError(f"Invalid labels for {kind} {identifier}.")
    if not isinstance(canonical, str) or not resource_identifier_valid(canonical):
        raise LifecycleError(f"Invalid canonical identity for {kind} {identifier}.")
    return {"canonical_id": canonical, "labels": labels}


def inspect_labels(kind: str, identifier: str) -> dict[str, str] | None:
    resource = inspect_resource(kind, identifier)
    return None if resource is None else resource["labels"]


def assert_resource_owned(
    kind: str, identifier: str, state: dict[str, Any]
) -> dict[str, Any]:
    resource = inspect_resource(kind, identifier)
    if resource is None:
        raise LifecycleError(f"Recorded {kind} resource does not exist: {identifier}")
    labels = resource["labels"]
    if labels.get(OWNERSHIP_LABEL) != state["verification_id"]:
        raise LifecycleError(f"Ownership label mismatch for {kind} {identifier}.")
    if labels.get(COMPOSE_LABEL) != state["compose_project"]:
        raise LifecycleError(f"Compose label mismatch for {kind} {identifier}.")
    return resource


def discover_owned_resources(state: dict[str, Any]) -> dict[str, list[str]]:
    """Discover exact dual-labelled resources that may predate inventory capture."""
    commands = {
        "containers": ["docker", "container", "ls", "--all", "--quiet", "--no-trunc"],
        "networks": ["docker", "network", "ls", "--quiet", "--no-trunc"],
        "volumes": ["docker", "volume", "ls", "--quiet"],
        "images": ["docker", "image", "ls", "--quiet", "--no-trunc"],
    }
    filters = [
        "--filter",
        f"label={OWNERSHIP_LABEL}={state['verification_id']}",
        "--filter",
        f"label={COMPOSE_LABEL}={state['compose_project']}",
    ]
    discovered: dict[str, list[str]] = {}
    for kind in RESOURCE_KINDS:
        result = command([*commands[kind], *filters])
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise LifecycleError(
                f"Could not discover owned {kind}: {detail or 'command failed'}"
            )
        identifiers = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
        if len(set(identifiers)) != len(identifiers) or any(
            not resource_identifier_valid(identifier) for identifier in identifiers
        ):
            raise LifecycleError(f"Discovered owned {kind} inventory is invalid.")
        discovered[kind] = identifiers
    return discovered


def run_owned_compose(state: dict[str, Any], compose_args: list[str]) -> str:
    """Run one bounded Compose action under the caller's generation lock."""
    arguments = list(compose_args)
    if arguments and arguments[0] == "--":
        arguments.pop(0)
    if not arguments or arguments[0] not in COMPOSE_ACTIONS:
        raise LifecycleError("Owned Compose action is missing or unsupported.")
    reserved = {
        "-f",
        "-p",
        "--file",
        "--project-name",
        "--project-directory",
        "--scale",
    }
    if any(
        argument in reserved
        or any(
            argument.startswith(f"{option}=")
            for option in reserved
            if option.startswith("--")
        )
        for argument in arguments
    ):
        raise LifecycleError(
            "Owned Compose action cannot override project identity, files, or scale."
        )
    result = command(
        [
            "docker",
            "compose",
            "--project-name",
            state["compose_project"],
            "--project-directory",
            state["project_root"],
            *arguments,
        ],
        timeout=1800,
    )
    if result.returncode != 0:
        raise LifecycleError("Owned Docker Compose action failed.")
    return result.stdout


def container_ports(identifier: str) -> dict[str, Any]:
    result = command(
        [
            "docker",
            "container",
            "inspect",
            identifier,
            "--format",
            "{{json .NetworkSettings.Ports}}",
        ]
    )
    if result.returncode != 0:
        raise LifecycleError(
            f"Could not inspect port bindings for container {identifier}."
        )
    try:
        value = json.loads(result.stdout.strip() or "{}")
    except json.JSONDecodeError as error:
        raise LifecycleError(
            f"Could not parse port bindings for container {identifier}."
        ) from error
    if not isinstance(value, dict):
        raise LifecycleError(f"Invalid port bindings for container {identifier}.")
    return value


def assert_port_isolation(
    web_container: str, database_container: str, web_port: int | None
) -> None:
    if web_port is None:
        raise LifecycleError("Resolved web port is missing.")
    bindings = [
        binding
        for value in container_ports(web_container).values()
        if isinstance(value, list)
        for binding in value
        if isinstance(binding, dict)
    ]
    if len(bindings) != 1:
        raise LifecycleError("Web container must expose exactly one host binding.")
    binding = bindings[0]
    if binding.get("HostIp") not in {"127.0.0.1", "::1"} or binding.get(
        "HostPort"
    ) != str(web_port):
        raise LifecycleError(
            "Web container binding is not the recorded dynamic loopback port."
        )
    database_bindings = [
        value
        for value in container_ports(database_container).values()
        if isinstance(value, list) and value
    ]
    if database_bindings:
        raise LifecycleError("Database container must not publish a host port.")


def record_browser(
    root_value: Path, title: str, url: str, closed: bool
) -> dict[str, Any]:
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    if not title or any(character in title for character in "\r\n"):
        raise LifecycleError("Browser tab title is invalid.")
    validate_loopback_url(url, "browser tab URL")
    verification_ids = parse_qs(urlsplit(url).query).get("verification_id", [])
    if verification_ids != [state["verification_id"]]:
        raise LifecycleError(
            "Browser tab URL must carry the exact verification_id marker."
        )
    expected_status = "CLOSED" if closed else "RUNNING"
    if state["browser_instance"]["status"] != expected_status:
        raise LifecycleError(
            f"Browser tab state requires dedicated Chrome to be {expected_status}."
        )
    state["browser_tab"] = {"title": title[:200], "url": url, "closed": closed}
    update_state(root, state)
    return state


def launch_browser(root_value: Path) -> dict[str, Any]:
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    launched: dict[str, Any] | None = None
    try:
        launched = three_tier_browser.launch(
            Path(state["run_root"]),
            state["verification_id"],
            state["browser_instance"],
        )
        state["browser_instance"] = launched
        update_state(root, state)
    except three_tier_browser.BrowserOwnershipError as error:
        raise LifecycleError(str(error)) from error
    except (LifecycleError, OSError) as error:
        if launched is not None and launched.get("status") == "RUNNING":
            try:
                three_tier_browser.close(
                    Path(state["run_root"]),
                    state["verification_id"],
                    launched,
                )
            except three_tier_browser.BrowserOwnershipError:
                raise LifecycleError(
                    "Dedicated Chrome launched but its state could not be "
                    "persisted or safely rolled back."
                ) from error
        if isinstance(error, LifecycleError):
            raise
        raise LifecycleError(
            f"Could not persist dedicated Chrome ownership: {error}"
        ) from error
    return state


def close_browser(root_value: Path) -> dict[str, Any]:
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    try:
        state["browser_instance"] = three_tier_browser.close(
            Path(state["run_root"]),
            state["verification_id"],
            state["browser_instance"],
        )
    except three_tier_browser.BrowserOwnershipError as error:
        raise LifecycleError(str(error)) from error
    update_state(root, state)
    return state


def prepare_public_images(root_value: Path) -> dict[str, Any]:
    """Pull the fixed public base images without using user credential helpers."""

    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    private_root = Path(state["private_root"])
    docker_config = private_root / "docker-config"
    reject_symlinks(docker_config, stop_at=Path(state["run_root"]))
    docker_config.mkdir(mode=0o700, exist_ok=True)
    if os.name == "posix":
        os.chmod(docker_config, 0o700)
    config_path = docker_config / "config.json"
    if config_path.exists() and (config_path.is_symlink() or not config_path.is_file()):
        raise LifecycleError("Private Docker configuration path is unsafe.")
    private_json(config_path, {})
    context_name = require_command(["docker", "context", "show"], "Docker context")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", context_name):
        raise LifecycleError("Docker context name is invalid.")
    for image in PUBLIC_BASE_IMAGES:
        present = command(["docker", "image", "inspect", image])
        if present.returncode == 0:
            continue
        pulled = command(
            [
                "docker",
                "--config",
                str(docker_config),
                "--context",
                context_name,
                "image",
                "pull",
                image,
            ],
            timeout=300,
        )
        if pulled.returncode != 0:
            detail = (pulled.stderr or pulled.stdout).strip()[:500]
            raise LifecycleError(
                f"Public base image pull failed for {image}: {detail or 'command failed'}"
            )
    state["environment"]["public_base_images"] = list(PUBLIC_BASE_IMAGES)
    update_state(root, state)
    return state


def record_git(
    root_value: Path, baseline_sha: str, promoted_sha: str | None
) -> dict[str, Any]:
    identities = [("baseline", baseline_sha)]
    if promoted_sha is not None:
        identities.append(("promoted", promoted_sha))
    for label, value in identities:
        if not valid_git_sha(value):
            raise LifecycleError(f"Invalid {label} Git SHA.")
    if promoted_sha is not None and baseline_sha == promoted_sha:
        raise LifecycleError("Promoted Git SHA must differ from the baseline SHA.")
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    project_root = Path(state["project_root"])
    git_path = project_root / ".git"
    if not git_path.is_dir() or git_path.is_symlink():
        raise LifecycleError("Three-tier project is not an initialized Git repository.")
    expected_head = promoted_sha or baseline_sha
    if (
        require_command(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"], "Git identity"
        )
        != expected_head
    ):
        label = "Promoted" if promoted_sha is not None else "Baseline"
        raise LifecycleError(f"{label} SHA is not the current project HEAD.")
    if require_command(
        ["git", "-C", str(project_root), "status", "--porcelain"], "Git cleanliness"
    ):
        raise LifecycleError(
            "Three-tier project must be clean before recording Git identity."
        )
    if require_command(
        ["git", "-C", str(project_root), "remote"], "Git remote inventory"
    ):
        raise LifecycleError("Three-tier project must not have a Git remote.")
    if promoted_sha is not None:
        ancestor = command(
            [
                "git",
                "-C",
                str(project_root),
                "merge-base",
                "--is-ancestor",
                baseline_sha,
                promoted_sha,
            ]
        )
        if ancestor.returncode != 0:
            raise LifecycleError(
                "Promoted SHA is not a descendant of the baseline SHA."
            )
    state["git"] = {"baseline_sha": baseline_sha, "promoted_sha": promoted_sha}
    update_state(root, state)
    return state


def validate_runtime_ready(state: dict[str, Any], *, keep: bool) -> None:
    resources = state["resources"]
    expected_counts = {
        "containers": 2,
        "networks": 1,
        "volumes": 1,
        "images": 1,
    }
    if any(len(resources[kind]) != count for kind, count in expected_counts.items()):
        raise LifecycleError("PASS requires the complete exact Docker inventory.")
    endpoints = state.get("endpoints")
    if not isinstance(endpoints, dict) or set(endpoints) != {
        "web",
        "api",
        "health",
        "database",
    }:
        raise LifecycleError("PASS requires every runtime endpoint.")
    for label in ("web", "api", "health"):
        validate_loopback_url(endpoints[label], f"{label} URL")
    database_endpoint = endpoints["database"]
    if (
        not isinstance(database_endpoint, str)
        or not database_endpoint.startswith("db:")
        or any(
            value in database_endpoint
            for value in ("localhost", "127.0.0.1", "0.0.0.0", "@", "?", "#")
        )
    ):
        raise LifecycleError("PASS requires an internal-only database endpoint.")
    browser_tab = state["browser_tab"]
    if (
        not browser_tab["title"]
        or not browser_tab["url"]
        or state["environment"].get("computer_use") != "PASS"
    ):
        raise LifecycleError("PASS requires an exact computer-use browser-tab record.")
    validate_loopback_url(browser_tab["url"], "browser tab URL")
    expected_browser_status = "RUNNING" if keep else "CLOSED"
    if state["browser_instance"]["status"] != expected_browser_status:
        raise LifecycleError(
            f"PASS requires dedicated Chrome to be {expected_browser_status}."
        )
    attempts = {attempt["stage"]: attempt for attempt in state["computer_use_attempts"]}
    if any(
        attempts.get(stage, {}).get("outcome") != "PASS"
        or attempts.get(stage, {}).get("dedicated_instance") != "yes"
        for stage in ("capability-discovery", "evaluate-readiness", "uat-readiness")
    ):
        raise LifecycleError("PASS requires distinct Computer Use readiness gates.")
    phase_map = {
        entry.get("phase"): entry
        for entry in state.get("phases", [])
        if isinstance(entry, dict)
    }
    if any(
        phase not in phase_map
        or phase_map[phase].get("status") != "PASS"
        or not phase_map[phase].get("evidence")
        for phase in REQUIRED_SDLC_PHASES
    ):
        raise LifecycleError("PASS requires recorded evidence for every SDLC phase.")
    for phase in REQUIRED_SDLC_PHASES:
        validate_phase_pass_artifact(state, phase, phase_map[phase]["evidence"])
    validations = state.get("validations", [])
    if not validations or any(
        validation.get("status") != "PASS" for validation in validations
    ):
        raise LifecycleError("PASS requires every recorded validation to pass.")
    for kind in RESOURCE_KINDS:
        for identifier in resources[kind]:
            assert_resource_owned(kind, identifier, state)
    assert_port_isolation(
        resources["containers"][0],
        resources["containers"][1],
        urlsplit(endpoints["web"]).port,
    )


def finish(root_value: Path, status: str, keep: bool) -> dict[str, Any]:
    if status not in FINAL_STATUSES:
        raise LifecycleError(f"Unsupported final status: {status}")
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    if keep and state["browser_tab"].get("closed"):
        raise LifecycleError("Keep mode must retain its dedicated browser tab.")
    state["semantic_summary"] = summarize_semantic_results(state)
    if status == "PASS":
        validate_runtime_ready(state, keep=keep)
        try:
            state["semantic_summary"] = validate_semantic_results(state, keep=keep)
        except SemanticEvidenceError as error:
            raise LifecycleError(str(error)) from error
        if not keep and not state["browser_tab"].get("closed"):
            raise LifecycleError(
                "Default create mode must close its dedicated browser tab."
            )
    state["result"] = status
    state["status"] = "KEPT" if keep else "READY_FOR_CLEANUP"
    if keep:
        state["cleanup"] = {
            "status": "KEPT",
            "removed": [],
            "already_absent": [],
            "remaining": [
                f"{kind}:{identifier}"
                for kind in RESOURCE_KINDS
                for identifier in state["resources"].get(kind, [])
            ],
        }
    state["finished_at"] = utc_now()
    update_state(root, state)
    return state


def write_report(root: Path, state: dict[str, Any]) -> None:
    report_path = Path(state["report_path"])
    expected = paths(root)["reports"] / state["verification_id"] / "report.md"
    if report_path != expected:
        raise LifecycleError("Report path does not match the lifecycle identity.")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=".report.", suffix=".tmp", dir=report_path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(report_text(state))
        if os.name == "posix":
            os.chmod(temporary, 0o600)
        os.replace(temporary, report_path)
    finally:
        if temporary.exists():
            temporary.unlink()


def remove_resource(kind: str, identifier: str) -> str:
    args = {
        "containers": ["docker", "container", "rm", "--force", identifier],
        "networks": ["docker", "network", "rm", identifier],
        "volumes": ["docker", "volume", "rm", identifier],
        "images": ["docker", "image", "rm", identifier],
    }[kind]
    result = command(args, timeout=60)
    if result.returncode == 0:
        return "REMOVED"
    if inspect_resource(kind, identifier) is None:
        return "ALREADY_ABSENT"
    detail = (result.stderr or result.stdout).strip()
    raise LifecycleError(
        f"Could not remove owned {kind} resource {identifier}: "
        f"{detail or 'command failed'}"
    )


def assert_project_safe_for_destroy(state: dict[str, Any]) -> None:
    project_root = Path(state["project_root"])
    git_path = project_root / ".git"
    if git_path.is_symlink() or (git_path.exists() and not git_path.is_dir()):
        raise LifecycleError("Owned project Git metadata is unsafe before destroy.")
    if not git_path.exists():
        if state["status"] == "KEPT":
            raise LifecycleError(
                "Kept project lost its Git metadata; destroy fails closed."
            )
        return
    top_level = require_command(
        ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"],
        "Owned project Git identity",
    )
    if Path(top_level).resolve(strict=False) != project_root:
        raise LifecycleError("Owned project Git root identity changed before destroy.")
    if require_command(
        ["git", "-C", str(project_root), "remote"], "Owned project remote inventory"
    ):
        raise LifecycleError("Owned project gained a Git remote; destroy fails closed.")
    if state["status"] == "KEPT" and require_command(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        "Owned project cleanliness",
    ):
        raise LifecycleError(
            "Kept project has uncommitted changes; preserve or revert them before destroy."
        )


def _destroy_active(root: Path) -> tuple[str, dict[str, Any] | None]:
    """Destroy the active owned lifecycle while the caller holds its lock."""
    active_path = paths(root)["active"]
    reject_symlinks(paths(root)["archive"], stop_at=root)
    reject_symlinks(active_path, stop_at=root)
    if active_path.is_symlink():
        raise LifecycleError("Active lifecycle pointer is symlinked.")
    if not active_path.exists():
        return "ALREADY_DESTROYED", None
    _, state = load_active(root)
    assert_project_safe_for_destroy(state)
    try:
        state["browser_instance"] = three_tier_browser.close(
            Path(state["run_root"]),
            state["verification_id"],
            state["browser_instance"],
        )
    except three_tier_browser.BrowserOwnershipError as error:
        state["status"] = "CLEANUP_FAILED"
        state["cleanup"]["status"] = "FAIL"
        update_state(root, state)
        raise LifecycleError(str(error)) from error
    state["status"] = "DESTROYING"
    state["cleanup"]["status"] = "RUNNING"
    update_state(root, state)

    try:
        discovered = discover_owned_resources(state)
        existing: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for kind in RESOURCE_KINDS:
            identifiers = [*state["resources"].get(kind, []), *discovered[kind]]
            for identifier in identifiers:
                resource = inspect_resource(kind, identifier)
                ledger_identity = f"{kind}:{identifier}"
                if resource is None:
                    if ledger_identity not in state["cleanup"]["removed"]:
                        state["cleanup"]["already_absent"] = list(
                            dict.fromkeys(
                                [
                                    *state["cleanup"]["already_absent"],
                                    ledger_identity,
                                ]
                            )
                        )
                    continue
                labels = resource["labels"]
                if labels.get(OWNERSHIP_LABEL) != state["verification_id"]:
                    raise LifecycleError(
                        f"Ownership label mismatch for {kind} {identifier}."
                    )
                if labels.get(COMPOSE_LABEL) != state["compose_project"]:
                    raise LifecycleError(
                        f"Compose label mismatch for {kind} {identifier}."
                    )
                identity = (kind, resource["canonical_id"])
                if identity not in seen:
                    seen.add(identity)
                    existing.append(identity)
        state["cleanup"]["remaining"] = [
            f"{kind}:{identifier}" for kind, identifier in existing
        ]
        update_state(root, state)
        for kind, identifier in existing:
            ledger_identity = f"{kind}:{identifier}"
            outcome = remove_resource(kind, identifier)
            if outcome == "REMOVED":
                state["cleanup"]["removed"] = list(
                    dict.fromkeys([*state["cleanup"]["removed"], ledger_identity])
                )
            elif ledger_identity not in state["cleanup"]["removed"]:
                state["cleanup"]["already_absent"] = list(
                    dict.fromkeys(
                        [*state["cleanup"]["already_absent"], ledger_identity]
                    )
                )
            state["cleanup"]["remaining"] = [
                item
                for item in state["cleanup"]["remaining"]
                if item != ledger_identity
            ]
            update_state(root, state)
        remaining = [
            f"{kind}:{identifier}"
            for kind, identifier in existing
            if inspect_resource(kind, identifier) is not None
        ]
        if remaining:
            state["cleanup"]["remaining"] = remaining
            raise LifecycleError("Owned Docker resources remain after cleanup.")
        run_root = Path(state["run_root"])
        validate_state(root, state)
        shutil.rmtree(run_root)
        state["cleanup"]["status"] = "PASS"
        state["status"] = "DESTROYED"
        state["destroyed_at"] = utc_now()
        state["updated_at"] = utc_now()
        write_report(root, state)
        private_json(paths(root)["archive"] / f"{state['verification_id']}.json", state)
        active_path.unlink()
        return "DESTROYED", state
    except (LifecycleError, OSError) as error:
        state["cleanup"]["status"] = "FAIL"
        state["status"] = "CLEANUP_FAILED"
        update_state(root, state)
        if isinstance(error, LifecycleError):
            raise
        raise LifecycleError(f"Owned filesystem cleanup failed: {error}") from error


def destroy(root_value: Path) -> tuple[str, dict[str, Any] | None]:
    try:
        root = verification_root(root_value, create=False)
    except LifecycleError as error:
        if (
            str(error) == "NO_ACTIVE_THREE_TIER_APPLICATION"
            and not root_value.expanduser().absolute().exists()
        ):
            return "ALREADY_DESTROYED", None
        raise
    with lifecycle_lock(root):
        return _destroy_active(root)


def resume(root_value: Path) -> dict[str, Any]:
    """Reopen one retained failed or partial lifecycle after safety checks."""
    root = verification_root(root_value, create=False)
    _, state = load_active(root)
    if state.get("status") != "KEPT" or state.get("result") not in {
        "FAIL",
        "PARTIAL",
    }:
        raise LifecycleError("RESUME_REQUIRES_KEPT_FAILED_OR_PARTIAL_RUN")
    project_root = Path(state["project_root"])
    git_path = project_root / ".git"
    if git_path.is_symlink() or (git_path.exists() and not git_path.is_dir()):
        raise LifecycleError("Owned project Git metadata is unsafe before resume.")
    if git_path.exists():
        assert_project_safe_for_destroy(state)
    for kind in RESOURCE_KINDS:
        for identifier in state["resources"][kind]:
            if inspect_labels(kind, identifier) is None:
                raise LifecycleError(
                    f"Recorded owned {kind} resource is missing: {identifier}"
                )
            assert_resource_owned(kind, identifier, state)
    if any(state["resources"].values()):
        expected_counts = {"containers": 2, "networks": 1, "volumes": 1, "images": 1}
        if any(
            len(state["resources"][kind]) != count
            for kind, count in expected_counts.items()
        ):
            raise LifecycleError("Recorded runtime inventory is incomplete for resume.")
        endpoints = state.get("endpoints", {})
        if set(endpoints) != {"web", "api", "health", "database"}:
            raise LifecycleError(
                "Recorded runtime endpoints are incomplete for resume."
            )
        for label in ("web", "api", "health"):
            validate_loopback_url(endpoints[label], f"{label} URL")
        web_container, database_container = state["resources"]["containers"]
        web_labels = inspect_labels("containers", web_container)
        database_labels = inspect_labels("containers", database_container)
        if web_labels is None or web_labels.get("com.docker.compose.service") != "web":
            raise LifecycleError("Recorded web container role changed before resume.")
        if (
            database_labels is None
            or database_labels.get("com.docker.compose.service") != "db"
        ):
            raise LifecycleError(
                "Recorded database container role changed before resume."
            )
        assert_port_isolation(
            web_container,
            database_container,
            urlsplit(endpoints["web"]).port,
        )
    state["status"] = "RUNNING"
    state.pop("finished_at", None)
    update_state(root, state)
    return state


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Manage private ownership state for the three-tier live scenario."
    )
    result.add_argument(
        "--verification-root",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        / "sdlc-verification",
    )
    result.add_argument(
        "--expected-verification-id",
        help="Required generation fence for every mutating action except prepare/destroy.",
    )
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    phase_parser = commands.add_parser("record-phase")
    phase_parser.add_argument("--phase", required=True)
    phase_parser.add_argument("--status", choices=sorted(PHASE_STATUSES), required=True)
    phase_parser.add_argument("--summary", required=True)
    phase_parser.add_argument("--evidence", action="append", default=[])
    validation_parser = commands.add_parser("record-validation")
    validation_parser.add_argument("--validation-command", required=True)
    validation_parser.add_argument(
        "--status", choices=sorted(PHASE_STATUSES), required=True
    )
    validation_parser.add_argument("--summary", required=True)
    computer_use_parser = commands.add_parser("record-computer-use")
    computer_use_parser.add_argument(
        "--stage", choices=sorted(COMPUTER_USE_STAGES), required=True
    )
    computer_use_parser.add_argument(
        "--outcome", choices=sorted(COMPUTER_USE_OUTCOMES), required=True
    )
    computer_use_parser.add_argument("--action-attempted", action="store_true")
    computer_use_parser.add_argument(
        "--response", choices=("success", "error", "timeout"), required=True
    )
    for field in ("lock-state", "window-visible", "window-frontmost", "current-space"):
        computer_use_parser.add_argument(
            f"--{field}", choices=("yes", "no", "unknown"), required=True
        )
    computer_use_parser.add_argument("--window-marker", required=True)
    runtime_parser = commands.add_parser("record-runtime")
    runtime_parser.add_argument("--web-url", required=True)
    runtime_parser.add_argument("--api-url", required=True)
    runtime_parser.add_argument("--health-url", required=True)
    runtime_parser.add_argument("--database-endpoint", required=True)
    runtime_parser.add_argument("--web-container", required=True)
    runtime_parser.add_argument("--database-container", required=True)
    for singular in ("network", "volume", "image"):
        runtime_parser.add_argument(f"--{singular}", action="append", required=True)
    commands.add_parser("prepare-images")
    commands.add_parser("launch-browser")
    commands.add_parser("close-browser")
    compose_parser = commands.add_parser("run-compose")
    compose_parser.add_argument("compose_args", nargs=argparse.REMAINDER)
    browser_parser = commands.add_parser("record-browser")
    browser_parser.add_argument("--title", required=True)
    browser_parser.add_argument("--url", required=True)
    browser_parser.add_argument("--closed", action="store_true")
    git_parser = commands.add_parser("record-git")
    git_parser.add_argument("--baseline-sha", required=True)
    git_parser.add_argument("--promoted-sha")
    finish_parser = commands.add_parser("finish")
    finish_parser.add_argument(
        "--status", choices=sorted(FINAL_STATUSES), required=True
    )
    finish_parser.add_argument("--keep", action="store_true")
    commands.add_parser("status")
    commands.add_parser("assert-active")
    commands.add_parser("resume")
    commands.add_parser("destroy")
    return result


def main(argv: list[str]) -> int:
    arguments = parser().parse_args(argv)
    try:
        guarded = arguments.command in GENERATION_GUARDED_COMMANDS
        root = None
        guard = nullcontext()
        if guarded:
            if arguments.expected_verification_id is None:
                raise LifecycleError(
                    "This action requires --expected-verification-id from prepare."
                )
            root = verification_root(arguments.verification_root, create=False)
            guard = lifecycle_lock(root)
        with guard:
            if guarded:
                assert root is not None
                assert_active_generation(root, arguments.expected_verification_id)
            if arguments.command == "prepare":
                state = prepare(arguments.verification_root)
                print(f"Verification ID: {state['verification_id']}")
                print(f"Project root: {state['project_root']}")
                print(f"Private root: {state['private_root']}")
                print(f"Evidence root: {state['evidence_root']}")
                print(f"Compose project: {state['compose_project']}")
                print(f"Report path: {state['report_path']}")
            elif arguments.command == "record-phase":
                record_phase(
                    arguments.verification_root,
                    arguments.phase,
                    arguments.status,
                    arguments.summary,
                    arguments.evidence,
                )
            elif arguments.command == "record-validation":
                record_validation(
                    arguments.verification_root,
                    arguments.validation_command,
                    arguments.status,
                    arguments.summary,
                )
            elif arguments.command == "record-computer-use":
                record_computer_use(
                    arguments.verification_root,
                    stage=arguments.stage,
                    outcome=arguments.outcome,
                    action_attempted=arguments.action_attempted,
                    response=arguments.response,
                    lock_state=arguments.lock_state,
                    window_visible=arguments.window_visible,
                    window_frontmost=arguments.window_frontmost,
                    current_space=arguments.current_space,
                    window_marker=arguments.window_marker,
                )
            elif arguments.command == "record-runtime":
                record_runtime(
                    arguments.verification_root,
                    web_url=arguments.web_url,
                    api_url=arguments.api_url,
                    health_url=arguments.health_url,
                    database_endpoint=arguments.database_endpoint,
                    web_container=arguments.web_container,
                    database_container=arguments.database_container,
                    networks=arguments.network,
                    volumes=arguments.volume,
                    images=arguments.image,
                )
            elif arguments.command == "prepare-images":
                state = prepare_public_images(arguments.verification_root)
                print(
                    "Public base images ready: "
                    + ", ".join(state["environment"]["public_base_images"])
                )
            elif arguments.command == "launch-browser":
                state = launch_browser(arguments.verification_root)
                print(
                    "Dedicated Chrome marker: "
                    + state["browser_instance"]["window_marker"]
                )
            elif arguments.command == "close-browser":
                state = close_browser(arguments.verification_root)
                print(
                    "Dedicated Chrome status: "
                    + state["browser_instance"]["status"]
                )
            elif arguments.command == "run-compose":
                assert root is not None
                _, state = load_active(root)
                output = run_owned_compose(state, arguments.compose_args)
                if output:
                    print(output, end="" if output.endswith("\n") else "\n")
            elif arguments.command == "record-browser":
                record_browser(
                    arguments.verification_root,
                    arguments.title,
                    arguments.url,
                    arguments.closed,
                )
            elif arguments.command == "record-git":
                record_git(
                    arguments.verification_root,
                    arguments.baseline_sha,
                    arguments.promoted_sha,
                )
            elif arguments.command == "finish":
                state = finish(
                    arguments.verification_root, arguments.status, arguments.keep
                )
                print(f"Lifecycle status: {state['status']}")
                print(f"Report path: {state['report_path']}")
            elif arguments.command == "status":
                status_root = verification_root(
                    arguments.verification_root, create=False
                )
                _, state = load_active(status_root)
                print(json.dumps(state, indent=2, sort_keys=True))
            elif arguments.command == "assert-active":
                print("ACTIVE_GENERATION_CONFIRMED")
            elif arguments.command == "resume":
                state = resume(arguments.verification_root)
                print(f"Lifecycle status: {state['status']}")
                print(f"Report path: {state['report_path']}")
            else:
                result, state = destroy(arguments.verification_root)
                print(result)
                if state is not None:
                    print(f"Report path: {state['report_path']}")
        return 0
    except LifecycleError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
