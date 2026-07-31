#!/usr/bin/env python3
"""Tests for the deterministic frontend candidate producer."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


SCRIPT_PATH = Path(__file__).with_name("frontend_project.py")
SPEC = importlib.util.spec_from_file_location("frontend_project", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {SCRIPT_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FrontendProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _request(
        self,
        *,
        routing: bool = False,
        lint: bool = False,
        formatting: bool = False,
        variables: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        versions = {
            "@testing-library/jest-dom": "6.6.3",
            "@testing-library/react": "16.3.0",
            "@types/node": "22.15.3",
            "@types/react": "19.1.2",
            "@types/react-dom": "19.1.2",
            "@vitejs/plugin-react": "4.4.1",
            "jsdom": "26.1.0",
            "react": "19.1.0",
            "react-dom": "19.1.0",
            "typescript": "5.8.3",
            "vite": "6.3.4",
            "vitest": "3.1.2",
        }
        paths = set(MODULE.BASE_PATHS)
        if routing:
            versions["react-router"] = "7.6.2"
            paths.add("src/router.tsx")
        if lint:
            versions["oxlint"] = "0.16.10"
            paths.add(".oxlintrc.json")
        if formatting:
            versions["prettier"] = "3.5.3"
            paths.update({".prettierignore", ".prettierrc.json"})
        return {
            "schema_version": 1,
            "candidate_set_id": "frontend-web",
            "profile": "react-vite",
            "component_id": "web",
            "materialization_unit_id": "web",
            "component_root": "apps/web",
            "assigned_paths": [f"apps/web/{path}" for path in sorted(paths)],
            "excluded_paths": ["apps/api"],
            "package": {
                "name": "@example/web",
                "display_name": "Example <Web>",
                "manager": "pnpm",
                "manager_version": "10.10.0",
                "node_range": ">=22.0.0 <23.0.0",
            },
            "versions": versions,
            "capabilities": {
                "routing": {
                    "profile": "react-router" if routing else "none",
                    "version": "7.6.2" if routing else None,
                },
                "styling": "plain-css",
                "testing": "vitest",
                "public_environment": {
                    "variables": variables
                    if variables is not None
                    else [
                        {"name": "VITE_API_ORIGIN", "required": True},
                        {"name": "VITE_SUPPORT_URL", "required": False},
                    ]
                },
                "lint": {
                    "profile": "oxlint" if lint else "none",
                    "version": "0.16.10" if lint else None,
                },
                "format": {
                    "profile": "prettier" if formatting else "none",
                    "version": "3.5.3" if formatting else None,
                },
            },
        }

    def _write_request(
        self, request: dict[str, Any], name: str = "request.json"
    ) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps(request, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def _render(
        self,
        request: dict[str, Any],
        output_name: str = "candidates",
    ) -> tuple[dict[str, Any], Path]:
        output = self.root / output_name
        result = MODULE.render_candidates(self._write_request(request), output)
        return result, output

    def test_render_is_byte_for_byte_deterministic(self) -> None:
        request = self._request()
        first, first_output = self._render(request, "first")
        second, second_output = self._render(request, "second")

        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])
        self.assertEqual(
            (first_output / "manifest.json").read_bytes(),
            (second_output / "manifest.json").read_bytes(),
        )
        first_manifest = json.loads(
            (first_output / "manifest.json").read_text(encoding="utf-8")
        )
        for entry in first_manifest["files"]:
            first_file = first_output / entry["candidate"]
            second_file = second_output / entry["candidate"]
            self.assertEqual(first_file.read_bytes(), second_file.read_bytes())
            self.assertEqual(stat.S_IMODE(first_file.stat().st_mode), 0o600)
        self.assertEqual(
            [item["path"] for item in first_manifest["files"]],
            sorted(request["assigned_paths"]),
        )
        self.assertEqual(
            first_manifest["inputs"], MODULE._normalized_request(request)[0]
        )
        self.assertEqual(
            first_manifest["input_sha256"],
            MODULE._sha256_bytes(MODULE._canonical_bytes(first_manifest["inputs"])),
        )

    def test_generated_typescript_config_includes_vite_client_types(self) -> None:
        _, output = self._render(self._request(), "vite-types")
        tsconfig = json.loads(
            (output / "files/tsconfig.app.json").read_text(encoding="utf-8")
        )

        self.assertIn("vite/client", tsconfig["compilerOptions"]["types"])

    def test_markdown_display_name_is_html_safe(self) -> None:
        _, output = self._render(self._request(), "markdown-safe")
        readme = (output / "files/README.md").read_text(encoding="utf-8")

        self.assertIn("# Example &lt;Web&gt;", readme)
        self.assertNotIn("<Web", readme)

    def test_assigned_and_excluded_paths_are_closed_and_root_relative(self) -> None:
        missing = self._request()
        missing["assigned_paths"].remove("apps/web/package.json")
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "exactly match"):
            self._render(missing, "missing")

        extra = self._request()
        extra["assigned_paths"].append("package.json")
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "escape"):
            self._render(extra, "outside")

        excluded = self._request()
        excluded["excluded_paths"].append("apps/web/package.json")
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "overlap"):
            self._render(excluded, "excluded")

        excluded_parent = self._request()
        excluded_parent["excluded_paths"].append("apps/web/src")
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "overlap"):
            self._render(excluded_parent, "excluded-parent")

    def test_json_context_and_candidate_identity_are_fail_closed(self) -> None:
        injected = self._request()
        injected["package"]["node_range"] = (
            '>=22","scripts":{"postinstall":"unsafe"},"unexpected":"'
        )
        with self.assertRaisesRegex(
            MODULE.FrontendProjectError,
            "supported Node version range",
        ):
            self._render(injected, "node-injection")

        unsafe_identity = self._request()
        unsafe_identity["candidate_set_id"] = "../frontend-web"
        with self.assertRaisesRegex(
            MODULE.FrontendProjectError,
            "safe identifier",
        ):
            self._render(unsafe_identity, "unsafe-identity")

    def test_external_json_rejects_duplicate_object_keys(self) -> None:
        request_path = self.root / "duplicate-request.json"
        request_path.write_text(
            '{"schema_version": 1, "schema_version": 1}\n',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            MODULE.FrontendProjectError,
            "duplicate object key",
        ):
            MODULE.render_candidates(request_path, self.root / "duplicate-output")

    def test_external_json_rejects_non_standard_numeric_literals(self) -> None:
        request = self._request()
        request["package"]["manager_version"] = float("nan")
        request_path = self._write_request(request, "non-finite-request.json")

        with self.assertRaisesRegex(
            MODULE.FrontendProjectError,
            "non-standard numeric literal",
        ):
            MODULE.render_candidates(request_path, self.root / "non-finite-output")

    def test_candidate_manifest_requires_private_root_and_manifest(self) -> None:
        _, output = self._render(self._request(), "private-contract")
        manifest_path = output / "manifest.json"

        os.chmod(manifest_path, 0o644)
        with self.assertRaisesRegex(
            MODULE.FrontendProjectError,
            "manifest.*0600",
        ):
            MODULE.validate_candidate_manifest(manifest_path)

        os.chmod(manifest_path, 0o600)
        os.chmod(output, 0o755)
        with self.assertRaisesRegex(
            MODULE.FrontendProjectError,
            "root.*0700",
        ):
            MODULE.validate_candidate_manifest(manifest_path)

    def test_public_env_emits_names_only_and_rejects_invalid_names(self) -> None:
        request = self._request()
        _, output = self._render(request)
        env_example = (output / "files/.env.example").read_text(encoding="utf-8")
        self.assertEqual(
            env_example,
            "VITE_API_ORIGIN=\nVITE_SUPPORT_URL=\n",
        )
        env_module = (output / "files/src/env.ts").read_text(encoding="utf-8")
        self.assertIn("VITE_API_ORIGIN", env_module)
        self.assertIn("Missing required public environment variable", env_module)
        self.assertNotIn("https://", env_example)

        invalid = self._request(
            variables=[{"name": "DATABASE_PASSWORD", "required": True}]
        )
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "VITE_ prefix"):
            self._render(invalid, "invalid-prefix")

        secret_like = self._request(
            variables=[{"name": "VITE_ACCESS_TOKEN", "required": True}]
        )
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "secret-like"):
            self._render(secret_like, "secret-like")

        api_key = self._request(variables=[{"name": "VITE_API_KEY", "required": True}])
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "secret-like"):
            self._render(api_key, "api-key")

        access_key = self._request(
            variables=[{"name": "VITE_ACCESS_KEY_ID", "required": True}]
        )
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "secret-like"):
            self._render(access_key, "access-key")

        for compact_name in ("VITE_APIKEY", "VITE_ACCESSKEY"):
            with self.subTest(compact_name=compact_name):
                compact_key = self._request(
                    variables=[{"name": compact_name, "required": True}]
                )
                with self.assertRaisesRegex(
                    MODULE.FrontendProjectError,
                    "secret-like",
                ):
                    self._render(compact_key, compact_name.lower())

        value_bearing = self._request()
        value_bearing["capabilities"]["public_environment"]["variables"][0]["value"] = (
            "forbidden"
        )
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "unknown fields"):
            self._render(value_bearing, "value-bearing")

    def test_optional_routing_lint_and_format_outputs_are_selection_bound(
        self,
    ) -> None:
        _, base_output = self._render(self._request(), "base")
        base_package = json.loads(
            (base_output / "files/package.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("lint", base_package["scripts"])
        self.assertNotIn("format", base_package["scripts"])
        self.assertNotIn("oxlint", base_package["devDependencies"])
        self.assertNotIn("prettier", base_package["devDependencies"])
        self.assertFalse((base_output / "files/src/router.tsx").exists())

        selected = self._request(routing=True, lint=True, formatting=True)
        _, selected_output = self._render(selected, "selected")
        selected_package = json.loads(
            (selected_output / "files/package.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            selected_package["dependencies"]["react-router"],
            "7.6.2",
        )
        self.assertEqual(selected_package["scripts"]["lint"], "oxlint")
        self.assertEqual(
            selected_package["scripts"]["format"],
            "prettier . --check",
        )
        self.assertTrue((selected_output / "files/src/router.tsx").is_file())
        self.assertTrue((selected_output / "files/.oxlintrc.json").is_file())
        self.assertTrue((selected_output / "files/.prettierrc.json").is_file())

    def test_render_and_validate_cli_round_trip_and_tamper_rejection(self) -> None:
        request_path = self._write_request(self._request())
        output = self.root / "cli-output"
        rendered = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "render",
                "--request",
                str(request_path),
                "--output",
                str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        result = json.loads(rendered.stdout)
        self.assertTrue(result["ok"])

        validated = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "validate",
                "--manifest",
                str(output / "manifest.json"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(json.loads(validated.stdout)["ok"])

        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        candidate = output / manifest["files"][0]["candidate"]
        os.chmod(candidate, 0o600)
        candidate.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "digest mismatch"):
            MODULE.validate_candidate_manifest(output / "manifest.json")

        _, provenance_output = self._render(self._request(), "provenance-tamper")
        provenance_manifest_path = provenance_output / "manifest.json"
        provenance_manifest = json.loads(
            provenance_manifest_path.read_text(encoding="utf-8")
        )
        provenance_manifest["inputs"]["package"]["display_name"] = "Tampered"
        provenance_manifest_path.write_text(
            json.dumps(
                provenance_manifest,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(provenance_manifest_path, 0o600)
        with self.assertRaisesRegex(
            MODULE.FrontendProjectError,
            "input_sha256 does not match",
        ):
            MODULE.validate_candidate_manifest(provenance_manifest_path)

    def test_unsupported_profile_and_floating_versions_fail_closed(self) -> None:
        unsupported = self._request()
        unsupported["profile"] = "nextjs"
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "unsupported"):
            self._render(unsupported, "unsupported")

        unsupported_manager = self._request()
        unsupported_manager["package"]["manager"] = "curl"
        with self.assertRaisesRegex(
            MODULE.FrontendProjectError,
            "supported package manager",
        ):
            self._render(unsupported_manager, "unsupported-manager")

        floating = copy.deepcopy(self._request())
        floating["versions"]["vite"] = "^6.3.4"
        with self.assertRaisesRegex(MODULE.FrontendProjectError, "exact"):
            self._render(floating, "floating")

        for index, invalid_version in enumerate(
            ("beta", "19.x", "https://example.invalid/react.tgz", "npm:react@19.1.0")
        ):
            with self.subTest(invalid_version=invalid_version):
                non_exact = self._request()
                non_exact["versions"]["react"] = invalid_version
                with self.assertRaisesRegex(
                    MODULE.FrontendProjectError,
                    "exact registry version",
                ):
                    self._render(non_exact, f"non-exact-{index}")


if __name__ == "__main__":
    unittest.main()
