"""A writable design eval cannot hide application changes behind a model score."""

import importlib.util
from pathlib import Path
import stat
import unittest

spec = importlib.util.spec_from_file_location(
    "skill_evaluator", Path(__file__).with_name("run-skill-evals.py")
)
evaluator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluator)


class DocsOnlyPolicyTests(unittest.TestCase):
    def test_regular_document_writes_allowed(self):
        after = {
            "docs": {"type": stat.S_IFDIR, "mode": 0o755},
            "docs/design.md": {"type": stat.S_IFREG, "mode": 0o644, "sha256": "abc"},
        }
        self.assertEqual(evaluator.docs_only_violations({}, after), [])

    def test_source_deletions_links_and_executables_rejected(self):
        for path, entry in [
            ("app.py", {"type": stat.S_IFREG, "mode": 0o644}),
            ("docs/link", {"type": stat.S_IFLNK, "mode": 0o777}),
            ("docs/run", {"type": stat.S_IFREG, "mode": 0o755}),
        ]:
            with self.subTest(path=path):
                self.assertEqual(
                    evaluator.docs_only_violations({}, {path: entry}), [path]
                )
        self.assertEqual(
            evaluator.docs_only_violations({"docs/design.md": {}}, {}),
            ["docs/design.md"],
        )


if __name__ == "__main__":
    unittest.main()
