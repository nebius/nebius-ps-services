#!/usr/bin/env python3
"""Offline contract tests for frontend-project and container assets."""

from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[2]
PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+\}\}")
CONTAINER_RENDERER_PATH = SKILLS_ROOT / "container/scripts/render_container_asset.py"
CONTAINER_RENDERER_SPEC = importlib.util.spec_from_file_location(
    "container_asset_renderer",
    CONTAINER_RENDERER_PATH,
)
assert (
    CONTAINER_RENDERER_SPEC is not None and CONTAINER_RENDERER_SPEC.loader is not None
)
CONTAINER_RENDERER = importlib.util.module_from_spec(CONTAINER_RENDERER_SPEC)
CONTAINER_RENDERER_SPEC.loader.exec_module(CONTAINER_RENDERER)
EXAMPLE_DIGEST = "a" * 64


def render(template: Path, values: dict[str, str]) -> str:
    content = template.read_text(encoding="utf-8")
    for key, value in values.items():
        content = content.replace(f"{{{{{key}}}}}", value)
    remaining = PLACEHOLDER.findall(content)
    if remaining:
        raise AssertionError(f"unrendered placeholders in {template}: {remaining}")
    return content


class FrontendTemplateTest(unittest.TestCase):
    def test_package_manifest_is_complete_and_parseable(self) -> None:
        values = {
            "PACKAGE_NAME": "@example/web",
            "PACKAGE_MANAGER": "pnpm",
            "PACKAGE_MANAGER_VERSION": "10.0.0",
            "NODE_RANGE_JSON": json.dumps(">=22.12.0"),
            "REACT_VERSION": "19.0.0",
            "REACT_DOM_VERSION": "19.0.0",
            "TESTING_LIBRARY_JEST_DOM_VERSION": "6.0.0",
            "TESTING_LIBRARY_REACT_VERSION": "16.0.0",
            "NODE_TYPES_VERSION": "24.0.0",
            "REACT_TYPES_VERSION": "19.0.0",
            "REACT_DOM_TYPES_VERSION": "19.0.0",
            "VITE_REACT_PLUGIN_VERSION": "6.0.0",
            "JSDOM_VERSION": "28.0.0",
            "TYPESCRIPT_VERSION": "6.0.0",
            "VITE_VERSION": "8.0.0",
            "VITEST_VERSION": "4.0.0",
        }
        package_path = (
            SKILLS_ROOT / "frontend-project/assets/react-vite/package.json.template"
        )
        package = json.loads(render(package_path, values))
        self.assertEqual(package["packageManager"], "pnpm@10.0.0")
        self.assertEqual(
            set(package["scripts"]),
            {"build", "dev", "preview", "test", "test:watch", "typecheck"},
        )
        self.assertIn("react", package["dependencies"])
        self.assertIn("vitest", package["devDependencies"])
        self.assertIn("@types/node", package["devDependencies"])
        self.assertNotIn("oxlint", package["devDependencies"])

    def test_static_json_templates_parse(self) -> None:
        template_root = SKILLS_ROOT / "frontend-project/assets/react-vite"
        for path in sorted(template_root.glob("tsconfig*.json.template")):
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))

    def test_react_entry_uses_client_create_root(self) -> None:
        main = (
            SKILLS_ROOT / "frontend-project/assets/react-vite/src/main.tsx.template"
        ).read_text(encoding="utf-8")
        self.assertIn('from "react-dom/client"', main)
        self.assertIn("createRoot(rootElement).render", main)

    def test_display_name_is_context_escaped_and_placeholders_fail_closed(self) -> None:
        root = SKILLS_ROOT / "frontend-project/assets/react-vite"
        display_name = 'Telemetry <"Δ"> & #1'
        values = {
            "DISPLAY_NAME_MARKDOWN": r'Telemetry &lt;"Δ"&gt; &amp; \#1',
            "DISPLAY_NAME_HTML": 'Telemetry &lt;"Δ"&gt; &amp; #1',
            "DISPLAY_NAME_JSON": json.dumps(display_name, ensure_ascii=False),
            "PACKAGE_MANAGER": "pnpm",
            "COMMANDS": "pnpm run dev\npnpm run typecheck",
        }
        readme = render(root / "README.md.template", values)
        html = render(root / "index.html.template", values)
        component = render(root / "src/App.tsx.template", values)
        test = render(root / "src/App.test.tsx.template", values)

        self.assertIn(values["DISPLAY_NAME_MARKDOWN"], readme)
        self.assertNotIn("<", readme.splitlines()[0])
        self.assertIn(values["DISPLAY_NAME_HTML"], html)
        self.assertNotIn(display_name, html)
        self.assertIn(f"const displayName = {values['DISPLAY_NAME_JSON']};", component)
        self.assertIn(f"name: {values['DISPLAY_NAME_JSON']}", test)
        with self.assertRaisesRegex(AssertionError, "unrendered placeholders"):
            render(root / "src/App.tsx.template", {})


class ContainerTemplateTest(unittest.TestCase):
    def test_python_dockerfile_contract(self) -> None:
        rendered = CONTAINER_RENDERER.render_python_dockerfile(
            {
                "build_image": f"python:3.13-slim@sha256:{EXAMPLE_DIGEST}",
                "runtime_image": f"python:3.13-slim@sha256:{EXAMPLE_DIGEST}",
                "package_source_paths": ["pyproject.toml", "README.md", "src"],
                "lockfile": "requirements.lock",
                "distribution_name": "sample",
                "project_version": "1.2.3",
                "runtime_uid": 10001,
                "runtime_gid": 10001,
                "runtime_port": 8000,
                "runtime_command": ["python", "-m", "sample"],
            },
        )
        self.assertIn(" AS build", rendered)
        self.assertIn(" AS runtime", rendered)
        self.assertIn("--require-hashes", rendered)
        self.assertIn("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_SAMPLE", rendered)
        self.assertIn("USER 10001:10001", rendered)
        self.assertNotIn("docker.sock", rendered)

    def test_react_dockerfile_requires_build_and_runtime_stages(self) -> None:
        rendered = CONTAINER_RENDERER.render_react_vite_dockerfile(
            {
                "build_image": f"node:22-alpine@sha256:{EXAMPLE_DIGEST}",
                "runtime_image": f"example/static:1.0@sha256:{EXAMPLE_DIGEST}",
                "package_manager": "pnpm",
                "lockfile": "pnpm-lock.yaml",
                "build_output_path": "dist",
                "static_root": "/srv",
                "runtime_uid": 10001,
                "runtime_port": 8080,
                "runtime_command": ["static-server", "/srv"],
            },
        )
        self.assertEqual(rendered.count("FROM "), 2)
        self.assertIn("COPY --from=build", rendered)
        self.assertIn("USER 10001", rendered)

    def test_compose_template_requires_complete_rendering(self) -> None:
        path = SKILLS_ROOT / "container/assets/compose.yaml.template"
        rendered = render(
            path,
            {
                "PROJECT_NAME": "sample",
                "SERVICE_DEFINITIONS": "  app:\n    image: sample:local",
                "VOLUME_DEFINITIONS": "  data: {}",
            },
        )
        self.assertIn("services:\n  app:", rendered)
        self.assertNotIn("{{", rendered)
        CONTAINER_RENDERER.validate_compose_model(
            {
                "name": "sample",
                "services": {
                    "app": {
                        "image": f"sample:local@sha256:{EXAMPLE_DIGEST}",
                    }
                },
                "volumes": {"data": {}},
            }
        )


if __name__ == "__main__":
    unittest.main()
