"""Behavioral regressions for selective and bounded evidence helpers."""

import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import bounded_process
import collect_evidence as collect
import repeat_command as repeat


class AdaptiveHelpers(unittest.TestCase):
    def test_oversized_newline_free_stream_is_bounded_during_read(self):
        result = bounded_process.observe(
            [sys.executable, "-c", "import os; os.write(1,b'x'*2000000)"]
        )
        self.assertEqual(result["status"], "truncated")
        self.assertEqual(len(result["stdout"]), 65536)

    def test_inherited_pipe_cannot_outlive_deadline(self):
        start = time.monotonic()
        result = bounded_process.observe(
            [
                sys.executable,
                "-c",
                "import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])",
            ],
            timeout=0.2,
        )
        self.assertEqual(result["status"], "timeout")
        self.assertLess(time.monotonic() - start, 3)

    def test_detached_inherited_pipe_does_not_defeat_timeout(self):
        with tempfile.TemporaryDirectory() as temp:
            pidfile = Path(temp) / "child.pid"
            child = f"import os,time; open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(60)"
            parent = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True)"
            start = time.monotonic()
            try:
                result = repeat.run_once([sys.executable, "-c", parent], 0.3, 128)
                self.assertEqual(result["outcome"], "timeout")
                self.assertFalse(result["output_complete"])
                self.assertLess(time.monotonic() - start, 2)
            finally:
                if pidfile.exists():
                    os.killpg(int(pidfile.read_text()), signal.SIGKILL)

    def test_unsupported_process_groups_fail_before_launch(self):
        with (
            mock.patch.object(bounded_process.os, "name", "nt"),
            mock.patch.object(subprocess, "Popen") as launch,
        ):
            result = bounded_process.observe(["unused"])
        launch.assert_not_called()
        self.assertEqual(result["status"], "unavailable")

    def test_invalid_root_exits_nonzero_with_incomplete_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [
                    sys.executable,
                    collect.__file__,
                    "--root",
                    str(Path(temp) / "absent"),
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "failed")

    def test_deadline_skips_launch(self):
        with mock.patch.object(subprocess, "Popen") as launch:
            result = bounded_process.observe(["unused"], deadline=time.monotonic() - 1)
        launch.assert_not_called()
        self.assertEqual(result["status"], "skipped")

    def test_manifest_bounds_never_emit_partial_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pyproject.toml").write_bytes(b"x" * 32)
            (root / "requirements.txt").write_bytes(b"abc")
            with mock.patch.object(collect, "MAX_MANIFEST_BYTES", 10):
                result = collect.collect_manifests(root)
            by_name = {row["path"]: row for row in result}
            self.assertEqual(by_name["pyproject.toml"]["status"], "skipped")
            self.assertIsNone(by_name["pyproject.toml"]["sha256"])
            self.assertEqual(
                by_name["requirements.txt"]["sha256"],
                hashlib.sha256(b"abc").hexdigest(),
            )
            with mock.patch.object(collect, "MAX_MANIFEST_TOTAL", 1):
                self.assertTrue(
                    all(
                        row["sha256"] is None for row in collect.collect_manifests(root)
                    )
                )

    def test_changed_manifest_has_no_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "pyproject.toml"
            path.write_bytes(b"abc")
            original = os.fstat
            count = 0

            def changing(fd):
                nonlocal count
                count += 1
                if count == 2:
                    path.write_bytes(b"different")
                return original(fd)

            with mock.patch.object(os, "fstat", side_effect=changing):
                result = collect.collect_manifests(root)[0]
            self.assertEqual(result["status"], "changed-during-read")
            self.assertIsNone(result["sha256"])

    def test_explicit_sections_and_tools_do_not_probe_others(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            mock.patch.object(
                collect,
                "run_bounded",
                return_value={"status": "ok", "value": "version"},
            ) as probe,
        ):
            result = collect.build_evidence(
                Path(temp), ["locale", "filesystem"], ["python3", "python3"]
            )
        self.assertNotIn("repository", result)
        self.assertEqual(
            result["filesystem"]["probe_scope"], "system_temporary_directory"
        )
        probe.assert_called_once()

    def test_interrupted_trial_is_retained_and_not_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "result.json"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(repeat.__file__)),
                    "--runs",
                    "3",
                    "--out",
                    str(output),
                    "--",
                    sys.executable,
                    "-c",
                    "import time; time.sleep(60)",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.3)
            process.send_signal(signal.SIGINT)
            self.assertEqual(process.wait(timeout=5), 130)
            result = json.loads(output.read_text())
        self.assertEqual(result["attempted_runs"], 1)
        self.assertEqual(result["launched_runs"], 1)
        self.assertEqual(result["completed_runs"], 0)
        self.assertEqual(result["interrupted_runs"], 1)
        self.assertIsNone(result["pass_rate"])

    def test_outcomes_do_not_mix_launch_errors_with_failures(self):
        rows = [
            {"outcome": outcome, "duration_seconds": duration, "signature": outcome}
            for outcome, duration in (
                ("launch_error", None),
                ("timeout", 2),
                ("nonzero", 1),
                ("pass", 0.5),
                ("interrupted", 0.1),
            )
        ]
        result = repeat.summarize(["test"], 5, rows, True)
        self.assertEqual(result["completed_runs"], 3)
        self.assertEqual(result["failure_count"], 2)
        self.assertEqual(result["timing_seconds"]["min"], 0.5)
        self.assertEqual(result["timing_by_outcome"]["interrupted"]["min"], 0.1)
        self.assertEqual(len(result["signature_clusters"]), 5)


if __name__ == "__main__":
    unittest.main()
