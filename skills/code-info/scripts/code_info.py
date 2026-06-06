#!/usr/bin/env python3
"""Print a read-only, copy/paste-friendly codebase metrics report."""

from __future__ import annotations

import argparse
import fnmatch
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


LANGUAGE_BY_EXTENSION = {
    ".awk": "Awk",
    ".bash": "Shell",
    ".bat": "Batch",
    ".c": "C",
    ".cc": "C++",
    ".clj": "Clojure",
    ".cljs": "Clojure",
    ".cmake": "CMake",
    ".cpp": "C++",
    ".cs": "C#",
    ".css": "CSS",
    ".cxx": "C++",
    ".dart": "Dart",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".fish": "Shell",
    ".fs": "F#",
    ".go": "Go",
    ".graphql": "GraphQL",
    ".groovy": "Groovy",
    ".h": "C/C++ Header",
    ".hh": "C++ Header",
    ".hpp": "C++ Header",
    ".hs": "Haskell",
    ".html": "HTML",
    ".hxx": "C++ Header",
    ".java": "Java",
    ".jl": "Julia",
    ".js": "JavaScript",
    ".json": "JSON",
    ".jsx": "JavaScript",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".less": "Less",
    ".lua": "Lua",
    ".m": "Objective-C",
    ".md": "Markdown",
    ".mjs": "JavaScript",
    ".mm": "Objective-C++",
    ".php": "PHP",
    ".pl": "Perl",
    ".ps1": "PowerShell",
    ".py": "Python",
    ".r": "R",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".sass": "Sass",
    ".scala": "Scala",
    ".scss": "SCSS",
    ".sh": "Shell",
    ".sql": "SQL",
    ".swift": "Swift",
    ".tf": "Terraform",
    ".tfvars": "Terraform",
    ".toml": "TOML",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".vue": "Vue",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".zig": "Zig",
    ".zsh": "Shell",
}

LANGUAGE_BY_NAME = {
    "Dockerfile": "Dockerfile",
    "Jenkinsfile": "Groovy",
    "Justfile": "Just",
    "Makefile": "Makefile",
    "Rakefile": "Ruby",
    "Tiltfile": "Starlark",
}

COMMENT_PREFIXES = {
    "Awk": ("#",),
    "Batch": ("rem ", "::"),
    "C": ("//", "/*", "*", "*/"),
    "C#": ("//", "/*", "*", "*/"),
    "C++": ("//", "/*", "*", "*/"),
    "C++ Header": ("//", "/*", "*", "*/"),
    "C/C++ Header": ("//", "/*", "*", "*/"),
    "CMake": ("#",),
    "Clojure": (";",),
    "CSS": ("/*", "*", "*/"),
    "Dart": ("//", "/*", "*", "*/"),
    "Dockerfile": ("#",),
    "Elixir": ("#",),
    "F#": ("//",),
    "Go": ("//", "/*", "*", "*/"),
    "GraphQL": ("#",),
    "Groovy": ("//", "/*", "*", "*/"),
    "Haskell": ("--",),
    "HTML": ("<!--", "-->"),
    "Java": ("//", "/*", "*", "*/"),
    "JavaScript": ("//", "/*", "*", "*/"),
    "Julia": ("#",),
    "Just": ("#",),
    "Kotlin": ("//", "/*", "*", "*/"),
    "Less": ("//", "/*", "*", "*/"),
    "Lua": ("--",),
    "Makefile": ("#",),
    "Objective-C": ("//", "/*", "*", "*/"),
    "Objective-C++": ("//", "/*", "*", "*/"),
    "PHP": ("//", "#", "/*", "*", "*/"),
    "Perl": ("#",),
    "PowerShell": ("#",),
    "Python": ("#",),
    "R": ("#",),
    "Ruby": ("#",),
    "Rust": ("//", "/*", "*", "*/"),
    "SCSS": ("//", "/*", "*", "*/"),
    "SQL": ("--",),
    "Sass": ("//", "/*", "*", "*/"),
    "Scala": ("//", "/*", "*", "*/"),
    "Shell": ("#",),
    "Starlark": ("#",),
    "Swift": ("//", "/*", "*", "*/"),
    "TOML": ("#",),
    "Terraform": ("#", "//", "/*", "*", "*/"),
    "TypeScript": ("//", "/*", "*", "*/"),
    "Vue": ("//", "/*", "*", "*/", "<!--", "-->"),
    "XML": ("<!--", "-->"),
    "YAML": ("#",),
    "Zig": ("//", "/*", "*", "*/"),
}

