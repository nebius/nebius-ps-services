"""Protected delivery state transitions; mocked GitHub transport, no remote effects."""

import base64
import copy
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import workflow_delivery as d
from workflow_state import WorkflowError, digest, read


class Blocked(RuntimeError):
    pass


class Pending(RuntimeError):
    pass


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.run = self.root / "home/sdlc-runs/project/run-test"
        self.project = self.root / "project"
        self.project.mkdir()
        self.head, self.base = "a" * 40, "b" * 40
        self.policy = {
            "schema": "merge-policy/v1",
            "sdlc_contract": d.CONTRACT,
            "merge_workflow": "merge.yml",
            "completion_workflow": "completion.yml",
            "ci_workflows": [{"file": "ci.yml", "paths": ["**"]}],
        }
        self.completion = {
            "boundary": "merge-verified",
            "repository": "owner/repo",
            "base": "main",
            "method": "squash",
            "delivery_scope": ["SLICE-001", "SLICE-002"],
            "merge_effects": ["merge.yml", "completion.yml", "ci.yml"],
        }
        self.expected = {
            "project": self.project,
            "head": self.head,
            "objective_sha256": "c" * 64,
            "basis_sha256": "d" * 64,
            "completion": self.completion,
        }
        self.pr = {"head": {"sha": self.head}, "base": {"sha": self.base}}
        self.receipt = {
            "execution_id": "SLICE-001",
            "candidate": {
                "head": self.head,
                "artifact_sha256": "e" * 64,
                "configuration_sha256": "f" * 64,
            },
            "criteria": {"AC-001": {}},
            "plan": {"live": True},
        }
        self.review = {
            "repository": "owner/repo",
            "pr": 1,
            "head": self.head,
            "base": "main",
            "base_sha": self.base,
        }
        self.api = SimpleNamespace(
            repo="owner/repo",
            repo_info=lambda: {"default_branch": "main"},
            api=self.api_call,
            pages=self.pages,
        )
        self.posts = []
        self.ambiguous, self.ci_failed, self.queue = False, False, False
        self.source = {
            "base_repository_id": 1,
            "head_repository_id": 1,
            "head_repository": "owner/repo",
            "head_ref": "candidate",
        }
        self.gate = SimpleNamespace(
            GitHub=lambda repo: self.api,
            attest=self.post_review,
            snapshot=lambda *a, **k: (self.pr, [], copy.deepcopy(self.posted)),
            source_identity=lambda pr: self.source,
            gh=lambda args: {"id": 7},
            Blocked=Blocked,
            Pending=Pending,
        )
        self.finish = SimpleNamespace(
            read_intent=self.intent,
            merged_result=lambda *args: "9" * 40,
            verify_or_dispatch=self.verify_ci,
        )
        for patch in (
            mock.patch.object(d, "_authorize"),
            mock.patch.object(d, "target", return_value=self.expected),
            mock.patch.object(
                d,
                "verify_receipt",
                side_effect=lambda *a, **k: copy.deepcopy(self.receipt),
            ),
            mock.patch.object(d, "_scope"),
            mock.patch.object(d, "broker", return_value=(self.gate, self.finish)),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def api_call(self, path, method="GET", data=None):
        if method == "POST":
            self.posts.append((path, data))
            if self.ambiguous:
                raise Blocked("transport uncertain")
            return {"workflow_run_id": 10}
        if path.startswith("contents/"):
            return {
                "content": base64.b64encode(json.dumps(self.policy).encode()).decode()
            }
        if path == "actions/variables/MERGE_OPERATOR_IDS":
            return {"value": "[7]"}
        if "/reviews/" in path:
            return {"body": json.dumps(self.posted)}
        if path.startswith("pulls/"):
            return self.pr
        raise AssertionError(path)

    def pages(self, path, key=None):
        if path.startswith("rules/"):
            return [{"type": "merge_queue"}] if self.queue else []
        if path.startswith("actions/workflows/"):
            submission = read(self.run / "delivery/SLICE-001/submission.json")
            return [
                {
                    "id": 10,
                    "run_attempt": 1,
                    "actor": {"id": 7},
                    "head_branch": "main",
                    "created_at": submission["attempted_at"][:19] + "Z",
                }
            ]
        raise AssertionError(path)

    def post_review(self, api, number, review):
        self.posted = review
        return {"id": 22}

    def intent(self, api, run_id, attempt, pr, head, policy, **kwargs):
        self.assertEqual(kwargs["allowed_ids"], [7])
        value = {
            k: self.submission[k]
            for k in (
                "repository",
                "pr",
                "head",
                "base",
                "base_sha",
                "review_id",
                "method",
            )
        }
        return {
            **value,
            **self.source,
            "sdlc": self.posted["sdlc"],
            "run_id": run_id,
            "run_attempt": attempt,
        }

    def verify_ci(self, *args, **kwargs):
        if self.ci_failed:
            raise Blocked("result CI failed")
        return {
            "outcome": "ci-verified",
            "result_sha": "9" * 40,
            "ci": [{"conclusion": "success"}],
        }

    def attest(self, execution="SLICE-001"):
        self.receipt["execution_id"] = execution
        return d.attest(
            self.run,
            execution,
            self.review,
            {"receipt": "qualification/reference.json", "sha256": digest(self.receipt)},
        )

    def test_two_slices_and_all_supported_merge_methods(self):
        for method in ("merge", "squash", "rebase"):
            self.run = self.run.parent / ("run-" + method)
            self.completion["method"] = method
            for execution in ("SLICE-001", "SLICE-002"):
                self.attest(execution)
                self.submission = d.submit(self.run, execution)
                result = d.reconcile(self.run, execution)
                self.assertEqual(result["status"], "result-verified")
                d.verify_completion(self.run, result)
                with self.assertRaises(WorkflowError):
                    d.submit(self.run, execution)
        self.assertEqual(len(self.posts), 6)

    def test_ambiguous_submission_reconciles_without_second_effect(self):
        self.attest()
        self.ambiguous = True
        with self.assertRaises(Blocked):
            d.submit(self.run, "SLICE-001")
        self.submission = read(self.run / "delivery/SLICE-001/submission.json")
        with self.assertRaises(WorkflowError):
            d.submit(self.run, "SLICE-001")
        self.assertEqual(
            d.reconcile(self.run, "SLICE-001")["status"], "result-verified"
        )
        self.assertEqual(len(self.posts), 1)

    def test_failed_result_verification_does_not_repeat_merge(self):
        self.attest()
        self.submission = d.submit(self.run, "SLICE-001")
        self.ci_failed = True
        with self.assertRaisesRegex(WorkflowError, "merged, verification"):
            d.reconcile(self.run, "SLICE-001")
        self.assertEqual(
            read(self.run / "delivery/SLICE-001/submission.json")["status"],
            "merged-verification-failed",
        )
        self.ci_failed = False
        self.assertEqual(
            d.reconcile(self.run, "SLICE-001")["status"], "result-verified"
        )
        self.assertEqual(len(self.posts), 1)

    def test_queue_and_base_drift_block_before_dispatch(self):
        self.attest()
        self.queue = True
        with self.assertRaises(WorkflowError):
            d.submit(self.run, "SLICE-001")
        self.queue = False
        self.pr["base"]["sha"] = "0" * 40
        with self.assertRaises(WorkflowError):
            d.submit(self.run, "SLICE-001")
        self.assertEqual(self.posts, [])

    def test_default_review_boundary_needs_no_merge_installation(self):
        self.expected["completion"] = {"boundary": "reviewed-pr"}
        self.queue = True
        with mock.patch.object(
            d, "_remote", side_effect=AssertionError("must not read merge policy")
        ):
            self.assertEqual(self.attest()["status"], "reviewed-pr")

    def test_revoked_authority_blocks_final_effect(self):
        self.attest()
        with mock.patch.object(
            d,
            "_authorize",
            side_effect=[None, None, WorkflowError("FENCED", "revoked")],
        ):
            with self.assertRaises(WorkflowError):
                d.submit(self.run, "SLICE-001")
        self.assertEqual(self.posts, [])


class SourceBranchTests(unittest.TestCase):
    def test_initial_history_and_next_source_branch_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project, origin = root / "project", root / "origin.git"
            project.mkdir()

            def git(cwd, *args):
                return subprocess.check_output(
                    ["git", "-C", str(cwd), *args], stderr=subprocess.DEVNULL, text=True
                ).strip()

            git(root, "init", "--bare", str(origin))
            git(project, "init", "-b", "main")
            git(project, "config", "user.email", "test@example.com")
            git(project, "config", "user.name", "Fixture")
            (project / "README.md").write_text("Initial\n")
            git(project, "add", "-A")
            git(project, "commit", "-m", "initial")
            git(project, "remote", "add", "origin", str(origin))
            git(project, "push", "origin", "main")
            git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
            git(project, "switch", "-c", "selected")
            (project / "README.md").write_text("Accepted first candidate\n")
            git(project, "commit", "-am", "accepted candidate")
            accepted = git(project, "rev-parse", "HEAD")
            run = root / "home/sdlc-runs/project/run-test"
            program = {
                "definition": {"slices": [{"id": "SLICE-001"}, {"id": "SLICE-002"}]}
            }
            from workflow_state import write

            with (
                mock.patch("workflow_program.verify_inputs", return_value=program),
                mock.patch.object(d, "verify_completion"),
                mock.patch.object(
                    d,
                    "accepted",
                    return_value=(
                        {"source_branch": "selected", "source_head": accepted},
                        {},
                        {
                            "completion": {
                                "boundary": "merge-verified",
                                "base": "main",
                                "repository": "owner/repo",
                            }
                        },
                    ),
                ),
                mock.patch.object(d, "_scope"),
            ):
                d.prepare_source(run, project, "SLICE-001")
                self.assertEqual(git(project, "branch", "--show-current"), "selected")
                self.assertEqual(git(project, "rev-parse", "HEAD"), accepted)
                git(project, "switch", "main")
                git(project, "merge", "--ff-only", "selected")
                git(project, "push", "origin", "main")
                git(project, "switch", "selected")
                write(
                    run / "delivery/SLICE-001/completion.json",
                    {"intent": {"base": "main"}, "result_sha": accepted},
                )
                write(
                    run / "execution/SLICE-001/coordinator.json",
                    {"base_branch": "selected", "promoted_head": accepted},
                )
                git(project, "switch", "main")
                with self.assertRaisesRegex(WorkflowError, "SOURCE_DRIFT"):
                    d.prepare_source(run, project, "SLICE-002")
                git(project, "switch", "selected")
                original_git = d._git

                def interrupted(cwd, *args):
                    result = original_git(cwd, *args)
                    if args[:2] == ("switch", "-c"):
                        raise WorkflowError("INTERRUPTED", "after branch creation")
                    return result

                with (
                    mock.patch.object(d, "_git", side_effect=interrupted),
                    self.assertRaisesRegex(WorkflowError, "after branch"),
                ):
                    d.prepare_source(run, project, "SLICE-002")
                self.assertEqual(
                    git(project, "branch", "--show-current"),
                    "codex/sdlc-run-test-slice-002",
                )
                self.assertEqual(git(project, "rev-parse", "HEAD"), accepted)
                git(project, "switch", "main")
                (project / "later.md").write_text("Concurrent default advancement\n")
                git(project, "add", "-A")
                git(project, "commit", "-m", "later default")
                git(project, "push", "origin", "main")
                git(project, "switch", "codex/sdlc-run-test-slice-002")
                d.prepare_source(run, project, "SLICE-002")
                self.assertEqual(git(project, "rev-parse", "selected"), accepted)

    def test_scope_observes_actual_unmanaged_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary).resolve()
            for args in (
                ("init", "-b", "candidate"),
                ("remote", "add", "origin", "https://github.com/owner/repo.git"),
            ):
                subprocess.run(
                    ["git", "-C", str(project), *args], check=True, capture_output=True
                )
            with mock.patch.dict(
                "os.environ", {"CODEX_HOME": str(project / "private-home")}
            ):
                d._scope(
                    {"project": project, "completion": {"boundary": "reviewed-pr"}},
                    "SLICE-001",
                    "owner/repo",
                )


