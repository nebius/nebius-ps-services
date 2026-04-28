"""Line-count helpers for repository archives."""

from __future__ import annotations

import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

from github_report.models import LocLanguageRow, LocReport, LocReportMetadata, LocTarget


@dataclass(slots=True, frozen=True)
class LanguageDefinition:
    """Minimal language metadata for physical source line counting."""

    name: str
    extensions: tuple[str, ...] = ()
    filenames: tuple[str, ...] = ()
    line_comments: tuple[str, ...] = ()
    block_comments: tuple[tuple[str, str], ...] = ()


@dataclass(slots=True, frozen=True)
class LineCounts:
    """Physical line totals for one file."""

    code_lines: int = 0
    comment_lines: int = 0
    blank_lines: int = 0
    total_lines: int = 0


LANGUAGE_DEFINITIONS: tuple[LanguageDefinition, ...] = (
    LanguageDefinition(
        "Python",
        extensions=(".py", ".pyi"),
        line_comments=("#",),
        block_comments=(('"""', '"""'), ("'''", "'''")),
    ),
    LanguageDefinition(
        "Shell",
        extensions=(".bash", ".bats", ".fish", ".ksh", ".sh", ".zsh"),
        filenames=("configure",),
        line_comments=("#",),
    ),
    LanguageDefinition(
        "Terraform",
        extensions=(".hcl", ".tf", ".tfvars"),
        line_comments=("#", "//"),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition("YAML", extensions=(".yaml", ".yml"), line_comments=("#",)),
    LanguageDefinition("JSON", extensions=(".json",)),
    LanguageDefinition("TOML", extensions=(".toml",), line_comments=("#",)),
    LanguageDefinition(
        "JavaScript",
        extensions=(".cjs", ".js", ".jsx", ".mjs"),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "TypeScript",
        extensions=(".ts", ".tsx"),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "Go", extensions=(".go",), line_comments=("//",), block_comments=(("/*", "*/"),)
    ),
    LanguageDefinition(
        "Rust",
        extensions=(".rs",),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "C/C++",
        extensions=(".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "Java",
        extensions=(".java",),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "C#",
        extensions=(".cs",),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "Kotlin",
        extensions=(".kt", ".kts"),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "Scala",
        extensions=(".scala", ".sc"),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition("Ruby", extensions=(".rb",), line_comments=("#",)),
    LanguageDefinition(
        "PHP",
        extensions=(".php",),
        line_comments=("//", "#"),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "Swift",
        extensions=(".swift",),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "SQL",
        extensions=(".sql",),
        line_comments=("--",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition(
        "CSS", extensions=(".css", ".scss", ".sass", ".less"), block_comments=(("/*", "*/"),)
    ),
    LanguageDefinition(
        "HTML",
        extensions=(".htm", ".html"),
        block_comments=(("<!--", "-->"),),
    ),
    LanguageDefinition(
        "XML",
        extensions=(".plist", ".svg", ".xml", ".xsd"),
        block_comments=(("<!--", "-->"),),
    ),
    LanguageDefinition(
        "Protocol Buffers",
        extensions=(".proto",),
        line_comments=("//",),
        block_comments=(("/*", "*/"),),
    ),
    LanguageDefinition("Dockerfile", filenames=("dockerfile",), line_comments=("#",)),
    LanguageDefinition("Makefile", filenames=("gnumakefile", "makefile"), line_comments=("#",)),
)

LANGUAGES_BY_EXTENSION = {
    extension: definition
    for definition in LANGUAGE_DEFINITIONS
    for extension in definition.extensions
}
LANGUAGES_BY_FILENAME = {
    filename: definition for definition in LANGUAGE_DEFINITIONS for filename in definition.filenames
}

EXCLUDED_DIR_NAMES = {
    ".eggs",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".terraform",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "target",
    "vendor",
}

EXCLUDED_FILE_NAMES = {
    ".terraform.lock.hcl",
    "cargo.lock",
    "composer.lock",
    "go.sum",
    "package-lock.json",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}


def build_loc_report_from_zip(
    archive_bytes: bytes,
    *,
    target: LocTarget,
    ref: str,
    generated_at,
) -> LocReport:
    """Build a line-count report from a GitHub zipball payload."""

    scope_path = target.path
    language_rows: dict[str, LocLanguageRow] = {}
    files_skipped = 0
    scope_seen = False

    with zipfile.ZipFile(BytesIO(archive_bytes)) as archive:
        for archive_entry in archive.infolist():
            if archive_entry.is_dir():
                continue
            repo_path = _strip_archive_root(archive_entry.filename)
            if not repo_path or not _is_in_scope(repo_path, scope_path):
                continue

            scope_seen = True
            if _should_skip_path(repo_path):
                files_skipped += 1
                continue

            language = detect_language(repo_path)
            if language is None:
                files_skipped += 1
                continue

            file_content = archive.read(archive_entry)
            text = decode_text(file_content)
            if text is None:
                files_skipped += 1
                continue

            counts = count_text_lines(text, language)
            row = language_rows.setdefault(language.name, LocLanguageRow(language=language.name))
            row.file_count += 1
            row.code_lines += counts.code_lines
            row.comment_lines += counts.comment_lines
            row.blank_lines += counts.blank_lines
            row.total_lines += counts.total_lines

    if not scope_seen:
        scope_label = scope_path or "repository root"
        raise ValueError(f"Path {scope_label!r} was not found in {target.full_name}@{ref}.")

    rows = sorted(
        language_rows.values(),
        key=lambda row: (-row.code_lines, -row.file_count, row.language),
    )
    return LocReport(
        metadata=LocReportMetadata(
            owner=target.owner,
            repo=target.repo,
            ref=ref,
            path=scope_path,
            generated_at=generated_at,
            files_counted=sum(row.file_count for row in rows),
            files_skipped=files_skipped,
            branch_scope=f"{ref} archive",
        ),
        language_rows=rows,
    )


def detect_language(repo_path: str) -> LanguageDefinition | None:
    """Detect a source language from a repository-relative path."""

    filename = PurePosixPath(repo_path).name
    lower_filename = filename.lower()
    if lower_filename in LANGUAGES_BY_FILENAME:
        return LANGUAGES_BY_FILENAME[lower_filename]

    suffix = PurePosixPath(lower_filename).suffix
    return LANGUAGES_BY_EXTENSION.get(suffix)


def decode_text(file_content: bytes) -> str | None:
    """Decode a source file payload, skipping binary or non-UTF-8 content."""

    if b"\x00" in file_content[:4096]:
        return None
    try:
        return file_content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def count_text_lines(text: str, language: LanguageDefinition) -> LineCounts:
    """Count physical blank, comment, and code lines for text content."""

    code_lines = 0
    comment_lines = 0
    blank_lines = 0
    in_block_comment: str | None = None
    total_lines = 0

    for raw_line in text.splitlines():
        total_lines += 1
        line = raw_line.strip()
        if not line:
            blank_lines += 1
            continue
        has_code, in_block_comment = _line_has_code(
            line,
            language,
            in_block_comment,
        )
        if has_code:
            code_lines += 1
        else:
            comment_lines += 1

    return LineCounts(
        code_lines=code_lines,
        comment_lines=comment_lines,
        blank_lines=blank_lines,
        total_lines=total_lines,
    )


def _line_has_code(
    line: str,
    language: LanguageDefinition,
    in_block_comment: str | None,
) -> tuple[bool, str | None]:
    has_code = False
    remaining = line
    active_block_end = in_block_comment

    while remaining:
        if active_block_end is not None:
            end_index = remaining.find(active_block_end)
            if end_index == -1:
                return has_code, active_block_end
            remaining = remaining[end_index + len(active_block_end) :].strip()
            active_block_end = None
            continue

        comment_start = _find_earliest_marker(
            remaining,
            [prefix for prefix in language.line_comments if prefix],
        )
        block_start = _find_earliest_block_start(remaining, language.block_comments)
        marker_start = _earliest_index(comment_start, block_start[0])
        if marker_start is None:
            return True, None
        if marker_start > 0:
            return True, None
        if comment_start == 0 and (block_start[0] is None or comment_start <= block_start[0]):
            return has_code, None

        block_index, block_end = block_start
        if block_index == 0 and block_end is not None:
            end_index = remaining.find(block_end, len(block_end))
            if end_index == -1:
                return has_code, block_end
            remaining = remaining[end_index + len(block_end) :].strip()
            continue

        return has_code, active_block_end

    return has_code, active_block_end


def _find_earliest_marker(text: str, markers: Iterable[str]) -> int | None:
    indexes = [text.find(marker) for marker in markers if text.find(marker) != -1]
    return min(indexes) if indexes else None


def _find_earliest_block_start(
    text: str,
    block_comments: tuple[tuple[str, str], ...],
) -> tuple[int | None, str | None]:
    matches = [
        (text.find(start), end)
        for start, end in block_comments
        if start and end and text.find(start) != -1
    ]
    if not matches:
        return None, None
    return min(matches, key=lambda match: match[0])


def _earliest_index(first: int | None, second: int | None) -> int | None:
    indexes = [index for index in (first, second) if index is not None]
    return min(indexes) if indexes else None


def _strip_archive_root(archive_path: str) -> str:
    normalized = archive_path.strip("/")
    if "/" not in normalized:
        return ""
    return normalized.split("/", 1)[1]


def _is_in_scope(repo_path: str, scope_path: str) -> bool:
    if not scope_path:
        return True
    return repo_path == scope_path or repo_path.startswith(f"{scope_path}/")


def _should_skip_path(repo_path: str) -> bool:
    parts = PurePosixPath(repo_path).parts
    if any(part.lower() in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return True
    filename = parts[-1].lower()
    if filename in EXCLUDED_FILE_NAMES:
        return True
    return filename.endswith((".min.css", ".min.js"))
