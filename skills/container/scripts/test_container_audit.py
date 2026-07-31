#!/usr/bin/env python3
"""Offline tests for the container audit helper."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("container_audit.py")
sys.path.insert(0, str(SCRIPT.parent))
COMMON = importlib.import_module("container_runtime_common")
SPEC = importlib.util.spec_from_file_location("container_audit", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
DOCKER = importlib.import_module("container_audit_docker")
SOURCE = importlib.import_module("container_audit_source")
SUPPLY = importlib.import_module("container_supply_chain")


def arguments(root: Path, **overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "path": root,
        "dockerfile": None,
        "compose_file": [],
        "image": None,
        "platform": None,
        "format": "json",
        "build": False,
        "runtime_test": False,
        "supply_chain": False,
        "allow_network": False,
        "keep_image": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class DockerfileAuditTest(unittest.TestCase):
    def test_final_stage_controls_and_secret_metadata_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text(
                "\n".join(
                    (
                        "FROM python AS build",
                        "ARG API_TOKEN",
                        "USER root",
                        "FROM python:3.13-slim",
                        'ENTRYPOINT ["python", "-m", "sample"]',
                        "USER 10001:10001",
                    )
                ),
                encoding="utf-8",
            )
            (root / ".dockerignore").write_text(
                ".git\n.env\n.venv\n",
                encoding="utf-8",
            )

            findings = MODULE.audit_dockerfile(dockerfile, root)
            codes = {finding.code for finding in findings}

            self.assertIn("build.secret-metadata", codes)
            self.assertIn("base.floating", codes)
            self.assertNotIn("runtime.root-user", codes)
            self.assertNotIn("runtime.user-missing", codes)

    def test_exact_non_root_dockerfile_has_no_blocking_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            digest = "a" * 64
            dockerfile = root / "Dockerfile"
            dockerfile.write_text(
                "\n".join(
                    (
                        f"FROM python:3.13-slim@sha256:{digest}",
                        "LABEL org.opencontainers.image.source=https://example.invalid/repo \\",
                        "      org.opencontainers.image.revision=abc \\",
                        "      org.opencontainers.image.version=1.2.3",
                        "USER 10001:10001",
                        'CMD ["python", "-m", "sample"]',
                    )
                ),
                encoding="utf-8",
            )
            (root / ".dockerignore").write_text(
                ".git\n.env\n.venv\n",
                encoding="utf-8",
            )

            findings = MODULE.audit_dockerfile(dockerfile, root)

            self.assertFalse(
                [finding for finding in findings if finding.severity == "error"]
            )

    def test_latest_and_missing_ignore_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text(
                "FROM example/service:latest\nUSER 10001\n",
                encoding="utf-8",
            )

            codes = {
                finding.code for finding in MODULE.audit_dockerfile(dockerfile, root)
            }

            self.assertIn("base.latest", codes)
            self.assertIn("context.ignore-missing", codes)

    def test_ignore_file_is_resolved_from_the_actual_build_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "docker"
            nested.mkdir()
            dockerfile = nested / "Dockerfile"
            dockerfile.write_text(
                "FROM example/app:1.0\nUSER 10001\n",
                encoding="utf-8",
            )
            (nested / ".dockerignore").write_text("secret\n", encoding="utf-8")

            codes = {
                finding.code for finding in MODULE.audit_dockerfile(dockerfile, root)
            }
            self.assertIn("context.ignore-missing", codes)

            (root / ".dockerignore").write_text("secret\n", encoding="utf-8")
            codes = {
                finding.code for finding in MODULE.audit_dockerfile(dockerfile, root)
            }
            self.assertNotIn("context.ignore-missing", codes)

    def test_every_multiline_env_key_is_checked_without_echoing_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text(
                "\n".join(
                    (
                        "FROM registry.example.invalid/team/app",
                        "ENV APP_MODE=prod \\",
                        "    API_TOKEN=literal",
                        "USER 10001",
                    )
                ),
                encoding="utf-8",
            )
            (root / ".dockerignore").write_text(
                ".git\n.env\n.venv\n",
                encoding="utf-8",
            )

            findings = MODULE.audit_dockerfile(dockerfile, root)

            self.assertIn("build.secret-metadata", {item.code for item in findings})
            serialized = repr(findings)
            self.assertNotIn("literal", serialized)
            self.assertNotIn("registry.example.invalid", serialized)

    def test_variable_based_final_user_is_not_assumed_non_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text(
                "FROM example/app:1.0\nARG UID=0\nUSER ${UID}\n",
                encoding="utf-8",
            )
            (root / ".dockerignore").write_text(
                ".git\n.env\n.venv\n",
                encoding="utf-8",
            )

            findings = MODULE.audit_dockerfile(dockerfile, root)

        self.assertIn(
            "runtime.user-unresolved",
            {item.code for item in findings},
        )

    def test_dockerignore_must_exclude_sensitive_paths_after_negations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text(
                "FROM example/app:1.0\nUSER 10001:10001\n",
                encoding="utf-8",
            )
            ignore = root / ".dockerignore"
            for contents in ("", ".git\n.env\n.venv\n!.env\n"):
                with self.subTest(contents=contents):
                    ignore.write_text(contents, encoding="utf-8")
                    findings = MODULE.audit_dockerfile(dockerfile, root)
                    self.assertIn(
                        "context.ignore-incomplete",
                        {item.code for item in findings},
                    )

    def test_dockerignore_covers_actual_environment_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text(
                "FROM example/app:1.0\nUSER 10001:10001\n",
                encoding="utf-8",
            )
            (root / ".env.local").write_text("TOKEN=private\n", encoding="utf-8")
            (root / ".env.example").write_text(
                "TOKEN=placeholder\n",
                encoding="utf-8",
            )
            ignore = root / ".dockerignore"
            ignore.write_text(".git\n.env\n.venv\n", encoding="utf-8")

            findings = MODULE.audit_dockerfile(dockerfile, root)

            self.assertIn(
                "context.ignore-incomplete",
                {item.code for item in findings},
            )

            ignore.write_text(
                ".git\n.env\n.env.*\n!.env.example\n.venv\n",
                encoding="utf-8",
            )
            findings = MODULE.audit_dockerfile(dockerfile, root)

            self.assertNotIn(
                "context.ignore-incomplete",
                {item.code for item in findings},
            )


class ComposeAuditTest(unittest.TestCase):
    def test_dangerous_fields_and_latest_are_detected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compose = root / "compose.production.yaml"
            compose.write_text(
                """
