#!/usr/bin/env python3
"""Tests for typed container rendering and Compose policy validation."""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("render_container_asset.py")
SPEC = importlib.util.spec_from_file_location("render_container_asset", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

DIGEST = "a" * 64


def python_values() -> dict[str, object]:
    return {
        "build_image": f"python:3.13-slim@sha256:{DIGEST}",
        "runtime_image": f"python:3.13-slim@sha256:{DIGEST}",
        "package_source_paths": ["pyproject.toml", "README.md", "src"],
        "lockfile": "requirements.lock",
        "distribution_name": "sample-service",
        "project_version": "1.2.3",
        "runtime_uid": 10001,
        "runtime_gid": 10001,
        "runtime_port": 8000,
        "runtime_command": ["python", "-m", "sample"],
    }


def react_values() -> dict[str, object]:
    return {
        "build_image": f"node:22-alpine@sha256:{DIGEST}",
        "runtime_image": f"example/static:1.0@sha256:{DIGEST}",
        "package_manager": "pnpm",
        "lockfile": "pnpm-lock.yaml",
        "build_output_path": "dist",
        "static_root": "/srv",
        "runtime_uid": 10001,
        "runtime_port": 8080,
        "runtime_command": ["static-server", "/srv"],
    }


class DockerfileRendererTest(unittest.TestCase):
    def test_python_render_consumes_hash_lock_and_preserves_version_identity(
        self,
    ) -> None:
        rendered = MODULE.render_python_dockerfile(python_values())

        self.assertIn("--require-hashes", rendered)
        self.assertIn("--no-build-isolation --no-deps", rendered)
        self.assertIn(
            "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAMPLE_SERVICE=1.2.3",
            rendered,
        )
        self.assertIn('"sample-service==1.2.3"', rendered)
        self.assertNotIn("ARG PROJECT_VERSION", rendered)
        self.assertNotIn("0+unknown", rendered)
        self.assertIn("USER 10001:10001", rendered)
        self.assertIn('COPY ["pyproject.toml","./pyproject.toml"]', rendered)
        self.assertIn('COPY ["README.md","./README.md"]', rendered)
        self.assertIn('COPY ["src","./src"]', rendered)
        self.assertNotIn(
            'COPY ["pyproject.toml","README.md","src","./"]',
            rendered,
        )

    def test_python_render_rejects_instruction_and_path_injection(self) -> None:
        cases = (
            ("build_image", f"python:3.13-slim@sha256:{DIGEST}\nRUN id"),
            ("lockfile", "requirements.lock\nRUN id"),
            ("project_version", "1.2.3\nRUN id"),
            ("runtime_uid", "10001\nRUN id"),
            ("runtime_command", ["python", "-m", "sample\nRUN id"]),
        )
        for field, value in cases:
            with self.subTest(field=field):
                values = python_values()
                values[field] = value
                with self.assertRaises(MODULE.ContainerAssetError):
                    MODULE.render_python_dockerfile(values)

    def test_json_input_rejects_duplicate_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "values.json"
            path.write_text('{"runtime_uid": 1, "runtime_uid": 2}', encoding="utf-8")
            with self.assertRaisesRegex(MODULE.ContainerAssetError, "duplicate JSON"):
                MODULE._load_json(path)

    def test_python_render_rejects_floating_and_fallback_identity(self) -> None:
        values = python_values()
        values["build_image"] = "python:latest"
        with self.assertRaisesRegex(MODULE.ContainerAssetError, "latest"):
            MODULE.render_python_dockerfile(values)

        values = python_values()
        values["project_version"] = "0+unknown"
        with self.assertRaisesRegex(MODULE.ContainerAssetError, "non-fallback"):
            MODULE.render_python_dockerfile(values)

        for version in ("1..2", "1+", "1_", "01.2.3", "1.2"):
            with self.subTest(version=version):
                values = python_values()
                values["project_version"] = version
                with self.assertRaisesRegex(MODULE.ContainerAssetError, "SemVer"):
                    MODULE.render_python_dockerfile(values)

    def test_python_render_preserves_nested_lockfile_path(self) -> None:
        values = python_values()
        values["lockfile"] = "requirements/production.lock"

        rendered = MODULE.render_python_dockerfile(values)

        self.assertIn(
            'COPY ["requirements/production.lock","./requirements/production.lock"]',
            rendered,
        )
        self.assertIn("--requirement requirements/production.lock", rendered)

    def test_image_reference_rejects_malformed_registry_ports(self) -> None:
        for reference in (
            "registry:123x/team/image:1",
            "registry:0/team/image:1",
            "registry:65536/team/image:1",
        ):
            with self.subTest(reference=reference):
                values = python_values()
                values["build_image"] = reference
                with self.assertRaisesRegex(
                    MODULE.ContainerAssetError, "registry port"
                ):
                    MODULE.render_python_dockerfile(values)

    def test_react_render_uses_bounded_package_manager_recipes(self) -> None:
        rendered = MODULE.render_react_vite_dockerfile(react_values())

        self.assertIn("corepack enable && pnpm install --frozen-lockfile", rendered)
        self.assertIn("pnpm run build", rendered)
        self.assertEqual(rendered.count("FROM "), 2)
        self.assertIn("USER 10001", rendered)

    def test_react_render_rejects_free_form_commands_and_injection(self) -> None:
        values = react_values()
        values["package_manager"] = "pnpm && curl example.invalid"
        with self.assertRaisesRegex(MODULE.ContainerAssetError, "package_manager"):
            MODULE.render_react_vite_dockerfile(values)

        values = react_values()
        values["build_output_path"] = "dist\nRUN id"
        with self.assertRaises(MODULE.ContainerAssetError):
            MODULE.render_react_vite_dockerfile(values)

    def test_react_render_binds_manager_to_lockfile(self) -> None:
        for manager, lockfile in (
            ("npm", "pnpm-lock.yaml"),
            ("pnpm", "yarn.lock"),
            ("yarn", "package-lock.json"),
        ):
            with self.subTest(manager=manager, lockfile=lockfile):
                values = react_values()
                values["package_manager"] = manager
                values["lockfile"] = lockfile
                with self.assertRaisesRegex(MODULE.ContainerAssetError, "lockfile"):
                    MODULE.render_react_vite_dockerfile(values)


class ComposeValidatorTest(unittest.TestCase):
    def model(self) -> dict[str, object]:
        return {
            "name": "sample",
            "services": {
                "database": {
                    "image": f"postgres:17@sha256:{DIGEST}",
                    "environment": {
                        "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD:?required}"
                    },
                    "healthcheck": {"test": ["CMD-SHELL", "pg_isready -U postgres"]},
                    "volumes": ["database-data:/var/lib/postgresql/data"],
                },
                "app": {
                    "build": {"context": "./apps/backend"},
                    "command": ["python", "-m", "sample"],
                    "depends_on": {"database": {"condition": "service_healthy"}},
                },
            },
            "volumes": {"database-data": {}},
        }

    def test_approved_local_profile_passes(self) -> None:
        MODULE.validate_compose_model(self.model())

    def test_privilege_host_and_socket_access_fail(self) -> None:
        mutations = (
            ("privileged", True),
            ("network_mode", "host"),
            ("cap_add", ["SYS_ADMIN"]),
            ("devices", ["/dev/kvm:/dev/kvm"]),
            ("volumes", ["/var/run/docker.sock:/var/run/docker.sock"]),
            ("volumes", ["/tmp/data:/data"]),
            ("volumes", ["${PROJECT_ROOT}:/data"]),
            ("volumes", [r"C:\data:/data"]),
            ("security_opt", ["apparmor=unconfined"]),
            ("use_api_socket", True),
            (
                "post_start",
                [{"command": ["sh", "-c", "id"], "privileged": True}],
            ),
            ("user", "${UID:-0}"),
            ("ports", ["8080:8000"]),
            ("command", ["sh", "-c", "echo ${API_TOKEN}"]),
            (
                "volumes",
                [
                    {
                        "type": "bind",
                        "source": "./data",
                        "target": "/data",
                        "bind": {"propagation": "rshared"},
                    }
                ],
            ),
            ("volumes", [".:/workspace:Z"]),
            ("volumes", ["./data:/data:rshared"]),
            ("volumes", ["cache:${TARGET_PATH}"]),
            ("volumes", ["undeclared:/data"]),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                model = self.model()
                model["services"]["app"][field] = value
                with self.assertRaises(MODULE.ContainerAssetError):
                    MODULE.validate_compose_model(model)

    def test_floating_image_literal_secret_and_invalid_health_dependency_fail(
        self,
    ) -> None:
        model = self.model()
        model["services"]["database"]["image"] = "postgres:latest"
        with self.assertRaisesRegex(MODULE.ContainerAssetError, "latest"):
            MODULE.validate_compose_model(model)

        model = self.model()
        model["services"]["database"]["environment"]["POSTGRES_PASSWORD"] = "secret"
        with self.assertRaisesRegex(MODULE.ContainerAssetError, "external"):
            MODULE.validate_compose_model(model)

        model = self.model()
        del model["services"]["database"]["healthcheck"]
        with self.assertRaisesRegex(
            MODULE.ContainerAssetError, "without a healthcheck"
        ):
            MODULE.validate_compose_model(model)

    def test_sensitive_interpolation_rejects_literal_defaults(self) -> None:
        for environment in (
            {"API_TOKEN": "${API_TOKEN:-embedded-secret}"},
            ["API_TOKEN=${API_TOKEN-default-secret}"],
            {"DB_PASS": "secret"},
            ["DATABASE_URL=postgres://alice:hunter2@db/app"],
        ):
            with self.subTest(environment=environment):
                model = self.model()
                model["services"]["app"]["environment"] = environment
                with self.assertRaisesRegex(MODULE.ContainerAssetError, "external"):
                    MODULE.validate_compose_model(model)

        for value in (
            "${API_TOKEN}",
            "${API_TOKEN:?required}",
            "${API_TOKEN?required}",
        ):
            with self.subTest(value=value):
                model = self.model()
                model["services"]["app"]["environment"] = {"API_TOKEN": value}
                MODULE.validate_compose_model(model)

    def test_healthcheck_mode_and_dependency_must_be_usable(self) -> None:
        for test in (
            ["NONE"],
            ["NOT-A-COMPOSE-HEALTHCHECK", "true"],
        ):
            with self.subTest(test=test):
                model = self.model()
                model["services"]["database"]["healthcheck"]["test"] = test
                with self.assertRaises(MODULE.ContainerAssetError):
                    MODULE.validate_compose_model(model)

    def test_optional_collections_and_health_durations_are_typed(self) -> None:
        for field, value in (
            ("security_opt", {}),
            ("security_opt", None),
            ("security_opt", [None]),
            ("volumes", {}),
            ("volumes", None),
            ("volumes", "database-data:/data"),
        ):
            with self.subTest(field=field, value=value):
                model = self.model()
                model["services"]["app"][field] = value
                with self.assertRaises(MODULE.ContainerAssetError):
                    MODULE.validate_compose_model(model)

        for duration in ("not-a-duration", 10, "", "1d", "10seconds", "0s"):
            with self.subTest(duration=duration):
                model = self.model()
                model["services"]["database"]["healthcheck"]["interval"] = duration
                with self.assertRaises(MODULE.ContainerAssetError):
                    MODULE.validate_compose_model(model)

        model = self.model()
        model["services"]["database"]["healthcheck"]["interval"] = "1h5m30s20ms"
        MODULE.validate_compose_model(model)

    def test_service_requires_source_and_rejects_unknown_fields(self) -> None:
        model = {"services": {"app": {}}}
        with self.assertRaisesRegex(MODULE.ContainerAssetError, "image or build"):
            MODULE.validate_compose_model(model)

        model = self.model()
        model["services"]["app"]["future_host_control"] = True
        with self.assertRaisesRegex(MODULE.ContainerAssetError, "unsupported"):
            MODULE.validate_compose_model(model)

    def test_service_may_bind_exact_image_name_to_local_build(self) -> None:
        model = self.model()
        model["services"]["app"]["image"] = f"sample:local@sha256:{DIGEST}"
        model["services"]["app"]["user"] = "10001:10001"
        model["services"]["app"]["ports"] = ["127.0.0.1:8000:8000"]
        MODULE.validate_compose_model(model)

    def test_dependency_graph_rejects_self_reference_and_cycles(self) -> None:
        model = self.model()
        model["services"]["database"]["depends_on"] = {
            "app": {"condition": "service_started"}
        }
        with self.assertRaisesRegex(MODULE.ContainerAssetError, "acyclic"):
            MODULE.validate_compose_model(model)

        model = self.model()
        model["services"]["app"]["depends_on"] = {
            "app": {"condition": "service_started"}
        }
        with self.assertRaisesRegex(MODULE.ContainerAssetError, "acyclic"):
            MODULE.validate_compose_model(model)


if __name__ == "__main__":
    unittest.main()