MODULE_FILE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".cxx",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".kts",
    ".mjs",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".swift",
    ".ts",
    ".tsx",
}
EXCLUDED_CODE_DIRS = {
    ".cache",
    ".git",
    ".gradle",
    ".hg",
    ".mypy_cache",
    ".next",
    ".nox",
    ".nuxt",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".terraform",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "site-packages",
    "target",
    "vendor",
    "venv",
}
EXCLUDED_SIZE_DIRS = {".git", "node_modules", ".venv", "venv", "site-packages"}
EXCLUDED_FILE_NAMES = {
    "Cargo.lock",
    "Pipfile.lock",
    "go.sum",
    "package-lock.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "yarn.lock",
}
PACKAGE_MARKER_NAMES = {
    "Chart.yaml",
    "Cargo.toml",
    "Gemfile",
    "build.gradle",
    "build.gradle.kts",
    "composer.json",
    "go.mod",
    "package.json",
    "pom.xml",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
}
PACKAGE_MARKER_SUFFIXES = {".csproj", ".fsproj", ".vbproj"}
ARTIFACT_DIR_NAMES = {
    ".next",
    "build",
    "dist",
    "out",
    "release",
    "target",
}
ARTIFACT_EXTENSIONS = {
    ".apk",
    ".app",
    ".deb",
    ".dmg",
    ".dll",
    ".dylib",
    ".egg",
    ".exe",
    ".jar",
    ".msi",
    ".pkg",
    ".rpm",
    ".so",
    ".tar",
    ".tgz",
    ".war",
    ".wasm",
    ".whl",
    ".zip",
}
PUBLIC_FORGE_HOSTS = {"bitbucket.org", "github.com", "gitlab.com"}
GITHUB_API_VERSION = "2022-11-28"
GITHUB_TOKEN_ENV_NAMES = ("GH_TOKEN", "GITHUB_TOKEN")


@dataclass(frozen=True)
class FileStat:
    path: Path
    language: str
    loc: int
    size: int
    is_test: bool


@dataclass(frozen=True)
class CliHit:
    category: str
    path: Path
    line: int
    snippet: str


@dataclass(frozen=True)
class AnalysisTarget:
    path: Path
    display_name: str
    source_lines: tuple[str, ...]
    git_root: Path | None
    repo_link_override: tuple[str, str | None] | None = None
    git_state_override: str | None = None


class CodeInfoError(Exception):
    """User-facing failure while resolving an analysis target."""


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read existing files and print a Markdown report with basic "
            "project code metrics. Does not modify the project."
        )
    )
    parser.add_argument(
        "--path",
        default=".",
        type=Path,
        help="Project folder to inspect. Defaults to the current directory.",
    )
    parser.add_argument(
        "--github-repo",
        help=(
            "GitHub repository to inspect without cloning. Accepts OWNER/REPO, "
            "a GitHub HTTPS URL, or a git@github.com:OWNER/REPO.git URL."
        ),
    )
    parser.add_argument(
        "--github-ref",
        help=(
            "Branch, tag, or commit SHA to inspect for --github-repo. "
            "Defaults to the repository's default branch."
        ),
    )
    parser.add_argument(
        "--github-token-env",
        default=",".join(GITHUB_TOKEN_ENV_NAMES),
        help=(
            "Comma-separated environment variable names to check for a GitHub "
            "token. Defaults to GH_TOKEN,GITHUB_TOKEN."
        ),
    )
    parser.add_argument(
        "--no-gh-cli-token",
        action="store_true",
        help="Do not fall back to `gh auth token` when no token env var is set.",
    )
    parser.add_argument(
        "--top",
        default=25,
        type=int,
        help="Maximum rows per detailed table. Use 0 for all rows.",
    )
    parser.add_argument(
        "--repo-link",
        choices=("auto", "show", "redact"),
        default="auto",
        help=(
            "Repo link handling. auto redacts local/internal hosts, show always "
            "prints the origin URL, redact always hides it."
        ),
    )
    return parser.parse_args(argv)