services:
  app:
    image: example/app:latest
    privileged: true
    network_mode: host
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
""".lstrip(),
                encoding="utf-8",
            )

            codes = {
                finding.code for finding in MODULE.audit_compose_text(compose, root)
            }

            self.assertEqual(
                codes,
                {
                    "compose.docker-socket",
                    "compose.host-namespace",
                    "compose.latest",
                    "compose.privileged",
                    "compose.static-limited",
                },
            )

    def test_compose_config_uses_no_resolution_flags(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv: list[str], **_: object) -> object:
            calls.append(argv)
            if "--help" in argv:
                return COMMON.CommandResult(
                    argv,
                    0,
                    "--no-interpolate --no-env-resolution --no-path-resolution",
                    "",
                )
            return COMMON.CommandResult(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yaml"
            compose.write_text("services: {}\n", encoding="utf-8")
            with mock.patch.object(DOCKER, "run_command", side_effect=fake_run):
                finding = MODULE.validate_compose_with_docker(compose)

        self.assertIsNone(finding)
        config = calls[-1]
        self.assertIn("--env-file", config)
        self.assertIn("--no-interpolate", config)
        self.assertIn("--no-env-resolution", config)
        self.assertIn("--no-path-resolution", config)
        self.assertNotIn("config --format json", " ".join(config))

    def test_literal_secret_environment_value_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            compose = root / "compose.yaml"
            compose.write_text(
                """
services:
  app:
    image: example/app:1.0
    environment:
      API_TOKEN: committed-secret
      PASSWORD: ${PASSWORD:-unsafe-default}
      AUTH_TOKEN: ${AUTH_TOKEN:?required}
