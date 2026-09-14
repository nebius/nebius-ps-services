#!/usr/bin/env python3
"""Deterministic tests of the native evaluation adapters and evidence boundary."""

import importlib.util
from contextlib import nullcontext
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("skill_evals", Path(__file__).with_name("run-skill-evals.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class EvalTests(unittest.TestCase):
    def test_malformed_short_quality_grade_is_unavailable_without_crashing(self):
        current = {"state": "RUNTIME_PASS", "response": "fixture", "loaded": True,
                   "models": ["fixture"]}
        valid_assertion = {"pass": True, "evidence": "fixture preserves the rule"}
        for checks, expected in (
            (["invalid"], "UNAVAILABLE"),
            ([None], "UNAVAILABLE"),
            ({"claim": valid_assertion}, "UNAVAILABLE"),
            ([valid_assertion], "FAIL"),
        ):
            with self.subTest(checks=checks):
                grade = {"assertions": checks, "no_regression": True}
                with patch.object(runner, "run_case", side_effect=[
                    dict(current), dict(current),
                    {**current, "response": json.dumps(grade)},
                ]):
                    result = runner.evaluate(
                        "codex", Path("candidate"), {"assertions": ["one", "two"]},
                        "quality", Path("baseline"),
                    )
                self.assertEqual(result["state"], expected)

    def test_task_owned_temp_root_alias_is_resolved_before_containment(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            root = base / "skill"
            root.mkdir()
            (root / "SKILL.md").write_text("---\nname: skill\n---\nRead the instructions.\n")
            actual = base / "actual"
            actual.mkdir()
            alias = base / "alias"
            alias.symlink_to(actual, target_is_directory=True)
            for agent in ("codex", "claude"):
                (actual / agent).mkdir()
                with patch.object(runner.tempfile, "TemporaryDirectory", return_value=nullcontext(str(alias / agent))), \
                        patch.object(runner.shutil, "which", return_value="/usr/bin/true"):
                    result = runner.run_case(agent, root, {"prompt": "Read the skill"})
                    self.assertEqual(result["state"], "UNAVAILABLE")

    def test_payload_special_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            os.mkfifo(root / "input")
            with self.assertRaises(ValueError):
                runner.validate_payload(root)

    def test_payload_symlinks_are_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            root = base / "skill"
            root.mkdir()
            (root / "SKILL.md").write_text("---\nname: skill\n---\nRead the instructions.\n")
            outside = base / "outside"
            outside.mkdir()
            (outside / "fixture.md").write_text("external fixture")
            for target in (outside, outside / "fixture.md"):
                with self.subTest(target=target.name):
                    link = root / ("payload-" + target.name)
                    link.symlink_to(target)
                    with patch.object(runner.shutil, "which", return_value="/bin/true"), patch.object(runner.subprocess, "Popen") as launch:
                        with self.assertRaises(ValueError):
                            runner.run_case("codex", root, {"prompt": "Read the skill"})
                        launch.assert_not_called()
                    link.unlink()

    def test_discarded_or_empty_skill_reads_do_not_confirm_loading(self):
        for command in ("cat /x/align-skill/SKILL.md >/dev/null", "head -n 0 /x/align-skill/SKILL.md"):
            trace = [{"type": "item.completed", "item": {"type": "command_execution", "command": command,
                                                          "exit_code": 0, "aggregated_output": ""}}]
            with self.subTest(command=command):
                self.assertFalse(runner.observed_loading(trace, "align-skill"))

    def test_loading_requires_successful_native_tool_evidence(self):
        use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "read-1", "name": "Skill", "input": {"skill": "skills:align-skill"}}]}}
        success = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "read-1", "content": "loaded"}]}}
        denied = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "read-1", "is_error": True}]}}
        self.assertFalse(runner.observed_loading([use], "align-skill"))
        self.assertFalse(runner.observed_loading([use, denied], "align-skill"))
        self.assertFalse(runner.observed_loading([{"type": "result", "result": [use, success]}], "align-skill"))
        self.assertTrue(runner.observed_loading([use, success], "align-skill"))
        skill_text = "---\nname: align-skill\n---\n# Align skill\nPreserve the workflow contract.\n"
        for command, code, expected in (("cat /x/align-skill/SKILL.md", 0, True), ("echo /x/align-skill/SKILL.md", 0, False), ("cat /x/align-skill/SKILL.md", 1, False),
                                        ("/bin/zsh -lc 'sed -n 1,220p /x/align-skill/SKILL.md'", 0, True),
                                        ("cat /x/align-skill/SKILL.md >/dev/null", 0, False)):
            trace = [{"type": "item.completed", "item": {"type": "command_execution", "command": command, "exit_code": code,
                                                          "aggregated_output": skill_text}}]
            self.assertEqual(runner.observed_loading(trace, "align-skill", skill_text), expected)
        trace[0]["item"].update(command="head -n 3 /x/align-skill/SKILL.md", aggregated_output="---\nname: align-skill\n---\n")
        self.assertFalse(runner.observed_loading(trace, "align-skill", skill_text))
        self.assertFalse(runner.observed_loading([{"item": trace[0]}], "align-skill"))

    def test_quality_cannot_pass_without_candidate_and_baseline_loading(self):
        current = {"state": "RUNTIME_PASS", "response": "result", "loaded": True, "models": ["fixture"]}
        for outcomes in ([{**current, "loaded": False}], [dict(current), {**current, "loaded": False}]):
            with patch.object(runner, "run_case", side_effect=outcomes):
                result = runner.evaluate("claude", Path("align-skill"), {"assertions": ["preserve rule"]}, "quality", Path("baseline"))
                self.assertEqual(result["state"], "UNAVAILABLE")

    def test_trigger_mismatch_and_unavailable_remain_distinct(self):
        root = Path("align-skill")
        for agent in ("codex", "claude"):
            with patch.object(runner, "run_case", return_value={"state": "RUNTIME_PASS", "response": "result", "loaded": False}):
                self.assertEqual(runner.evaluate(agent, root, {"should_trigger": "true"}, "triggers", None)["state"], "FAIL")
            with patch.object(runner, "run_case", return_value={"state": "UNAVAILABLE", "reason": "auth"}):
                self.assertEqual(runner.evaluate(agent, root, {}, "triggers", None)["state"], "UNAVAILABLE")

    def test_quality_requires_baseline_and_independent_assertion_evidence(self):
        current = {"state": "RUNTIME_PASS", "response": "result", "loaded": True, "models": ["fixture"]}
        with patch.object(runner, "run_case", return_value=dict(current)):
            self.assertEqual(runner.evaluate("claude", Path("align-skill"), {}, "quality", None)["state"], "UNAVAILABLE")
        grade = {"assertions": [{"pass": True, "evidence": "candidate file preserves the rule"}], "no_regression": True}
        with patch.object(runner, "run_case", side_effect=[dict(current), dict(current), {**current, "response": json.dumps(grade)}]):
            result = runner.evaluate("codex", Path("align-skill"), {"assertions": ["preserve rule"]}, "quality", Path("baseline"))
            self.assertEqual(result["state"], "QUALITY_PASS")
            self.assertTrue(result["assertions"][0]["evidence"])

    def test_fixture_containment_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "safe.md").write_text("fixture")
            (root / "link.md").symlink_to(root / "safe.md")
            for path in ("../outside", "link.md"):
                with self.assertRaises(ValueError):
                    runner.safe_file(root, path)

    def test_redaction(self):
        secret = "sk-" + "X" * 32
        self.assertNotIn(secret, runner.public_text("API result: " + secret))


if __name__ == "__main__":
    unittest.main()
