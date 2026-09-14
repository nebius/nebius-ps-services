"""Claude instruction declarations and import checks; never a runtime-load claim."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .contracts import MAX_BODY_BYTES, ProjectInstructionsError, _canonical_json, _read_regular, _sha256_bytes


SCHEMA = "project-agent-instructions.claude-runtime-config.v1"


def fail(message: str) -> None:
    raise ProjectInstructionsError("DISCOVERY_CONTEXT_UNVERIFIED", message)


def _sources(value: object, target: Path) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > 128:
        fail("Claude declaration requires a bounded source list")
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            fail("Claude source identity is invalid")
        path = Path(str(item["path"]))
        if (not path.is_absolute() or path != path.resolve() or path == target
                or str(path) in seen):
            fail("Claude source path is unsafe or duplicates the managed target")
        raw = _read_regular(path, "Claude discovery source")
        if item["sha256"] != _sha256_bytes(raw):
            fail("Claude discovery source changed")
        seen.add(str(path))
        result.append(dict(item))
    return result


def imports(text: str) -> list[str]:
    """Recognize import tokens outside Markdown comments, fences and code spans."""
    visible = []
    position = 0
    while position < len(text):
        # Recognize outer Markdown boundaries before their contents. A comment
        # opener inside code is literal, and a fence inside a comment is inert.
        if position == 0 or text[position - 1] == "\n":
            fence = re.match(r" {0,3}(`{3,}|~{3,})([^\n]*)(?:\n|$)", text[position:])
            if fence and not (fence[1][0] == "`" and "`" in fence[2]):
                end = position + fence.end()
                closing = re.search(
                    r"^ {0,3}" + re.escape(fence[1][0]) + "{" + str(len(fence[1])) + r",}[ \t]*(?:\n|$)",
                    text[end:], flags=re.MULTILINE,
                )
                position = end + closing.end() if closing else len(text)
                visible.append("\n")
                continue
            indented = re.match(r"(?: {4}|\t)[^\n]*(?:\n|$)", text[position:])
            if indented:
                position += indented.end()
                visible.append("\n")
                continue
        if text.startswith("<!--", position):
            closing = text.find("-->", position + 4)
            position = closing + 3 if closing >= 0 else len(text)
            visible.append(" ")
            continue
        if text[position] == "`":
            delimiter = re.match(r"`+", text[position:])[0]
            end = position + len(delimiter)
            closing = re.search(r"(?<!`)" + re.escape(delimiter) + r"(?!`)", text[end:])
            if closing:
                position = end + closing.end()
                visible.append(" ")
                continue
            visible.append(delimiter)
            position = end
            continue
        visible.append(text[position])
        position += 1
    return re.findall(r"(?<![\w\\])@([^\s`<>()]+)", "".join(visible))



def discover(home: Path, git_root: Path, project: Path, declaration: Path):
    from .discovery import _git_ignored, _git_tracked, _instruction_entry

    target = project / "AGENTS.md"
    if declaration.is_relative_to(git_root) or declaration.stat().st_mode & 0o777 != 0o600:
        fail("Claude runtime declaration must be private and outside Git")
    raw = _read_regular(declaration, "Claude runtime declaration")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        fail("Claude runtime declaration is invalid")
    if (not isinstance(value, dict)
            or set(value) != {"schema", "session_sha256", "instruction_files", "settings_files"}
            or value.get("schema") != SCHEMA):
        fail("Claude runtime declaration has an unsupported shape")
    session = os.environ.get("SKILLS_SESSION_ID")
    if (os.environ.get("CODEX_THREAD_ID") or os.environ.get("SKILLS_AGENT") != "claude"
            or not session or value["session_sha256"] != hashlib.sha256(session.encode()).hexdigest()):
        fail("Claude discovery is not bound to the current native session")
    instructions = _sources(value["instruction_files"], target)
    settings = _sources(value["settings_files"], target)
    known_instructions = {item["path"] for item in instructions}
    known_settings = {item["path"] for item in settings}
    # These are a required discovery floor, not an assertion that a file list
    # proves the host loaded every source. Managed/CLI additions must be listed.
    directories = [home, *reversed((project, *project.parents))]
    managed = Path("/Library/Application Support/ClaudeCode" if sys.platform == "darwin" else "/etc/claude-code")
    required_instructions = [managed / "CLAUDE.md"]
    required_settings = [home / "settings.json", managed / "managed-settings.json"]
    for directory in directories:
        required_instructions.extend([directory / "CLAUDE.md", directory / "CLAUDE.local.md",
                                      directory / ".claude/CLAUDE.md"])
        for rules in (directory / ".claude/rules", directory / "rules" if directory == home else directory / ".claude/rules"):
            if rules.is_symlink():
                fail("Claude rules directory is unsafe")
            if rules.is_dir():
                required_instructions.extend(rules.rglob("*.md"))
        if directory.is_relative_to(git_root):
            required_settings.extend([directory / ".claude/settings.json", directory / ".claude/settings.local.json"])
    for paths, known in ((required_instructions, known_instructions), (required_settings, known_settings)):
        for path in paths:
            if path.is_symlink() or path.exists() and str(path.resolve()) not in known:
                fail("Claude declaration omits an existing discovery source")
    imports_target = False
    global_entries, ancestors = [], []
    for item in instructions:
        path = Path(item["path"])
        text = _read_regular(path, "Claude instruction source").decode("utf-8")
        is_project = path.is_relative_to(git_root) and _git_tracked(git_root, path) and not _git_ignored(git_root, path)
        for imported_name in imports(text):
            imported = (path.parent / Path(imported_name).expanduser()).resolve()
            if imported == target:
                imports_target |= is_project and path in {project / "CLAUDE.md", project / ".claude/CLAUDE.md"}
            elif str(imported) not in known_instructions:
                fail("Claude declaration omits an imported instruction source")
        entry = _instruction_entry(path, scope="project" if is_project else "global",
                                   kind="project-fallback" if is_project else "global", project_root=project)
        (ancestors if is_project else global_entries).append(entry)
    if not imports_target:
        fail("Claude requires an existing project CLAUDE.md import of AGENTS.md")
    if target.exists():
        for imported_name in imports(_read_regular(target, "managed instruction target").decode("utf-8")):
            imported = (target.parent / Path(imported_name).expanduser()).resolve()
            if imported == target or str(imported) not in known_instructions:
                fail("Claude declaration omits a target import or contains a self import")
    active = (_instruction_entry(target, scope="project", kind="project-agents", project_root=project)
              if target.exists() else None)
    config = {"fallback_filenames": [], "project_doc_max_bytes": MAX_BODY_BYTES,
              "project_root_markers": [".git"], "sources": settings + [{"path": str(declaration), "sha256": _sha256_bytes(raw)}],
              "runtime_config_sha256": _sha256_bytes(raw)}
    # The 4 KiB value is this repository's generated-body budget, not a Claude limit.
    config["sha256"] = _sha256_bytes(_canonical_json(config))
    return config, global_entries, ancestors, active
