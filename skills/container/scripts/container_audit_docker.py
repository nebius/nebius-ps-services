#!/usr/bin/env python3
"""Docker inspection, validation, build, and cleanup primitives."""

from __future__ import annotations

import json
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any

from container_audit_source import _relative
from container_audit_types import Finding
from container_runtime_common import run_command

OWNERSHIP_LABEL = "com.openai.codex.container-audit"


def docker_capabilities() -> dict[str, Any]:
    capability: dict[str, Any] = {
        "docker_cli": shutil.which("docker") is not None,
        "daemon_available": False,
        "buildx": False,
        "build_check": False,
        "compose": False,
    }
    if not capability["docker_cli"]:
        return capability
    version = run_command(
        ["docker", "version", "--format", "{{json .}}"],
        timeout=10,
    )
    capability["daemon_available"] = version.returncode == 0
    if version.returncode == 0:
        try:
            data = json.loads(version.stdout)
        except json.JSONDecodeError:
            data = {}
        capability["client_version"] = data.get("Client", {}).get("Version")
        capability["server_version"] = data.get("Server", {}).get("Version")
    buildx = run_command(["docker", "buildx", "version"], timeout=10)
    capability["buildx"] = buildx.returncode == 0
    if capability["buildx"]:
        build_help = run_command(["docker", "buildx", "build", "--help"], timeout=10)
        capability["build_check"] = (
            build_help.returncode == 0 and "--check" in build_help.stdout
        )
    compose = run_command(["docker", "compose", "version", "--short"], timeout=10)
    capability["compose"] = compose.returncode == 0
    return capability


def inspect_local_image(reference: str) -> tuple[dict[str, Any] | None, Finding | None]:
    result = run_command(["docker", "image", "inspect", "--", reference], timeout=30)
    if result.returncode != 0:
        return None, Finding(
            "image.inspect-failed",
            "error",
            "Local image inspection failed; no registry pull was attempted.",
        )
    try:
        items = json.loads(result.stdout)
        item = items[0]
        config = item.get("Config") or {}
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        return None, Finding(
            "image.inspect-invalid",
            "error",
            "Docker returned an invalid or oversized image inspection payload.",
        )
    env_names = sorted(
        {
            value.split("=", 1)[0]
            for value in config.get("Env") or []
            if isinstance(value, str) and "=" in value
        }
    )
    entrypoint = config.get("Entrypoint") or []
    command = config.get("Cmd") or []
    summary = {
        "id": item.get("Id"),
        "architecture": item.get("Architecture"),
        "os": item.get("Os"),
        "variant": item.get("Variant"),
        "user": config.get("User") or "",
        "workdir": config.get("WorkingDir") or "",
        "entrypoint_present": bool(entrypoint),
        "entrypoint_argument_count": max(0, len(entrypoint) - 1),
        "command_present": bool(command),
        "command_argument_count": max(0, len(command) - 1),
        "environment_names": env_names,
        "exposed_ports": sorted((config.get("ExposedPorts") or {}).keys()),
        "label_names": sorted((config.get("Labels") or {}).keys()),
        "healthcheck_present": bool(config.get("Healthcheck")),
    }
    return summary, None


def _image_identity_findings(summary: dict[str, Any]) -> list[Finding]:
    identity = str(summary.get("user") or "")
    match = re.fullmatch(r"([1-9][0-9]*):([1-9][0-9]*)", identity)
    if match is not None:
        return []
    return [
        Finding(
            "image.user-unverified",
            "error",
            "The inspected image does not declare a positive numeric UID and GID.",
        )
    ]


def validate_compose_with_docker(path: Path) -> Finding | None:
    try:
        compose_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return Finding(
            "compose.config-unreadable",
            "error",
            "Compose file cannot be read for safe configuration validation.",
            str(path),
        )
    if re.search(r"(?m)^\s*(?:env_file|file|include)\s*:", compose_text):
        return Finding(
            "compose.config-host-read-skipped",
            "warning",
            "Compose configuration validation was skipped because the file references additional host files.",
            str(path),
        )
    help_result = run_command(["docker", "compose", "config", "--help"], timeout=10)
    if help_result.returncode != 0:
        return Finding(
            "compose.config-unavailable",
            "warning",
            "Docker Compose configuration validation is unavailable.",
            str(path),
        )
    required_flags = {
        "--no-interpolate",
        "--no-env-resolution",
        "--no-path-resolution",
    }
    if not all(flag in help_result.stdout for flag in required_flags):
        return Finding(
            "compose.config-unsafe-version",
            "warning",
            "Installed Compose lacks the no-resolution flags required for a redacted audit.",
            str(path),
        )
    with tempfile.NamedTemporaryFile() as empty_env:
        result = run_command(
            [
                "docker",
                "compose",
                "--env-file",
                empty_env.name,
                "--file",
                str(path),
                "config",
                "--quiet",
                "--no-interpolate",
                "--no-env-resolution",
                "--no-path-resolution",
            ],
            timeout=30,
        )
    if result.returncode != 0:
        return Finding(
            "compose.config-invalid",
            "error",
            "Docker Compose rejected the file; rendered values were not emitted.",
            str(path),
        )
    return None


