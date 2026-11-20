"""Native correlation cannot be replaced by source fixture success or declarations."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import three_tier_native as native
from native_activation_fixture import SESSION, seed

ROOT = Path(__file__).resolve().parents[2]


class NativeActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.run = Path(self.temp).resolve()
        self.state = {
            "run_root": str(self.run),
            "private_root": str(self.run / "private"),
            "verification_id": "a" * 32,
            "git": {"baseline_sha": "b" * 40},
        }
        self.startup = {
            "schema": "agentic-sdlc/workflow-startup-v3",
            "verification_id": "a" * 32,
            "baseline_sha": "b" * 40,
            "project_id": "test-project",
            "run_id": "run-test",
            "agent": "codex",
            "current_phase": "sdlc-start",
            "source_state_discovery": "MATCH",
            "owner_session_hash": hashlib.sha256(SESSION.encode()).hexdigest(),
            "lock_sha256": "c" * 64,
            "objective_sha256": "d" * 64,
            "observed_at": "2026-11-06T00:00:00Z",
        }
        self.startup_path = self.run / "evidence/workflow-startup.json"
        self.write(self.startup_path, self.startup)
        self.state["workflow_startup"] = {
            "path": "evidence/workflow-startup.json",
            "sha256": hashlib.sha256(self.startup_path.read_bytes()).hexdigest(),
        }
        self.value = seed(self.state, self.startup, ROOT)
        self.trace = self.run / self.value["native"]["path"]
        self.hooks = self.run / self.value["events"]["path"]

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(json.dumps(value) + "\n")
        path.chmod(0o600)

    def test_native_receipt_correlates_without_copying_private_bodies(self):
        value = native.validate(self.state, skills_root=ROOT)
        self.assertEqual(len(value["matches"]), 1)
        self.assertNotIn("private contents", json.dumps(value))
        self.assertNotIn("arguments", json.dumps(value))
        entrypoint = ROOT / "sdlc-start/assets/hooks/pre_tool_use_sdlc_policy.py"
        self.assertEqual(value["hook"], {
            name: hashlib.sha256(entrypoint.read_bytes()).hexdigest()
            for name in ("source_sha256", "installed_sha256")
        })

    def test_private_hook_dependency_drift_and_deletion_are_rejected(self):
        home = Path(self.state["private_root"]) / "codex-home"
        for relative in (
            "trusted_runtime.py", "git_environment.py", "agent_runtime.py",
            "lib/__init__.py", "lib/sdlc_state.py", "lib/sdlc_policy.py",
        ):
            path = home / "hooks" / relative
            original = path.read_bytes()
            for action in ("changed", "missing"):
                with self.subTest(relative=relative, action=action):
                    try:
                        if action == "changed":
                            path.write_bytes(original + b"\n# changed helper\n")
                        else:
                            path.unlink()
                        with self.assertRaises(native.NativeEvidenceError):
                            native.validate(self.state, skills_root=ROOT)
                    finally:
                        path.write_bytes(original)
                        path.chmod(0o600)

    def test_unterminated_hook_suffix_is_not_ignored(self):
        path = Path(self.state["private_root"]) / "codex-home/hooks/pre_tool_use_sdlc_policy.py"
        with path.open("ab") as stream:
            stream.write(b"# changed without a final newline")
        with self.assertRaises(native.NativeEvidenceError):
            native.validate(self.state, skills_root=ROOT)

    def test_source_discovery_alone_never_qualifies_native_activation(self):
        self.state.pop("native_activation")
        with self.assertRaisesRegex(native.NativeEvidenceError, "missing"):
            native.validate(self.state, skills_root=ROOT)

    def test_hook_events_alone_are_not_native_proof(self):
        self.trace.unlink()
        with self.assertRaises(native.NativeEvidenceError):
            native.observe(self.state, skills_root=ROOT)

    def test_model_tool_call_does_not_substitute_for_native_execution_item(self):
        records = [json.loads(line) for line in self.trace.read_text().splitlines()]
        records = [record for record in records if record.get("payload", {}).get("type") != "item_completed"]
        records[-1]["payload"]["call_id"] = "native-call"
        self.trace.write_text("".join(json.dumps(record) + "\n" for record in records))
        with self.assertRaisesRegex(native.NativeEvidenceError, "matching naturally emitted"):
            native.observe(self.state, skills_root=ROOT)

    def test_foreign_command_thread_cannot_borrow_coordinator_metadata(self):
        records = [json.loads(line) for line in self.trace.read_text().splitlines()]
        records[-1]["payload"]["thread_id"] = "foreign-thread"
        self.trace.write_text("".join(json.dumps(record) + "\n" for record in records))
        with self.assertRaisesRegex(native.NativeEvidenceError, "foreign session"):
            native.observe(self.state, skills_root=ROOT)

    def test_unproven_command_execution_does_not_qualify(self):
        original = self.trace.read_text()
        for field, value in (("source", "foreign"), ("status", "in_progress"),
                             ("status", "declined"), ("exit_code", None), ("exit_code", True)):
            with self.subTest(field=field, value=value):
                records = [json.loads(line) for line in original.splitlines()]
                records[-1]["payload"]["item"][field] = value
                self.trace.write_text("".join(json.dumps(record) + "\n" for record in records))
                with self.assertRaisesRegex(native.NativeEvidenceError, "matching naturally emitted"):
                    native.observe(self.state, skills_root=ROOT)

    def test_large_unrelated_worker_trace_does_not_block_coordinator(self):
        worker = self.trace.parent / "worker.jsonl"
        self.write(worker, {"type": "session_meta", "payload": {"id": "unrelated-worker"}})
        with worker.open("ab") as stream:
            stream.truncate(native.MAX_PREFIX + 1)
        value = native.observe(self.state, skills_root=ROOT)
        self.assertEqual(value["owner_session_hash"], self.startup["owner_session_hash"])

    def test_malformed_session_metadata_has_sanitized_failure(self):
        self.write(self.trace, {"type": "session_meta", "payload": None})
        with self.assertRaisesRegex(native.NativeEvidenceError, "metadata is malformed"):
            native.observe(self.state, skills_root=ROOT)

    def test_foreign_session_and_unsupported_version_are_rejected(self):
        original = self.trace.read_text()
        for old, new in ((SESSION, "foreign-session"), ("0.170.0", "unsupported"),
                         ("0.170.0", "0.166.0")):
            with self.subTest(new=new):
                self.trace.write_text(original.replace(old, new))
                with self.assertRaises(native.NativeEvidenceError):
                    native.observe(self.state, skills_root=ROOT)
        self.trace.write_text(original)

    def test_unmatched_turn_or_tool_id_is_rejected(self):
        original = self.hooks.read_text()
        for old in ("native-turn", "native-call"):
            self.hooks.write_text(original.replace(old, "unmatched"))
            with self.assertRaises(native.NativeEvidenceError):
                native.observe(self.state, skills_root=ROOT)

    def test_prefix_tampering_fails_but_later_native_append_is_allowed(self):
        with self.trace.open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "response_item",
                        "payload": {"type": "message", "content": "later private body"},
                    }
                )
                + "\n"
            )
        native.validate(self.state, skills_root=ROOT)
        self.trace.write_text(
            self.trace.read_text().replace("native-call", "changed-call")
        )
        with self.assertRaises(native.NativeEvidenceError):
            native.validate(self.state, skills_root=ROOT)

    def test_forged_receipt_matches_are_rejected_even_when_resigned(self):
        path = self.run / native.RELATIVE
        value = json.loads(path.read_text())
        value["matches"][0]["native_line"] += 1
        self.write(path, value)
        self.state["native_activation"]["sha256"] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        with self.assertRaises(native.NativeEvidenceError):
            native.validate(self.state, skills_root=ROOT)

    def test_symlink_and_oversized_record_are_rejected(self):
        original = self.trace.read_bytes()
        other = self.run / "other.jsonl"
        other.write_bytes(original)
        self.trace.unlink()
        self.trace.symlink_to(other)
        with self.assertRaises(native.NativeEvidenceError):
            native.observe(self.state, skills_root=ROOT)
        self.trace.unlink()
        self.trace.write_bytes(b" " * (native.MAX_LINE + 1) + b"\n")
        self.trace.chmod(0o600)
        with self.assertRaises(native.NativeEvidenceError):
            native.observe(self.state, skills_root=ROOT)

    def test_old_startup_receipt_is_not_accepted_as_native_proof(self):
        self.startup["schema"] = "agentic-sdlc/workflow-startup-v2"
        self.startup["hook_discovery"] = self.startup.pop("source_state_discovery")
        self.write(self.startup_path, self.startup)
        self.state["workflow_startup"]["sha256"] = hashlib.sha256(
            self.startup_path.read_bytes()
        ).hexdigest()
        with self.assertRaises(native.NativeEvidenceError):
            native.validate(self.state, skills_root=ROOT)


class NativeContinuationTests(unittest.TestCase):
    """A new process/owner alone cannot certify a public continuation."""

    write = NativeActivationTests.write

    def setUp(self):
        NativeActivationTests.setUp(self)
        from unittest import mock
        self.state["selected_project_root"] = str(self.run / "project/project-a")
        self.resumed_session = "resumed-native-session"
        owner = hashlib.sha256(self.resumed_session.encode()).hexdigest()
        self.enterContext(mock.patch.object(native.target, "_registration",
                          return_value=(None, None, None, {"owner_session_hash": owner})))
        self.command = ["python3", "-B", str(ROOT / "worktree/scripts/workflow_intake.py"),
                        "--owner", "agentic-sdlc", "continue", "--project-root",
                        self.state["selected_project_root"]]
        self.records = [{"type": "session_meta", "payload": {
            "id": self.resumed_session, "source": "cli", "cli_version": "0.170.0"}}]
        for identity, command in (("continue-call", self.command), ("status-call", ["python3", "-V"])):
            self.records.append({"type": "event_msg", "payload": {
                "type": "item_completed", "thread_id": self.resumed_session, "turn_id": "resume-turn",
                "item": {"type": "CommandExecution", "id": identity, "status": "completed",
                         "source": "unified_exec_startup", "exit_code": 0, "command": command}}})
        self.resumed_trace = self.trace.parent / "rollout-resumed.jsonl"
        self.write_trace()
        with self.hooks.open("a") as stream:
            stream.write(json.dumps({"event": "PreToolUse", "project_id": self.startup["project_id"],
                "run_id": self.startup["run_id"], "turn_id": "resume-turn",
                "tool_use_id": "status-call", "decision": "allow"}) + "\n")

    def write_trace(self):
        self.resumed_trace.write_text("".join(json.dumps(r) + "\n" for r in self.records))
        self.resumed_trace.chmod(0o600)

    def test_successful_native_continuation_has_bounded_correlated_receipt(self):
        value = native.observe_continuation(self.state, skills_root=ROOT, agent="codex")
        native.validate_continuation(self.state, value, skills_root=ROOT, agent="codex")
        self.assertEqual(value["operation"]["tool_use_id"], "continue-call")
        self.assertEqual(value["operation"]["followup"]["tool_use_id"], "status-call")
        self.assertNotIn("command", json.dumps(value))
        native.validate(self.state, skills_root=ROOT)

    def test_failed_unrelated_or_compound_continue_cannot_qualify(self):
        import copy
        original = copy.deepcopy(self.records)
        for mutation in ("failed", "wrong-project", "wrong-action", "compound", "foreign-thread"):
            with self.subTest(mutation=mutation):
                self.records = copy.deepcopy(original)
                payload = self.records[1]["payload"]
                item = payload["item"]
                if mutation == "failed":
                    item.update(status="failed", exit_code=2)
                elif mutation == "wrong-project":
                    item["command"][-1] += "-foreign"
                elif mutation == "wrong-action":
                    item["command"][5] = "status"
                elif mutation == "compound":
                    item["command"] = ["/bin/zsh", "-c", native.shlex.join(self.command) + "; true"]
                else:
                    payload["thread_id"] = SESSION
                self.write_trace()
                with self.assertRaises(native.NativeEvidenceError):
                    native.observe_continuation(self.state, skills_root=ROOT, agent="codex")

    def test_direct_shell_continuation_is_supported_but_tampering_fails(self):
        self.records[1]["payload"]["item"]["command"] = ["/bin/zsh", "-c", native.shlex.join(self.command)]
        self.write_trace()
        value = native.observe_continuation(self.state, skills_root=ROOT, agent="codex")
        value["operation"]["native_line"] += 1
        with self.assertRaisesRegex(native.NativeEvidenceError, "correlation changed"):
            native.validate_continuation(self.state, value, skills_root=ROOT, agent="codex")


if __name__ == "__main__":
    unittest.main()