def parse_github_repo(value: str) -> tuple[str, str]:
    raw = value.strip()
    if not raw:
        raise CodeInfoError("--github-repo cannot be empty")

    ssh_match = re.fullmatch(
        r"(?:git@|ssh://git@)github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?",
        raw,
    )
    if ssh_match:
        owner = ssh_match.group("owner")
        repo = ssh_match.group("repo")
    elif raw.startswith(("http://", "https://")):
        parsed = urlparse(raw)
        if parsed.hostname != "github.com":
            raise CodeInfoError(
                f"--github-repo URL host must be github.com, got {parsed.hostname or 'empty host'}"
            )
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if len(parts) < 2:
            raise CodeInfoError(
                "--github-repo URL must include owner and repository name"
            )
        owner, repo = parts[0], parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
    else:
        parts = [part for part in raw.strip("/").split("/") if part]
        if len(parts) != 2:
            raise CodeInfoError(
                "--github-repo must be OWNER/REPO or a GitHub repository URL"
            )
        owner, repo = parts
        if repo.endswith(".git"):
            repo = repo[:-4]

    name_re = re.compile(r"^[A-Za-z0-9_.-]+$")
    if not name_re.fullmatch(owner) or not name_re.fullmatch(repo):
        raise CodeInfoError(
            "--github-repo owner and repository may contain only letters, "
            "numbers, dot, underscore, and hyphen"
        )
    return owner, repo


def parse_token_env_names(value: str) -> tuple[str, ...]:
    names = tuple(name.strip() for name in value.split(",") if name.strip())
    return names or GITHUB_TOKEN_ENV_NAMES


def github_token(
    env_names: tuple[str, ...], *, allow_gh_cli: bool
) -> tuple[str | None, str | None]:
    for name in env_names:
        token = os.environ.get(name)
        if token:
            return token, f"environment variable {name}"

    if not allow_gh_cli:
        return None, None

    gh = shutil.which("gh")
    if not gh:
        return None, None

    try:
        result = subprocess.run(
            [gh, "auth", "token", "--hostname", "github.com"],
            check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, ValueError):
        return None, None

    token = result.stdout.strip()
    if result.returncode == 0 and token:
        return token, "GitHub CLI authentication"
    return None, None


def github_archive_url(owner: str, repo: str, ref: str | None) -> str:
    path = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}/tarball"
    if ref:
        path += f"/{quote(ref, safe='')}"
    return f"https://api.github.com{path}"