class PhaseEvidenceTests(unittest.TestCase):
    def test_transitive_phase_barrier_and_changed_source_invalidate_exit(self):
        from workflow_state import write

        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary).resolve()
            phases = [
                {"id": "P0", "depends_on": [], "exit_criteria": ["AC0"]},
                {"id": "P1", "depends_on": ["P0"], "exit_criteria": ["AC1"]},
                {"id": "P2", "depends_on": ["P1"], "exit_criteria": ["AC2"]},
            ]
            program = {"phases": phases, "slices": []}
            program_digest = digest(program)
            write(run / "program" / (program_digest + ".json"), program)
            records = [
                {
                    "schema": "workflow/phase-exit-v1",
                    "phase": p["id"],
                    "definition_sha256": digest(p),
                    "slices": [],
                    "qualifications": [{"phase": p["id"]}],
                }
                for p in phases
            ]
            for record in records:
                write(run / "program/phase-exits" / (record["phase"] + ".json"), record)
            basis = {"AC0": "fresh", "AC1": "fresh", "AC2": "fresh"}

            def receipt(_run, reference, **_kwargs):
                criterion = "AC" + reference["phase"][1:]
                return {
                    "execution_id": "PHASE:" + reference["phase"],
                    "criteria": {criterion: {}},
                    "obligation_basis": {criterion: "fresh"},
                }

            with (
                mock.patch.object(
                    d,
                    "accepted",
                    return_value=(
                        {"project_root": str(run)},
                        {},
                        {"inputs": {"program_sha256": program_digest}},
                    ),
                ),
                mock.patch(
                    "workflow_program.verify_inputs",
                    return_value={"bundle": {"obligation_basis": basis}},
                ),
                mock.patch.object(d, "verify_receipt", side_effect=receipt),
            ):
                d.verify_phase_exit(run, records[2], phases[2])
                basis["AC0"] = "drifted"
                with self.assertRaises(WorkflowError):
                    d.verify_phase_exit(run, records[2], phases[2])


if __name__ == "__main__":
    unittest.main()
