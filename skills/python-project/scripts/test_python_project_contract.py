"""Deterministic contract checks for the python-project skill."""

from __future__ import annotations

import csv
import json
import re
import tomllib
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (SKILL_ROOT / relative_path).read_text(encoding="utf-8")


class PythonProjectContractTests(unittest.TestCase):
    def test_skill_routes_uv_dependency_work_and_preserves_ownership(self) -> None:
        skill = read("SKILL.md")

        self.assertIn("references/dependency-management.md", skill)
        self.assertIn("Use one canonical uv project workflow for new scaffolds", skill)
        self.assertIn("retain their current package manager", skill)
        self.assertIn("unless the user requests migration", skill)
        self.assertIn("Never generate, template, or hand-edit `uv.lock`", skill)
        self.assertRegex(skill, r"pending\s+post-integration step")

    def test_pyproject_separates_development_tools_from_consumer_extras(self) -> None:
        template = read("assets/pyproject.toml.template")
        rendered = (
            template.replace("{{project_slug}}", "example-project")
            .replace("{{package_name}}", "example_project")
            .replace("{{short_description}}", "Example project")
            .replace("{{author_name}}", "Example Author")
        )
        document = tomllib.loads(rendered)

        self.assertEqual(document["project"]["dependencies"], [])
        self.assertNotIn("dev", document["project"]["optional-dependencies"])
        dev = document["dependency-groups"]["dev"]
        self.assertTrue(any(requirement.startswith("pytest>=") for requirement in dev))
        self.assertTrue(any(requirement.startswith("ruff>=") for requirement in dev))

    def test_makefile_uses_locked_uv_and_preserves_project_environment(self) -> None:
        makefile = read("assets/Makefile.template")

        self.assertIn("$(UV) lock --check", makefile)
        self.assertIn("$(UV) sync --locked", makefile)
        self.assertIn("$(UV) run --locked", makefile)
        self.assertNotRegex(makefile, r"(?m)^.*\bpip(?:3)?\s+install\b")
        self.assertNotIn("VENV", makefile)
        self.assertNotIn("--upgrade", makefile)
        clean = makefile.split("clean:\n", maxsplit=1)[1]
        self.assertNotIn(".venv", clean)
        self.assertNotRegex(clean, r"rm\s+-rf\s+\$\(")

    def test_ci_pins_uv_and_uses_the_lock_in_every_job(self) -> None:
        workflow = read("assets/github-actions-ci.yml.template")

        actions = re.findall(r"(?m)^\s*(?:- )?uses: ([^\s]+)", workflow)
        self.assertEqual(len(actions), 18)
        for action in actions:
            with self.subTest(action=action):
                self.assertRegex(action, r"^[^@]+@[0-9a-f]{40}$")
        self.assertRegex(workflow, r"astral-sh/setup-uv@[0-9a-f]{40} # v\d+\.\d+\.\d+")
        self.assertRegex(workflow, r'(?m)^\s+version: "\d+\.\d+\.\d+"$')
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertEqual(workflow.count("persist-credentials: false"), 6)
        self.assertNotRegex(workflow, r"(?m)^.*\bpip(?:3)?\s+install\b")
        self.assertNotIn("--upgrade", workflow)

        for job in (
            "lint",
            "unit-tests",
            "build",
            "integration-tests",
            "coverage",
            "packaging",
        ):
            with self.subTest(job=job):
                section = re.search(
                    rf"(?ms)^  {re.escape(job)}:\n(.*?)(?=^  [a-z][a-z-]*:\n|\Z)",
                    workflow,
                )
                self.assertIsNotNone(section)
                body = section.group(1)
                self.assertLess(
                    body.index("uv lock --check"), body.index("uv sync --locked")
                )
                self.assertLess(
                    body.index("uv sync --locked"), body.index("uv run --locked")
                )

    def test_layout_and_assets_treat_lockfile_as_generated(self) -> None:
        layout = read("references/base-layout.md")

        self.assertIn("uv.lock", layout)
        self.assertIn(".python-version", layout)
        self.assertIn("uv sync --locked", layout)
        self.assertNotRegex(layout, r"(?m)^.*\bpip(?:3)?\s+install\b")
        lock_templates = [
            path
            for path in (SKILL_ROOT / "assets").rglob("*")
            if "uv.lock" in path.name
        ]
        self.assertEqual(lock_templates, [])

    def test_dependency_guidance_covers_safety_and_failure_contracts(self) -> None:
        guidance = read("references/dependency-management.md")
        required = (
            "--locked",
            "--frozen",
            "--active",
            "--no-sync",
            "lowest-direct",
            "first-index",
            "explicit = true",
            "credential-free index URLs",
            "requirements.txt",
            "uv lock --dry-run --upgrade-package",
            "Preserve uv's complete resolver explanation",
            "git diff -- pyproject.toml uv.lock",
        )
        for phrase in required:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, guidance)

        self.assertIn("may still contact configured package indexes", guidance)
        self.assertIn("do not replace a package-scoped", guidance)
        self.assertIn(
            "uv lock --upgrade --resolution lowest-direct --no-sources", guidance
        )
        self.assertIn("uv sync --locked --no-sources", guidance)
        self.assertIn("uv run --locked --no-sources pytest", guidance)
        self.assertIn("existing latest-resolution\nlock", guidance)
        self.assertNotIn("--index-strategy unsafe", guidance)

    def test_systemd_uses_the_project_owned_locked_environment(self) -> None:
        service = read("assets/systemd.service.template")
        guidance = read("references/cli-systemd.md")
        skill = read("SKILL.md")

        self.assertRegex(
            service,
            r"(?m)^ExecStart=/opt/\{\{project_slug\}\}/\.venv/bin/python -m ",
        )
        self.assertNotIn("/usr/bin/env python", service)
        self.assertIn("{{service_module}}", service)
        self.assertNotIn("{{package_name}}.agent.main", service)
        self.assertIn("uv sync --locked --no-dev", guidance)
        self.assertIn("exact project-owned interpreter", guidance)
        self.assertRegex(guidance, r"matching\s+importable module")
        self.assertIn("fully qualified service module", skill)

    def test_metadata_and_readme_expose_uv_intent(self) -> None:
        self.assertIn("uv-first", read("README.md"))
        metadata = read("agents/openai.yaml")
        self.assertIn("uv-locked", metadata)
        self.assertIn("preserve an existing manager", metadata)

    def test_evals_cover_trigger_boundaries_and_output_quality(self) -> None:
        with (SKILL_ROOT / "evals/trigger-prompts.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreaterEqual(sum(row["should_trigger"] == "true" for row in rows), 6)
        self.assertGreaterEqual(
            sum(row["should_trigger"] == "false" for row in rows), 6
        )

        evals = json.loads(read("evals/evals.json"))
        self.assertEqual(evals["skill_name"], "python-project")
        self.assertGreaterEqual(len(evals["evals"]), 4)
        prompts = "\n".join(case["prompt"] for case in evals["evals"])
        self.assertIn("published uv library", prompts)
        self.assertIn("private package index", prompts)
        self.assertIn("do not migrate", prompts)
        self.assertIn("Python systemd service", prompts)


if __name__ == "__main__":
    unittest.main()
