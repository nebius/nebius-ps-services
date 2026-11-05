"""Issue closure is a per-member decision, bound to immutable local delivery."""

from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
import unittest

import test_workflow_objectives as objective_fixtures
import workflow_issue_resolution as issues
import workflow_objectives as objectives
from workflow_state import WorkflowError, read, write


class IssueResolutionTests(unittest.TestCase):
    setUp = objective_fixtures.ObjectiveTests.setUp
    tearDown = objective_fixtures.ObjectiveTests.tearDown
    git = objective_fixtures.ObjectiveTests.git
    accept = objective_fixtures.ObjectiveTests.accept

    def context(self, close=True):
        return {"plan_path": "docs/github-issues/batch-1-network.md", "plan_sha256": "a" * 64,
                "close_after_merge": close, "closure_request_sha256": "b" * 64 if close else None,
                "members": [{"host": "github.com", "repository": repo, "number": 1,
                             "criteria": ["Connection succeeds", "Restart persists"]}
                            for repo in ("example/one", "example/two")]}

    def evidence(self, context, statuses=("passed", "passed")):
        return {"schema": issues.EVIDENCE_SCHEMA, "issues": [
            {**issues.identity(member), "summary": "Connection repair", "criteria": [
                {"criterion": criterion, "status": status, "evidence": "Focused check observed this outcome"}
                for criterion, status in zip(member["criteria"], statuses)]}
            for member in context["members"]]}

    def delivered(self, close=True, statuses=("passed", "passed")):
        self.value["issue_resolution"] = self.context(close)
        run = Path(self.accept()["run_dir"])
        binding = read(run / "objective.json")
        head = self.git("rev-parse", "HEAD")
        record = {"status": "done", "promoted_head": head, "integration_head": head,
                  "issue_evidence": self.evidence(self.context(close), statuses),
                  "issue_evidence_head": head, "issue_objective_sha256": binding["revisions"][-1]["sha256"]}
        write(run / "execution/FEAT-001/coordinator.json", record)
        objectives.complete_run(run, "session-a")
        return run, issues.read_handoff(run)

    def observations(self, receipt):
        return {"head": receipt["delivered_head"], "issues": [
            {**issues.identity(row), "summary": row["summary"], "criteria": row["criteria"],
             "state": "open", "scope_unchanged": True, "comment_verified": True, "auto_close": "enabled"}
            for row in receipt["issues"]]}

    def block(self, receipt, observations=None, **changes):
        args = {"head": receipt["delivered_head"], "host": "github.com", "base": "main",
                "default_branch": "main", "observations": observations or self.observations(receipt)}
        return issues.closing_block(receipt, **{**args, **changes})

    def test_complete_writes_exact_receipt_and_replay_is_byte_identical(self):
        run, receipt = self.delivered()
        before = (run / "issue-resolution.json").read_bytes()
        objectives.complete_run(run, "session-a")
        self.assertEqual(before, (run / "issue-resolution.json").read_bytes())
        self.assertEqual(receipt["merge_status"], "pending")
        self.assertEqual((run / "issue-resolution.json").stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(row["eligible"] for row in receipt["issues"]))

    def test_absent_metadata_keeps_ordinary_completion_unchanged(self):
        run = Path(self.accept()["run_dir"])
        write(run / "execution/FEAT-001/coordinator.json", {"status": "done"})
        objectives.complete_run(run, "session-a")
        self.assertFalse((run / "issue-resolution.json").exists())

    def test_wrong_coordinator_cannot_publish_receipt(self):
        run = Path(self.accept()["run_dir"])
        with self.assertRaises(WorkflowError):
            objectives.complete_run(run, "worker")
        self.assertFalse((run / "issue-resolution.json").exists())

    def test_issue_metadata_is_not_sdlc_completion_authority(self):
        self.value["issue_resolution"] = self.context()
        with self.assertRaisesRegex(WorkflowError, "owned by Implement"):
            objectives.accept(self.project, self.home, "agentic-sdlc", "session-a", self.value)

    def test_plan_text_cannot_supply_closure_provenance(self):
        value = self.context()
        value["closure_request_sha256"] = value["plan_sha256"]
        with self.assertRaises(WorkflowError):
            issues.metadata(value)

    def test_full_identity_distinguishes_same_number_in_two_repositories(self):
        _, receipt = self.delivered()
        self.assertIn("Closes example/one#1", self.block(receipt))
        self.assertIn("Closes example/two#1", self.block(receipt))

    def test_duplicate_members_and_missing_criteria_fail(self):
        value = self.context()
        value["members"][1] = deepcopy(value["members"][0])
        with self.assertRaises(WorkflowError):
            issues.metadata(value)
        evidence = self.evidence(self.context())
        evidence["issues"][0]["criteria"].pop()
        with self.assertRaises(WorkflowError):
            issues.evidence(self.context(), evidence)

    def test_unselected_member_cannot_be_substituted(self):
        evidence = self.evidence(self.context())
        evidence["issues"][0]["number"] = 999
        with self.assertRaises(WorkflowError):
            issues.evidence(self.context(), evidence)

    def test_no_issue_evidence_without_accepted_metadata(self):
        with self.assertRaises(WorkflowError):
            issues.evidence(None, self.evidence(self.context()))

    def test_no_closure_intent_emits_no_keywords(self):
        _, receipt = self.delivered(close=False)
        self.assertEqual(self.block(receipt), "")

    def test_partial_resolution_stays_open(self):
        _, receipt = self.delivered(statuses=("passed", "not_run"))
        self.assertEqual(receipt["issues"][0]["resolution"], "partial")
        self.assertEqual(self.block(receipt), "")

    def test_mixed_batch_closes_only_fully_verified_member(self):
        _, receipt = self.delivered()
        observed = self.observations(receipt)
        observed["issues"][1]["criteria"][1]["status"] = "failed"
        block = self.block(receipt, observed)
        self.assertIn("Closes example/one#1", block)
        self.assertNotIn("example/two#1", block)

    def test_closed_changed_uncommented_or_disabled_members_stay_out(self):
        _, receipt = self.delivered()
        for field, value in (("state", "closed"), ("scope_unchanged", False),
                             ("comment_verified", False), ("auto_close", "disabled")):
            with self.subTest(field=field):
                observed = self.observations(receipt)
                for row in observed["issues"]:
                    row[field] = value
                self.assertEqual(self.block(receipt, observed), "")

    def test_nondefault_or_wrong_host_cannot_arm_closure(self):
        _, receipt = self.delivered()
        self.assertEqual(self.block(receipt, base="release"), "")
        self.assertEqual(self.block(receipt, host="other.example.com"), "")

    def test_stale_head_evidence_rejected(self):
        _, receipt = self.delivered()
        with self.assertRaises(WorkflowError):
            self.block(receipt, head="c" * 40)

    def test_tampered_receipt_and_sealed_evidence_rejected(self):
        run, receipt = self.delivered()
        receipt["issues"][0]["number"] = 999
        write(run / "issue-resolution.json", receipt)
        with self.assertRaises(WorkflowError):
            issues.read_handoff(run)
        with self.assertRaises(WorkflowError):
            objectives.complete_run(run, "session-a")

    def test_changed_completion_head_cannot_reuse_old_results(self):
        run, _ = self.delivered()
        path = run / "execution/FEAT-001/coordinator.json"
        record = read(path)
        record["promoted_head"] = record["integration_head"] = "c" * 40
        write(path, record)
        with self.assertRaisesRegex(WorkflowError, "stale"):
            issues.read_handoff(run)

    def test_pr_section_replay_and_removal_preserve_human_bytes(self):
        body = "Human title\n\nRefs example/one#7\n"
        block = f"{issues.START}\nCloses example/one#1\n{issues.END}"
        written = issues.replace_block(body, block)
        self.assertEqual(issues.replace_block(written, block), written)
        self.assertEqual(issues.replace_block(written, ""), body + "\n\n")
        with self.assertRaises(WorkflowError):
            issues.replace_block(written + written, block)

    def test_untracked_plan_is_normalized_request_not_copied_source(self):
        path = self.project / self.context()["plan_path"]
        path.parent.mkdir(parents=True)
        path.write_text("User planning input")
        self.value["issue_resolution"] = self.context()
        run = Path(self.accept()["run_dir"])
        self.assertTrue(path.exists())
        self.assertEqual(read(run / "objectives/r0001.json")["issue_resolution"], self.context())
        self.assertFalse((run / path.name).exists())

    def test_private_publication_cli_checks_git_and_reports_unknown_capability(self):
        run, receipt = self.delivered()
        observed = self.observations(receipt)
        observed["issues"][0]["auto_close"] = "unknown"
        obs = self.root / "observations.json"
        write(obs, observed)
        body = self.root / "body.md"
        body.write_text("Human body")
        args = [sys.executable, "-B", str(Path(issues.__file__)), "--run-dir", str(run),
                "--checkout", str(self.project), "--head", receipt["delivered_head"],
                "--host", "github.com", "--base", "main", "--default-branch", "main",
                "--observations", str(obs), "--body-file", str(body)]
        result = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["closure"], "configured-unverified")
        self.assertEqual(body.read_text(), "Human body")
        dirty = self.project / "uncommitted.txt"
        dirty.write_text("unverified change")
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 2)
        dirty.unlink()
        args[args.index("--head") + 1] = "c" * 40
        self.assertEqual(subprocess.run(args, capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
