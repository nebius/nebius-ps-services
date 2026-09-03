#!/usr/bin/env python3
"""Deterministic completion evidence and read-only lane visibility."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from typing import Iterable

from prompt_workspace_core import (
    PromptWorkspaceError,
    RUN_ID_RE,
    RUN_SCHEMA,
    TERMINAL_RUN_STATUSES,
    load_json_object,
    read_only_git_environment,
    require_mode,
    required_string,
    stable_json,
    write_atomic,
    write_exclusive,
)
from prompt_workspace_execution import (
    SHA_RE,
    load_coordinator_state,
    orchestration_dir,
    sha256_json,
)


SUMMARY_SCHEMA = "task-implementer/run-summary-v1"
SUMMARY_STATE_SCHEMA = "task-implementer/run-summary-state-v1"
SOURCE_OBSERVATION_SCHEMA = "task-implementer/source-observation-v1"
PREPARED_NAME = "run-summary.prepared.json"
SEALED_NAME = "run-summary.json"
STATE_NAME = "run-summary-state.json"
SOURCE_OBSERVATION_NAME = "source-observation.json"
GIT_TIMEOUT_SECONDS = 30
GIT_OUTPUT_LIMIT = 8 * 1024 * 1024
DIFF_RENAME_LIMIT = 32767
SUMMARY_BYTES_LIMIT = 8 * 1024 * 1024
LANE_REPORT_BYTES_LIMIT = 16 * 1024 * 1024
PENDING_GENERATIONS_LIMIT = 1024
LANE_REPORT_SCHEMA = "task-implementer/lane-report-v2"
LANE_REPORT_MAX_STATE_FILES = 4096
LANE_REPORT_HUMAN_BYTES_LIMIT = 4096
_DISCOVERED_GIT = shutil.which("git")
GIT_EXECUTABLE = (
    Path(_DISCOVERED_GIT).resolve() if _DISCOVERED_GIT is not None else None
)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _summary_path(run_dir: Path, name: str) -> Path:
    return orchestration_dir(run_dir) / name


def _terminate_git_process(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    process.wait()


def _git_bytes(
    repo: Path,
    arguments: list[str],
    description: str,
    *,
    check: bool = True,
) -> tuple[int, bytes]:
    """Run bounded Git without inheriting external diff or text conversion."""

    if (
        GIT_EXECUTABLE is None
        or not GIT_EXECUTABLE.is_absolute()
        or not GIT_EXECUTABLE.is_file()
        or not os.access(GIT_EXECUTABLE, os.X_OK)
    ):
        raise PromptWorkspaceError("ENVIRONMENT_BLOCKER", "Git is unavailable")
    command = [
        str(GIT_EXECUTABLE),
        "--no-optional-locks",
        "-c",
        "core.quotePath=false",
        "-c",
        "diff.external=",
        "-c",
        "diff.renames=copies",
        "-c",
        f"diff.renameLimit={DIFF_RENAME_LIMIT}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.preloadIndex=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "maintenance.auto=false",
        "-c",
        "gc.auto=0",
        "-C",
        str(repo),
        *arguments,
    ]
    environment = read_only_git_environment()
    try:
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                command,
                stdout=output,
                stderr=errors,
                env=environment,
                start_new_session=True,
            )
            deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
            while process.poll() is None:
                size = output.tell() + errors.tell()
                if size > GIT_OUTPUT_LIMIT:
                    _terminate_git_process(process)
                    raise PromptWorkspaceError(
                        "GIT_REPORT_FAILED",
                        f"Git output exceeded the reporting bound while trying to {description}",
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_git_process(process)
                    raise PromptWorkspaceError(
                        "GIT_REPORT_FAILED",
                        f"Git timed out while trying to {description}",
                    )
                time.sleep(min(0.01, remaining))
            returncode = int(process.returncode)
            size = output.tell() + errors.tell()
            if size > GIT_OUTPUT_LIMIT:
                raise PromptWorkspaceError(
                    "GIT_REPORT_FAILED",
                    f"Git output exceeded the reporting bound while trying to {description}",
                )
            output.seek(0)
            raw = output.read(GIT_OUTPUT_LIMIT + 1)
    except OSError as error:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", f"Git could not {description}"
        ) from error
    if check and returncode != 0:
        raise PromptWorkspaceError("GIT_REPORT_FAILED", f"Git could not {description}")
    return returncode, raw


def _git_text(repo: Path, arguments: list[str], description: str) -> str:
    return (
        _git_bytes(repo, arguments, description)[1]
        .decode("utf-8", errors="strict")
        .strip()
    )


def _commit(repo: Path, value: object, label: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise PromptWorkspaceError("GIT_REPORT_FAILED", f"{label} is not a commit")
    status, _ = _git_bytes(
        repo,
        ["cat-file", "-e", f"{value}^{{commit}}"],
        f"validate {label}",
        check=False,
    )
    if status != 0:
        raise PromptWorkspaceError("GIT_REPORT_FAILED", f"{label} is not a commit")
    return value


def _diff_arguments(left: str, right: str, mode: str) -> list[str]:
    return [
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--diff-algorithm=myers",
        "--find-renames=50%",
        "--find-copies=50%",
        "--find-copies-harder",
        mode,
        "-z",
        left,
        right,
        "--",
    ]


def _name_status(raw: bytes) -> list[tuple[str, tuple[bytes, ...]]]:
    values = raw.split(b"\0")
    if values and values[-1] == b"":
        values.pop()
    result: list[tuple[str, tuple[bytes, ...]]] = []
    index = 0
    while index < len(values):
        token = values[index]
        index += 1
        if not token:
            raise PromptWorkspaceError("GIT_REPORT_FAILED", "Git diff status is empty")
        status = chr(token[0])
        if status not in {"A", "M", "D", "R", "C", "T"}:
            raise PromptWorkspaceError(
                "GIT_REPORT_FAILED", "Git diff contains an unsupported file status"
            )
        path_count = 2 if status in {"R", "C"} else 1
        if index + path_count > len(values):
            raise PromptWorkspaceError(
                "GIT_REPORT_FAILED", "Git diff status output is truncated"
            )
        paths = tuple(values[index : index + path_count])
        index += path_count
        if any(not path for path in paths):
            raise PromptWorkspaceError("GIT_REPORT_FAILED", "Git diff path is empty")
        result.append((status, paths))
    return result


def _numstat(raw: bytes) -> list[tuple[int, int, bool]]:
    values = raw.split(b"\0")
    if values and values[-1] == b"":
        values.pop()
    result: list[tuple[int, int, bool]] = []
    index = 0
    while index < len(values):
        record = values[index]
        index += 1
        fields = record.split(b"\t", 2)
        if len(fields) != 3:
            raise PromptWorkspaceError("GIT_REPORT_FAILED", "Git numstat is invalid")
        added, deleted, path = fields
        if path == b"":
            if index + 2 > len(values):
                raise PromptWorkspaceError(
                    "GIT_REPORT_FAILED", "Git renamed numstat is truncated"
                )
            index += 2
        if added == b"-" and deleted == b"-":
            result.append((0, 0, True))
            continue
        if not added.isdigit() or not deleted.isdigit():
            raise PromptWorkspaceError("GIT_REPORT_FAILED", "Git numstat is invalid")
        result.append((int(added), int(deleted), False))
    return result


def _empty_statistics() -> dict[str, int]:
    return {
        key: 0
        for key in (
            "files",
            "added",
            "modified",
            "deleted",
            "renamed",
            "copied",
            "type_changed",
            "insertions",
            "deletions",
            "binary_files",
        )
    }


def _add_statistics(
    target: dict[str, int],
    key: str,
    line_counts: tuple[int, int, bool],
) -> None:
    insertions, deletions, binary = line_counts
    target["files"] += 1
    target[key] += 1
    target["insertions"] += insertions
    target["deletions"] += deletions
    target["binary_files"] += int(binary)


def escape_git_path(path: bytes) -> str:
    """Render arbitrary Git path bytes without terminals interpreting them."""

    rendered: list[str] = []
    for byte in path:
        if byte == 0x2F or 0x20 <= byte <= 0x7E and byte not in {0x22, 0x5C}:
            rendered.append(chr(byte))
        else:
            rendered.append(f"\\x{byte:02x}")
    return "".join(rendered)


def _inside_scope(path: bytes, scope: str) -> bool:
    if scope == ".":
        return True
    prefix = scope.encode("utf-8")
    return path == prefix or path.startswith(prefix + b"/")


def _project_path(path: bytes, scope: str) -> str:
    if scope == ".":
        return escape_git_path(path)
    prefix = scope.encode("utf-8") + b"/"
    if path.startswith(prefix):
        return escape_git_path(path[len(prefix) :])
    if path == scope.encode("utf-8"):
        return "."
    return f"<outside-scope>/{escape_git_path(path)}"


def diff_statistics(
    repo: Path,
    left: str,
    right: str,
    scope: str,
    *,
    require_ancestry: bool,
) -> dict[str, object]:
    """Return deterministic full-repository and selected-scope Git statistics."""

    left = _commit(repo, left, "left report endpoint")
    right = _commit(repo, right, "right report endpoint")
    if require_ancestry:
        status, _ = _git_bytes(
            repo,
            ["merge-base", "--is-ancestor", left, right],
            "validate report ancestry",
            check=False,
        )
        if status != 0:
            raise PromptWorkspaceError(
                "GIT_REPORT_FAILED", "run-local report endpoints are not ancestral"
            )
    raw_status = _git_bytes(
        repo, _diff_arguments(left, right, "--name-status"), "read file changes"
    )[1]
    raw_numstat = _git_bytes(
        repo, _diff_arguments(left, right, "--numstat"), "read line statistics"
    )[1]
    entries = _name_status(raw_status)
    line_counts = _numstat(raw_numstat)
    if len(line_counts) != len(entries):
        raise PromptWorkspaceError(
            "GIT_REPORT_FAILED", "Git diff status and numstat counts disagree"
        )
    keys = {
        "A": "added",
        "M": "modified",
        "D": "deleted",
        "R": "renamed",
        "C": "copied",
        "T": "type_changed",
    }
    full = _empty_statistics()
    selected = _empty_statistics()
    outside = _empty_statistics()
    cross_scope = _empty_statistics()
    files: list[dict[str, object]] = []
    for (status, paths), lines in zip(entries, line_counts, strict=True):
        key = keys[status]
        _add_statistics(full, key, lines)
        membership = [_inside_scope(path, scope) for path in paths]
        if any(membership):
            _add_statistics(selected, key, lines)
        if not all(membership):
            _add_statistics(outside, key, lines)
        if any(membership) and not all(membership):
            _add_statistics(cross_scope, key, lines)
        files.append(
            {
                "status": status,
                "paths": [_project_path(path, scope) for path in paths],
            }
        )
    return {
        "endpoints": {"from": left, "to": right},
        "full_repository": full,
        "selected_scope": selected,
        "outside_scope": outside,
        "cross_scope": cross_scope,
        "files": files,
    }


def commit_relationship(repo: Path, source: str, lane: str) -> str:
    source = _commit(repo, source, "source comparison endpoint")
    lane = _commit(repo, lane, "lane comparison endpoint")
    if source == lane:
        return "equal"
    source_ancestor = (
        _git_bytes(
            repo,
            ["merge-base", "--is-ancestor", source, lane],
            "compare source and lane",
            check=False,
        )[0]
        == 0
    )
    lane_ancestor = (
        _git_bytes(
            repo,
            ["merge-base", "--is-ancestor", lane, source],
            "compare lane and source",
            check=False,
        )[0]
        == 0
    )
    if source_ancestor:
        return "lane_ahead"
    if lane_ancestor:
        return "source_ahead"
    return "diverged"


def record_source_head_at_open(
    run_dir: Path, source_ref: str, source_head: str
) -> dict[str, object]:
    """Persist the source observation that existed before worker execution."""

    value = {
        "schema": SOURCE_OBSERVATION_SCHEMA,
        "source_ref": source_ref,
        "source_head_at_open": source_head,
    }
    path = _summary_path(run_dir, SOURCE_OBSERVATION_NAME)
    raw = stable_json(value)
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "source observation changed after planning"
            )
        existing = load_json_object(path, "source observation")
        if (
            set(existing) != {"schema", "source_ref", "source_head_at_open"}
            or existing.get("schema") != SOURCE_OBSERVATION_SCHEMA
            or existing.get("source_ref") != source_ref
            or SHA_RE.fullmatch(str(existing.get("source_head_at_open") or "")) is None
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "source observation changed after planning"
            )
        return existing
    else:
        write_exclusive(path, raw)
    return value


def _source_observation(
    workspace: dict[str, object], run_dir: Path, repo: Path
) -> dict[str, object]:
    path = _summary_path(run_dir, SOURCE_OBSERVATION_NAME)
    source_ref = required_string(workspace, "source_ref", "workspace manifest")
    current = _git_text(repo, ["rev-parse", "--verify", source_ref], "read source ref")
    current = _commit(repo, current, "current source head")
    if not path.exists():
        return {
            "status": "unknown_legacy",
            "source_branch": required_string(
                workspace, "source_branch", "workspace manifest"
            ),
            "source_head_at_open": None,
            "source_head_at_completion": current,
        }
    if path.is_symlink() or not path.is_file():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "source observation is invalid"
        )
    value = load_json_object(path, "source observation")
    if (
        set(value) != {"schema", "source_ref", "source_head_at_open"}
        or value.get("schema") != SOURCE_OBSERVATION_SCHEMA
        or value.get("source_ref") != source_ref
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "source observation is invalid"
        )
    opened = _commit(repo, value.get("source_head_at_open"), "source head at open")
    return {
        "status": "unchanged" if opened == current else "moved",
        "source_branch": required_string(
            workspace, "source_branch", "workspace manifest"
        ),
        "source_head_at_open": opened,
        "source_head_at_completion": current,
    }


def _correction_ids(run_dir: Path) -> set[str]:
    root = orchestration_dir(run_dir) / "pending-plans"
    if not root.is_dir():
        return set()
    result: set[str] = set()
    for path in sorted(root.glob("*/*.json")):
        value = load_json_object(path, "pending plan")
        tasks = value.get("tasks")
        if isinstance(tasks, list):
            result.update(
                str(item["task_id"])
                for item in tasks
                if isinstance(item, dict) and isinstance(item.get("task_id"), str)
            )
    return result


def _evidence_outcomes(
    run_dir: Path, waves: list[dict[str, object]]
) -> dict[str, object]:
    validation: list[str] = []
    review: list[str] = []
    for wave in waves:
        evidence = load_json_object(
            orchestration_dir(run_dir) / "evidence" / f"{wave['wave_id']}.json",
            "combined wave evidence",
        )
        for key, target in (("validation", validation), ("code_review", review)):
            value = evidence.get(key)
            if not isinstance(value, str) or not value.strip():
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID",
                    "combined validation evidence is incomplete",
                )
            target.append(value)
    return {
        "validation": {
            "status": "passed",
            "waves": len(validation),
            "evidence_sha256": _sha256(stable_json(validation)),
        },
        "review": {
            "status": "passed",
            "waves": len(review),
            "evidence_sha256": _sha256(stable_json(review)),
        },
    }


def _primary_project(workspace: dict[str, object]) -> Path:
    primary = Path(required_string(workspace, "primary_root", "workspace manifest"))
    scope = required_string(workspace, "scope", "workspace manifest")
    return primary if scope == "." else primary / PurePosixPath(scope)


def _clean(repo: Path) -> bool:
    return not _git_bytes(
        repo,
        ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
        "inspect checkout cleanliness",
    )[1]


def _quoted_path(path: Path) -> str:
    value = str(path)
    if re.search(r"[\x00-\x1f\x7f]", value) is not None:
        raise PromptWorkspaceError(
            "WORKSPACE_PATH_INVALID", "project path contains a control character"
        )
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def _quoted_invocation(path: Path) -> str:
    return f"$task-implementer integrate {_quoted_path(path)}"


def build_run_summary(
    workspace: dict[str, object],
    run_dir: Path,
    coordinator: dict[str, object],
    waves: list[dict[str, object]],
    promoted_head: str,
    queue: dict[str, object],
) -> dict[str, object]:
    repo = Path(required_string(workspace, "repo_root", "workspace manifest"))
    scope = required_string(workspace, "scope", "workspace manifest")
    initial_head = _commit(repo, coordinator.get("initial_head"), "run initial head")
    promoted_head = _commit(repo, promoted_head, "final promoted head")
    source = _source_observation(workspace, run_dir, repo)
    source_head = str(source["source_head_at_completion"])
    relationship = commit_relationship(repo, source_head, promoted_head)
    run_delta = diff_statistics(
        repo, initial_head, promoted_head, scope, require_ancestry=True
    )
    accumulated = diff_statistics(
        repo, source_head, promoted_head, scope, require_ancestry=False
    )
    task_ids = [str(task_id) for wave in waves for task_id in wave["task_ids"]]
    corrections = set(task_ids) & _correction_ids(run_dir)
    retained = sum(len(wave["cleanup_retained"]) for wave in waves)
    worker_total = len(task_ids)
    temporary_total = worker_total + len(waves)
    primary_project = _primary_project(workspace)
    source_clean = _clean(
        Path(required_string(workspace, "primary_root", "workspace manifest"))
    )
    lane_clean = _clean(repo)
    invocation = _quoted_invocation(primary_project)
    if not source_clean:
        readiness = "commit_primary_first"
        instruction = f'Invoke $commit in "{primary_project}" first, then {invocation}'
    elif not lane_clean:
        readiness = "lane_not_ready"
        instruction = "Resolve managed lane changes before integration."
    else:
        readiness = "ready"
        instruction = invocation
    if source["status"] == "moved":
        instruction += "; integration will rebuild and revalidate its candidate because the source moved"
    queued_entries = queue.get("entries")
    queued_count = len(queued_entries) if isinstance(queued_entries, list) else 0
    return {
        "schema": SUMMARY_SCHEMA,
        "status": "done",
        "work": {
            "tasks": {
                "total": len(task_ids),
                "implementation": len(task_ids) - len(corrections),
                "corrections": len(corrections),
            },
            "waves": len(waves),
            "temporary_worker_worktrees": worker_total,
            "temporary_resources": {
                "total": temporary_total,
                "removed": temporary_total - retained,
                "retained": retained,
            },
        },
        "outcomes": _evidence_outcomes(run_dir, waves),
        "lane": {
            "promotion": "promoted",
            "promoted_head": promoted_head,
            "generation_release": "released",
        },
        "source_observation": source,
        "changes": {
            "run_local": {"label": "final integration result", **run_delta},
            "accumulated_pending": {
                "label": (
                    "source-to-lane comparison; histories diverged"
                    if relationship == "diverged"
                    else "source-to-lane comparison"
                ),
                "relationship": relationship,
                **accumulated,
            },
        },
        "queued_prompt": {
            "status": "activation_scheduled" if queued_count else "none",
            "pending": queued_count,
        },
        "next_action": {
            "action": "integrate",
            "readiness": readiness,
            "invocation": invocation,
            "instruction": instruction,
        },
    }


def _validate_summary(value: dict[str, object]) -> dict[str, object]:
    expected = {
        "schema",
        "status",
        "work",
        "outcomes",
        "lane",
        "source_observation",
        "changes",
        "queued_prompt",
        "next_action",
    }
    if (
        set(value) != expected
        or value.get("schema") != SUMMARY_SCHEMA
        or value.get("status") != "done"
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "sealed run summary is invalid")

    work = value.get("work")
    if not isinstance(work, dict) or set(work) != {
        "tasks",
        "waves",
        "temporary_worker_worktrees",
        "temporary_resources",
    }:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "sealed run summary is invalid")
    tasks = work.get("tasks")
    resources = work.get("temporary_resources")
    if (
        not isinstance(tasks, dict)
        or set(tasks) != {"total", "implementation", "corrections"}
        or not isinstance(resources, dict)
        or set(resources) != {"total", "removed", "retained"}
        or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 0
            for item in [
                *tasks.values(),
                work.get("waves"),
                work.get("temporary_worker_worktrees"),
                *resources.values(),
            ]
        )
        or tasks["implementation"] + tasks["corrections"] != tasks["total"]
        or resources["removed"] + resources["retained"] != resources["total"]
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "sealed run summary is invalid")

    outcomes = value.get("outcomes")
    if not isinstance(outcomes, dict) or set(outcomes) != {"validation", "review"}:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "sealed run summary is invalid")
    for outcome in outcomes.values():
        if (
            not isinstance(outcome, dict)
            or set(outcome) != {"status", "waves", "evidence_sha256"}
            or outcome.get("status") != "passed"
            or not isinstance(outcome.get("waves"), int)
            or isinstance(outcome.get("waves"), bool)
            or int(outcome["waves"]) < 0
            or re.fullmatch(r"[0-9a-f]{64}", str(outcome.get("evidence_sha256") or ""))
            is None
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "sealed run summary is invalid"
            )

    lane = value.get("lane")
    if (
        not isinstance(lane, dict)
        or set(lane) != {"promotion", "promoted_head", "generation_release"}
        or lane.get("promotion") != "promoted"
        or lane.get("generation_release") != "released"
        or SHA_RE.fullmatch(str(lane.get("promoted_head") or "")) is None
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "sealed run summary is invalid")

    source = value.get("source_observation")
    if (
        not isinstance(source, dict)
        or set(source)
        != {
            "status",
            "source_branch",
            "source_head_at_open",
            "source_head_at_completion",
        }
        or source.get("status") not in {"unchanged", "moved", "unknown_legacy"}
        or not isinstance(source.get("source_branch"), str)
        or not source["source_branch"]
        or SHA_RE.fullmatch(str(source.get("source_head_at_completion") or "")) is None
        or (
            source.get("status") == "unknown_legacy"
            and source.get("source_head_at_open") is not None
        )
        or (
            source.get("status") != "unknown_legacy"
            and SHA_RE.fullmatch(str(source.get("source_head_at_open") or "")) is None
        )
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "sealed run summary is invalid")

    changes = value.get("changes")
    if not isinstance(changes, dict) or set(changes) != {
        "run_local",
        "accumulated_pending",
    }:
        raise PromptWorkspaceError("RUN_STATE_INVALID", "sealed run summary is invalid")
    for name, delta in changes.items():
        expected_delta = {
            "label",
            "endpoints",
            "full_repository",
            "selected_scope",
            "outside_scope",
            "cross_scope",
            "files",
        }
        if name == "accumulated_pending":
            expected_delta.add("relationship")
        if not isinstance(delta, dict) or set(delta) != expected_delta:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "sealed run summary is invalid"
            )
        endpoints = delta.get("endpoints")
        totals = delta.get("full_repository")
        statistic_keys = {
            "files",
            "added",
            "modified",
            "deleted",
            "renamed",
            "copied",
            "type_changed",
            "insertions",
            "deletions",
            "binary_files",
        }
        statistics = [
            delta.get(scope_name)
            for scope_name in (
                "full_repository",
                "selected_scope",
                "outside_scope",
                "cross_scope",
            )
        ]
        if (
            not isinstance(delta.get("label"), str)
            or not delta["label"]
            or not isinstance(endpoints, dict)
            or set(endpoints) != {"from", "to"}
            or any(
                SHA_RE.fullmatch(str(endpoint or "")) is None
                for endpoint in endpoints.values()
            )
            or not isinstance(totals, dict)
            or any(not isinstance(stats, dict) for stats in statistics)
            or any(set(stats) != statistic_keys for stats in statistics)
            or any(
                not isinstance(item, int) or isinstance(item, bool) or item < 0
                for stats in statistics
                for item in stats.values()
            )
            or any(
                stats["files"]
                != sum(
                    stats[key]
                    for key in (
                        "added",
                        "modified",
                        "deleted",
                        "renamed",
                        "copied",
                        "type_changed",
                    )
                )
                or stats["binary_files"] > stats["files"]
                for stats in statistics
            )
            or not isinstance(delta.get("files"), list)
            or delta["full_repository"]["files"] != len(delta["files"])
            or delta["cross_scope"]["files"] > delta["selected_scope"]["files"]
            or delta["cross_scope"]["files"] > delta["outside_scope"]["files"]
            or (
                name == "accumulated_pending"
                and delta.get("relationship")
                not in {"equal", "lane_ahead", "source_ahead", "diverged"}
            )
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "sealed run summary is invalid"
            )
        for file_value in delta["files"]:
            if (
                not isinstance(file_value, dict)
                or set(file_value) != {"status", "paths"}
                or file_value.get("status") not in {"A", "M", "D", "R", "C", "T"}
                or not isinstance(file_value.get("paths"), list)
                or len(file_value["paths"])
                != (2 if file_value.get("status") in {"R", "C"} else 1)
                or any(
                    not isinstance(path, str) or not path
                    for path in file_value["paths"]
                )
            ):
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "sealed run summary is invalid"
                )

    queued = value.get("queued_prompt")
    if (
        not isinstance(queued, dict)
        or set(queued) != {"status", "pending"}
        or queued.get("status") not in {"none", "activation_scheduled"}
        or not isinstance(queued.get("pending"), int)
        or isinstance(queued.get("pending"), bool)
        or queued["pending"] < 0
        or (queued["status"] == "none") != (queued["pending"] == 0)
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "sealed run summary is invalid")
    next_action = value.get("next_action")
    if (
        not isinstance(next_action, dict)
        or set(next_action) != {"action", "readiness", "invocation", "instruction"}
        or next_action.get("action") != "integrate"
        or next_action.get("readiness")
        not in {"ready", "commit_primary_first", "lane_not_ready"}
        or any(
            not isinstance(next_action.get(key), str) or not next_action[key]
            for key in ("invocation", "instruction")
        )
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "sealed run summary is invalid")
    return value


def prepare_run_summary(
    run_dir: Path,
    summary: dict[str, object],
    queue: dict[str, object] | None = None,
) -> bytes:
    raw = stable_json(_validate_summary(summary))
    if len(raw) > SUMMARY_BYTES_LIMIT:
        raise PromptWorkspaceError(
            "GIT_REPORT_FAILED", "run summary exceeded the reporting bound"
        )
    digest = _sha256(raw)
    entries = [] if queue is None else queue.get("entries")
    if not isinstance(entries, list):
        raise PromptWorkspaceError(
            "QUEUE_STATE_INVALID", "prompt queue entries are invalid"
        )
    queue_digest = (
        _sha256(str(entries[0]["queue_id"]).encode("utf-8")) if entries else None
    )
    state_path = _summary_path(run_dir, STATE_NAME)
    state = {
        "schema": SUMMARY_STATE_SCHEMA,
        "phase": "prepared",
        "summary_sha256": digest,
        "queue_head_sha256": queue_digest,
        "prepared_summary": summary,
    }
    if state_path.exists():
        existing = load_json_object(state_path, "run summary state")
        if (
            existing.get("summary_sha256") != digest
            or existing.get("prepared_summary") != summary
            or existing.get("queue_head_sha256") != queue_digest
        ):
            raise PromptWorkspaceError(
                "FINALIZATION_CONFLICT", "run summary state changed during replay"
            )
    else:
        write_exclusive(state_path, stable_json(state))
    prepared = _summary_path(run_dir, PREPARED_NAME)
    if prepared.exists():
        if (
            prepared.is_symlink()
            or not prepared.is_file()
            or prepared.read_bytes() != raw
        ):
            raise PromptWorkspaceError(
                "FINALIZATION_CONFLICT", "prepared run summary changed during replay"
            )
    else:
        write_exclusive(prepared, raw)
    return raw


def _advance_state(run_dir: Path, digest: str, phase: str) -> None:
    state_path = _summary_path(run_dir, STATE_NAME)
    existing = load_json_object(state_path, "run summary state")
    order = {"prepared": 0, "sealed": 1, "handoff_published": 2, "complete": 3}
    current = existing.get("phase")
    if current not in order or phase not in order or order[phase] < order[current]:
        raise PromptWorkspaceError(
            "FINALIZATION_CONFLICT", "run summary phase cannot move backward"
        )
    write_atomic(
        state_path,
        stable_json(
            {
                "schema": SUMMARY_STATE_SCHEMA,
                "phase": phase,
                "summary_sha256": digest,
                "queue_head_sha256": existing.get("queue_head_sha256"),
                "prepared_summary": existing.get("prepared_summary"),
            }
        ),
    )


def bind_summary_queue_head(run_dir: Path, queue: dict[str, object]) -> None:
    """Bind the queued head privately so activation replay cannot skip ahead."""

    entries = queue.get("entries")
    if not isinstance(entries, list):
        raise PromptWorkspaceError(
            "QUEUE_STATE_INVALID", "prompt queue entries are invalid"
        )
    queue_digest = (
        _sha256(str(entries[0]["queue_id"]).encode("utf-8")) if entries else None
    )
    state_path = _summary_path(run_dir, STATE_NAME)
    state = load_json_object(state_path, "run summary state")
    existing = state.get("queue_head_sha256")
    if existing is not None and existing != queue_digest:
        raise PromptWorkspaceError(
            "FINALIZATION_CONFLICT", "queued prompt head changed during finalization"
        )
    state["queue_head_sha256"] = queue_digest
    write_atomic(state_path, stable_json(state))


def queue_activation_pending(run_dir: Path, queue: dict[str, object]) -> bool:
    """Return whether the exact prepared queue head still needs activation."""

    state = load_json_object(_summary_path(run_dir, STATE_NAME), "run summary state")
    expected = state.get("queue_head_sha256")
    if expected is None:
        return False
    entries = queue.get("entries")
    history = queue.get("history")
    if not isinstance(entries, list) or not isinstance(history, list):
        raise PromptWorkspaceError("QUEUE_STATE_INVALID", "prompt queue is invalid")
    if entries and _sha256(str(entries[0]["queue_id"]).encode("utf-8")) == expected:
        return True
    resolved = [
        item
        for item in history
        if _sha256(str(item.get("queue_id", "")).encode("utf-8")) == expected
    ]
    if len(resolved) == 1 and resolved[0].get("disposition") in {
        "activated",
        "no_effect",
    }:
        return False
    raise PromptWorkspaceError(
        "FINALIZATION_CONFLICT", "the prepared queued prompt head changed"
    )


def _read_summary_bytes(path: Path, label: str, missing_code: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise PromptWorkspaceError(missing_code, f"{label} is missing")
    try:
        if path.stat().st_size > SUMMARY_BYTES_LIMIT:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", f"{label} exceeded the reporting bound"
            )
        raw = path.read_bytes()
    except OSError as exc:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"{label} could not be read"
        ) from exc
    if len(raw) > SUMMARY_BYTES_LIMIT:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"{label} exceeded the reporting bound"
        )
    return raw


def _decode_summary_bytes(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"{label} is not valid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"{label} must contain a JSON object"
        )
    return _validate_summary(value)


def seal_prepared_summary(run_dir: Path) -> dict[str, object]:
    prepared = _summary_path(run_dir, PREPARED_NAME)
    raw = _read_summary_bytes(prepared, "prepared run summary", "FINALIZATION_PENDING")
    value = _decode_summary_bytes(raw, "prepared run summary")
    if stable_json(value) != raw:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prepared run summary is not stable"
        )
    digest = _sha256(raw)
    sealed = _summary_path(run_dir, SEALED_NAME)
    if sealed.exists():
        if (
            _read_summary_bytes(sealed, "sealed run summary", "FINALIZATION_CONFLICT")
            != raw
        ):
            raise PromptWorkspaceError(
                "FINALIZATION_CONFLICT", "sealed run summary differs"
            )
    else:
        write_exclusive(sealed, raw)
    _advance_state(run_dir, digest, "sealed")
    return value


def load_prepared_summary(run_dir: Path) -> dict[str, object]:
    path = _summary_path(run_dir, PREPARED_NAME)
    state = load_json_object(_summary_path(run_dir, STATE_NAME), "run summary state")
    prepared = state.get("prepared_summary")
    if not isinstance(prepared, dict):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prepared run summary state is invalid"
        )
    value = _validate_summary(prepared)
    raw = stable_json(value)
    if state.get("summary_sha256") != _sha256(raw):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prepared run summary digest is invalid"
        )
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != raw:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "prepared run summary differs"
            )
    else:
        write_exclusive(path, raw)
    return value


def mark_handoff_published(run_dir: Path) -> None:
    raw = sealed_summary_bytes(run_dir)
    _advance_state(run_dir, _sha256(raw), "handoff_published")


def mark_finalization_complete(run_dir: Path) -> None:
    raw = sealed_summary_bytes(run_dir)
    _advance_state(run_dir, _sha256(raw), "complete")


def summary_phase(run_dir: Path) -> str | None:
    path = _summary_path(run_dir, STATE_NAME)
    if not path.exists():
        return None
    state = load_json_object(path, "run summary state")
    if (
        set(state)
        != {
            "schema",
            "phase",
            "summary_sha256",
            "queue_head_sha256",
            "prepared_summary",
        }
        or state.get("schema") != SUMMARY_STATE_SCHEMA
        or state.get("phase")
        not in {"prepared", "sealed", "handoff_published", "complete"}
        or re.fullmatch(r"[0-9a-f]{64}", str(state.get("summary_sha256") or "")) is None
        or (
            state.get("queue_head_sha256") is not None
            and re.fullmatch(r"[0-9a-f]{64}", str(state["queue_head_sha256"])) is None
        )
        or not isinstance(state.get("prepared_summary"), dict)
        or _sha256(stable_json(state["prepared_summary"]))
        != state.get("summary_sha256")
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "run summary state is invalid")
    return str(state["phase"])


def sealed_summary_bytes(run_dir: Path) -> bytes:
    path = _summary_path(run_dir, SEALED_NAME)
    raw = _read_summary_bytes(path, "sealed run summary", "FINALIZATION_PENDING")
    value = _decode_summary_bytes(raw, "sealed run summary")
    if stable_json(value) != raw:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "sealed run summary is not stable"
        )
    state = load_json_object(_summary_path(run_dir, STATE_NAME), "run summary state")
    if state.get("summary_sha256") != _sha256(raw):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "sealed run summary digest is invalid"
        )
    return raw


def load_sealed_summary(run_dir: Path) -> dict[str, object] | None:
    path = _summary_path(run_dir, SEALED_NAME)
    if not path.exists():
        return None
    return json.loads(sealed_summary_bytes(run_dir).decode("utf-8"))


def render_completion_projection(summary: dict[str, object]) -> str:
    work = summary["work"]
    tasks = work["tasks"]
    resources = work["temporary_resources"]
    source = summary["source_observation"]
    run_files = summary["changes"]["run_local"]["full_repository"]
    next_action = summary["next_action"]
    return (
        "## Completion Report\n\n"
        f"- Schema: {SUMMARY_SCHEMA}\n"
        f"- Tasks: {tasks['total']} ({tasks['corrections']} corrections)\n"
        f"- Waves: {work['waves']}\n"
        f"- Temporary resources: {resources['removed']} removed; {resources['retained']} retained\n"
        f"- Validation: {summary['outcomes']['validation']['status']}\n"
        f"- Review: {summary['outcomes']['review']['status']}\n"
        f"- Source observation: {source['status']}\n"
        f"- Run-local files: {run_files['files']}\n"
        f"- Next action: {next_action['instruction']}\n"
    )


def public_summary_response(run_dir: Path) -> dict[str, object]:
    """Return only the immutable public-safe summary payload."""

    value = load_sealed_summary(run_dir)
    if value is None:
        raise PromptWorkspaceError(
            "FINALIZATION_PENDING", "sealed run summary is missing"
        )
    return value


def _run_matches_workspace_incarnation(
    workspace: dict[str, object],
    run_dir: Path,
    interop: dict[str, object],
) -> bool:
    """Validate one run's project identity and classify its lane incarnation."""

    orchestration = orchestration_dir(run_dir)
    manifest_path = run_dir / "manifest.json"
    interop_path = orchestration / "interop.json"
    if (
        run_dir.is_symlink()
        or not run_dir.is_dir()
        or orchestration.is_symlink()
        or not orchestration.is_dir()
        or manifest_path.is_symlink()
        or not manifest_path.is_file()
        or interop_path.is_symlink()
        or not interop_path.is_file()
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", "released run path is unsafe")
    require_mode(run_dir, 0o700, "run directory")
    require_mode(orchestration, 0o700, "run orchestration directory")
    require_mode(manifest_path, 0o600, "run manifest")
    require_mode(interop_path, 0o600, "run interoperability state")
    manifest = load_json_object(manifest_path, "run manifest")
    if (
        manifest.get("schema") != RUN_SCHEMA
        or manifest.get("run_id") != run_dir.name
        or manifest.get("project_id") != workspace.get("project_id")
        or manifest.get("scope_id") != workspace.get("scope_id")
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "released run belongs to another workspace"
        )
    expected_interop = {
        "name": workspace.get("lane_name"),
        "branch": workspace.get("lane_branch"),
        "worktree": workspace.get("repo_root"),
        "task_scope": workspace.get("scope"),
        "lane_id": workspace.get("lane_id"),
    }
    return all(interop.get(key) == value for key, value in expected_interop.items())


def _validate_run_binding(
    workspace: dict[str, object],
    run_dir: Path,
    interop: dict[str, object],
    summary: dict[str, object] | None,
) -> None:
    """Bind one current-incarnation report to its terminal coordinator."""

    from prompt_workspace_execution import load_coordinator_state

    if not _run_matches_workspace_incarnation(workspace, run_dir, interop):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID",
            "run lane identity does not match the current workspace incarnation",
        )
    promoted = interop.get("promoted_head")
    if not isinstance(promoted, str) or SHA_RE.fullmatch(promoted) is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "released run promoted head is invalid"
        )
    coordinator = load_coordinator_state(run_dir)
    if coordinator is None or coordinator.get("status") != "done":
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "released run coordinator is not terminal"
        )
    if summary is not None and summary["lane"]["promoted_head"] != promoted:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "sealed summary does not match the released head"
        )


