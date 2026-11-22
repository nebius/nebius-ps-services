"""Regression proofs for safe evidence presentation and collection completion."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import collect_evidence as collect
import compare_evidence as compare
import evidence_redaction as redaction
import repeat_command as repeat


class ReviewRegressions(unittest.TestCase):
    def test_truncated_streams_cannot_disclose_unlabelled_secret_fragments(self):
        samples = (
            "password=synthetic123\n",
            "-----BEGIN PRIVATE KEY-----\nsynthetic123\n-----END PRIVATE KEY-----\n",
        )
        for stream in ("stdout", "stderr"):
            for sample in samples:
                for limit in (1, 15, 42):
                    if limit >= len(sample):
                        continue
                    with self.subTest(stream=stream, sample=sample, limit=limit):
                        row = repeat.run_once(
                            [
                                sys.executable,
                                "-c",
                                f"import sys; sys.{stream}.write({sample!r})",
                            ],
                            5,
                            limit,
                        )
                        self.assertEqual(row[f"{stream}_tail"], "")
                        self.assertTrue(row[f"{stream}_truncated"])
                        self.assertEqual(row[f"{stream}_bytes_seen"], len(sample))
        complete = repeat.run_once(
            [sys.executable, "-c", "print('safe diagnostic')"], 5, 128
        )
        self.assertEqual(complete["stdout_tail"], "safe diagnostic\n")

    def test_missing_requested_tool_exits_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [
                    sys.executable,
                    collect.__file__,
                    "--root",
                    temp,
                    "--section",
                    "platform",
                    "--tool",
                    "bash",
                ],
                env={**os.environ, "PATH": ""},
                capture_output=True,
                text=True,
                timeout=5,
            )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            json.loads(result.stdout)["tools"]["bash"]["status"], "unavailable"
        )

    def test_nested_incomplete_observations_control_exit_without_losing_payload(self):
        complete = {"platform": {"system": "TestOS"}, "tools": {}}
        incomplete = (
            {"tools": {"bash": {"status": "failed", "value": None}}},
            {"repository": {"git": {"detection_available": False}}},
            {
                "repository": {
                    "git": {
                        "detection_available": True,
                        "is_work_tree": True,
                        "status_available": False,
                    }
                }
            },
            {"repository": {"manifests": [{"status": "skipped", "sha256": None}]}},
            {"repository": {"manifests": [{"status": "truncated", "sha256": None}]}},
            {"resource_limits": {"supported": False}},
        )
        for payload in (complete, *incomplete):
            with (
                self.subTest(payload=payload),
                mock.patch.object(sys, "argv", [collect.__file__]),
                mock.patch.object(
                    collect,
                    "observe",
                    return_value={
                        "status": "ok",
                        "stdout": json.dumps(payload).encode(),
                    },
                ),
                mock.patch.object(sys, "stdout", new_callable=io.StringIO) as output,
            ):
                code = collect.main()
                self.assertEqual(code, 0 if payload is complete else 2)
                self.assertEqual(json.loads(output.getvalue()), payload)

    def test_nested_keys_are_redacted_for_all_structural_changes(self):
        subtree = {"node.internal": 1, "password=synthetic123": "hidden"}
        for before, after in (
            ({}, {"peers": subtree}),
            ({"peers": subtree}, {}),
            ({"peers": 1}, {"peers": subtree}),
        ):
            with self.subTest(before=before):
                rendered = json.dumps(list(compare.compare(before, after, set())))
                self.assertNotIn("node.internal", rendered)
                self.assertNotIn("synthetic123", rendered)
                self.assertNotIn("hidden", rendered)

    def test_redacted_key_collisions_preserve_every_entry(self):
        values = {
            "password=first": "one",
            "password=second": "two",
            "password=[REDACTED] [2]": "three",
        }
        rendered = redaction.redact_value(values)
        self.assertEqual(len(rendered), len(values))
        self.assertEqual(list(rendered.values()), ["[REDACTED]"] * 3)
        self.assertNotIn("first", json.dumps(rendered))
        self.assertNotIn("second", json.dumps(rendered))


if __name__ == "__main__":
    unittest.main()
