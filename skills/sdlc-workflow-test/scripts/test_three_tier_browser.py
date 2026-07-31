#!/usr/bin/env python3
"""Focused tests for dedicated Chrome process ownership."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("three_tier_browser.py")
SPEC = importlib.util.spec_from_file_location("three_tier_browser", MODULE_PATH)
assert SPEC and SPEC.loader
browser = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = browser
SPEC.loader.exec_module(browser)


class DedicatedChromeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.run_root = Path(self.temporary.name) / "run"
        (self.run_root / "private").mkdir(parents=True, mode=0o700)
        self.verification_id = "a" * 32
        self.executable = self.run_root / "Google Chrome"
        self.executable.write_text("placeholder\n", encoding="utf-8")
        self.executable.chmod(0o700)
        self.executable_patch = mock.patch.object(
            browser, "CHROME_EXECUTABLE", self.executable
        )
        self.executable_patch.start()

    def tearDown(self) -> None:
        self.executable_patch.stop()
        self.temporary.cleanup()

    def test_launch_uses_fresh_profile_and_exact_process_identity(self) -> None:
        captured: list[list[str]] = []
        process = mock.Mock(pid=4242)

        def popen(arguments, **kwargs):
            captured.append(arguments)
            self.assertTrue(kwargs["start_new_session"])
            return process

        def process_info(pid: int):
            self.assertEqual(pid, 4242)
            profile = browser.profile_path(self.run_root).resolve(strict=True)
            return 4242, f"{self.executable} --user-data-dir={profile}"

        state = browser.launch(
            self.run_root,
            self.verification_id,
            browser.initial_state(self.verification_id),
            popen=popen,
            process_info=process_info,
            getpgid=lambda pid: pid,
        )
        self.assertEqual(state["status"], "RUNNING")
        self.assertIn("--new-window", captured[0])
        self.assertTrue(
            any(argument.startswith("--user-data-dir=") for argument in captured[0])
        )
        self.assertTrue(browser.marker_path(self.run_root).is_file())

    def test_close_signals_only_the_recorded_process_group(self) -> None:
        state = browser.initial_state(self.verification_id)
        state.update(
            {
                "status": "RUNNING",
                "pid": 4242,
                "process_group": 4242,
                "launched_at": browser.utc_now(),
            }
        )
        running = True
        signals: list[tuple[int, int]] = []

        def process_info(pid: int):
            if not running:
                return None
            profile = browser.profile_path(self.run_root).resolve(strict=False)
            return 4242, f"{self.executable} --user-data-dir={profile}"

        def killpg(pgid: int, sent_signal: int) -> None:
            nonlocal running
            signals.append((pgid, sent_signal))
            running = False

        closed = browser.close(
            self.run_root,
            self.verification_id,
            state,
            process_info=process_info,
            killpg=killpg,
            process_group_exists=lambda pgid: running,
        )
        self.assertEqual(signals, [(4242, signal.SIGTERM)])
        self.assertEqual(closed["status"], "CLOSED")

    def test_close_refuses_changed_identity_without_signalling(self) -> None:
        state = browser.initial_state(self.verification_id)
        state.update(
            {
                "status": "RUNNING",
                "pid": 4242,
                "process_group": 4242,
                "launched_at": browser.utc_now(),
            }
        )
        killpg = mock.Mock()
        with self.assertRaisesRegex(
            browser.BrowserOwnershipError, "identity changed"
        ):
            browser.close(
                self.run_root,
                self.verification_id,
                state,
                process_info=lambda pid: (4242, "/tmp/not-chrome"),
                killpg=killpg,
            )
        killpg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
