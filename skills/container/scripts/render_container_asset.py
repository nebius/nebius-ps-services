#!/usr/bin/env python3
"""Render typed container assets and validate the bounded Compose profile."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path, PurePosixPath
from typing import Any


ASSET_ROOT = Path(__file__).resolve().parents[1] / "assets"
PLACEHOLDER_RE = re.compile(r"\{\{([A-Z][A-Z0-9_]*)\}\}")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
DISTRIBUTION_RE = re.compile(r"^[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*$")
VERSION_RE = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
SAFE_EXTERNAL_VALUE_RE = re.compile(
    r"^\$\{[A-Za-z_][A-Za-z0-9_]*(?:(?::?\?)[A-Za-z0-9 _.-]{0,80})?\}$"
)
COMPOSE_ROOT_KEYS = frozenset({"name", "services", "volumes"})
COMPOSE_SERVICE_KEYS = frozenset(
    {
        "build",
        "command",
        "depends_on",
        "entrypoint",
        "environment",
        "healthcheck",
        "image",
        "ports",
        "read_only",
        "security_opt",
        "user",
        "volumes",
    }
)
COMPOSE_BUILD_KEYS = frozenset({"context", "dockerfile", "target"})
COMPOSE_HEALTHCHECK_KEYS = frozenset(
    {"interval", "retries", "start_interval", "start_period", "test", "timeout"}
)
COMPOSE_DEPENDENCY_KEYS = frozenset({"condition"})
COMPOSE_VOLUME_KEYS = frozenset({"read_only", "source", "target", "type"})
ENVIRONMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LOOPBACK_PORT_RE = re.compile(
    r"^127\.0\.0\.1:([1-9][0-9]{0,4}):([1-9][0-9]{0,4})(?:/(tcp|udp))?$"
)
COMPOSE_DURATION_RE = re.compile(r"^(?:[0-9]+(?:us|ms|s|m|h))+$")


class ContainerAssetError(ValueError):
    """A bounded rendering or validation failure."""


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContainerAssetError(f"{label} must be an object with string keys")
    return value


def _exact_keys(values: dict[str, Any], required: set[str], label: str) -> None:
    missing = sorted(required - set(values))
    extra = sorted(set(values) - required)
    if missing or extra:
        raise ContainerAssetError(
            f"{label} keys do not match the contract; missing={missing}, extra={extra}"
        )


def _reject_unknown_keys(
    values: dict[str, Any], allowed: frozenset[str], label: str
) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ContainerAssetError(f"{label} contains unsupported keys: {unknown}")


def _safe_string(value: Any, label: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ContainerAssetError(f"{label} must be a non-empty bounded string")
    if CONTROL_RE.search(value):
        raise ContainerAssetError(f"{label} must not contain control characters")
    return value


def _oci_reference(value: Any, label: str) -> str:
    reference = _safe_string(value, label)
    if reference.count("@") > 1:
        raise ContainerAssetError(
            f"{label} must be an exact OCI image tag or sha256 digest reference"
        )
    name_and_tag, separator, digest = reference.partition("@")
    if separator and not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ContainerAssetError(f"{label} has an invalid sha256 digest")

    last_segment = name_and_tag.rsplit("/", 1)[-1]
    if ":" in last_segment:
        repository, tag = name_and_tag.rsplit(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag):
            raise ContainerAssetError(f"{label} has an invalid image tag")
    else:
        repository, tag = name_and_tag, ""
    if not tag and not digest:
        raise ContainerAssetError(
            f"{label} must include an exact image tag or sha256 digest"
        )
    if tag.casefold() == "latest":
        raise ContainerAssetError(f"{label} must not use the floating latest tag")

    parts = repository.split("/")
    if not parts or any(not part for part in parts):
        raise ContainerAssetError(f"{label} has an invalid repository path")
    authority = parts[0]
    has_authority = len(parts) > 1 and (
        "." in authority or ":" in authority or authority == "localhost"
    )
    repository_parts = parts[1:] if has_authority else parts
    if has_authority:
        host = authority
        if ":" in authority:
            host, port = authority.rsplit(":", 1)
            if not port.isdigit() or not 1 <= int(port) <= 65535:
                raise ContainerAssetError(
                    f"{label} registry port must be numeric and between 1 and 65535"
                )
        if host != "localhost":
            host_labels = host.split(".")
            if any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", host_label)
                for host_label in host_labels
            ):
                raise ContainerAssetError(f"{label} has an invalid registry hostname")
    if not repository_parts or any(
        not re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", part)
        for part in repository_parts
    ):
        raise ContainerAssetError(f"{label} has an invalid repository path")
    return reference


def _relative_path(
    value: Any,
    label: str,
    *,
    allow_dot: bool = False,
) -> str:
    raw = _safe_string(value, label)
    comparable = raw[2:] if allow_dot and raw.startswith("./") else raw
    path = PurePosixPath(comparable)
    if path.is_absolute() or raw.startswith("/") or raw.endswith("/") or not comparable:
        raise ContainerAssetError(f"{label} must be a normalized relative path")
    if any(part in {"", ".", ".."} for part in path.parts):
        if not (allow_dot and raw == "."):
            raise ContainerAssetError(
                f"{label} must not contain dot or parent segments"
            )
    if path.as_posix() != comparable:
        raise ContainerAssetError(f"{label} must already be normalized")
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", raw):
        raise ContainerAssetError(f"{label} must use bounded portable path characters")
    return raw


def _absolute_container_path(value: Any, label: str) -> str:
    raw = _safe_string(value, label)
    path = PurePosixPath(raw)
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ContainerAssetError(
            f"{label} must be a normalized absolute container path"
        )
    if path.as_posix() != raw or not re.fullmatch(r"/[A-Za-z0-9._/-]+", raw):
        raise ContainerAssetError(f"{label} must be a safe normalized container path")
    return raw


def _bounded_int(
    value: Any,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContainerAssetError(f"{label} must be an integer")
    if not minimum <= value <= maximum:
        raise ContainerAssetError(f"{label} must be between {minimum} and {maximum}")
    return value


def _command(
    value: Any, label: str, *, reject_interpolation: bool = False
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContainerAssetError(f"{label} must be a non-empty JSON string array")
    command = [_safe_string(item, f"{label} item") for item in value]
    if reject_interpolation and any("$" in item for item in command):
        raise ContainerAssetError(f"{label} must not use Compose interpolation")
    return command


def _copy_json(sources: list[str], destination: str) -> str:
    return json.dumps([*sources, destination], ensure_ascii=True, separators=(",", ":"))


def _render_template(
    template_name: str,
    replacements: dict[str, str],
    *,
    multiline_replacements: frozenset[str] = frozenset(),
) -> str:
    template = (ASSET_ROOT / template_name).read_text(encoding="utf-8")
    placeholders = set(PLACEHOLDER_RE.findall(template))
    if placeholders != set(replacements):
        raise ContainerAssetError(
            f"{template_name} placeholder contract mismatch; "
            f"template={sorted(placeholders)}, replacements={sorted(replacements)}"
        )
    rendered = template
    for name, value in replacements.items():
        if name in multiline_replacements:
            has_unsafe_control = any(
                (ord(character) < 32 and character != "\n") or ord(character) == 127
                for character in value
            )
        else:
            has_unsafe_control = CONTROL_RE.search(value) is not None
        if has_unsafe_control:
            raise ContainerAssetError(
                f"replacement for {name} must not contain control characters"
            )
        rendered = rendered.replace(f"{{{{{name}}}}}", value)
    if PLACEHOLDER_RE.search(rendered):
        raise ContainerAssetError(f"{template_name} contains unresolved placeholders")
    return rendered


def render_python_dockerfile(raw_values: dict[str, Any]) -> str:
    """Render a hash-locked, version-identifiable Python image Dockerfile."""

    values = _mapping(raw_values, "Python values")
    required = {
        "build_image",
        "runtime_image",
        "package_source_paths",
        "lockfile",
        "distribution_name",
        "project_version",
        "runtime_uid",
        "runtime_gid",
        "runtime_port",
        "runtime_command",
    }
    _exact_keys(values, required, "Python values")

    source_values = values["package_source_paths"]
    if not isinstance(source_values, list) or not source_values:
        raise ContainerAssetError("package_source_paths must be a non-empty path array")
    sources = [
        _relative_path(item, "package_source_paths item") for item in source_values
    ]
    if len(set(sources)) != len(sources):
        raise ContainerAssetError("package_source_paths must not contain duplicates")

    lockfile = _relative_path(values["lockfile"], "lockfile")
    if PurePosixPath(lockfile).suffix not in {".lock", ".txt"}:
        raise ContainerAssetError(
            "lockfile must be a requirements-format .lock or .txt file"
        )
    if lockfile in sources:
        raise ContainerAssetError(
            "lockfile must not be repeated in package_source_paths"
        )
    distribution = _safe_string(values["distribution_name"], "distribution_name")
    if not DISTRIBUTION_RE.fullmatch(distribution):
        raise ContainerAssetError(
            "distribution_name is not a valid Python distribution name"
        )
    version = _safe_string(values["project_version"], "project_version")
    if not VERSION_RE.fullmatch(version) or version == "0+unknown":
        raise ContainerAssetError(
            "project_version must be an approved non-fallback canonical SemVer release"
        )
    scm_env = (
        "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_"
        + re.sub(r"[-_.]+", "_", distribution).upper()
    )

    return _render_template(
        "Dockerfile.python.template",
        {
            "PYTHON_BUILD_IMAGE": _oci_reference(values["build_image"], "build_image"),
            "PYTHON_RUNTIME_IMAGE": _oci_reference(
                values["runtime_image"], "runtime_image"
            ),
            "PYTHON_LOCKFILE_COPY_JSON": _copy_json(
                [lockfile],
                f"./{lockfile}",
            ),
            "PYTHON_LOCKFILE_PATH": shlex.quote(lockfile),
            "PACKAGE_SOURCE_COPY_LINES": "\n".join(
                f"COPY {_copy_json([source], f'./{source}')}" for source in sources
            ),
            "PROJECT_VERSION": version,
            "SCM_PRETEND_VERSION_ENV": scm_env,
            "PYTHON_DISTRIBUTION_NAME": distribution,
            "RUNTIME_UID": str(
                _bounded_int(
                    values["runtime_uid"], "runtime_uid", minimum=1, maximum=2**31 - 1
                )
            ),
            "RUNTIME_GID": str(
                _bounded_int(
                    values["runtime_gid"], "runtime_gid", minimum=1, maximum=2**31 - 1
                )
            ),
            "RUNTIME_PORT": str(
                _bounded_int(
                    values["runtime_port"], "runtime_port", minimum=1, maximum=65535
                )
            ),
            "RUNTIME_COMMAND_JSON": json.dumps(
                _command(values["runtime_command"], "runtime_command"),
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        },
        multiline_replacements=frozenset({"PACKAGE_SOURCE_COPY_LINES"}),
    )


def render_react_vite_dockerfile(raw_values: dict[str, Any]) -> str:
    """Render a React/Vite Dockerfile from a bounded package-manager recipe."""

    values = _mapping(raw_values, "React/Vite values")
    required = {
        "build_image",
        "runtime_image",
        "package_manager",
        "lockfile",
        "build_output_path",
        "static_root",
        "runtime_uid",
        "runtime_port",
        "runtime_command",
    }
    _exact_keys(values, required, "React/Vite values")
    manager = _safe_string(values["package_manager"], "package_manager")
    recipes = {
        "npm": ("npm ci", "npm run build"),
        "pnpm": ("corepack enable && pnpm install --frozen-lockfile", "pnpm run build"),
        "yarn": ("corepack enable && yarn install --immutable", "yarn build"),
    }
    lockfiles = {
        "npm": {"npm-shrinkwrap.json", "package-lock.json"},
        "pnpm": {"pnpm-lock.yaml"},
        "yarn": {"yarn.lock"},
    }
    if manager not in recipes:
        raise ContainerAssetError("package_manager must be npm, pnpm, or yarn")
    install_recipe, build_recipe = recipes[manager]

    lockfile = _relative_path(values["lockfile"], "lockfile")
    if lockfile not in lockfiles[manager]:
        raise ContainerAssetError(
            f"lockfile {lockfile!r} does not match package_manager {manager!r}"
        )
    output_path = _relative_path(values["build_output_path"], "build_output_path")
    static_root = _absolute_container_path(values["static_root"], "static_root")
    return _render_template(
        "Dockerfile.react-vite.template",
        {
            "NODE_BUILD_IMAGE": _oci_reference(values["build_image"], "build_image"),
            "STATIC_RUNTIME_IMAGE": _oci_reference(
                values["runtime_image"], "runtime_image"
            ),
            "PACKAGE_MANIFEST_COPY_JSON": _copy_json(["package.json", lockfile], "./"),
            "INSTALL_RECIPE": install_recipe,
            "BUILD_RECIPE": build_recipe,
            "BUILD_OUTPUT_PATH": output_path,
            "STATIC_ROOT": static_root,
            "RUNTIME_UID": str(
                _bounded_int(
                    values["runtime_uid"], "runtime_uid", minimum=1, maximum=2**31 - 1
                )
            ),
            "RUNTIME_PORT": str(
                _bounded_int(
                    values["runtime_port"], "runtime_port", minimum=1, maximum=65535
                )
            ),
            "RUNTIME_COMMAND_JSON": json.dumps(
                _command(values["runtime_command"], "runtime_command"),
                ensure_ascii=True,
                separators=(",", ":"),
            ),
        },
    )


def _validate_compose_volume(
    value: Any,
    label: str,
    named_volumes: set[str],
) -> None:
    if isinstance(value, str):
        raw = _safe_string(value, label)
        if "docker.sock" in raw:
            raise ContainerAssetError(f"{label} must not mount a Docker socket")
        if re.match(r"^[A-Za-z]:[\\/]", raw):
            raise ContainerAssetError(f"{label} must not use an absolute host bind")
        parts = raw.split(":")
        if len(parts) not in {2, 3}:
            raise ContainerAssetError(
                f"{label} must use SOURCE:TARGET[:MODE] short syntax"
            )
        source, target = parts[:2]
        mode = parts[2] if len(parts) == 3 else ""
        if source.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", source):
            raise ContainerAssetError(f"{label} must not use an absolute host bind")
        is_bind = source.startswith(".")
        if source.startswith("."):
            _relative_path(source, label, allow_dot=True)
        elif not NAME_RE.fullmatch(source):
            raise ContainerAssetError(
                f"{label} source must be a named volume or normalized relative bind"
            )
        elif source not in named_volumes:
            raise ContainerAssetError(
                f"{label} references undeclared named volume {source}"
            )
        _absolute_container_path(target, f"{label} target")
        if is_bind and mode not in {"", "ro"}:
            raise ContainerAssetError(
                f"{label} relative bind mode must be omitted or ro"
            )
        if not is_bind and mode not in {"", "ro", "rw"}:
            raise ContainerAssetError(
                f"{label} named-volume mode must be omitted, ro, or rw"
            )
        return
    volume = _mapping(value, label)
    _reject_unknown_keys(volume, COMPOSE_VOLUME_KEYS, label)
    volume_type = volume.get("type")
    if volume_type == "bind":
        source = _safe_string(volume.get("source"), f"{label}.source")
        if "docker.sock" in source:
            raise ContainerAssetError(f"{label} must not mount a Docker socket")
        _relative_path(source, f"{label}.source", allow_dot=True)
    elif volume_type == "volume":
        source = _safe_string(volume.get("source"), f"{label}.source")
        if not NAME_RE.fullmatch(source):
            raise ContainerAssetError(f"{label}.source must be a named volume")
        if source not in named_volumes:
            raise ContainerAssetError(
                f"{label}.source references undeclared named volume {source}"
            )
    else:
        raise ContainerAssetError(f"{label}.type must be bind or volume")
    _absolute_container_path(volume.get("target"), f"{label}.target")
    if "read_only" in volume and not isinstance(volume["read_only"], bool):
        raise ContainerAssetError(f"{label}.read_only must be a boolean")


def _validate_compose_environment(value: Any, label: str) -> None:
    if isinstance(value, list):
        for index, item in enumerate(value):
            rendered = _safe_string(item, f"{label}[{index}]")
            name, separator, item_value = rendered.partition("=")
            if not ENVIRONMENT_NAME_RE.fullmatch(name):
                raise ContainerAssetError(
                    f"{label}[{index}] has an invalid environment variable name"
                )
            if separator and not SAFE_EXTERNAL_VALUE_RE.fullmatch(item_value):
                raise ContainerAssetError(
                    f"{label}[{index}] value must be an exact external "
                    "reference or required-value expression"
                )
        return
    environment = _mapping(value, label)
    for name, item in environment.items():
        if not ENVIRONMENT_NAME_RE.fullmatch(name):
            raise ContainerAssetError(
                f"{label}.{name} has an invalid environment variable name"
            )
        if item is not None and not isinstance(item, str):
            raise ContainerAssetError(
                f"{label}.{name} must be a string or an external null reference"
            )
        if isinstance(item, str):
            _safe_string(item, f"{label}.{name}")
        if item is not None and not SAFE_EXTERNAL_VALUE_RE.fullmatch(item):
            raise ContainerAssetError(
                f"{label}.{name} value must be an exact external "
                "reference or required-value expression"
            )


def _compose_duration(value: Any, label: str) -> str:
    duration = _safe_string(value, label)
    if not COMPOSE_DURATION_RE.fullmatch(duration):
        raise ContainerAssetError(
            f"{label} must use Compose duration units us, ms, s, m, or h"
        )
    components = re.findall(r"([0-9]+)(?:us|ms|s|m|h)", duration)
    if not components or all(int(component) == 0 for component in components):
        raise ContainerAssetError(f"{label} must be greater than zero")
    return duration


def validate_compose_model(raw_model: Any) -> None:
    """Validate the intentionally narrow local development/test Compose profile."""

    model = _mapping(raw_model, "Compose document")
    _reject_unknown_keys(model, COMPOSE_ROOT_KEYS, "Compose document")
    if "name" in model:
        name = _safe_string(model["name"], "Compose name")
        if not NAME_RE.fullmatch(name):
            raise ContainerAssetError(
                "Compose name must use lowercase portable characters"
            )
    services = _mapping(model.get("services"), "Compose services")
    if not services:
        raise ContainerAssetError("Compose services must not be empty")

    named_volumes: set[str] = set()
    if "volumes" in model:
        volumes = _mapping(model["volumes"], "Compose volumes")
        for volume_name, raw_volume in volumes.items():
            if not NAME_RE.fullmatch(volume_name):
                raise ContainerAssetError(
                    f"invalid Compose named volume: {volume_name}"
                )
            if _mapping(raw_volume, f"volume {volume_name}"):
                raise ContainerAssetError(
                    f"volume {volume_name} must use the bounded empty definition"
                )
            named_volumes.add(volume_name)

    service_models: dict[str, dict[str, Any]] = {}
    health_enabled: dict[str, bool] = {}
    for service_name, raw_service in services.items():
        if not NAME_RE.fullmatch(service_name):
            raise ContainerAssetError(f"invalid Compose service name: {service_name}")
        service = _mapping(raw_service, f"service {service_name}")
        _reject_unknown_keys(
            service,
            COMPOSE_SERVICE_KEYS,
            f"service {service_name}",
        )
        service_models[service_name] = service
        if "image" not in service and "build" not in service:
            raise ContainerAssetError(
                f"service {service_name} must declare an exact image or build"
            )
        if "user" in service:
            user = _safe_string(service["user"], f"service {service_name} user")
            match = re.fullmatch(r"([1-9][0-9]*)(?::([1-9][0-9]*))?", user)
            if match is None:
                raise ContainerAssetError(
                    f"service {service_name} user must be a non-root numeric identity"
                )
        if "security_opt" in service:
            security_options = service["security_opt"]
            if not isinstance(security_options, list):
                raise ContainerAssetError(
                    f"service {service_name} security_opt must be a list"
                )
            if any(
                not isinstance(option, str)
                or option
                not in {
                    "no-new-privileges",
                    "no-new-privileges=true",
                    "no-new-privileges:true",
                }
                for option in security_options
            ):
                raise ContainerAssetError(
                    f"service {service_name} security_opt may only enable "
                    "no-new-privileges"
                )
        if "image" in service:
            _oci_reference(service["image"], f"service {service_name} image")
        build = service.get("build")
        if isinstance(build, str):
            _relative_path(
                build, f"service {service_name} build context", allow_dot=True
            )
        elif build is not None:
            build_model = _mapping(build, f"service {service_name} build")
            _reject_unknown_keys(
                build_model,
                COMPOSE_BUILD_KEYS,
                f"service {service_name} build",
            )
            _relative_path(
                build_model.get("context"),
                f"service {service_name} build context",
                allow_dot=True,
            )
            if "dockerfile" in build_model:
                _relative_path(
                    build_model["dockerfile"],
                    f"service {service_name} Dockerfile path",
                )
            if "target" in build_model:
                target = _safe_string(
                    build_model["target"], f"service {service_name} build target"
                )
                if not NAME_RE.fullmatch(target):
                    raise ContainerAssetError(
                        f"service {service_name} build target is not portable"
                    )
        if "command" in service:
            _command(
                service["command"],
                f"service {service_name} command",
                reject_interpolation=True,
            )
        if "entrypoint" in service:
            _command(
                service["entrypoint"],
                f"service {service_name} entrypoint",
                reject_interpolation=True,
            )
        if "environment" in service:
            _validate_compose_environment(
                service["environment"], f"service {service_name} environment"
            )
        if "ports" in service:
            ports = service["ports"]
            if not isinstance(ports, list):
                raise ContainerAssetError(
                    f"service {service_name} ports must be a list"
                )
            for index, port in enumerate(ports):
                rendered_port = _safe_string(
                    port, f"service {service_name} port {index}"
                )
                match = LOOPBACK_PORT_RE.fullmatch(rendered_port)
                if match is None or any(
                    int(value) > 65535 for value in match.groups()[:2]
                ):
                    raise ContainerAssetError(
                        f"service {service_name} port {index} must bind explicit "
                        "loopback host and valid container ports"
                    )
        if "read_only" in service and not isinstance(service["read_only"], bool):
            raise ContainerAssetError(
                f"service {service_name} read_only must be a boolean"
            )
        if "volumes" in service:
            service_volumes = service["volumes"]
            if not isinstance(service_volumes, list):
                raise ContainerAssetError(
                    f"service {service_name} volumes must be a list"
                )
            for index, volume in enumerate(service_volumes):
                _validate_compose_volume(
                    volume,
                    f"service {service_name} volume {index}",
                    named_volumes,
                )
        healthcheck = service.get("healthcheck")
        health_enabled[service_name] = False
        if healthcheck is not None:
            health_model = _mapping(healthcheck, f"service {service_name} healthcheck")
            _reject_unknown_keys(
                health_model,
                COMPOSE_HEALTHCHECK_KEYS,
                f"service {service_name} healthcheck",
            )
            health_command = _command(
                health_model.get("test"),
                f"service {service_name} healthcheck test",
                reject_interpolation=True,
            )
            if health_command == ["NONE"]:
                health_enabled[service_name] = False
            elif health_command[0] in {"CMD", "CMD-SHELL"}:
                health_enabled[service_name] = True
            else:
                raise ContainerAssetError(
                    f"service {service_name} healthcheck test must start with "
                    "CMD or CMD-SHELL, or be exactly NONE"
                )
            for field in ("interval", "start_interval", "start_period", "timeout"):
                if field in health_model:
                    _compose_duration(
                        health_model[field],
                        f"service {service_name} healthcheck {field}",
                    )
            if "retries" in health_model:
                _bounded_int(
                    health_model["retries"],
                    f"service {service_name} healthcheck retries",
                    minimum=1,
                    maximum=100,
                )

    dependency_graph: dict[str, set[str]] = {
        service_name: set() for service_name in service_models
    }
    for service_name, service in service_models.items():
        depends_on = service.get("depends_on")
        if depends_on is None:
            continue
        dependencies = _mapping(depends_on, f"service {service_name} depends_on")
        for dependency_name, raw_condition in dependencies.items():
            if dependency_name not in service_models:
                raise ContainerAssetError(
                    f"service {service_name} depends on unknown service {dependency_name}"
                )
            dependency_graph[service_name].add(dependency_name)
            condition_model = _mapping(
                raw_condition,
                f"service {service_name} dependency {dependency_name}",
            )
            _reject_unknown_keys(
                condition_model,
                COMPOSE_DEPENDENCY_KEYS,
                f"service {service_name} dependency {dependency_name}",
            )
            condition = condition_model.get("condition")
            if condition not in {"service_started", "service_healthy"}:
                raise ContainerAssetError(
                    f"service {service_name} dependency {dependency_name} "
                    "must declare service_started or service_healthy"
                )
            if condition == "service_healthy" and not health_enabled[dependency_name]:
                raise ContainerAssetError(
                    f"service {service_name} uses service_healthy for "
                    f"{dependency_name} without a healthcheck that is enabled"
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(service_name: str) -> None:
        if service_name in visiting:
            raise ContainerAssetError("Compose depends_on graph must be acyclic")
        if service_name in visited:
            return
        visiting.add(service_name)
        for dependency_name in sorted(dependency_graph[service_name]):
            visit(dependency_name)
        visiting.remove(service_name)
        visited.add(service_name)

    for service_name in sorted(dependency_graph):
        visit(service_name)


def _load_json(path: Path) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ContainerAssetError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContainerAssetError(f"cannot load JSON from {path}: {exc}") from exc


def _load_yaml(path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise ContainerAssetError(
            "PyYAML is required to validate Compose YAML; do not install it implicitly"
        ) from exc

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader: Any, node: Any, deep: bool = False) -> Any:
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise ContainerAssetError("YAML mapping keys must be scalar") from exc
            if duplicate:
                raise ContainerAssetError(f"duplicate YAML key: {key}")
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    try:
        return yaml.load(
            path.read_text(encoding="utf-8"),
            Loader=UniqueKeyLoader,  # noqa: S506 - duplicate-checking SafeLoader.
        )
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ContainerAssetError(
            f"cannot load Compose YAML from {path}: {exc}"
        ) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser(
        "render", help="render a typed Dockerfile to stdout"
    )
    render_parser.add_argument(
        "--kind", choices=("python", "react-vite"), required=True
    )
    render_parser.add_argument("--values", type=Path, required=True)

    compose_parser = subparsers.add_parser(
        "validate-compose", help="validate a Compose file without running Docker"
    )
    compose_parser.add_argument("--file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "render":
            values = _mapping(_load_json(args.values), "render values")
            rendered = (
                render_python_dockerfile(values)
                if args.kind == "python"
                else render_react_vite_dockerfile(values)
            )
            sys.stdout.write(rendered)
        else:
            validate_compose_model(_load_yaml(args.file))
    except ContainerAssetError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
