#!/usr/bin/env python3
"""Validate Codex/Agent skill folder structure without network access."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


KNOWN_OPTIONAL_DIRS = {"agents", "assets", "references", "scripts"}
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
LOCAL_REF_RE = re.compile(
    r"(?P<path>(?:agents|assets|references|scripts)/[A-Za-z0-9._/@%+=:,~/-]+)"
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+)\)")


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
    if target.startswith(("agents/", "assets/", "references/", "scripts/")):
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


def validate_skill(skill_dir: Path) -> SkillResult:
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
        all_results.extend(validate_skill(skill) for skill in skills)

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
