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
                grade = {"A": {"assertions": checks}, "B": {"assertions": checks},
                         "A_preserves_B": True, "B_preserves_A": True}
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
        for outcomes in ([{**current, "loaded": False}, dict(current)], [dict(current), {**current, "loaded": False}]):
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
        checks = [{"pass": True, "evidence": "Output preserves the rule"}]
        grade = {"A": {"assertions": checks}, "B": {"assertions": checks},
                 "A_preserves_B": True, "B_preserves_A": True}
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

    def test_malformed_or_partial_trace_cannot_be_silently_discarded(self):
        for text in ('{"type":"turn.completed"}\nnot-json\n', '[]\n', '{"type":'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                runner.parse_events(text)
        self.assertEqual(runner.parse_events('\n{"type":"turn.completed"}\n'),
                         [{"type": "turn.completed"}])

    def test_full_payload_fingerprint_covers_references_and_fixtures(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "SKILL.md").write_text("skill")
            (root / "references").mkdir()
            ref = root / "references/rule.md"
            ref.write_text("original")
            before = runner.payload_manifest(root)
            ref.write_text("changed")
            self.assertNotEqual(before["sha256"], runner.payload_manifest(root)["sha256"])
            (root / "evals").mkdir()
            (root / "evals/fixture.txt").write_text("fixture")
            self.assertIn("evals/fixture.txt", runner.payload_manifest(root)["files"])
            before_mode = runner.payload_manifest(root)
            ref.chmod(ref.stat().st_mode ^ 0o100)
            self.assertNotEqual(before_mode["sha256"], runner.payload_manifest(root)["sha256"])

    def test_model_order_and_blind_judge_remain_separate(self):
        current = {"state": "RUNTIME_PASS", "response": "result", "loaded": True,
                   "models": ["fixture"], "metrics": {"agent_elapsed_seconds": 2}}
        checks = [{"pass": True, "evidence": "observed preserved contract"}]
        grade = {"A": {"assertions": checks}, "B": {"assertions": checks},
                 "A_preserves_B": True, "B_preserves_A": True}
        for baseline_first in (True, False):
            with patch.object(runner, "run_case", side_effect=[dict(current), dict(current),
                    {**current, "response": json.dumps(grade)}]) as run:
                result = runner.evaluate("codex", Path("candidate"), {"assertions": ["rule"]},
                                         "quality", Path("baseline"), model="frozen-model",
                                         baseline_first=baseline_first)
            self.assertEqual(result["state"], "QUALITY_PASS")
            self.assertEqual(run.call_args_list[0].args[1], Path("baseline" if baseline_first else "candidate"))
            self.assertTrue(all(c.kwargs["model"] == "frozen-model" for c in run.call_args_list))
            judge = run.call_args_list[-1].kwargs["judge_prompt"]
            self.assertNotIn('"candidate":', judge)
            self.assertNotIn('"baseline":', judge)
            self.assertEqual(result["judge"]["metrics"]["agent_elapsed_seconds"], 2)

    def test_deterministic_write_violation_cannot_be_overridden_by_judge(self):
        current = {"state": "RUNTIME_PASS", "response": "done", "loaded": True,
                   "workspace_changed": True}
        with patch.object(runner, "run_case", return_value=current) as run:
            result = runner.evaluate("claude", Path("candidate"),
                                     {"assertions": ["no writes"], "fixture_policy": "unchanged"},
                                     "quality", Path("baseline"))
        self.assertEqual(result["state"], "FAIL")
        self.assertEqual(run.call_count, 2)

    def test_missing_descendant_accounting_stays_unknown(self):
        events = [{"type": "thread.started", "thread_id": "private-id"},
                  {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}]
        result = runner.trace_metadata(events, "codex", 1.5)
        self.assertIsNone(result["aggregate_tokens"])
        self.assertFalse(result["accounting_complete"])
        self.assertEqual(result["observed_tokens"], 15)
        self.assertNotIn("private-id", json.dumps(result))

    def test_catalog_rejects_target_override_and_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "target").mkdir()
            (root / "target/SKILL.md").write_text("target")
            with self.assertRaises(ValueError):
                runner.catalog_roots(root, "target")
            (root / "linked").symlink_to(root / "target", target_is_directory=True)
            with self.assertRaises(ValueError):
                runner.catalog_roots(root, "other")

    def test_workspace_oracle_detects_hidden_modes_and_empty_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = root / "input.txt"
            fixture.write_text("same bytes")
            fixture.chmod(0o600)
            before = runner.workspace_snapshot(root)
            fixture.chmod(0o700)
            self.assertNotEqual(before, runner.workspace_snapshot(root))
            fixture.chmod(0o600)
            (root / ".git").mkdir()
            (root / ".git/config").write_text("changed")
            self.assertNotEqual(before, runner.workspace_snapshot(root))
            self.assertIn(".git/config", runner.workspace_snapshot(root))
            (root / "empty").mkdir()
            self.assertIn("empty", runner.workspace_snapshot(root))

    def test_workspace_bounds_fail_without_reading_large_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "a").write_bytes(b"a" * 20)
            (root / "b").write_bytes(b"b" * 20)
            for bounds in ({"max_file_bytes": 10}, {"max_total_bytes": 30},
                           {"max_entries": 1}, {"timeout": -1}):
                with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                    runner.workspace_snapshot(root, **bounds)

    def test_tool_evidence_is_redacted_ordered_and_marks_omissions(self):
        events = [{"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "private-native-id", "name": "Read", "input": {
                "file_path": "/trial-root/workspace/input.txt", "secret": "sensitive-fixture"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result",
                "tool_use_id": "private-native-id", "content": "bounded result"}]}}]
        evidence = runner.tool_evidence(events, Path("/trial-root"))
        text = json.dumps(evidence)
        self.assertTrue(evidence["complete"])
        self.assertNotIn("sensitive-fixture", text)
        self.assertNotIn("private-native-id", text)
        self.assertIn("<trial>", text)
        self.assertEqual(evidence["records"][0]["call"], evidence["records"][1]["call"])
        events[-1]["message"]["content"][0]["content"] = "x" * 70000
        self.assertFalse(runner.tool_evidence(events, Path("/trial-root"))["complete"])

    def test_baseline_assertion_omission_cannot_pass(self):
        current = {"state": "RUNTIME_PASS", "response": "output", "loaded": True}
        checks = [{"pass": True, "evidence": "observed rule"}]
        for baseline_checks in ([], checks * 2):
            grade = {"A": {"assertions": checks}, "B": {"assertions": baseline_checks},
                     "A_preserves_B": True, "B_preserves_A": True}
            with patch.object(runner.secrets, "randbelow", return_value=0), patch.object(
                    runner, "run_case", side_effect=[dict(current), dict(current),
                        {**current, "response": json.dumps(grade)}]):
                result = runner.evaluate("codex", Path("candidate"), {"assertions": ["rule"]},
                                         "quality", Path("baseline"))
            self.assertEqual(result["state"], "FAIL")

    def test_credentials_are_redacted_from_every_evidence_string(self):
        marker = "synthetic-credential-value"
        samples = [f"Authorization: Bearer {marker}", f"Proxy-Authorization: Basic {marker}",
                   json.dumps({"password": marker}), json.dumps({"api_key": marker}),
                   f"secret='{marker}'", f"https://user:{marker}@example.test"]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertNotIn(marker, runner.public_text(sample))
                events = [{"type": "item.completed", "item": {"type": "command_execution",
                           "id": "id", "command": "read", "aggregated_output": sample}}]
                self.assertNotIn(marker, json.dumps(runner.tool_evidence(events, Path("/trial"))))

    def test_large_judge_input_is_a_file_not_a_process_argument(self):
        with patch.object(runner.shutil, "which", return_value="/usr/bin/true"), patch.object(
                runner, "native_command", wraps=runner.native_command) as command:
            result = runner.run_case("codex", Path("skill"), {}, judge_prompt="x" * (3 * 1024 * 1024))
        self.assertLess(len(command.call_args.args[1]), 1024)
        self.assertNotEqual(result.get("reason"), "agent process could not start")

    def test_resource_reads_only_a_bounded_regular_file_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            installed = Path(temp).resolve()
            ref = installed / "skill/references/example.md"
            ref.parent.mkdir(parents=True)
            with ref.open("wb") as handle:
                handle.write(b"example content")
                handle.truncate(20 * 1024 * 1024)
            events = [{"type": "item.completed", "item": {"type": "command_execution",
                       "exit_code": 0, "command": "cat " + str(ref),
                       "aggregated_output": "example content"}}]
            with patch.object(Path, "read_text", side_effect=AssertionError("unbounded read")):
                runner.confirmed_resources(events, installed)
            link = ref.parent / "linked.md"
            link.symlink_to(ref)
            events[0]["item"]["command"] = "cat " + str(link)
            self.assertEqual(runner.confirmed_resources(events, installed), [])

    def test_resource_reads_reject_compound_commands_and_redirection(self):
        with tempfile.TemporaryDirectory() as temp:
            installed = Path(temp).resolve()
            ref = installed / "skill/references/example.md"
            ref.parent.mkdir(parents=True)
            ref.write_text("known reference body")
            commands = [
                (f"cat {ref}", True),
                (f"head -n 3 {ref}", True),
                (f"/bin/zsh -lc 'sed -n 1,20p {ref}'", True),
                (f"cat /dev/null; printf '%s' 'known reference body' {ref}", False),
                (f"cat /dev/null && printf '%s' 'known reference body' {ref}", False),
                (f"cat {ref} >/dev/null", False),
                (f"head -n 0 {ref}", False),
            ]
            for command, expected in commands:
                with self.subTest(command=command):
                    events = [{"type": "item.completed", "item": {
                        "type": "command_execution", "exit_code": 0,
                        "command": command, "aggregated_output": ref.read_text()}}]
                    self.assertEqual(runner.confirmed_resources(events, installed),
                                     ["skill/references/example.md"] if expected else [])

    def test_main_executes_cases_from_the_frozen_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            root, baseline = base / "candidate/example", base / "old/example"
            for directory in (root, baseline):
                directory.mkdir(parents=True)
                (directory / "SKILL.md").write_text("original")
            (root / "evals").mkdir()
            definition = {"id": 42, "prompt": "old prompt", "assertions": ["old oracle"], "files": []}
            eval_file = root / "evals/evals.json"
            eval_file.write_text(json.dumps({"evals": [definition]}))
            args = runner.argparse.Namespace(skill_dir=root, agent="codex", suite="quality",
                output_dir=base / "results", model="frozen", baseline_dir=baseline,
                catalog_dir=None, repetitions=1, case_id="42")
            original_manifest = runner.payload_manifest

            def manifest(path):
                if path == root:
                    definition.update(prompt="updated prompt", assertions=["updated oracle"])
                    eval_file.write_text(json.dumps({"evals": [definition]}))
                return original_manifest(path)

            def evaluate(agent, frozen, case, *unused, **options):
                self.assertEqual(case, runner.cases(frozen, "quality")[0])
                self.assertEqual(case["prompt"], "updated prompt")
                return {"state": "QUALITY_PASS"}

            with patch.object(runner, "arguments", return_value=args), patch.object(
                    runner, "payload_manifest", side_effect=manifest), patch.object(
                    runner, "evaluate", side_effect=evaluate), patch.object(runner.subprocess, "run",
                    return_value=runner.argparse.Namespace(stdout="fixture-version")), patch("builtins.print"):
                self.assertEqual(runner.main(), 0)

    def test_main_freezes_catalog_and_inputs_for_alternating_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            root, baseline, catalog = base / "candidate/example", base / "old/example", base / "catalog"
            for directory in (root, baseline, catalog / "adviser"):
                directory.mkdir(parents=True)
                (directory / "SKILL.md").write_text("original")
            (root / "evals").mkdir()
            (root / "evals/evals.json").write_text(json.dumps({"evals": [
                {"id": 42, "prompt": "plan", "assertions": ["preserve behavior"], "files": []}]}))
            args = runner.argparse.Namespace(skill_dir=root, agent="codex", suite="quality",
                output_dir=base / "results", model="frozen", baseline_dir=baseline,
                catalog_dir=catalog, repetitions=3, case_id=None)
            observed = []

            def evaluate(agent, frozen, case, suite, old, **options):
                observed.append(options["baseline_first"])
                self.assertEqual((frozen / "SKILL.md").read_text(), "original")
                self.assertEqual((options["catalog"] / "adviser/SKILL.md").read_text(), "original")
                self.assertEqual(options["model"], "frozen")
                (root / "SKILL.md").write_text("later edit")
                (catalog / "adviser/SKILL.md").write_text("later edit")
                return {"state": "QUALITY_PASS"}

            with patch.object(runner, "arguments", return_value=args), patch.object(
                    runner, "evaluate", side_effect=evaluate), patch.object(runner.subprocess, "run",
                    return_value=runner.argparse.Namespace(stdout="fixture-version")), patch("builtins.print"):
                self.assertEqual(runner.main(), 0)
            self.assertEqual(observed, [True, False, True])
            report = json.loads((base / "results/report.json").read_text())
            self.assertEqual([r["case"] for r in report["results"]], ["42"] * 3)
            self.assertEqual(report["environment"]["catalog_lane"], "multi-skill")
            self.assertNotEqual(report["fingerprints"]["candidate"], runner.payload_manifest(root))

    def test_repetition_bounds(self):
        self.assertEqual(runner.repetition_count("3"), 3)
        for value in ("0", "6"):
            with self.assertRaises(Exception):
                runner.repetition_count(value)

    def test_redaction(self):
        secret = "sk-" + "X" * 32
        self.assertNotIn(secret, runner.public_text("API result: " + secret))


if __name__ == "__main__":
    unittest.main()
