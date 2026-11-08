#!/usr/bin/env python3
"""Offline tests for the sequential Agentic SDLC worker fallback."""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("workflow_dispatch.py")
SPEC = importlib.util.spec_from_file_location("workflow_dispatch", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
dispatch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(dispatch)
import workflow_worker_contract as contract  # noqa: E402 — load verified shared runtime first


class WorkerDispatchTests(unittest.TestCase):
    def test_launch_setup_failure_releases_anchor(self):
        from workflow_process import OwnedProcessGroup
        owners = []
        def create():
            owner = OwnedProcessGroup()
            owners.append(owner)
            return owner
        assignment = self.assignment("TASK-001")
        try:
            with mock.patch.object(dispatch, "OwnedProcessGroup", side_effect=create), \
                    mock.patch.object(dispatch, "runtime_environment", side_effect=ValueError("invalid environment")):
                with self.assertRaises(ValueError):
                    dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake), terminate_grace=.01)
            self.assertTrue(all(owner.closed for owner in owners))
        finally:
            for owner in owners:
                owner.close(grace=0)
                owner.verify_absent()

    def test_cleanup_stops_redirected_child_after_parent_exit(self):
        import subprocess
        import sys
        from workflow_process import OwnedProcessGroup
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "child-pid"
            owner = OwnedProcessGroup()
            process = subprocess.Popen([sys.executable, "-c",
                "import subprocess,sys; subprocess.Popen([sys.executable,'-c',"
                "\"import os,signal,time; from pathlib import Path; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)\".replace('import os,','import sys,os,'),sys.argv[1]],"
                "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)", str(marker)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, process_group=owner.id)
            process._workflow_group = owner
            try:
                process.communicate(timeout=5)
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(marker.exists())
                dispatch._stop_worker(process, terminate_grace=.05)
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(marker.read_text()), 0)
            finally:
                owner.close(grace=0)
                process.wait(timeout=5)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.schema = MODULE_PATH.parents[1] / "assets" / "worker-result.schema.json"
        self.log = self.root / "codex.jsonl"
        self.helper = self.root / "workflow_transition.py"
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
    print(os.environ.get("FAKE_CODEX_STDERR", ""), file=sys.stderr)
    raise SystemExit(7)
if os.environ.get("FAKE_CODEX_CHECK_SCHEMA"):
    schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
    def check_schema(value):
        if isinstance(value, dict):
            if set(value) & {"allOf", "if", "then", "else", "uniqueItems"}:
                print("invalid_json_schema: codex_output_schema", file=sys.stderr)
                raise SystemExit(1)
            for child in value.values():
                check_schema(child)
        elif isinstance(value, list):
            for child in value:
                check_schema(child)
    check_schema(schema)
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
result = {
    "task_id": assignment["task_id"],
    "assignment_digest": assignment["assignment_digest"],
    "status": status,
    "summary": "implemented scoped task",
    "decisions": [],
    "open_risks": [],
    "spec_gaps": spec_gaps,
    "validation": "focused tests passed",
    "review": "focused review passed",
}
result.update(json.loads(os.environ.get("FAKE_CODEX_RESULT_OVERRIDES", "{}")))
field = os.environ.get("FAKE_CODEX_RESULT_FIELD")
if field:
    value = "example: token=" + "A" * 20
    result[field] = [value] if field in ("decisions", "open_risks") else value
if os.environ.get("FAKE_CODEX_NONSTRING_DECISION"):
    result["decisions"] = [{"unexpected": "nested evidence"}]
if os.environ.get("SKILLS_AGENT") == "claude":
    assert not os.environ.get("CODEX_THREAD_ID")
    assert not os.environ.get("SKILLS_SESSION_ID")
    assert Path.cwd().resolve() == Path(assignment["scope_cwd"]).resolve()
    result = {"type": "result", "subtype": "success", "is_error": False, "structured_output": result}
    if os.environ.get("FAKE_CLAUDE_ERROR"):
        result["is_error"] = True
