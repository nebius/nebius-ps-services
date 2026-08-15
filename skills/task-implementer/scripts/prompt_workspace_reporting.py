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
import subprocess
import tempfile
import time
from typing import Iterable

from prompt_workspace_core import (
    PromptWorkspaceError,
    RUN_ID_RE,
    RUN_SCHEMA,
    load_json_object,
    require_mode,
    required_string,
    stable_json,
    write_atomic,
    write_exclusive,
)
from prompt_workspace_execution import SHA_RE, orchestration_dir


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
LANE_BRANCH_DISPLAY = "managed persistent lane (private branch redacted)"
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
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "LANG": "C",
            "LC_ALL": "C",
        }
    )
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


def lane_report(manifest_path: Path) -> dict[str, object]:
    """Inspect the managed lane and pending generations without mutating them."""

    from prompt_workspace_core import verify_workspace, verify_workspace_for_removal
    from prompt_workspace_interop import inspect_anchor, load_interop

    manifest = load_json_object(manifest_path, "workspace manifest")
    primary = Path(required_string(manifest, "primary_root", "workspace manifest"))
    scope = required_string(manifest, "scope", "workspace manifest")
    project = primary if scope == "." else primary / PurePosixPath(scope)
    lane_root = Path(required_string(manifest, "repo_root", "workspace manifest"))
    removed = not lane_root.is_dir()
    if removed:
        workspace = verify_workspace_for_removal(manifest_path, project)
        anchor: dict[str, object] | None = None
    else:
        workspace = verify_workspace(manifest_path)
        anchor = inspect_anchor(workspace)
        if anchor.get("status") != "task-lane":
            raise PromptWorkspaceError(
                "WORKTREE_CONFLICT", "managed lane inspection is inconsistent"
            )

    source_ref = required_string(workspace, "source_ref", "workspace manifest")
    source_head = _git_text(
        primary, ["rev-parse", "--verify", source_ref], "read source head"
    )
    source_head = _commit(primary, source_head, "source report head")
    runs_root = Path(required_string(workspace, "runs_root", "workspace manifest"))
    finalization_pending = pending_finalization_generations(runs_root, workspace)
    active = False
    for run_dir in sorted(
        path
        for path in runs_root.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and RUN_ID_RE.fullmatch(path.name) is not None
    ):
        interop = load_interop(run_dir, required=False)
        if (
            interop is not None
            and interop.get("mode") == "lane"
            and interop.get("released") is False
        ):
            if not _run_matches_workspace_incarnation(workspace, run_dir, interop):
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "active run has mismatched lane identity"
                )
            active = True
            break

    if anchor is not None:
        lane_head = _commit(primary, anchor.get("head"), "lane report head")
        latest = int(anchor["latest_generation"])
        integrated = int(anchor["last_integrated_generation"])
        pending_numbers = list(range(integrated + 1, latest + 1))
        summaries = dict(sealed_summaries(runs_root, workspace, set(pending_numbers)))
        comparison = diff_statistics(
            primary, source_head, lane_head, scope, require_ancestry=False
        )
        relationship = commit_relationship(primary, source_head, lane_head)
        source_clean = _clean(primary)
        lane_clean = _clean(lane_root)
        pending_summaries: list[dict[str, object]] = []
        for generation in pending_numbers:
            summary = summaries.get(generation)
            pending_summaries.append(
                {
                    "generation": generation,
                    "summary": (
                        "summary finalization pending"
                        if generation in finalization_pending
                        else (
                            summary
                            if summary is not None
                            else "summary unavailable for legacy generation"
                        )
                    ),
                }
            )
        invocation = _quoted_invocation(project)
        if active or finalization_pending:
            next_action = {
                "action": "run",
                "readiness": "finalization_pending",
                "invocation": '$task-implementer run "<prompt-file>"',
                "instruction": "Finish the active Task Implementer run finalization.",
            }
        elif pending_numbers:
            if not source_clean:
                readiness = "commit_primary_first"
                instruction = f'Invoke $commit in "{project}" first, then {invocation}'
            elif not lane_clean:
                readiness = "lane_not_ready"
                instruction = "Resolve managed lane changes before integration."
            else:
                readiness = "ready"
                instruction = invocation
            next_action = {
                "action": "integrate",
                "readiness": readiness,
                "invocation": invocation,
                "instruction": instruction,
            }
        else:
            next_action = {
                "action": "run",
                "readiness": "ready",
                "invocation": '$task-implementer run "<prompt-file>"',
                "instruction": "The managed lane has no pending generation.",
            }
        return {
            "schema": "task-implementer/lane-report-v1",
            "status": "managed",
            "source": {
                "branch": required_string(
                    workspace, "source_branch", "workspace manifest"
                ),
                "commit": source_head,
                "clean": source_clean,
            },
            "lane": {
                "branch": LANE_BRANCH_DISPLAY,
                "commit": lane_head,
                "clean": lane_clean,
                "state": anchor["lane_state"],
            },
            "generations": {
                "active": active,
                "pending": len(pending_numbers),
                "finalization_pending": len(finalization_pending),
            },
            "comparison": {
                "label": (
                    "source-to-lane comparison; histories diverged"
                    if relationship == "diverged"
                    else "source-to-lane comparison"
                ),
                "relationship": relationship,
                **comparison,
            },
            "pending_summaries": pending_summaries,
            "next_action": next_action,
        }

    last_summary = _latest_sealed_summary(runs_root, workspace)
    lane_head = (
        str(last_summary["lane"]["promoted_head"])
        if isinstance(last_summary, dict)
        else source_head
    )
    comparison = diff_statistics(
        primary, source_head, lane_head, scope, require_ancestry=False
    )
    return {
        "schema": "task-implementer/lane-report-v1",
        "status": "removed",
        "source": {
            "branch": required_string(workspace, "source_branch", "workspace manifest"),
            "commit": source_head,
            "clean": _clean(primary),
        },
        "lane": {
            "branch": LANE_BRANCH_DISPLAY,
            "commit": lane_head,
            "state": "removed",
        },
        "generations": {
            "active": False,
            "pending": 0,
            "finalization_pending": 0,
        },
        "comparison": {
            "label": "source-to-last-sealed-lane comparison",
            "relationship": commit_relationship(primary, source_head, lane_head),
            **comparison,
        },
        "pending_summaries": [],
        "next_action": {
            "action": "workspace init",
            "readiness": "ready",
            "invocation": f"$task-implementer workspace init {_quoted_path(project)}",
            "instruction": "Create a new managed persistent lane before another run.",
        },
    }
