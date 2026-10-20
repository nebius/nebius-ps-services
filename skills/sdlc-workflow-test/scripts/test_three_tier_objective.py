#!/usr/bin/env python3
"""Regression tests for canonical three-tier prompt rendering and intake."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))


SKILLS_ROOT = Path(__file__).resolve().parents[2]

RENDERER = Path(__file__).with_name("render_three_tier_objective.py")
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
    / "three-tier-objective.md.template",
)


class ThreeTierObjectiveTests(unittest.TestCase):
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

    def values(self):
        return {'PROJECT_ROOT':'/tmp/project','PRIVATE_ROOT':'/tmp/private','EVIDENCE_ROOT':'/tmp/evidence',
                'VERIFICATION_ID':'a'*32,'COMPOSE_PROJECT':'sdlc-workflow-test-aaaaaaaaaaaa'}

    def test_rendered_objective_has_no_managed_frontmatter(self):
        from render_three_tier_objective import render
        value = render(SKILLS_ROOT / 'sdlc-workflow-test/assets/three-tier-objective.md.template', self.values())
        self.assertFalse(value.startswith('---'))
        self.assertNotIn('prompt_ref:', value)
        self.assertNotIn('{{', value)
        self.assertIn('## Acceptance criteria', value)

    def test_renderer_rejects_compose_identity_drift(self):
        from render_three_tier_objective import render, ObjectiveRenderError
        with self.assertRaisesRegex(ObjectiveRenderError, 'does not match'):
            render(SKILLS_ROOT / 'sdlc-workflow-test/assets/three-tier-objective.md.template',
                   {**self.values(), 'COMPOSE_PROJECT':'foreign-project'})

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