print(os.environ.get("FAKE_CODEX_RAW_OUTPUT", json.dumps(result)))
""",
            encoding="utf-8",
        )
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        self.transition = mock.Mock(return_value={"status": "ACTIVE"})

        def transition_result(assignment, action, *extra):
            result = self.transition(assignment, action, *extra)
            if action == "task-arm":
                path = (Path(assignment["run_dir"]) / "execution" / assignment["execution_id"]
                        / "assignments" / assignment["wave_id"] / (assignment["task_id"] + ".json"))
                return {**result, "launch": contract.launch_contract(path, assignment)}
            return result

        transition = mock.patch.object(dispatch, "_transition", side_effect=transition_result)
        transition.start()
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
            "schema": "agentic-sdlc/incoming-handoff-v3",
            "execution_id": "FEAT-001",
            "wave_id": "WAVE-001",
            "task_id": task_id,
            "assignment_base_head": "a" * 40,
            "dependencies": [],
            "predecessors": [],
            "created_at": "2026-07-17T00:00:00Z",
        }
        handoff["handoff_digest"] = contract.stable_digest(handoff)
        handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
        handoff_path.chmod(0o600)
        value = {
            "schema": "workflow/worker-assignment-v2",
            "execution_id": "FEAT-001",
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
            "execution_helper": str(contract.canonical_execution_helper()),
            "run_dir": str((self.root / "run-1").resolve()),
            "heartbeat_seconds": 30,
            "start_seconds": 300,
            "worker_profile": "standard",
            "read_only_warning_seconds": 240,
            "read_only_seconds": 300,
            "stall_seconds": 240,
            "max_seconds": 3600,
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
        value["assignment_digest"] = contract.stable_digest(value)
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_obsolete_worker_deadline_is_rejected_before_dispatch(self) -> None:
        path = self.assignment("TASK-001")
        self.assertEqual(contract.load_assignment(path)["max_seconds"], 3600)
        value = json.loads(path.read_text())
        value["max_seconds"] = 1800
        value.pop("assignment_digest")
        value["assignment_digest"] = contract.stable_digest(value)
        path.write_text(json.dumps(value))
        with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as failure:
            contract.load_assignment(path)
        self.assertEqual(failure.exception.code, "EXECUTION_STATE_INVALID")

    def test_obsolete_startup_limit_is_rejected_before_native_launch(self) -> None:
        path = self.assignment("TASK-001")
        value = json.loads(path.read_text())
        value["start_seconds"] = 60
        value.pop("assignment_digest")
        value["assignment_digest"] = contract.stable_digest(value)
        path.write_text(json.dumps(value))
        with mock.patch.object(dispatch.subprocess, "Popen") as spawn:
            with self.assertRaises(contract.WorkerContractError) as failure:
                dispatch.dispatch_sequential([path], self.schema, agent_binary=str(self.fake))
            self.assertEqual(failure.exception.code, "EXECUTION_STATE_INVALID")
            spawn.assert_not_called()

    def test_claude_worker_uses_native_schema_envelope_and_fresh_identity(self) -> None:
        assignment = self.assignment("TASK-001")
        with mock.patch.dict(os.environ, {"SKILLS_AGENT": "claude", "SKILLS_SESSION_ID": "parent",
                                          "FAKE_CODEX_LOG": str(self.log)}):
            os.environ.pop("CODEX_THREAD_ID", None)
            result = dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))
            self.assertEqual(result[0]["status"], "implemented")
            argv = json.loads(self.log.read_text().splitlines()[0])["argv"]
            self.assertIn("--print", argv)
            self.assertIn("--no-session-persistence", argv)
            self.assertEqual(argv[argv.index("--add-dir") + 1], str(self.root.resolve()))
            self.assertEqual(json.loads(argv[argv.index("--json-schema") + 1]), json.loads(self.schema.read_text()))
            self.assertNotIn("--sandbox", argv)
            self.assertNotIn("--dangerously-skip-permissions", argv)
            with mock.patch.dict(os.environ, {"FAKE_CLAUDE_ERROR": "1"}):
                with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as failure:
                    dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))
                self.assertEqual(failure.exception.code, "WORKER_FAILED")

    def test_dispatches_fresh_ephemeral_workers_sequentially(self) -> None:
        first = self.assignment("TASK-001")
        second = self.assignment("TASK-002")
        with mock.patch.dict(os.environ, {"FAKE_CODEX_LOG": str(self.log)}):
            results = dispatch.dispatch_sequential(
                [first, second], self.schema, agent_binary=str(self.fake)
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
            self.assertEqual(argv[argv.index("--add-dir") + 1], str(self.root.resolve()))
            self.assertEqual(argv[-1], "-")
            self.assertNotIn("--last", argv)
            self.assertNotIn("--skip-git-repo-check", argv)
            self.assertNotIn("danger-full-access", argv)
            self.assertIn("task-start", entry["prompt"])
            self.assertIn("task-heartbeat", entry["prompt"])
            self.assertIn("never create a background heartbeat loop", entry["prompt"])
            self.assertIn("Before repairing or rerunning an unexpected failed check", entry["prompt"])
            self.assertIn("declared before execution in the immutable assignment", entry["prompt"])
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
            with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
                dispatch.dispatch_sequential(
                    [first, second], self.schema, agent_binary=str(self.fake)
                )
        self.assertEqual(caught.exception.code, "WORKER_FAILED")
        entries = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual([entry["task"] for entry in entries], ["TASK-001"])
        self.assertNotIn("FAKE_CODEX", str(caught.exception))

    def test_worker_spec_gap_stops_later_dispatch(self) -> None:
        first = self.assignment("TASK-001")
        second = self.assignment("TASK-002")
        with mock.patch.dict(os.environ, {
            "FAKE_CODEX_LOG": str(self.log), "FAKE_CODEX_SPEC_GAP": "TASK-001",
        }), self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_sequential([first, second], self.schema, agent_binary=str(self.fake))
        self.assertEqual(caught.exception.code, "WORKER_REPLAN_REQUIRED")
        self.assertEqual(caught.exception.worker_result["spec_gaps"][0]["kind"], "design")
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_blocked_worker_returns_error_evidence_and_stops_later_dispatch(self) -> None:
        first = self.assignment("TASK-001")
        second = self.assignment("TASK-002")
        blocked = {
            "status": "blocked",
            "summary": "Runtime observation failed; assigned work is incomplete.",
            "validation": "Runtime tests were not run after the observation failed.",
            "review": "Source review completed; runtime evidence remains unavailable.",
            "spec_gaps": [],
        }
        args = mock.Mock(
            assignment=[first, second], output_schema=self.schema,
            agent_binary=str(self.fake), watch_interval=30, terminate_grace=5,
        )
        for host in ("codex", "claude"):
            with self.subTest(host=host):
                self.log.write_text("")
                self.transition.reset_mock()
                output = io.StringIO()
                with mock.patch.dict(os.environ, {
                    "SKILLS_AGENT": host,
                    "FAKE_CODEX_LOG": str(self.log),
                    "FAKE_CODEX_RESULT_OVERRIDES": json.dumps(blocked),
                }), mock.patch.object(dispatch, "parse_args", return_value=args), \
                        mock.patch("sys.stdout", output):
                    self.assertEqual(dispatch.main(), 2)
                result = json.loads(output.getvalue())
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["code"], "WORKER_BLOCKED")
                self.assertNotIn("results", result)
                for key, value in blocked.items():
                    self.assertEqual(result["worker_result"][key], value)
                self.assertEqual(result["worker_result"]["task_id"], "TASK-001")
                entries = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertEqual([entry["task"] for entry in entries], ["TASK-001"])
                self.assertEqual(
                    [call.args[1] for call in self.transition.call_args_list],
                    ["task-arm", "task-watch"],
                )

    def test_invalid_blocked_result_never_returns_worker_payload(self) -> None:
        assignment = self.assignment("TASK-001")
        gap = {
            "kind": "design", "summary": "The required interface is missing.",
            "evidence": ["The assigned boundary lacks the interface."],
            "requirement_ids": ["REQ-001"], "design_ids": ["FEAT-001"],
        }
        cases = [
            ({"spec_gaps": [gap]}, "blocked_with_spec_gaps"),
            ({"task_id": "TASK-999"}, "task_identity"),
            ({"validation": []}, "validation_type"),
            ({"summary": "token=" + "A" * 30}, "summary_sensitive"),
        ]
        for override, reason in cases:
            with self.subTest(reason=reason), mock.patch.dict(os.environ, {
                "FAKE_CODEX_LOG": str(self.log),
                "FAKE_CODEX_RESULT_OVERRIDES": json.dumps({"status": "blocked", **override}),
            }), self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
                dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))
            self.assertEqual(caught.exception.code, "WORKER_FAILED")
            self.assertIsNone(caught.exception.worker_result)
            self.assertIn("reason=" + reason, str(caught.exception))
            self.assertNotIn("A" * 30, str(caught.exception))

    def test_prestart_diagnostics_are_retained_without_result_authority(self) -> None:
        first = self.assignment("TASK-001")
        second = self.assignment("TASK-002")
        args = type("Args", (), {"assignment": [first, second], "output_schema": self.schema,
                                "agent_binary": str(self.fake), "watch_interval": 30,
                                "terminate_grace": 5})()
        self.transition.return_value = {"status": "PENDING_START"}
        for host in ("codex", "claude"):
            for status in ("blocked", "replan_required"):
                with self.subTest(host=host, status=status):
                    self.log.unlink(missing_ok=True)
                    output = io.StringIO()
                    override = {"summary": "Native startup could not complete.",
                                "validation": "No assigned checks ran.",
                                "review": "No implementation was admitted."}
                    if status == "blocked":
                        override["status"] = status
                    with mock.patch.dict(os.environ, {
                        "SKILLS_AGENT": host, "CODEX_THREAD_ID": "",
                        "FAKE_CODEX_LOG": str(self.log),
                        "FAKE_CODEX_SPEC_GAP": "TASK-001" if status == "replan_required" else "",
                        "FAKE_CODEX_RESULT_OVERRIDES": json.dumps(override),
                    }), mock.patch.object(dispatch, "parse_args", return_value=args), \
                            mock.patch("sys.stdout", output):
                        self.assertEqual(dispatch.main(), 2)
                    result = json.loads(output.getvalue())
                    self.assertEqual(result["code"], "WORKER_FAILED")
                    self.assertNotIn("worker_result", result)
                    self.assertNotIn("results", result)
                    diagnostic = result["prestart_result"]
                    self.assertEqual(diagnostic["status"], status)
                    self.assertEqual(diagnostic["task_id"], "TASK-001")
                    self.assertEqual(diagnostic["assignment_digest"], json.loads(first.read_text())["assignment_digest"])
                    for key, value in override.items():
                        self.assertEqual(diagnostic[key], value)
                    entries = [json.loads(line) for line in self.log.read_text().splitlines()]
                    self.assertEqual([entry["task"] for entry in entries], ["TASK-001"])
                    self.assertIn("--print" if host == "claude" else "exec", entries[0]["argv"])

    def test_prestart_diagnostics_reject_unsafe_or_unadmitted_output(self) -> None:
        assignment = self.assignment("TASK-001")
        self.transition.return_value = {"status": "PENDING_START"}
        expanded = {
            "task_id": "TASK-001", "assignment_digest": json.loads(assignment.read_text())["assignment_digest"],
            "status": "blocked", "summary": "é" * 20000, "decisions": [],
            "open_risks": [], "spec_gaps": [], "validation": "Not run.", "review": "Not admitted.",
        }
        cases = [
            ({"FAKE_CODEX_RAW_OUTPUT": "not-json-private-marker"}, "invalid JSON"),
            ({"FAKE_CODEX_RAW_OUTPUT": "9" * 5000}, "invalid JSON"),
            ({"FAKE_CODEX_RAW_OUTPUT": "[" * 2000 + "]" * 2000}, "shape"),
            ({"FAKE_CODEX_RAW_OUTPUT": "x" * 65537}, "output_size"),
            ({"FAKE_CODEX_RAW_OUTPUT": json.dumps(expanded, ensure_ascii=False)}, "result_size"),
            ({"FAKE_CODEX_RESULT_OVERRIDES": json.dumps({"status": "blocked", "task_id": "TASK-999"})}, "task_identity"),
            ({"FAKE_CODEX_RESULT_OVERRIDES": json.dumps({"status": "blocked", "assignment_digest": "f" * 64})}, "assignment_identity"),
            ({"FAKE_CODEX_RESULT_OVERRIDES": json.dumps({"status": "blocked", "summary": "token=" + "A" * 30})}, "summary_sensitive"),
            ({"SKILLS_AGENT": "claude", "FAKE_CLAUDE_ERROR": "1"}, "unsuccessful"),
            ({}, "implemented_before_start"),
        ]
        for values, reason in cases:
            with self.subTest(reason=reason), mock.patch.dict(os.environ, {
                "CODEX_THREAD_ID": "", "SKILLS_AGENT": "codex",
                "FAKE_CODEX_LOG": str(self.log), **values,
            }), self.assertRaises(dispatch.DispatchError) as caught:
                dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))
            self.assertEqual(caught.exception.code, "WORKER_FAILED")
            self.assertIsNone(caught.exception.prestart_result)
            self.assertIsNone(caught.exception.worker_result)
            self.assertIn(reason, str(caught.exception))
            self.assertNotIn("private-marker", str(caught.exception))
            self.assertNotIn("A" * 30, str(caught.exception))

    def test_prestart_diagnostics_never_override_exit_or_liveness_failures(self) -> None:
        assignment = self.assignment("TASK-001")
        for status, failed in (("PENDING_START", True), ("WORKER_PRESTART_TIMEOUT", False),
                               ("ASSIGNED", False)):
            self.transition.return_value = {"status": status}
            with self.subTest(status=status, nonzero=failed), mock.patch.dict(os.environ, {
                "FAKE_CODEX_LOG": str(self.log),
                "FAKE_CODEX_FAIL_TASK": "TASK-001" if failed else "",
                "FAKE_CODEX_RESULT_OVERRIDES": json.dumps({"status": "blocked"}),
            }), self.assertRaises(dispatch.DispatchError) as caught:
                dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))
            self.assertIsNone(caught.exception.prestart_result)
            self.assertIsNone(caught.exception.worker_result)
            if status == "WORKER_PRESTART_TIMEOUT":
                self.assertEqual(caught.exception.code, status)

    def test_schema_declares_all_terminal_worker_outcomes(self) -> None:
        schema = json.loads(self.schema.read_text())
        self.assertEqual(
            schema["properties"]["status"]["enum"],
            ["implemented", "verified_no_change", "replan_required", "blocked"],
        )

    def test_native_transport_accepts_worker_result_schema(self) -> None:
        assignment = self.assignment("TASK-001")
        with mock.patch.dict(os.environ, {
            "FAKE_CODEX_LOG": str(self.log), "FAKE_CODEX_CHECK_SCHEMA": "1",
        }):
            result = dispatch.dispatch_sequential(
                [assignment], self.schema, agent_binary=str(self.fake)
            )
        self.assertEqual(result[0]["status"], "implemented")

    def test_local_validation_preserves_conditional_and_unique_gap_rules(self) -> None:
        assignment = self.assignment("TASK-001")
        gap = {"kind": "design", "summary": "Scoped interface is incomplete.",
               "evidence": ["The required interface is outside the claim."],
               "requirement_ids": ["REQ-001"], "design_ids": ["FEAT-001"]}
        cases = [
            {"status": "implemented", "spec_gaps": [gap]},
            {"status": "replan_required", "spec_gaps": []},
        ]
        for field in ("requirement_ids", "design_ids"):
            for invalid in ([gap[field][0]] * 2, [42], [{}]):
                cases.append({"status": "replan_required", "spec_gaps": [{**gap, field: invalid}]})
        for override in cases:
            with self.subTest(override=override), mock.patch.dict(os.environ, {
                "FAKE_CODEX_LOG": str(self.log),
                "FAKE_CODEX_RESULT_OVERRIDES": json.dumps(override),
            }):
                with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
                    dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))
                self.assertEqual(caught.exception.code, "WORKER_FAILED")

    def test_schema_failure_diagnostic_never_reflects_worker_stderr(self) -> None:
        assignment = self.assignment("TASK-001")
        marker = "synthetic-private-output-do-not-reflect"
        for stderr, recognized in (
            ("invalid_json_schema: codex_output_schema " + marker, True),
            (marker, False),
            ("invalid_json_schema " + marker, False),
        ):
            with self.subTest(recognized=recognized), mock.patch.dict(os.environ, {
                "FAKE_CODEX_LOG": str(self.log), "FAKE_CODEX_FAIL_TASK": "TASK-001",
                "FAKE_CODEX_STDERR": stderr,
            }), self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
                dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))
            self.assertEqual(caught.exception.code, "WORKER_FAILED")
            self.assertIn("exit 7", str(caught.exception))
            self.assertEqual("output schema was rejected" in str(caught.exception), recognized)
            self.assertNotIn(marker, str(caught.exception))

    def test_result_rejection_preserves_predicate_without_worker_content(self) -> None:
        assignment = self.assignment("TASK-001")
        marker = "synthetic-private-result-do-not-reflect"
        with (
            mock.patch.dict(
                os.environ,
                {
                    "FAKE_CODEX_LOG": str(self.log),
                    "FAKE_CODEX_RESULT_OVERRIDES": json.dumps(
                        {
                            "status": "replan_required",
                            "spec_gaps": [],
                            "summary": marker,
                        }
                    ),
                },
            ),
            self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught,
        ):
            dispatch.dispatch_sequential(
                [assignment], self.schema, agent_binary=str(self.fake)
            )
        self.assertEqual(caught.exception.code, "WORKER_FAILED")
        self.assertIn("reason=replan_without_spec_gap", str(caught.exception))
        self.assertNotIn(marker, str(caught.exception))

    def test_result_reasons_are_fixed_and_all_guards_remain_closed(self) -> None:
        identity = {"task_id": "TASK-001", "assignment_digest": "a" * 64}
        valid = {
            **identity,
            "status": "implemented",
            "summary": "Scoped work complete.",
            "validation": "Tests passed.",
            "review": "Review passed.",
            "decisions": [],
            "open_risks": [],
            "spec_gaps": [],
        }
        gap = {
            "kind": "design",
            "summary": "A boundary is missing.",
            "evidence": ["The required interface is outside the claim."],
            "requirement_ids": ["REQ-001"],
            "design_ids": ["FEAT-001"],
        }
        cases = [
            (None, "shape"),
            ([], "shape"),
            ({**valid, "synthetic-private-field": "do-not-reflect"}, "shape"),
            ({**valid, "task_id": "synthetic-private-task"}, "task_identity"),
            (
                {**valid, "assignment_digest": "synthetic-private-digest"},
                "assignment_identity",
            ),
            ({**valid, "status": {}}, "status"),
            ({**valid, "status": "synthetic-private-status"}, "status"),
            ({**valid, "spec_gaps": None}, "spec_gaps_type"),
            ({**valid, "spec_gaps": [{}]}, "spec_gap_shape"),
            ({**valid, "spec_gaps": [{**gap, "kind": []}]}, "spec_gap_kind"),
            (
                {**valid, "spec_gaps": [{**gap, "evidence": []}]},
                "spec_gap_evidence_empty",
            ),
            (
                {
                    **valid,
                    "spec_gaps": [{**gap, "requirement_ids": ["REQ-001", "REQ-001"]}],
                },
                "spec_gap_requirement_ids_duplicate",
            ),
            (
                {**valid, "spec_gaps": [{**gap, "design_ids": [{}]}]},
                "spec_gap_design_ids_item",
            ),
            ({**valid, "spec_gaps": [gap]}, "implemented_with_spec_gaps"),
            ({**valid, "status": "replan_required"}, "replan_without_spec_gap"),
        ]
        sensitive = "token=" + "A" * 20
        for field in ("summary", "validation", "review"):
            for value, suffix in (
                (None, "type"),
                (" ", "empty"),
                (sensitive, "sensitive"),
            ):
                cases.append(({**valid, field: value}, f"{field}_{suffix}"))
        for field in ("decisions", "open_risks"):
            for value, suffix in (
                (None, "type"),
                ([{}], "item_type"),
                ([" "], "item_empty"),
                ([sensitive], "item_sensitive"),
            ):
                cases.append(({**valid, field: value}, f"{field}_{suffix}"))
        for result, reason in cases:
            with self.subTest(reason=reason):
                self.assertEqual(contract.result_rejection(result, identity), reason)
        self.assertIsNone(contract.result_rejection(valid, identity))
        self.assertIsNone(
            contract.result_rejection(
                {**valid, "status": "replan_required", "spec_gaps": [gap]}, identity
            )
        )

    def test_sensitive_spec_gap_is_rejected_before_dispatch_output(self) -> None:
        assignment = self.assignment("TASK-001")
        with mock.patch.dict(
            os.environ,
            {
                "FAKE_CODEX_LOG": str(self.log),
                "FAKE_CODEX_SECRET_GAP": "TASK-001",
            },
        ):
            with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
                dispatch.dispatch_sequential(
                    [assignment], self.schema, agent_binary=str(self.fake)
                )
        self.assertEqual(caught.exception.code, "WORKER_FAILED")

    def test_every_worker_result_field_is_screened_before_return(self):
        assignment = self.assignment("TASK-001")
        for field in ("summary", "validation", "review", "decisions", "open_risks", "extra"):
            with self.subTest(field=field), mock.patch.dict(
                os.environ, {"FAKE_CODEX_LOG": str(self.log), "FAKE_CODEX_RESULT_FIELD": field}
            ):
                with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
                    dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))
                self.assertEqual(caught.exception.code, "WORKER_FAILED")
                self.assertNotIn("A" * 20, str(caught.exception))

    def test_worker_result_rejects_untyped_list_entries(self):
        assignment = self.assignment("TASK-001")
        with mock.patch.dict(os.environ, {
            "FAKE_CODEX_LOG": str(self.log), "FAKE_CODEX_NONSTRING_DECISION": "1",
        }), self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)):
            dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))

    def test_missing_codex_is_environment_blocker(self) -> None:
        assignment = self.assignment("TASK-001")
        with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
            dispatch.dispatch_sequential(
                [assignment], self.schema, agent_binary=str(self.root / "missing")
            )
        self.assertEqual(caught.exception.code, "ENVIRONMENT_BLOCKER")

    def test_corrective_assignment_preserves_oracle_ordering_in_prompt(self) -> None:
        assignment_path = self.assignment("TASK-001", corrective=True)
        assignment = contract.load_assignment(assignment_path)
        prompt = contract.worker_prompt(assignment_path, assignment)
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
            with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
                dispatch.dispatch_sequential(
                    [assignment], self.schema, agent_binary=str(self.fake)
                )
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")
        self.assertFalse(self.log.exists())

    def test_redigested_noncanonical_helper_fails_before_worker_spawn(self) -> None:
        assignment = self.assignment("TASK-001")
        value = json.loads(assignment.read_text(encoding="utf-8"))
        value["execution_helper"] = str(self.helper.resolve())
        value.pop("assignment_digest")
        value["assignment_digest"] = contract.stable_digest(value)
        assignment.write_text(json.dumps(value), encoding="utf-8")

        with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
            dispatch.dispatch_sequential(
                [assignment], self.schema, agent_binary=str(self.fake)
            )

        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")
        self.assertFalse(self.log.exists())

    def test_noncanonical_output_schema_fails_before_worker_spawn(self) -> None:
        assignment = self.assignment("TASK-001")
        alternate = self.root / "copied-schema.json"
        alternate.write_bytes(self.schema.read_bytes())
        with self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_sequential([assignment], alternate, agent_binary=str(self.fake))
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

        with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
            dispatch.dispatch_sequential(
                [assignment],
                self.schema,
                agent_binary=str(slow),
                watch_interval=0.05,
                terminate_grace=0.05,
            )

        self.assertEqual(caught.exception.code, "WORKER_STALLED")

    def test_arm_failure_rejects_before_native_spawn(self) -> None:
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

        with mock.patch.object(dispatch.subprocess, "Popen") as spawn, \
                self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
            dispatch.dispatch_sequential(
                [assignment],
                self.schema,
                agent_binary=str(slow),
                terminate_grace=0.05,
            )

        self.assertEqual(caught.exception.code, "WORKTREE_CONFLICT")
        spawn.assert_not_called()

    def test_mismatched_launch_admission_rejects_before_native_spawn(self) -> None:
        assignment = self.assignment("TASK-001")
        with mock.patch.object(dispatch, "_transition", return_value={"launch": {}}), \
                mock.patch.object(dispatch.subprocess, "Popen") as spawn, \
                self.assertRaises(dispatch.DispatchError) as caught:
            dispatch.dispatch_sequential([assignment], self.schema, agent_binary=str(self.fake))
        self.assertEqual(caught.exception.code, "EXECUTION_STATE_INVALID")
        spawn.assert_not_called()

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
            self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught,
        ):
            dispatch.dispatch_sequential(
                [assignment],
                self.schema,
                agent_binary=str(worker),
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

        with self.assertRaises((dispatch.DispatchError, contract.WorkerContractError)) as caught:
            contract.load_assignment(assignment)

        self.assertEqual(caught.exception.code, "WORKFLOW_UPGRADE_REQUIRED")


class ProcessGroupTests(unittest.TestCase):
    def test_interrupted_grace_still_cleans_and_close_replay_does_not_signal(self):
        from workflow_process import OwnedProcessGroup
        owner = OwnedProcessGroup()
        original_sleep = time.sleep
        interrupted = False

        def interrupt_once(delay):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt
            original_sleep(delay)

        try:
            with mock.patch("workflow_process.time.sleep", side_effect=interrupt_once):
                with self.assertRaises(KeyboardInterrupt):
                    owner.close(grace=5)
            self.assertTrue(owner.closed)
            owner.verify_absent()
            with mock.patch("workflow_process.os.killpg") as signal_group:
                owner.close(grace=0)
                signal_group.assert_not_called()
        finally:
            owner.close(grace=0)
            owner.verify_absent()


if __name__ == "__main__":
    unittest.main()
