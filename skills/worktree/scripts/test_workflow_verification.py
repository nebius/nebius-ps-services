import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import workflow_verification as owner


class VerificationTests(unittest.TestCase):
    def test_exact_inventory_preserves_same_method_in_different_classes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "test_fixture.py"
            path.write_text("import unittest\nclass A(unittest.TestCase):\n def test_same(self): pass\nclass B(unittest.TestCase):\n def test_same(self): pass\n")
            group = owner.Group("one", "suite", (str(path),), root)
            rows = owner.schedule([group], os.environ.copy(), memory_probe=lambda: {"status": "normal"})
            self.assertEqual(rows[0]["status"], "PASS")
            self.assertEqual(set(rows[0]["inventory"]), {"test_fixture.A.test_same", "test_fixture.B.test_same"})
            self.assertEqual(set(rows[0]["outcomes"]), set(rows[0]["inventory"]))

    def test_missing_module_stops_before_execution(self):
        group = owner.Group("missing", "suite", ("/missing/test_fixture.py",), Path.cwd())
        rows = owner.schedule([group], os.environ.copy(), memory_probe=lambda: {"status": "normal"})
        self.assertEqual(rows[0]["status"], "FAIL")
        self.assertEqual(rows[0]["outcomes"], {})

    def test_collected_identity_survives_fixture_attribute_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "test_fixture.py"
            path.write_text(
                "import unittest\nclass A(unittest.TestCase):\n"
                " def setUp(self): self.id = 'fixture-value'\n"
                " def test_success(self): pass\n"
            )
            rows = owner.schedule([owner.Group("one", "suite", (str(path),), root)],
                                  os.environ.copy(), memory_probe=lambda: {"status": "normal"})
            self.assertEqual(rows[0]["status"], "PASS")
            self.assertEqual(rows[0]["outcomes"], {"test_fixture.A.test_success": "passed"})

    def test_fixture_identity_changes_preserve_failure_and_subtest_outcomes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "test_fixture.py"
            path.write_text(
                "import unittest\nclass A(unittest.TestCase):\n"
                " def setUp(self): self.id = lambda: 'changed-identity'\n"
                " def test_failure(self): self.fail('expected negative control')\n"
                " def test_subtest(self):\n"
                "  with self.subTest(item=1): self.fail('expected negative control')\n"
                " def test_success(self): pass\n"
            )
            rows = owner.schedule([owner.Group("one", "suite", (str(path),), root)],
                                  os.environ.copy(), memory_probe=lambda: {"status": "normal"})
            self.assertEqual(rows[0]["status"], "FAIL")
            self.assertEqual(rows[0]["outcomes"], {
                "test_fixture.A.test_failure": "failed",
                "test_fixture.A.test_subtest": "failed",
                "test_fixture.A.test_success": "passed",
            })
            self.assertTrue(rows[0]["observation"]["local_group_quiescent"])

    def test_at_most_two_audited_groups_and_exclusive_barrier(self):
        groups = [owner.Group(str(i), "suite", (), Path.cwd(), i < 3) for i in range(5)]
        active = set()
        peaks = []
        guard = threading.Lock()
        def run(group, env, *, inventory=False, cancel=None):
            if not inventory:
                with guard:
                    active.add(group.id)
                    peaks.append(set(active))
                time.sleep(.03)
                with guard:
                    active.remove(group.id)
            return {"id": group.id, "suite": group.suite, "status": "PASS", "inventory": [group.id],
                    "outcomes": {} if inventory else {group.id: "passed"}, "observation": {"local_group_quiescent": True}}
        with patch.object(owner, "_run", side_effect=run):
            rows = owner.schedule(groups, {}, memory_probe=lambda: {"status": "normal"})
        self.assertTrue(all(row["status"] == "PASS" for row in rows))
        self.assertEqual(max(map(len, peaks)), 2)
        self.assertTrue(all(len(peak) == 1 for peak in peaks if peak & {"3", "4"}))

    def test_required_failure_cancels_peer_and_marks_queue_not_run(self):
        groups = [owner.Group(str(i), "suite", (), Path.cwd(), i < 2) for i in range(3)]
        peer_started = threading.Event()
        def run(group, env, *, inventory=False, cancel=None):
            status = "PASS"
            if not inventory:
                if group.id == "0":
                    peer_started.wait(2)
                    status = "FAIL"
                else:
                    peer_started.set()
                    cancel.wait(2)
                    status = "INTERRUPTED"
            return {"id": group.id, "suite": group.suite, "status": status,
                    "inventory": [group.id], "outcomes": {}, "observation": {"local_group_quiescent": True}}
        with patch.object(owner, "_run", side_effect=run):
            rows = owner.schedule(groups, {}, memory_probe=lambda: {"status": "normal"})
        self.assertEqual([r["status"] for r in rows], ["FAIL", "INTERRUPTED", "NOT_RUN"])

    def test_changed_collection_cannot_pass(self):
        group = owner.Group("one", "suite", (), Path.cwd())
        def run(group, env, *, inventory=False, cancel=None):
            return {"id": group.id, "suite": group.suite, "status": "PASS", "inventory": ["old" if inventory else "new"],
                    "outcomes": {}, "observation": {"local_group_quiescent": True}}
        with patch.object(owner, "_run", side_effect=run):
            rows = owner.schedule([group], {}, memory_probe=lambda: {"status": "normal"})
        self.assertEqual(rows[0]["status"], "FAIL")

    def test_unknown_memory_admits_no_heavy_group(self):
        group = owner.Group("one", "suite", (), Path.cwd())
        with patch.object(owner, "_run") as run:
            rows = owner.schedule([group], {}, memory_probe=lambda: {"status": "unknown"})
        run.assert_not_called()
        self.assertEqual(rows[0]["status"], "NOT_RUN")

    def test_duplicate_or_uncollected_events_are_rejected(self):
        events = owner.Events()
        events.line(owner.PREFIX + b'{"event":"inventory","ids":["a"]}')
        for _ in range(2):
            events.line(owner.PREFIX + b'{"event":"test","id":"a","outcome":"passed"}')
        self.assertTrue(events.invalid)

    def test_incomplete_pipe_cannot_certify_complete_looking_events(self):
        process = owner.source_module("workflow_process", Path(owner.__file__).with_name("workflow_process.py"))
        def run(*args, **kwargs):
            kwargs["on_stdout"](owner.PREFIX + b'{"event":"inventory","ids":["a"]}\n'
                + owner.PREFIX + b'{"event":"test","id":"a","outcome":"passed"}\n'
                + owner.PREFIX + b'{"event":"complete","successful":true,"count":1}\n')
            return {"local_group_quiescent": True, "exit_code": 0, "stop_reason": "exited",
                    "stdout": {"complete": False}, "stderr": {"complete": True}}
        with patch.object(process, "run_owned", side_effect=run):
            result = owner._run(owner.Group("one", "suite", (), Path.cwd()), {})
        self.assertEqual(result["status"], "EXECUTION_UNPROVEN")

    def test_memory_floor_during_collection_stops_before_test_admission(self):
        started = threading.Event()
        calls = []
        def run(group, env, *, inventory=False, cancel=None):
            calls.append(inventory)
            started.set()
            self.assertTrue(cancel.wait(3))
            return {"id": group.id, "suite": group.suite, "status": "INTERRUPTED",
                    "inventory": None, "outcomes": {}, "observation": {"local_group_quiescent": True}}
        def memory():
            return {"status": "critical" if started.is_set() else "normal"}
        with patch.object(owner, "_run", side_effect=run):
            rows = owner.schedule([owner.Group("one", "suite", (), Path.cwd())], {}, memory_probe=memory)
        self.assertEqual(calls, [True])
        self.assertEqual(rows[0]["status"], "INTERRUPTED")

    def test_interruption_retains_completed_peer_and_queued_inventory(self):
        groups = [owner.Group(str(i), "suite", (), Path.cwd()) for i in range(3)]
        second_started = threading.Event()
        retained = {}
        def run(group, env, *, inventory=False, cancel=None):
            status = "PASS"
            if not inventory and group.id == "1":
                second_started.set()
                self.assertTrue(cancel.wait(3))
                status = "INTERRUPTED"
            return {"id": group.id, "suite": group.suite, "status": status, "inventory": [group.id],
                    "outcomes": {group.id: "passed"} if not inventory and status == "PASS" else {},
                    "observation": {"local_group_quiescent": True}}
        def memory():
            if second_started.is_set():
                raise KeyboardInterrupt()
            return {"status": "normal"}
        with patch.object(owner, "_run", side_effect=run):
            rows = owner.schedule(groups, {}, memory_probe=memory,
                                  on_result=lambda row: retained.update({row["id"]: row}))
        self.assertEqual([row["status"] for row in rows], ["PASS", "INTERRUPTED", "NOT_RUN"])
        self.assertEqual([retained[str(i)]["status"] for i in range(3)], ["PASS", "INTERRUPTED", "NOT_RUN"])
        self.assertEqual(retained["0"]["outcomes"], {"0": "passed"})
        self.assertEqual(retained["2"]["inventory"], ["2"])

    def test_collection_observer_failure_cancels_and_retains_collector(self):
        retained = []
        def run(group, env, *, inventory=False, cancel=None):
            self.assertTrue(inventory)
            self.assertTrue(cancel.wait(3))
            return {"id": group.id, "suite": group.suite, "status": "INTERRUPTED",
                    "inventory": None, "outcomes": {}, "observation": {"local_group_quiescent": True}}
        def checkpoint(value):
            raise RuntimeError("observer failed")
        with patch.object(owner, "_run", side_effect=run), self.assertRaisesRegex(RuntimeError, "observer failed"):
            owner.schedule([owner.Group("one", "suite", (), Path.cwd())], {},
                           memory_probe=lambda: {"status": "normal"}, on_result=retained.append,
                           on_checkpoint=checkpoint, observation_interval_seconds=0)
        self.assertEqual(retained[0]["status"], "INTERRUPTED")
        self.assertTrue(retained[0]["collection_observation"]["local_group_quiescent"])


if __name__ == "__main__":
    unittest.main()
