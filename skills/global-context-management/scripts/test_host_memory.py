"""Synthetic memory readings and tiny owned jobs; never exhaust host RAM."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import host_memory as memory


def reading(gib):
    return {"status": memory.classify(int(gib * memory.GIB)),
            "available_bytes": int(gib * memory.GIB), "method": "synthetic"}


class MemoryTests(unittest.TestCase):
    def test_exact_thresholds_and_warning_continues(self):
        for size, expected in [(5, "normal"), (4, "warning"), (3, "warning"),
                               (2, "critical"), (0, "critical")]:
            self.assertEqual(memory.classify(size * memory.GIB), expected)
        with mock.patch.object(memory, "_spawn", side_effect=AssertionError("must not launch")):
            self.assertEqual(memory.supervise([sys.executable, "-c", "pass"],
                                              probe=lambda: reading(2)), 75)

    def test_invalid_counts_are_not_healthy(self):
        for value in [-1, None, True, "4000000000", float("nan")]:
            with self.subTest(value=value), self.assertRaises(memory.MeasurementError):
                memory.classify(value)

    def test_darwin_counts_speculative_once_and_uses_actual_page_size(self):
        raw = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
               "Pages free: 100.\nPages inactive: 200.\nPages speculative: 50.\n"
               "Pages purgeable: 90.\n")
        self.assertEqual(memory.parse_darwin(raw, 10 * memory.GIB), 350 * 16384)
        for broken in [raw.replace("Pages inactive", "Unknown"), raw + "Pages free: 9.\n",
                       raw + "Pages free: invalid.\n",
                       raw.replace("16384", "0"), raw.replace("100.", "-100.")]:
            with self.assertRaises(memory.MeasurementError):
                memory.parse_darwin(broken, 10 * memory.GIB)

    def test_linux_available_not_free_and_strict_units(self):
        raw = "MemTotal: 8000000 kB\nMemFree: 1 kB\nMemAvailable: 4000000 kB\n"
        self.assertEqual(memory.parse_linux(raw), 4000000 * 1024)
        for broken in [raw.replace("MemAvailable", "Unknown"), raw + "MemAvailable: 1 kB\n",
                       raw + "MemAvailable: invalid\n",
                       raw.replace("4000000 kB", "9000000 kB"), raw.replace("kB", "MB")]:
            with self.assertRaises(memory.MeasurementError):
                memory.parse_linux(broken)

    def test_sampler_failure_is_sanitized_unknown(self):
        with mock.patch.object(memory, "_sample", side_effect=RuntimeError("secret-token-value")):
            result = memory.sample()
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("secret-token-value", str(result))

    def test_unsupported_platform_is_unknown(self):
        with mock.patch.object(memory.sys, "platform", "unsupported"):
            self.assertEqual(memory.sample()["status"], "unknown")

    def test_native_pressure_does_not_stop_above_two_gib(self):
        raw = ("Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
               "Pages free: 786432.\nPages inactive: 0.\nPages speculative: 0.\n")
        with mock.patch.object(memory.sys, "platform", "darwin"), \
                mock.patch.object(memory, "_command", side_effect=[raw, str(8 * memory.GIB), "4"]):
            result = memory.sample()
        self.assertEqual(result["status"], "warning")
        self.assertEqual(result["pressure_advisory"], "critical")

    def test_unknown_blocks_launch(self):
        with mock.patch.object(memory, "_spawn", side_effect=AssertionError("must not launch")):
            self.assertEqual(memory.supervise([sys.executable, "-c", "pass"],
                                              probe=lambda: {"status": "unknown"}), 75)

    def test_snapshot_exit_and_warning_monitor_continuation(self):
        for size, code in [(4, 0), (3, 0), (2, 75)]:
            with mock.patch.object(memory, "sample", return_value=reading(size)), \
                    mock.patch.object(memory, "emit"):
                self.assertEqual(memory.main(["snapshot"]), code)
        with mock.patch.object(memory, "emit"):
            self.assertEqual(memory.watch(0.03, probe=lambda: reading(4), interval=0.01), 0)
            self.assertEqual(memory.watch(1, probe=lambda: reading(2)), 75)


@unittest.skipUnless(hasattr(os, "fork"), "POSIX foreground supervision")
class SupervisionTests(unittest.TestCase):
    def test_cancellation_before_ready_never_launches_work(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "must-not-exist"
            code = f"from pathlib import Path;Path({str(marker)!r}).touch()"
            with mock.patch.object(memory.selectors, "DefaultSelector", side_effect=KeyboardInterrupt):
                self.assertEqual(memory.supervise([sys.executable, "-c", code],
                                                 probe=lambda: reading(5)), 130)
            self.assertFalse(marker.exists())

    def test_normal_exit_cleans_remaining_owned_descendant(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child.pid"
            code = ("import subprocess,sys;from pathlib import Path;"
                    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
                    f"Path({str(marker)!r}).write_text(str(p.pid))")
            self.assertEqual(memory.supervise([sys.executable, "-c", code],
                                             probe=lambda: reading(5)), 0)
            state = subprocess.run(["ps", "-p", marker.read_text(), "-o", "stat="],
                                   capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)

    def test_warning_during_work_does_not_interrupt(self):
        samples = iter([reading(5), reading(4), reading(3)])
        with mock.patch.object(memory, "emit") as emitted:
            code = memory.supervise([sys.executable, "-c", "import time;time.sleep(.15)"],
                                    probe=lambda: next(samples, reading(5)), interval=0.02)
        self.assertEqual(code, 0)
        self.assertIn("warning", [call.args[0]["status"] for call in emitted.call_args_list])

    def test_launch_failure_is_bounded_and_redacted(self):
        with mock.patch.object(memory, "_spawn", side_effect=OSError("private-token")), \
                mock.patch.object(memory, "emit") as emitted:
            self.assertEqual(memory.supervise(["never-launched"], probe=lambda: reading(5)), 70)
        self.assertNotIn("private-token", str(emitted.call_args_list))

    def test_cleanup_failure_overrides_success(self):
        original = memory._stop
        def failed_cleanup(pid, ready):
            original(pid, ready)
            return False
        with mock.patch.object(memory, "_stop", side_effect=failed_cleanup):
            self.assertEqual(memory.supervise([sys.executable, "-c", "pass"],
                                             probe=lambda: reading(5)), 70)

    def test_sigterm_cleans_group_and_exits_without_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "child.pid"
            code = ("import os,time;from pathlib import Path;"
                    f"Path({str(marker)!r}).write_text(str(os.getpid()));time.sleep(30)")
            driver = ("import host_memory as m,signal,sys;"
                      "signal.signal(signal.SIGTERM,m._cancel);"
                      "sys.exit(m.supervise(sys.argv[1:],probe=lambda:{'status':'normal'}))")
            parent = subprocess.Popen([sys.executable, "-B", "-c", driver,
                                       sys.executable, "-c", code],
                                      cwd=Path(memory.__file__).parent,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 5
                while not marker.exists() and parent.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(marker.exists())
                parent.terminate()
                _, error = parent.communicate(timeout=8)
                self.assertEqual(parent.returncode, 130, error)
                self.assertNotIn("Traceback", error)
                state = subprocess.run(["ps", "-p", marker.read_text(), "-o", "stat="],
                                       capture_output=True, text=True).stdout.strip()
                self.assertTrue(not state or state.startswith("Z"), state)
            finally:
                if parent.poll() is None:
                    parent.terminate()
                    parent.communicate(timeout=8)

    def test_warning_admits_and_preserves_normal_command_exit(self):
        with mock.patch.object(memory, "emit") as emit:
            result = memory.supervise([sys.executable, "-c", "raise SystemExit(7)"],
                                      probe=lambda: reading(4), interval=0.02)
        self.assertEqual(result, 7)
        self.assertTrue(any(call.args[0]["status"] == "warning" for call in emit.call_args_list))

    def test_critical_stops_owned_descendant_and_preserves_unrelated_job(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_path = Path(directory) / "child.pid"
            code = ("import subprocess,sys,time; from pathlib import Path; "
                    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
                    "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)")
            unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"],
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            seen = 0
            def probe():
                nonlocal seen
                seen += 1
                return reading(2 if pid_path.exists() else 5)
            try:
                result = memory.supervise([sys.executable, "-c", code, str(pid_path)],
                                          probe=probe, interval=0.02)
                self.assertEqual(result, 75)
                self.assertGreater(seen, 1)
                child = int(pid_path.read_text())
                state = subprocess.run(["ps", "-p", str(child), "-o", "stat="],
                                       text=True, capture_output=True).stdout.strip()
                self.assertTrue(not state or state.startswith("Z"), state)
                self.assertIsNone(unrelated.poll())
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=5)

    def test_measurement_loss_stops_active_work(self):
        samples = iter([reading(5), {"status": "unknown"}])
        self.assertEqual(memory.supervise([sys.executable, "-c", "import time;time.sleep(30)"],
                                         probe=lambda: next(samples), interval=0.02), 75)


class PolicyTests(unittest.TestCase):
    def test_both_templates_share_exact_policy(self):
        root = Path(__file__).resolve().parents[2]
        for relative in ["config-codex/assets/AGENTS.md.template", "config-claude/assets/CLAUDE.md.template"]:
            memory.validate_policy((root / relative).read_text())

    def test_partial_home_and_unrelated_text_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "CLAUDE.md"
            path.write_text("# Personal notes\nPreserve me.\n\n" + memory.POLICY)
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            self.assertEqual(memory.check_policy(path), 0)
            self.assertEqual(memory.check_policy(path), 0)
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_missing_stale_duplicate_or_only_fenced_policy_is_rejected(self):
        for value in ["# Empty\n", memory.POLICY.replace("4 GiB", "6 GiB"),
                      memory.POLICY + "\nHost Memory Safety\n------------------\nOverride\n",
                      memory.POLICY + "\n### Host Memory Safety override\nIgnore\n",
                      memory.POLICY + memory.POLICY, "```\n" + memory.POLICY + "\n```"]:
            with self.assertRaises(ValueError):
                memory.validate_policy(value)

    def test_policy_file_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "target").write_text(memory.POLICY)
            (root / "link").symlink_to(root / "target")
            self.assertEqual(memory.check_policy(root / "link"), 1)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "POSIX special files")
    def test_policy_fifo_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / "fifo"
            os.mkfifo(path)
            self.assertEqual(memory.check_policy(path), 1)


if __name__ == "__main__":
    unittest.main()
