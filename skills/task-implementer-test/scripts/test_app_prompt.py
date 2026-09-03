#!/usr/bin/env python3

import tempfile
import unittest
import uuid
from pathlib import Path

from render_app_prompt import preserve_managed_frontmatter, render, write_private


class RenderPromptTests(unittest.TestCase):
    def test_renders_every_placeholder(self) -> None:
        generation = str(uuid.uuid4())
        result = render(
            "{{GENERATION_ID}} {{COMPOSE_PROJECT}}",
            generation,
            "task-test-abc",
        )
        self.assertEqual(result, f"{generation} task-test-abc")

    def test_rejects_unresolved_placeholder(self) -> None:
        with self.assertRaises(ValueError):
            render(
                "{{GENERATION_ID}} {{COMPOSE_PROJECT}} {{OTHER}}",
                str(uuid.uuid4()),
                "task-test-abc",
            )

    def test_private_write_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "target"
            target.write_text("x", encoding="utf-8")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                write_private(link, "new")

    def test_preserves_managed_identity_frontmatter(self) -> None:
        managed = (
            "---\n"
            "schema: task-implementer/prompt-v3\n"
            "prompt_id: prompt-1\n"
            "prompt_ref: abcde\n"
            "title: Test prompt\n"
            "created_at: now\n"
            "---\n\n"
            "old\n"
        )
        result = preserve_managed_frontmatter(managed, "# New body\n")
        self.assertTrue(result.startswith("---\nschema: task-implementer/prompt-v3"))
        self.assertTrue(result.endswith("# New body\n"))

    def test_rejects_legacy_managed_prompt(self) -> None:
        managed = (
            "---\n"
            "schema: task-implementer/prompt-v1\n"
            "prompt_id: prompt-1\n"
            "title: Test prompt\n"
            "created_at: now\n"
            "---\n\n"
            "old\n"
        )
        with self.assertRaisesRegex(ValueError, "task-implementer/prompt-v3"):
            preserve_managed_frontmatter(managed, "# New body\n")


if __name__ == "__main__":
    unittest.main()
