#!/usr/bin/env python3
"""Focused tests for root prompt intake and project-contract observation."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


HOOK_DIR = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


lifecycle = load_module(
    "project_specs_lifecycle", HOOK_DIR / "project_specs_lifecycle.py"
)


def git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    return completed.stdout.strip()


class ProjectSpecHookTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.email", "test@example.com")
        git(self.project, "config", "user.name", "Test User")
        (self.project / "README.md").write_text("# Project\n", encoding="utf-8")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "baseline")
        self.previous_home = os.environ.get("CODEX_HOME")
        self.codex_home = self.root / "codex"
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.base = {
            "cwd": str(self.project),
            "session_id": "session-1",
            "turn_id": "turn-1",
        }

    def tearDown(self) -> None:
        if self.previous_home is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous_home
        self.temporary.cleanup()

    def write_markers(self, version: int = 2) -> None:
        docs = self.project / "docs"
        docs.mkdir()
        (docs / "requirements.md").write_text(
            "<!-- maintain-project-specs:requirements:start "
            f"schema=maintain-project-specs/requirements-v{version} -->\n",
            encoding="utf-8",
        )
        (docs / "design.md").write_text(
            "<!-- maintain-project-specs:design:start "
            f"schema=maintain-project-specs/design-v{version} -->\n",
            encoding="utf-8",
        )

    def intake_files(self) -> list[Path]:
        root = self.codex_home / "project-specs/prompt-intake"
        return list(root.rglob("*.json")) if root.exists() else []

    def test_missing_contract_reports_actual_pending_state(self) -> None:
        result = lifecycle.evaluate(
            {**self.base, "hook_event_name": "SessionStart"}
        )
        message = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("CONTRACT_PENDING", message)
        self.assertNotIn("ADVISORY_UNAVAILABLE", message)
        self.assertFalse((self.codex_home / "project-specs").exists())

    def test_current_contract_is_silent(self) -> None:
        self.write_markers()
        self.assertEqual(
            lifecycle.evaluate({**self.base, "hook_event_name": "SessionStart"}),
            {},
        )

    def test_observation_rejects_symlinked_canonical_document(self) -> None:
        docs = self.project / "docs"
        docs.mkdir()
        external = self.root / "external-requirements.md"
        external.write_text(lifecycle.REQUIREMENTS_MARKER, encoding="utf-8")
        (docs / "requirements.md").symlink_to(external)
        (docs / "design.md").write_text(
            lifecycle.DESIGN_MARKER, encoding="utf-8"
        )

        result = lifecycle.evaluate(
            {**self.base, "hook_event_name": "SessionStart"}
        )

        self.assertIn(
            "CONTRACT_INVALID", result["hookSpecificOutput"]["additionalContext"]
        )

    def test_observation_rejects_oversized_canonical_document(self) -> None:
        docs = self.project / "docs"
        docs.mkdir()
        (docs / "requirements.md").write_text(
            lifecycle.REQUIREMENTS_MARKER + "x" * (1024 * 1024), encoding="utf-8"
        )
        (docs / "design.md").write_text(
            lifecycle.DESIGN_MARKER, encoding="utf-8"
        )

        result = lifecycle.evaluate(
            {**self.base, "hook_event_name": "SessionStart"}
        )

        self.assertIn(
            "CONTRACT_INVALID", result["hookSpecificOutput"]["additionalContext"]
        )

    def test_only_committed_exact_policy_can_disable_prompt_intake(self) -> None:
        policy = self.project / ".codex/project-specs.json"
        policy.parent.mkdir()
        policy.write_text(
            '{"schema":"maintain-project-specs.project.v1",'
            '"mode":"disabled","scope":"."}\n',
            encoding="utf-8",
        )
        payload = {
            **self.base,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Implement the accepted requirement.",
        }

        self.assertTrue(lifecycle.evaluate(payload)["continue"])
        git(self.project, "add", ".codex/project-specs.json")
        git(self.project, "commit", "-qm", "disable project spec intake")

        self.assertEqual(lifecycle.evaluate(payload), {})

    def test_legacy_contract_requests_explicit_migration(self) -> None:
        self.write_markers(version=1)
        result = lifecycle.evaluate(
            {**self.base, "hook_event_name": "SessionStart"}
        )
        message = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("CONTRACT_MIGRATION_REQUIRED", message)
        self.assertNotIn("lifecycle.json", message)

    def test_direct_prompt_stages_only_identity_metadata(self) -> None:
        prompt = "Add one durable requirement and implement it."
        result = lifecycle.evaluate(
            {**self.base, "hook_event_name": "UserPromptSubmit", "prompt": prompt}
        )
        message = result["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(result["continue"])
        self.assertIn("Classify the delivered prompt statement-by-statement", message)
        self.assertIn("Workers inherit the root intent", message)
        self.assertNotIn(prompt, message)
        files = self.intake_files()
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], lifecycle.INTAKE_SCHEMA)
        self.assertEqual(record["classification"], "unclassified")
        self.assertNotIn(prompt, files[0].read_text(encoding="utf-8"))
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)

    def test_repeated_identical_turn_is_idempotent(self) -> None:
        payload = {
            **self.base,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Implement the accepted design.",
        }
        first = lifecycle.evaluate(payload)
        second = lifecycle.evaluate(payload)
        self.assertEqual(first, second)
        self.assertEqual(len(self.intake_files()), 1)

    def test_concurrent_identical_turn_publishes_one_intake_record(self) -> None:
        payload = {
            **self.base,
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Implement the accepted design.",
        }
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lifecycle.evaluate, [payload] * 8))
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(self.intake_files()), 1)

    def test_secret_prompt_is_not_persisted(self) -> None:
        result = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "UserPromptSubmit",
                "prompt": "token=abcdefghijklmnopqrstuvwxyz123456",
            }
        )
        self.assertIn(
            "sensitive data",
            result["hookSpecificOutput"]["additionalContext"],
        )
        self.assertEqual(self.intake_files(), [])

    def test_generated_and_worker_prompts_are_excluded(self) -> None:
        for extra in (
            {"is_subagent": True},
            {"agent_type": "worker"},
            {"prompt_source": "continuation"},
            {"source": "compaction"},
            {"stop_hook_active": True},
        ):
            with self.subTest(extra=extra):
                result = lifecycle.evaluate(
                    {
                        **self.base,
                        **extra,
                        "hook_event_name": "UserPromptSubmit",
                        "prompt": "Continue",
                    }
                )
                self.assertEqual(result, {})
        self.assertEqual(self.intake_files(), [])

    def test_tool_and_stop_events_have_no_project_spec_hook_result(self) -> None:
        for event in ("PreToolUse", "PostToolUse", "Stop"):
            with self.subTest(event=event):
                self.assertEqual(
                    lifecycle.evaluate({**self.base, "hook_event_name": event}), {}
                )

    def test_main_failure_is_nonblocking_without_legacy_status(self) -> None:
        payload = {**self.base, "hook_event_name": "UserPromptSubmit"}
        stdout = io.StringIO()
        with (
            mock.patch.object(lifecycle, "evaluate", side_effect=RuntimeError()),
            mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))),
            redirect_stdout(stdout),
        ):
            self.assertEqual(lifecycle.main(), 0)
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["continue"])
        self.assertIn("unavailable", result["hookSpecificOutput"]["additionalContext"])
        self.assertNotIn(
            "ADVISORY_UNAVAILABLE",
            result["hookSpecificOutput"]["additionalContext"],
        )

    def test_manifest_registers_only_session_and_prompt_events(self) -> None:
        manifest = json.loads(
            (HOOK_DIR.parent / "hooks.json.template").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(manifest["hooks"]), {"SessionStart", "UserPromptSubmit"}
        )


if __name__ == "__main__":
    unittest.main()