def safe_extract_tar_stream(fileobj, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with tarfile.open(fileobj=fileobj, mode="r|gz") as archive:
        for member in archive:
            parts = member.name.split("/")
            if len(parts) < 2:
                continue
            relative_parts = parts[1:]
            if any(part in {"", ".", ".."} for part in relative_parts):
                continue
            target = destination.joinpath(*relative_parts)
            try:
                target.resolve().relative_to(destination_resolved)
            except ValueError:
                continue

            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                continue
            with source, target.open("wb") as handle:
                shutil.copyfileobj(source, handle)


def download_github_archive(
    owner: str, repo: str, ref: str | None, token: str | None, destination: Path
) -> None:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "code-info-skill",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(github_archive_url(owner, repo, ref), headers=headers)
    try:
        with urlopen(request, timeout=60) as response:
            safe_extract_tar_stream(response, destination)
    except HTTPError as exc:
        detail = f"GitHub archive request failed with HTTP {exc.code}"
        if exc.code in {401, 403, 404}:
            detail += (
                "; check that the repository exists and the token has read "
                "access to repository contents"
            )
        raise CodeInfoError(detail) from exc
    except (URLError, TimeoutError, tarfile.TarError) as exc:
        raise CodeInfoError(f"GitHub archive download failed: {exc}") from exc


@contextmanager
def resolve_analysis_target(args: argparse.Namespace) -> Iterator[AnalysisTarget]:
    if args.github_repo:
        owner, repo = parse_github_repo(args.github_repo)
        token, token_source = github_token(
            parse_token_env_names(args.github_token_env),
            allow_gh_cli=not args.no_gh_cli_token,
        )
        ref_label = args.github_ref or "default branch"
        with tempfile.TemporaryDirectory(prefix="code-info-github-") as temp_dir:
            temp_path = Path(temp_dir)
            download_github_archive(owner, repo, args.github_ref, token, temp_path)
            repo_full_name = f"{owner}/{repo}"
            repo_url = f"https://github.com/{repo_full_name}"
            repo_link_value = "Redacted" if args.repo_link == "redact" else repo_url
            repo_link_note = (
                "repo link redacted by request" if args.repo_link == "redact" else None
            )
            auth_line = (
                f"GitHub auth: token used from {token_source}"
                if token_source
                else "GitHub auth: unauthenticated public request"
            )
            yield AnalysisTarget(
                path=temp_path,
                display_name=repo_full_name,
                source_lines=(
                    f"Source: GitHub repository `{repo_full_name}`",
                    f"GitHub ref: {ref_label}",
                    auth_line,
                    "Temporary workspace: removed after report generation",
                ),
                git_root=None,
                repo_link_override=(repo_link_value, repo_link_note),
                git_state_override=f"GitHub archive {ref_label}",
            )
        return

    target = args.path.expanduser().resolve()
    if not target.exists() or not target.is_dir():
        raise CodeInfoError(f"path is not a directory: {target}")
    git_root = git_root_for(target)
    yield AnalysisTarget(
        path=target,
        display_name=target.name,
        source_lines=(f"Path: `{target}`",),
        git_root=git_root,
    )


def git_output(cwd: Path, args: list[str]) -> str | None:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args],
            check=False,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_root_for(path: Path) -> Path | None:
    output = git_output(path, ["rev-parse", "--show-toplevel"])
    return Path(output).resolve() if output else None


def iter_files(root: Path, excluded_dirs: set[str]) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in excluded_dirs and not name.endswith(".egg-info")
        )
        base = Path(dirpath)
        for filename in sorted(filenames):
            yield base / filename


def language_for(path: Path) -> str | None:
    if path.name in LANGUAGE_BY_NAME:
        return LANGUAGE_BY_NAME[path.name]
    return LANGUAGE_BY_EXTENSION.get(path.suffix.lower())


def is_probably_binary(path: Path) -> bool:
    try:
        sample = path.read_bytes()[:8192]
    except OSError:
        return True
    return b"\x00" in sample


def count_loc(path: Path, language: str) -> int:
    prefixes = COMMENT_PREFIXES.get(language, ())
    loc = 0
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                lowered = stripped.lower()
                if any(lowered.startswith(prefix.lower()) for prefix in prefixes):
                    continue
                loc += 1
    except OSError:
        return 0
    return loc


def is_test_file(path: Path, root: Path) -> bool:
    lowered_name = path.name.lower()
    try:
        relative_parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        relative_parts = path.parts
    lowered_parts = {part.lower() for part in relative_parts}
    if lowered_parts & {"__tests__", "spec", "specs", "test", "tests"}:
        return True
    patterns = (
        "test_*.py",
        "*_test.py",
        "*_test.go",
        "*.test.*",
        "*.spec.*",
        "*tests.*",
    )
    return any(fnmatch.fnmatchcase(lowered_name, pattern) for pattern in patterns)


def collect_file_stats(root: Path) -> list[FileStat]:
    stats: list[FileStat] = []
    for path in iter_files(root, EXCLUDED_CODE_DIRS):
        if path.name in EXCLUDED_FILE_NAMES:
            continue
        language = language_for(path)
        if not language or is_probably_binary(path):
            continue
        loc = count_loc(path, language)
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        stats.append(
            FileStat(
                path=path,
                language=language,
                loc=loc,
                size=size,
                is_test=is_test_file(path, root),
            )
        )
    return stats


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{num_bytes} B"


def format_int(value: int) -> str:
    return f"{value:,}"


def rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def table(headers: list[str], rows: list[list[str]]) -> str:
    def clean_cell(value: str) -> str:
        return str(value).replace("\n", " ").replace("|", r"\|")

    all_rows = [[clean_cell(cell) for cell in row] for row in [headers, *rows]]
    escaped_headers = all_rows[0]
    escaped_rows = all_rows[1:]
    widths = [0] * len(headers)
    for row in all_rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    def render(row: list[str]) -> str:
        cells = (str(cell).ljust(widths[index]) for index, cell in enumerate(row))
        return "| " + " | ".join(cells) + " |"

    separator = "| " + " | ".join("-" * width for width in widths) + " |"
    return "\n".join(
        [render(escaped_headers), separator, *(render(row) for row in escaped_rows)]
    )


def limited(items: list, top: int) -> list:
    return items if top <= 0 else items[:top]


def component_for(path: Path, root: Path) -> str:
    relative = path.resolve().relative_to(root.resolve())
    if len(relative.parts) <= 1:
        return "(root)"
    return relative.parts[0]


def dir_size(path: Path, excluded_dirs: set[str] | None = None) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for file_path in iter_files(path, excluded_dirs or set()):
        try:
            total += file_path.stat().st_size
        except OSError:
            continue
    return total


def tracked_size(git_root: Path | None, target: Path) -> int | None:
    if not git_root:
        return None
    try:
        target_rel = target.resolve().relative_to(git_root.resolve())
        target_arg = target_rel.as_posix() or "."
    except ValueError:
        return None
    output = git_output(git_root, ["ls-files", "-z", "--", target_arg])
    if output is None:
        return None
    total = 0
    for raw in output.split("\0"):
        if not raw:
            continue
        path = git_root / raw
        try:
            total += path.stat().st_size
        except OSError:
            continue
    return total


def normalize_remote_url(raw: str) -> str:
    value = raw.strip()
    scp_like = re.match(r"^(?:[^@]+@)?([^:]+):(.+)$", value)
    if scp_like and not value.startswith(("http://", "https://", "ssh://")):
        host, path = scp_like.groups()
        value = f"https://{host}/{path}"
    if value.startswith("ssh://"):
        parsed = urlparse(value)
        path = parsed.path.lstrip("/")
        value = f"https://{parsed.hostname or ''}/{path}"
    if value.endswith(".git"):
        value = value[:-4]
    return value


def host_is_internal(host: str | None) -> bool:
    if not host:
        return True
    lowered = host.lower()
    if lowered in PUBLIC_FORGE_HOSTS:
        return False
    if lowered.endswith((".internal", ".local", ".corp", ".lan")):
        return True
    if "." not in lowered:
        return True
    try:
        parsed = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    return parsed.is_private or parsed.is_loopback or parsed.is_link_local


def repo_link(git_root: Path | None, mode: str) -> tuple[str, str | None]:
    if not git_root:
        return "Unavailable", "not a Git repository"
    raw = git_output(git_root, ["config", "--get", "remote.origin.url"])
    if not raw:
        return "Unavailable", "origin remote is not configured"
    normalized = normalize_remote_url(raw)
    parsed = urlparse(normalized)
    if mode == "redact":
        return "Redacted", "repo link redacted by request"
    if mode == "auto" and host_is_internal(parsed.hostname):
        return (
            "Redacted",
            "origin host looks local or internal; rerun with --repo-link show for internal-only reports",
        )
    return normalized, None


def package_markers(root: Path) -> list[Path]:
    markers: list[Path] = []
    for path in iter_files(root, EXCLUDED_CODE_DIRS):
        if path.name in PACKAGE_MARKER_NAMES or path.suffix in PACKAGE_MARKER_SUFFIXES:
            markers.append(path)
    return sorted(markers)


def python_package_dirs(root: Path) -> list[Path]:
    dirs = {
        path.parent
        for path in iter_files(root, EXCLUDED_CODE_DIRS)
        if path.name == "__init__.py"
    }
    return sorted(dirs)


def source_module_files(stats: list[FileStat]) -> list[Path]:
    return sorted(
        stat.path
        for stat in stats
        if stat.path.suffix.lower() in MODULE_FILE_EXTENSIONS and not stat.is_test
    )


