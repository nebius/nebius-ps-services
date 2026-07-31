#!/usr/bin/env python3
"""Render and validate deterministic React/Vite scaffold candidates."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import stat
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


REQUEST_SCHEMA_VERSION = 1
CANDIDATE_MANIFEST_SCHEMA_VERSION = 1
PROFILE = "react-vite"
OWNER = "frontend-project"
FILE_MODE = "0644"
HEX_DIGEST_LENGTH = 64
PACKAGE_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
SAFE_COMMAND = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
SUPPORTED_PACKAGE_MANAGERS = {"bun", "npm", "pnpm", "yarn"}
SAFE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
EXACT_SEMVER = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
NODE_VERSION_RANGE = re.compile(r"^[0-9A-Za-z.+*<>=~^| -]+$")
PUBLIC_ENV_NAME = re.compile(r"^VITE_[A-Z][A-Z0-9_]*$")
SECRET_ENV_MARKERS = (
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PRIVATE_KEY",
    "API_KEY",
    "ACCESS_KEY",
    "CREDENTIAL",
)
UNRESOLVED_PLACEHOLDER = re.compile(r"\{\{[A-Z][A-Z0-9_]*\}\}")

BASE_VERSION_KEYS = {
    "@testing-library/jest-dom",
    "@testing-library/react",
    "@types/node",
    "@types/react",
    "@types/react-dom",
    "@vitejs/plugin-react",
    "jsdom",
    "react",
    "react-dom",
    "typescript",
    "vite",
    "vitest",
}
BASE_PATHS = {
    ".env.example",
    "README.md",
    "index.html",
    "package.json",
    "src/App.test.tsx",
    "src/App.tsx",
    "src/env.ts",
    "src/main.tsx",
    "src/styles.css",
    "src/test/setup.ts",
    "tsconfig.app.json",
    "tsconfig.json",
    "tsconfig.node.json",
    "vite.config.ts",
    "vitest.config.ts",
}
TEMPLATE_PATHS = {
    "README.md": "README.md.template",
    "index.html": "index.html.template",
    "package.json": "package.json.template",
    "src/App.test.tsx": "src/App.test.tsx.template",
    "src/App.tsx": "src/App.tsx.template",
    "src/main.tsx": "src/main.tsx.template",
    "src/styles.css": "src/styles.css.template",
    "src/test/setup.ts": "src/test/setup.ts.template",
    "tsconfig.app.json": "tsconfig.app.json.template",
    "tsconfig.json": "tsconfig.json.template",
    "tsconfig.node.json": "tsconfig.node.json.template",
    "vite.config.ts": "vite.config.ts.template",
    "vitest.config.ts": "vitest.config.ts.template",
}


class FrontendProjectError(RuntimeError):
    """A user-actionable candidate rendering or validation failure."""


def _error(message: str) -> None:
    raise FrontendProjectError(message)


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _error(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        _error(f"{label} must be an array")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        _error(f"{label} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        _error(f"{label} must use Unicode NFC normalization")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _error(f"{label} contains a control character")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{label} must be a boolean")
    return value


def _require_exact_keys(
    value: dict[str, Any],
    *,
    required: Iterable[str],
    optional: Iterable[str] = (),
    label: str,
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - value.keys())
    unknown = sorted(value.keys() - allowed)
    if missing:
        _error(f"{label} is missing required fields: {', '.join(missing)}")
    if unknown:
        _error(f"{label} contains unknown fields: {', '.join(unknown)}")


def _normalize_relative_path(value: Any, label: str) -> str:
    raw = _require_string(value, label)
    if "\\" in raw:
        _error(f"{label} contains an unsafe path character")
    path = PurePosixPath(raw)
    if path.is_absolute() or raw.startswith("/") or raw.endswith("/"):
        _error(f"{label} must be a normalized relative path")
    if any(part in {"", ".", ".."} for part in path.parts):
        _error(f"{label} must not contain empty, dot, or parent segments")
    if path.as_posix() != raw:
        _error(f"{label} must already be normalized")
    return raw


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _pretty_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def _parse_json_bytes(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise FrontendProjectError(f"{label} must be UTF-8 JSON") from error

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FrontendProjectError(
                    f"{label} contains duplicate object key: {key}"
                )
            result[key] = value
        return result

    def reject_non_standard_number(value: str) -> None:
        raise FrontendProjectError(
            f"{label} contains non-standard numeric literal: {value}"
        )

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_standard_number,
        )
    except json.JSONDecodeError as error:
        raise FrontendProjectError(f"{label} is invalid JSON: {error}") from error


def _mode_string(mode: int) -> str:
    return f"{stat.S_IMODE(mode):04o}"


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == HEX_DIGEST_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _read_regular_bytes(path: Path, label: str) -> bytes:
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as error:
        raise FrontendProjectError(f"cannot safely open {label}: {path}") from error
    try:
        result = os.fstat(descriptor)
        if not stat.S_ISREG(result.st_mode):
            _error(f"{label} must be a regular file: {path}")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _require_private_directory(path: Path, label: str) -> None:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise FrontendProjectError(f"cannot inspect {label}: {path}") from error
    if (
        not stat.S_ISDIR(result.st_mode)
        or stat.S_ISLNK(result.st_mode)
        or _mode_string(result.st_mode) != "0700"
    ):
        _error(f"{label} must be a private 0700 directory: {path}")


def _require_private_file(path: Path, label: str) -> None:
    try:
        result = os.lstat(path)
    except OSError as error:
        raise FrontendProjectError(f"cannot inspect {label}: {path}") from error
    if (
        not stat.S_ISREG(result.st_mode)
        or stat.S_ISLNK(result.st_mode)
        or _mode_string(result.st_mode) != "0600"
    ):
        _error(f"{label} must be a private 0600 regular file: {path}")


def _validate_private_parent_chain(root: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(root)
    except ValueError:
        _error(f"candidate path escapes the candidate root: {parent}")
    _require_private_directory(root, "candidate root")
    current = root
    for part in relative.parts:
        current /= part
        _require_private_directory(current, "candidate parent")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    return _require_dict(
        _parse_json_bytes(_read_regular_bytes(path, label), label),
        label,
    )


def _make_private_directory(path: Path) -> Path:
    expanded = Path(os.path.abspath(os.path.expanduser(str(path))))
    if expanded.exists() or expanded.is_symlink():
        result = os.lstat(expanded)
        if (
            not stat.S_ISDIR(result.st_mode)
            or stat.S_ISLNK(result.st_mode)
            or _mode_string(result.st_mode) != "0700"
        ):
            _error(f"output must be a private 0700 directory: {expanded}")
        if any(expanded.iterdir()):
            _error(f"output directory must be empty: {expanded}")
    else:
        expanded.mkdir(mode=0o700, parents=True)
    os.chmod(expanded, 0o700)
    return expanded.resolve(strict=True)


def _ensure_private_parent(root: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(root)
    except ValueError:
        _error(f"candidate path escapes the output directory: {parent}")
    current = root
    for part in relative.parts:
        current /= part
        if current.exists() or current.is_symlink():
            result = os.lstat(current)
            if not stat.S_ISDIR(result.st_mode) or stat.S_ISLNK(result.st_mode):
                _error(f"candidate parent must be a real directory: {current}")
        else:
            current.mkdir(mode=0o700)
        os.chmod(current, 0o700)


def _write_private_bytes(root: Path, relative: str, payload: bytes) -> None:
    path = root.joinpath(*PurePosixPath(relative).parts)
    _ensure_private_parent(root, path.parent)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _validate_exact_version(value: Any, label: str) -> str:
    version = _require_string(value, label)
    if EXACT_SEMVER.fullmatch(version) is None:
        _error(f"{label} must be an exact registry version")
    return version


def _paths_overlap(first: str, second: str) -> bool:
    return (
        first == second
        or first.startswith(f"{second}/")
        or second.startswith(f"{first}/")
    )


def _normalized_request(request: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
    _require_exact_keys(
        request,
        required={
            "schema_version",
            "candidate_set_id",
            "profile",
            "component_id",
            "materialization_unit_id",
            "component_root",
            "assigned_paths",
            "excluded_paths",
            "package",
            "versions",
            "capabilities",
        },
        label="request",
    )
    if request["schema_version"] != REQUEST_SCHEMA_VERSION:
        _error(f"unsupported request schema_version: {request['schema_version']}")
    if request["profile"] != PROFILE:
        _error(f"unsupported frontend profile: {request['profile']}")

    candidate_set_id = _require_string(
        request["candidate_set_id"], "request.candidate_set_id"
    )
    if SAFE_IDENTIFIER.fullmatch(candidate_set_id) is None:
        _error("request.candidate_set_id must be a safe identifier")
    component_id = _require_string(request["component_id"], "request.component_id")
    unit_id = _require_string(
        request["materialization_unit_id"], "request.materialization_unit_id"
    )
    root = _normalize_relative_path(request["component_root"], "request.component_root")

    package = _require_dict(request["package"], "request.package")
    _require_exact_keys(
        package,
        required={
            "name",
            "display_name",
            "manager",
            "manager_version",
            "node_range",
        },
        label="request.package",
    )
    package_name = _require_string(package["name"], "request.package.name")
    if PACKAGE_NAME.fullmatch(package_name) is None:
        _error("request.package.name is not a supported package name")
    display_name = _require_string(
        package["display_name"], "request.package.display_name"
    )
    manager = _require_string(package["manager"], "request.package.manager")
    if SAFE_COMMAND.fullmatch(manager) is None:
        _error("request.package.manager must be a single safe command name")
    if manager not in SUPPORTED_PACKAGE_MANAGERS:
        _error(
            "request.package.manager must be a supported package manager: "
            + ", ".join(sorted(SUPPORTED_PACKAGE_MANAGERS))
        )
    manager_version = _validate_exact_version(
        package["manager_version"], "request.package.manager_version"
    )
    node_range = _require_string(package["node_range"], "request.package.node_range")
    if NODE_VERSION_RANGE.fullmatch(node_range) is None or not any(
        character.isdigit() for character in node_range
    ):
        _error("request.package.node_range must be a supported Node version range")

    versions_value = _require_dict(request["versions"], "request.versions")
    versions: dict[str, str] = {}
    for key, value in versions_value.items():
        versions[_require_string(key, "request.versions key")] = (
            _validate_exact_version(value, f"request.versions.{key}")
        )

    capabilities = _require_dict(request["capabilities"], "request.capabilities")
    _require_exact_keys(
        capabilities,
        required={
            "routing",
            "styling",
            "testing",
            "public_environment",
            "lint",
            "format",
        },
        label="request.capabilities",
    )
    if capabilities["styling"] != "plain-css":
        _error("request.capabilities.styling must be plain-css")
    if capabilities["testing"] != "vitest":
        _error("request.capabilities.testing must be vitest")

    routing = _require_dict(capabilities["routing"], "request.capabilities.routing")
    _require_exact_keys(
        routing,
        required={"profile", "version"},
        label="request.capabilities.routing",
    )
    routing_profile = routing["profile"]
    if routing_profile not in {"none", "react-router"}:
        _error("request.capabilities.routing.profile is unsupported")
    routing_version = routing["version"]
    if routing_profile == "none":
        if routing_version is not None:
            _error("routing.version must be null when routing.profile is none")
    else:
        routing_version = _validate_exact_version(
            routing_version, "request.capabilities.routing.version"
        )
        if versions.get("react-router") != routing_version:
            _error("request.versions.react-router must match routing.version")

    lint = _require_dict(capabilities["lint"], "request.capabilities.lint")
    _require_exact_keys(
        lint,
        required={"profile", "version"},
        label="request.capabilities.lint",
    )
    lint_profile = lint["profile"]
    if lint_profile not in {"none", "oxlint"}:
        _error("request.capabilities.lint.profile is unsupported")
    lint_version = lint["version"]
    if lint_profile == "none":
        if lint_version is not None:
            _error("lint.version must be null when lint.profile is none")
    else:
        lint_version = _validate_exact_version(
            lint_version, "request.capabilities.lint.version"
        )
        if versions.get("oxlint") != lint_version:
            _error("request.versions.oxlint must match lint.version")

    formatting = _require_dict(capabilities["format"], "request.capabilities.format")
    _require_exact_keys(
        formatting,
        required={"profile", "version"},
        label="request.capabilities.format",
    )
    format_profile = formatting["profile"]
    if format_profile not in {"none", "prettier"}:
        _error("request.capabilities.format.profile is unsupported")
    format_version = formatting["version"]
    if format_profile == "none":
        if format_version is not None:
            _error("format.version must be null when format.profile is none")
    else:
        format_version = _validate_exact_version(
            format_version, "request.capabilities.format.version"
        )
        if versions.get("prettier") != format_version:
            _error("request.versions.prettier must match format.version")

    required_version_keys = set(BASE_VERSION_KEYS)
    if routing_profile == "react-router":
        required_version_keys.add("react-router")
    if lint_profile == "oxlint":
        required_version_keys.add("oxlint")
    if format_profile == "prettier":
        required_version_keys.add("prettier")
    if set(versions) != required_version_keys:
        missing = sorted(required_version_keys - versions.keys())
        unknown = sorted(versions.keys() - required_version_keys)
        _error(
            "request.versions must match the selected profiles; "
            f"missing={missing}, unknown={unknown}"
        )

    public_environment = _require_dict(
        capabilities["public_environment"],
        "request.capabilities.public_environment",
    )
    _require_exact_keys(
        public_environment,
        required={"variables"},
        label="request.capabilities.public_environment",
    )
    variables: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, variable_value in enumerate(
        _require_list(
            public_environment["variables"],
            "request.capabilities.public_environment.variables",
        )
    ):
        variable = _require_dict(
            variable_value,
            f"request.capabilities.public_environment.variables[{index}]",
        )
        _require_exact_keys(
            variable,
            required={"name", "required"},
            label=f"request.capabilities.public_environment.variables[{index}]",
        )
        name = _require_string(
            variable["name"],
            f"request.capabilities.public_environment.variables[{index}].name",
        )
        if PUBLIC_ENV_NAME.fullmatch(name) is None:
            _error(f"public environment name must use the VITE_ prefix: {name}")
        compact_name = name.replace("_", "")
        if any(
            marker.replace("_", "") in compact_name for marker in SECRET_ENV_MARKERS
        ):
            _error(f"secret-like public environment name is forbidden: {name}")
        if name in names:
            _error(f"duplicate public environment name: {name}")
        names.add(name)
        variables.append(
            {
                "name": name,
                "required": _require_bool(
                    variable["required"],
                    (
                        "request.capabilities.public_environment."
                        f"variables[{index}].required"
                    ),
                ),
            }
        )
    variables.sort(key=lambda item: item["name"])

    expected_relative_paths = set(BASE_PATHS)
    if routing_profile == "react-router":
        expected_relative_paths.add("src/router.tsx")
    if lint_profile == "oxlint":
        expected_relative_paths.add(".oxlintrc.json")
    if format_profile == "prettier":
        expected_relative_paths.update({".prettierignore", ".prettierrc.json"})
    expected_paths = {f"{root}/{path}" for path in expected_relative_paths}

    assigned = {
        _normalize_relative_path(path, "request.assigned_paths[]")
        for path in _require_list(request["assigned_paths"], "request.assigned_paths")
    }
    excluded = {
        _normalize_relative_path(path, "request.excluded_paths[]")
        for path in _require_list(request["excluded_paths"], "request.excluded_paths")
    }
    if len(assigned) != len(request["assigned_paths"]):
        _error("request.assigned_paths must be unique")
    if len(excluded) != len(request["excluded_paths"]):
        _error("request.excluded_paths must be unique")
    outside = sorted(path for path in assigned if not path.startswith(f"{root}/"))
    if outside:
        _error(f"assigned paths escape the component root: {', '.join(outside)}")
    overlaps = sorted(
        (assigned_path, excluded_path)
        for assigned_path in assigned
        for excluded_path in excluded
        if _paths_overlap(assigned_path, excluded_path)
    )
    if overlaps:
        _error(
            "assigned and excluded paths must not overlap: "
            + ", ".join(f"{assigned} <> {excluded}" for assigned, excluded in overlaps)
        )
    if assigned != expected_paths:
        _error(
            "assigned paths must exactly match the selected profile; "
            f"missing={sorted(expected_paths - assigned)}, "
            f"extra={sorted(assigned - expected_paths)}"
        )

    normalized = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "candidate_set_id": candidate_set_id,
        "profile": PROFILE,
        "component_id": component_id,
        "materialization_unit_id": unit_id,
        "component_root": root,
        "assigned_paths": sorted(assigned),
        "excluded_paths": sorted(excluded),
        "package": {
            "name": package_name,
            "display_name": display_name,
            "manager": manager,
            "manager_version": manager_version,
            "node_range": node_range,
        },
        "versions": {key: versions[key] for key in sorted(versions)},
        "capabilities": {
            "routing": {
                "profile": routing_profile,
                "version": routing_version,
            },
            "styling": "plain-css",
            "testing": "vitest",
            "public_environment": {"variables": variables},
            "lint": {"profile": lint_profile, "version": lint_version},
            "format": {"profile": format_profile, "version": format_version},
        },
    }
    return normalized, expected_relative_paths


def _markdown_heading(value: str) -> str:
    html_safe = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>-])", r"\\\1", html_safe)


def _render_template(template_path: Path, values: dict[str, str]) -> bytes:
    rendered = template_path.read_text(encoding="utf-8")
    for key, value in values.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    match = UNRESOLVED_PLACEHOLDER.search(rendered)
    if match is not None:
        _error(
            f"template {template_path.name} contains unresolved placeholder "
            f"{match.group(0)}"
        )
    return rendered.encode("utf-8")


def _render_public_environment(variables: list[dict[str, Any]]) -> bytes:
    if not variables:
        return (
            "export const publicEnvironment = Object.freeze({}) as const;\n"
        ).encode()
    key_union = " | ".join(json.dumps(item["name"]) for item in variables)
    fields = "\n".join(
        (f"  readonly {item['name']}{'' if item['required'] else '?'}: string;")
        for item in variables
    )
    values = "\n".join(
        (
            f"  {item['name']}: readPublicEnvironment("
            f"{json.dumps(item['name'])}, "
            f"{str(item['required']).lower()}),"
        )
        for item in variables
    )
    return (
        f"type PublicEnvironmentKey = {key_union};\n\n"
        "type PublicEnvironment = Readonly<{\n"
        f"{fields}\n"
        "}>;\n\n"
        "const source = import.meta.env as Record<\n"
        "  string,\n"
        "  string | boolean | undefined\n"
        ">;\n\n"
        "function readPublicEnvironment(\n"
        "  name: PublicEnvironmentKey,\n"
        "  required: true,\n"
        "): string;\n"
        "function readPublicEnvironment(\n"
        "  name: PublicEnvironmentKey,\n"
        "  required: false,\n"
        "): string | undefined;\n"
        "function readPublicEnvironment(\n"
        "  name: PublicEnvironmentKey,\n"
        "  required: boolean,\n"
        "): string | undefined {\n"
        "  const value = source[name];\n\n"
        '  if (typeof value === "string" && value.length > 0) {\n'
        "    return value;\n"
        "  }\n"
        "  if (required) {\n"
        "    throw new Error(`Missing required public environment variable: ${name}`);\n"
        "  }\n"
        "  return undefined;\n"
        "}\n\n"
        "export const publicEnvironment: PublicEnvironment = Object.freeze({\n"
        f"{values}\n"
        "});\n"
    ).encode("utf-8")


def _render_files(
    request: dict[str, Any], expected_relative_paths: set[str]
) -> dict[str, bytes]:
    package = request["package"]
    versions = request["versions"]
    capabilities = request["capabilities"]
    commands = ["dev"]
    if capabilities["lint"]["profile"] == "oxlint":
        commands.append("lint")
    if capabilities["format"]["profile"] == "prettier":
        commands.append("format")
    commands.extend(["typecheck", "test", "build"])
    command_lines = "\n".join(
        f"{package['manager']} run {command}" for command in commands
    )
    values = {
        "PACKAGE_NAME": package["name"],
        "DISPLAY_NAME_MARKDOWN": _markdown_heading(package["display_name"]),
        "DISPLAY_NAME_HTML": html.escape(package["display_name"], quote=True),
        "DISPLAY_NAME_JSON": json.dumps(package["display_name"], ensure_ascii=False),
        "PACKAGE_MANAGER": package["manager"],
        "PACKAGE_MANAGER_VERSION": package["manager_version"],
        "NODE_RANGE_JSON": json.dumps(package["node_range"]),
        "REACT_VERSION": versions["react"],
        "REACT_DOM_VERSION": versions["react-dom"],
        "REACT_TYPES_VERSION": versions["@types/react"],
        "REACT_DOM_TYPES_VERSION": versions["@types/react-dom"],
        "NODE_TYPES_VERSION": versions["@types/node"],
        "TYPESCRIPT_VERSION": versions["typescript"],
        "VITE_VERSION": versions["vite"],
        "VITE_REACT_PLUGIN_VERSION": versions["@vitejs/plugin-react"],
        "VITEST_VERSION": versions["vitest"],
        "JSDOM_VERSION": versions["jsdom"],
        "TESTING_LIBRARY_REACT_VERSION": versions["@testing-library/react"],
        "TESTING_LIBRARY_JEST_DOM_VERSION": versions["@testing-library/jest-dom"],
        "COMMANDS": command_lines,
    }
    template_root = Path(__file__).resolve().parent.parent / "assets" / "react-vite"
    files: dict[str, bytes] = {}
    for relative, template_relative in TEMPLATE_PATHS.items():
        template = template_root.joinpath(*PurePosixPath(template_relative).parts)
        files[relative] = _render_template(template, values)

    package_json = json.loads(files["package.json"].decode("utf-8"))
    scripts = package_json["scripts"]
    dependencies = package_json["dependencies"]
    dev_dependencies = package_json["devDependencies"]
    if capabilities["routing"]["profile"] == "react-router":
        dependencies["react-router"] = versions["react-router"]
        files["src/main.tsx"] = _render_template(
            template_root / "src/main.router.tsx.template", values
        )
        files["src/router.tsx"] = _render_template(
            template_root / "src/router.tsx.template", values
        )
    if capabilities["lint"]["profile"] == "oxlint":
        scripts["lint"] = "oxlint"
        dev_dependencies["oxlint"] = versions["oxlint"]
        files[".oxlintrc.json"] = b"{}\n"
    if capabilities["format"]["profile"] == "prettier":
        scripts["format"] = "prettier . --check"
        scripts["format:write"] = "prettier . --write"
        dev_dependencies["prettier"] = versions["prettier"]
        files[".prettierrc.json"] = b"{}\n"
        files[".prettierignore"] = b"dist/\nnode_modules/\n"
    package_json["scripts"] = dict(sorted(scripts.items()))
    package_json["dependencies"] = dict(sorted(dependencies.items()))
    package_json["devDependencies"] = dict(sorted(dev_dependencies.items()))
    files["package.json"] = (
        json.dumps(package_json, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    variables = capabilities["public_environment"]["variables"]
    files["src/env.ts"] = _render_public_environment(variables)
    files[".env.example"] = "".join(f"{item['name']}=\n" for item in variables).encode(
        "utf-8"
    )
    if set(files) != expected_relative_paths:
        _error(
            "rendered files do not match the selected profile; "
            f"missing={sorted(expected_relative_paths - files.keys())}, "
            f"extra={sorted(files.keys() - expected_relative_paths)}"
        )
    return files


def _validation_records(request: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_set_id = request["candidate_set_id"]
    manager = request["package"]["manager"]
    unit_id = request["materialization_unit_id"]
    records = [
        {
            "id": f"{candidate_set_id}:static",
            "owner": OWNER,
            "materialization_unit_id": unit_id,
            "candidate_set_id": candidate_set_id,
            "phase": "candidate",
            "command": (
                "python3 frontend-project/scripts/frontend_project.py validate "
                "--manifest <candidate-manifest>"
            ),
            "network_required": False,
            "status": "passed",
        }
    ]
    commands = ["typecheck", "test", "build"]
    if request["capabilities"]["lint"]["profile"] == "oxlint":
        commands.insert(0, "lint")
    if request["capabilities"]["format"]["profile"] == "prettier":
        commands.insert(0, "format")
    for command in commands:
        records.append(
            {
                "id": f"{candidate_set_id}:{command}",
                "owner": OWNER,
                "materialization_unit_id": unit_id,
                "candidate_set_id": candidate_set_id,
                "phase": "post-apply",
                "command": f"{manager} run {command}",
                "network_required": False,
                "status": "pending",
            }
        )
    return records


def render_candidates(request_path: Path, output: Path) -> dict[str, Any]:
    request, expected_relative_paths = _normalized_request(
        _load_json(request_path, "request")
    )
    output = _make_private_directory(output)
    files = _render_files(request, expected_relative_paths)
    input_sha256 = _sha256_bytes(_canonical_bytes(request))
    entries: list[dict[str, Any]] = []
    for relative in sorted(files):
        candidate = f"files/{relative}"
        payload = files[relative]
        _write_private_bytes(output, candidate, payload)
        entries.append(
            {
                "path": f"{request['component_root']}/{relative}",
                "candidate": candidate,
                "mode": FILE_MODE,
                "sha256": _sha256_bytes(payload),
            }
        )
    manifest = {
        "schema_version": CANDIDATE_MANIFEST_SCHEMA_VERSION,
        "candidate_set_id": request["candidate_set_id"],
        "owner": OWNER,
        "materialization_unit_id": request["materialization_unit_id"],
        "profile": PROFILE,
        "input_sha256": input_sha256,
        "inputs": request,
        "files": entries,
        "validations": _validation_records(request),
    }
    manifest_bytes = _pretty_bytes(manifest)
    _write_private_bytes(output, "manifest.json", manifest_bytes)
    validate_candidate_manifest(output / "manifest.json")
    return {
        "ok": True,
        "manifest": str(output / "manifest.json"),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "input_sha256": input_sha256,
        "file_count": len(entries),
    }


def _validate_validation_record(value: Any, index: int) -> dict[str, Any]:
    record = _require_dict(value, f"manifest.validations[{index}]")
    _require_exact_keys(
        record,
        required={
            "id",
            "owner",
            "materialization_unit_id",
            "candidate_set_id",
            "phase",
            "command",
            "network_required",
            "status",
        },
        label=f"manifest.validations[{index}]",
    )
    for field in (
        "id",
        "owner",
        "materialization_unit_id",
        "candidate_set_id",
        "command",
    ):
        _require_string(record[field], f"manifest.validations[{index}].{field}")
    if record["phase"] not in {"candidate", "post-apply"}:
        _error(f"manifest.validations[{index}].phase is invalid")
    _require_bool(
        record["network_required"],
        f"manifest.validations[{index}].network_required",
    )
    if record["status"] not in {"passed", "pending", "not-run", "failed"}:
        _error(f"manifest.validations[{index}].status is invalid")
    return record


def validate_candidate_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest_path = Path(os.path.abspath(os.path.expanduser(str(manifest_path))))
    _require_private_file(manifest_path, "candidate manifest")
    root = manifest_path.parent
    _require_private_directory(root, "candidate root")
    manifest_bytes = _read_regular_bytes(manifest_path, "candidate manifest")
    manifest = _require_dict(
        _parse_json_bytes(manifest_bytes, "candidate manifest"),
        "candidate manifest",
    )
    _require_exact_keys(
        manifest,
        required={
            "schema_version",
            "candidate_set_id",
            "owner",
            "materialization_unit_id",
            "profile",
            "input_sha256",
            "inputs",
            "files",
            "validations",
        },
        label="candidate manifest",
    )
    if manifest["schema_version"] != CANDIDATE_MANIFEST_SCHEMA_VERSION:
        _error(
            "unsupported candidate manifest schema_version: "
            f"{manifest['schema_version']}"
        )
    if manifest["owner"] != OWNER or manifest["profile"] != PROFILE:
        _error("candidate manifest owner/profile is unsupported")
    for field in ("candidate_set_id", "materialization_unit_id"):
        _require_string(manifest[field], f"manifest.{field}")
    if not _is_digest(manifest["input_sha256"]):
        _error("manifest.input_sha256 is invalid")
    normalized_inputs, expected_relative_paths = _normalized_request(
        _require_dict(manifest["inputs"], "manifest.inputs")
    )
    if manifest["input_sha256"] != _sha256_bytes(_canonical_bytes(normalized_inputs)):
        _error("manifest.input_sha256 does not match manifest.inputs")
    if (
        manifest["candidate_set_id"] != normalized_inputs["candidate_set_id"]
        or manifest["materialization_unit_id"]
        != normalized_inputs["materialization_unit_id"]
        or manifest["profile"] != normalized_inputs["profile"]
    ):
        _error("candidate manifest identity does not match manifest.inputs")

    file_paths: set[str] = set()
    candidate_paths: set[str] = set()
    files = _require_list(manifest["files"], "manifest.files")
    if not files:
        _error("manifest.files must be non-empty")
    for index, file_value in enumerate(files):
        file = _require_dict(file_value, f"manifest.files[{index}]")
        _require_exact_keys(
            file,
            required={"path", "candidate", "mode", "sha256"},
            label=f"manifest.files[{index}]",
        )
        path = _normalize_relative_path(file["path"], f"manifest.files[{index}].path")
        candidate = _normalize_relative_path(
            file["candidate"], f"manifest.files[{index}].candidate"
        )
        if not candidate.startswith("files/"):
            _error(f"manifest.files[{index}].candidate must be under files/")
        if file["mode"] != FILE_MODE:
            _error(f"manifest.files[{index}].mode must be {FILE_MODE}")
        if not _is_digest(file["sha256"]):
            _error(f"manifest.files[{index}].sha256 is invalid")
        if path in file_paths or candidate in candidate_paths:
            _error("candidate manifest file paths must be unique")
        file_paths.add(path)
        candidate_paths.add(candidate)
        candidate_path = root.joinpath(*PurePosixPath(candidate).parts)
        _validate_private_parent_chain(root, candidate_path.parent)
        try:
            candidate_path.resolve(strict=True).relative_to(root.resolve(strict=True))
        except (FileNotFoundError, ValueError):
            _error(f"candidate escapes or is missing: {candidate}")
        _require_private_file(candidate_path, "candidate")
        if (
            _sha256_bytes(_read_regular_bytes(candidate_path, "candidate"))
            != file["sha256"]
        ):
            _error(f"candidate digest mismatch: {candidate}")
    expected_paths = {
        f"{normalized_inputs['component_root']}/{relative}"
        for relative in expected_relative_paths
    }
    if file_paths != expected_paths:
        _error("candidate manifest paths do not match manifest.inputs")

    validation_ids: set[str] = set()
    candidate_checks = 0
    normalized_validations: list[dict[str, Any]] = []
    for index, validation_value in enumerate(
        _require_list(manifest["validations"], "manifest.validations")
    ):
        record = _validate_validation_record(validation_value, index)
        if (
            record["owner"] != OWNER
            or record["candidate_set_id"] != manifest["candidate_set_id"]
            or record["materialization_unit_id"] != manifest["materialization_unit_id"]
        ):
            _error(f"manifest.validations[{index}] binding does not match manifest")
        normalized_validations.append(record)
        if record["id"] in validation_ids:
            _error(f"duplicate validation id: {record['id']}")
        validation_ids.add(record["id"])
        if record["phase"] == "candidate":
            candidate_checks += 1
            if record["status"] != "passed" or record["network_required"]:
                _error("candidate-phase validation must pass offline")
    if candidate_checks == 0:
        _error("candidate manifest requires a passed candidate-phase validation")
    if normalized_validations != _validation_records(normalized_inputs):
        _error("candidate manifest validations do not match manifest.inputs")
    return {
        "ok": True,
        "manifest": str(manifest_path.resolve(strict=True)),
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "input_sha256": manifest["input_sha256"],
        "file_count": len(file_paths),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render or validate deterministic React/Vite candidates."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    render = subparsers.add_parser("render")
    render.add_argument("--request", required=True, type=Path)
    render.add_argument("--output", required=True, type=Path)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "render":
            result = render_candidates(arguments.request, arguments.output)
        else:
            result = validate_candidate_manifest(arguments.manifest)
    except (FrontendProjectError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