def sealed_summaries(
    runs_root: Path,
    workspace: dict[str, object],
    generations: set[int],
) -> Iterable[tuple[int, dict[str, object] | None]]:
    """Yield requested current-incarnation summaries, preserving legacy gaps."""

    from prompt_workspace_interop import load_interop

    rows: list[tuple[int, dict[str, object] | None]] = []
    total_bytes = 0
    for run_dir in sorted(
        path
        for path in runs_root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and RUN_ID_RE.fullmatch(path.name) is not None
    ):
        interop = load_interop(run_dir, required=False)
        if interop is None:
            continue
        generation = interop.get("generation")
        if (
            interop.get("mode") == "lane"
            and interop.get("released") is True
            and isinstance(generation, int)
            and not isinstance(generation, bool)
            and generation > 0
            and generation in generations
        ):
            if not _run_matches_workspace_incarnation(workspace, run_dir, interop):
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID",
                    "current pending generation has mismatched lane identity",
                )
            summary = load_sealed_summary(run_dir)
            _validate_run_binding(workspace, run_dir, interop, summary)
            total_bytes += len(stable_json(summary)) if summary is not None else 0
            if (
                len(rows) >= PENDING_GENERATIONS_LIMIT
                or total_bytes > LANE_REPORT_BYTES_LIMIT
            ):
                raise PromptWorkspaceError(
                    "GIT_REPORT_FAILED",
                    "pending lane summaries exceeded the reporting bound",
                )
            rows.append((generation, summary))
    generations = [generation for generation, _summary in rows]
    if len(generations) != len(set(generations)):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "multiple runs claim one lane generation"
        )
    yield from sorted(rows, key=lambda item: item[0])