def _cleanup_owned_image(reference: str, token: str) -> bool:
    inspect = run_command(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            f'{{{{ index .Config.Labels "{OWNERSHIP_LABEL}" }}}}',
            reference,
        ],
        timeout=15,
    )
    if inspect.returncode != 0 or inspect.stdout.strip() != token:
        return False
    remove = run_command(["docker", "image", "rm", "--", reference], timeout=30)
    return remove.returncode == 0


def run_build_check(
    root: Path,
    dockerfile: Path,
    platforms: list[tuple[str, str, str | None]],
) -> tuple[list[Finding], dict[str, Any]]:
    command = [
        "docker",
        "buildx",
        "build",
        "--check",
        "--file",
        str(dockerfile),
    ]
    if platforms:
        command.extend(
            [
                "--platform",
                ",".join(
                    "/".join(item for item in platform if item)
                    for platform in platforms
                ),
            ]
        )
    command.append(str(root))
    result = run_command(command, timeout=120)
    output = f"{result.stdout}\n{result.stderr}"
    warning_detected = bool(re.search(r"\bwarning\b", output, re.IGNORECASE))
    evidence = {
        "supported": True,
        "returncode": result.returncode,
        "output_truncated": result.truncated,
        "warning_detected": warning_detected,
    }
    findings: list[Finding] = []
    if result.returncode != 0:
        findings.append(
            Finding(
                "build.check-failed",
                "error",
                "Dockerfile build checks failed.",
                _relative(dockerfile, root),
            )
        )
    elif warning_detected:
        findings.append(
            Finding(
                "build.check-warning",
                "warning",
                "Dockerfile build checks reported one or more warnings.",
                _relative(dockerfile, root),
            )
        )
    return findings, evidence


def build_image(
    root: Path,
    dockerfile: Path,
    platform_name: str | None,
    keep_image: bool,
) -> tuple[str | None, str | None, list[Finding], dict[str, Any]]:
    token = secrets.token_hex(12)
    tag = f"codex-container-audit:{token}"
    findings: list[Finding] = []
    evidence: dict[str, Any] = {"requested": True, "tag_retained": keep_image}
    platforms = [
        item.strip() for item in (platform_name or "").split(",") if item.strip()
    ]
    with tempfile.TemporaryDirectory(prefix="container-audit-") as directory:
        command = [
            "docker",
            "buildx",
            "build",
            "--pull",
            "--network=default",
            "--label",
            f"{OWNERSHIP_LABEL}={token}",
            "--file",
            str(dockerfile),
        ]
        if platforms:
            command.extend(["--platform", ",".join(platforms)])
        if len(platforms) > 1:
            command.extend(
                ["--output", f"type=oci,dest={Path(directory) / 'image.oci.tar'}"]
            )
        else:
            command.extend(["--load", "--tag", tag])
        command.append(str(root))
        result = run_command(command, timeout=1800)
        evidence["returncode"] = result.returncode
        evidence["output_truncated"] = (
            result.stdout_truncated or result.stderr_truncated
        )
        if result.returncode != 0:
            findings.append(
                Finding(
                    "build.failed",
                    "error",
                    "The selected image target did not build successfully.",
                    _relative(dockerfile, root),
                )
            )
            if len(platforms) <= 1 and not keep_image:
                evidence["cleanup_verified"] = _cleanup_owned_image(tag, token)
                if not evidence["cleanup_verified"]:
                    findings.append(
                        Finding(
                            "build.cleanup-unverified",
                            "warning",
                            "A failed build may have created a local audit tag; task-owned cleanup was not verified.",
                        )
                    )
            return None, None, findings, evidence
        if len(platforms) > 1:
            evidence["artifact"] = "temporary OCI archive"
            evidence["runtime_image_available"] = False
            return None, None, findings, evidence
        evidence["runtime_image_available"] = True
        if not keep_image:
            evidence["cleanup_verified"] = _cleanup_owned_image(tag, token)
            if not evidence["cleanup_verified"]:
                findings.append(
                    Finding(
                        "build.cleanup-unverified",
                        "warning",
                        "Task-created image cleanup was not verified; inspect the local audit tag.",
                    )
                )
            return None, None, findings, evidence
    return tag, token, findings, evidence
