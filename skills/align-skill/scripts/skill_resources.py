"""Extract explicit Markdown destinations and standalone bundled resource paths."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from skill_frontmatter import read_text

LINK_START = re.compile(r"\[(?:\\.|[^\]\\])*\]\(\s*")
BARE_PATH = re.compile(r"(?<![\w/])(?:agents|assets|evals|references|scripts)/[A-Za-z0-9._/@%+=:,~/-]+")
REMOTE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s<>]+")


def local_destination(raw: str) -> str | None:
    target = raw.strip()
    if (not target or target.startswith("#") or re.match(r"^[A-Za-z][\w+.-]*:", target)
            or any(char in target for char in ("*", "<", ">", "$", "{", "}"))):
        return None
    target = target.split("#", 1)[0]
    return unquote(re.sub(r"\\([\\ ()])", r"\1", target)) or None


def referenced_paths(skill_md: Path) -> set[str]:
    text = read_text(skill_md)
    refs = set()
    masked = list(text)
    for match in LINK_START.finditer(text):
        start = index = match.end()
        angle = index < len(text) and text[index] == "<"
        depth = 0
        if angle:
            start = index = index + 1
        while index < len(text):
            char = text[index]
            if char == "\\" and index + 1 < len(text):
                index += 2
                continue
            if angle:
                if char == ">":
                    break
            elif char == "(" :
                depth += 1
            elif char == ")":
                if not depth:
                    break
                depth -= 1
            elif char.isspace() and not depth:
                break
            index += 1
        destination = local_destination(text[start:index])
        if destination:
            refs.add(destination)
        # Also mask optional title text, which is prose rather than a path.
        end = text.find(")", index)
        if end < 0:
            end = index
        masked[match.start():end + 1] = " " * (end + 1 - match.start())
    # Reference-style links use their definition's destination, including LICENSE.
    for match in re.finditer(r'^ {0,3}\[[^\]]+\]:\s*(?:<([^>]+)>|(\S+))', text, re.MULTILINE):
        destination = local_destination(match[1] or match[2])
        if destination:
            refs.add(destination)
        masked[match.start():match.end()] = " " * (match.end() - match.start())
    remaining = REMOTE.sub("", "".join(masked))
    for match in BARE_PATH.finditer(remaining):
        raw = match.group().rstrip(".,;:")
        # Bare extensionless mentions often denote commands, not resource files.
        if raw.endswith("/") or "." in raw.rsplit("/", 1)[-1]:
            destination = local_destination(raw)
            if destination:
                refs.add(destination)
    return refs