def _latest_sealed_summary(
    runs_root: Path, workspace: dict[str, object]
) -> dict[str, object] | None:
    """Return the latest sealed summary for a removed lane incarnation."""

    from prompt_workspace_interop import load_interop

    seen_generations: set[int] = set()
    latest: tuple[int, Path, dict[str, object]] | None = None
    for run_dir in sorted(
        path
        for path in runs_root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and RUN_ID_RE.fullmatch(path.name) is not None
    ):
        interop = load_interop(run_dir, required=False)
        if (
            interop is None
            or interop.get("mode") != "lane"
            or interop.get("released") is not True
            or not _run_matches_workspace_incarnation(workspace, run_dir, interop)
        ):
            continue
        generation = interop.get("generation")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "released lane generation is invalid"
            )
        if generation in seen_generations:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "multiple runs claim one lane generation"
            )
        seen_generations.add(generation)
        sealed = _summary_path(run_dir, SEALED_NAME)
        if (sealed.exists() or sealed.is_symlink()) and (
            latest is None or generation > latest[0]
        ):
            latest = (generation, run_dir, interop)
    if latest is None:
        return None
    _generation, run_dir, interop = latest
    summary = load_sealed_summary(run_dir)
    if summary is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "selected sealed run summary is missing"
        )
    _validate_run_binding(workspace, run_dir, interop, summary)
    return summary


