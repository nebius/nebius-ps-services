#!/usr/bin/env python3
"""Offline source-policy primitives for the container audit helper."""

from __future__ import annotations

import os
import re
from pathlib import Path
from pathlib import PurePosixPath

from container_audit_types import AuditError, Finding

MAX_SCANNED_FILES = 100_000
SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".terraform",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
}
SECRET_NAME_RE = re.compile(
    r"(?:^|_)(?:API_?KEY|AUTH|CREDENTIAL|PASSWORD|PRIVATE_?KEY|SECRET|TOKEN)(?:_|$)",
    re.IGNORECASE,
)
FROM_RE = re.compile(r"^\s*FROM(?:\s+--[^\s]+)*\s+([^\s]+)", re.IGNORECASE)
INSTRUCTION_RE = re.compile(
    r"^\s*(ARG|ENV|ENTRYPOINT|CMD|USER|ADD)\b(.*)$", re.IGNORECASE
)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _scoped_input(root: Path, value: Path, label: str) -> Path:
    root = root.resolve()
    candidate = value if value.is_absolute() else root / value
    if candidate.is_symlink():
        raise AuditError(f"{label} must not be a symbolic link")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise AuditError(f"{label} must resolve inside --path") from exc
    if not resolved.is_file():
        raise AuditError(f"{label} is not a file: {resolved}")
    return resolved


def discover_container_files(
    root: Path,
) -> tuple[dict[str, list[Path]], bool, list[Finding]]:
    groups: dict[str, list[Path]] = {
        "dockerfiles": [],
        "ignore_files": [],
        "compose_files": [],
        "bake_files": [],
    }
    findings: list[Finding] = []
    walk_errors: list[OSError] = []
    seen = 0
    for directory, names, files in os.walk(
        root,
        followlinks=False,
        onerror=walk_errors.append,
    ):
        names[:] = sorted(name for name in names if name not in SKIP_DIRECTORIES)
        base = Path(directory)
        for filename in sorted(files):
            seen += 1
            if seen > MAX_SCANNED_FILES:
                return groups, True, findings
            path = base / filename
            lowered = filename.casefold()
            group: str | None = None
            if (
                filename == "Dockerfile"
                or filename == "Containerfile"
                or filename.startswith("Dockerfile.")
                or filename.startswith("Containerfile.")
            ):
                group = "dockerfiles"
            elif filename == ".dockerignore":
                group = "ignore_files"
            elif lowered in {
                "compose.yaml",
                "compose.yml",
                "compose.override.yaml",
                "compose.override.yml",
                "compose.production.yaml",
                "compose.production.yml",
                "compose.test.yaml",
                "compose.test.yml",
            }:
                group = "compose_files"
            elif lowered in {"docker-bake.hcl", "docker-bake.json"}:
                group = "bake_files"
            if group is None:
                continue
            try:
                groups[group].append(
                    _scoped_input(root, path, "discovered container file")
                )
            except AuditError as exc:
                findings.append(
                    Finding(
                        "discovery.unsafe-path",
                        "error",
                        str(exc),
                        path.relative_to(root).as_posix(),
                    )
                )
    findings.extend(
        Finding(
            "discovery.walk-error",
            "error",
            f"Container-file discovery could not traverse a path: {type(error).__name__}.",
        )
        for error in walk_errors
    )
    return groups, False, findings


def _image_is_exact(reference: str) -> bool:
    if "@sha256:" in reference:
        return bool(re.search(r"@sha256:[0-9a-f]{64}$", reference))
    last = reference.rsplit("/", 1)[-1]
    return ":" in last and not last.casefold().endswith(":latest")


def _dockerignore_excludes(lines: list[str], target: str) -> bool:
    excluded = False
    for raw in lines:
        rule = raw.strip()
        if not rule or rule.startswith("#"):
            continue
        negated = rule.startswith("!")
        if negated:
            rule = rule[1:]
        rule = rule.lstrip("/")
        directory_rule = rule.endswith("/")
        rule = rule.rstrip("/")
        if not rule:
            continue
        matched = (
            target == rule
            or target.startswith(f"{rule}/")
            or PurePosixPath(target).match(rule)
            or PurePosixPath(target).match(f"**/{rule}")
            or (directory_rule and target.startswith(f"{rule}/"))
        )
        if matched:
            excluded = not negated
    return excluded


