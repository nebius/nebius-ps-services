#!/usr/bin/env python3
"""Audit container source, local images, and explicitly selected validation modes."""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from container_audit_docker import (
    _cleanup_owned_image,
    _image_identity_findings,
    build_image,
    docker_capabilities,
    inspect_local_image,
    run_build_check,
    validate_compose_with_docker,
)
from container_audit_source import (
    MAX_SCANNED_FILES,
    _image_is_exact,
    _relative,
    _scoped_input,
    audit_compose_text,
    audit_dockerfile,
    discover_container_files,
)
from container_audit_types import AuditError, Finding
from container_runtime_common import run_command
from container_supply_chain import run_supply_chain

SCHEMA = "container-audit/v1"
PLATFORM_COMPONENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _platform_name(system: str, machine: str) -> str:
    architecture = machine.casefold()
    aliases = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }
    return f"{system.casefold()}/{aliases.get(architecture, architecture)}"


def _safe_image_reference(value: str) -> str:
    if (
        not value
        or value.startswith("-")
        or len(value) > 512
        or any(character in value for character in ("\x00", "\n", "\r"))
    ):
        raise AuditError("--image must be a bounded OCI reference")
    return value


def _parse_platforms(value: str | None) -> list[tuple[str, str, str | None]]:
    parsed: list[tuple[str, str, str | None]] = []
    for raw in (value or "").split(","):
        if not raw.strip():
            continue
        components = raw.strip().split("/")
        if len(components) not in {2, 3} or not all(
            PLATFORM_COMPONENT_RE.fullmatch(component) for component in components
        ):
            raise AuditError(
                "--platform must use comma-separated os/arch[/variant] values"
            )
        parsed.append(
            (
                components[0],
                components[1],
                components[2] if len(components) == 3 else None,
            )
        )
    return parsed