def pending_finalization_generations(
    runs_root: Path,
    workspace: dict[str, object] | None = None,
) -> set[int]:
    """Return current generations that must finish summary publication."""

    from prompt_workspace_interop import load_interop

    pending: set[int] = set()
    for run_dir in sorted(
        path
        for path in runs_root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and RUN_ID_RE.fullmatch(path.name) is not None
    ):
        interop = load_interop(run_dir, required=False)
        if interop is None or interop.get("mode") != "lane":
            continue
        phase = summary_phase(run_dir)
        released = interop.get("released") is True
        if phase not in {"prepared", "sealed", "handoff_published"} and not released:
            continue
        generation = interop.get("generation")
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 1
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "lane generation is invalid"
            )
        if workspace is not None and not _run_matches_workspace_incarnation(
            workspace, run_dir, interop
        ):
            if phase in {"prepared", "sealed", "handoff_published"}:
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID",
                    "incomplete finalization has mismatched lane identity",
                )
            continue
        if workspace is not None:
            _validate_run_binding(workspace, run_dir, interop, None)
        if phase in {"prepared", "sealed", "handoff_published"}:
            pending.add(generation)
        elif phase == "complete":
            if not released:
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID",
                    "completed summary precedes lane generation release",
                )
            sealed_summary_bytes(run_dir)
        elif any(
            _summary_path(run_dir, name).exists()
            for name in (
                SOURCE_OBSERVATION_NAME,
                PREPARED_NAME,
                SEALED_NAME,
                STATE_NAME,
            )
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "released generation summary state is incomplete"
            )
        # A released run with none of the versioned reporting artifacts is an
        # older generation. It remains integrable but has no reconstructed
        # historical summary.
    return pending