CLI_PATTERNS = [
    (
        "Python Click/Typer decorators",
        {".py"},
        re.compile(r"@\w+(?:\.\w+)*\.(?:command|group)\s*\("),
    ),
    ("Python argparse subparsers", {".py"}, re.compile(r"\.add_parser\s*\(")),
    ("Python Typer sub-apps", {".py"}, re.compile(r"\.add_typer\s*\(")),
    (
        "JavaScript/TypeScript command()",
        {".js", ".jsx", ".mjs", ".ts", ".tsx"},
        re.compile(r"\.command\s*\("),
    ),
    ("Go Cobra commands", {".go"}, re.compile(r"&cobra\.Command\s*\{")),
    ("Rust clap commands", {".rs"}, re.compile(r"#\[(?:command|subcommand)\b")),
]


def detect_cli_hits(root: Path) -> list[CliHit]:
    hits: list[CliHit] = []
    for path in iter_files(root, EXCLUDED_CODE_DIRS):
        suffix = path.suffix.lower()
        matching = [entry for entry in CLI_PATTERNS if suffix in entry[1]]
        if not matching:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()
            for category, _, pattern in matching:
                if pattern.search(stripped):
                    hits.append(CliHit(category, path, line_number, stripped[:80]))
    hits.extend(package_script_hits(root))
    return hits


def package_script_hits(root: Path) -> list[CliHit]:
    hits: list[CliHit] = []
    for marker in package_markers(root):
        if marker.name != "package.json":
            continue
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scripts = data.get("scripts")
        if not isinstance(scripts, dict):
            continue
        for name in sorted(scripts):
            hits.append(CliHit("package.json scripts", marker, 0, name))
    return hits


def artifact_dirs(root: Path) -> list[tuple[Path, int]]:
    found: list[tuple[Path, int]] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in {".git", "node_modules", ".venv", "venv", "site-packages"}
        ]
        current = Path(dirpath)
        for dirname in list(dirnames):
            candidate = current / dirname
            if dirname in ARTIFACT_DIR_NAMES:
                found.append((candidate, dir_size(candidate, {".git", "node_modules"})))
                dirnames.remove(dirname)
    return sorted(found, key=lambda item: item[1], reverse=True)


def artifact_files(root: Path) -> list[tuple[Path, int]]:
    found: list[tuple[Path, int]] = []
    for path in iter_files(
        root, {".git", "node_modules", ".venv", "venv", "site-packages"}
    ):
        suffixes = [suffix.lower() for suffix in path.suffixes]
        if path.suffix.lower() in ARTIFACT_EXTENSIONS or any(
            suffix in {".tar", ".tgz", ".zip"} for suffix in suffixes
        ):
            try:
                found.append((path, path.stat().st_size))
            except OSError:
                continue
    return sorted(found, key=lambda item: item[1], reverse=True)


def coverage_candidates(root: Path) -> list[Path]:
    names = {
        ".coverage",
        "coverage-summary.json",
        "coverage-final.json",
        "coverage.json",
        "coverage.xml",
        "lcov.info",
    }
    candidates: list[Path] = []
    for path in iter_files(
        root, {".git", "node_modules", ".venv", "venv", "site-packages"}
    ):
        if path.name in names:
            candidates.append(path)
    return sorted(candidates)


def parse_coverage(path: Path) -> tuple[str, str] | None:
    if path.name == "coverage-summary.json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        total = data.get("total", {})
        lines = total.get("lines", {}).get("pct")
        branches = total.get("branches", {}).get("pct")
        if lines is not None:
            detail = f"lines {lines}%"
            if branches is not None:
                detail += f", branches {branches}%"
            return detail, "Istanbul coverage summary"
    if path.name == "lcov.info":
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        lf = sum(
            int(match.group(1))
            for match in re.finditer(r"^LF:(\d+)$", text, re.MULTILINE)
        )
        lh = sum(
            int(match.group(1))
            for match in re.finditer(r"^LH:(\d+)$", text, re.MULTILINE)
        )
        if lf:
            return f"lines {lh / lf * 100:.1f}% ({lh}/{lf})", "LCOV"
    if path.name == "coverage.xml":
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            return None
        line_rate = root.attrib.get("line-rate")
        branch_rate = root.attrib.get("branch-rate")
        if line_rate is not None:
            detail = f"lines {float(line_rate) * 100:.1f}%"
            if branch_rate is not None:
                detail += f", branches {float(branch_rate) * 100:.1f}%"
            return detail, "Cobertura XML"
    if path.name == ".coverage":
        return (
            "coverage.py data file detected; export XML or JSON for a percentage",
            "coverage.py",
        )
    return None


