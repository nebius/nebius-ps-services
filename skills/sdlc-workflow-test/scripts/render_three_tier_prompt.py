#!/usr/bin/env python3
"""Render the three-tier scenario into one canonical managed SDLC prompt."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
import tempfile


PROMPT_SCHEMA = "agentic-sdlc/prompt-v1"
FRONTMATTER_KEYS = ("schema", "prompt_id", "title", "created_at")
PLACEHOLDERS = (
    "PROJECT_ROOT",
    "PRIVATE_ROOT",
    "EVIDENCE_ROOT",
    "VERIFICATION_ID",
    "COMPOSE_PROJECT",
)
SECTIONS = (
    "Ask",
    "Outcome",
    "Context",
    "Constraints",
    "Acceptance criteria",
    "Verification",
    "Live Experiment Environment",
    "Non-goals",
    "References",
    "Steering",
)


class PromptRenderError(RuntimeError):
    """The managed starter or scenario template is invalid."""


def regular_file(path: Path, label: str) -> Path:
    candidate = path.expanduser().absolute()
    if candidate.is_symlink() or not candidate.is_file():
        raise PromptRenderError(f"{label} must be a regular file: {candidate}")
    return candidate


def frontmatter(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise PromptRenderError("Managed starter is missing YAML front matter.")
    end = next(
        (index for index, line in enumerate(lines[1:], 1) if line.rstrip("\r\n") == "---"),
        None,
    )
    if end is None:
        raise PromptRenderError("Managed starter front matter is not closed.")
    values: dict[str, str] = {}
    for line in lines[1:end]:
        key, separator, value = line.rstrip("\r\n").partition(":")
        if not separator or key in values:
            raise PromptRenderError("Managed starter front matter is malformed.")
        values[key] = value.strip()
    if tuple(values) != FRONTMATTER_KEYS or values["schema"] != PROMPT_SCHEMA:
        raise PromptRenderError("Managed starter identity fields are invalid.")
    if not values["prompt_id"] or not values["title"] or not values["created_at"]:
        raise PromptRenderError("Managed starter identity values are incomplete.")
    return "".join(lines[: end + 1]).rstrip("\r\n")


def render(starter: Path, template: Path, values: dict[str, str]) -> str:
    starter_text = regular_file(starter, "Managed starter").read_text(encoding="utf-8")
    template_text = regular_file(template, "Scenario template").read_text(encoding="utf-8")
    found = set(re.findall(r"\{\{([A-Z0-9_]+)\}\}", template_text))
    if found != set(PLACEHOLDERS) or set(values) != set(PLACEHOLDERS):
        raise PromptRenderError("Scenario template placeholders are invalid.")
    verification_id = values["VERIFICATION_ID"]
    if not re.fullmatch(r"[0-9a-f]{32}", verification_id):
        raise PromptRenderError("Verification ID must be 32 lowercase hex characters.")
    if values["COMPOSE_PROJECT"] != f"sdlc-workflow-test-{verification_id[:12]}":
        raise PromptRenderError("Compose project does not match the verification ID.")
    for name in ("PROJECT_ROOT", "PRIVATE_ROOT", "EVIDENCE_ROOT"):
        if not Path(values[name]).expanduser().is_absolute():
            raise PromptRenderError(f"Replacement {name} must be an absolute path.")
    for name, value in values.items():
        if not value or any(character in value for character in "\r\n"):
            raise PromptRenderError(f"Replacement {name} is invalid.")
        template_text = template_text.replace(f"{{{{{name}}}}}", value)
    if re.search(r"\{\{[A-Z0-9_]+\}\}", template_text):
        raise PromptRenderError("Scenario template contains an unresolved placeholder.")
    headings = re.findall(r"^## (.+)$", template_text, flags=re.MULTILINE)
    if tuple(headings) != SECTIONS:
        raise PromptRenderError("Scenario template section order is invalid.")
    return f"{frontmatter(starter_text)}\n\n{template_text.rstrip()}\n"


def write_atomic(path: Path, content: str) -> None:
    target = path.expanduser().absolute()
    if target.is_symlink() or not target.parent.is_dir():
        raise PromptRenderError(f"Prompt output path is unsafe: {target}")
    current_mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(temporary, current_mode)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Render the live three-tier body into a managed SDLC starter prompt."
    )
    result.add_argument("--starter", type=Path, required=True)
    result.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "assets"
        / "three-tier-prompt.md.template",
    )
    result.add_argument("--project-root", required=True)
    result.add_argument("--private-root", required=True)
    result.add_argument("--evidence-root", required=True)
    result.add_argument("--verification-id", required=True)
    result.add_argument("--compose-project", required=True)
    return result


def main() -> int:
    arguments = parser().parse_args()
    values = {
        "PROJECT_ROOT": arguments.project_root,
        "PRIVATE_ROOT": arguments.private_root,
        "EVIDENCE_ROOT": arguments.evidence_root,
        "VERIFICATION_ID": arguments.verification_id,
        "COMPOSE_PROJECT": arguments.compose_project,
    }
    try:
        content = render(arguments.starter, arguments.template, values)
        write_atomic(arguments.starter, content)
        print(arguments.starter.expanduser().absolute())
        return 0
    except (OSError, UnicodeError, PromptRenderError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
