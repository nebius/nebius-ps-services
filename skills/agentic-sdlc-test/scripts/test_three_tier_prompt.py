#!/usr/bin/env python3
"""Regression tests for canonical three-tier prompt rendering and intake."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SKILLS_ROOT = Path(__file__).resolve().parents[2]
PROMPT_WORKSPACE = SKILLS_ROOT / "sdlc-start" / "scripts" / "prompt_workspace.py"
RENDERER = Path(__file__).with_name("render_three_tier_prompt.py")
COMPUTER_USE_CONTRACT_FILES = (
    SKILLS_ROOT / "agentic-sdlc-test" / "SKILL.md",
    SKILLS_ROOT / "agentic-sdlc-test" / "references" / "three-tier-live.md",
    SKILLS_ROOT
    / "agentic-sdlc-test"
    / "references"
    / "verification-checklist.md",
    SKILLS_ROOT
    / "agentic-sdlc-test"
    / "assets"
    / "three-tier-prompt.md.template",
)


class ThreeTierPromptTests(unittest.TestCase):
    def run_json(self, *arguments: str) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_rendered_starter_is_accepted_as_a_new_managed_run(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            codex_home = root / "codex-home"
            initialized = self.run_json(
                str(PROMPT_WORKSPACE),
                "init",
                str(project),
                "--codex-home",
                str(codex_home),
                "--no-open",
                "--json",
            )
            starter = Path(str(initialized["starter_prompt"]))
            rendered = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--starter",
                    str(starter),
                    "--project-root",
                    str(project),
                    "--private-root",
                    str(root / "private"),
                    "--evidence-root",
                    str(root / "evidence"),
                    "--verification-id",
                    "a" * 32,
                    "--compose-project",
                    "agentic-sdlc-test-aaaaaaaaaaaa",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            intake = self.run_json(
                str(PROMPT_WORKSPACE),
                "intake",
                str(starter),
                "--project-path",
                str(project),
                "--codex-home",
                str(codex_home),
                "--json",
            )
            self.assertEqual(intake["action"], "new")
            self.assertEqual(intake["revision"], "r0001")

    def test_renderer_rejects_compose_identity_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            codex_home = root / "codex-home"
            initialized = self.run_json(
                str(PROMPT_WORKSPACE),
                "init",
                str(project),
                "--codex-home",
                str(codex_home),
                "--no-open",
                "--json",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(RENDERER),
                    "--starter",
                    str(initialized["starter_prompt"]),
                    "--project-root",
                    str(project),
                    "--private-root",
                    str(root / "private"),
                    "--evidence-root",
                    str(root / "evidence"),
                    "--verification-id",
                    "a" * 32,
                    "--compose-project",
                    "foreign-project",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("does not match", result.stderr)

    def test_computer_use_jit_readiness_contract_is_mirrored(self) -> None:
        for path in COMPUTER_USE_CONTRACT_FILES:
            with self.subTest(path=path):
                text = " ".join(
                    path.read_text(encoding="utf-8").lower().split()
                )
                self.assertIn("immediately before", text)
                self.assertIn("unlocked", text)
                self.assertIn("visible", text)
                self.assertIn("foreground", text)
                self.assertIn("current macos space", text)
                self.assertIn("environment_defect", text)
                self.assertIn("pre-navigation-window-capture", text)
                self.assertIn("no gui navigation or action was attempted", text)
                self.assertIn("stop all further computer use calls", text)
                self.assertIn("separate explicitly authorized action", text)


if __name__ == "__main__":
    unittest.main()
