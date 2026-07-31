#!/usr/bin/env python3
"""Tests for bounded container helper subprocess execution."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("container_runtime_common.py")
SPEC = importlib.util.spec_from_file_location("container_runtime_common", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class BoundedCommandTest(unittest.TestCase):
    def test_large_output_is_retained_only_to_the_configured_limit(self) -> None:
        result = MODULE.run_command(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 5000000)"],
            output_limit=1024,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.encode("utf-8")), 1024)
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.truncated)

    def test_timeout_is_reported_without_unbounded_wait(self) -> None:
        result = MODULE.run_command(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout=0.01,
        )

        self.assertEqual(result.returncode, 124)

    def test_command_runs_in_the_explicit_validated_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = MODULE.run_command(
                [
                    sys.executable,
                    "-c",
                    "import pathlib; print(pathlib.Path.cwd())",
                ],
                cwd=Path(directory),
            )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(Path(result.stdout.strip()), Path(directory).resolve())

    @unittest.skipUnless(os.name == "posix", "process groups are POSIX-specific")
    def test_timeout_terminates_descendant_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sentinel = Path(directory) / "child-survived"
            child_code = (
                "import pathlib,signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(0.2); "
                f"pathlib.Path({json.dumps(str(sentinel))}).write_text('alive')"
            )
            parent_code = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable, '-c', {json.dumps(child_code)}]); "
                "time.sleep(5)"
            )

            result = MODULE.run_command(
                [sys.executable, "-c", parent_code],
                timeout=0.05,
            )
            time.sleep(0.35)

            self.assertEqual(result.returncode, 124)
            self.assertFalse(sentinel.exists())


if __name__ == "__main__":
    unittest.main()
