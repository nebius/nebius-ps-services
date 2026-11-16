"""Execute the frozen fixture harness and reject incomplete accounting."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "worktree/scripts"))
from workflow_task_container import assess
from workflow_validation import digest


class FixtureHarnessTests(unittest.TestCase):
    def test_final_candidate_cannot_weaken_frozen_deferred_harness(self):
        from three_tier_task_acceptance import verify_final_harness
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            def git(*args):
                return subprocess.check_output(["git", "-c", "commit.gpgsign=false", "-C", str(root), *args],
                                               stderr=subprocess.DEVNULL, text=True).strip()
            git("init", "-b", "candidate")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "test@example.com")
            harness = root / "project-a/acceptance_fixture/independent.py"
            harness.parent.mkdir(parents=True)
            harness.write_text("assert True\n")
            profile = {"harness": {"acceptance_fixture/independent.py": hashlib.sha256(harness.read_bytes()).hexdigest()}}
            git("add", "-A")
            git("commit", "-m", "frozen oracle")
            original = git("rev-parse", "HEAD")
            verify_final_harness(root, original, Path("project-a"), profile)
            harness.write_text("pass\n")
            git("add", "-A")
            git("commit", "-m", "weakened oracle")
            with self.assertRaises(RuntimeError):
                verify_final_harness(root, git("rev-parse", "HEAD"), Path("project-a"), profile)
            verify_final_harness(root, original, Path("project-a"), profile)

    def run_harness(self, product):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            harness = root / "acceptance_fixture/task_domain_acceptance.py"
            harness.parent.mkdir()
            harness.write_bytes((Path(__file__).resolve().parents[1] / "assets/task_domain_acceptance.py").read_bytes())
            (root / "tasks").mkdir()
            (root / "tasks/domain.py").write_text(product)
            output = root / "output.json"
            inventory_path = root / "inventory.json"
            check = {"suite_sha256": "a" * 64, "runner": {"name": "stdlib-acceptance", "version": "1"}}
            env = {**os.environ, "SDLC_ACCEPTANCE_OUTPUT": str(output), "SDLC_ACCEPTANCE_INVENTORY": str(inventory_path),
                   "SDLC_ACCEPTANCE_SUITE_SHA256": "a" * 64, "SDLC_ACCEPTANCE_SELECTION_SHA256": digest(check),
                   "SDLC_ACCEPTANCE_INPUT_SHA256": "b" * 64, "SDLC_ACCEPTANCE_CASE_ID": "trial",
                   "SDLC_ACCEPTANCE_PLAN_SHA256": digest(check)}
            collected = subprocess.run([sys.executable, "-B", str(harness), "collect", "title"], env=env, capture_output=True)
            self.assertEqual(collected.returncode, 0)
            inventory = json.loads(output.read_text())
            inventory_path.write_text(output.read_text())
            tested = subprocess.run([sys.executable, "-B", str(harness), "run", "title"], env=env, capture_output=True)
            receipt = json.loads(output.read_text())
            receipt.update(process_completed=True, process_exit_code=tested.returncode, interrupted=False)
            return check, inventory, receipt

    def test_frozen_harness_observes_real_product_assertions(self):
        check, inventory, receipt = self.run_harness(
            "def normalize_title(value):\n    value=value.strip()\n    if not value: raise ValueError()\n    return value\n")
        assessment = assess(check, inventory, receipt, "b" * 64, "trial")
        self.assertEqual(assessment["status"], "PASS")
        self.assertEqual(assessment["counts"]["passed"], 3)
        receipt["observed"].pop()
        self.assertNotEqual(assess(check, inventory, receipt, "b" * 64, "trial")["status"], "PASS")

    def test_frozen_harness_failure_cannot_be_a_pass_label(self):
        check, inventory, receipt = self.run_harness("def normalize_title(value): return value\n")
        self.assertEqual(assess(check, inventory, receipt, "b" * 64, "trial")["status"], "FAIL")
        receipt["completed"] = False
        self.assertNotEqual(assess(check, inventory, receipt, "b" * 64, "trial")["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
