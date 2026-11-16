"""Logging records the decision's observation without rediscovering authority."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / "pre_tool_use_sdlc_policy.py"
SPEC = importlib.util.spec_from_file_location("policy_observation_test", PATH)
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class PolicyObservationTests(unittest.TestCase):
    def test_allow_and_deny_log_the_single_authority_observation(self):
        active = SimpleNamespace(project_root=PATH.parent, project_id="project-test",
                                 run_id="run-test", history_dir=PATH.parent / "history")
        payload = {"hook_event_name": "PreToolUse", "cwd": str(PATH.parent),
                   "session_id": "test", "tool_name": "mcp__example__read", "tool_input": {}}
        for reason in (None, "Blocked: forbidden operation"):
            with self.subTest(reason=reason), \
                    mock.patch.object(policy, "load_active_state", return_value=(active, None, {}, None)) as load, \
                    mock.patch.object(policy, "mcp_policy_reason", return_value=reason), \
                    mock.patch.object(policy, "append_jsonl") as append:
                result = policy.evaluate(payload)
                self.assertEqual(load.call_count, 1)
                self.assertEqual(append.call_args.args[1]["run_id"], active.run_id)
                self.assertEqual(append.call_args.args[1]["decision"], "deny" if reason else "allow")
                self.assertEqual(result.get("hookSpecificOutput", {}).get("permissionDecision"),
                                 "deny" if reason else None)

    def test_corrupt_state_is_denied_without_second_discovery(self):
        with mock.patch.object(policy, "load_active_state", side_effect=json.JSONDecodeError("bad", "", 0)) as load, \
                mock.patch.object(policy, "append_jsonl") as append:
            result = policy.evaluate({"hook_event_name": "PreToolUse", "tool_name": "Bash"})
            self.assertEqual(result["hookSpecificOutput"]["permissionDecision"], "deny")
            self.assertEqual(load.call_count, 1)
            append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
