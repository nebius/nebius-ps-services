"""Real Markdown regression checks for the private design-candidate checker."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import check_design_markdown as checker


FEATURE = (
    "<!-- FEATURE: FEAT-001 reqs=REQ-001,REQ-002 status=ready "
    "delivery=not-started priority=P0 version=1 -->"
)
DOCUMENT = (
    f"{checker.START}\n# Design\n\n{FEATURE}\n"
    "## Evidence\n\n[Source](https://example.com/reference)\n\n"
    f"<!-- /FEATURE: FEAT-001 -->\n{checker.END}\n"
)


class DesignMarkdownTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="design-markdown-test-")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        # Make the fixture independent of ancestor/user configuration.
        (self.project / ".markdownlint.json").write_text('{"default": true}\n')
        self.candidate = self.project / "design.md"
        self.candidate.write_text(DOCUMENT)

    def test_original_gate_fails_but_owned_markers_pass_without_source_edits(self):
        if not shutil.which("markdownlint"):
            self.skipTest("markdownlint is required for the real checker regression")
        original = self.candidate.read_bytes()
        baseline = subprocess.run(
            ["markdownlint", "--json", str(self.candidate)],
            cwd=self.project,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(baseline.returncode, 1)
        self.assertEqual(
            [issue["ruleNames"][0] for issue in json.loads(baseline.stderr)],
            ["MD013", "MD013"],
        )
        report = checker.check(self.candidate, self.project)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["marker_line_exceptions"], [1, 4])
        self.assertEqual(self.candidate.read_bytes(), original)

    def test_citations_and_prose_still_fail_at_original_lines(self):
        if not shutil.which("markdownlint"):
            self.skipTest("markdownlint is required for the real checker regression")
        text = DOCUMENT.replace(
            "[Source](https://example.com/reference)",
            "https://example.com/reference\n\n" + "ordinary prose words " * 8 + "end.",
        )
        self.candidate.write_text(text)
        report = checker.check(self.candidate, self.project)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(
            {(item["line"], item["rule"]) for item in report["violations"]},
            {(7, "MD034"), (9, "MD013")},
        )

    def test_code_examples_and_unrecognized_comments_are_not_exempt(self):
        text = DOCUMENT.replace(
            "## Evidence",
            f"```markdown\n{FEATURE}\n```\n\n"
            f"<!-- unrelated {'ordinary words ' * 8}-->\n\n## Evidence",
        )
        projected, _, exempted = checker.projection(text)
        self.assertEqual(exempted, [1, 4])
        self.assertIn(f"```markdown\n{FEATURE}\n```", projected)
        self.assertEqual(projected.count(checker.DIRECTIVE), 2)

    def test_fake_or_repeated_schema_markers_fail_closed(self):
        for text in (
            f"```markdown\n{DOCUMENT}```\n",
            DOCUMENT + checker.START,
            DOCUMENT.replace("design-v2", "design-v3"),
        ):
            with self.subTest(text=text[:30]), self.assertRaises(checker.CheckError):
                checker.projection(text)

    def test_projection_preserves_original_bytes_and_crlf(self):
        for text in (DOCUMENT, DOCUMENT.replace("\n", "\r\n")):
            projected, _, _ = checker.projection(text)
            self.assertEqual(projected.replace(checker.DIRECTIVE + "\n", ""), text)

    def test_diagnostics_omit_tool_source_excerpts(self):
        issues = [
            {
                "lineNumber": 9,
                "ruleNames": ["MD034", "no-bare-urls"],
                "errorContext": "private source excerpt",
                "errorDetail": "private detail",
            }
        ]
        result = subprocess.CompletedProcess([], 1, "", json.dumps(issues))
        with (
            patch.object(checker.shutil, "which", return_value="markdownlint"),
            patch.object(checker.subprocess, "run", return_value=result),
        ):
            report = checker.check(self.candidate, self.project)
        self.assertEqual(report["violations"], [{"line": 7, "rule": "MD034"}])
        self.assertNotIn("private", json.dumps(report))

    def test_tool_failure_does_not_reflect_source_or_configuration_output(self):
        result = subprocess.CompletedProcess([], 2, "", "private tool diagnostics")
        with (
            patch.object(checker.shutil, "which", return_value="markdownlint"),
            patch.object(checker.subprocess, "run", return_value=result),
            self.assertRaisesRegex(checker.CheckError, "^MARKDOWNLINT_PROTOCOL_ERROR$"),
        ):
            checker.check(self.candidate, self.project)

    def test_missing_tool_cannot_pass(self):
        with (
            patch.object(checker.shutil, "which", return_value=None),
            self.assertRaisesRegex(checker.CheckError, "^MARKDOWNLINT_UNAVAILABLE$"),
        ):
            checker.check(self.candidate, self.project)

    def test_changed_candidate_cannot_pass(self):
        result = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch.object(checker.shutil, "which", return_value="markdownlint"),
            patch.object(checker.subprocess, "run", return_value=result),
            patch.object(
                checker, "read_candidate", side_effect=[DOCUMENT.encode(), b"changed"]
            ),
            self.assertRaisesRegex(checker.CheckError, "^CANDIDATE_CHANGED$"),
        ):
            checker.check(self.candidate, self.project)


if __name__ == "__main__":
    unittest.main()
