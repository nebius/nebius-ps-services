#!/usr/bin/env python3
"""Validate Codex/Agent skill folder structure without network access."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


KNOWN_OPTIONAL_DIRS = {"agents", "assets", "evals", "references", "scripts"}
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
LOCAL_REF_RE = re.compile(
    r"(?P<path>"
    r"(?:agents|assets|evals|references|scripts)/[A-Za-z0-9._/@%+=:,~/-]+"
    r")"
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+)\)")
LEARNING_LOOP_HEADING = "\n## Learning Loop\n"
LEARNING_LOOP_REQUIRED_SNIPPETS = (
    "capture durable, reusable, public-safe learnings",
    "contract allows source edits",
    "narrowest appropriate surface",
    "read-only/report-only",
    "Do not capture secrets",
    "unverified/vendor-specific claims",
    "report that it was skipped",
)
STATEFUL_WORKFLOW_REQUIRED_HEADINGS = (
    "## Purpose",
    "## When To Use",
    "## When Not To Use",
    "## Inputs",
    "## Required Reads",
    "## Writes",
    "## Process",
    "## Idempotency",
    "## Failure Handling",
    "## Must Not",
    "## Completion Criteria",
    "## Output Contract",
)
SDLC_ONLY_DESCRIPTION_PREFIX = "Use only as part of the Agentic SDLC workflow;"
EXPLICIT_ONLY_SKILL_NAMES = {
    "agent-nebius-auth",
    "agentic-sdlc-test",
    "apply-security",
    "attach-ubuntu",
    "code-info",
    "commit",
    "commit-push",
    "config-codex",
    "create-pr",
    "install-grafana-mcp-for-nebius",
    "merge-pr",
    "publish-helm",
    "publish-image",
    "publish-release",
    "review-pr",
}


@dataclass
class SkillResult:
    path: Path
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a single skill folder or a folder containing multiple "
            "skills. Uses Python standard library only and performs no "
            "network calls."
        ),
        epilog=(
            "Examples:\n"
            "  python3 scripts/validate-skill-structure.py .\n"
            "  python3 scripts/validate-skill-structure.py skills/align-skill\n"
            "  python3 scripts/validate-skill-structure.py skills/"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "targets",
        nargs="+",
        type=Path,
        help="Skill folder or parent folder containing skill folders.",
    )
    parser.add_argument(
        "--profile",
        choices=("basic", "stateful-workflow"),
        default="basic",
        help=(
            "Optional validation profile. The default basic profile checks "
            "generic skill structure only. stateful-workflow additionally "
            "requires the standard state-machine skill sections."
        ),
    )
    return parser.parse_args(argv)


def read_frontmatter(skill_md: Path, result: SkillResult) -> dict[str, str]:
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        result.failures.append(f"cannot read SKILL.md: {exc}")
        return {}

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        result.failures.append("SKILL.md is missing YAML front matter")
        return {}

    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break

    if end_index is None:
        result.failures.append("SKILL.md front matter is not closed")
        return {}

    metadata: dict[str, str] = {}
    current_key: str | None = None
    for line in lines[1:end_index]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and current_key:
            metadata[current_key] = f"{metadata[current_key]} {line.strip()}".strip()
            continue
        if ":" not in line:
            result.warnings.append(f"front matter line is not parsed: {line!r}")
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        metadata[current_key] = value.strip().strip("\"'")

    return metadata


def is_valid_name(name: str) -> bool:
    return (
        bool(NAME_RE.fullmatch(name))
        and "--" not in name
        and len(name) <= 64
        and not name.startswith("-")
        and not name.endswith("-")
    )


def clean_reference(raw: str) -> str | None:
    target = raw.strip().strip("`'\".,;:")
    if (
        not target
        or target.startswith(("#", "http://", "https://", "mailto:"))
        or "*" in target
        or "<" in target
        or ">" in target
        or "$" in target
    ):
        return None
    if "#" in target:
        target = target.split("#", 1)[0]
    if target.startswith(
        ("agents/", "assets/", "evals/", "references/", "scripts/")
    ):
        name = target.rstrip("/").rsplit("/", 1)[-1]
        if not target.endswith("/") and "." not in name:
            return None
    return target or None


def referenced_paths(skill_md: Path) -> set[str]:
    text = skill_md.read_text(encoding="utf-8")
    refs: set[str] = set()

    for match in MARKDOWN_LINK_RE.finditer(text):
        target = clean_reference(match.group("target"))
        if target and "/" in target:
            refs.add(target)

    for match in LOCAL_REF_RE.finditer(text):
        target = clean_reference(match.group("path"))
        if target:
            refs.add(target)

    return refs


def extract_learning_loop_section(skill_text: str) -> str | None:
    start = skill_text.find(LEARNING_LOOP_HEADING)
    if start == -1:
        return None

    section_start = start + len(LEARNING_LOOP_HEADING)
    next_heading = skill_text.find("\n## ", section_start)
    if next_heading == -1:
        return skill_text[section_start:]
    return skill_text[section_start:next_heading]


def validate_stateful_workflow_profile(skill_text: str, result: SkillResult) -> None:
    for heading in STATEFUL_WORKFLOW_REQUIRED_HEADINGS:
        if f"\n{heading}\n" not in f"\n{skill_text}\n":
            result.failures.append(
                f"stateful-workflow profile missing required heading: {heading}"
            )


def expected_implicit_invocation(name: str) -> str:
    if name.startswith("sdlc-") or name in EXPLICIT_ONLY_SKILL_NAMES:
        return "false"
    return "true"


def validate_openai_metadata_policy(skill_dir: Path, name: str, result: SkillResult) -> None:
    metadata_path = skill_dir / "agents" / "openai.yaml"
    if not metadata_path.exists():
        result.failures.append(
            "missing agents/openai.yaml metadata with "
            "policy.allow_implicit_invocation"
        )
        return
    if not metadata_path.is_file():
        result.failures.append("agents/openai.yaml exists but is not a file")
        return

    try:
        lines = metadata_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        result.failures.append(f"cannot read agents/openai.yaml: {exc}")
        return

    in_policy = False
    value: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            in_policy = stripped.split(":", 1)[0] == "policy"
            continue
        if in_policy and stripped.startswith("allow_implicit_invocation:"):
            value = stripped.split(":", 1)[1].strip().strip("\"'")

    if value is None:
        result.failures.append(
            "agents/openai.yaml is missing policy.allow_implicit_invocation"
        )
        return
    if value not in {"true", "false"}:
        result.failures.append(
            "policy.allow_implicit_invocation must be lowercase true or false"
        )
        return

    expected = expected_implicit_invocation(name)
    if value != expected:
        result.failures.append(
            "policy.allow_implicit_invocation must be "
            f"{expected} for {name}"
        )


def validate_skill(skill_dir: Path, *, profile: str = "basic") -> SkillResult:
    result = SkillResult(path=skill_dir)
    skill_md = skill_dir / "SKILL.md"

    if not skill_md.exists():
        result.failures.append("missing SKILL.md")
        return result
    if not skill_md.is_file():
        result.failures.append("SKILL.md exists but is not a file")
        return result

    metadata = read_frontmatter(skill_md, result)
    name = metadata.get("name", "")
    description = metadata.get("description", "")

    if not name:
        result.failures.append("front matter is missing name")
    elif not is_valid_name(name):
        result.failures.append(
            "name must be lowercase alphanumeric with single hyphens, "
            "1-64 characters, and no leading or trailing hyphen"
        )
    elif name != skill_dir.name:
        result.failures.append(
            f"name {name!r} does not match parent folder {skill_dir.name!r}"
        )

    if not description:
        result.failures.append("front matter is missing description")
    elif len(description) > 1024:
        result.failures.append("description exceeds 1024 characters")
    elif name.startswith("sdlc-") and not description.startswith(
        SDLC_ONLY_DESCRIPTION_PREFIX
    ):
        result.failures.append(
            "SDLC-only skills must start the description with: "
            f"{SDLC_ONLY_DESCRIPTION_PREFIX}"
        )
    elif description.startswith(SDLC_ONLY_DESCRIPTION_PREFIX) and not name.startswith(
        "sdlc-"
    ):
        result.failures.append(
            "skills with the SDLC-only description prefix must use an sdlc-* name"
        )

    if name:
        validate_openai_metadata_policy(skill_dir, name, result)

    try:
        skill_text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        result.failures.append(f"cannot read SKILL.md for learning loop: {exc}")
        skill_text = ""

    learning_loop = extract_learning_loop_section(skill_text)
    if skill_text and learning_loop is None:
        result.failures.append("SKILL.md is missing ## Learning Loop")
    elif learning_loop is not None:
        for snippet in LEARNING_LOOP_REQUIRED_SNIPPETS:
            if snippet not in learning_loop:
                result.failures.append(
                    f"## Learning Loop is missing required text: {snippet}"
                )

    if profile == "stateful-workflow" and skill_text:
        validate_stateful_workflow_profile(skill_text, result)

    for child in sorted(skill_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if child.name not in KNOWN_OPTIONAL_DIRS:
            result.warnings.append(
                f"non-canonical folder reported for review: {child.name}/"
            )

    try:
        refs = referenced_paths(skill_md)
    except OSError as exc:
        result.failures.append(f"cannot scan local references: {exc}")
        refs = set()

    for ref in sorted(refs):
        if not (skill_dir / ref).exists():
            result.failures.append(f"referenced local path does not exist: {ref}")

    return result


def discover_skills(target: Path) -> tuple[list[Path], list[str]]:
    target = target.expanduser()
    warnings: list[str] = []

    if not target.exists():
        return [], [f"{target}: path does not exist"]
    if not target.is_dir():
        return [], [f"{target}: path is not a directory"]
    if (target / "SKILL.md").exists():
        return [target], warnings

    child_dirs = [
        child
        for child in sorted(target.iterdir())
        if child.is_dir() and not child.name.startswith(".")
    ]
    skills = [child for child in child_dirs if (child / "SKILL.md").exists()]
    missing = [child for child in child_dirs if not (child / "SKILL.md").exists()]

    for child in missing:
        warnings.append(f"{child}: child directory has no SKILL.md")

    if not skills:
        warnings.append(f"{target}: no skill folders found")
    return skills, warnings


def print_result(result: SkillResult) -> None:
    status = "OK" if result.ok else "FAIL"
    print(f"{status} {result.path}")
    for warning in result.warnings:
        print(f"  WARN: {warning}")
    for failure in result.failures:
        print(f"  FAIL: {failure}")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    all_results: list[SkillResult] = []
    discovery_warnings: list[str] = []

    for target in args.targets:
        skills, warnings = discover_skills(target)
        discovery_warnings.extend(warnings)
        all_results.extend(validate_skill(skill, profile=args.profile) for skill in skills)

    for warning in discovery_warnings:
        print(f"WARN: {warning}")

    for result in all_results:
        print_result(result)

    if not all_results:
        print("FAIL: no skills validated")
        return 1

    failures = sum(len(result.failures) for result in all_results)
    warnings = len(discovery_warnings) + sum(
        len(result.warnings) for result in all_results
    )
    print(
        f"Validated {len(all_results)} skill(s): "
        f"{failures} failure(s), {warnings} warning(s)"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
