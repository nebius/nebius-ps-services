#!/usr/bin/env python3
"""Coverage and failure contracts for repository CI parallelism."""

from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

from run_unittest_shard import partition

ROOT = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).with_name("run_unittest_shard.py")


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader
    )


class ShardTests(unittest.TestCase):
    def fixture_cases(self):
        result = []
        for name, size in [("Small", 2), ("Large", 7), ("Medium", 4), ("Other", 3)]:
            cls = type(
                name,
                (unittest.TestCase,),
                {f"test_{index}": lambda self: None for index in range(size)},
            )
            result.extend(unittest.defaultTestLoader.loadTestsFromTestCase(cls))
        return result

    def test_partition_preserves_every_test_once_and_whole_classes(self):
        tests = self.fixture_cases()
        shards = partition(tests, 3)
        self.assertEqual(
            Counter(t.id() for t in tests), Counter(t.id() for s in shards for t in s)
        )
        owners = {}
        for index, shard in enumerate(shards):
            self.assertTrue(shard)
            self.assertEqual(shard, [t for t in tests if t in shard])
            for test in shard:
                self.assertEqual(owners.setdefault(type(test), index), index)
        self.assertEqual(shards, partition(tests, 3))

    def test_invalid_empty_duplicate_or_excessive_partition_fails(self):
        tests = self.fixture_cases()
        for values, count in [(tests, 0), ([], 1), (tests + tests, 2), (tests, 5)]:
            with (
                self.subTest(count=count, length=len(values)),
                self.assertRaises(ValueError),
            ):
                partition(values, count)

    def run_fixture(self, source, index=0, count=1, extra=()):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "test_fixture.py").write_text(source)
            return subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(RUNNER),
                    "--start-directory",
                    directory,
                    "--shard-index",
                    str(index),
                    "--shard-count",
                    str(count),
                    *extra,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_cli_runs_module_and_class_fixtures_once_and_propagates_failure(self):
        source = """import unittest
calls = []
def setUpModule(): calls.append("module")
class Example(unittest.TestCase):
    @classmethod
    def setUpClass(cls): calls.append("class")
    def test_a(self): self.assertEqual(calls, ["module", "class"])
    def test_b(self): self.assertEqual(calls, ["module", "class"])
"""
        result = self.run_fixture(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Ran 2 tests", result.stderr)
        result = self.run_fixture(
            source.replace(
                'self.assertEqual(calls, ["module", "class"])', 'self.fail("sentinel")'
            )
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("sentinel", result.stderr)

    def test_import_error_fails_every_shard(self):
        for index in range(2):
            result = self.run_fixture('raise ImportError("sentinel")', index, 2)
            self.assertEqual(result.returncode, 1)
            self.assertIn("sentinel", result.stderr)

    def test_invalid_selection_and_empty_discovery_fail(self):
        for source, index, count in [("", 0, 1), ("", -1, 2), ("", 2, 2)]:
            self.assertNotEqual(self.run_fixture(source, index, count).returncode, 0)

    def test_list_does_not_run_tests(self):
        result = self.run_fixture(
            "import unittest\nclass Example(unittest.TestCase):\n def test_failure(self): self.fail()\n",
            extra=("--list",),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["selected"], 1)


class WorkflowTests(unittest.TestCase):
    def test_all_skills_lanes_are_required_by_both_evidence_owners(self):
        jobs = workflow("skills-project-specs-ci.yml")["jobs"]
        required = set(jobs) - {"merge-inputs", "result-evidence", "candidate-evidence"}
        self.assertTrue(
            {"verify", "workflow-adapters", "worktree-tests", "native-harness"}
            <= required
        )
        for name in ("result-evidence", "candidate-evidence"):
            self.assertEqual(set(jobs[name]["needs"]), required | {"merge-inputs"})
            self.assertIn("always()", jobs[name]["if"])
        evidence = next(
            s
            for s in jobs["result-evidence"]["steps"]
            if "CI_EXPECTED_JOBS" in s.get("env", {})
        )
        self.assertEqual(set(json.loads(evidence["env"]["CI_EXPECTED_JOBS"])), required)
        for name in required:
            job = jobs[name]
            self.assertEqual(job["needs"], ["merge-inputs"])
            self.assertNotIn("continue-on-error", job)
            steps = job["steps"]
            identity = next(
                i
                for i, s in enumerate(steps)
                if s.get("name") == "Verify exact checkout identity"
            )
            for script in (
                "candidate_ci.py checkout",
                "merge_completion.py ci-checkout",
            ):
                self.assertTrue(
                    any(script in s.get("run", "") for s in steps[:identity])
                )
        sharding = jobs["worktree-tests"]
        self.assertEqual(
            sharding["strategy"],
            {
                "fail-fast": "false",
                "max-parallel": "4",
                "matrix": {"shard": ["0", "1", "2", "3"]},
            },
        )
        command = sharding["steps"][-1]["run"]
        self.assertIn("--shard-count 4", command)
        self.assertIn("--pattern 'test_workflow_*.py'", command)
        harness = jobs["native-harness"]["steps"][-1]
        self.assertEqual(harness["env"]["GIT_CONFIG_NOSYSTEM"], "1")

    def test_split_lanes_preserve_the_original_pytest_dependency(self):
        jobs = workflow("skills-project-specs-ci.yml")["jobs"]
        original = next(
            step
            for step in jobs["verify"]["steps"]
            if step.get("name") == "Validate container software test helpers offline"
        )
        dependency = next(
            word for word in original["run"].split() if word.startswith("pytest==")
        )
        for name in ("workflow-adapters", "worktree-tests", "native-harness"):
            install = next(
                step
                for step in jobs[name]["steps"]
                if step.get("name") == "Install validation dependencies"
            )
            self.assertIn(dependency, install["run"].split())

    def test_helper_changes_trigger_skills_ci_and_match_merge_policy(self):
        value = workflow("skills-project-specs-ci.yml")
        policy = json.loads((ROOT / ".github/merge-policy.json").read_text())
        row = next(
            row
            for row in policy["ci_workflows"]
            if row["file"] == "skills-project-specs-ci.yml"
        )
        for event in ("pull_request", "push"):
            paths = value["on"][event]["paths"]
            self.assertEqual(set(paths), set(row["paths"]))
            self.assertIn(".github/scripts/run_unittest_shard.py", paths)
            self.assertIn(".github/scripts/test_ci_parallelism.py", paths)

    def test_vpn_build_is_independent_but_remains_a_required_gate(self):
        jobs = workflow("vpngw-ci.yml")["jobs"]
        self.assertEqual(jobs["build"]["needs"], ["merge-inputs"])
        for name in (
            "result-evidence",
            "candidate-evidence",
            "release-qualification-vpngw",
        ):
            self.assertTrue(
                {
                    "build",
                    "unit-tests",
                    "lint",
                    "ordinary-job-settlement",
                    "historical-package-upgrades",
                }
                <= set(jobs[name]["needs"])
            )


if __name__ == "__main__":
    unittest.main()
