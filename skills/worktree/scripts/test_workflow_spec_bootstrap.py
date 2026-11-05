#!/usr/bin/env python3
"""Real-Git SDLC initial contract admission and interrupted preparation."""

from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock

import test_workflow_contracts as contract_fixtures
import workflow_execution as engine
import workflow_impact as impact
import workflow_objectives as objectives
from workflow_refinement import refinement, settle
from workflow_state import WorkflowError, read, write
from test_workflow_execution import git


class SdlcContractPreparationTests(unittest.TestCase):
    """Exercise first publication from an accepted, committed README-only source."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        self.session = "contract-coordinator"
        environment = mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home),
            "CODEX_THREAD_ID": self.session,
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.repo = self.root / "repo"
        git(self.root, "init", "-q", "-b", "main", str(self.repo))
        git(self.repo, "config", "user.name", "Contract Test")
        git(self.repo, "config", "user.email", "contract@example.invalid")
        (self.repo / "README.md").write_text("fixture\n")
        (self.repo / "service").mkdir()
        (self.repo / "service/README.md").write_text("selected scope\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "baseline")
        self.base = git(self.repo, "rev-parse", "HEAD")
        origin = self.root / "origin.git"
        git(self.root, "init", "--bare", "-q", "-b", "main", str(origin))
        git(self.repo, "remote", "add", "origin", str(origin))
        git(self.repo, "push", "-qu", "origin", "main")
        git(self.repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.project = self.repo
        self.objective = {
            "outcome": "Publish one canonical contract pair",
            "scope": ["canonical requirements and design"], "constraints": [],
            "acceptance_criteria": ["Both documents publish together"],
            "clarifications": [],
            "provenance": {"kind": "direct_request", "source_sha256": "a" * 64},
        }

    def publish_and_plan(self):
        from project_specs_lib.impact import statement_inventory
        self.run_dir = Path(objectives.accept(
            self.project, self.home, "agentic-sdlc", self.session, self.objective
        )["run_dir"])
        for path, body in getattr(self, "bundle_files", {}).items():
            target = self.project / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        objective = objectives.validate_run(self.run_dir)[1]["revisions"][-1]["sha256"]
        contract_fixtures.DirectExecutionTests.publication(self, self.project, objective=objective)
        if hasattr(self, "program"):
            from workflow_program import admit, admit_inputs
            admit(self.run_dir, self.program)
            admit_inputs(self.run_dir)
        else:
            git(self.repo, "add", "-A")
        value = refinement(self.run_dir)
        claim = {key: value[key] for key in ("objective_id", "revision", "intent_sha256")}
        claim.update(schema=impact.CLAIM_SCHEMA, declared_effects=["design", "requirements"],
                     declared_plan_action="replan_required", dispositions=[{
                         "statement": item["key"], "disposition": "changed_contract",
                         "requirements": ["REQ-001"], "design": ["FEAT-001"],
                         "effects": ["design", "requirements"], "reason": None,
                     } for item in statement_inventory(value["extracted"])])
        settle(self.run_dir, claim)
        self.plan = self.run_dir / "plans/FEAT-001.plan.v1.md"
        self.plan.parent.mkdir()
        scope = self.project.relative_to(self.repo)
        self.plan.write_text(
            "# FEAT-001 Plan v1\n\n## Task Graph\n\n### TASK-001\n\n"
            "- Requirements: REQ-001\n- Goal: implement pair publication\n"
            "- Depends on: none\n- Write claims: exact: " + str(scope / "pair.py") + "\n"
            "- Conflict domains: code:pair\n- Validation: verify the pair\n"
            "- Done criteria: both documents publish together\n"
            "- Rollback or stop conditions: stop on changed scope\n")
        self.plan.with_suffix(".md.lock").write_text("locked\n")
        write(self.run_dir / "host-capacity.json", {
            "schema": "workflow/host-capacity-v1", "native_slots": 1,
            "evidence": "deterministic test capacity; not native proof"})

    def prepare(self):
        return engine.prepare_execution(self.run_dir, self.project, "FEAT-001", self.plan, 1)

    def test_initial_pair_is_committed_once_before_execution(self):
        self.publish_and_plan()
        result = self.prepare()
        contract = git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(contract, self.base)
        self.assertEqual(git(self.repo, "rev-list", "--parents", "-n", "1", contract), contract + " " + self.base)
        self.assertEqual(git(self.repo, "diff", "--name-only", self.base, contract).splitlines(),
                         ["docs/design.md", "docs/requirements.md"])
        self.assertEqual(result["base_head"], contract)
        self.assertEqual(read(self.run_dir / "run.json")["source_head"], self.base)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual(self.prepare()["integration_worktree"], result["integration_worktree"])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), contract)

    def test_nested_project_contract_stays_scoped(self):
        self.project = self.repo / "service"
        self.publish_and_plan()
        result = self.prepare()
        self.assertEqual(result["project_scope"], "service")
        self.assertEqual(git(self.repo, "diff", "--name-only", self.base, "HEAD").splitlines(),
                         ["service/docs/design.md", "service/docs/requirements.md"])

    def test_unrelated_dirt_blocks_contract_commit(self):
        self.publish_and_plan()
        (self.repo / "unrelated.txt").write_text("preserve me\n")
        with self.assertRaises(engine.ExecutionError):
            self.prepare()
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.base)
        self.assertFalse(engine.coordinator_path(self.run_dir, "FEAT-001").exists())

    def test_contract_recovers_commit_before_state_write(self):
        self.publish_and_plan()
        original = engine.git
        def crash(repo, arguments, label, **kwargs):
            result = original(repo, arguments, label, **kwargs)
            if arguments[0] == "commit":
                raise RuntimeError("injected crash after real contract commit")
            return result
        with mock.patch.object(engine, "git", side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, "injected crash"):
                self.prepare()
        contract = git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(contract, self.base)
        self.assertEqual(self.prepare()["base_head"], contract)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), contract)


    def managed_project(self):
        import worktree_manager as manager
        git(self.repo, "switch", "-qc", "feature/contract-source")
        outer = manager.add_worktree(cwd=self.repo / "service", project=None, task_slug="contract-test")
        self.repo = Path(outer["worktree"])
        self.project = Path(outer["scope_cwd"])

    def test_managed_intake_owns_source_before_authoring(self):
        import workflow_interop
        self.managed_project()
        self.publish_and_plan()
        lease = workflow_interop.load(self.run_dir)
        self.assertIsNotNone(lease)
        self.assertEqual(lease["initial_head"], self.base)
        self.assertFalse(lease["released"])
        result = self.prepare()
        self.assertEqual(workflow_interop.load(self.run_dir)["promoted_head"], result["base_head"])
        self.assertEqual(result["base_head"], git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(self.prepare()["base_head"], result["base_head"])
        with mock.patch.object(engine, "worktrees", return_value={}):
            with self.assertRaisesRegex(engine.ExecutionError, "do not recreate"):
                self.prepare()

    def test_managed_commit_recovery_records_head_before_reconciliation(self):
        import workflow_interop
        self.managed_project()
        self.publish_and_plan()
        with mock.patch.object(workflow_interop, "record_promotion", side_effect=RuntimeError("promotion interrupted")):
            with self.assertRaisesRegex(RuntimeError, "promotion interrupted"):
                self.prepare()
        contract = git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(contract, self.base)
        self.assertIsNone(workflow_interop.load(self.run_dir)["promoted_head"])
        self.assertEqual(self.prepare()["base_head"], contract)
        self.assertEqual(workflow_interop.load(self.run_dir)["promoted_head"], contract)

    def test_missing_publication_receipt_is_not_admitted(self):
        self.publish_and_plan()
        for receipt in (self.home / "project-specs").glob("*/transactions/*/test-publication.json"):
            receipt.unlink()
        with self.assertRaisesRegex(engine.ExecutionError, "paired-publication receipt"):
            self.prepare()
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.base)

    def test_contract_rejects_deleted_symlinked_and_ignored_files(self):
        self.publish_and_plan()
        path = self.project / "docs/design.md"
        original = path.read_bytes()
        path.unlink()
        with self.assertRaises(engine.ExecutionError):
            self.prepare()
        external = self.root / "external.md"
        external.write_bytes(original)
        path.symlink_to(external)
        with self.assertRaises(engine.ExecutionError):
            self.prepare()
        path.unlink()
        path.write_bytes(original)
        (self.repo / ".git/info/exclude").write_text("docs/design.md\n")
        with self.assertRaisesRegex(engine.ExecutionError, "must not be ignored"):
            self.prepare()
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.base)

    def test_contract_intent_rejects_changed_plan_and_unjournaled_commit(self):
        self.publish_and_plan()
        original = engine.git
        def interrupt(repo, args, label, **kwargs):
            if args[0] == "commit":
                raise RuntimeError("before commit")
            return original(repo, args, label, **kwargs)
        with mock.patch.object(engine, "git", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "before commit"):
                self.prepare()
        plan = self.plan.read_text()
        self.plan.write_text(plan.replace("verify the pair", "verify both documents"))
        with self.assertRaisesRegex(engine.ExecutionError, "intent changed"):
            self.prepare()
        self.plan.write_text(plan)
        git(self.repo, "commit", "-qm", "unrelated commit message")
        with self.assertRaisesRegex(engine.ExecutionError, "journaled clean direct child"):
            self.prepare()

    def test_dirty_managed_intake_cannot_acquire_lease(self):
        self.managed_project()
        (self.project / "unrelated.txt").write_text("unleased dirt\n")
        with self.assertRaises(WorkflowError):
            objectives.accept(self.project, self.home, "agentic-sdlc", self.session, self.objective)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.base)


    def test_publication_race_cannot_commit_unpublished_bytes(self):
        import workflow_spec_bootstrap as bootstrap
        self.publish_and_plan()
        original = bootstrap._publication_receipt
        def race(*args, **kwargs):
            receipt = original(*args, **kwargs)
            path = self.project / "docs/requirements.md"
            path.write_bytes(path.read_bytes() + b"\nUnpublished change\n")
            return receipt
        with mock.patch.object(bootstrap, "_publication_receipt", side_effect=race):
            with self.assertRaisesRegex(engine.ExecutionError, "bytes changed"):
                self.prepare()
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.base)

    def test_document_program_contract_includes_exact_admitted_bundle(self):
        import hashlib
        import json
        from workflow_state import digest
        body = b'[{"id":"AC-001"}]\n'
        manifest = {
            "schema": "maintain-project-specs/source-bundle-v1", "conflicts": [],
            "sources": [{"path": "docs/contracts/acceptance.json", "sha256": hashlib.sha256(body).hexdigest(),
                         "authority": "normative", "role": "acceptance", "ids": ["AC-001"],
                         "references": [], "id_field": "id"}],
            "coverage": [{"source_id": "AC-001", "kind": "acceptance", "disposition": "required",
                          "requirements": ["REQ-001"], "features": ["FEAT-001"], "reason": "required pair"}],
        }
        raw = json.dumps(manifest).encode()
        self.program = {"schema": "workflow/program-v1", "phases": [
            {"id": "P1", "depends_on": [], "exit_criteria": ["AC-001"]}], "slices": [
                {"id": "SLICE-001", "phase": "P1", "depends_on": [], "feature_ids": ["FEAT-001"],
                 "requirement_ids": ["REQ-001"], "criteria": ["AC-001"], "regression_criteria": [],
                 "change_kind": "non-runtime"}]}
        self.objective["inputs"] = {"manifest": "docs/contracts/manifest.json",
                                    "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                                    "program_sha256": digest(self.program)}
        self.bundle_files = {"docs/contracts/manifest.json": raw, "docs/contracts/acceptance.json": body}
        self.publish_and_plan()
        self.plan.write_text(self.plan.read_text().replace("# FEAT-001", "# SLICE-001")
                             .replace("- Requirements: REQ-001", "- Requirements: REQ-001\n- Features: FEAT-001"))
        result = engine.prepare_execution(self.run_dir, self.project, "SLICE-001", self.plan, 1)
        self.assertEqual(result["base_head"], git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(set(git(self.repo, "diff", "--name-only", self.base, "HEAD").splitlines()),
                         set(self.bundle_files) | {"docs/design.md", "docs/requirements.md"})


    def test_clean_unjournaled_contract_commit_is_rejected(self):
        self.publish_and_plan()
        git(self.repo, "commit", "-qm", "external contract commit")
        with self.assertRaisesRegex(engine.ExecutionError, "workflow checkpoint"):
            self.prepare()
        self.assertFalse(engine.coordinator_path(self.run_dir, "FEAT-001").exists())


if __name__ == "__main__":
    unittest.main()