""".lstrip(),
                encoding="utf-8",
            )

            findings = MODULE.audit_compose_text(compose, root)

            self.assertEqual(
                [
                    finding.line
                    for finding in findings
                    if finding.code == "compose.secret-value"
                ],
                [5, 6],
            )

    def test_compose_host_file_references_skip_docker_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "compose.yaml"
            compose.write_text(
                "services:\n  app:\n    env_file: .env\n",
                encoding="utf-8",
            )
            with mock.patch.object(DOCKER, "run_command") as runner:
                finding = MODULE.validate_compose_with_docker(compose)

        self.assertEqual(finding.code, "compose.config-host-read-skipped")
        runner.assert_not_called()

    def test_complex_yaml_never_receives_a_static_pass(self) -> None:
        documents = (
            "services: {app: {image: x:1, privileged: true, "
            "environment: {API_TOKEN: committed-secret}}}\n",
            "x-service: &service\n  image: example/app:1.0\n"
            "services:\n  app:\n    <<: *service\n    command: >-\n"
            "      run --unsafe\n",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, document in enumerate(documents):
                with self.subTest(index=index):
                    compose = root / f"compose-{index}.yaml"
                    compose.write_text(document, encoding="utf-8")
                    findings = MODULE.audit_compose_text(compose, root)
                    self.assertEqual(MODULE._status(findings), "review")
                    self.assertIn(
                        "compose.static-limited",
                        {item.code for item in findings},
                    )


class ImageAndModeTest(unittest.TestCase):
    def test_image_inspection_redacts_environment_and_arguments(self) -> None:
        payload = [
            {
                "Id": "sha256:" + "a" * 64,
                "Architecture": "arm64",
                "Os": "linux",
                "Config": {
                    "User": "10001:10001",
                    "WorkingDir": "/app",
                    "Entrypoint": ["python", "--token", "secret-value"],
                    "Cmd": ["serve", "--password", "another-secret"],
                    "Env": ["API_TOKEN=secret-value", "LOG_LEVEL=info"],
                    "ExposedPorts": {"8000/tcp": {}},
                    "Labels": {"org.opencontainers.image.source": "private-value"},
                },
            }
        ]
        result = COMMON.CommandResult(
            ["docker", "image", "inspect", "image"],
            0,
            json.dumps(payload),
            "",
        )
        with mock.patch.object(DOCKER, "run_command", return_value=result):
            summary, finding = MODULE.inspect_local_image("image")

        self.assertIsNone(finding)
        self.assertEqual(summary["environment_names"], ["API_TOKEN", "LOG_LEVEL"])
        serialized = json.dumps(summary)
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("another-secret", serialized)
        self.assertNotIn("private-value", serialized)
        self.assertNotIn("entrypoint_executable", summary)
        self.assertNotIn("command_executable", summary)

    def test_build_requires_explicit_network_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.AuditError, "--allow-network"):
                MODULE.audit(arguments(Path(directory), build=True))

    def test_explicit_files_must_resolve_inside_root(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as outside_directory,
        ):
            root = Path(directory)
            outside = Path(outside_directory) / "Dockerfile"
            outside.write_text("FROM scratch\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.AuditError, "inside --path"):
                MODULE.audit(arguments(root, dockerfile=outside))

    def test_image_reference_cannot_be_an_option(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.AuditError, "OCI reference"):
                MODULE.audit(arguments(Path(directory), image="--format"))

    def test_platform_variant_compares_the_architecture_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(
                    MODULE,
                    "docker_capabilities",
                    return_value={
                        "docker_cli": True,
                        "daemon_available": True,
                        "buildx": False,
                        "compose": False,
                    },
                ),
                mock.patch.object(
                    MODULE,
                    "inspect_local_image",
                    return_value=(
                        {
                            "os": "linux",
                            "architecture": "arm",
                            "variant": "v7",
                            "user": "10001:10001",
                        },
                        None,
                    ),
                ),
            ):
                report = MODULE.audit(
                    arguments(
                        Path(directory),
                        image="example/app:1.0",
                        platform="linux/arm/v7",
                    )
                )

        self.assertNotIn(
            "platform.image-mismatch",
            {item["code"] for item in report["findings"]},
        )

    def test_existing_image_requires_exact_reference_and_numeric_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(
                    MODULE,
                    "docker_capabilities",
                    return_value={
                        "docker_cli": True,
                        "daemon_available": True,
                        "buildx": False,
                        "build_check": False,
                        "compose": False,
                    },
                ),
                mock.patch.object(
                    MODULE,
                    "inspect_local_image",
                    return_value=(
                        {
                            "os": "linux",
                            "architecture": "amd64",
                            "variant": None,
                            "user": "",
                        },
                        None,
                    ),
                ),
            ):
                report = MODULE.audit(
                    arguments(Path(directory), image="example/app:latest")
                )

        codes = {item["code"] for item in report["findings"]}
        self.assertIn("image.mutable-reference", codes)
        self.assertIn("image.user-unverified", codes)
        self.assertEqual(report["status"], "fail")

    def test_platform_rejects_wrong_os_and_malformed_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(
                    MODULE,
                    "docker_capabilities",
                    return_value={
                        "docker_cli": True,
                        "daemon_available": True,
                        "buildx": False,
                        "build_check": False,
                        "compose": False,
                    },
                ),
                mock.patch.object(
                    MODULE,
                    "inspect_local_image",
                    return_value=(
                        {
                            "os": "linux",
                            "architecture": "amd64",
                            "variant": None,
                            "user": "10001:10001",
                        },
                        None,
                    ),
                ),
            ):
                report = MODULE.audit(
                    arguments(
                        root,
                        image="example/app:1.0",
                        platform="windows/amd64",
                    )
                )
            self.assertIn(
                "platform.image-mismatch",
                {item["code"] for item in report["findings"]},
            )

            with (
                mock.patch.object(MODULE, "docker_capabilities") as capabilities,
                self.assertRaisesRegex(MODULE.AuditError, "os/arch"),
            ):
                MODULE.audit(
                    arguments(
                        root,
                        image="example/app:1.0",
                        platform="linux",
                    )
                )
            capabilities.assert_not_called()

    def test_build_and_existing_image_are_not_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(MODULE.AuditError, "omit --image"):
                MODULE.audit(
                    arguments(
                        Path(directory),
                        build=True,
                        allow_network=True,
                        image="example/app:1.0",
                    )
                )

    def test_default_audit_never_invokes_mutating_docker_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[list[str]] = []

            def fake_run(argv: list[str], **_: object) -> object:
                calls.append(argv)
                return COMMON.CommandResult(argv, 1, "", "")

            with mock.patch.object(DOCKER, "run_command", side_effect=fake_run):
                report = MODULE.audit(arguments(root))

            self.assertEqual(report["schema"], MODULE.SCHEMA)
            forbidden = {"build", "create", "run", "start", "rm", "rmi", "push"}
            self.assertFalse(
                [argv for argv in calls if any(part in forbidden for part in argv[1:3])]
            )

    def test_empty_and_bake_only_scopes_never_pass(self) -> None:
        capabilities = {
            "docker_cli": False,
            "daemon_available": False,
            "buildx": False,
            "build_check": False,
            "compose": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.object(
                MODULE,
                "docker_capabilities",
                return_value=capabilities,
            ):
                empty = MODULE.audit(arguments(root))
            self.assertEqual(empty["status"], "fail")
            self.assertIn(
                "scope.empty",
                {item["code"] for item in empty["findings"]},
            )

            (root / "docker-bake.hcl").write_text(
                'target "default" {}\n',
                encoding="utf-8",
            )
            with mock.patch.object(
                MODULE,
                "docker_capabilities",
                return_value=capabilities,
            ):
                bake = MODULE.audit(arguments(root))
            self.assertEqual(bake["status"], "review")
            self.assertIn(
                "bake.unvalidated",
                {item["code"] for item in bake["findings"]},
            )

    def test_discovery_reports_when_its_safety_bound_is_reached(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

            with mock.patch.object(SOURCE, "MAX_SCANNED_FILES", 1):
                files, truncated, findings = MODULE.discover_container_files(root)

            self.assertTrue(truncated)
            self.assertFalse(findings)
            self.assertEqual(sum(len(items) for items in files.values()), 1)

    def test_discovered_external_symlink_blocks_build_before_docker(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as outside_directory,
        ):
            root = Path(directory)
            outside = Path(outside_directory) / "Dockerfile"
            outside.write_text("FROM scratch\n", encoding="utf-8")
            (root / "Dockerfile").symlink_to(outside)

            with (
                mock.patch.object(MODULE, "docker_capabilities") as capabilities,
                self.assertRaisesRegex(MODULE.AuditError, "--dockerfile"),
            ):
                MODULE.audit(arguments(root, build=True, allow_network=True))

            capabilities.assert_not_called()

    def test_discovery_walk_errors_are_reported(self) -> None:
        def fake_walk(
            _: Path,
            *,
            followlinks: bool,
            onerror: object,
        ) -> list[object]:
            self.assertFalse(followlinks)
            onerror(PermissionError("denied"))
            return []

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(SOURCE.os, "walk", side_effect=fake_walk):
                _, truncated, findings = MODULE.discover_container_files(
                    Path(directory)
                )

        self.assertFalse(truncated)
        self.assertEqual([item.code for item in findings], ["discovery.walk-error"])

    def test_ambiguous_discovered_dockerfiles_block_before_docker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

            with (
                mock.patch.object(MODULE, "docker_capabilities") as capabilities,
                self.assertRaisesRegex(MODULE.AuditError, "exactly one"),
            ):
                MODULE.audit(arguments(root, build=True, allow_network=True))

            capabilities.assert_not_called()

    def test_multi_platform_rejects_local_image_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            for option in ("keep_image", "runtime_test", "supply_chain"):
                with (
                    self.subTest(option=option),
                    mock.patch.object(MODULE, "docker_capabilities") as capabilities,
                    self.assertRaisesRegex(MODULE.AuditError, "multi-platform"),
                ):
                    MODULE.audit(
                        arguments(
                            root,
                            build=True,
                            allow_network=True,
                            platform="linux/amd64,linux/arm64",
                            **{option: True},
                        )
                    )
                capabilities.assert_not_called()

    def test_failed_build_attempts_owned_tag_cleanup(self) -> None:
        responses = iter(
            (
                COMMON.CommandResult([], 124, "", ""),
                COMMON.CommandResult([], 0, TOKEN := "a" * 24 + "\n", ""),
                COMMON.CommandResult([], 0, "", ""),
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            with (
                mock.patch.object(
                    DOCKER.secrets, "token_hex", return_value=TOKEN.strip()
                ),
                mock.patch.object(
                    DOCKER, "run_command", side_effect=responses
                ) as runner,
            ):
                _, _, findings, evidence = MODULE.build_image(
                    root,
                    dockerfile,
                    None,
                    False,
                )

        self.assertIn("build.failed", {item.code for item in findings})
        self.assertTrue(evidence["cleanup_verified"])
        self.assertEqual(
            runner.call_args_list[-1].args[0][:3], ["docker", "image", "rm"]
        )

    def test_supply_chain_rejects_wrong_json_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".trivy.yaml").write_text("{}\n", encoding="utf-8")
            result = COMMON.CommandResult([], 0, "[]", "")
            with (
                mock.patch.object(SUPPLY.shutil, "which", return_value="/tool"),
                mock.patch.object(
                    SUPPLY,
                    "run_command",
                    return_value=result,
                ) as runner,
            ):
                findings, _ = MODULE.run_supply_chain(root, "example/app:1.0")

        self.assertIn(
            "supply-chain.trivy-invalid",
            {item.code for item in findings},
        )
        self.assertEqual(runner.call_args.kwargs["cwd"], root)

    def test_supply_chain_rejects_missing_required_result_arrays(self) -> None:
        cases = (
            (".syft.yaml", "supply-chain.syft-invalid"),
            (".trivy.yaml", "supply-chain.trivy-invalid"),
        )
        for config_name, expected_code in cases:
            with self.subTest(config_name=config_name):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / config_name).write_text("{}\n", encoding="utf-8")
                    result = COMMON.CommandResult([], 0, "{}", "")
                    with (
                        mock.patch.object(
                            SUPPLY.shutil,
                            "which",
                            return_value="/tool",
                        ),
                        mock.patch.object(
                            SUPPLY,
                            "run_command",
                            return_value=result,
                        ),
                    ):
                        findings, evidence = MODULE.run_supply_chain(
                            root,
                            "example/app:1.0",
                        )

                self.assertIn(
                    expected_code,
                    {item.code for item in findings},
                )
                self.assertFalse(evidence["sbom_generated"])
                self.assertFalse(evidence["vulnerability_assessed"])

    def test_supply_chain_never_infers_policy_from_counts_or_ignore_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".trivyignore").write_text("CVE-EXAMPLE\n", encoding="utf-8")
            with mock.patch.object(SUPPLY, "run_command") as runner:
                findings, _ = MODULE.run_supply_chain(root, "example/app:1.0")
            self.assertIn(
                "supply-chain.unconfigured",
                {item.code for item in findings},
            )
            runner.assert_not_called()

            (root / ".trivy.yaml").write_text("{}\n", encoding="utf-8")
            payload = {
                "Results": [
                    {
                        "Vulnerabilities": [
                            {"Severity": "CRITICAL"},
                        ]
                    }
                ]
            }
            result = COMMON.CommandResult([], 0, json.dumps(payload), "")
            with (
                mock.patch.object(SUPPLY.shutil, "which", return_value="/tool"),
                mock.patch.object(SUPPLY, "run_command", return_value=result),
            ):
                findings, evidence = MODULE.run_supply_chain(
                    root,
                    "example/app:1.0",
                )
            self.assertEqual(
                evidence["tools"]["trivy"]["vulnerability_counts"],
                {"CRITICAL": 1},
            )
            self.assertIn(
                "supply-chain.vulnerability-policy-unverified",
                {item.code for item in findings},
            )

    def test_build_check_support_and_warnings_are_reported(self) -> None:
        responses = iter(
            (
                COMMON.CommandResult([], 0, '{"Client":{},"Server":{}}', ""),
                COMMON.CommandResult([], 0, "buildx version", ""),
                COMMON.CommandResult([], 0, "Usage: build --check", ""),
                COMMON.CommandResult([], 0, "v2", ""),
            )
        )
        with (
            mock.patch.object(DOCKER.shutil, "which", return_value="/docker"),
            mock.patch.object(DOCKER, "run_command", side_effect=responses),
        ):
            capabilities = MODULE.docker_capabilities()
        self.assertTrue(capabilities["build_check"])

        unsupported = iter(
            (
                COMMON.CommandResult([], 0, '{"Client":{},"Server":{}}', ""),
                COMMON.CommandResult([], 1, "", ""),
                COMMON.CommandResult([], 0, "v2", ""),
            )
        )
        with (
            mock.patch.object(DOCKER.shutil, "which", return_value="/docker"),
            mock.patch.object(DOCKER, "run_command", side_effect=unsupported),
        ):
            capabilities = MODULE.docker_capabilities()
        self.assertFalse(capabilities["build_check"])

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dockerfile = root / "Dockerfile"
            dockerfile.write_text("FROM scratch\n", encoding="utf-8")
            warning = COMMON.CommandResult([], 0, "", "WARNING: JSONArgsRecommended")
            with mock.patch.object(DOCKER, "run_command", return_value=warning):
                findings, evidence = MODULE.run_build_check(root, dockerfile, [])
        self.assertTrue(evidence["warning_detected"])
        self.assertIn("build.check-warning", {item.code for item in findings})

    def test_retained_build_cleanup_runs_when_followup_check_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            with (
                mock.patch.object(
                    MODULE,
                    "docker_capabilities",
                    return_value={
                        "docker_cli": True,
                        "daemon_available": True,
                        "buildx": True,
                        "compose": False,
                    },
                ),
                mock.patch.object(
                    MODULE,
                    "build_image",
                    return_value=("audit:image", "token", [], {}),
                ),
                mock.patch.object(
                    MODULE,
                    "inspect_local_image",
                    return_value=({"os": "linux", "architecture": "amd64"}, None),
                ),
                mock.patch.object(
                    MODULE,
                    "run_supply_chain",
                    side_effect=RuntimeError("scanner failed unexpectedly"),
                ),
                mock.patch.object(
                    MODULE,
                    "_cleanup_owned_image",
                    return_value=True,
                ) as cleanup,
                self.assertRaisesRegex(RuntimeError, "unexpectedly"),
            ):
                MODULE.audit(
                    arguments(
                        root,
                        build=True,
                        allow_network=True,
                        supply_chain=True,
                    )
                )

        cleanup.assert_called_once_with("audit:image", "token")

    def test_cleanup_requires_matching_label(self) -> None:
        mismatch = COMMON.CommandResult([], 0, "different-token\n", "")
        with mock.patch.object(DOCKER, "run_command", return_value=mismatch) as runner:
            self.assertFalse(MODULE._cleanup_owned_image("image:test", "expected"))
        self.assertEqual(runner.call_count, 1)


if __name__ == "__main__":
    unittest.main()
