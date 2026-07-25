#!/usr/bin/env python3
"""Disposable tests for the remediation-attempt Codex hook."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


HOOK = Path(__file__).resolve().parents[1] / "remediation_attempt_guard.py"
SPEC = importlib.util.spec_from_file_location("remediation_attempt_guard", HOOK)
assert SPEC and SPEC.loader
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def attempt(
    number: int, *, distinct_key: str | None = None, result: str = "failed_same_blocker"
) -> dict[str, str]:
    return {
        "id": f"attempt-{number}",
        "distinct_key": distinct_key or f"hypothesis-{number}|variable-{number}|target",
        "hypothesis": f"hypothesis {number}",
        "remediation": f"remediation {number}",
        "verification": f"verification {number}",
        "result": result,
    }


def state_data(**updates: object) -> dict[str, object]:
    data: dict[str, object] = {
        "schema": guard.SCHEMA,
        "blocker_key": "component|operation|error-class|boundary",
        "blocker_summary": "The same bounded operation still fails.",
        "tranche": 1,
        "started_at": "2026-01-01T00:30:00Z",
        "active_seconds": 0,
        "attempt_limit": 3,
        "time_limit_minutes": 60,
        "attempts": [],
        "status": "active",
        "stop_trigger": None,
        "override_summary": None,
    }
    data.update(updates)
    return data


def complete_report(*, stop_trigger: str = "attempt_limit") -> str:
    return "\n".join(
        [
            guard.REPORT_MARKER,
            f"Stop trigger: {stop_trigger}",
            "## Outcome",
            "Unresolved after the bounded remediation tranche.",
            "## Blocking Error",
            "A redacted error summary.",
            "## Source",
            "The affected component and bounded log location.",
            "## Attempts",
            "Three concise attempt summaries.",
            "## Evidence",
            "Observed facts and negative evidence.",
            "## Current State",
            "No additional remediation was attempted.",
            "## Next Action",
            "User review is required.",
        ]
    )


class RemediationAttemptGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "workspace"
        self.root.mkdir()
        self.home = Path(self.tmp.name) / "codex"
        self.home.mkdir()
        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.home)})
        self.env.start()
        self.payload: dict[str, object] = {
            "session_id": "session-1",
            "cwd": str(self.root),
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "run-check"},
        }

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def write_state(self, data: object, *, raw: str | None = None) -> Path:
        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        state_file.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(data, sort_keys=True) if raw is None else raw
        state_file.write_text(
            f"# Current state\n\n{guard.MARKER_START}{body}{guard.MARKER_END}\n",
            encoding="utf-8",
        )
        return state_file

    def evaluate_pre_tool(self) -> dict[str, object]:
        return guard.evaluate(self.payload)

    def test_missing_marker_and_missing_session_fail_open(self) -> None:
        self.assertEqual(self.evaluate_pre_tool(), {})
        self.payload.pop("session_id")
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_three_unique_failed_attempts_deny_next_tool(self) -> None:
        self.write_state(state_data(attempts=[attempt(1), attempt(2), attempt(3)]))
        output = self.evaluate_pre_tool()
        decision = output["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("budget exhausted", decision["permissionDecisionReason"])

    def test_duplicate_distinct_key_does_not_consume_another_attempt(self) -> None:
        repeated = "same-hypothesis|same-variable|same-target"
        self.write_state(
            state_data(
                attempts=[
                    attempt(1, distinct_key=repeated),
                    attempt(2, distinct_key=repeated),
                    attempt(3),
                ]
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_time_limit_is_inclusive_and_uses_active_time(self) -> None:
        self.write_state(
            state_data(
                active_seconds=3600,
                attempt_limit=10,
                time_limit_minutes=60,
                override_summary="The current user raised the attempt limit.",
            )
        )
        output = self.evaluate_pre_tool()
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_idle_wall_clock_time_does_not_consume_active_time(self) -> None:
        self.write_state(
            state_data(
                started_at="2020-01-01T00:00:00Z",
                active_seconds=3599,
                attempt_limit=10,
                time_limit_minutes=60,
                override_summary="The current user raised the attempt limit.",
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_resolved_state_does_not_block(self) -> None:
        self.write_state(
            state_data(
                attempts=[attempt(1), attempt(2), attempt(3)],
                status="resolved",
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_explicit_unlimited_override_is_valid(self) -> None:
        self.write_state(
            state_data(
                attempt_limit=None,
                time_limit_minutes=None,
                override_summary="The current user explicitly requested no limit.",
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_nondefault_limits_without_override_fail_closed(self) -> None:
        self.write_state(state_data(attempt_limit=4))
        output = self.evaluate_pre_tool()
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

        self.write_state(state_data(attempt_limit=3, time_limit_minutes=None))
        output = self.evaluate_pre_tool()
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

        self.write_state(state_data(attempt_limit=None, time_limit_minutes=None))
        output = self.evaluate_pre_tool()
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(
            "present but invalid",
            output["hookSpecificOutput"]["permissionDecisionReason"],
        )

    def test_noncanonical_attempt_fails_with_repairable_reason(self) -> None:
        self.write_state(
            state_data(
                attempts=[
                    {
                        "attempt": 1,
                        "finished_at": "2026-01-01T00:45:00Z",
                        "result": "failed",
                        "error": "same blocker",
                    }
                ]
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "attempt 1 id must be a non-empty bounded string",
            output["permissionDecisionReason"],
        )
        self.assertIn(
            "does not consume an attempt or exhaust",
            output["permissionDecisionReason"],
        )

    def test_unsupported_result_names_the_canonical_repair(self) -> None:
        invalid_result = "different_blocker"
        self.write_state(state_data(attempts=[attempt(1, result=invalid_result)]))

        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        reason = output["permissionDecisionReason"]
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertIn(
            "attempt 1 result must be failed_same_blocker or succeeded",
            reason,
        )
        self.assertIn("record unverified progress in prose", reason)
        self.assertIn("fresh empty attempt ledger", reason)
        self.assertNotIn(invalid_result, reason)

    def test_continuation_tranche_requires_user_override_summary(self) -> None:
        self.write_state(state_data(tranche=2))
        output = self.evaluate_pre_tool()
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_status_trigger_and_limit_state_must_be_consistent(self) -> None:
        self.write_state(state_data(stop_trigger="attempt_limit"))
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "active state requires a null stop_trigger",
            output["permissionDecisionReason"],
        )

        self.write_state(
            state_data(
                attempts=[attempt(1)],
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "failed-attempt limit to be reached",
            output["permissionDecisionReason"],
        )

        self.write_state(
            state_data(
                active_seconds=1,
                status="exhausted",
                stop_trigger="time_limit",
            )
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        self.assertIn(
            "active-time limit to be reached",
            output["permissionDecisionReason"],
        )

    def test_marker_repair_preserves_active_budget(self) -> None:
        state_file = self.write_state(
            state_data(
                attempts=[
                    {
                        "attempt": 1,
                        "result": "failed",
                    }
                ]
            )
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Update File: {state_file}",
                            "@@",
                            "-old",
                            "+new",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

        self.write_state(state_data(attempts=[attempt(1), attempt(2)]))
        self.payload.update(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "run-check"},
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "Marker repaired; work remains.",
                "stop_hook_active": False,
            }
        )
        self.assertEqual(guard.evaluate(self.payload), {"continue": True})

    def test_causally_new_blocker_starts_a_fresh_budget(self) -> None:
        self.write_state(
            state_data(
                attempts=[attempt(1), attempt(2), attempt(3)],
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        self.write_state(
            state_data(
                blocker_key="other-component|other-operation|other-error|other-boundary",
                blocker_summary="A causally independent operation now fails.",
                tranche=1,
                started_at="2026-01-01T01:00:00Z",
                active_seconds=0,
                attempts=[],
                status="active",
                stop_trigger=None,
                override_summary=None,
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_only_exact_current_state_patch_is_allowed_after_exhaustion(self) -> None:
        state_file = self.write_state(
            state_data(
                attempts=[attempt(1), attempt(2), attempt(3)],
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Update File: {state_file}",
                            "@@",
                            "-old",
                            "+new",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(self.evaluate_pre_tool(), {})
        self.payload["tool_input"] = {
            "command": "\n".join(
                [
                    "*** Begin Patch",
                    f"*** Update File: {state_file}",
                    "@@",
                    "-old",
                    "+new",
                    f"*** Update File: {self.root / 'other.txt'}",
                    "@@",
                    "-old",
                    "+new",
                    "*** End Patch",
                ]
            )
        }
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_malformed_and_oversized_markers_fail_closed(self) -> None:
        self.write_state({}, raw="{")
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_invalid_state_reason_does_not_reflect_paths_or_marker_content(
        self,
    ) -> None:
        state_file = self.write_state(
            {},
            raw='{"private_value": "/private/internal-host/current.md",',
        )
        output = self.evaluate_pre_tool()["hookSpecificOutput"]
        reason = output["permissionDecisionReason"]
        self.assertIn("marker JSON is malformed", reason)
        self.assertNotIn("private_value", reason)
        self.assertNotIn("/private/internal-host/current.md", reason)

        self.write_state(state_data())
        with patch.object(
            Path,
            "open",
            side_effect=PermissionError(
                f"permission denied: {state_file.parent / 'private-current.md'}"
            ),
        ):
            output = self.evaluate_pre_tool()["hookSpecificOutput"]
        reason = output["permissionDecisionReason"]
        self.assertIn("task-state marker could not be read safely", reason)
        self.assertNotIn("private-current.md", reason)
        self.assertLessEqual(len(reason), 1024)

        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        state_file.write_text(
            f"{guard.MARKER_START}{json.dumps(state_data())}"
            + (" " * guard.MAX_MARKER_PREFIX_BYTES)
            + guard.MARKER_END,
            encoding="utf-8",
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_large_task_state_after_bounded_marker_is_allowed(self) -> None:
        state_file = self.write_state(state_data())
        with state_file.open("a", encoding="utf-8") as handle:
            handle.write("x" * guard.MAX_MARKER_PREFIX_BYTES)
        self.assertEqual(self.evaluate_pre_tool(), {})

    def test_marker_after_prefix_and_oversized_task_state_fail_closed(self) -> None:
        state_file = guard.state_file_for_payload(self.payload)
        assert state_file is not None
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(
            ("x" * guard.MAX_MARKER_PREFIX_BYTES)
            + guard.MARKER_START
            + json.dumps(state_data())
            + guard.MARKER_END,
            encoding="utf-8",
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        state_file.write_bytes(b"x" * (guard.MAX_TASK_STATE_BYTES + 1))
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_symlinked_state_fails_closed(self) -> None:
        state_file = self.write_state(state_data())
        state_file.unlink()
        target = Path(self.tmp.name) / "unsafe-state"
        target.write_text("unsafe", encoding="utf-8")
        state_file.symlink_to(target)
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

        self.payload.update(
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "\n".join(
                        [
                            "*** Begin Patch",
                            f"*** Update File: {state_file}",
                            "@@",
                            "-old",
                            "+new",
                            "*** End Patch",
                        ]
                    )
                },
            }
        )
        self.assertEqual(
            self.evaluate_pre_tool()["hookSpecificOutput"]["permissionDecision"],
            "deny",
        )

    def test_stop_requires_complete_report_once_then_stops(self) -> None:
        self.write_state(
            state_data(
                attempts=[attempt(1), attempt(2), attempt(3)],
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The command still fails.",
                "stop_hook_active": False,
            }
        )
        first = guard.evaluate(self.payload)
        self.assertEqual(first["decision"], "block")
        self.assertIn("Do not troubleshoot further", first["reason"])

        self.payload["stop_hook_active"] = True
        second = guard.evaluate(self.payload)
        self.assertFalse(second["continue"])
        self.assertIn("incomplete", second["stopReason"])

    def test_stop_requests_marker_repair_without_exhaustion_report(self) -> None:
        self.write_state({}, raw="{")
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": "The marker needs repair.",
                "stop_hook_active": False,
            }
        )
        output = guard.evaluate(self.payload)
        self.assertEqual(output["decision"], "block")
        self.assertIn("Repair only the exact advertised current.md", output["reason"])
        self.assertIn("do not exhaust", output["reason"])
        self.assertNotIn(guard.REPORT_MARKER, output["reason"])
        self.assertNotIn("## Outcome", output["reason"])

    def test_stop_rejects_inexact_heading_or_missing_stop_trigger(self) -> None:
        self.write_state(
            state_data(
                active_seconds=3600,
                status="exhausted",
                stop_trigger="time_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_report().replace(
                    "## Source", "## Source Details"
                ),
                "stop_hook_active": False,
            }
        )
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

        self.payload["last_assistant_message"] = complete_report()
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

        self.payload["last_assistant_message"] = "\n".join(
            [
                guard.REPORT_MARKER,
                "Stop trigger: time_limit",
                *guard.REPORT_HEADINGS,
            ]
        )
        self.assertEqual(guard.evaluate(self.payload)["decision"], "block")

    def test_complete_report_stops_even_when_other_stop_hooks_might_continue(
        self,
    ) -> None:
        self.write_state(
            state_data(
                attempts=[attempt(1), attempt(2), attempt(3)],
                status="exhausted",
                stop_trigger="attempt_limit",
            )
        )
        self.payload.update(
            {
                "hook_event_name": "Stop",
                "last_assistant_message": complete_report(),
                "stop_hook_active": False,
            }
        )
        output = guard.evaluate(self.payload)
        self.assertFalse(output["continue"])
        self.assertIn("report delivered", output["stopReason"])

    def test_fresh_continuation_tranche_is_allowed(self) -> None:
        self.write_state(
            state_data(
                tranche=2,
                started_at="2026-01-01T00:59:00Z",
                attempts=[],
                override_summary="The current user said continue for three more attempts.",
            )
        )
        self.assertEqual(self.evaluate_pre_tool(), {})


if __name__ == "__main__":
    unittest.main()
