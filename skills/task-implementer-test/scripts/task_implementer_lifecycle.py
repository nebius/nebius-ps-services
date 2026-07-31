#!/usr/bin/env python3
"""Own one replaceable disposable Task Implementer live-test generation."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from task_implementer_reporting import (
    LIVE_STAGE_NAMES,
    build_report,
    default_live_stages,
)


OWNER = "task-implementer-test"
OWNER_VERSION = 1
GENERATION_LABEL = "org.openai.task-implementer-test.generation"
PROJECT_LABEL = "org.openai.task-implementer-test.compose-project"
COMPOSE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,62}$")


class LifecycleError(RuntimeError):
    status = "FAIL"


class OwnershipBlockedError(LifecycleError):
    status = "OWNERSHIP_BLOCKED"


class CapabilityUnavailableError(LifecycleError):
    status = "PARTIAL"


def default_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return base / "task-implementer-test"


def _reject_symlink_components(path: Path) -> None:
    absolute = path.expanduser().absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.exists() and current.is_symlink():
            raise OwnershipBlockedError(
                f"symlinked path component is forbidden: {current}"
            )


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise LifecycleError(f"refusing symlinked state file: {path}")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise LifecycleError(f"refusing symlinked file: {path}")
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(value)
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise LifecycleError(f"missing or symlinked JSON file: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise LifecycleError(f"JSON object required: {path}")
    return value


def _canonical_generation(value: Any) -> str:
    if not isinstance(value, str):
        raise OwnershipBlockedError("generation ID must be a canonical UUID")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise OwnershipBlockedError("generation ID must be a canonical UUID") from exc
    if str(parsed) != value:
        raise OwnershipBlockedError("generation ID must be a canonical UUID")
    return value


def _set_stage(state: dict[str, Any], stage_id: str, status: str, detail: str) -> None:
    if stage_id not in LIVE_STAGE_NAMES:
        raise LifecycleError("unknown live-test stage")
    if status not in {"PASS", "PARTIAL", "FAIL", "NOT_RUN"}:
        raise LifecycleError("invalid live-test stage status")
    safe_detail = " ".join(detail.split())[:500]
    if not safe_detail:
        raise LifecycleError("live-test stage detail must not be empty")
    stages = state.get("stages")
    if not isinstance(stages, list):
        raise LifecycleError("live-test stage ledger is missing")
    for stage in stages:
        if not isinstance(stage, dict) or set(stage) != {
            "id",
            "name",
            "status",
            "detail",
        }:
            raise LifecycleError("live-test stage ledger is invalid")
        if stage["id"] != stage_id:
            continue
        previous = stage["status"]
        if previous != "NOT_RUN" and (
            previous != status or stage["detail"] != safe_detail
        ):
            raise LifecycleError("completed live-test stage is immutable")
        stage["status"] = status
        stage["detail"] = safe_detail
        return
    raise LifecycleError("live-test stage ledger is incomplete")


def _stage_status(state: dict[str, Any], stage_id: str) -> str:
    stages = state.get("stages")
    if not isinstance(stages, list):
        raise LifecycleError("live-test stage ledger is missing")
    for stage in stages:
        if isinstance(stage, dict) and stage.get("id") == stage_id:
            status_value = stage.get("status")
            if status_value in {"PASS", "PARTIAL", "FAIL", "NOT_RUN"}:
                return status_value
            break
    raise LifecycleError("live-test stage ledger is invalid")


def record_stage(
    root: Path, expected: str, stage_id: str, status_value: str, detail: str
) -> dict[str, Any]:
    with _locked_root(root) as owned:
        found = _active(owned)
        if found is None:
            raise LifecycleError("no active generation")
        state, run_path = found
        _validate_generation(state, expected)
        _set_stage(state, stage_id, status_value, detail)
        _atomic_json(run_path / "lifecycle.json", state)
        return {
            "status": "STAGE_RECORDED",
            "generation_id": expected,
            "stage": stage_id,
            "stage_status": status_value,
        }


def _ensure_root(root: Path) -> Path:
    root = root.expanduser().absolute()
    _reject_symlink_components(root)
    marker = root / "owner.json"
    if root.exists():
        if not root.is_dir() or marker.is_symlink() or not marker.is_file():
            raise OwnershipBlockedError(
                "existing private root is not owned by task-implementer-test"
            )
        try:
            owner = _load_json(marker)
        except (LifecycleError, json.JSONDecodeError) as exc:
            raise OwnershipBlockedError(
                "private root ownership marker is invalid"
            ) from exc
        if owner != {"owner": OWNER, "version": OWNER_VERSION}:
            raise OwnershipBlockedError("private root ownership marker does not match")
    else:
        root.mkdir(parents=True, mode=0o700)
        os.chmod(root, 0o700)
        _atomic_json(marker, {"owner": OWNER, "version": OWNER_VERSION})
    return root


@contextmanager
def _locked_root(root: Path) -> Iterator[Path]:
    owned = _ensure_root(root)
    lock_path = owned / ".lifecycle.lock"
    _reject_symlink_components(lock_path)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise LifecycleError(f"could not open lifecycle lock: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise LifecycleError("lifecycle lock must be one regular file")
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield owned
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _run(
    command: list[str], cwd: Path | None = None, timeout: int = 120
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if command and command[0] == "git":
        env = {key: value for key, value in env.items() if not key.startswith("GIT_")}
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = os.devnull
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise LifecycleError(f"{command[0]} timed out after {timeout} seconds") from exc
    if completed.returncode != 0:
        message = (
            completed.stderr.strip() or completed.stdout.strip() or "command failed"
        )
        raise LifecycleError(f"{command[0]} failed: {message[:500]}")
    return completed


def _active(root: Path) -> tuple[dict[str, Any], Path] | None:
    pointer_path = root / "active.json"
    deleting_path = root / "deleting.json"
    if pointer_path.exists() and deleting_path.exists():
        raise OwnershipBlockedError("active and deleting lifecycle pointers conflict")
    if not pointer_path.exists():
        if not deleting_path.exists():
            return None
        raise LifecycleError("exact cleanup deletion is pending; run destroy to retry")
    pointer = _load_json(pointer_path)
    if set(pointer) != {"generation_id", "run"}:
        raise OwnershipBlockedError("active pointer has an unknown shape")
    generation = _canonical_generation(pointer["generation_id"])
    run_path = root / "runs" / generation
    expected = f"runs/{generation}"
    _reject_symlink_components(run_path)
    if (
        pointer["run"] != expected
        or run_path.is_symlink()
        or not run_path.is_dir()
        or os.path.commonpath((str(root.resolve()), str(run_path.resolve())))
        != str(root.resolve())
    ):
        raise OwnershipBlockedError("active run path does not match its generation")
    state = _load_json(run_path / "lifecycle.json")
    if state.get("generation_id") != generation or state.get("owner") != OWNER:
        raise OwnershipBlockedError("active lifecycle ownership does not match")
    required = {
        "active": bool,
        "baseline_branch": str,
        "baseline_head": str,
        "cleanup_status": str,
        "compose_project": str,
        "generation_id": str,
        "live_started": bool,
        "outcome": str,
        "owner": str,
        "retained": bool,
        "version": int,
    }
    for key, expected_type in required.items():
        if key not in state or type(state[key]) is not expected_type:
            raise LifecycleError(f"active lifecycle field is invalid: {key}")
    if not state["active"] or state["version"] != OWNER_VERSION:
        raise OwnershipBlockedError("active lifecycle version or state is invalid")
    if not COMPOSE_RE.fullmatch(state["compose_project"]):
        raise LifecycleError("active Compose project name is invalid")
    if state.get("web_port") is not None and type(state["web_port"]) is not int:
        raise LifecycleError("active lifecycle field is invalid: web_port")
    snapshot_digest = state.get("compose_snapshot_sha256")
    if snapshot_digest is not None and (
        not isinstance(snapshot_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", snapshot_digest)
    ):
        raise LifecycleError("active Compose snapshot digest is invalid")
    return state, run_path


def _deleting_generation(root: Path) -> tuple[str, Path, Path] | None:
    pointer_path = root / "deleting.json"
    if not pointer_path.exists():
        return None
    if (root / "active.json").exists():
        raise OwnershipBlockedError("active and deleting lifecycle pointers conflict")
    pointer = _load_json(pointer_path)
    if set(pointer) != {"generation_id", "run"}:
        raise OwnershipBlockedError("deleting pointer has an unknown shape")
    generation = _canonical_generation(pointer["generation_id"])
    if pointer["run"] != f"runs/{generation}":
        raise OwnershipBlockedError(
            "deleting pointer run does not match its generation"
        )
    source = root / "runs" / generation
    tombstone = root / "deleting" / generation
    for path in (source, tombstone):
        _reject_symlink_components(path)
        if path.is_symlink():
            raise OwnershipBlockedError("cleanup deletion path must not be symlinked")
        if os.path.commonpath((str(root.resolve()), str(path.resolve()))) != str(
            root.resolve()
        ):
            raise OwnershipBlockedError(
                "cleanup deletion path escapes the private root"
            )
    archived = _load_json(root / "archive" / generation / "lifecycle.json")
    if (
        archived.get("generation_id") != generation
        or archived.get("owner") != OWNER
        or archived.get("active") is not False
        or archived.get("cleanup_status") != "PASS"
    ):
        raise OwnershipBlockedError("cleanup deletion archive proof does not match")
    return generation, source, tombstone


def _resume_deleting(root: Path) -> dict[str, Any] | None:
    pending = _deleting_generation(root)
    if pending is None:
        return None
    generation, source, tombstone = pending
    if source.exists() and tombstone.exists():
        raise OwnershipBlockedError("cleanup source and tombstone both exist")
    if source.exists():
        tombstone.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(source, tombstone)
    if tombstone.exists():
        shutil.rmtree(tombstone)
    (root / "deleting.json").unlink(missing_ok=True)
    archived = _load_json(root / "archive" / generation / "lifecycle.json")
    final_lifecycle = archived.get("final_lifecycle", "DESTROYED")
    if final_lifecycle not in {"CLEANED", "DESTROYED"}:
        raise OwnershipBlockedError("cleanup deletion lifecycle result is invalid")
    overall = archived.get("outcome")
    if overall not in {"PASS", "PARTIAL", "FAIL"}:
        overall = None
    for report_path in (
        root / "report.md",
        root / "archive" / generation / "report.md",
    ):
        _rewrite_report_lifecycle(report_path, final_lifecycle, overall=overall)
    return {"status": "DESTROYED", "generation_id": generation}


def _validate_generation(state: dict[str, Any], expected: str) -> None:
    if state.get("generation_id") != expected:
        raise OwnershipBlockedError(
            "stale generation cannot mutate the active lifecycle"
        )


def _git_value(project: Path, *arguments: str) -> str:
    return _run(["git", *arguments], cwd=project).stdout.strip()


def _validate_project(
    state: dict[str, Any], run_path: Path, *, for_destroy: bool = False
) -> Path:
    project = run_path / "project"
    if project.is_symlink() or not project.is_dir():
        raise OwnershipBlockedError("owned project is missing or symlinked")
    try:
        marker = _load_json(project / ".task-implementer-test.json")
    except (LifecycleError, json.JSONDecodeError) as exc:
        raise OwnershipBlockedError("project ownership marker is invalid") from exc
    if marker != {"generation_id": state["generation_id"], "owner": OWNER}:
        raise OwnershipBlockedError("project ownership marker does not match")
    top = _git_value(project, "rev-parse", "--show-toplevel")
    if Path(top).resolve() != project.resolve():
        raise OwnershipBlockedError(
            "project Git root does not match the owned directory"
        )
    remotes = _git_value(project, "remote")
    if remotes:
        raise OwnershipBlockedError("owned project unexpectedly has a Git remote")
    if for_destroy:
        worktrees = _git_value(project, "worktree", "list", "--porcelain")
        for line in worktrees.splitlines():
            if not line.startswith("worktree "):
                continue
            worktree = Path(line[9:]).resolve()
            if os.path.commonpath((str(run_path.resolve()), str(worktree))) != str(
                run_path.resolve()
            ):
                raise OwnershipBlockedError("linked worktree escapes the owned run")
        return project
    branch = _git_value(project, "branch", "--show-current")
    if branch != state.get("baseline_branch"):
        raise OwnershipBlockedError("owned project branch identity changed")
    head = _git_value(project, "rev-parse", "HEAD")
    _run(
        ["git", "merge-base", "--is-ancestor", state["baseline_head"], head],
        cwd=project,
    )
    return project


def _record_promoted_identity(
    state: dict[str, Any], project: Path, *, strict: bool
) -> None:
    branch = _git_value(project, "branch", "--show-current")
    head = _git_value(project, "rev-parse", "HEAD")
    if branch != state["baseline_branch"]:
        raise LifecycleError("promoted project is not on its owned named branch")
    _run(
        ["git", "merge-base", "--is-ancestor", state["baseline_head"], head],
        cwd=project,
    )
    if strict and _git_value(project, "status", "--porcelain"):
        raise LifecycleError("kept project must be clean at finish")
    if strict:
        worktrees = _git_value(project, "worktree", "list", "--porcelain")
        paths = [
            line[9:] for line in worktrees.splitlines() if line.startswith("worktree ")
        ]
        if paths != [str(project.resolve())]:
            raise LifecycleError("Task Implementer worktrees remain at finish")
    state["promoted_branch"] = branch
    state["promoted_head"] = head


def _archive(root: Path, state: dict[str, Any], run_path: Path) -> None:
    generation = _canonical_generation(state["generation_id"])
    destination = root / "archive" / generation
    _reject_symlink_components(destination)
    if os.path.commonpath((str(root.resolve()), str(destination.resolve()))) != str(
        root.resolve()
    ):
        raise LifecycleError("archive path escapes the private root")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    _atomic_json(destination / "lifecycle.json", state)
    report = run_path / "report.md"
    if report.is_file() and not report.is_symlink():
        _atomic_text(destination / "report.md", report.read_text(encoding="utf-8"))


def _rewrite_report_lifecycle(
    path: Path, lifecycle: str, *, overall: str | None = None
) -> None:
    if not path.is_file() or path.is_symlink():
        return
    content = path.read_text(encoding="utf-8")
    replacement = f"- Lifecycle: `{lifecycle}`"
    updated, count = re.subn(
        r"(?m)^- Lifecycle: `[^`]*`$", replacement, content, count=1
    )
    if count == 0:
        updated = content.rstrip() + f"\n\n## Lifecycle Result\n\n{replacement}\n"
    if overall is not None:
        updated, overall_count = re.subn(
            r"(?m)^- Overall: \*\*[^*]+\*\*$",
            f"- Overall: **{overall}**",
            updated,
            count=1,
        )
        if overall_count == 0:
            updated = updated.rstrip() + f"\n- Overall: **{overall}**\n"
    cleanup_results = {
        "CLEANED": (
            "PASS",
            "Exact owned runtime resources and the active lifecycle were removed.",
        ),
        "DESTROYED": (
            "PASS",
            "Exact owned runtime resources and the active lifecycle were removed.",
        ),
        "CLEANUP_FAILED": (
            "FAIL",
            "Exact cleanup failed and the owned active lifecycle was retained.",
        ),
    }
    if lifecycle in cleanup_results:
        status, detail = cleanup_results[lifecycle]
        stage_name = LIVE_STAGE_NAMES["cleanup"]
        clean_detail = detail.replace("|", "\\|").replace("`", "'")
        updated = re.sub(
            rf"(?m)^\| {re.escape(stage_name)} \| \*\*[^*]+\*\* \| [^\n]* \|$",
            f"| {stage_name} | **{status}** | {clean_detail} |",
            updated,
            count=1,
        )
        updated = re.sub(
            rf"(?m)^- \*\*{re.escape(stage_name)}\*\* - [^\n]*\n?",
            "",
            updated,
            count=1,
        )
        updated = re.sub(
            r"(?m)(^## Not Run\n\n)(?=\n*## Next Action$)",
            r"\1- Every recorded stage ran.\n\n",
            updated,
            count=1,
        )
        if lifecycle in {"CLEANED", "DESTROYED"}:
            passed_row = f"- **{stage_name}** - {detail}"
            if passed_row not in updated:
                updated = re.sub(
                    r"(^## Passed\n\n)",
                    rf"\1{passed_row}\n",
                    updated,
                    count=1,
                    flags=re.MULTILINE,
                )
        if lifecycle in {"CLEANED", "DESTROYED"}:
            updated = re.sub(
                r"(?m)(^## Next Action\n\n)- [^\n]*$",
                r"\1- No corrective action is required; exact cleanup is complete.",
                updated,
                count=1,
            )
        if lifecycle == "CLEANUP_FAILED":
            cleanup_row = f"| {stage_name} | **FAIL** | {clean_detail} |"
            no_failure = "- No failed or partial stage was recorded."
            if no_failure in updated:
                updated = updated.replace(
                    no_failure,
                    "| Failed or partial stage | Status | What failed or blocked |\n"
                    "| --- | --- | --- |\n"
                    f"{cleanup_row}",
                    1,
                )
            elif cleanup_row not in updated:
                updated = re.sub(
                    r"(## Failure Analysis\n\n"
                    r"\| Failed or partial stage \| Status \| What failed or blocked \|\n"
                    r"\| --- \| --- \| --- \|\n)",
                    rf"\1{cleanup_row}\n",
                    updated,
                    count=1,
                )
            updated = re.sub(
                r"(?m)(^## Next Action\n\n)- [^\n]*$",
                r"\1- Inspect the retained cleanup inventory, resolve the exact "
                "cleanup blocker, then run $task-implementer-test --destroy.",
                updated,
                count=1,
            )
        stage_block = re.search(
            r"(?s)## Stage Results\n\n(?P<body>.*?)\n\n## Failure Analysis",
            updated,
        )
        stage_statuses = (
            []
            if stage_block is None
            else re.findall(
                r"(?m)^\| [^|]+ \| \*\*(PASS|PARTIAL|FAIL|NOT_RUN)\*\* \|",
                stage_block.group("body"),
            )
        )
        if stage_statuses:
            counts = {
                value: stage_statuses.count(value) for value in ALLOWED_REPORT_STATUSES
            }
            updated = re.sub(
                r"(?m)^- Stage totals: .*?$",
                "- Stage totals: "
                f"{counts['PASS']} PASS, {counts['FAIL']} FAIL, "
                f"{counts['PARTIAL']} PARTIAL, {counts['NOT_RUN']} NOT_RUN",
                updated,
                count=1,
            )
    _atomic_text(path, updated)


ALLOWED_REPORT_STATUSES = ("PASS", "PARTIAL", "FAIL", "NOT_RUN")


def _validate_complete_report(
    path: Path, outcome: str, recorded_stages: list[dict[str, str]]
) -> None:
    content = path.read_text(encoding="utf-8")
    required_fragments = (
        "# Task Implementer Test Report",
        "## Stage Results",
        "## Passed",
        "## Failure Analysis",
        "## Not Run",
        "## Next Action",
        f"| {LIVE_STAGE_NAMES['report-generation']} | **PASS** |",
    )
    missing = [fragment for fragment in required_fragments if fragment not in content]
    missing.extend(
        f"stage:{stage_id}"
        for stage_id, name in LIVE_STAGE_NAMES.items()
        if f"| {name} | **" not in content
    )
    if missing:
        raise LifecycleError("report is incomplete or lacks the required stage matrix")
    if f"- Overall: **{outcome}**" not in content:
        raise LifecycleError("report overall status does not match finish outcome")
    stage_block = re.search(
        r"(?s)## Stage Results\n\n(?P<body>.*?)\n\n## Passed", content
    )
    if stage_block is None:
        raise LifecycleError("report stage matrix is malformed")
    rows = re.findall(
        r"(?m)^\| ([^|]+) \| \*\*(PASS|PARTIAL|FAIL|NOT_RUN)\*\* \|",
        stage_block.group("body"),
    )
    if [name.strip() for name, _status_value in rows] != list(
        LIVE_STAGE_NAMES.values()
    ):
        raise LifecycleError("report stage matrix does not match the canonical stages")
    if not isinstance(recorded_stages, list) or len(recorded_stages) != len(
        LIVE_STAGE_NAMES
    ):
        raise LifecycleError("recorded stage ledger is incomplete")
    try:
        recorded_statuses = {
            LIVE_STAGE_NAMES[stage["id"]]: stage["status"] for stage in recorded_stages
        }
    except (KeyError, TypeError) as exc:
        raise LifecycleError("recorded stage ledger is invalid") from exc
    if any(
        recorded_statuses.get(name.strip()) != status_value
        for name, status_value in rows
    ):
        raise LifecycleError("report stage statuses do not match lifecycle evidence")
    statuses = [status_value for _name, status_value in rows]
    if outcome == "FAIL" and "FAIL" not in statuses:
        raise LifecycleError("FAIL report must identify a failed stage")
    if outcome == "PARTIAL" and "PARTIAL" not in statuses:
        raise LifecycleError("PARTIAL report must identify a partial stage")
    if outcome == "PASS" and any(
        status_value != "PASS"
        for name, status_value in rows
        if name.strip() != LIVE_STAGE_NAMES["cleanup"]
    ):
        raise LifecycleError("PASS report has an incomplete or failed stage")


def _resource_ids(kind: str, label: str) -> set[str]:
    commands = {
        "container": ["docker", "ps", "-aq"],
        "volume": ["docker", "volume", "ls", "-q"],
        "network": ["docker", "network", "ls", "-q"],
        "image": ["docker", "image", "ls", "-q"],
    }
    output = _run([*commands[kind], "--filter", f"label={label}"]).stdout.splitlines()
    return {item.strip() for item in output if item.strip()}


def _resource_labels(kind: str, resource_id: str) -> dict[str, str]:
    inspected = json.loads(_run(["docker", kind, "inspect", resource_id]).stdout)
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise LifecycleError(f"Docker {kind} inspection returned an invalid shape")
    if kind in {"container", "image"}:
        labels = inspected[0].get("Config", {}).get("Labels") or {}
    else:
        labels = inspected[0].get("Labels") or {}
    if not isinstance(labels, dict):
        raise LifecycleError(f"Docker {kind} labels are invalid")
    return labels


def _owned_resource_inventory(state: dict[str, Any]) -> dict[str, list[str]]:
    generation = state["generation_id"]
    project = state["compose_project"]
    inventory: dict[str, list[str]] = {}
    for kind in ("container", "volume", "network"):
        ids = _resource_ids(kind, f"com.docker.compose.project={project}")
        ids |= _resource_ids(kind, f"{GENERATION_LABEL}={generation}")
        for resource_id in ids:
            labels = _resource_labels(kind, resource_id)
            if (
                labels.get("com.docker.compose.project") != project
                or labels.get(GENERATION_LABEL) != generation
            ):
                raise OwnershipBlockedError(
                    f"Docker {kind} ownership labels are incomplete or ambiguous"
                )
        inventory[kind] = sorted(ids)
    image_ids = _resource_ids("image", f"{PROJECT_LABEL}={project}")
    image_ids |= _resource_ids("image", f"{GENERATION_LABEL}={generation}")
    for image_id in image_ids:
        labels = _resource_labels("image", image_id)
        if (
            labels.get(PROJECT_LABEL) != project
            or labels.get(GENERATION_LABEL) != generation
        ):
            raise OwnershipBlockedError(
                "Docker image ownership labels are incomplete or ambiguous"
            )
    inventory["image"] = sorted(image_ids)
    return inventory


def _validate_live_inventory(inventory: dict[str, list[str]]) -> None:
    services = {
        _resource_labels("container", resource_id).get("com.docker.compose.service")
        for resource_id in inventory["container"]
    }
    if (
        len(inventory["container"]) != 3
        or services != {"frontend", "api", "db"}
        or len(inventory["network"]) != 1
        or len(inventory["volume"]) != 1
        or len(inventory["image"]) != 2
    ):
        raise LifecycleError(
            "live resource inventory does not match the owned three-tier stack"
        )


def _save_cleanup_ledger(
    state: dict[str, Any], run_path: Path, inventory: dict[str, list[str]]
) -> None:
    ledger = state.setdefault(
        "cleanup_ledger", {"removed": [], "already_absent": [], "remaining": {}}
    )
    ledger["remaining"] = inventory
    _atomic_json(run_path / "lifecycle.json", state)


def _remove_owned_resources(state: dict[str, Any], run_path: Path) -> None:
    ledger = state.setdefault(
        "cleanup_ledger", {"removed": [], "already_absent": [], "remaining": {}}
    )
    if (
        not isinstance(ledger, dict)
        or set(ledger) != {"removed", "already_absent", "remaining"}
        or not isinstance(ledger["removed"], list)
        or not isinstance(ledger["already_absent"], list)
        or not isinstance(ledger["remaining"], dict)
    ):
        raise LifecycleError("cleanup ledger is invalid")
    for _attempt in range(3):
        inventory = _owned_resource_inventory(state)
        _save_cleanup_ledger(state, run_path, inventory)
        if not any(inventory.values()):
            return
        commands = {
            "container": ["docker", "container", "rm", "--force"],
            "network": ["docker", "network", "rm"],
            "volume": ["docker", "volume", "rm"],
            "image": ["docker", "image", "rm"],
        }
        for kind in ("container", "network", "volume", "image"):
            for resource_id in inventory[kind]:
                entry = f"{kind}:{resource_id}"
                try:
                    _run([*commands[kind], resource_id], timeout=300)
                    if entry not in state["cleanup_ledger"]["removed"]:
                        state["cleanup_ledger"]["removed"].append(entry)
                except LifecycleError as exc:
                    observed = _owned_resource_inventory(state)
                    if resource_id in observed[kind]:
                        _save_cleanup_ledger(state, run_path, observed)
                        raise LifecycleError(
                            f"Docker {kind} removal failed and resource remains: {exc}"
                        ) from exc
                    if entry not in state["cleanup_ledger"]["already_absent"]:
                        state["cleanup_ledger"]["already_absent"].append(entry)
                _save_cleanup_ledger(state, run_path, _owned_resource_inventory(state))
    remaining = _owned_resource_inventory(state)
    _save_cleanup_ledger(state, run_path, remaining)
    if any(remaining.values()):
        raise LifecycleError("exact owned Docker resources remain after cleanup")


def _trusted_snapshot(state: dict[str, Any], run_path: Path) -> Path:
    snapshot = run_path / "compose.snapshot.json"
    expected = state.get("compose_snapshot_sha256")
    if (
        snapshot.is_symlink()
        or not snapshot.is_file()
        or not isinstance(expected, str)
        or _sha256(snapshot) != expected
    ):
        raise OwnershipBlockedError(
            "Compose snapshot is missing, symlinked, or modified"
        )
    return snapshot


def _destroy_active(
    root: Path, *, final_lifecycle: str = "DESTROYED"
) -> dict[str, Any]:
    resumed = _resume_deleting(root)
    if resumed is not None:
        return resumed
    found = _active(root)
    if found is None:
        return {"status": "ALREADY_DESTROYED"}
    state, run_path = found
    _validate_project(state, run_path, for_destroy=True)
    state["cleanup_attempted_at"] = int(time.time())
    try:
        if state.get("live_started"):
            _trusted_snapshot(state, run_path)
            _remove_owned_resources(state, run_path)
        state["cleanup_status"] = "PASS"
        _set_stage(
            state,
            "cleanup",
            "PASS",
            "Exact owned runtime resources and the active lifecycle were removed.",
        )
        _atomic_json(run_path / "lifecycle.json", state)
        final_state = dict(state)
        final_state["active"] = False
        final_state["destroyed_at"] = int(time.time())
        final_state["final_lifecycle"] = final_lifecycle
        _archive(root, final_state, run_path)
        for report_path in (
            run_path / "report.md",
            root / "report.md",
            root / "archive" / state["generation_id"] / "report.md",
        ):
            _rewrite_report_lifecycle(report_path, final_lifecycle)
        os.replace(root / "active.json", root / "deleting.json")
        tombstone = root / "deleting" / state["generation_id"]
        tombstone.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.replace(run_path, tombstone)
        shutil.rmtree(tombstone)
        try:
            (root / "deleting.json").unlink()
        except FileNotFoundError:
            pass
        return {"status": "DESTROYED", "generation_id": state["generation_id"]}
    except (LifecycleError, OSError, subprocess.SubprocessError) as exc:
        deleting_path = root / "deleting.json"
        if deleting_path.exists():
            tombstone = root / "deleting" / state["generation_id"]
            for report_path in (
                tombstone / "report.md",
                root / "report.md",
                root / "archive" / state["generation_id"] / "report.md",
            ):
                _rewrite_report_lifecycle(report_path, "CLEANUP_FAILED", overall="FAIL")
            raise LifecycleError(
                f"cleanup deletion failed; exact tombstone retained for retry: {exc}"
            ) from exc
        state["active"] = True
        state["cleanup_status"] = "FAIL"
        state["cleanup_error"] = str(exc)[:500]
        _set_stage(
            state,
            "cleanup",
            "FAIL",
            "Exact cleanup failed and the owned active lifecycle was retained.",
        )
        _atomic_json(run_path / "lifecycle.json", state)
        _archive(root, state, run_path)
        for report_path in (
            run_path / "report.md",
            root / "report.md",
            root / "archive" / state["generation_id"] / "report.md",
        ):
            _rewrite_report_lifecycle(report_path, "CLEANUP_FAILED", overall="FAIL")
        error_type = (
            OwnershipBlockedError
            if isinstance(exc, OwnershipBlockedError)
            else LifecycleError
        )
        raise error_type(f"cleanup failed; active generation retained: {exc}") from exc


def _validate_fixture(fixture: Path) -> Path:
    _reject_symlink_components(fixture)
    fixture = fixture.resolve()
    if not fixture.is_dir():
        raise LifecycleError("fixture directory is missing")
    for path in fixture.rglob("*"):
        if path.is_symlink():
            raise LifecycleError("fixture must not contain symlinks")
    if shutil.which("git") is None:
        raise CapabilityUnavailableError(
            "Git is required before replacing an active generation"
        )
    try:
        _run(["git", "--version"])
    except LifecycleError as exc:
        raise CapabilityUnavailableError(f"Git is unavailable: {exc}") from exc
    return fixture


def _copy_fixture(fixture: Path, project: Path) -> None:
    fixture = _validate_fixture(fixture)
    shutil.copytree(fixture, project)


def _validate_runtime_preflight() -> None:
    if shutil.which("docker") is None:
        raise CapabilityUnavailableError(
            "Docker is required before replacing an active generation"
        )
    try:
        _run(["docker", "version", "--format", "{{.Server.Version}}"])
        _run(["docker", "compose", "version", "--short"])
    except LifecycleError as exc:
        raise CapabilityUnavailableError(
            f"Docker Engine or Compose is unavailable: {exc}"
        ) from exc


def prepare(root: Path, fixture: Path) -> dict[str, Any]:
    fixture = _validate_fixture(fixture)
    _validate_runtime_preflight()
    with _locked_root(root) as owned:
        if _active(owned) is not None:
            _destroy_active(owned)
        generation = str(uuid.uuid4())
        compose_project = f"task-implementer-test-{generation}"
        if not COMPOSE_RE.fullmatch(compose_project):
            raise LifecycleError("generated Compose project name is invalid")
        run_path = owned / "runs" / generation
        run_path.mkdir(parents=True, mode=0o700)
        project = run_path / "project"
        try:
            _copy_fixture(fixture, project)
            _atomic_json(
                project / ".task-implementer-test.json",
                {"generation_id": generation, "owner": OWNER},
            )
            (run_path / "codex-home").mkdir(mode=0o700)
            (run_path / "evidence").mkdir(mode=0o700)
            git_template = run_path / "empty-git-template"
            git_hooks = run_path / "empty-git-hooks"
            git_template.mkdir(mode=0o700)
            git_hooks.mkdir(mode=0o700)
            _run(
                [
                    "git",
                    "init",
                    "--initial-branch",
                    "task-implementer-test",
                    f"--template={git_template}",
                ],
                cwd=project,
            )
            _run(["git", "config", "user.name", "Task Implementer Test"], cwd=project)
            _run(
                [
                    "git",
                    "config",
                    "user.email",
                    "task-implementer-test@example.invalid",
                ],
                cwd=project,
            )
            _run(["git", "config", "core.hooksPath", str(git_hooks)], cwd=project)
            _run(["git", "add", "-A"], cwd=project)
            _run(
                ["git", "commit", "-m", "Seed disposable multi-tier fixture"],
                cwd=project,
            )
            baseline = _run(["git", "rev-parse", "HEAD"], cwd=project).stdout.strip()
            state: dict[str, Any] = {
                "active": True,
                "baseline_branch": "task-implementer-test",
                "baseline_head": baseline,
                "cleanup_status": "NOT_RUN",
                "compose_project": compose_project,
                "compose_snapshot_sha256": None,
                "created_at": int(time.time()),
                "generation_id": generation,
                "live_started": False,
                "outcome": "NOT_RUN",
                "owner": OWNER,
                "retained": False,
                "stages": default_live_stages(),
                "version": OWNER_VERSION,
                "web_port": None,
            }
            _set_stage(
                state,
                "fixture-preparation",
                "PASS",
                "Owned remote-free fixture and isolated Task Implementer state were created.",
            )
            _atomic_json(run_path / "lifecycle.json", state)
            _atomic_json(
                owned / "active.json",
                {"generation_id": generation, "run": f"runs/{generation}"},
            )
            return {
                "status": "PREPARED",
                "generation_id": generation,
                "compose_project": compose_project,
                "project": str(project),
                "codex_home": str(run_path / "codex-home"),
                "evidence": str(run_path / "evidence"),
            }
        except Exception:
            if run_path.exists():
                shutil.rmtree(run_path)
            raise


def _validate_compose_model(
    model: Any,
    expected: str,
    project: Path,
    *,
    raw: bool = False,
) -> None:
    if not isinstance(model, dict):
        raise LifecycleError("Compose model must be an object")
    compose_project = f"task-implementer-test-{expected}"
    allowed_top = {"services", "networks", "volumes"}
    if not raw:
        allowed_top.add("name")
    unknown_top = set(model) - allowed_top
    if unknown_top:
        raise LifecycleError(
            f"Compose model uses forbidden top-level keys: {', '.join(sorted(unknown_top))}"
        )
    if not raw and model.get("name") != compose_project:
        raise LifecycleError("canonical Compose project name does not match")
    services = model.get("services")
    if not isinstance(services, dict) or not services:
        raise LifecycleError("Compose model has no services")
    if set(services) != {"frontend", "api", "db"}:
        raise LifecycleError("Compose services must be exactly frontend, api, and db")
    declared_networks = model.get("networks")
    declared_volumes = model.get("volumes")
    if not isinstance(declared_networks, dict) or not isinstance(
        declared_volumes, dict
    ):
        raise LifecycleError("Compose networks and volumes must use object syntax")
    if len(declared_networks) != 1 or len(declared_volumes) != 1:
        raise LifecycleError("Compose must declare exactly one network and one volume")
    project_root = project.resolve()
    for name, service in services.items():
        if not isinstance(service, dict):
            raise LifecycleError(f"service {name} has an invalid model")
        allowed_service = {
            "build",
            "depends_on",
            "environment",
            "healthcheck",
            "image",
            "labels",
            "networks",
            "ports",
            "restart",
            "volumes",
        }
        if not raw:
            # Docker Compose canonical JSON materializes absent runtime overrides
            # as null. The strict raw-source pass still forbids authors from
            # declaring either key.
            allowed_service.update({"command", "entrypoint"})
        unknown_service = set(service) - allowed_service
        if unknown_service:
            raise LifecycleError(
                f"service {name} uses forbidden keys: "
                f"{', '.join(sorted(unknown_service))}"
            )
        if not raw and any(
            service.get(key) is not None for key in ("command", "entrypoint")
        ):
            raise LifecycleError(f"service {name} has a canonical runtime override")
        labels = service.get("labels") or {}
        if not isinstance(labels, dict):
            raise LifecycleError(f"service {name} labels must use object syntax")
        if labels != {GENERATION_LABEL: expected}:
            raise LifecycleError(f"service {name} lacks the exact generation label")
        environment = service.get("environment", {})
        if not isinstance(environment, dict) or not all(
            isinstance(key, str)
            and isinstance(value, (str, int, float, bool, type(None)))
            for key, value in environment.items()
        ):
            raise LifecycleError(f"service {name} environment must be a scalar map")
        restart = service.get("restart")
        if restart not in {None, "no", "on-failure", "unless-stopped"}:
            raise LifecycleError(f"service {name} has an unsupported restart policy")
        depends_on = service.get("depends_on", {})
        if isinstance(depends_on, list):
            if not all(isinstance(dependency, str) for dependency in depends_on):
                raise LifecycleError(f"service {name} dependencies are invalid")
            dependency_names = set(depends_on)
        elif isinstance(depends_on, dict):
            dependency_names = set(depends_on)
            for dependency, condition in depends_on.items():
                if not isinstance(dependency, str) or not isinstance(
                    condition, (dict, str, type(None))
                ):
                    raise LifecycleError(
                        f"service {name} has invalid dependency metadata"
                    )
                if isinstance(condition, dict) and set(condition) - {
                    "condition",
                    "required",
                    "restart",
                }:
                    raise LifecycleError(
                        f"service {name} has unsupported dependency options"
                    )
        else:
            raise LifecycleError(f"service {name} dependencies are invalid")
        if not dependency_names.issubset(services) or name in dependency_names:
            raise LifecycleError(f"service {name} has an invalid dependency target")
        healthcheck = service.get("healthcheck")
        if healthcheck is not None:
            if not isinstance(healthcheck, dict) or set(healthcheck) - {
                "disable",
                "interval",
                "retries",
                "start_interval",
                "start_period",
                "test",
                "timeout",
            }:
                raise LifecycleError(f"service {name} healthcheck is invalid")
            test = healthcheck.get("test")
            if test is not None and (
                not isinstance(test, list)
                or not test
                or test[0] not in {"CMD", "CMD-SHELL", "NONE"}
                or not all(isinstance(part, str) for part in test)
            ):
                raise LifecycleError(f"service {name} healthcheck test is invalid")
        build = service.get("build")
        if name in {"frontend", "api"} and (
            not isinstance(build, dict) or service.get("image")
        ):
            raise LifecycleError(
                f"service {name} must build without an explicit shared image tag"
            )
        if name == "db" and (
            build is not None or service.get("image") != "postgres:16-alpine"
        ):
            raise LifecycleError(
                "db must use exactly the verifier-approved postgres:16-alpine image"
            )
        if build is not None and not isinstance(build, dict):
            raise LifecycleError(f"service {name} build must use long-form syntax")
        if isinstance(build, dict):
            if set(build) != {"context", "dockerfile", "labels"}:
                raise LifecycleError(
                    f"service {name} build must use only context, dockerfile, and labels"
                )
            build_labels = build.get("labels") or {}
            if not isinstance(build_labels, dict):
                raise LifecycleError(
                    f"service {name} build labels must use object syntax"
                )
            if build_labels != {
                GENERATION_LABEL: expected,
                PROJECT_LABEL: compose_project,
            }:
                raise LifecycleError(
                    f"service {name} build lacks exact dual image ownership labels"
                )
            context = Path(str(build.get("context", "")))
            if not context.is_absolute():
                context = project_root / context
            context = context.resolve()
            if os.path.commonpath((str(project_root), str(context))) != str(
                project_root
            ):
                raise LifecycleError(
                    f"service {name} build context escapes the project"
                )
            if not context.is_dir():
                raise LifecycleError(f"service {name} build context is missing")
            dockerfile = Path(str(build.get("dockerfile", "Dockerfile")))
            if not dockerfile.is_absolute():
                dockerfile = context / dockerfile
            dockerfile = dockerfile.resolve()
            if (
                os.path.commonpath((str(context), str(dockerfile))) != str(context)
                or not dockerfile.is_file()
            ):
                raise LifecycleError(
                    f"service {name} Dockerfile is missing or escapes its build context"
                )
        mounts = service.get("volumes") or []
        if not isinstance(mounts, list):
            raise LifecycleError(f"service {name} volumes must use long-form syntax")
        if name != "db" and mounts:
            raise LifecycleError(f"service {name} must not mount a volume")
        for mount in mounts:
            if not isinstance(mount, dict) or mount.get("type") != "volume":
                raise LifecycleError(f"service {name} uses a non-volume host mount")
            if set(mount) - {"read_only", "source", "target", "type", "volume"}:
                raise LifecycleError(f"service {name} uses unsupported mount options")
            if mount.get("source") not in declared_volumes:
                raise LifecycleError(f"service {name} uses an undeclared volume")
            target = mount.get("target")
            if (
                not isinstance(target, str)
                or not target.startswith("/")
                or ".." in Path(target).parts
            ):
                raise LifecycleError(f"service {name} has an invalid volume target")
            volume_options = mount.get("volume")
            if volume_options is not None and volume_options != {}:
                raise LifecycleError(f"service {name} has unsupported volume options")
        networks = service.get("networks") or {}
        if not isinstance(networks, dict) or not networks:
            raise LifecycleError(f"service {name} must use an owned named network")
        if set(networks) != set(declared_networks):
            raise LifecycleError(f"service {name} uses an undeclared network")
        if any(value not in ({}, None) for value in networks.values()):
            raise LifecycleError(f"service {name} uses unsupported network options")
        ports = service.get("ports") or []
        if not isinstance(ports, list):
            raise LifecycleError(f"service {name} ports must use long-form syntax")
        if name == "frontend" and len(ports) != 1:
            raise LifecycleError("frontend must publish exactly one loopback port")
        if name != "frontend" and ports:
            raise LifecycleError(f"service {name} must not publish a host port")
        if name == "db" and not mounts:
            raise LifecycleError("db must use an owned named volume")
        for port in ports:
            if not isinstance(port, dict):
                raise LifecycleError(f"service {name} has an invalid published port")
            if set(port) - {"host_ip", "mode", "protocol", "published", "target"}:
                raise LifecycleError(f"service {name} uses unsupported port options")
            try:
                target_port = int(port.get("target", 0))
            except (TypeError, ValueError) as exc:
                raise LifecycleError(
                    f"service {name} has an invalid target port"
                ) from exc
            if target_port == 5432:
                raise LifecycleError("PostgreSQL must not be host-published")
            if port.get("host_ip") != "127.0.0.1":
                raise LifecycleError("published ports must bind exactly to loopback")
            if name == "frontend" and target_port != 80:
                raise LifecycleError("frontend must publish container port 80")
            if name == "frontend" and port.get("published") not in {None, ""}:
                raise LifecycleError("frontend must use a Docker-assigned host port")
    for group_name in ("networks", "volumes"):
        group = model.get(group_name) or {}
        if not isinstance(group, dict) or not group:
            raise LifecycleError(f"Compose model must declare owned {group_name}")
        for name, item in group.items():
            if not isinstance(item, dict):
                raise LifecycleError(f"{group_name[:-1]} {name} has an invalid model")
            allowed_item = {"labels"} if raw else {"labels", "name"}
            if not raw and group_name == "networks":
                allowed_item.add("ipam")
            if set(item) - allowed_item:
                raise LifecycleError(f"{group_name[:-1]} {name} uses forbidden options")
            if not raw and group_name == "networks" and item.get("ipam", {}) != {}:
                raise LifecycleError(f"network {name} has canonical IPAM overrides")
            labels = item.get("labels") or {}
            if not isinstance(labels, dict) or labels != {GENERATION_LABEL: expected}:
                raise LifecycleError(
                    f"{group_name[:-1]} {name} lacks the generation label"
                )
            if not raw and item.get("name") != f"{compose_project}_{name}":
                raise LifecycleError(
                    f"canonical {group_name[:-1]} {name} has an unexpected name"
                )


def _compose_command(
    state: dict[str, Any], run_path: Path, project: Path, *arguments: str
) -> list[str]:
    return [
        "docker",
        "compose",
        "-p",
        state["compose_project"],
        "--project-directory",
        str(project),
        "-f",
        str(run_path / "compose.snapshot.json"),
        *arguments,
    ]


def validate_compose(root: Path, expected: str) -> dict[str, Any]:
    with _locked_root(root) as owned:
        found = _active(owned)
        if found is None:
            raise LifecycleError("no active generation")
        state, run_path = found
        _validate_generation(state, expected)
        if state["live_started"]:
            raise LifecycleError("cannot replace the Compose snapshot after live start")
        project = _validate_project(state, run_path)
        for candidate in project.rglob("*"):
            if candidate.is_symlink():
                raise LifecycleError("project symlinks are forbidden before Docker")
        compose = project / "compose.yaml"
        if compose.is_symlink() or not compose.is_file():
            raise LifecycleError("compose.yaml is missing or symlinked")
        compose_text = compose.read_text(encoding="utf-8")
        if "${" in compose_text:
            raise LifecycleError("Compose environment interpolation is forbidden")
        try:
            raw_model = json.loads(compose_text)
        except json.JSONDecodeError as exc:
            raise LifecycleError("compose.yaml must use strict JSON syntax") from exc
        _validate_compose_model(
            raw_model,
            expected,
            project,
            raw=True,
        )
        empty_env = run_path / "empty.env"
        _atomic_text(empty_env, "")
        completed = _run(
            [
                "docker",
                "compose",
                "--env-file",
                str(empty_env),
                "-p",
                state["compose_project"],
                "--project-directory",
                str(project),
                "-f",
                str(compose),
                "config",
                "--no-env-resolution",
                "--no-path-resolution",
                "--format",
                "json",
            ],
            cwd=project,
        )
        model = json.loads(completed.stdout)
        _validate_compose_model(model, expected, project)
        snapshot = run_path / "compose.snapshot.json"
        _atomic_text(snapshot, completed.stdout)
        state["compose_snapshot_sha256"] = _sha256(snapshot)
        state["compose_validated_at"] = int(time.time())
        _set_stage(
            state,
            "compose-validation",
            "PASS",
            "The strict allowlisted Compose model passed canonical validation.",
        )
        _atomic_json(run_path / "lifecycle.json", state)
        return {"status": "COMPOSE_VALID", "generation_id": expected}


def compose_up(root: Path, expected: str) -> dict[str, Any]:
    with _locked_root(root) as owned:
        found = _active(owned)
        if found is None:
            raise LifecycleError("no active generation")
        state, run_path = found
        _validate_generation(state, expected)
        if state["live_started"]:
            raise LifecycleError("compose-up is single-attempt; clean and create again")
        _trusted_snapshot(state, run_path)
        project = _validate_project(state, run_path)
        if any(_owned_resource_inventory(state).values()):
            raise LifecycleError(
                "owned Docker resources already exist before compose-up"
            )
        state["live_started"] = True
        state["live_marked_at"] = int(time.time())
        _atomic_json(run_path / "lifecycle.json", state)
        try:
            _run(
                _compose_command(state, run_path, project, "up", "--build", "--detach"),
                cwd=project,
                timeout=900,
            )
            published = _run(
                _compose_command(state, run_path, project, "port", "frontend", "80"),
                cwd=project,
            ).stdout.strip()
            match = re.fullmatch(r"127\.0\.0\.1:([0-9]{1,5})", published)
            if match is None or not 1 <= int(match.group(1)) <= 65535:
                raise LifecycleError("Docker returned an invalid frontend port")
            state["web_port"] = int(match.group(1))
            inventory = _owned_resource_inventory(state)
            _validate_live_inventory(inventory)
            state["live_inventory"] = inventory
            _set_stage(
                state,
                "runtime-launch",
                "PASS",
                "The exactly owned three-tier runtime started on loopback.",
            )
            _atomic_json(run_path / "lifecycle.json", state)
        except LifecycleError as exc:
            state["live_error"] = str(exc)[:500]
            _atomic_json(run_path / "lifecycle.json", state)
            raise
        return {
            "status": "LIVE_STARTED",
            "generation_id": expected,
            "web_port": state["web_port"],
        }


def _active_live(root: Path, expected: str) -> tuple[dict[str, Any], Path, Path]:
    found = _active(root)
    if found is None:
        raise LifecycleError("no active generation")
    state, run_path = found
    _validate_generation(state, expected)
    if not state["live_started"]:
        raise LifecycleError("compose-up must succeed before live inspection")
    _trusted_snapshot(state, run_path)
    project = _validate_project(state, run_path)
    return state, run_path, project


def _parse_compose_ps(output: str) -> list[dict[str, Any]]:
    stripped = output.strip()
    if not stripped:
        return []
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        records = [json.loads(line) for line in stripped.splitlines() if line.strip()]
    else:
        records = value if isinstance(value, list) else [value]
    if not all(isinstance(record, dict) for record in records):
        raise LifecycleError("Compose ps JSON records have an invalid shape")
    return records


def _compose_ps_active(
    state: dict[str, Any], run_path: Path, project: Path
) -> dict[str, Any]:
    output = _run(
        _compose_command(state, run_path, project, "ps", "--all", "--format", "json"),
        cwd=project,
    ).stdout
    return {"status": "LIVE_INSPECTED", "services": _parse_compose_ps(output)}


def compose_ps(root: Path, expected: str) -> dict[str, Any]:
    with _locked_root(root) as owned:
        state, run_path, project = _active_live(owned, expected)
        return _compose_ps_active(state, run_path, project)


def _restart_api_active(
    state: dict[str, Any], run_path: Path, project: Path
) -> dict[str, Any]:
    _run(_compose_command(state, run_path, project, "restart", "api"), cwd=project)
    return {"status": "API_RESTARTED", "generation_id": state["generation_id"]}


def restart_api(root: Path, expected: str) -> dict[str, Any]:
    with _locked_root(root) as owned:
        state, run_path, project = _active_live(owned, expected)
        return _restart_api_active(state, run_path, project)


def _database_probe_active(
    state: dict[str, Any], run_path: Path, project: Path, task_id: int
) -> dict[str, Any]:
    query = f"SELECT id,title,completed FROM tasks WHERE id = {task_id:d}"
    output = _run(
        _compose_command(
            state,
            run_path,
            project,
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "task_test",
            "-d",
            "task_test",
            "-At",
            "-F",
            "\t",
            "-c",
            query,
        ),
        cwd=project,
    ).stdout.strip()
    fields = output.split("\t")
    if len(fields) != 3 or fields[0] != str(task_id):
        raise LifecycleError("database evidence did not match the requested task")
    return {
        "status": "DATABASE_VERIFIED",
        "task_id": task_id,
        "title": fields[1],
        "completed": fields[2] in {"t", "true", "1"},
    }


def database_probe(root: Path, expected: str, task_id: int) -> dict[str, Any]:
    if task_id < 1:
        raise LifecycleError("task ID must be positive")
    with _locked_root(root) as owned:
        state, run_path, project = _active_live(owned, expected)
        return _database_probe_active(state, run_path, project, task_id)


def collect_application(root: Path, expected: str) -> dict[str, Any]:
    from collect_live_evidence import collect

    with _locked_root(root) as owned:
        state, run_path, project = _active_live(owned, expected)
        evidence = collect(
            owned,
            expected,
            current={
                "generation_id": state["generation_id"],
                "web_port": state["web_port"],
            },
            compose_ps_fn=lambda _root, _generation: _compose_ps_active(
                state, run_path, project
            ),
            database_probe_fn=lambda _root, _generation, task_id: (
                _database_probe_active(state, run_path, project, task_id)
            ),
            restart_api_fn=lambda _root, _generation: _restart_api_active(
                state, run_path, project
            ),
        )
        inventory = _owned_resource_inventory(state)
        _validate_live_inventory(inventory)
        output = run_path / "evidence" / "application.json"
        _atomic_json(output, evidence)
        digest = _sha256(output)
        state["application_evidence_sha256"] = digest
        state["application_evidence_at"] = int(time.time())
        _set_stage(
            state,
            "application-semantics",
            "PASS",
            "Frontend, API CRUD, database correlation, and restart persistence passed.",
        )
        _atomic_json(run_path / "lifecycle.json", state)
        return {
            "status": "APPLICATION_VERIFIED",
            "generation_id": expected,
            "artifact": str(output),
            "sha256": digest,
        }


def validate_results(
    root: Path,
    expected: str,
    manifest: Path,
    run_dir: Path,
    managed_prompt: Path,
    task_implementer_scripts: Path,
) -> dict[str, Any]:
    from task_implementer_semantics import validate_results as validate_semantics

    with _locked_root(root) as owned:
        found = _active(owned)
        if found is None:
            raise LifecycleError("no active generation")
        state, run_path = found
        _validate_generation(state, expected)
        project = _validate_project(state, run_path)
        expected_manifest = run_path / "evidence" / "live-results.json"
        manifest = manifest.expanduser().absolute()
        if (
            manifest.is_symlink()
            or not manifest.is_file()
            or manifest.resolve() != expected_manifest.resolve()
        ):
            raise LifecycleError(
                "semantic manifest must be the active evidence/live-results.json"
            )
        private_home = (run_path / "codex-home").resolve()
        for label, path in (("run", run_dir), ("managed prompt", managed_prompt)):
            _reject_symlink_components(path)
            resolved = path.expanduser().absolute().resolve()
            if path.is_symlink() or os.path.commonpath(
                (str(private_home), str(resolved))
            ) != str(private_home):
                raise OwnershipBlockedError(
                    f"Task Implementer {label} escapes isolated state"
                )
        scripts = task_implementer_scripts.expanduser().absolute()
        _reject_symlink_components(scripts)
        if scripts.is_symlink() or not scripts.is_dir():
            raise LifecycleError("Task Implementer scripts directory is invalid")
        data = _load_json(manifest)
        errors = validate_semantics(
            data,
            run_path / "evidence",
            expected,
            project_root=project,
            run_dir=run_dir,
            task_implementer_scripts=scripts,
            managed_prompt=managed_prompt,
            lifecycle_state=run_path / "lifecycle.json",
        )
        if errors:
            raise LifecycleError(f"semantic evidence failed: {'; '.join(errors)[:500]}")
        if data.get("status") != "PASS":
            raise LifecycleError("canonical live results must have PASS status")
        state["semantic_status"] = "PASS"
        state["semantic_results_sha256"] = _sha256(manifest)
        state["semantic_project_head"] = data["project_head"]
        state["semantic_validated_at"] = int(time.time())
        _set_stage(
            state,
            "results-validation",
            "PASS",
            "Canonical Task Implementer, Git, application, and lifecycle evidence passed.",
        )
        _atomic_json(run_path / "lifecycle.json", state)
        return {
            "status": "RESULTS_VALID",
            "generation_id": expected,
            "sha256": state["semantic_results_sha256"],
        }


def finish(
    root: Path,
    expected: str,
    outcome: str,
    keep: bool,
    report: Path | None,
    reason: str = "",
    failed_stage: str | None = None,
) -> dict[str, Any]:
    if outcome not in {"PASS", "PARTIAL", "FAIL"}:
        raise LifecycleError("outcome must be PASS, PARTIAL, or FAIL")
    with _locked_root(root) as owned:
        found = _active(owned)
        if found is None:
            raise LifecycleError("no active generation")
        state, run_path = found
        _validate_generation(state, expected)
        if outcome == "PASS":
            project = _validate_project(state, run_path)
            _record_promoted_identity(state, project, strict=True)
        elif keep:
            _validate_project(state, run_path, for_destroy=True)
        if outcome == "PASS" and (
            state.get("semantic_status") != "PASS"
            or state.get("application_evidence_sha256") is None
            or state.get("semantic_project_head") != state.get("promoted_head")
        ):
            raise LifecycleError(
                "PASS requires generation-fenced canonical semantic validation"
            )
        canonical_report = run_path / "report.md"
        if report is None:
            if outcome == "PASS":
                raise LifecycleError("PASS requires a complete report")
            safe_reason = " ".join(reason.split())[:500] or "report creation failed"
            failed_stages = [
                stage
                for stage in state["stages"]
                if stage["status"] in {"FAIL", "PARTIAL"}
            ]
            if failed_stage is not None:
                failed_stage_status = _stage_status(state, failed_stage)
                if failed_stage_status == "NOT_RUN":
                    _set_stage(state, failed_stage, outcome, safe_reason)
                elif failed_stage_status != outcome:
                    raise LifecycleError(
                        "failed finish conflicts with the recorded failed stage"
                    )
            elif not failed_stages:
                raise LifecycleError(
                    "failed finish without a report requires --failed-stage"
                )
            report_stage_status = _stage_status(state, "report-generation")
            if report_stage_status == "NOT_RUN":
                _set_stage(
                    state,
                    "report-generation",
                    "PASS",
                    "The lifecycle helper generated the sanitized stage report.",
                )
            elif report_stage_status != "PASS":
                raise LifecycleError("fallback report conflicts with the stage ledger")
            deterministic = next(
                (
                    stage["status"]
                    for stage in state["stages"]
                    if stage["id"] == "deterministic-verification"
                ),
                "NOT_RUN",
            )
            _atomic_text(
                canonical_report,
                build_report(
                    {
                        "mode": f"create{' --keep' if keep else ''}",
                        "overall": outcome,
                        "deterministic": deterministic,
                        "live": outcome,
                        "lifecycle": "RETAINED" if keep else "CLEANUP_PENDING",
                        "report_path": str(owned / "report.md"),
                        "stages": state["stages"],
                        "next_action": (
                            f"Resolve the recorded {failed_stage or 'failed'} stage, then rerun "
                            "$task-implementer-test --create."
                        ),
                    }
                ),
            )
        else:
            report = report.expanduser().absolute()
            if report.is_symlink() or not report.is_file():
                raise LifecycleError("report is missing or symlinked")
            if os.path.commonpath(
                (str(run_path.resolve()), str(report.resolve()))
            ) != str(run_path.resolve()):
                raise LifecycleError("report must be inside the active run")
            if report.resolve() != canonical_report.resolve():
                _atomic_text(canonical_report, report.read_text(encoding="utf-8"))
            report_stage_status = _stage_status(state, "report-generation")
            if report_stage_status == "NOT_RUN":
                _set_stage(
                    state,
                    "report-generation",
                    "PASS",
                    "The complete sanitized stage report was generated.",
                )
            elif report_stage_status != "PASS":
                raise LifecycleError("complete report conflicts with the stage ledger")
            _validate_complete_report(canonical_report, outcome, state["stages"])
        _atomic_text(owned / "report.md", canonical_report.read_text(encoding="utf-8"))
        state["outcome"] = outcome
        state["retained"] = keep
        state["finished_at"] = int(time.time())
        _atomic_json(run_path / "lifecycle.json", state)
        _archive(owned, state, run_path)
        if keep:
            for report_path in (
                canonical_report,
                owned / "report.md",
                owned / "archive" / expected / "report.md",
            ):
                _rewrite_report_lifecycle(report_path, "RETAINED")
            return {"status": "KEPT", "generation_id": expected, "outcome": outcome}
        destroyed = _destroy_active(owned, final_lifecycle="CLEANED")
        return {
            "status": "CLEANED",
            "generation_id": expected,
            "outcome": outcome,
            "cleanup": destroyed["status"],
        }


def status(root: Path) -> dict[str, Any]:
    with _locked_root(root) as owned:
        pending = _deleting_generation(owned)
        if pending is not None:
            return {
                "status": "CLEANUP_PENDING",
                "generation_id": pending[0],
            }
        found = _active(owned)
        if found is None:
            return {"status": "ALREADY_DESTROYED"}
        state, run_path = found
        _validate_project(
            state,
            run_path,
            for_destroy=state["outcome"] in {"PARTIAL", "FAIL"},
        )
        return {
            "status": "ACTIVE",
            "generation_id": state["generation_id"],
            "outcome": state["outcome"],
            "retained": state["retained"],
            "project": str(run_path / "project"),
            "compose_project": state["compose_project"],
            "web_port": state["web_port"],
        }


def destroy(root: Path) -> dict[str, Any]:
    with _locked_root(root) as owned:
        return _destroy_active(owned)


def main() -> int:
    skill_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=default_root(), help=argparse.SUPPRESS
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument(
        "--fixture", type=Path, default=skill_root / "assets" / "multi-tier-fixture"
    )
    for name in (
        "validate-compose",
        "compose-up",
        "compose-ps",
        "restart-api",
        "collect-application",
    ):
        child = subparsers.add_parser(name)
        child.add_argument("--expected-generation", required=True)
    stage_parser = subparsers.add_parser("record-stage")
    stage_parser.add_argument("--expected-generation", required=True)
    stage_parser.add_argument("--stage", choices=tuple(LIVE_STAGE_NAMES), required=True)
    stage_parser.add_argument(
        "--status", choices=("PASS", "PARTIAL", "FAIL", "NOT_RUN"), required=True
    )
    stage_parser.add_argument("--detail", required=True)
    database_parser = subparsers.add_parser("database-probe")
    database_parser.add_argument("--expected-generation", required=True)
    database_parser.add_argument("--task-id", type=int, required=True)
    results_parser = subparsers.add_parser("validate-results")
    results_parser.add_argument("--expected-generation", required=True)
    results_parser.add_argument("--manifest", type=Path, required=True)
    results_parser.add_argument("--run-dir", type=Path, required=True)
    results_parser.add_argument("--managed-prompt", type=Path, required=True)
    results_parser.add_argument("--task-implementer-scripts", type=Path, required=True)
    finish_parser = subparsers.add_parser("finish")
    finish_parser.add_argument("--expected-generation", required=True)
    finish_parser.add_argument(
        "--outcome", choices=("PASS", "PARTIAL", "FAIL"), required=True
    )
    finish_parser.add_argument("--keep", action="store_true")
    finish_parser.add_argument("--report", type=Path)
    finish_parser.add_argument("--reason", default="")
    finish_parser.add_argument("--failed-stage", choices=tuple(LIVE_STAGE_NAMES))
    subparsers.add_parser("status")
    subparsers.add_parser("destroy")
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            result = prepare(args.root, args.fixture)
        elif args.action == "validate-compose":
            result = validate_compose(args.root, args.expected_generation)
        elif args.action == "compose-up":
            result = compose_up(args.root, args.expected_generation)
        elif args.action == "compose-ps":
            result = compose_ps(args.root, args.expected_generation)
        elif args.action == "restart-api":
            result = restart_api(args.root, args.expected_generation)
        elif args.action == "database-probe":
            result = database_probe(args.root, args.expected_generation, args.task_id)
        elif args.action == "collect-application":
            result = collect_application(args.root, args.expected_generation)
        elif args.action == "validate-results":
            result = validate_results(
                args.root,
                args.expected_generation,
                args.manifest,
                args.run_dir,
                args.managed_prompt,
                args.task_implementer_scripts,
            )
        elif args.action == "record-stage":
            result = record_stage(
                args.root,
                args.expected_generation,
                args.stage,
                args.status,
                args.detail,
            )
        elif args.action == "finish":
            result = finish(
                args.root,
                args.expected_generation,
                args.outcome,
                args.keep,
                args.report,
                args.reason,
                args.failed_stage,
            )
        elif args.action == "status":
            result = status(args.root)
        else:
            result = destroy(args.root)
    except (
        LifecycleError,
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        error_status = getattr(exc, "status", "FAIL")
        print(
            json.dumps(
                {"status": error_status, "error": str(exc)[:500]}, sort_keys=True
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