class _LaneReportUnstable(RuntimeError):
    """Internal signal that a zero-write observation crossed a state transition."""


def _read_lane_evidence(path: Path, remaining: int) -> bytes:
    """Read one regular evidence file without following links or exceeding bounds."""

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "lane status evidence could not be read safely"
        ) from exc
    try:
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_size > remaining:
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "lane status evidence exceeded its bound"
                )
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(remaining + 1)
        except OSError as exc:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "lane status evidence could not be read safely"
            ) from exc
    finally:
        os.close(descriptor)
    if len(raw) > remaining:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "lane status evidence exceeded its bound"
        )
    return raw


def _bounded_lane_directory(
    directory: Path, remaining: int
) -> tuple[list[tuple[Path, bool]], int]:
    """List one directory without following links or exceeding the entry bound."""

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(directory, flags)
    except OSError as exc:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "lane status state directory is unsafe"
        ) from exc
    try:
        try:
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "lane status state directory is unsafe"
                )
            entries: list[tuple[Path, bool]] = []
            with os.scandir(descriptor) as iterator:
                for entry in iterator:
                    if remaining <= 0:
                        raise PromptWorkspaceError(
                            "RUN_STATE_INVALID",
                            "lane status evidence exceeded its bound",
                        )
                    remaining -= 1
                    details = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(details.st_mode):
                        raise PromptWorkspaceError(
                            "RUN_STATE_INVALID",
                            "lane status state directory is unsafe",
                        )
                    if stat.S_ISDIR(details.st_mode):
                        is_directory = True
                    elif stat.S_ISREG(details.st_mode):
                        is_directory = False
                    else:
                        raise PromptWorkspaceError(
                            "RUN_STATE_INVALID",
                            "lane status state directory is unsafe",
                        )
                    entries.append((directory / entry.name, is_directory))
        except OSError as exc:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "lane status state directory is unsafe"
            ) from exc
    finally:
        os.close(descriptor)
    return sorted(entries, key=lambda item: item[0].name), remaining


