#!/usr/bin/env python3
"""Disposable fixture tests for check-local-idempotency.py."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().with_name("check-local-idempotency.py")
SKILL_ROOT = Path(__file__).resolve().parent.parent
ASSETS = SKILL_ROOT / "assets"


def copy_template(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


class CheckLocalIdempotencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.codex_home = Path(self.tmp.name) / "codex"
        self.codex_home.mkdir()
        self.render_valid_home()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def render_valid_home(self) -> None:
        copy_template(ASSETS / "AGENTS.md.template", self.codex_home / "AGENTS.md")
        copy_template(ASSETS / "config.toml.template", self.codex_home / "config.toml")
        copy_template(ASSETS / "hooks.json.template", self.codex_home / "hooks.json")
        for source in (ASSETS / "hooks").glob("*.template"):
            target = self.codex_home / "hooks" / source.name.removesuffix(".template")
            copy_template(source, target)
        for source in (ASSETS / "agents").glob("*.template"):
            target = self.codex_home / "agents" / source.name.removesuffix(".template")
            copy_template(source, target)
        task_state = self.codex_home / "task-state"
        task_state.mkdir()
        task_state.chmod(0o700)

    def run_check(self) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--codex-home",
                str(self.codex_home),
                "--strict-agents-template",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
            timeout=10,
        )

    def write_policy(self, value: object) -> None:
        path = self.codex_home / "hooks" / "global_context_policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def assert_check_passes(self) -> None:
        result = self.run_check()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Idempotency preflight passed", result.stdout)

    def assert_check_fails_policy(self) -> None:
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(
            "optional global_context_policy.json does not enable read-only subagent delegation",
            result.stdout,
        )

    def test_passes_without_optional_policy(self) -> None:
        self.assert_check_passes()

    def test_passes_with_enabled_optional_policy(self) -> None:
        self.write_policy({"auto_read_only_subagents": True})
        self.assert_check_passes()

    def test_rejects_empty_optional_policy(self) -> None:
        self.write_policy({})
        self.assert_check_fails_policy()

    def test_rejects_disabled_optional_policy(self) -> None:
        self.write_policy({"auto_read_only_subagents": False})
        self.assert_check_fails_policy()

    def test_rejects_non_object_optional_policy(self) -> None:
        self.write_policy([])
        result = self.run_check()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("global_context_policy.json must contain a JSON object", result.stdout)

    def test_does_not_create_bytecode(self) -> None:
        before = {path.resolve() for path in SKILL_ROOT.rglob("__pycache__")}
        self.assert_check_passes()
        after = {path.resolve() for path in SKILL_ROOT.rglob("__pycache__")}
        self.assertEqual(
            before,
            after,
            "idempotency fixture test should not leave __pycache__ under config-codex",
        )


if __name__ == "__main__":
    unittest.main()
