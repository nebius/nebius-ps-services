#!/usr/bin/env python3
"""Structural preservation checks; semantic quality needs native comparisons."""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DesignContractTests(unittest.TestCase):
    def test_all_core_reference_routes_resolve(self):
        core = (ROOT / "SKILL.md").read_text()
        paths = set(re.findall(r"references/[a-z-]+\.md", core))
        self.assertEqual(paths, {"references/design-workflow.md",
                                 "references/readme-design.md",
                                 "references/portable-html-design.md"})
        for relative in paths:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_seven_phases_remain_ordered_and_core_is_smaller(self):
        core = (ROOT / "SKILL.md").read_text()
        self.assertEqual(re.findall(r"^### Phase (\d):", core, re.M), list("1234567"))
        self.assertLess(len(core.splitlines()), 499)

    def test_existing_cases_and_all_fixture_inputs_remain_available(self):
        cases = json.loads((ROOT / "evals/evals.json").read_text())["evals"]
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        self.assertTrue(set(range(1, 23)) <= {case["id"] for case in cases})
        for case in cases:
            self.assertTrue(case["assertions"])
            for relative in case["files"]:
                path = ROOT / relative
                self.assertTrue(path.is_file() and path.resolve().is_relative_to(ROOT))

    def test_planning_cases_have_a_deterministic_no_write_oracle(self):
        cases = json.loads((ROOT / "evals/evals.json").read_text())["evals"]
        for case in cases:
            if case["id"] != 8:  # Existing explicitly authorized implementation case.
                self.assertEqual(case["fixture_policy"], "unchanged")
        groups = {case["id"] for case in cases
                  if case.get("performance_group") == "simple-standard"}
        self.assertEqual(groups, {1, 2, 3, 4, 11, 23, 24, 27})


if __name__ == "__main__":
    unittest.main()
