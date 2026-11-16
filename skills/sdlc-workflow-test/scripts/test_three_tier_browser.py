#!/usr/bin/env python3
"""Ownership and integrity tests for headless stage execution (no browser needed)."""

from pathlib import Path
import tempfile
import json
import sys
import subprocess
import hashlib
import unittest
from unittest import mock

import three_tier_browser as browser


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.id = "a" * 32
        self.state = {
            "run_root": str(self.root),
            "verification_id": self.id,
            "browser_instance": browser.initial_state(self.id),
            "browser_stages": [],
            "environment": {},
        }
        self.state["browser_bundle"] = browser.freeze_bundle(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_private_json_publishes_complete_bytes_and_rejects_replacement(self):
        import json

        target = self.root / "response.json"
        browser.private_json(target, {"ok": True})
        self.assertEqual(json.loads(target.read_text()), {"ok": True})
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "already exists"):
            browser.private_json(target, {"ok": False})
        self.assertEqual(list(self.root.glob(".publish-*")), [])

    def test_frozen_oracle_rejects_changed_code(self):
        bundle = browser.verify_bundle(self.state)
        (bundle / "acceptance.spec.mjs").write_text("changed")
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "oracle changed"):
            browser.verify_bundle(self.state)

    def test_frozen_oracle_rejects_changed_completion_control(self):
        bundle = browser.verify_bundle(self.state)
        (bundle / "completion-control.mjs").write_text("changed")
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "oracle changed"):
            browser.verify_bundle(self.state)

    def test_frozen_oracle_rejects_previous_bundle_without_control(self):
        self.state["browser_bundle"].pop("completion-control.mjs")
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "oracle is missing"):
            browser.verify_bundle(self.state)

    def test_frozen_oracle_rejects_symlink(self):
        bundle = browser.verify_bundle(self.state)
        item = bundle / "package.json"
        item.unlink()
        item.symlink_to(browser.ASSETS / "package.json")
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "symlink"):
            browser.verify_bundle(self.state)

    def running(self):
        return {
            **browser.initial_state(self.id),
            "status": "RUNNING",
            "active": {"attempt_id": "b" * 32, "pid": 12345},
        }

    def test_cleanup_refuses_reused_pid(self):
        with (
            mock.patch.object(
                browser,
                "_processes",
                return_value=[(12345, 12345, "unrelated user browser")],
            ),
            mock.patch.object(browser.os, "killpg") as kill,
        ):
            with self.assertRaisesRegex(
                browser.BrowserOwnershipError, "identity changed"
            ):
                browser.close(self.root, self.id, self.running())
            kill.assert_not_called()

    def test_cleanup_signals_only_owned_runner_and_chrome(self):
        attempt = self.root / "private/browser" / ("b" * 32)
        processes = [
            (
                12345,
                12345,
                "node test --config " + str(attempt / "playwright.config.mjs"),
            ),
            (
                12346,
                12346,
                "chrome --headless --user-data-dir="
                + str(attempt / "tmp/playwright_profile"),
            ),
            (
                12347,
                12346,
                "chrome --type=utility --user-data-dir="
                + str(attempt / "tmp/playwright_profile"),
            ),
            (44444, 44444, "user Chrome"),
        ]
        with (
            mock.patch.object(browser, "_processes", side_effect=[processes, [], []]),
            mock.patch.object(browser.os, "killpg") as kill,
        ):
            result = browser.close(self.root, self.id, self.running())
        self.assertEqual(result["status"], "CLOSED")
        self.assertEqual({call.args[0] for call in kill.call_args_list}, {12345, 12346})

    def test_cleanup_waits_for_exit_without_signaling_a_cleared_argv(self):
        attempt = self.root / "private/browser" / ("b" * 32)
        owned = [
            (12345, 12345, "node --config " + str(attempt / "playwright.config.mjs"))
        ]
        exiting = [(12345, 12345, "(node)")]
        with (
            mock.patch.object(browser, "_processes", side_effect=[owned, exiting, []]),
            mock.patch.object(browser.os, "killpg") as kill,
        ):
            self.assertEqual(
                browser.close(self.root, self.id, self.running())["status"], "CLOSED"
            )
            self.assertEqual(kill.call_count, 1)

    def test_process_inventory_excludes_exited_zombies(self):
        result = mock.Mock(
            returncode=0, stdout="123 123 Z <defunct>\n124 124 S chrome --headless\n"
        )
        with mock.patch.object(browser.subprocess, "run", return_value=result):
            self.assertEqual(browser._processes(), [(124, 124, "chrome --headless")])

    def test_absent_process_cleanup_is_idempotent(self):
        with (
            mock.patch.object(browser, "_processes", return_value=[]),
            mock.patch.object(browser.os, "killpg") as kill,
        ):
            result = browser.close(self.root, self.id, self.running())
            self.assertEqual(browser.close(self.root, self.id, result), result)
            kill.assert_not_called()

    def test_runner_streams_are_bounded_and_complete_before_stage_pass(self):
        bundle = browser.verify_bundle(self.state)
        cli = bundle / "node_modules/@playwright/test/cli.js"
        cli.parent.mkdir(parents=True)
        cli.touch()
        real_popen = subprocess.Popen
        script = """import os,json,pathlib
for i in range(32):
 os.write(1,b'x'*65536)
 os.write(2,b'y'*65536)
p=pathlib.Path(os.environ['SDLC_BROWSER_OUTPUT'])
v=json.loads(pathlib.Path(os.environ['SDLC_BROWSER_INPUT']).read_text())
(p/'observations.json').write_text(json.dumps({'attempt_id':v['attempt_id'],'headless':True,'browser_version':'fixture','actions':[{'action':'close-browser'}]}))
"""
        def launch(_argv, **kwargs):
            if _argv[0] != "node":
                return real_popen(_argv, **kwargs)
            return real_popen([sys.executable, "-I", "-c", script], **kwargs)
        with mock.patch.object(browser.subprocess, "Popen", side_effect=launch):
            result = browser.run_stage(self.state, "capability-discovery", {}, persist=lambda: None, checkpoint=lambda *args: None)
        output = self.root / "evidence/gui-uat" / result["attempt_id"]
        value = json.loads((output / "runner-output.json").read_text())
        self.assertEqual(result["outcome"], "PASS")
        self.assertFalse((output / "runner.log").exists())
        self.assertLess((output / "runner-output.json").stat().st_size, 1024)
        self.assertEqual(value["stdout"]["bytes"], 32 * 65536)
        self.assertEqual(value["stderr"]["sha256"], hashlib.sha256(b'y' * 32 * 65536).hexdigest())
        self.assertTrue(all(row["complete"] for row in value.values()))

    def test_install_failure_is_preserved_without_success_claim(self):
        with mock.patch.object(
            browser, "install_dependencies", return_value=mock.Mock(returncode=1)
        ):
            with self.assertRaisesRegex(
                browser.BrowserOwnershipError, "installation failed"
            ):
                browser.run_stage(
                    self.state,
                    "capability-discovery",
                    {},
                    persist=lambda: None,
                    checkpoint=lambda *args: None,
                )
        self.assertEqual(self.state["browser_stages"][0]["outcome"], "FAIL")
        self.assertEqual(self.state["environment"]["headless_browser"], "FAIL")
        self.assertTrue((self.root / self.state["browser_stages"][0]["path"]).is_file())

    def test_incomplete_dependency_writer_blocks_browser_and_cleanup(self):
        from verification_suites import scheduler
        process = scheduler().source_module("workflow_process", Path(browser.__file__).resolve().parents[2] / "worktree/scripts/workflow_process.py")
        result = {"exit_code": 0, "stop_reason": "exited", "local_group_quiescent": False,
                  "stdout": {"complete": False}, "stderr": {"complete": True}}
        with mock.patch.object(process, "run_owned", return_value=result), mock.patch.object(browser.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(browser.BrowserOwnershipError, "unresolved"):
                browser.run_stage(self.state, "capability-discovery", {}, persist=lambda: None, checkpoint=lambda *args: None)
        launch.assert_not_called()
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "unresolved"):
            browser.close(self.root, self.id, self.state["browser_instance"])

    def test_recovery_preserves_interrupted_attempt_and_is_idempotent(self):
        self.state["browser_instance"] = self.running()
        attempt = self.state["browser_instance"]["active"]["attempt_id"]
        private = self.root / "private/browser" / attempt
        evidence = self.root / "evidence/gui-uat" / attempt
        private.mkdir(parents=True)
        evidence.mkdir(parents=True)
        browser.private_json(
            private / "input.json",
            {
                "attempt_id": attempt,
                "verification_id": self.id,
                "stage": "capability-discovery",
            },
        )
        with mock.patch.object(browser, "_processes", return_value=[]):
            browser.recover_interrupted_stage(self.state)
            browser.recover_interrupted_stage(self.state)
        self.assertEqual(self.state["browser_instance"]["status"], "CLOSED")
        self.assertEqual(len(self.state["browser_stages"]), 1)
        self.assertEqual(self.state["browser_stages"][0]["outcome"], "FAIL")
        self.assertEqual(self.state["environment"]["headless_browser"], "FAIL")
        with self.assertRaisesRegex(
            browser.BrowserOwnershipError, "failed browser attempt"
        ):
            browser.validate_receipts(self.state)

    def test_no_receipts_cannot_pass(self):
        with self.assertRaisesRegex(browser.BrowserOwnershipError, "four ordered"):
            browser.validate_receipts(self.state)

    def test_stage_history_allows_bounded_evaluation_only_before_uat(self):
        for count in range(1, 6):
            browser.validate_stage_order(["capability-discovery"] + ["evaluate"] * count
                                         + ["uat-before-restart", "uat-after-restart"], complete=True)
        for stages in (["evaluate"], ["capability-discovery", "uat-before-restart"],
                       ["capability-discovery"] + ["evaluate"] * 6,
                       list(browser.STAGES) + ["evaluate"],
                       list(browser.STAGES) + ["uat-after-restart"],
                       ["capability-discovery", "capability-discovery"]):
            with self.subTest(stages=stages), self.assertRaises(browser.BrowserOwnershipError):
                browser.validate_stage_order(stages)


if __name__ == "__main__":
    unittest.main()
