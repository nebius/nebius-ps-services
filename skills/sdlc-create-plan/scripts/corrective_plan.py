#!/usr/bin/env python3
"""Validate append-only Agentic SDLC corrective plan versions."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any


MANIFEST_SCHEMA = "agentic-sdlc/completed-task-manifest-v1"
TASK_RE = re.compile(r"(?m)^### (TASK-([0-9]{3,}))\s*$")
PLAN_RE = re.compile(r"(?m)^# (FEAT-[0-9]{3,}) Plan v([0-9]+)\s*$")
DIGEST_RE = re.compile(r"(?:sha256:)?([0-9a-f]{64})")


class CorrectivePlanError(RuntimeError):
    """Fail-closed corrective plan contract error."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def plan_identity(text: str) -> tuple[str, int]:
    match = PLAN_RE.search(text)
    if match is None:
        raise CorrectivePlanError("plan heading is missing or malformed")
    return match.group(1), int(match.group(2))


def task_sections(text: str) -> dict[str, str]:
    matches = list(TASK_RE.finditer(text))
    result: dict[str, str] = {}
    for index, match in enumerate(matches):
        task_id = match.group(1)
        if task_id in result:
            raise CorrectivePlanError(f"duplicate task ID: {task_id}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.start() : end]
        next_heading = re.search(r"(?m)^## [^#]", section[match.end() - match.start() :])
        if next_heading is not None:
            section = section[: match.end() - match.start() + next_heading.start()]
        result[task_id] = section.rstrip() + "\n"
    if not result:
        raise CorrectivePlanError("plan contains no TASK records")
    return result


def task_definition_digests(text: str) -> dict[str, str]:
    return {
        task_id: sha256_bytes(section.encode("utf-8"))
        for task_id, section in task_sections(text).items()
    }


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise CorrectivePlanError("completed task manifest must not be a symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorrectivePlanError("completed task manifest is unreadable") from exc
    if not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA:
        raise CorrectivePlanError("completed task manifest schema is invalid")
    tasks = value.get("completed_tasks")
    if not isinstance(tasks, dict):
        raise CorrectivePlanError("completed_tasks must be an object")
    for task_id, digest in tasks.items():
        if TASK_RE.fullmatch(f"### {task_id}") is None:
            raise CorrectivePlanError(f"invalid completed task ID: {task_id}")
        if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
            raise CorrectivePlanError(
                f"completed task {task_id} has an invalid definition digest"
            )
    return value


def _field(text: str, label: str) -> str:
    match = re.search(rf"(?m)^- {re.escape(label)}:\s*(.+?)\s*$", text)
    if match is None or not match.group(1).strip():
        raise CorrectivePlanError(f"corrective plan is missing {label}")
    return match.group(1).strip()


def _task_number(task_id: str) -> int:
    return int(task_id.partition("-")[2])


def validate_corrective_plan(
    previous_plan: Path,
    corrective_plan: Path,
    completed_manifest: Path,
    diagnosis_id: str,
    regression_oracle: str,
) -> dict[str, Any]:
    """Prove one immutable vN+1 plan is an append-only correction."""

    for plan in (previous_plan, corrective_plan):
        if plan.is_symlink() or not plan.is_file():
            raise CorrectivePlanError(f"locked plan is unavailable: {plan.name}")
        lock = plan.with_suffix(plan.suffix + ".lock")
        if lock.is_symlink() or not lock.is_file():
            raise CorrectivePlanError(f"locked-plan marker is unavailable: {lock.name}")
    old_text = previous_plan.read_text(encoding="utf-8")
    new_text = corrective_plan.read_text(encoding="utf-8")
    old_feature, old_version = plan_identity(old_text)
    new_feature, new_version = plan_identity(new_text)
    if new_feature != old_feature or new_version != old_version + 1:
        raise CorrectivePlanError("corrective plan must be the adjacent feature plan version")
    if _field(new_text, "Plan kind") != "corrective":
        raise CorrectivePlanError("Plan kind must be corrective")
    if _field(new_text, "Supersedes") != previous_plan.name:
        raise CorrectivePlanError("Supersedes must name the exact prior plan")
    if _field(new_text, "Diagnosis") != diagnosis_id:
        raise CorrectivePlanError("Diagnosis must name the exact diagnosis-v1 record")
    if _field(new_text, "Regression oracle") != regression_oracle:
        raise CorrectivePlanError("Regression oracle changed")
    manifest = _load_manifest(completed_manifest)
    if manifest.get("feature_id") != old_feature:
        raise CorrectivePlanError("completed task manifest feature changed")
    if manifest.get("plan_digest") != sha256_bytes(previous_plan.read_bytes()):
        raise CorrectivePlanError("completed task manifest plan digest changed")
    manifest_digest = sha256_bytes(completed_manifest.read_bytes())
    if _field(new_text, "Completed task manifest digest") != f"sha256:{manifest_digest}":
        raise CorrectivePlanError("completed task manifest digest changed")
    old_sections = task_sections(old_text)
    new_sections = task_sections(new_text)
    old_digests = task_definition_digests(old_text)
    for task_id, section in old_sections.items():
        if new_sections.get(task_id) != section:
            raise CorrectivePlanError(
                f"corrective plan changes existing task definition {task_id}"
            )
    for task_id, recorded_digest in manifest["completed_tasks"].items():
        normalized = DIGEST_RE.fullmatch(recorded_digest)
        assert normalized is not None
        if old_digests.get(task_id) != normalized.group(1):
            raise CorrectivePlanError(
                f"completed task digest does not match prior plan: {task_id}"
            )
    old_ids = list(old_sections)
    new_ids = list(new_sections)
    corrective_ids = new_ids[len(old_ids) :]
    if new_ids[: len(old_ids)] != old_ids or not corrective_ids:
        raise CorrectivePlanError("corrective tasks must append after all existing task IDs")
    expected_next = max(_task_number(task_id) for task_id in old_ids) + 1
    expected_ids = [
        f"TASK-{number:03d}"
        for number in range(expected_next, expected_next + len(corrective_ids))
    ]
    if corrective_ids != expected_ids:
        raise CorrectivePlanError("corrective task IDs must be adjacent and append-only")
    declared = [
        item.strip()
        for item in _field(new_text, "Corrective tasks").split(",")
        if item.strip()
    ]
    if declared != corrective_ids:
        raise CorrectivePlanError("Corrective tasks does not match appended task IDs")
    for task_id in corrective_ids:
        section = new_sections[task_id]
        if _field(section, "Diagnosis") != diagnosis_id:
            raise CorrectivePlanError(f"{task_id} does not bind the diagnosis")
        if _field(section, "Regression oracle") != regression_oracle:
            raise CorrectivePlanError(f"{task_id} does not bind the regression oracle")
    return {
        "schema": "agentic-sdlc/corrective-plan-validation-v1",
        "feature_id": old_feature,
        "previous_version": old_version,
        "corrective_version": new_version,
        "previous_plan_digest": f"sha256:{sha256_bytes(previous_plan.read_bytes())}",
        "corrective_plan_digest": f"sha256:{sha256_bytes(corrective_plan.read_bytes())}",
        "completed_task_manifest_digest": f"sha256:{manifest_digest}",
        "preserved_task_ids": old_ids,
        "corrective_task_ids": corrective_ids,
        "diagnosis_id": diagnosis_id,
        "regression_oracle": regression_oracle,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-plan", type=Path, required=True)
    parser.add_argument("--corrective-plan", type=Path, required=True)
    parser.add_argument("--completed-manifest", type=Path, required=True)
    parser.add_argument("--diagnosis", required=True)
    parser.add_argument("--regression-oracle", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        result = validate_corrective_plan(
            args.previous_plan,
            args.corrective_plan,
            args.completed_manifest,
            args.diagnosis,
            args.regression_oracle,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except CorrectivePlanError as exc:
        print(json.dumps({"error": "CORRECTIVE_PLAN_INVALID", "message": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