def _lane_state_digest(runs_root: Path) -> str:
    """Digest only bounded machine state that can affect the lane projection."""

    records: list[dict[str, object]] = []
    total_bytes = 0
    run_entries, remaining_entries = _bounded_lane_directory(
        runs_root, LANE_REPORT_MAX_STATE_FILES
    )
    for run_dir, is_run_directory in run_entries:
        if not is_run_directory:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "runs root contains an unsafe entry"
            )
        if RUN_ID_RE.fullmatch(run_dir.name) is None:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "runs root contains an unknown entry"
            )
        orchestration = orchestration_dir(run_dir)
        run_contents, remaining_entries = _bounded_lane_directory(
            run_dir, remaining_entries
        )
        candidates = [
            path
            for path, is_directory in run_contents
            if not is_directory
            and (path.suffix == ".json" or path.name == "handoff.md")
        ]
        orchestration_entries = [
            is_directory
            for path, is_directory in run_contents
            if path == orchestration
        ]
        if orchestration_entries:
            if orchestration_entries != [True]:
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "run orchestration directory is unsafe"
                )
            orchestration_contents, remaining_entries = _bounded_lane_directory(
                orchestration, remaining_entries
            )
            candidates.extend(
                path
                for path, is_directory in orchestration_contents
                if not is_directory and path.suffix == ".json"
            )
            for state_root in (
                orchestration / "waves",
                orchestration / "tasks",
            ):
                state_entries = [
                    is_directory
                    for path, is_directory in orchestration_contents
                    if path == state_root
                ]
                if not state_entries:
                    continue
                if state_entries != [True]:
                    raise PromptWorkspaceError(
                        "RUN_STATE_INVALID",
                        "lane status state directory is unsafe",
                    )
                pending_directories = [state_root]
                while pending_directories:
                    directory = pending_directories.pop()
                    contents, remaining_entries = _bounded_lane_directory(
                        directory, remaining_entries
                    )
                    pending_directories.extend(
                        path
                        for path, is_directory in reversed(contents)
                        if is_directory
                    )
                    candidates.extend(
                        path
                        for path, is_directory in contents
                        if not is_directory and path.suffix == ".json"
                    )
        for path in sorted(set(candidates)):
            if path.is_symlink() or not path.is_file():
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "lane status evidence contains an unsafe path"
                )
            raw = _read_lane_evidence(path, LANE_REPORT_BYTES_LIMIT - total_bytes)
            total_bytes += len(raw)
            records.append(
                {
                    "run": run_dir.name,
                    "path": path.relative_to(run_dir).as_posix(),
                    "bytes": len(raw),
                    "sha256": _sha256(raw),
                }
            )
    return _sha256(stable_json(records))


def _clean_evidence(repo: Path) -> tuple[bool, str]:
    raw = _git_bytes(
        repo,
        ["status", "--porcelain=v2", "-z", "--untracked-files=all"],
        "inspect checkout cleanliness",
    )[1]
    return not raw, _sha256(raw)


def _report_run_metadata(
    workspace: dict[str, object],
    run_dir: Path,
    manifest: dict[str, object],
) -> dict[str, object]:
    """Validate only run metadata needed by lane status, never prompt snapshots."""

    from prompt_workspace_resume import effective_run_status
    from prompt_workspace_runs import (
        handoff_field,
        manifest_revisions,
        markdown_section,
        pending_steering_revisions,
        read_handoff_text,
        run_status,
    )

    if (
        manifest.get("project_id") != workspace.get("project_id")
        or manifest.get("scope_id") != workspace.get("scope_id")
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "run metadata belongs to another workspace"
        )
    revisions = manifest_revisions(manifest)
    for number, revision in enumerate(revisions, start=1):
        if (
            revision.get("revision") != f"r{number:04d}"
            or re.fullmatch(r"[0-9a-f]{64}", str(revision.get("sha256") or ""))
            is None
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run revision metadata is invalid"
            )
    latest = revisions[-1]
    bound = latest
    handoff_text = read_handoff_text(run_dir)
    if handoff_text is not None:
        run_section = markdown_section(handoff_text, "Run")
        if (
            handoff_field(run_section, "Run ID") != run_dir.name
            or handoff_field(run_section, "Prompt ID")
            != str(manifest.get("prompt_id"))
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run handoff metadata is invalid"
            )
        bound_revision = handoff_field(run_section, "Bound revision")
        bound_digest = handoff_field(run_section, "Bound SHA-256")
        matches = [
            revision
            for revision in revisions
            if revision.get("revision") == bound_revision
            and revision.get("sha256") == bound_digest
        ]
        if len(matches) != 1:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "run handoff revision is invalid"
            )
        bound = matches[0]
    pending = pending_steering_revisions(run_dir, revisions)
    return {
        "status": effective_run_status(run_dir, run_status(run_dir)),
        "reconciliation_pending": bound["revision"] != latest["revision"],
        "steering_pending": bool(pending),
    }


def _current_run(
    workspace: dict[str, object], runs_root: Path
) -> dict[str, object] | None:
    """Select the sole unfinished current-incarnation run without changing it."""

    from prompt_workspace_interop import load_interop
    from prompt_workspace_runs import load_run_manifests

    active: list[dict[str, object]] = []
    try:
        for run_dir, manifest in load_run_manifests(runs_root):
            verified = _report_run_metadata(workspace, run_dir, manifest)
            coordinator = load_coordinator_state(run_dir)
            interop = load_interop(run_dir, required=False)
            phase = summary_phase(run_dir)
            if interop is not None and not _run_matches_workspace_incarnation(
                workspace, run_dir, interop
            ):
                if interop.get("released") is False:
                    raise PromptWorkspaceError(
                        "RUN_STATE_INVALID", "active run has mismatched lane identity"
                    )
                continue
            unfinished = (
                str(verified["status"]) not in TERMINAL_RUN_STATUSES
                or bool(verified["steering_pending"])
                or bool(verified["reconciliation_pending"])
                or (
                    coordinator is not None
                    and coordinator.get("status") in {"running", "blocked"}
                )
                or (
                    interop is not None
                    and interop.get("mode") == "lane"
                    and interop.get("released") is False
                )
                or phase in {"prepared", "sealed", "handoff_published"}
            )
            if unfinished:
                active.append(
                    {
                        "run_dir": run_dir,
                        "verified": verified,
                        "coordinator": coordinator,
                        "interop": interop,
                        "summary_phase": phase,
                    }
                )
    except PromptWorkspaceError as exc:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "lane status run evidence is invalid"
        ) from exc
    if len(active) > 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "workspace has multiple unfinished runs"
        )
    return active[0] if active else None


