"""Read-only project metadata and documented-feature analysis."""

from __future__ import annotations

import configparser
import json
import re
import tomllib
from pathlib import Path

MAX_DESCRIPTION_CHARS = 240
MAX_FEATURE_CHARS = 120
DOCUMENT_LANGUAGES = {"Markdown"}
CONFIG_DATA_LANGUAGES = {
    "CMake",
    "Dockerfile",
    "CSV",
    "JSON",
    "Makefile",
    "Properties",
    "Starlark",
    "TOML",
    "TSV",
    "XML",
    "YAML",
}
README_NAMES = ("README.md", "README.rst", "README.txt", "README")
FEATURE_HEADINGS = {
    "capabilities",
    "features",
    "key capabilities",
    "key features",
    "what it does",
}


def primary_readme(root: Path) -> Path | None:
    entries = {
        path.name.lower(): path
        for path in root.iterdir()
        if path.is_file() and not path.is_symlink()
    }
    for name in README_NAMES:
        if match := entries.get(name.lower()):
            return match
    return None


def markdown_plain_text(value: str) -> str:
    text = re.sub(r"!\[[^]]*]\([^)]*\)", "", value)
    text = re.sub(r"\[([^]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"<https?://[^>]+>", "", text)
    text = re.sub(r"https?://\S+", "[link omitted]", text)
    text = re.sub(r"[*_`~]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("<", "&lt;").replace(">", "&gt;")


def bounded_text(value: str, limit: int) -> str:
    value = markdown_plain_text(value)
    if len(value) <= limit:
        return value
    shortened = value[: limit - 1].rsplit(" ", 1)[0]
    return f"{shortened or value[: limit - 1]}…"


def manifest_description(root: Path) -> tuple[str, str] | None:
    pyproject = root / "pyproject.toml"
    if pyproject.is_file() and not pyproject.is_symlink():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            project = data.get("project")
            tool = data.get("tool")
            poetry = tool.get("poetry") if isinstance(tool, dict) else None
            candidates = (
                project.get("description") if isinstance(project, dict) else None,
                poetry.get("description") if isinstance(poetry, dict) else None,
            )
            if description := next(
                (item for item in candidates if isinstance(item, str) and item.strip()),
                None,
            ):
                return bounded_text(
                    description, MAX_DESCRIPTION_CHARS
                ), "pyproject.toml"
        except (OSError, tomllib.TOMLDecodeError):
            pass

    for filename, keys in (
        ("package.json", ("description",)),
        ("composer.json", ("description",)),
    ):
        path = root / filename
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        value = data
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, str) and value.strip():
            return bounded_text(value, MAX_DESCRIPTION_CHARS), filename

    cargo = root / "Cargo.toml"
    if cargo.is_file() and not cargo.is_symlink():
        try:
            data = tomllib.loads(cargo.read_text(encoding="utf-8"))
            package = data.get("package")
            value = package.get("description") if isinstance(package, dict) else None
            if isinstance(value, str) and value.strip():
                return bounded_text(value, MAX_DESCRIPTION_CHARS), "Cargo.toml"
        except (OSError, tomllib.TOMLDecodeError):
            pass

    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file() and not setup_cfg.is_symlink():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(setup_cfg, encoding="utf-8")
            value = parser.get("metadata", "description", fallback="")
            if value.strip():
                return bounded_text(value, MAX_DESCRIPTION_CHARS), "setup.cfg"
        except (configparser.Error, OSError):
            pass
    return None


def readme_description(root: Path) -> tuple[str, str] | None:
    readme = primary_readme(root)
    if not readme:
        return None
    try:
        lines = readme.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    paragraph: list[str] = []
    in_fence = False
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not stripped:
            if paragraph:
                break
            continue
        if (
            stripped.startswith(("#", "![", "[![", "<", "---", "==="))
            or re.match(r"^[-*+]\s+", stripped)
            or re.match(r"^\d+[.)]\s+", stripped)
        ):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    text = bounded_text(" ".join(paragraph), MAX_DESCRIPTION_CHARS)
    return (text, readme.name) if text else None


def project_description(root: Path) -> tuple[str, str] | None:
    return manifest_description(root) or readme_description(root)


def documented_features(root: Path) -> tuple[list[str], str | None]:
    readme = primary_readme(root)
    if not readme:
        return [], None
    try:
        lines = readme.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return [], None
    active_level: int | None = None
    in_fence = False
    features: list[str] = []
    for raw in lines:
        if raw.strip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", raw)
        if heading:
            level = len(heading.group(1))
            title = markdown_plain_text(heading.group(2)).lower().rstrip(":")
            if title in FEATURE_HEADINGS:
                active_level = level
                continue
            if active_level is not None and level <= active_level:
                active_level = None
            continue
        if active_level is None:
            continue
        item = re.match(r"^[-*+]\s+(.+?)\s*$", raw)
        if not item:
            item = re.match(r"^\d+[.)]\s+(.+?)\s*$", raw)
        if item:
            value = bounded_text(item.group(1), MAX_FEATURE_CHARS)
            if value and value not in features:
                features.append(value)
    return features, readme.name


def loc_category(language: str) -> str:
    if language in DOCUMENT_LANGUAGES:
        return "documentation"
    if language in CONFIG_DATA_LANGUAGES:
        return "configuration/data"
    return "code"


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True
