#!/usr/bin/env python3
"""Regression tests for canonical three-tier prompt rendering and intake."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import owned_git_origin  # noqa: E402


SKILLS_ROOT = Path(__file__).resolve().parents[2]
PROMPT_WORKSPACE = SKILLS_ROOT / "sdlc-start" / "scripts" / "prompt_workspace.py"
RENDERER = Path(__file__).with_name("render_three_tier_prompt.py")
HEADLESS_CONTRACT_FILES = (
    SKILLS_ROOT / "sdlc-workflow-test" / "references" / "three-tier-process.md",
    SKILLS_ROOT / "sdlc-workflow-test" / "references" / "three-tier-live.md",
    SKILLS_ROOT
    / "sdlc-workflow-test"
    / "references"
    / "verification-checklist.md",
    SKILLS_ROOT
    / "sdlc-workflow-test"
    / "assets"
    / "three-tier-prompt.md.template",
)


class ThreeTierPromptTests(unittest.TestCase):
    def run_json(self, *arguments: str, cwd=None, env=None) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, *arguments],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            cwd=cwd,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def test_rendered_starter_is_accepted_as_a_new_managed_run(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.home()) as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            (project / "README.md").write_text("# Disposable admission test\n")
            baseline = owned_git_origin.create_baseline(project)
            owned_git_origin.initialize(project, root, "a" * 32)
            codex_home = root / "codex-home"
            initialized = self.run_json(
                str(PROMPT_WORKSPACE),
                "init",
                str(project),
                "--agent-home",
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
                    "sdlc-workflow-test-aaaaaaaaaaaa",
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
                "--agent-home",
                str(codex_home),
                "--json",
            )
            self.assertEqual(intake["action"], "new")
            self.assertEqual(intake["revision"], "r0001")
            promotions = list(codex_home.rglob("git-promotion.json"))
            self.assertEqual(len(promotions), 1)
            promotion = json.loads(promotions[0].read_text())
            self.assertEqual(promotion["default_branch"], "main")
            self.assertEqual(
                promotion["promotion_branch"], owned_git_origin._git(project, "branch", "--show-current")
            )
            manager = SKILLS_ROOT / "worktree/scripts/worktree_manager.py"
            environment = {**os.environ, "SKILLS_AGENT": "codex", "CODEX_HOME": str(codex_home)}
            worktree = self.run_json(
                str(manager), "add", "--project", ".", "--task-slug", "admission",
                cwd=project, env=environment,
            )
            self.assertTrue(Path(str(worktree["worktree"])).is_dir())
            self.run_json(str(manager), "remove", "--name", str(worktree["name"]), cwd=project, env=environment)
            owned_git_origin.validate(project, root, "a" * 32, baseline=baseline)

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
                "--agent-home",
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

    def test_headless_browser_contract_is_mirrored(self) -> None:
        skill = (SKILLS_ROOT / "sdlc-workflow-test" / "SKILL.md").read_text()
        self.assertIn("read and follow\n`references/three-tier-process.md` before any live operation", skill)
        for path in HEADLESS_CONTRACT_FILES:
            with self.subTest(path=path):
                text = " ".join(
                    path.read_text(encoding="utf-8").lower().split()
                )
                self.assertIn("headless playwright test", text)
                self.assertIn("uat-before-restart", text)
                self.assertIn("uat-after-restart", text)
                self.assertIn("fresh", text)
                self.assertIn("independent", text)
                self.assertNotIn("pre-navigation-window-capture", text)
                self.assertNotIn("harness: computer-use", text)



if __name__ == "__main__":
    unittest.main()
