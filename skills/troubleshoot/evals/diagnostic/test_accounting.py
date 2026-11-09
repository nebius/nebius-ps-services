"""Tree accounting regression cases; synthetic traces are not native qualification."""

import copy
import unittest

from accounting import aggregate, native_accounting


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.people = [
            {
                "id": "root",
                "parent": None,
                "status": "completed",
                "trace_complete": True,
            },
            {
                "id": "child",
                "parent": "root",
                "status": "cancelled",
                "trace_complete": True,
            },
        ]
        self.actions = [
            {"agent": "root", "id": "a", "kind": "coordination", "signature": "spawn"},
            {"agent": "child", "id": "b", "kind": "diagnostic", "signature": "read"},
            {"agent": "child", "id": "c", "kind": "diagnostic", "signature": "read"},
        ]
        self.totals = [{"id": "inclusive", "covers": ["root", "child"], "tokens": 100}]

    def run_accounting(self, **changes):
        args = dict(
            participants=self.people,
            actions=self.actions,
            totals=self.totals,
            lineage_complete=True,
            stream_complete=True,
        )
        args.update(changes)
        return aggregate(**args)

    def test_inclusive_or_disjoint_totals_count_once(self):
        result = self.run_accounting()
        self.assertTrue(result["accounting_complete"])
        self.assertEqual(result["aggregate_tokens"], 100)
        self.assertEqual(result["participant_outcomes"]["child"], "cancelled")
        split = [
            {"id": "root", "covers": ["root"], "tokens": 40},
            {"id": "child", "covers": ["child"], "tokens": 60},
        ]
        self.assertEqual(self.run_accounting(totals=split)["aggregate_tokens"], 100)
        self.assertIsNone(
            self.run_accounting(totals=self.totals + split)["aggregate_tokens"]
        )
        self.assertEqual(
            self.run_accounting(totals=self.totals * 2)["aggregate_tokens"], 100
        )

    def test_duplicate_delivery_is_not_a_repeated_operation(self):
        result = self.run_accounting(actions=self.actions + [self.actions[-1]])
        self.assertEqual(result["diagnostic_actions"], 2)
        self.assertEqual(result["coordination_actions"], 1)
        self.assertEqual(result["duplicate_actions"], 1)
        self.assertEqual(result["duplicate_deliveries"], 1)
        self.assertIsNone(result["coordination_tokens"])
        changed = {**self.actions[-1], "signature": "different"}
        self.assertFalse(
            self.run_accounting(actions=self.actions + [changed])["accounting_complete"]
        )

    def test_missing_late_or_conflicting_evidence_cannot_qualify(self):
        for changes in (
            {"lineage_complete": False},
            {"stream_complete": False},
            {"totals": []},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(self.run_accounting(**changes)["accounting_complete"])
        for field, value in (
            ("status", "running"),
            ("trace_complete", False),
            ("parent", "child"),
        ):
            people = copy.deepcopy(self.people)
            people[1][field] = value
            self.assertFalse(
                self.run_accounting(participants=people)["accounting_complete"]
            )
        self.assertFalse(
            self.run_accounting(participants=self.people[:1])["accounting_complete"]
        )
        for value in (None, -1, True, float("nan"), "100"):
            totals = [{**self.totals[0], "tokens": value}]
            self.assertFalse(self.run_accounting(totals=totals)["accounting_complete"])
        self.assertFalse(
            self.run_accounting(actions=[{**self.actions[0], "id": None}])[
                "accounting_complete"
            ]
        )

    def claude_events(self):
        return [
            {"type": "system", "session_id": "root"},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "child",
                            "name": "Agent",
                            "input": {},
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "parent_tool_use_id": "child",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "read",
                            "name": "Read",
                            "input": {"file": "app.py"},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "parent_tool_use_id": "child",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "read",
                            "content": "source",
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "child",
                            "content": "findings",
                        }
                    ]
                },
            },
            {
                "type": "result",
                "is_error": False,
                "usage": {"input_tokens": 999},
                "modelUsage": {
                    "model": {
                        "inputTokens": 10,
                        "outputTokens": 20,
                        "cacheReadInputTokens": 30,
                        "cacheCreationInputTokens": 40,
                        "thinkingTokens": 5,
                    }
                },
            },
        ]

    def test_claude_inclusive_usage_and_forwarded_child_tools(self):
        events = self.claude_events()
        result = native_accounting(events, host="claude")
        self.assertTrue(result["accounting_complete"], result)
        self.assertEqual(result["aggregate_tokens"], 100)

        self.assertEqual(result["diagnostic_actions"], 1)
        self.assertEqual(result["coordination_actions"], 1)
        events.insert(3, events[2])
        result = native_accounting(events, host="claude")
        self.assertEqual(result["diagnostic_actions"], 1)
        self.assertEqual(result["aggregate_tokens"], 100)

    def test_foreground_task_identity_and_non_agent_tasks(self):
        events = self.claude_events()
        started = {
            "type": "system",
            "subtype": "task_started",
            "task_type": "local_agent",
            "task_id": "native-child",
            "tool_use_id": "child",
            "run_id": "run",
            "is_backgrounded": False,
        }
        events.insert(2, started)
        events[3]["agent_id"] = "native-child"
        self.assertTrue(native_accounting(events, host="claude")["accounting_complete"])
        events.insert(
            1,
            {
                "type": "system",
                "subtype": "task_started",
                "task_type": "local_bash",
                "task_id": "bash-task",
                "parent_task_id": "native-child",
            },
        )
        self.assertTrue(native_accounting(events, host="claude")["accounting_complete"])
        changed = copy.deepcopy(events)
        changed[3]["is_backgrounded"] = True
        self.assertFalse(
            native_accounting(changed, host="claude")["accounting_complete"]
        )
        changed = copy.deepcopy(events)
        changed[4]["agent_id"] = "unmatched"
        self.assertFalse(
            native_accounting(changed, host="claude")["accounting_complete"]
        )

    def test_claude_missing_child_or_inclusive_usage_is_incomplete(self):
        for index in (2, 3, 4, 5):
            events = self.claude_events()
            del events[index]
            self.assertFalse(
                native_accounting(events, host="claude")["accounting_complete"]
            )
        events = self.claude_events()
        del events[-1]["modelUsage"]
        self.assertFalse(
            native_accounting(events, host="claude")["accounting_complete"]
        )

    def test_terminal_conflicts_and_late_new_actions_are_incomplete(self):
        events = self.claude_events()
        conflicting = copy.deepcopy(events[-2])
        conflicting["message"]["content"][0]["is_error"] = True
        events.insert(-1, conflicting)
        self.assertFalse(
            native_accounting(events, host="claude")["accounting_complete"]
        )
        for index in (-1, 6):
            events = self.claude_events()
            late = copy.deepcopy(events[2])
            late["message"]["content"][0]["id"] = "late-read"
            events.insert(index, late)
            self.assertFalse(
                native_accounting(events, host="claude")["accounting_complete"]
            )
        events = self.claude_events()
        events += [events[2], events[-1]]
        self.assertTrue(native_accounting(events, host="claude")["accounting_complete"])

    def test_late_text_or_thinking_cannot_reuse_final_usage(self):
        for kind in ("text", "thinking"):
            for index in (-1, 6):
                events = self.claude_events()
                late = {
                    "type": "assistant",
                    "parent_tool_use_id": "child",
                    "message": {
                        "id": "new-message",
                        "content": [{"type": kind, kind: "late finding"}],
                    },
                }
                events.insert(index, late)
                self.assertFalse(
                    native_accounting(events, host="claude")["accounting_complete"]
                )
        events = self.claude_events()
        finding = {
            "type": "assistant",
            "parent_tool_use_id": "child",
            "message": {
                "id": "finding",
                "content": [{"type": "text", "text": "finding"}],
            },
        }
        events.insert(4, finding)
        events.append(finding)
        self.assertTrue(native_accounting(events, host="claude")["accounting_complete"])
        events = self.claude_events()
        events.insert(1, {"type": "system", "subtype": "task_started"})
        self.assertFalse(
            native_accounting(events, host="claude")["accounting_complete"]
        )

    def test_codex_root_counts_never_claim_descendant_coverage(self):
        action = {
            "type": "item.completed",
            "item": {"id": "a", "type": "command_execution", "command": "read"},
        }
        events = [
            {"type": "thread.started", "thread_id": "root"},
            action,
            action,
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 50,
                    "cached_input_tokens": 30,
                    "output_tokens": 10,
                },
            },
        ]
        result = native_accounting(events, host="codex")
        self.assertEqual(result["diagnostic_actions"], 1)
        self.assertEqual(result["observed_tokens"], 60)
        self.assertIsNone(result["aggregate_tokens"])
        self.assertFalse(result["accounting_complete"])
        events.append(events[-1])
        self.assertIsNone(native_accounting(events, host="codex")["observed_tokens"])


if __name__ == "__main__":
    unittest.main()