def _coordinator_progress(
    run_dir: Path, coordinator: dict[str, object]
) -> tuple[dict[str, object], str]:
    """Project validated coordinator/wave/task state into concise progress."""

    from prompt_workspace_waves import _load_task_plane, _load_wave

    indexed = coordinator["waves"]
    if coordinator.get("plan_sha256") != sha256_json(
        [entry.get("tasks") for entry in indexed if isinstance(entry, dict)]
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "coordinator plan digest is invalid"
        )
    waves: list[dict[str, object]] = []
    planes: list[tuple[dict[str, object], bool]] = []
    wave_ids: list[str] = []
    for entry in indexed:
        if not isinstance(entry, dict) or not isinstance(entry.get("wave_id"), str):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator wave entry is invalid"
            )
        wave_id = str(entry["wave_id"])
        wave_ids.append(wave_id)
        wave = _load_wave(run_dir, wave_id)
        planned_tasks = entry.get("tasks")
        if (
            not isinstance(planned_tasks, list)
            or [
                item.get("task_id")
                for item in planned_tasks
                if isinstance(item, dict)
            ]
            != wave["task_ids"]
            or entry.get("batches") != wave["batches"]
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "coordinator and wave plans differ"
            )
        promoted_head = wave.get("promoted_head")
        promoted = promoted_head is not None
        if promoted and (
            not isinstance(promoted_head, str)
            or SHA_RE.fullmatch(promoted_head) is None
            or wave.get("status") not in {"promoted", "cleanup", "done"}
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "wave promotion evidence is invalid"
            )
        if wave.get("status") in {"promoted", "cleanup", "done"} and not promoted:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "promoted wave has no promotion evidence"
            )
        for task_id in wave["task_ids"]:
            plane = _load_task_plane(run_dir, wave_id, str(task_id))
            if plane.get("state") != wave["task_states"].get(task_id):
                raise PromptWorkspaceError(
                    "EXECUTION_STATE_INVALID", "wave and task-plane state differ"
                )
            planes.append((plane, promoted))
        waves.append(wave)
    if len(wave_ids) != len(set(wave_ids)):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "coordinator wave IDs are duplicated"
        )
    active_wave = coordinator.get("active_wave")
    if active_wave is not None and active_wave not in wave_ids:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "coordinator active wave is not indexed"
        )

    total_tasks = len(planes)
    promoted_tasks = sum(
        1
        for plane, promoted in planes
        if promoted and plane["state"] != "superseded"
    )
    pending_tasks = sum(
        1
        for plane, promoted in planes
        if not promoted and plane["state"] == "planned"
    )
    in_progress_tasks = sum(
        1
        for plane, promoted in planes
        if not promoted
        and plane["state"] in {"assigned", "running", "committed", "merged"}
    )
    blocked_tasks = sum(
        1
        for plane, promoted in planes
        if not promoted and plane["state"] == "failed"
    )
    superseded_tasks = sum(
        1 for plane, _promoted in planes if plane["state"] == "superseded"
    )
    promoted_waves = sum(1 for wave in waves if wave["promoted_head"] is not None)
    worker_planned = sum(
        1 for plane, _promoted in planes if plane["state"] == "planned"
    )
    worker_created = sum(
        1
        for plane, _promoted in planes
        if plane["state"] == "assigned" and plane["dispatched_at"] is None
    )
    worker_queued = sum(
        1
        for plane, _promoted in planes
        if plane["state"] == "assigned" and plane["dispatched_at"] is not None
    )
    worker_active = sum(
        1 for plane, _promoted in planes if plane["state"] == "running"
    )
    worker_finished = sum(
        1
        for plane, _promoted in planes
        if plane["state"] in {"committed", "merged"}
    )
    worker_failed = sum(
        1 for plane, _promoted in planes if plane["state"] == "failed"
    )
    worker_superseded = sum(
        1 for plane, _promoted in planes if plane["state"] == "superseded"
    )

    coordinator_status = str(coordinator["status"])
    if coordinator_status == "blocked":
        phase = "blocked"
    elif coordinator_status == "done":
        phase = "finalizing"
    else:
        active = next(wave for wave in waves if wave["wave_id"] == active_wave)
        wave_status = str(active["status"])
        if wave_status == "planned":
            phase = "planning"
        elif wave_status == "preparing":
            phase = "preparing_wave"
        elif wave_status == "running":
            phase = "workers_running" if worker_active else "workers_queued"
        elif wave_status == "integrating":
            phase = "integrating"
        elif wave_status == "promotion_pending":
            phase = "promotion_pending"
        elif wave_status in {"promoted", "cleanup", "done"}:
            phase = "promoted_cleanup"
        else:
            phase = "blocked"
    return (
        {
            "tasks": {
                "total": total_tasks,
                "promoted": promoted_tasks,
                "pending": pending_tasks,
                "in_progress": in_progress_tasks,
                "blocked": blocked_tasks,
                "superseded": superseded_tasks,
                "remaining": total_tasks - promoted_tasks - superseded_tasks,
            },
            "workers": {
                "total": total_tasks,
                "planned": worker_planned,
                "created": worker_created,
                "queued": worker_queued,
                "active": worker_active,
                "finished": worker_finished,
                "failed": worker_failed,
                "superseded": worker_superseded,
            },
            "waves": {
                "total": len(waves),
                "promoted": promoted_waves,
                "active": 1 if isinstance(active_wave, str) else 0,
                "active_ordinal": (
                    wave_ids.index(active_wave) + 1
                    if isinstance(active_wave, str)
                    else None
                ),
                "remaining": len(waves) - promoted_waves,
            },
        },
        phase,
    )


def _active_run_projection(active: dict[str, object]) -> dict[str, object]:
    coordinator = active["coordinator"]
    if not isinstance(coordinator, dict):
        return {"status": "planning", "phase": "planning", "progress": None}
    progress, phase = _coordinator_progress(Path(active["run_dir"]), coordinator)
    interop = active["interop"]
    finalization_phase = active["summary_phase"]
    if coordinator["status"] == "blocked":
        status = "blocked"
    elif coordinator["status"] == "done":
        status = (
            "complete"
            if isinstance(interop, dict)
            and interop.get("released") is True
            and finalization_phase == "complete"
            else "finalizing"
        )
        phase = "complete" if status == "complete" else "finalizing"
    else:
        status = "running"
    return {"status": status, "phase": phase, "progress": progress}


def _sealed_run_projection(summary: dict[str, object]) -> dict[str, object]:
    work = summary["work"]
    tasks = int(work["tasks"]["total"])
    workers = int(work["temporary_worker_worktrees"])
    waves = int(work["waves"])
    return {
        "status": "complete",
        "phase": "complete",
        "source_status": str(summary["source_observation"]["status"]),
        "progress": {
            "tasks": {
                "total": tasks,
                "promoted": tasks,
                "pending": 0,
                "in_progress": 0,
                "blocked": 0,
                "superseded": 0,
                "remaining": 0,
            },
            "workers": {
                "total": workers,
                "planned": 0,
                "created": 0,
                "queued": 0,
                "active": 0,
                "finished": workers,
                "failed": 0,
                "superseded": 0,
            },
            "waves": {
                "total": waves,
                "promoted": waves,
                "active": 0,
                "active_ordinal": None,
                "remaining": 0,
            },
        },
    }


_CURRENT_STEPS = {
    "planning": "Preparing the immutable task and wave plan.",
    "preparing_wave": "Preparing the active wave.",
    "workers_queued": "Temporary workers are queued for the active wave.",
    "workers_running": "Temporary workers are executing the active wave.",
    "integrating": "Integrating accepted worker results.",
    "promotion_pending": "Validating and promoting the active wave.",
    "promoted_cleanup": "Cleaning resources after persistent-lane promotion.",
    "blocked": "The current run is blocked and requires correction.",
    "finalizing": "Finalizing and releasing the active lane generation.",
    "complete": "The latest run is complete in the persistent lane.",
    "unavailable": "Detailed progress is unavailable for this legacy generation.",
}


