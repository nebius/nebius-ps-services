#!/usr/bin/env python3
"""Offline tests for the sequential Agentic SDLC worker fallback."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import time
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
        self.helper = self.root / "sdlc_execution.py"
        self.helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
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
spec_gaps = []
status = "implemented"
if os.environ.get("FAKE_CODEX_SPEC_GAP") == assignment["task_id"]:
    status = "replan_required"
    spec_gaps = [{
        "kind": "design",
        "summary": "The assigned boundary cannot satisfy the requirement.",
        "evidence": ["The required interface is outside the immutable claim."],
        "requirement_ids": ["REQ-001"],
        "design_ids": ["FEAT-001"],
    }]
if os.environ.get("FAKE_CODEX_SECRET_GAP") == assignment["task_id"]:
    status = "replan_required"
    spec_gaps = [{
        "kind": "design",
        "summary": "token=abcdefghijklmnopqrstuvwxyz123456",
        "evidence": ["The required interface is outside the immutable claim."],
        "requirement_ids": ["REQ-001"],
        "design_ids": ["FEAT-001"],
    }]
print(json.dumps({
    "task_id": assignment["task_id"],
    "assignment_digest": assignment["assignment_digest"],
    "status": status,
    "summary": "implemented scoped task",
    "decisions": [],
    "open_risks": [],
    "spec_gaps": spec_gaps,
    "validation": "focused tests passed",
    "review": "focused review passed",
}))
""",
            encoding="utf-8",
        )
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        transition = mock.patch.object(
            dispatch, "_transition", return_value={"status": "ACTIVE"}
        )
        self.transition = transition.start()
        self.addCleanup(transition.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def assignment(self, task_id: str, *, corrective: bool = False) -> Path:
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
            "schema": "agentic-sdlc/worker-assignment-v4",
            "feature_id": "FEAT-001",
            "wave_id": "WAVE-001",
            "task_id": task_id,
            "root_intent_sha256": "b" * 64,
            "project_spec_receipt": {
                "schema": "maintain-project-specs.worker-receipt.v1",
                "requirements_sha256": "c" * 64,
                "design_sha256": "d" * 64,
            },
            "scope_cwd": str(scope),
            "incoming_handoff_path": str(handoff_path),
            "incoming_handoff_digest": handoff["handoff_digest"],
            "execution_helper": str(dispatch.canonical_execution_helper()),
            "run_dir": str((self.root / "run-1").resolve()),
            "heartbeat_seconds": 30,
            "start_seconds": 60,
            "worker_profile": "standard",
            "read_only_warning_seconds": 240,
            "read_only_seconds": 300,
            "stall_seconds": 240,
            "max_seconds": 1800,
            "worker_phases": [
                "preflight",
                "implementing",
                "validating",
                "reviewing",
                "reporting",
            ],
        }
        if corrective:
            value["diagnosis_id"] = "d" * 64
            value["regression_oracle"] = "python3 -m unittest test_regression.py"
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
            self.assertIn("task-heartbeat", entry["prompt"])
            self.assertIn("never create a background heartbeat loop", entry["prompt"])
            self.assertNotIn("--session-id", entry["prompt"])
            self.assertIn("Do not commit", entry["prompt"])
        self.assertEqual(
            [call.args[1] for call in self.transition.call_args_list],
            ["task-arm", "task-watch", "task-arm", "task-watch"],
        )

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

    def test_worker_can_return_typed_spec_gap_without_editing_specs(self) -> None:
        assignment = self.assignment("TASK-001")
        with mock.patch.dict(
            os.environ,
            {"FAKE_CODEX_LOG": str(self.log), "FAKE_CODEX_SPEC_GAP": "TASK-001"},
        ):
            results = dispatch.dispatch_sequential(
                [assignment], self.schema, codex_binary=str(self.fake)
            )
        self.assertEqual(results[0]["status"], "replan_required")
        self.assertEqual(results[0]["spec_gaps"][0]["kind"], "design")

    def test_sensitive_spec_gap_is_rejected_before_dispatch_output(self) -> None:
        assignment = self.assignment("TASK-001")
        with mock.patch.dict(
            os.environ,
            {
                "FAKE_CODEX_LOG": str(self.log),
                "FAKE_CODEX_SECRET_GAP": "TASK-001",
            },
        ):
            with self.assertRaises(dispatch.DispatchError) as caught:
                dispatch.dispatch_sequential(
                    [assignment], self.schema, codex_binary=str(self.fake)
                )
        self.assertEqual(caught.exception.code, "WORKER_FAILED")

    def test_missing_codex_is_environment_blocker(self) -> None:
        assignment = self.assignment("TASK-001")
        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_sequential(
                [assignment], self.schema, codex_binary=str(self.root / "missing")
            )
        self.assertEqual(caught.exception.code, "ENVIRONMENT_BLOCKER")

    def test_corrective_assignment_preserves_oracle_ordering_in_prompt(self) -> None:
        assignment_path = self.assignment("TASK-001", corrective=True)
        assignment = dispatch.load_assignment(assignment_path)
        prompt = dispatch.worker_prompt(assignment_path, assignment)
        self.assertIn("corrective task", prompt)
        self.assertIn("regression_oracle first", prompt)
        self.assertIn("Do not reinterpret", prompt)

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

    def test_redigested_noncanonical_helper_fails_before_worker_spawn(self) -> None:
        assignment = self.assignment("TASK-001")
        value = json.loads(assignment.read_text(encoding="utf-8"))
        value["execution_helper"] = str(self.helper.resolve())
        value.pop("assignment_digest")
        value["assignment_digest"] = dispatch.stable_digest(value)
        assignment.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_sequential(
                [assignment], self.schema, codex_binary=str(self.fake)
            )

        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")
        self.assertFalse(self.log.exists())

    def test_terminal_watch_interrupts_worker(self) -> None:
        assignment = self.assignment("TASK-001")
        slow = self.root / "slow-codex"
        slow.write_text(
            "#!/usr/bin/env python3\nimport sys, time\nsys.stdin.read()\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        slow.chmod(slow.stat().st_mode | stat.S_IXUSR)
        self.transition.side_effect = [
            {"status": "assigned"},
            {"status": "WORKER_STALLED"},
        ]

        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_sequential(
                [assignment],
                self.schema,
                codex_binary=str(slow),
                watch_interval=0.05,
                terminate_grace=0.05,
            )

        self.assertEqual(caught.exception.code, "WORKER_STALLED")

    def test_arm_failure_stops_spawned_worker(self) -> None:
        assignment = self.assignment("TASK-001")
        slow = self.root / "arm-failure-codex"
        slow.write_text(
            "#!/usr/bin/env python3\nimport sys, time\nsys.stdin.read()\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        slow.chmod(slow.stat().st_mode | stat.S_IXUSR)
        self.transition.side_effect = dispatch.DispatchError(
            "WORKTREE_CONFLICT", "worker arm failed"
        )

        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_sequential(
                [assignment],
                self.schema,
                codex_binary=str(slow),
                terminate_grace=0.05,
            )

        self.assertEqual(caught.exception.code, "WORKTREE_CONFLICT")

    @unittest.skipUnless(os.name == "posix", "process-group signals require POSIX")
    def test_watch_failure_stops_worker_process_group(self) -> None:
        assignment = self.assignment("TASK-001")
        ready = self.root / "child-ready"
        stopped = self.root / "child-stopped"
        worker = self.root / "process-group-codex"
        worker.write_text(
            """#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import sys
import time

child_code = '''
import os
from pathlib import Path
import signal
import time

ready = Path(os.environ["FAKE_CHILD_READY"])
stopped = Path(os.environ["FAKE_CHILD_STOPPED"])

def stop(_signum, _frame):
    stopped.write_text("stopped\\\\n", encoding="utf-8")
    raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
ready.write_text("ready\\\\n", encoding="utf-8")
while True:
    time.sleep(1)
'''
child = subprocess.Popen([sys.executable, "-c", child_code])
deadline = time.monotonic() + 5
while not Path(os.environ["FAKE_CHILD_READY"]).exists():
    if time.monotonic() >= deadline:
        raise SystemExit(9)
    time.sleep(0.01)
sys.stdin.read()
child.wait()
""",
            encoding="utf-8",
        )
        worker.chmod(worker.stat().st_mode | stat.S_IXUSR)
        self.transition.side_effect = [
            {"status": "assigned"},
            dispatch.DispatchError("EXECUTION_STATE_INVALID", "watch failed"),
        ]

        with (
            mock.patch.dict(
                os.environ,
                {
                    "FAKE_CHILD_READY": str(ready),
                    "FAKE_CHILD_STOPPED": str(stopped),
                },
            ),
            self.assertRaises(dispatch.DispatchError) as caught,
        ):
            dispatch.dispatch_sequential(
                [assignment],
                self.schema,
                codex_binary=str(worker),
                watch_interval=1,
                terminate_grace=1,
            )

        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")
        deadline = time.monotonic() + 1
        while not stopped.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(ready.exists())
        self.assertTrue(stopped.exists())

    def test_legacy_assignment_requires_upgrade(self) -> None:
        assignment = self.assignment("TASK-001")
        value = json.loads(assignment.read_text(encoding="utf-8"))
        value["schema"] = "agentic-sdlc/worker-assignment-v3"
        assignment.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.load_assignment(assignment)

        self.assertEqual(caught.exception.code, "WORKFLOW_UPGRADE_REQUIRED")


if __name__ == "__main__":
    unittest.main()
