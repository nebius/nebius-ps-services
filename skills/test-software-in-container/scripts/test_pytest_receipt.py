"""Execute the optional reporter against tiny independent pytest processes."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tsc_support.contracts import fingerprint, read_json
from tsc_support.suite_contracts import assess_receipt

SCRIPTS = Path(__file__).resolve().parent


@unittest.skipUnless(
    importlib.util.find_spec("pytest"), "optional pytest adapter requires pytest"
)
class PytestReporterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.settings = {
            "suite_sha256": fingerprint("suite"),
            "input_sha256": fingerprint("frozen input"),
            "selection_sha256": fingerprint(["test_sample.py"]),
            "target": "source",
            "origin_reference": "frozen/project",
        }
        self.sequence = 0

    def run_pytest(self, mode, content=None, conftest=None, flags=()):
        if content is not None:
            (self.project / "test_sample.py").write_text(content)
        if conftest is not None:
            (self.project / "conftest.py").write_text(conftest)
        self.sequence += 1
        out = self.root / f"result-{self.sequence}"
        settings = dict(self.settings, mode=mode, output=str(out))
        if mode == "run":
            settings["inventory"] = str(self.inventory_path)
            settings.update(case_id="synthetic-case", plan_sha256=fingerprint("plan"))
        config = self.root / f"settings-{self.sequence}.json"
        config.write_text(json.dumps(settings))
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "pytest",
                "-q",
                "-p",
                "tsc_support.pytest_receipt",
                "-p",
                "no:cacheprovider",
                *(["--collect-only"] if mode == "collect" else []),
                *flags,
                "test_sample.py",
            ],
            cwd=self.project,
            capture_output=True,
            text=True,
            timeout=30,
            env={
                **os.environ,
                "TSC_PYTEST_CONFIG": str(config),
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONPATH": str(SCRIPTS),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        if mode == "collect":
            self.inventory_path = out / "inventory.json"
        return result, out

    def assess(self, result, out):
        terminal = read_json(out / "terminal.json")
        self.assertFalse(
            terminal["process_completed"]
        )  # Plugin cannot attest process termination.
        terminal.update(process_completed=True, process_exit_code=result.returncode)
        inventory = read_json(self.inventory_path)
        suite = {
            "kind": "unit",
            "sha256": self.settings["suite_sha256"],
            "inventory": inventory,
        }
        return assess_receipt(suite, terminal, "source", self.settings["input_sha256"])

    def test_separate_collection_execution_and_process_observation(self):
        result, _ = self.run_pytest("collect", "def test_ok():\n    assert True\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        result, out = self.run_pytest("run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.assess(result, out)["status"], "PASS")
        self.assertTrue(list(out.glob("event-*.json")))
        self.assertEqual((out / "terminal.json").stat().st_mode & 0o777, 0o600)

    def test_parametrized_counts_not_test_file_counts(self):
        self.run_pytest(
            "collect",
            'import pytest\n@pytest.mark.parametrize("value", [1,2,3])\ndef test_ok(value):\n    assert value\n',
        )
        result, out = self.run_pytest("run")
        self.assertEqual(self.assess(result, out)["counts"]["passed"], 3)

    def test_project_contracts_module_is_not_shadowed(self):
        (self.project / "contracts.py").write_text("PROJECT_VALUE = 42\n")
        result, _ = self.run_pytest(
            "collect",
            "from contracts import PROJECT_VALUE\ndef test_value():\n    assert PROJECT_VALUE == 42\n",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        result, out = self.run_pytest("run")
        self.assertEqual(self.assess(result, out)["status"], "PASS")

    def test_setup_call_teardown_failures(self):
        templates = [
            "def test_bad():\n    assert False\n",
            'import pytest\n@pytest.fixture\ndef fx():\n    raise RuntimeError("setup")\ndef test_bad(fx):\n    pass\n',
            'import pytest\n@pytest.fixture\ndef fx():\n    yield\n    raise RuntimeError("teardown")\ndef test_bad(fx):\n    pass\n',
        ]
        for content in templates:
            self.run_pytest("collect", content)
            result, out = self.run_pytest("run")
            self.assertEqual(self.assess(result, out)["status"], "FAIL")

    def test_all_skipped_and_collection_skips_incomplete(self):
        self.run_pytest(
            "collect",
            'import pytest\n@pytest.mark.skip(reason="dependency")\ndef test_skip():\n    pass\n',
        )
        result, out = self.run_pytest("run")
        self.assertEqual(self.assess(result, out)["status"], "INCOMPLETE")
        self.run_pytest(
            "collect",
            'import os,pytest\nif os.environ.get("TSC_SKIP_MODULE"):\n    pytest.skip("capability", allow_module_level=True)\ndef test_ok():\n    pass\n',
        )
        saved = os.environ.get("TSC_SKIP_MODULE")
        try:
            os.environ["TSC_SKIP_MODULE"] = "1"
            result, out = self.run_pytest("run")
        finally:
            if saved is None:
                os.environ.pop("TSC_SKIP_MODULE", None)
            else:
                os.environ["TSC_SKIP_MODULE"] = saved
        self.assertEqual(self.assess(result, out)["status"], "INCOMPLETE")

    def test_empty_collection_incomplete(self):
        self.run_pytest("collect", "# no tests\n")
        result, out = self.run_pytest("run")
        self.assertEqual(self.assess(result, out)["status"], "INCOMPLETE")

    def test_late_finish_error_never_passes(self):
        self.run_pytest(
            "collect",
            "def test_ok():\n    pass\n",
            'import os,pytest\n@pytest.hookimpl(trylast=True)\ndef pytest_sessionfinish():\n    if os.environ.get("TSC_FAIL_FINISH"):\n        raise RuntimeError("finish")\n',
        )
        os.environ["TSC_FAIL_FINISH"] = "1"
        try:
            result, out = self.run_pytest("run")
        finally:
            del os.environ["TSC_FAIL_FINISH"]
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.assess(result, out)["status"], "FAIL")

    def test_unexpected_pass_and_unapproved_xfail_fail(self):
        for assertion in ("True", "False"):
            self.run_pytest(
                "collect",
                f'import pytest\n@pytest.mark.xfail(reason="known")\ndef test_x():\n    assert {assertion}\n',
            )
            result, out = self.run_pytest("run")
            self.assertEqual(self.assess(result, out)["status"], "FAIL")

    def test_collection_import_error_preserved_no_inventory(self):
        result, out = self.run_pytest(
            "collect", 'raise RuntimeError("import failed")\n'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.inventory_path.exists())
        self.assertEqual(
            read_json(out / "terminal.json")["errors"][0]["phase"], "collection"
        )

    def test_known_retry_plugin_rejected(self):
        self.run_pytest("collect", "def test_ok():\n    pass\n")
        (self.project / "pytest_repeat.py").write_text(
            "# synthetic retry plugin identity\n"
        )
        result, _ = self.run_pytest("run", flags=("-p", "pytest_repeat"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("retry/distributed", result.stderr)


if __name__ == "__main__":
    unittest.main()
