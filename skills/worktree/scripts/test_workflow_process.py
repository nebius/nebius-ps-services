"""Real child-process checks for bounded, progress-aware supervision."""
import hashlib
import os
import sys
import signal
import subprocess
import json
import threading
import time
import unittest
from unittest.mock import patch

from workflow_process import PipeCapture, Capture, ProcessCleanupError, execution_policy, run_owned


class ProcessTests(unittest.TestCase):
    def run_child(self, code, **kwargs):
        return run_owned([sys.executable, "-I", "-c", code], **kwargs)

    def test_external_owner_pipe_capture_is_bounded_and_hashes_complete_streams(self):
        process = subprocess.Popen([sys.executable, "-I", "-c",
            "import os\nfor i in range(64):\n os.write(1,b'x'*65536)\n os.write(2,b'y'*65536)"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        capture = PipeCapture(process)
        try:
            deadline = time.monotonic() + 5  # Declared negative-control test limit.
            while process.poll() is None and time.monotonic() < deadline:
                capture.drain(.01)
            self.assertEqual(process.poll(), 0)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            result = capture.finish()
        self.assertLess(len(json.dumps(result)), 1024)
        for name, byte in (("stdout", b"x"), ("stderr", b"y")):
            self.assertEqual(result[name]["bytes"], 64 * 65536)
            self.assertEqual(result[name]["sha256"], hashlib.sha256(byte * 64 * 65536).hexdigest())
            self.assertTrue(result[name]["complete"])
            self.assertNotIn("data", result[name])

    def test_external_owner_capture_reports_incomplete_pipe_without_waiting_for_writer(self):
        read_fd, write_fd = os.pipe()
        second_read, second_write = os.pipe()
        class Pipes:
            stdout = os.fdopen(read_fd, "rb")
            stderr = os.fdopen(second_read, "rb")
        capture = PipeCapture(Pipes())
        try:
            result = capture.finish()
            self.assertFalse(result["stdout"]["complete"])
            self.assertFalse(result["stderr"]["complete"])
        finally:
            os.close(write_fd)
            os.close(second_write)

    def test_silent_work_and_unknown_health_continue(self):
        result = self.run_child("import time; time.sleep(.15)",
                                control=lambda sample: "health_unknown")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stop_reason"], "exited")
        self.assertEqual(result["observation_gaps"], 1)
        self.assertTrue(result["local_group_quiescent"])

    def test_flood_both_streams_hashes_every_byte_without_retaining_output(self):
        result = self.run_child("import os\nfor i in range(100):\n os.write(1,b'x'*65536)\n os.write(2,b'y'*65536)")
        for stream, byte in (("stdout", b"x"), ("stderr", b"y")):
            self.assertEqual(result[stream]["bytes"], 6553600)
            self.assertEqual(result[stream]["sha256"], hashlib.sha256(byte * 6553600).hexdigest())
            self.assertTrue(result[stream]["complete"])
            self.assertIsNone(result[stream]["data"])
        self.assertEqual(result["exit_code"], 0)

    def test_structured_overflow_never_returns_prefix_for_parsing(self):
        result = self.run_child("print('x'*10000)", stdout_capture=Capture(32))
        self.assertTrue(result["stdout"]["overflow"])
        self.assertIsNone(result["stdout"]["data"])
        self.assertEqual(result["stdout"]["bytes"], 10001)

    def test_complete_binary_protocol_preserves_split_utf8(self):
        result = self.run_child("import os; os.write(1,b'\\xe2'); os.write(1,b'\\x82\\xac')",
                                stdout_capture=Capture(32))
        self.assertEqual(result["stdout"]["data"].decode(), "€")

    def test_cancellation_proves_local_group_absence(self):
        result = self.run_child("import time; time.sleep(100)", control=lambda sample: "cancelled")
        self.assertEqual(result["stop_reason"], "cancelled")
        self.assertTrue(result["local_group_quiescent"])

    def test_direct_child_exit_cleans_pipe_holding_descendant(self):
        result = self.run_child("import subprocess,sys; subprocess.Popen([sys.executable,'-c','import time; time.sleep(100)'])")
        self.assertEqual(result["exit_code"], 0)
        self.assertTrue(result["local_group_quiescent"])
        self.assertTrue(result["stdout"]["complete"])

    def test_probe_timeout_is_unknown_not_cancellation(self):
        def unavailable(sample):
            raise TimeoutError()
        result = self.run_child("import time; time.sleep(.1)", control=unavailable)
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["observation_gaps"], 1)

    def test_bound_deadline_and_validation(self):
        with self.assertRaises(ValueError):
            execution_policy({"observation_interval_seconds": 30, "deadline": {"seconds": 1}})
        result = self.run_child("import time; time.sleep(100)", policy={
            "observation_interval_seconds": 1,
            "deadline": {"seconds": 1, "basis": "negative_control", "reference": "hung-child"}},
            deadline_authority={"seconds": 1, "basis": "negative_control", "reference": "hung-child"})
        self.assertEqual(result["stop_reason"], "bound_deadline")
        self.assertTrue(result["local_group_quiescent"])

    def test_launch_failure_preserves_exception(self):
        with self.assertRaises(FileNotFoundError):
            run_owned(["/nonexistent/workflow-process-test"])

    def test_cleanup_uncertainty_has_explicit_evidence(self):
        with patch("workflow_process.OwnedProcessGroup.verify_absent", side_effect=ProcessCleanupError("unknown")):
            with self.assertRaises(ProcessCleanupError) as raised:
                self.run_child("pass")
        self.assertFalse(raised.exception.observation["local_group_quiescent"])

    def test_cancel_signal_error_still_proves_owned_group_cleanup(self):
        original = os.killpg
        def signal_group(group, sig):
            if sig == signal.SIGINT:
                raise OSError("grace signal failed")
            return original(group, sig)
        with patch("workflow_process.os.killpg", side_effect=signal_group):
            with self.assertRaises(OSError) as raised:
                self.run_child("import time; time.sleep(100)", control=lambda sample: "cancelled")
        self.assertTrue(raised.exception.observation["local_group_quiescent"])

    def test_slow_probe_does_not_block_output_and_is_joined(self):
        done = threading.Event()
        def probe(sample):
            time.sleep(.25)
            done.set()
            return "health_unknown"
        result = self.run_child("import os; os.write(1,b'x'*1048576)", control=probe)
        self.assertEqual(result["stdout"]["bytes"], 1048576)
        self.assertTrue(result["stdout"]["complete"])
        self.assertTrue(done.is_set())
        self.assertAlmostEqual(sum(result["timings"].values()), result["elapsed_seconds"], places=4)


if __name__ == "__main__":
    unittest.main()
