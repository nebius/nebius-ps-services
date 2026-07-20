#!/usr/bin/env python3
"""Create a sanitized Markdown report from bounded verifier summary data."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


ALLOWED_STATUS = {"PASS", "PARTIAL", "FAIL", "NOT_RUN"}

LIVE_STAGE_DEFINITIONS = (
    ("deterministic-verification", "Deterministic verification"),
    ("fixture-preparation", "Disposable fixture preparation"),
    ("workspace-initialization", "Task Implementer workspace initialization"),
    ("dependency-planning", "Dependency planning"),
    ("frontend-worker", "Frontend worker"),
    ("api-worker", "API worker"),
    ("database-worker", "Database worker"),
    ("wave-1-integration", "Wave 1 integration, promotion, and cleanup"),
    ("integration-runtime-worker", "Integration/runtime worker"),
    (
        "task-implementer-finalization",
        "Task Implementer final validation and alignment",
    ),
    ("unchanged-prompt-rerun", "Unchanged-prompt rerun"),
    ("compose-validation", "Compose validation"),
    ("runtime-launch", "Runtime launch"),
    ("application-semantics", "Application and database semantics"),
    ("results-validation", "Canonical results validation"),
    ("report-generation", "Report generation"),
    ("cleanup", "Owned-resource cleanup"),
)
LIVE_STAGE_NAMES = dict(LIVE_STAGE_DEFINITIONS)


def _clean(value: Any) -> str:
    text = (
        str(value)
        .replace("`", "'")
        .replace("|", "\\|")
        .replace("\r", " ")
        .replace("\n", " ")
        .strip()
    )
    return text[:500]


def default_live_stages() -> list[dict[str, str]]:
    return [
        {
            "id": stage_id,
            "name": name,
            "status": "NOT_RUN",
            "detail": "Stage was not reached.",
        }
        for stage_id, name in LIVE_STAGE_DEFINITIONS
    ]


def build_report(summary: dict[str, Any]) -> str:
    required = {
        "mode",
        "overall",
        "deterministic",
        "live",
        "lifecycle",
        "report_path",
        "stages",
        "next_action",
    }
    if set(summary) != required:
        raise ValueError("summary must contain exactly the required keys")
    for key in ("overall", "deterministic", "live"):
        if summary[key] not in ALLOWED_STATUS:
            raise ValueError(f"invalid {key} status")
    stages = summary["stages"]
    if not isinstance(stages, list) or not stages:
        raise ValueError("stages must be a non-empty list")
    seen: set[str] = set()
    validated_stages: list[dict[str, str]] = []
    for stage in stages:
        if not isinstance(stage, dict) or set(stage) != {
            "id",
            "name",
            "status",
            "detail",
        }:
            raise ValueError("each stage must contain id, name, status, and detail")
        if not isinstance(stage["id"], str) or not stage["id"] or stage["id"] in seen:
            raise ValueError("stage IDs must be unique non-empty strings")
        if stage["status"] not in ALLOWED_STATUS:
            raise ValueError("invalid stage status")
        seen.add(stage["id"])
        validated_stages.append(stage)
    failures = [
        stage for stage in validated_stages if stage["status"] in {"FAIL", "PARTIAL"}
    ]
    passed = [stage for stage in validated_stages if stage["status"] == "PASS"]
    not_run = [stage for stage in validated_stages if stage["status"] == "NOT_RUN"]
    counts = {
        status: sum(stage["status"] == status for stage in validated_stages)
        for status in ALLOWED_STATUS
    }
    lines = [
        "# Task Implementer Test Report",
        "",
        "## Summary",
        "",
        f"- Mode: `{_clean(summary['mode'])}`",
        f"- Overall: **{summary['overall']}**",
        f"- Deterministic profile: **{summary['deterministic']}**",
        f"- Live profile: **{summary['live']}**",
        f"- Lifecycle: `{_clean(summary['lifecycle'])}`",
        f"- Report: `{_clean(summary['report_path'])}`",
        (
            "- Stage totals: "
            f"{counts['PASS']} PASS, {counts['FAIL']} FAIL, "
            f"{counts['PARTIAL']} PARTIAL, {counts['NOT_RUN']} NOT_RUN"
        ),
        "",
        "## Stage Results",
        "",
        "| Stage | Status | Evidence or reason |",
        "| --- | --- | --- |",
    ]
    for stage in validated_stages:
        lines.append(
            f"| {_clean(stage['name'])} | **{stage['status']}** | {_clean(stage['detail'])} |"
        )
    lines.extend(["", "## Passed", ""])
    if passed:
        for stage in passed:
            lines.append(f"- **{_clean(stage['name'])}** - {_clean(stage['detail'])}")
    else:
        lines.append("- No stage passed.")
    lines.extend(["", "## Failure Analysis", ""])
    if failures:
        lines.extend(
            [
                "| Failed or partial stage | Status | What failed or blocked |",
                "| --- | --- | --- |",
            ]
        )
        for stage in failures:
            lines.append(
                f"| {_clean(stage['name'])} | **{stage['status']}** | {_clean(stage['detail'])} |"
            )
    else:
        lines.append("- No failed or partial stage was recorded.")
    lines.extend(["", "## Not Run", ""])
    if not_run:
        for stage in not_run:
            lines.append(f"- **{_clean(stage['name'])}** - {_clean(stage['detail'])}")
    else:
        lines.append("- Every recorded stage ran.")
    lines.extend(
        [
            "",
            "## Next Action",
            "",
            f"- {_clean(summary['next_action'])}",
            "",
            "Raw logs, prompt bodies, credentials, and private orchestration IDs are intentionally omitted.",
            "",
        ]
    )
    return "\n".join(lines)


def write_private(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError("report path must not be a symlink")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.summary.read_text(encoding="utf-8"))
    write_private(args.output, build_report(data))
    print(str(args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