def _sensitive_context_targets(root: Path) -> set[str]:
    targets = {".env", ".git/config", ".venv/lib/site.py"}
    scanned = 0
    public_env_names = {".env.example", ".env.sample", ".env.template"}
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = [name for name in names if name not in SKIP_DIRECTORIES]
        base = Path(directory)
        for filename in files:
            scanned += 1
            if scanned > MAX_SCANNED_FILES:
                return targets
            if filename == ".env" or (
                filename.startswith(".env.") and filename not in public_env_names
            ):
                targets.add((base / filename).relative_to(root).as_posix())
    return targets


def _logical_dockerfile_lines(lines: list[str]) -> list[tuple[int, str]]:
    logical: list[tuple[int, str]] = []
    parts: list[str] = []
    start = 0
    for number, line in enumerate(lines, start=1):
        if not parts:
            start = number
        stripped = line.rstrip()
        continued = stripped.endswith("\\")
        parts.append(stripped[:-1].rstrip() if continued else stripped)
        if not continued:
            logical.append((start, " ".join(part.lstrip() for part in parts)))
            parts = []
    if parts:
        logical.append((start, " ".join(part.lstrip() for part in parts)))
    return logical


def audit_dockerfile(path: Path, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        return [
            Finding(
                "dockerfile.unreadable",
                "error",
                f"Cannot read Dockerfile: {type(exc).__name__}",
                _relative(path, root),
            )
        ]

    has_user = False
    has_non_root_user = False
    has_unresolved_user = False
    has_explicit_gid = False
    label_names: set[str] = set()
    for number, line in _logical_dockerfile_lines(lines):
        from_match = FROM_RE.match(line)
        if from_match:
            has_user = False
            has_non_root_user = False
            has_unresolved_user = False
            has_explicit_gid = False
            label_names = set()
            reference = from_match.group(1)
            if reference.casefold() == "scratch":
                continue
            if reference.casefold().endswith(":latest"):
                findings.append(
                    Finding(
                        "base.latest",
                        "error",
                        "Production base images must not use :latest.",
                        _relative(path, root),
                        number,
                    )
                )
            elif not _image_is_exact(reference):
                findings.append(
                    Finding(
                        "base.floating",
                        "error",
                        "Base image must use an explicit tag or sha256 digest.",
                        _relative(path, root),
                        number,
                    )
                )
            continue

        match = INSTRUCTION_RE.match(line)
        if not match:
            if line.lstrip().upper().startswith("LABEL "):
                label_names.update(re.findall(r"([A-Za-z0-9_.-]+)\s*=", line))
            continue
        instruction, remainder = match.group(1).upper(), match.group(2).strip()
        if instruction in {"ARG", "ENV"}:
            if instruction == "ENV" and "=" in remainder:
                names = re.findall(
                    r"(?:^|\s)([A-Za-z_][A-Za-z0-9_]*)\s*=",
                    remainder,
                )
            else:
                names = [remainder.split("=", 1)[0].split(None, 1)[0].strip()]
            if any(SECRET_NAME_RE.search(name) for name in names):
                findings.append(
                    Finding(
                        "build.secret-metadata",
                        "error",
                        f"{instruction} name appears secret-bearing; use a BuildKit secret or SSH mount.",
                        _relative(path, root),
                        number,
                    )
                )
        elif instruction == "USER":
            has_user = True
            identity = remainder.split(None, 1)[0].strip()
            match = re.fullmatch(r"([1-9][0-9]*)(?::([1-9][0-9]*))?", identity)
            has_unresolved_user = match is None
            has_non_root_user = match is not None
            has_explicit_gid = bool(match and match.group(2))
        elif instruction in {"ENTRYPOINT", "CMD"} and not remainder.startswith("["):
            findings.append(
                Finding(
                    "runtime.shell-form",
                    "warning",
                    f"{instruction} uses shell form; prefer JSON-array exec form for signals.",
                    _relative(path, root),
                    number,
                )
            )
        elif instruction == "ADD" and re.search(r"https?://", remainder):
            findings.append(
                Finding(
                    "build.remote-add",
                    "warning",
                    "Remote ADD requires explicit source integrity and network review.",
                    _relative(path, root),
                    number,
                )
            )

    if not has_user:
        findings.append(
            Finding(
                "runtime.user-missing",
                "error",
                "No final USER instruction was found; production images require an explicit identity.",
                _relative(path, root),
            )
        )
    elif has_unresolved_user:
        findings.append(
            Finding(
                "runtime.user-unresolved",
                "error",
                "Final runtime identity is variable-based and cannot be verified as non-root.",
                _relative(path, root),
            )
        )
    elif not has_non_root_user:
        findings.append(
            Finding(
                "runtime.root-user",
                "error",
                "Final runtime identity is root; document an exception or use non-root.",
                _relative(path, root),
            )
        )
    elif not has_explicit_gid:
        findings.append(
            Finding(
                "runtime.gid-unresolved",
                "warning",
                "Final runtime identity does not declare a positive numeric GID.",
                _relative(path, root),
            )
        )
    required_labels = {
        "org.opencontainers.image.revision",
        "org.opencontainers.image.source",
        "org.opencontainers.image.version",
    }
    missing_labels = sorted(required_labels - label_names)
    if missing_labels:
        findings.append(
            Finding(
                "metadata.oci-labels",
                "warning",
                f"OCI release metadata labels not found statically: {', '.join(missing_labels)}",
                _relative(path, root),
            )
        )

    context_ignore = root / ".dockerignore"
    dockerfile_ignore = path.parent / f"{path.name}.dockerignore"
    effective_ignore = (
        dockerfile_ignore
        if dockerfile_ignore.is_file()
        else context_ignore
        if context_ignore.is_file()
        else None
    )
    if effective_ignore is None:
        findings.append(
            Finding(
                "context.ignore-missing",
                "error",
                "The Dockerfile build context has no .dockerignore.",
                _relative(path, root),
            )
        )
    else:
        try:
            ignore_lines = effective_ignore.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            findings.append(
                Finding(
                    "context.ignore-unreadable",
                    "error",
                    f"Cannot read the effective Docker ignore file: {type(exc).__name__}.",
                    _relative(effective_ignore, root),
                )
            )
        else:
            required_targets = sorted(_sensitive_context_targets(root))
            missing = [
                target
                for target in required_targets
                if not _dockerignore_excludes(ignore_lines, target)
            ]
            if missing:
                findings.append(
                    Finding(
                        "context.ignore-incomplete",
                        "error",
                        "The effective Docker ignore rules do not exclude required sensitive development paths.",
                        _relative(effective_ignore, root),
                    )
                )
    return findings


def audit_compose_text(path: Path, root: Path) -> list[Finding]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [
            Finding(
                "compose.unreadable",
                "error",
                f"Cannot read Compose file: {type(exc).__name__}",
                _relative(path, root),
            )
        ]
    checks = {
        r"(?m)^\s*privileged\s*:\s*true\s*$": (
            "compose.privileged",
            "Privileged services are prohibited by default.",
        ),
        r"(?m)^\s*(?:network_mode|pid|ipc)\s*:\s*host\s*$": (
            "compose.host-namespace",
            "Host namespaces are prohibited by default.",
        ),
        r"(?:^|[\"'/:])var/run/docker\.sock(?:$|[\"':])": (
            "compose.docker-socket",
            "Docker socket mounts are prohibited.",
        ),
        r"(?m)^\s*image\s*:\s*[^\s#]+:latest(?:\s|$)": (
            "compose.latest",
            "Production image references must not use :latest.",
        ),
    }
    findings: list[Finding] = []
    for pattern, (code, message) in checks.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            findings.append(
                Finding(
                    code,
                    "error",
                    message,
                    _relative(path, root),
                    text.count("\n", 0, match.start()) + 1,
                )
            )
    if re.search(r"(?m)^\s*-\s+\.\s*:\s*/", text):
        findings.append(
            Finding(
                "compose.source-bind",
                "warning",
                "Source bind mount detected; keep it out of production overrides.",
                _relative(path, root),
            )
        )
    environment_indent: int | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if stripped == "environment:":
            environment_indent = indent
            continue
        if environment_indent is None:
            continue
        if indent <= environment_indent:
            environment_indent = None
            continue
        entry = stripped[2:].strip() if stripped.startswith("- ") else stripped
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?::|=)\s*(.*)$", entry)
        if not match or not SECRET_NAME_RE.search(match.group(1)):
            continue
        value = match.group(2).strip().strip("\"'")
        safe_external = re.fullmatch(
            rf"\$\{{{re.escape(match.group(1))}(?::\?[^}}]*)?\}}",
            value,
        )
        if value and not safe_external:
            findings.append(
                Finding(
                    "compose.secret-value",
                    "error",
                    f"Secret-like environment name {match.group(1)} has a literal or defaulted value.",
                    _relative(path, root),
                    number,
                )
            )
    findings.append(
        Finding(
            "compose.static-limited",
            "warning",
            "Static source checks are not a complete YAML semantic or rendered-model security audit.",
            _relative(path, root),
        )
    )
    return findings
