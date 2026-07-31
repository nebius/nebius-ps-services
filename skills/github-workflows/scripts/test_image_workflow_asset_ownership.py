#!/usr/bin/env python3
"""Regression tests for the single owner of image-publish workflow YAML."""

from __future__ import annotations

import unittest
from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[2]
CANONICAL = (
    SKILLS_ROOT
    / "github-workflows"
    / "assets"
    / "project-name-image-publish.yml.template"
)


class ImageWorkflowAssetOwnershipTest(unittest.TestCase):
    def test_github_workflows_is_the_only_template_owner(self) -> None:
        matches = sorted(
            path.relative_to(SKILLS_ROOT).as_posix()
            for path in SKILLS_ROOT.glob(
                "*/assets/project-name-image-publish.yml.template"
            )
        )

        self.assertEqual(
            matches,
            ["github-workflows/assets/project-name-image-publish.yml.template"],
        )

    def test_canonical_template_checks_tag_lineage(self) -> None:
        template = CANONICAL.read_text(encoding="utf-8")

        self.assertIn("fetch-depth: 0", template)
        self.assertIn("git merge-base --is-ancestor", template)
        self.assertIn("provenance: mode=max", template)
        self.assertIn("sbom: true", template)
        self.assertNotIn("provenance: false", template)


if __name__ == "__main__":
    unittest.main()