def _status(findings: Iterable[Finding]) -> str:
    severities = {finding.severity for finding in findings}
    if "error" in severities:
        return "fail"
    if "warning" in severities:
        return "review"
    return "pass"


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Container Audit",
        "",
        f"- Status: `{report['status']}`",
        f"- Host platform: `{report['context']['host_platform']}`",
        f"- Target platform: `{report['context']['target_platform'] or 'not specified'}`",
        f"- Network allowed: `{str(report['context']['network_allowed']).lower()}`",
        "",
        "## Files",
        "",
    ]
    for group, paths in report["files"].items():
        lines.append(f"- {group}: {', '.join(f'`{path}`' for path in paths) or 'none'}")
    lines.extend(["", "## Findings", ""])
    if not report["findings"]:
        lines.append("- None.")
    for finding in report["findings"]:
        location = finding.get("path") or ""
        if finding.get("line"):
            location += f":{finding['line']}"
        suffix = f" ({location})" if location else ""
        lines.append(
            f"- **{finding['severity'].upper()} `{finding['code']}`**: "
            f"{finding['message']}{suffix}"
        )
    lines.extend(["", "## Evidence", "", "```json"])
    lines.append(json.dumps(report["evidence"], indent=2, sort_keys=True))
    lines.append("```")
    return "\n".join(lines) + "\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=Path("."))
    parser.add_argument("--dockerfile", type=Path)
    parser.add_argument("--compose-file", type=Path, action="append", default=[])
    parser.add_argument("--image")
    parser.add_argument("--platform")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--runtime-test", action="store_true")
    parser.add_argument("--supply-chain", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--keep-image", action="store_true")
    return parser


def audit(args: argparse.Namespace) -> dict[str, Any]:
    root = args.path.resolve()
    if not root.is_dir():
        raise AuditError(f"--path is not a directory: {root}")
    if args.build and not args.allow_network:
        raise AuditError("--build requires --allow-network because builders may pull")
    if args.keep_image and not args.build:
        raise AuditError("--keep-image requires --build")
    if args.runtime_test and not args.image and not args.build:
        raise AuditError("--runtime-test requires --image or --build")
    if args.supply_chain and not args.image and not args.build:
        raise AuditError("--supply-chain requires --image or --build")
    if args.build and args.image:
        raise AuditError("--build creates its own image tag; omit --image")
    image = _safe_image_reference(args.image) if args.image else None

    discovered, discovery_truncated, discovery_findings = discover_container_files(root)
    dockerfiles = (
        [_scoped_input(root, args.dockerfile, "--dockerfile")]
        if args.dockerfile
        else discovered["dockerfiles"]
    )
    compose_files = (
        [_scoped_input(root, path, "--compose-file") for path in args.compose_file]
        if args.compose_file
        else discovered["compose_files"]
    )
    findings: list[Finding] = list(discovery_findings)
    if discovery_truncated:
        findings.append(
            Finding(
                "discovery.truncated",
                "error",
                f"Container-file discovery stopped after {MAX_SCANNED_FILES} files.",
            )
        )
    platforms = _parse_platforms(args.platform)
    if args.build and args.dockerfile is None and len(dockerfiles) != 1:
        raise AuditError(
            "--build requires --dockerfile when discovery does not find exactly one Dockerfile"
        )
    if len(platforms) > 1 and (
        args.keep_image or args.runtime_test or args.supply_chain
    ):
        raise AuditError(
            "multi-platform builds cannot use --keep-image, --runtime-test, or --supply-chain"
        )
    for path in dockerfiles:
        findings.extend(audit_dockerfile(path, root))
    for path in compose_files:
        findings.extend(audit_compose_text(path, root))
    for path in discovered["bake_files"]:
        findings.append(
            Finding(
                "bake.unvalidated",
                "warning",
                "Bake definition was discovered but is not semantically validated by the offline audit.",
                _relative(path, root),
            )
        )
    if (
        not dockerfiles
        and not compose_files
        and not discovered["bake_files"]
        and not args.image
        and not args.build
    ):
        findings.append(
            Finding(
                "scope.empty",
                "error",
                "No Dockerfile, Containerfile, Compose, Bake, or image target was found.",
            )
        )

    capabilities = docker_capabilities()
    evidence: dict[str, Any] = {
        "docker": capabilities,
        "discovery_truncated": discovery_truncated,
    }
    if capabilities.get("compose"):
        compose_results = []
        for path in compose_files:
            finding = validate_compose_with_docker(path)
            if finding:
                findings.append(finding)
                compose_results.append(
                    {"path": _relative(path, root), "status": "fail"}
                )
            else:
                compose_results.append(
                    {"path": _relative(path, root), "status": "pass"}
                )
        evidence["compose_config"] = compose_results

    image_summary: dict[str, Any] | None = None
    if image:
        if not _image_is_exact(image):
            findings.append(
                Finding(
                    "image.mutable-reference",
                    "error",
                    "Existing-image audits require an explicit non-latest tag or digest.",
                )
            )
        if not capabilities.get("daemon_available"):
            findings.append(
                Finding(
                    "image.daemon-unavailable",
                    "error",
                    "Local image inspection requires an available Docker daemon.",
                )
            )
        else:
            image_summary, finding = inspect_local_image(image)
            if finding:
                findings.append(finding)
            else:
                evidence["image"] = image_summary
                findings.extend(_image_identity_findings(image_summary))
                requested = platforms[0] if len(platforms) == 1 else None
                mismatch = bool(
                    requested
                    and (
                        requested[0] != image_summary.get("os")
                        or requested[1] != image_summary.get("architecture")
                        or (
                            requested[2]
                            and image_summary.get("variant")
                            and requested[2] != image_summary.get("variant")
                        )
                    )
                )
                if mismatch:
                    findings.append(
                        Finding(
                            "platform.image-mismatch",
                            "error",
                            "The local image OS, architecture, or variant does not match --platform.",
                        )
                    )
                elif requested and requested[2] and not image_summary.get("variant"):
                    findings.append(
                        Finding(
                            "platform.variant-unverified",
                            "warning",
                            "The requested image variant was not present in local inspection evidence.",
                        )
                    )

    build_cleanup_token: str | None = None
    if args.build:
        if not capabilities.get("buildx"):
            raise AuditError("--build requires Docker Buildx")
        if capabilities.get("build_check"):
            check_findings, check_evidence = run_build_check(
                root,
                dockerfiles[0],
                platforms,
            )
            findings.extend(check_findings)
            evidence["build_check"] = check_evidence
        else:
            evidence["build_check"] = {"supported": False}
        retain_for_checks = args.keep_image or args.runtime_test or args.supply_chain
        image, build_cleanup_token, build_findings, build_evidence = build_image(
            root,
            dockerfiles[0],
            args.platform,
            retain_for_checks,
        )
        findings.extend(build_findings)
        evidence["build"] = build_evidence
        if image:
            image_summary, finding = inspect_local_image(image)
            if finding:
                findings.append(finding)
            else:
                evidence["image"] = image_summary
                findings.extend(_image_identity_findings(image_summary))

    try:
        if args.runtime_test:
            if not image:
                findings.append(
                    Finding(
                        "runtime.image-unavailable",
                        "error",
                        "No retained local image is available for the runtime test.",
                    )
                )
            else:
                smoke_script = Path(__file__).with_name("container_smoke_test.py")
                smoke = run_command(
                    [
                        sys.executable,
                        str(smoke_script),
                        "--image",
                        image,
                        "--format",
                        "json",
                    ],
                    timeout=180,
                )
                evidence["runtime_test"] = {
                    "returncode": smoke.returncode,
                    "output_truncated": (
                        smoke.stdout_truncated or smoke.stderr_truncated
                    ),
                }
                if smoke.returncode == 0:
                    try:
                        smoke_report = json.loads(smoke.stdout)
                    except json.JSONDecodeError:
                        findings.append(
                            Finding(
                                "runtime.smoke-invalid",
                                "error",
                                "The runtime helper returned invalid or oversized JSON.",
                            )
                        )
                    else:
                        if not isinstance(smoke_report, dict):
                            findings.append(
                                Finding(
                                    "runtime.smoke-invalid",
                                    "error",
                                    "The runtime helper returned an unexpected JSON shape.",
                                )
                            )
                        else:
                            evidence["runtime_test"]["status"] = smoke_report.get(
                                "status"
                            )
                if smoke.returncode != 0:
                    findings.append(
                        Finding(
                            "runtime.smoke-failed",
                            "error",
                            "The bounded disposable runtime test failed.",
                        )
                    )

        if args.supply_chain:
            if not image:
                findings.append(
                    Finding(
                        "supply-chain.image-unavailable",
                        "error",
                        "No retained local image is available for supply-chain checks.",
                    )
                )
            else:
                supply_findings, supply_evidence = run_supply_chain(root, image)
                findings.extend(supply_findings)
                evidence["supply_chain"] = supply_evidence
    finally:
        if args.build and image and build_cleanup_token and not args.keep_image:
            cleanup_verified = _cleanup_owned_image(image, build_cleanup_token)
            evidence.setdefault("build", {})["cleanup_verified"] = cleanup_verified
            evidence["build"]["tag_retained"] = False
            if not cleanup_verified:
                findings.append(
                    Finding(
                        "build.cleanup-unverified",
                        "warning",
                        "Task-created image cleanup was not verified; inspect the local audit tag.",
                    )
                )

    file_report = {
        key: [_relative(path, root) for path in sorted(paths)]
        for key, paths in discovered.items()
    }
    report = {
        "schema": SCHEMA,
        "status": _status(findings),
        "context": {
            "root": str(root),
            "host_platform": _platform_name(platform.system(), platform.machine()),
            "builder_platform": None,
            "target_platform": args.platform,
            "runtime_validation_platform": (
                f"{image_summary.get('os')}/{image_summary.get('architecture')}"
                if args.runtime_test
                and image_summary
                and image_summary.get("os")
                and image_summary.get("architecture")
                else None
            ),
            "network_allowed": args.allow_network,
        },
        "files": file_report,
        "findings": [
            asdict(finding)
            for finding in sorted(
                findings,
                key=lambda item: (
                    {"error": 0, "warning": 1, "info": 2}.get(item.severity, 3),
                    item.code,
                    item.path or "",
                    item.line or 0,
                ),
            )
        ],
        "evidence": evidence,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit(args)
    except AuditError as exc:
        print(
            json.dumps({"schema": SCHEMA, "error": str(exc)}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        sys.stdout.write(_render_markdown(report))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
