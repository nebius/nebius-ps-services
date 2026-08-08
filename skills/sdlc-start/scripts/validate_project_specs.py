#!/usr/bin/env python3
"""Validate Agentic SDLC product specs and emit a bound receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys


MAX_SPEC_BYTES = 1024 * 1024
REQUIREMENTS_SCHEMA = "agentic-sdlc.requirements.v1"
DESIGN_SCHEMA = "agentic-sdlc.design.v1"
RECEIPT_SCHEMA = "project-agent-instructions.spec-validation.v2"
REQ_RE = re.compile(r"REQ-[0-9]{3,}")
FEATURE_MARKER_RE = re.compile(
    r"<!-- FEATURE: (FEAT-[0-9]{3,}) reqs=([^ ]+) "
    r"status=(ready|draft|blocked|stale) priority=(P[0-9]+) version=([0-9]+) -->"
)
PLACEHOLDER_RE = re.compile(r"(?i)(?:\bTODO\b|\bTBD\b|<[^>\n]+>)")


class SpecValidationError(RuntimeError):
    """A stable validation failure safe to expose to the coordinator."""


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_digest(value: object) -> str:
    return _digest(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _read(path: Path, label: str) -> tuple[bytes, str]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise SpecValidationError(f"{label} is missing") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > MAX_SPEC_BYTES
        or metadata.st_mode & 0o022
    ):
        raise SpecValidationError(f"{label} path or mode is unsafe")
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise SpecValidationError(f"{label} contains invalid bytes")
    try:
        return raw, raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SpecValidationError(f"{label} must be UTF-8") from error


def _frontmatter(text: str, label: str) -> dict[str, str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if match is None:
        raise SpecValidationError(f"{label} has invalid frontmatter")
    values: dict[str, str] = {}
    for line in match.group(1).splitlines():
        item = re.fullmatch(r"([a-z_]+):\s*(.*?)\s*", line)
        if item is None or item.group(1) in values:
            raise SpecValidationError(f"{label} frontmatter is ambiguous")
        values[item.group(1)] = item.group(2).strip("\"'")
    return values


def _section(block: str, heading: str, identifier: str) -> str:
    match = re.search(
        rf"(?ms)^#### {re.escape(heading)}\s*\n(.*?)(?=^#### |^### |\Z)",
        block,
    )
    if match is None or not match.group(1).strip():
        raise SpecValidationError(f"{identifier} is missing {heading}")
    return match.group(1).strip()


def _require_bullets(block: str, heading: str, identifier: str) -> None:
    if re.search(r"(?m)^-\s+\S", _section(block, heading, identifier)) is None:
        raise SpecValidationError(f"{identifier} has no {heading} entries")


def _require_owner(
    frontmatter: dict[str, str], schema: str, skill: str, label: str
) -> None:
    if (
        frontmatter.get("schema") != schema
        or frontmatter.get("created_by_skill") != skill
        or frontmatter.get("updated_by_skill") != skill
        or not frontmatter.get("project")
        or not frontmatter.get("status")
    ):
        raise SpecValidationError(f"{label} ownership metadata is invalid")


def _validate_requirements(text: str) -> dict[str, object]:
    frontmatter = _frontmatter(text, "requirements")
    _require_owner(
        frontmatter,
        REQUIREMENTS_SCHEMA,
        "sdlc-create-requirements",
        "requirements",
    )
    if "task-implementer:requirements:start" in text:
        raise SpecValidationError("requirements has a foreign workflow owner")
    markers = list(
        re.finditer(
            r"<!-- REQUIREMENT: (REQ-[0-9]{3,}) "
            r"status=([a-z-]+) priority=(P[0-9]+) type=([a-z-]+) -->",
            text,
        )
    )
    if (
        not markers
        or text.count("<!-- REQUIREMENT:") != len(markers)
        or text.count("<!-- /REQUIREMENT:") != len(markers)
    ):
        raise SpecValidationError("requirements has no managed requirement blocks")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, marker in enumerate(markers):
        identifier = marker.group(1)
        end_marker = f"<!-- /REQUIREMENT: {identifier} -->"
        end = text.find(end_marker, marker.end())
        next_start = (
            markers[index + 1].start() if index + 1 < len(markers) else len(text)
        )
        if (
            end < 0
            or end >= next_start
            or identifier in seen
            or marker.group(2)
            not in {"draft", "active", "accepted", "satisfied", "superseded", "blocked"}
        ):
            raise SpecValidationError(f"{identifier} markers are invalid")
        block = text[marker.end() : end]
        if re.search(rf"(?m)^### {re.escape(identifier)}:\s+\S", block) is None:
            raise SpecValidationError(f"{identifier} heading is invalid")
        for heading in (
            "User Story",
            "Validation Method",
            "Test Method",
            "Evaluation Method",
        ):
            value = _section(block, heading, identifier)
            if PLACEHOLDER_RE.search(value):
                raise SpecValidationError(f"{identifier} has unresolved {heading}")
        for heading in ("Acceptance Criteria", "Negative Criteria"):
            _require_bullets(block, heading, identifier)
            if PLACEHOLDER_RE.search(_section(block, heading, identifier)):
                raise SpecValidationError(f"{identifier} has unresolved {heading}")
        records.append(
            {
                "id": identifier,
                "status": marker.group(2),
                "priority": marker.group(3),
                "type": marker.group(4),
                "sha256": _digest(block.strip().encode("utf-8")),
            }
        )
        seen.add(identifier)
    return {"frontmatter": frontmatter, "records": records}


def _validate_design(text: str, requirement_ids: set[str]) -> dict[str, object]:
    frontmatter = _frontmatter(text, "design")
    _require_owner(frontmatter, DESIGN_SCHEMA, "sdlc-create-design", "design")
    if (
        frontmatter.get("source_requirements") != "docs/requirements.md"
        or "task-implementer:design:start" in text
    ):
        raise SpecValidationError("design ownership or requirements source is invalid")
    markers = list(FEATURE_MARKER_RE.finditer(text))
    if (
        not markers
        or text.count("<!-- FEATURE:") != len(markers)
        or text.count("<!-- /FEATURE:") != len(markers)
    ):
        raise SpecValidationError("design has no managed feature blocks")
    records: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, marker in enumerate(markers):
        identifier = marker.group(1)
        end_marker = f"<!-- /FEATURE: {identifier} -->"
        end = text.find(end_marker, marker.end())
        next_start = (
            markers[index + 1].start() if index + 1 < len(markers) else len(text)
        )
        if end < 0 or end >= next_start or identifier in seen:
            raise SpecValidationError(f"{identifier} markers are invalid")
        block = text[marker.end() : end]
        if re.search(rf"(?m)^### {re.escape(identifier)}:\s+\S", block) is None:
            raise SpecValidationError(f"{identifier} heading is invalid")
        requirements = marker.group(2).split(",")
        if (
            not requirements
            or len(requirements) != len(set(requirements))
            or any(REQ_RE.fullmatch(item) is None for item in requirements)
            or set(requirements) - requirement_ids
        ):
            raise SpecValidationError(f"{identifier} requirement mapping is invalid")
        for heading in (
            "Requirements Covered",
            "Context Evidence",
            "Design Details",
            "Selected Option",
            "Alternatives Considered",
            "Implementation Boundaries",
            "Test-First Success Criteria",
            "Validation Plan",
            "Test Plan",
            "Evaluation Plan",
            "Rollout And Rollback",
            "Done Definition",
        ):
            _section(block, heading, identifier)
        covered: list[str] = []
        for line in _section(block, "Requirements Covered", identifier).splitlines():
            item = re.fullmatch(r"- (REQ-[0-9]{3,})(?:: \S.*)?", line.strip())
            if item is None:
                raise SpecValidationError(
                    f"{identifier} Requirements Covered has an invalid entry"
                )
            covered.append(item.group(1))
        if len(covered) != len(set(covered)) or set(covered) != set(requirements):
            raise SpecValidationError(
                f"{identifier} Requirements Covered does not match its marker"
            )
        if marker.group(3) == "ready" and PLACEHOLDER_RE.search(block):
            raise SpecValidationError(
                f"{identifier} is ready but contains placeholders"
            )
        records.append(
            {
                "id": identifier,
                "requirements": requirements,
                "status": marker.group(3),
                "priority": marker.group(4),
                "version": int(marker.group(5)),
                "sha256": _digest(block.strip().encode("utf-8")),
            }
        )
        seen.add(identifier)
    return {"frontmatter": frontmatter, "records": records}


def validate(project_root: Path) -> dict[str, object]:
    project_root = project_root.expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise SpecValidationError("project root is not in a Git worktree") from error
    git_root = Path(result.stdout.strip()).resolve()
    try:
        scope_path = project_root.relative_to(git_root)
    except ValueError as error:
        raise SpecValidationError("project root escaped the Git worktree") from error
    requirements_path = project_root / "docs" / "requirements.md"
    design_path = project_root / "docs" / "design.md"
    for path, label in (
        (requirements_path, "requirements"),
        (design_path, "design"),
    ):
        relative = path.relative_to(git_root).as_posix()
        try:
            tracked = subprocess.run(
                [
                    "git",
                    "-C",
                    str(git_root),
                    "ls-files",
                    "--error-unmatch",
                    "--",
                    relative,
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SpecValidationError(
                f"could not verify {label} Git tracking"
            ) from error
        if tracked.returncode != 0:
            raise SpecValidationError(f"{label} must be tracked in Git")
    requirements_raw, requirements_text = _read(requirements_path, "requirements")
    design_raw, design_text = _read(design_path, "design")
    requirements = _validate_requirements(requirements_text)
    requirement_ids = {str(record["id"]) for record in list(requirements["records"])}
    design = _validate_design(design_text, requirement_ids)
    applicable_requirements = {
        str(record["id"])
        for record in list(requirements["records"])
        if record["status"] != "superseded"
    }
    covered_requirements = {
        str(requirement)
        for record in list(design["records"])
        if record["status"] != "stale"
        for requirement in list(record["requirements"])
    }
    if covered_requirements != applicable_requirements:
        raise SpecValidationError(
            "current design features must map every applicable requirement exactly"
        )
    scope = "." if scope_path == Path(".") else scope_path.as_posix()
    return {
        "schema": RECEIPT_SCHEMA,
        "owner": "agentic-sdlc",
        "project_root": str(project_root),
        "git_root": str(git_root),
        "project_scope": scope,
        "validator": "sdlc-start/spec-validation",
        "validator_version": 2,
        "requirements": {
            "path": "docs/requirements.md",
            "sha256": _digest(requirements_raw),
        },
        "design": {"path": "docs/design.md", "sha256": _digest(design_raw)},
        "traceability_sha256": _canonical_digest(
            {
                "requirements": requirements,
                "design": design,
            }
        ),
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Agentic SDLC specs and emit a v2 private receipt."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = validate(args.project_root)
    except SpecValidationError as error:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "code": "SPEC_VALIDATION_REQUIRED",
                    "error": str(error),
                },
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
