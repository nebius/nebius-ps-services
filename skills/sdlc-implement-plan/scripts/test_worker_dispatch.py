#!/usr/bin/env python3
"""Offline tests for the sequential Agentic SDLC worker fallback."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("worker_dispatch.py")
SPEC = importlib.util.spec_from_file_location("worker_dispatch", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)


class WorkerDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.schema = MODULE_PATH.parents[1] / "assets" / "worker-result.schema.json"
        self.log = self.root / "codex.jsonl"
        self.fake = self.root / "codex"
        self.fake.write_text(
            """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

prompt = sys.stdin.read()
assignment_path = Path(prompt.split("Read the assignment JSON at:\\n", 1)[1].splitlines()[0])
assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
with Path(os.environ["FAKE_CODEX_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt, "task": assignment["task_id"]}) + "\\n")
if os.environ.get("FAKE_CODEX_FAIL_TASK") == assignment["task_id"]:
    raise SystemExit(7)
print(json.dumps({
    "task_id": assignment["task_id"],
    "assignment_digest": assignment["assignment_digest"],
    "status": "implemented",
    "summary": "implemented scoped task",
    "decisions": [],
    "open_risks": [],
    "validation": "focused tests passed",
    "review": "focused review passed",
}))
""",
            encoding="utf-8",
        )
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assignment(self, task_id: str) -> Path:
        scope = self.root / "workers" / task_id / "services" / "a"
        scope.mkdir(parents=True)
        path = (
            self.root
            / "run-1"
            / "execution"
            / "FEAT-001"
            / "assignments"
            / "WAVE-001"
            / f"{task_id}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        handoff_path = (
            self.root
            / "run-1"
            / "execution"
            / "FEAT-001"
            / "incoming-handoffs"
            / "WAVE-001"
            / f"{task_id}.json"
        )
        handoff_path.parent.mkdir(parents=True, exist_ok=True)
        handoff = {
            "schema": "agentic-sdlc/incoming-handoff-v1",
            "feature_id": "FEAT-001",
            "wave_id": "WAVE-001",
            "task_id": task_id,
            "assignment_base_head": "a" * 40,
            "dependencies": [],
            "predecessors": [],
            "created_at": "2026-07-17T00:00:00Z",
        }
        handoff["handoff_digest"] = dispatch.stable_digest(handoff)
        handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        handoff_path.chmod(0o600)
        value = {
            "schema": "agentic-sdlc/worker-assignment-v2",
            "feature_id": "FEAT-001",
            "wave_id": "WAVE-001",
            "task_id": task_id,
            "scope_cwd": str(scope),
            "incoming_handoff_path": str(handoff_path),
            "incoming_handoff_digest": handoff["handoff_digest"],
        }
        value["assignment_digest"] = dispatch.stable_digest(value)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_dispatches_fresh_ephemeral_workers_sequentially(self) -> None:
        first = self.assignment("TASK-001")
        second = self.assignment("TASK-002")
        with mock.patch.dict(os.environ, {"FAKE_CODEX_LOG": str(self.log)}):
            results = dispatch.dispatch_sequential(
                [first, second], self.schema, codex_binary=str(self.fake)
            )
        self.assertEqual(
            [item["task_id"] for item in results], ["TASK-001", "TASK-002"]
        )
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual([entry["task"] for entry in entries], ["TASK-001", "TASK-002"])
        for entry in entries:
            argv = entry["argv"]
            self.assertEqual(argv[0], "exec")
            self.assertIn("--ephemeral", argv)
            self.assertIn("--output-schema", argv)
            self.assertIn("workspace-write", argv)
            self.assertEqual(argv[-1], "-")
            self.assertNotIn("--last", argv)
            self.assertNotIn("--skip-git-repo-check", argv)
            self.assertNotIn("danger-full-access", argv)
            self.assertIn("task-start", entry["prompt"])
            self.assertNotIn("--session-id", entry["prompt"])
            self.assertIn("Do not commit", entry["prompt"])

    def test_first_failure_stops_later_dispatch_and_retains_concise_error(self) -> None:
        first = self.assignment("TASK-001")
        second = self.assignment("TASK-002")
        with mock.patch.dict(
            os.environ,
            {
                "FAKE_CODEX_LOG": str(self.log),
                "FAKE_CODEX_FAIL_TASK": "TASK-001",
            },
        ):
            with self.assertRaises(dispatch.DispatchError) as caught:
                dispatch.dispatch_sequential(
                    [first, second], self.schema, codex_binary=str(self.fake)
                )
        self.assertEqual(caught.exception.code, "WORKER_FAILED")
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual([entry["task"] for entry in entries], ["TASK-001"])
        self.assertNotIn("FAKE_CODEX", str(caught.exception))

    def test_missing_codex_is_environment_blocker(self) -> None:
        assignment = self.assignment("TASK-001")
        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_sequential(
                [assignment], self.schema, codex_binary=str(self.root / "missing")
            )
        self.assertEqual(caught.exception.code, "ENVIRONMENT_BLOCKER")

    def test_tampered_handoff_fails_before_worker_spawn(self) -> None:
        assignment = self.assignment("TASK-001")
        value = json.loads(assignment.read_text(encoding="utf-8"))
        handoff_path = Path(value["incoming_handoff_path"])
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        handoff["dependencies"] = ["TASK-999"]
        handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        handoff_path.chmod(0o600)
        with mock.patch.dict(os.environ, {"FAKE_CODEX_LOG": str(self.log)}):
            with self.assertRaises(dispatch.DispatchError) as caught:
                dispatch.dispatch_sequential(
                    [assignment], self.schema, codex_binary=str(self.fake)
                )
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