def _count_label(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _remaining_steps(
    status: str,
    current_run: dict[str, object] | None,
    generations: dict[str, int],
) -> list[str]:
    if status == "removed":
        return ["Initialize the managed workspace"]
    steps: list[str] = []
    if current_run is not None:
        progress = current_run.get("progress")
        if current_run["status"] == "blocked":
            steps.append("Resolve the current blocker")
        if isinstance(progress, dict):
            task_remaining = int(progress["tasks"]["remaining"])
            wave_remaining = int(progress["waves"]["remaining"])
            if task_remaining:
                steps.append(f"Finish {_count_label(task_remaining, 'remaining task')}")
            if wave_remaining:
                steps.append(f"Promote {_count_label(wave_remaining, 'remaining wave')}")
        if current_run["status"] not in {"complete", "blocked"}:
            steps.append("Finalize the active generation")
    if generations["active"] or generations["pending_integration"]:
        steps.append("Integrate pending generations into the source branch")
    if not steps:
        steps.append("Start the next Task Implementer run")
    return steps[:4]


def _next_action(
    *,
    status: str,
    project: Path,
    primary: Path,
    lane_root: Path,
    active: dict[str, object] | None,
    current_run: dict[str, object] | None,
    generations: dict[str, int],
) -> tuple[dict[str, str], dict[str, str]]:
    evidence: dict[str, str] = {}
    if status == "removed":
        return (
            {
                "action": "workspace init",
                "readiness": "ready",
                "invocation": f"$task-implementer workspace init {_quoted_path(project)}",
                "instruction": "Create a managed persistent lane before another run.",
            },
            evidence,
        )
    if active is not None or generations["active"] or generations["finalization_pending"]:
        blocked = current_run is not None and current_run["status"] == "blocked"
        finalizing = (
            generations["finalization_pending"] > 0
            or (current_run is not None and current_run["status"] == "finalizing")
        )
        return (
            {
                "action": "run",
                "readiness": (
                    "blocked" if blocked else "finalization_pending" if finalizing else "in_progress"
                ),
                "invocation": '$task-implementer run "<prompt-file>"',
                "instruction": (
                    "Resolve the current blocker in the same prompt, then run Task Implementer again."
                    if blocked
                    else "Continue the active Task Implementer run."
                ),
            },
            evidence,
        )
    if generations["pending_integration"]:
        source_clean, source_digest = _clean_evidence(primary)
        lane_clean, lane_digest = _clean_evidence(lane_root)
        evidence = {"source_status": source_digest, "lane_status": lane_digest}
        invocation = _quoted_invocation(project)
        if not source_clean:
            readiness = "commit_primary_first"
            instruction = f'Invoke $commit in "{project}" first, then {invocation}'
        elif not lane_clean:
            readiness = "lane_not_ready"
            instruction = "Resolve managed lane changes before integration."
        else:
            readiness = "ready"
            instruction = invocation
        if current_run is not None and current_run.get("source_status") == "moved":
            instruction += (
                " Source moved while workers were active; integration will rebuild "
                "and revalidate its candidate."
            )
        return (
            {
                "action": "integrate",
                "readiness": readiness,
                "invocation": invocation,
                "instruction": instruction,
            },
            evidence,
        )
    return (
        {
            "action": "run",
            "readiness": "ready",
            "invocation": '$task-implementer run "<prompt-file>"',
            "instruction": "The managed lane has no pending generation.",
        },
        evidence,
    )


def _lane_report_once(manifest_path: Path) -> tuple[dict[str, object], str]:
    from prompt_workspace_core import verify_workspace, verify_workspace_for_removal
    from prompt_workspace_interop import inspect_anchor

    manifest = load_json_object(manifest_path, "workspace manifest")
    primary = Path(required_string(manifest, "primary_root", "workspace manifest"))
    scope = required_string(manifest, "scope", "workspace manifest")
    project = primary if scope == "." else primary / PurePosixPath(scope)
    lane_root = Path(required_string(manifest, "repo_root", "workspace manifest"))
    removed = not lane_root.is_dir()
    git_environment = read_only_git_environment()
    try:
        workspace = (
            verify_workspace_for_removal(
                manifest_path, project, git_environment=git_environment
            )
            if removed
            else verify_workspace(manifest_path, git_environment=git_environment)
        )
    except PromptWorkspaceError as exc:
        if (not lane_root.is_dir()) != removed:
            raise _LaneReportUnstable from exc
        raise
    if (not lane_root.is_dir()) != removed:
        raise _LaneReportUnstable
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    anchor_before: dict[str, object] | None = None
    if not removed:
        try:
            anchor_before = inspect_anchor(workspace, environment=git_environment)
        except PromptWorkspaceError as exc:
            if not lane_root.is_dir():
                raise _LaneReportUnstable from exc
            raise
        if anchor_before.get("status") != "task-lane":
            if not lane_root.is_dir():
                raise _LaneReportUnstable
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "managed lane inspection is inconsistent"
            )
    state_before = _lane_state_digest(runs_root)
    error: PromptWorkspaceError | None = None
    try:
        finalization_pending = pending_finalization_generations(runs_root, workspace)
        active = _current_run(workspace, runs_root) if not removed else None
        if removed:
            latest = integrated = 0
            current_run = None
        else:
            assert anchor_before is not None
            latest = int(anchor_before["latest_generation"])
            integrated = int(anchor_before["last_integrated_generation"])
            if integrated < 0 or latest < integrated:
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "lane generation totals are invalid"
                )
            current_run = _active_run_projection(active) if active is not None else None
            if current_run is None and latest > integrated:
                pending_generations = set(range(integrated + 1, latest + 1))
                if len(pending_generations) > PENDING_GENERATIONS_LIMIT:
                    raise PromptWorkspaceError(
                        "RUN_STATE_INVALID",
                        "pending lane generations exceeded the reporting bound",
                    )
                summaries = [
                    (generation, summary)
                    for generation, summary in sealed_summaries(
                        runs_root, workspace, pending_generations
                    )
                    if summary is not None
                ]
                if not summaries:
                    current_run = {
                        "status": "unavailable",
                        "phase": "unavailable",
                        "progress": None,
                    }
                else:
                    _generation, summary = max(summaries, key=lambda item: item[0])
                    current_run = _sealed_run_projection(summary)
        generations = {
            "active": (
                1
                if active is not None
                and isinstance(active.get("interop"), dict)
                and active["interop"].get("released") is False
                else 0
            ),
            "released_total": latest,
            "integrated_total": integrated,
            "pending_integration": latest - integrated,
            "finalization_pending": len(finalization_pending),
        }
        next_action, git_evidence = _next_action(
            status="removed" if removed else "managed",
            project=project,
            primary=primary,
            lane_root=lane_root,
            active=active,
            current_run=current_run,
            generations=generations,
        )
        phase = str(current_run["phase"]) if current_run is not None else None
        report = {
            "schema": LANE_REPORT_SCHEMA,
            "status": "removed" if removed else "managed",
            "lane": {"state": "removed" if removed else anchor_before["lane_state"]},
            "generations": generations,
            "current_run": current_run,
            "current_step": (
                _CURRENT_STEPS.get(phase, "No Task Implementer run is active.")
                if not removed
                else "The managed persistent lane has been removed."
            ),
            "remaining_steps": _remaining_steps(
                "removed" if removed else "managed", current_run, generations
            ),
            "next_action": next_action,
        }
    except PromptWorkspaceError as caught:
        error = caught
        report = {}
        git_evidence = {}

    try:
        state_after = _lane_state_digest(runs_root)
        anchor_after = (
            inspect_anchor(workspace, environment=git_environment)
            if not removed
            else None
        )
    except PromptWorkspaceError as exc:
        raise _LaneReportUnstable from exc
    if (
        state_before != state_after
        or stable_json(anchor_before) != stable_json(anchor_after)
        or (not lane_root.is_dir()) != removed
    ):
        raise _LaneReportUnstable
    if error is not None:
        raise error
    fingerprint = _sha256(
        stable_json(
            {
                "anchor": anchor_before,
                "state": state_before,
                "git": git_evidence,
                "report": report,
            }
        )
    )
    return report, fingerprint


def lane_report(manifest_path: Path) -> dict[str, object]:
    """Return one concise, stable, zero-write lane status snapshot."""

    for _attempt in range(2):
        observations: list[tuple[dict[str, object], str]] = []
        unstable = False
        for _observation in range(2):
            try:
                observations.append(_lane_report_once(manifest_path))
            except _LaneReportUnstable:
                unstable = True
        if unstable:
            continue
        first, first_fingerprint = observations[0]
        second, second_fingerprint = observations[1]
        if first_fingerprint == second_fingerprint and first == second:
            return second
    raise PromptWorkspaceError(
        "WORKSPACE_BUSY",
        "Task Implementer state is changing; run the lane status task again.",
    )


def render_lane_report(report: dict[str, object]) -> str:
    """Render the bounded human view used by the generated workspace task."""

    generations = report["generations"]
    current = report.get("current_run")
    released = int(generations["released_total"])
    integrated = int(generations["integrated_total"])
    pending = int(generations["pending_integration"])
    active = int(generations["active"])
    finalizing = int(generations["finalization_pending"])
    lane_parts = [
        f"Lane: {report['lane']['state']}",
        f"{_count_label(released, 'generation')} released",
        f"{_count_label(integrated, 'generation')} integrated",
        f"{_count_label(pending, 'generation')} "
        f"{'awaits' if pending == 1 else 'await'} source integration",
        _count_label(active, "active generation"),
    ]
    if finalizing:
        lane_parts.append(f"{_count_label(finalizing, 'generation')} finalizing")
    lines = ["Task Implementer Lane Status", "; ".join(lane_parts)]
    if isinstance(current, dict):
        phase = str(current["phase"]).replace("_", " ")
        run_status = str(current["status"])
        lines.append(
            f"Run: {run_status}"
            + (f" — {phase}" if phase != run_status else "")
        )
        if current.get("source_status") is not None:
            lines.append(
                "Source since workers started: " + str(current["source_status"])
            )
        progress = current.get("progress")
        if isinstance(progress, dict):
            tasks = progress["tasks"]
            workers = progress["workers"]
            waves = progress["waves"]
            task_parts = [
                f"{tasks['total']} total",
                f"{tasks['promoted']} promoted",
            ]
            task_parts.extend(
                f"{tasks[key]} {label}"
                for key, label in (
                    ("pending", "pending"),
                    ("in_progress", "in progress"),
                    ("blocked", "blocked"),
                    ("superseded", "superseded"),
                )
                if tasks[key]
            )
            task_parts.append(f"{tasks['remaining']} remaining")
            worker_parts = [f"{workers['total']} total"]
            worker_parts.extend(
                f"{workers[key]} {label}"
                for key, label in (
                    ("planned", "planned"),
                    ("created", "created"),
                    ("queued", "queued"),
                    ("active", "active"),
                    ("finished", "finished"),
                    ("failed", "failed"),
                    ("superseded", "superseded"),
                )
                if workers[key]
            )
            active_wave = waves["active_ordinal"]
            wave_active = (
                f"wave {active_wave} active" if active_wave is not None else "none active"
            )
            lines.extend(
                [
                    "Tasks: " + "; ".join(task_parts),
                    "Temporary workers: " + "; ".join(worker_parts),
                    "Waves: "
                    f"{waves['total']} total; {waves['promoted']} promoted; "
                    f"{wave_active}; {waves['remaining']} remaining",
                ]
            )
    else:
        lines.append("Run: none active")
    lines.extend(
        [
            f"Current: {report['current_step']}",
            "Remaining: " + " -> ".join(report["remaining_steps"]),
            f"Next: {report['next_action']['instruction']}",
        ]
    )
    rendered = "\n".join(lines) + "\n"
    if len(rendered.encode("utf-8")) > LANE_REPORT_HUMAN_BYTES_LIMIT:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "human lane status exceeded its output bound"
        )
    return rendered
