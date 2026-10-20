#!/usr/bin/env python3

import tempfile
import unittest
import uuid
from pathlib import Path

from render_app_objective import render, write_private


class RenderObjectiveTests(unittest.TestCase):
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

    def test_direct_objective_has_no_managed_frontmatter(self) -> None:
        template = (Path(__file__).resolve().parents[1] / 'assets/app-objective.md.template').read_text()
        result = render(template, str(uuid.uuid4()), 'test-generation')
        self.assertFalse(result.startswith('---'))
        self.assertNotIn('prompt_id:', result)


if __name__ == "__main__":
    unittest.main()