def coverage_summary(root: Path) -> list[tuple[Path, str, str]]:
    parsed: list[tuple[Path, str, str]] = []
    for path in coverage_candidates(root):
        result = parse_coverage(path)
        if result:
            value, source = result
            parsed.append((path, value, source))
    return parsed


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        with resolve_analysis_target(args) as analysis_target:
            return print_report(args, analysis_target)
    except CodeInfoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def print_report(args: argparse.Namespace, analysis_target: AnalysisTarget) -> int:
    target = analysis_target.path
    git_root = analysis_target.git_root
    stats = collect_file_stats(target)
    total_loc = sum(stat.loc for stat in stats)
    total_source_files = len(stats)
    test_stats = [stat for stat in stats if stat.is_test]
    markers = package_markers(target)
    python_packages = python_package_dirs(target)
    module_files = source_module_files(stats)
    cli_hits = detect_cli_hits(target)
    artifacts_by_dir = artifact_dirs(target)
    artifacts_by_file = artifact_files(target)
    artifact_dir_paths = [path for path, _ in artifacts_by_dir]
    standalone_artifact_files = [
        (path, size)
        for path, size in artifacts_by_file
        if not any(is_under(path, artifact_dir) for artifact_dir in artifact_dir_paths)
    ]
    coverage = coverage_summary(target)
    tracked = tracked_size(git_root, target)
    tree_scan_size = dir_size(target, EXCLUDED_SIZE_DIRS)
    git_dir_size = (
        dir_size(git_root / ".git", set())
        if git_root and (git_root / ".git").exists()
        else 0
    )
    if analysis_target.repo_link_override:
        link, link_note = analysis_target.repo_link_override
    else:
        link, link_note = repo_link(git_root, args.repo_link)
    branch = (
        git_output(git_root or target, ["rev-parse", "--abbrev-ref", "HEAD"])
        if git_root
        else None
    )
    commit = (
        git_output(git_root or target, ["rev-parse", "--short", "HEAD"])
        if git_root
        else None
    )
    dirty = (
        git_output(git_root or target, ["status", "--porcelain"]) if git_root else None
    )

    language_counter: Counter[str] = Counter()
    language_files: Counter[str] = Counter()
    component_loc: Counter[str] = Counter()
    component_files: Counter[str] = Counter()
    test_counter: Counter[str] = Counter()
    cli_counter: Counter[str] = Counter()
    for stat in stats:
        language_counter[stat.language] += stat.loc
        language_files[stat.language] += 1
        component = component_for(stat.path, target)
        component_loc[component] += stat.loc
        component_files[component] += 1
        if stat.is_test:
            test_counter[stat.language] += 1
    for hit in cli_hits:
        cli_counter[hit.category] += 1

    repo_size_value = f"{human_size(tree_scan_size)} working tree scan"
    if tracked is not None:
        repo_size_value += f"; {human_size(tracked)} tracked in scope"
    if git_dir_size:
        repo_size_value += f"; {human_size(git_dir_size)} .git"

    coverage_value = coverage[0][1] if coverage else "Not detected"
    artifact_total = sum(size for _, size in artifacts_by_dir) + sum(
        size for _, size in standalone_artifact_files
    )
    artifact_value = human_size(artifact_total) if artifact_total else "Not detected"
    git_state = analysis_target.git_state_override or "Unavailable"
    if not analysis_target.git_state_override and git_root:
        state_bits = []
        if branch:
            state_bits.append(branch)
        if commit:
            state_bits.append(commit)
        state_bits.append("dirty" if dirty else "clean")
        git_state = " ".join(state_bits)

    print(f"# Code Info: {analysis_target.display_name}")
    print()
    print(f"Generated: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    for line in analysis_target.source_lines:
        print(line)
    if git_root:
        print(f"Git root: `{git_root}`")
    print(f"Git state: {git_state}")
    print(f"Repo link: {link}")
    if link_note:
        print(f"Repo link note: {link_note}")
    print()

    summary_rows = [
        ["Source LOC", format_int(total_loc)],
        ["Source files", format_int(total_source_files)],
        ["Languages", format_int(len(language_counter))],
        ["Repo size", repo_size_value],
        ["Top-level components", format_int(len(component_loc))],
        ["Test files", format_int(len(test_stats))],
        ["CLI commands/subcommands", f"{format_int(len(cli_hits))} best-effort"],
        [
            "Modules/packages",
            f"{format_int(len(markers))} markers; {format_int(len(module_files))} source module files",
        ],
        ["Build/artifact size", artifact_value],
        ["Coverage", coverage_value],
    ]
    print("## Summary")
    print()
    print(table(["Metric", "Value"], summary_rows))
    print()

    print("## LOC Per Language")
    print()
    language_rows = [
        [language, format_int(loc), format_int(language_files[language])]
        for language, loc in language_counter.most_common()
    ]
    print(
        table(["Language", "LOC", "Files"], limited(language_rows, args.top))
        if language_rows
        else "Not detected"
    )
    print()

    print("## LOC Per Top-Level Component")
    print()
    component_rows = [
        [component, format_int(loc), format_int(component_files[component])]
        for component, loc in component_loc.most_common()
    ]
    print(
        table(["Component", "LOC", "Files"], limited(component_rows, args.top))
        if component_rows
        else "Not detected"
    )
    print()

    print("## Test Files")
    print()
    test_rows = [
        [language, format_int(count)] for language, count in test_counter.most_common()
    ]
    print(table(["Language", "Test files"], test_rows) if test_rows else "Not detected")
    print()

    print("## CLI Commands / Subcommands")
    print()
    cli_rows = [
        [category, format_int(count)] for category, count in cli_counter.most_common()
    ]
    print(table(["Detection", "Count"], cli_rows) if cli_rows else "Not detected")
    if cli_hits:
        example_rows = [
            [
                hit.category,
                f"{rel(hit.path, target)}:{hit.line}"
                if hit.line
                else rel(hit.path, target),
                hit.snippet,
            ]
            for hit in limited(cli_hits, min(args.top, 10) if args.top else 10)
        ]
        print()
        print("Examples:")
        print()
        print(table(["Detection", "Location", "Snippet"], example_rows))
    print()

    print("## Modules / Packages")
    print()
    module_rows = [
        ["Package/project marker files", format_int(len(markers))],
        ["Python package directories", format_int(len(python_packages))],
        ["Source module files", format_int(len(module_files))],
    ]
    print(table(["Metric", "Count"], module_rows))
    if markers:
        marker_rows = [[rel(path, target)] for path in limited(markers, args.top)]
        print()
        print("Package/project markers:")
        print()
        print(table(["Path"], marker_rows))
    print()

    print("## Build Artifacts")
    print()
    artifact_rows: list[list[str]] = []
    for path, size in limited(artifacts_by_dir, args.top):
        artifact_rows.append(["Directory", rel(path, target), human_size(size)])
    for path, size in limited(standalone_artifact_files, args.top):
        artifact_rows.append(["File", rel(path, target), human_size(size)])
    print(
        table(["Type", "Path", "Size"], artifact_rows)
        if artifact_rows
        else "Not detected"
    )
    print()

    print("## Coverage")
    print()
    coverage_rows = [
        [rel(path, target), value, source] for path, value, source in coverage
    ]
    print(
        table(["File", "Coverage", "Source"], coverage_rows)
        if coverage_rows
        else "Not detected"
    )
    print()

    print("## Notes")
    print()
    print(
        "- LOC counts non-empty, non-comment lines for recognized source/config/documentation file types."
    )
    print(
        "- Generated, dependency, cache, and common build folders are excluded from LOC counts."
    )
    print(
        "- CLI command counts are best-effort pattern matches and may need framework-specific review."
    )
    print(
        "- Coverage is read from existing artifacts only; no tests or coverage commands were run."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
