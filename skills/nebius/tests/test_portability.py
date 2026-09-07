"""Skill closure and side-effect-free command surfaces; no cloud calls."""

import ast
import csv
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PortabilityTests(unittest.TestCase):
    def test_all_asset_imports_work_without_sdk_or_configuration(self):
        code = """
import pathlib, runpy, socket, sys
def blocked(*args, **kwargs):
    raise AssertionError('import attempted network access')
socket.socket = blocked
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / 'assets'))
for path in sorted((root / 'assets').rglob('*.py')):
    runpy.run_path(str(path), run_name='offline_import')
"""
        result = subprocess.run(
            [sys.executable, "-S", "-B", "-c", code, str(ROOT)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_inspector_help_and_bad_arguments_need_no_sdk(self):
        for script in sorted((ROOT / "scripts").glob("inspect_*.py")):
            for args, expected in [(["--help"], 0), (["--unknown-option"], 2)]:
                with self.subTest(script=script.name, args=args):
                    result = subprocess.run(
                        [sys.executable, "-S", "-B", str(script), *args],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=10,
                        env={"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1"},
                    )
                    self.assertEqual(result.returncode, expected, result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_python_syntax_and_local_markdown_links(self):
        for path in ROOT.rglob("*.py"):
            ast.parse(path.read_text(), filename=str(path), feature_version=(3, 10))
        for path in ROOT.rglob("*.md"):
            for target in re.findall(r"\]\(([^\s)]+)\)", path.read_text()):
                target = target.split("#", 1)[0]
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (path.parent / target).resolve()
                self.assertTrue(
                    resolved.is_relative_to(ROOT), f"link escapes skill: {path.name}"
                )
                self.assertTrue(
                    resolved.exists(), f"broken local link: {path.name}: {target}"
                )

    def test_trigger_and_quality_cases_are_valid_definitions(self):
        with (ROOT / "evals/trigger-prompts.csv").open() as source:
            reader = csv.DictReader(source)
            self.assertEqual(reader.fieldnames, ["id", "should_trigger", "prompt"])
            rows = list(reader)
        self.assertEqual(len({r["id"] for r in rows}), len(rows))
        self.assertTrue(all(r["id"] and r["prompt"] for r in rows))
        for value in ("true", "false"):
            self.assertGreaterEqual(sum(r["should_trigger"] == value for r in rows), 3)
        data = json.loads((ROOT / "evals/evals.json").read_text())
        self.assertEqual(data["skill_name"], "nebius")
        self.assertGreaterEqual(len(data["evals"]), 2)
        self.assertTrue(
            all(
                case["assertions"] and case["expected_output"] for case in data["evals"]
            )
        )


if __name__ == "__main__":
    unittest.main()
