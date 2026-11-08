#!/usr/bin/env python3
"""Real-Git direct workflow contracts; native observations are explicit test stubs."""

from pathlib import Path
import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import workflow_execution as engine
import workflow_impact as impact
import workflow_objectives as objectives
import workflow_interop as interop
import workflow_issue_resolution as issue_resolution
import workflow_intake as intake
import workflow_reconcile as reconcile
import workflow_resources as resources
from workflow_refinement import refinement, settle
from workflow_state import WorkflowError, read, write
from workflow_worker_results import ingest_result
from test_workflow_execution import git, capacity_fixture


from test_workflow_execution import write_private  # noqa: E402
from test_workflow_fixture import prepare_execution as fixture_prepare_execution, complete_wave as fixture_complete_wave, replan_future as fixture_replan_future, seed_validation  # noqa: E402

class TransitionErrorTests(unittest.TestCase):
    def test_resource_errors_are_structured_at_cli_boundary(self):
        import workflow_transition as transition
        output = io.StringIO()
        with mock.patch.object(transition, "parser") as parser, mock.patch.object(
            transition, "execute",
            side_effect=WorkflowError("RESOURCE_UNASSIGNED", "resource is not assigned"),
        ), contextlib.redirect_stdout(output):
            parser.return_value.parse_args.return_value = object()
            self.assertEqual(transition.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {
            "status": "error", "code": "RESOURCE_UNASSIGNED",
            "message": "resource is not assigned",
        })

    def test_unavailable_result_input_has_a_controlled_cli_error(self):
        import workflow_transition as transition
        output = io.StringIO()
        with mock.patch.object(transition, "parser"), mock.patch.object(
            transition, "execute", side_effect=FileNotFoundError("private input path"),
        ), contextlib.redirect_stdout(output):
            self.assertEqual(transition.main(), 1)
        value = json.loads(output.getvalue())
        self.assertEqual(value["code"], "ENVIRONMENT_BLOCKER")
        self.assertNotIn("private input path", output.getvalue())


from test_worker_result_cases import WorkerResultContractCases  # noqa: E402


class DirectExecutionTests(WorkerResultContractCases, unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.home = self.root / "home"
        isolated_home = mock.patch.dict(os.environ, {
            "CODEX_HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home),
        })
        isolated_home.start()
        self.addCleanup(isolated_home.stop)
        self.project = self.root / "project"
        self.project.mkdir()
        git(self.project, "init", "-q", "-b", "feature/source")
        git(self.project, "config", "user.name", "Workflow Test")
        git(self.project, "config", "user.email", "test@example.invalid")
        (self.project / "README.md").write_text("fixture\n")
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "initial")
        self.base = git(self.project, "rev-parse", "HEAD")
        git(self.project, "update-ref", "refs/remotes/origin/main", self.base)
        git(
            self.project,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
        )
        self.session = "root-native-test-session"
        self.workflow_owner = getattr(self, 'workflow_owner', 'implement')
        if self.workflow_owner == 'agentic-sdlc':
            origin = self.root / 'origin.git'
            git(self.root, 'clone', '--bare', str(self.project), str(origin))
            git(origin, 'update-ref', 'refs/heads/main', self.base)
            git(origin, 'symbolic-ref', 'HEAD', 'refs/heads/main')
            git(self.project, 'remote', 'add', 'origin', str(origin))
        self.objective = {
            "outcome": "Apply a bounded housekeeping change",
            "scope": ["a.txt, b.txt and c.txt"],
            "constraints": [],
            "acceptance_criteria": ["files contain their task identifiers"],
            "clarifications": [],
            "provenance": {"kind": "current_plan", "source_sha256": "a" * 64},
        }
        self.run_dir = Path(
            objectives.accept(
                self.project, self.home, self.workflow_owner, self.session, self.objective
            )["run_dir"]
        )
        self.settle()
        self.plan = self.run_dir / "plans/FEAT-001.plan.v1.md"
        self.plan.parent.mkdir()
        self.plan.write_text(
            "# FEAT-001 Plan v1\n\n## Task Graph\n"
            + "".join(
                f"\n### TASK-{index:03d}\n\n- Requirements: none\n- Goal: write {name}.txt\n"
                f"- Depends on: none\n- Write claims: exact: {name}.txt\n- Conflict domains: runtime:test-{name}\n"
                "- Validation: verify task identifier\n- Done criteria: expected identifier is present\n"
                "- Rollback or stop conditions: stop on changed scope\n"
                for index, name in enumerate("abc", 1)
            )
        )
        self.plan.with_suffix(".md.lock").write_text("locked\n")
        seed_validation(self.plan)
        write_private(self.run_dir / "host-capacity.json", capacity_fixture(self.run_dir, 2))

    def tearDown(self):
        self.temporary.cleanup()

    def settle(self):
        value = refinement(self.run_dir)
        from project_specs_lib.impact import statement_inventory

        claim = {
            key: value[key] for key in ("objective_id", "revision", "intent_sha256")
        }
        claim.update(
            schema=impact.CLAIM_SCHEMA,
            declared_effects=["execution"],
            declared_plan_action="replan_required",
            dispositions=[
                {
                    "statement": item["key"],
                    "disposition": "execution_only",
                    "requirements": [],
                    "design": [],
                    "effects": ["execution"],
                    "reason": None,
                }
                for item in statement_inventory(value["extracted"])
            ],
        )
        settle(self.run_dir, claim)

    def prepare(self):
        if self.workflow_owner == 'agentic-sdlc':
            result = fixture_prepare_execution(self.run_dir, self.project, 'FEAT-001', self.plan, 4)
            engine.seal_tdd_base(self.run_dir, 'FEAT-001', 'test: synthetic native admission base')
            return result
        if not engine.coordinator_path(self.run_dir, "FEAT-001").exists():
            engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        return fixture_prepare_execution(
            self.run_dir, self.project, "FEAT-001", self.plan, 4
        )

    def test_native_launch_admits_registered_support_directories_before_arm(self):
        import workflow_transition as transition
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, 'FEAT-001', 'WAVE-001')[0]
        args = transition.parser().parse_args([
            'task-arm', '--run-dir', str(self.run_dir), '--execution', 'FEAT-001',
            '--wave', 'WAVE-001', '--task', assignment['task_id'],
            '--assignment-digest', assignment['assignment_digest'],
        ])
        with mock.patch.object(transition, 'runtime_session_identity', return_value=self.session):
            result = transition.execute(args)
        self.assertEqual(result['launch']['support_write_roots'], [
            str(self.run_dir.parent), str(self.home / 'workflow-resources'),
        ])
        self.assertTrue((self.home / 'workflow-resources').is_dir())
        self.assertEqual((self.home / 'workflow-resources').stat().st_mode & 0o777, 0o700)
        self.assertNotIn(str(self.home), result['launch']['support_write_roots'])

    def test_native_launch_rejects_foreign_authority_before_state_changes(self):
        import workflow_transition as transition
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, 'FEAT-001', 'WAVE-001')[0]
        args = transition.parser().parse_args([
            'task-arm', '--run-dir', str(self.run_dir), '--execution', 'FEAT-001',
            '--wave', 'WAVE-001', '--task', assignment['task_id'],
            '--assignment-digest', assignment['assignment_digest'],
        ])
        task = engine.task_path(self.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id'])
        original = task.read_bytes()
        with mock.patch.object(transition, 'runtime_session_identity', return_value=self.session), \
                mock.patch.dict(os.environ, {'CODEX_HOME': str(self.root / 'foreign-home'),
                                             'CLAUDE_CONFIG_DIR': str(self.root / 'foreign-home')}):
            with self.assertRaises(WorkflowError):
                transition.execute(args)
        self.assertEqual(task.read_bytes(), original)
        self.assertFalse((self.root / 'foreign-home').exists())
        self.assertFalse((self.home / 'workflow-resources').exists())

    def test_native_launch_identity_rejects_unregistered_and_symlinked_support(self):
        from workflow_worker_contract import validate_launch_identity
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, 'FEAT-001', 'WAVE-001')[0]
        path = engine.assignment_path(self.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id'])
        copied = self.run_dir / 'copied-assignment.json'
        write(copied, assignment)
        with self.assertRaises(WorkflowError):
            validate_launch_identity(copied, self.home, self.session)
        with self.assertRaises(WorkflowError):
            validate_launch_identity(path, self.home, 'foreign-coordinator')
        project_file = self.run_dir.parent / 'project.json'
        manifest = read(project_file)
        write(project_file, {**manifest, 'project_id': 'f' * 24})
        with self.assertRaises(WorkflowError):
            validate_launch_identity(path, self.home, self.session)
        write(project_file, manifest)
        foreign = self.root / 'foreign-storage'
        foreign.mkdir(mode=0o700)
        (self.home / 'workflow-resources').symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(WorkflowError):
            validate_launch_identity(path, self.home, self.session)
        self.assertEqual(list(foreign.iterdir()), [])

    def test_native_launch_recovery_declares_lease_storage_without_runtime_domains(self):
        from workflow_worker_contract import launch_contract
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, 'FEAT-001', 'WAVE-001')[0]
        path = engine.assignment_path(self.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id'])
        without_runtime = {**assignment, 'conflict_domains': []}
        self.assertEqual(launch_contract(path, without_runtime)['support_write_roots'],
                         [str(self.run_dir.parent)])
        self.assertEqual(launch_contract(path, without_runtime, recovery_attempt=1)['support_write_roots'],
                         [str(self.run_dir.parent), str(self.home / 'workflow-resources')])

    def test_native_launch_admission_sdlc_arm_and_recovery(self):
        import workflow_transition as transition
        fixture = DirectExecutionTests('test_native_launch_admits_registered_support_directories_before_arm')
        fixture.workflow_owner = 'agentic-sdlc'
        fixture.setUp()
        try:
            fixture.test_native_launch_admits_registered_support_directories_before_arm()
            assignment = read(engine.assignment_path(fixture.run_dir, 'FEAT-001', 'WAVE-001', 'TASK-001'))
            common = ['--run-dir', str(fixture.run_dir), '--execution', 'FEAT-001',
                      '--wave', 'WAVE-001', '--task', assignment['task_id']]
            engine.start_task(fixture.run_dir, 'FEAT-001', 'WAVE-001', assignment['task_id'],
                              assignment['assignment_digest'], 'synthetic-sdlc-worker',
                              Path(assignment['scope_cwd']))
            observed = fixture.result_observation(assignment)
            args = transition.parser().parse_args([
                'authorize-recovery', *common, '--termination', str(observed),
                '--next-session-sha256', objectives.session_hash('synthetic-sdlc-recovery'),
            ])
            with mock.patch.object(transition, 'runtime_session_identity', return_value=fixture.session):
                result = transition.execute(args)
            self.assertEqual(result['launch']['support_write_roots'], [
                str(fixture.run_dir.parent), str(fixture.home / 'workflow-resources'),
            ])
            self.assertEqual(result['launch']['recovery_attempt'], 1)
        finally:
            fixture.tearDown()
            fixture.doCleanups()

    def test_result_cli_ingests_complete_payload_and_finishes_only_from_receipt(self):
        import workflow_transition as transition
        first, _ = self.result_fixture()
        self.result_write(first)
        observation = self.result_observation(first)
        incoming = self.run_dir / "native-output.json"
        write(incoming, self.result_payload(first))
        common = ["--run-dir", str(self.run_dir), "--execution", "FEAT-001",
                  "--wave", "WAVE-001", "--task", first["task_id"],
                  "--termination", str(observation)]
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": self.session}):
            ingested = transition.execute(transition.parser().parse_args([
                "task-result", *common, "--result", str(incoming),
            ]))
            args = ["task-finish", *common, "--receipt", ingested["receipt_path"],
                    "--message", "test: result CLI"]
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                transition.parser().parse_args([*args, "--validation", "projected evidence"])
            result = transition.execute(transition.parser().parse_args(args))
        self.assertEqual(result["worker_result_receipt_sha256"], ingested["receipt_sha256"])
        self.assertEqual(result["validation"], self.result_payload(first)["validation"])

    def test_execution_cli_prepares_and_reports_current_execution(self):
        import workflow_transition

        common = ["--run-dir", str(self.run_dir), "--execution", "FEAT-001"]
        commands = [
            ["prepare-specs", *common, "--project-root", str(self.project)],
            ["prepare", *common, "--project-root", str(self.project),
             "--plan", str(self.plan), "--capacity", "4"],
            ["wave-prepare", *common, "--wave", "WAVE-001"],
            ["status", *common],
        ]
        with mock.patch.object(workflow_transition, "runtime_session_identity",
                               return_value=self.session):
            for command in commands:
                with self.subTest(command=command[0]):
                    result = workflow_transition.execute(
                        workflow_transition.parser().parse_args(command)
                    )
                    self.assertTrue(result)
        self.assertTrue(engine.assignment_path(
            self.run_dir, "FEAT-001", "WAVE-001", "TASK-001"
        ).is_file())

    def test_worker_reconciliation_uses_canonical_execution_flag(self):
        import workflow_transition

        args = workflow_transition.parser().parse_args([
            "worker-reconcile", "--run-dir", str(self.run_dir),
            "--execution", "FEAT-001", "--wave", "WAVE-001",
            "--task", "TASK-001", "--termination", str(self.root / "stopped.json"),
        ])
        with mock.patch.object(workflow_transition, "runtime_session_identity",
                               return_value=self.session), \
                mock.patch.object(reconcile, "reconcile_worker") as observe:
            workflow_transition.execute(args)
        observe.assert_called_once_with(
            self.run_dir, "FEAT-001", "WAVE-001", "TASK-001",
            self.root / "stopped.json", self.session,
        )

    def test_multiline_contract_survives_assignment_serialization(self):
        goal = 'write a.txt\n  JSON fields: {"id": "string", "done": "boolean"}'
        validation = "verify task identifier\n  Reject missing id and non-boolean done."
        done = "expected identifier is present\n  Preserve the exact JSON field types."
        self.plan.write_text(self.plan.read_text().replace("write a.txt", goal, 1)
                             .replace("verify task identifier", validation, 1)
                             .replace("expected identifier is present", done, 1))
        self.plan.write_bytes(self.plan.read_bytes().replace(b"\n", b"\r\n"))
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        persisted = read(engine.assignment_path(self.run_dir, "FEAT-001", "WAVE-001", "TASK-001"))
        for record in (assignment, persisted):
            self.assertEqual(record["goal"], goal)
            self.assertEqual(record["validation"], validation)
            self.assertEqual(record["done_criteria"], done)

    def test_dirty_canonical_specs_cannot_be_excluded_from_preparation(self):
        self.publication(self.project)
        git(self.project, "add", "-A")
        self.settle()
        write(self.run_dir / "input-dependencies.json", {
            "schema": "workflow/input-dependencies-v1",
            "base_head": self.base, "complete": True,
            "claims": [{"kind": "exact", "path": "README.md"}],
        })
        with self.assertRaisesRegex(engine.ExecutionError, "canonical spec"):
            self.prepare()
        self.assertFalse((self.run_dir / "execution").exists())
        self.assertFalse((self.run_dir / "worktrees").exists())
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base)

    def bootstrap_publication(self):
        coordinator = engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        integration = Path(coordinator["integration_worktree"])
        objective = objectives.validate_run(self.run_dir)[1]["revisions"][-1]["sha256"]
        self.publication(integration, objective=objective)
        git(integration, "add", "-A")
        self.settle()
        return integration

    def test_initial_specs_are_committed_in_integration_before_dispatch(self):
        (self.project / "unrelated.txt").write_text("preserve source dirt\n")
        write(self.run_dir / "input-dependencies.json", {
            "schema": "workflow/input-dependencies-v1", "base_head": self.base,
            "complete": True, "claims": [{"kind": "exact", "path": "README.md"}],
        })
        integration = self.bootstrap_publication()
        coordinator = self.prepare()
        initial = coordinator["integration_head"]
        self.assertNotEqual(initial, self.base)
        self.assertEqual(git(integration, "rev-list", "--parents", "-n", "1", initial).split(), [initial, self.base])
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base)
        self.assertEqual((self.project / "unrelated.txt").read_text(), "preserve source dirt\n")
        self.assertFalse((self.project / "docs").exists())
        for assignment in engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001"):
            self.assertEqual(assignment["base_head"], initial)
            for name in ("requirements", "design"):
                self.assertEqual((Path(assignment["scope_cwd"]) / "docs" / f"{name}.md").read_bytes(),
                                 (integration / "docs" / f"{name}.md").read_bytes())

    def test_bootstrap_rejects_unrelated_staged_changes(self):
        integration = self.bootstrap_publication()
        (integration / "unexpected.txt").write_text("not a specification\n")
        git(integration, "add", "-A")
        with self.assertRaisesRegex(engine.ExecutionError, "unrelated integration"):
            self.prepare()
        self.assertEqual(git(integration, "rev-parse", "HEAD"), self.base)
        self.assertFalse((self.run_dir / "execution/FEAT-001/assignments").exists())

    def test_bootstrap_requires_bound_paired_publication(self):
        from project_specs_lib.contracts import canonical_document
        from test_project_specs_transaction import REQUIREMENTS, DESIGN
        coordinator = engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        integration = Path(coordinator["integration_worktree"])
        (integration / "docs").mkdir()
        for name, body in (("requirements", REQUIREMENTS), ("design", DESIGN)):
            (integration / "docs" / f"{name}.md").write_bytes(canonical_document(name, body))
        git(integration, "add", "-A")
        self.settle()
        with self.assertRaisesRegex(engine.ExecutionError, "paired-publication receipt"):
            self.prepare()
        self.assertEqual(git(integration, "rev-parse", "HEAD"), self.base)

    def test_bootstrap_recovers_exact_interrupted_spec_commit(self):
        integration = self.bootstrap_publication()
        real_git = engine.git

        def interrupted(repo, command, action):
            result = real_git(repo, command, action)
            if action == "commit initial canonical specifications":
                raise engine.ExecutionError("TEST_INTERRUPTION", "interrupted after specification commit")
            return result

        with mock.patch.object(engine, "git", side_effect=interrupted):
            with self.assertRaisesRegex(engine.ExecutionError, "interrupted after"):
                self.prepare()
        committed = git(integration, "rev-parse", "HEAD")
        self.assertNotEqual(committed, self.base)
        self.assertEqual(self.prepare()["integration_head"], committed)
        self.assertEqual(git(integration, "rev-parse", "HEAD"), committed)
        self.assertTrue(engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001"))

    def test_bootstrap_blocks_premature_dispatch_and_foreign_publication(self):
        from project_specs_lib.contracts import ProjectSpecError
        coordinator = engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        with self.assertRaisesRegex(engine.ExecutionError, "execution basis"):
            engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertFalse((self.run_dir / "execution/FEAT-001/assignments").exists())
        objective = objectives.validate_run(self.run_dir)[1]["revisions"][-1]["sha256"]
        integration = Path(coordinator["integration_worktree"])
        for checkout, native, expected in (
            (self.project, self.session, "registered integration"),
            (integration, "foreign-root", "bound coordinator"),
        ):
            with self.assertRaisesRegex(ProjectSpecError, expected):
                self.publication(checkout, native=native, objective=objective)
        objectives.fence(self.run_dir, self.session, "cancelled")
        with self.assertRaisesRegex(WorkflowError, "fenced"):
            engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base)
        self.assertTrue(integration.exists())

    def test_interrupted_bootstrap_allocation_cannot_publish_to_source(self):
        from project_specs_lib.contracts import ProjectSpecError
        real_git = engine.git

        def interrupted(repo, command, action):
            if action == "create integration worktree":
                raise engine.ExecutionError("TEST_INTERRUPTION", "allocation interrupted")
            return real_git(repo, command, action)

        with mock.patch.object(engine, "git", side_effect=interrupted):
            with self.assertRaisesRegex(engine.ExecutionError, "allocation interrupted"):
                engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        objective = objectives.validate_run(self.run_dir)[1]["revisions"][-1]["sha256"]
        with self.assertRaisesRegex(ProjectSpecError, "bootstrap is not ready"):
            self.publication(self.project, objective=objective)
        self.assertFalse((self.project / "docs").exists())
        coordinator = engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        self.assertEqual(coordinator["status"], "specs_prepared")
        self.assertTrue(Path(coordinator["integration_worktree"]).exists())

    def test_ignored_canonical_specs_cannot_look_like_an_unchanged_base(self):
        from workflow_spec_bootstrap import seal_specifications, verify_source_specs
        (self.project / ".git/info/exclude").write_text("docs/*.md\n")
        coordinator = engine.prepare_specifications(self.run_dir, self.project, "FEAT-001")
        integration = Path(coordinator["integration_worktree"])
        objective = objectives.validate_run(self.run_dir)[1]["revisions"][-1]["sha256"]
        self.publication(integration, objective=objective)
        with self.assertRaisesRegex(engine.ExecutionError, "committed integration blobs"):
            seal_specifications(self.run_dir, "FEAT-001", coordinator)
        (self.project / "docs").mkdir()
        (self.project / "docs/requirements.md").write_bytes((integration / "docs/requirements.md").read_bytes())
        with self.assertRaisesRegex(engine.ExecutionError, "canonical spec inputs"):
            verify_source_specs(self.project, ".", self.base)

    def test_committed_spec_verification_preserves_crlf_bytes(self):
        from workflow_spec_bootstrap import _verify_committed_pair
        self.publication(self.project)
        git(self.project, "config", "core.autocrlf", "false")
        for path in (self.project / "docs").glob("*.md"):
            path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        git(self.project, "add", "-A")
        git(self.project, "commit", "-qm", "CRLF canonical pair")
        _verify_committed_pair(self.project, self.project)

    def start(self, assignment, *, wave="WAVE-001"):
        engine.arm_task(
            self.run_dir,
            "FEAT-001",
            wave,
            assignment["task_id"],
            assignment["assignment_digest"],
        )
        session = "native-stub-" + assignment["task_id"]
        engine.start_task(
            self.run_dir,
            "FEAT-001",
            wave,
            assignment["task_id"],
            assignment["assignment_digest"],
            session,
            Path(assignment["scope_cwd"]),
        )
        return session

    def observation(self, assignment, *, wave="WAVE-001"):
        task = read(
            engine.task_path(
                self.run_dir, "FEAT-001", wave, assignment["task_id"]
            )
        )
        path = (
            self.run_dir
            / "execution/FEAT-001/terminations"
            / (assignment["task_id"] + ".json")
        )
        write(
            path,
            {
                "schema": "workflow/worker-termination-v1",
                "assignment_digest": assignment["assignment_digest"],
                "worker_session_hash": task["worker_session_hash"],
                "status": "terminated",
                "observer_session_hash": objectives.session_hash(self.session),
                "native_handle": "deterministic-native-stub",
                "observation": "synthetic observed exit fixture",
            },
        )
        return path

    def finish(self, assignment, *, admit=True, wave="WAVE-001"):
        worker = Path(assignment["worktree"])
        target = assignment["write_claims"][0]["path"]
        (worker / target).write_text(assignment["task_id"] + "\n")
        termination = self.observation(assignment, wave=wave)
        receipt = ingest_result(self.run_dir, "FEAT-001", wave, assignment["task_id"], {
            "task_id": assignment["task_id"], "assignment_digest": assignment["assignment_digest"],
            "status": "implemented", "summary": "housekeeping completed", "decisions": [],
            "open_risks": [], "spec_gaps": [], "validation": "identifier verified",
            "review": "scoped review passed",
        }, termination, self.session)
        result = engine.finish_task(
            self.run_dir,
            "FEAT-001",
            wave,
            assignment["task_id"],
            Path(receipt["receipt_path"]),
            "test: " + assignment["task_id"],
            termination=termination,
            session_identity=self.session,
        )
        if admit:
            engine.admit_task(
                self.run_dir,
                "FEAT-001",
                wave,
                assignment["task_id"],
                termination,
            )
        return result

    def integrate(self):
        self.prepare()
        pending = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        while pending:
            for assignment in pending:
                self.start(assignment)
                self.finish(assignment)
            pending = engine.refill_wave(self.run_dir, "FEAT-001", "WAVE-001")
        engine.integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")
        fixture_complete_wave(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            "all identifiers independently checked",
            session=self.session,
        )

    def deliver(self):
        self.integrate()
        engine.seal_feature(
            self.run_dir,
            "FEAT-001",
            "combined validation and alignment passed",
            "test: final",
        )
        return engine.promote_feature(
            self.run_dir, "FEAT-001", "exact delivered files checked"
        )

    def test_issue_evidence_cli_seal_promote_and_completion(self):
        import workflow_transition as transition
        import workflow_issue_resolution as issue_owner

        member = {"host": "github.com", "repository": "example/files", "number": 12}
        criterion = "a.txt contains TASK-001"
        self.objective["issue_resolution"] = {
            "plan_path": "docs/github-issues/batch-12-files.md", "plan_sha256": "a" * 64,
            "close_after_merge": True, "closure_request_sha256": "b" * 64,
            "members": [{**member, "criteria": [criterion]}],
        }
        objectives.accept(self.project, self.home, "implement", self.session,
                          self.objective, revise=True)
        self.settle()
        reconcile.resume_replanned(self.run_dir, self.session)
        self.integrate()
        coordinator = read(engine.coordinator_path(self.run_dir, "FEAT-001"))
        self.assertIn("TASK-001", (Path(coordinator["integration_worktree"]) / "a.txt").read_text())
        evidence = {"schema": issue_owner.EVIDENCE_SCHEMA, "issues": [{
            **member, "summary": "Delivered required identifier", "criteria": [{
                "criterion": criterion, "status": "passed",
                "evidence": "Independently read a.txt and observed TASK-001",
            }],
        }]}
        path = self.run_dir / "issue-checks.json"
        write(path, evidence)
        args = transition.parser().parse_args([
            "seal-feature", "--run-dir", str(self.run_dir), "--execution", "FEAT-001",
            "--evidence", "combined validation passed", "--message", "test: final",
            "--issue-evidence", str(path),
        ])
        with mock.patch.object(transition, "runtime_session_identity", return_value=self.session):
            sealed = transition.execute(args)
            evidence["issues"][0]["summary"] = "Conflicting retry"
            write(path, evidence)
            with self.assertRaisesRegex(engine.ExecutionError, "sealed issue evidence changed"):
                transition.execute(args)
            promoted = transition.execute(transition.parser().parse_args([
                "promote", "--run-dir", str(self.run_dir), "--execution", "FEAT-001",
                "--evidence", "delivered files independently checked",
            ]))
        receipt = issue_owner.read_handoff(self.run_dir)
        self.assertEqual(receipt["delivered_head"], git(self.project, "rev-parse", "HEAD"))
        self.assertEqual(receipt["delivered_head"], promoted["promoted_head"])
        self.assertEqual(receipt["delivered_head"], sealed["integration_head"])
        self.assertTrue(receipt["issues"][0]["eligible"])
        self.assertEqual(receipt["merge_status"], "pending")
        receipt_path = self.run_dir / "issue-resolution.json"
        before = receipt_path.read_bytes()
        objectives.complete_run(self.run_dir, self.session)
        self.assertEqual(receipt_path.read_bytes(), before)
        self.assertEqual(receipt_path.stat().st_mode & 0o777, 0o600)

    def publication(self, checkout, *, native=None, objective=None, basis=None):
        from project_specs_lib.transaction import publish_spec_pair
        from project_specs_lib.contracts import canonical_document
        from test_project_specs_transaction import REQUIREMENTS, DESIGN
        import os

        arguments = dict(
            requirements_candidate=canonical_document("requirements", REQUIREMENTS),
            design_candidate=canonical_document("design", DESIGN),
            expected_git_head=git(checkout, "rev-parse", "HEAD"),
            expected_requirements_sha256="absent",
            expected_design_sha256="absent",
            operation_id="test-publication",
            expected_objective_sha256=objective,
            expected_basis_sha256=basis,
        )
        with mock.patch.dict(
            os.environ,
            {"CODEX_HOME": str(self.home), "CODEX_THREAD_ID": native or self.session},
        ):
            return publish_spec_pair(checkout, **arguments)

    def test_publication_boundary_enforces_checkout_worker_and_revision(self):
        from project_specs_lib.contracts import ProjectSpecError
        from workflow_publication import _basis

        coordinator = self.prepare()
        integration = Path(coordinator["integration_worktree"])
        objective = objectives.validate_run(self.run_dir)[1]["revisions"][-1]["sha256"]
        basis = _basis(self.run_dir, "FEAT-001")
        for checkout, native, expected in (
            (self.project, self.session, "registered integration"),
            (integration, "foreign-worker", "bound coordinator"),
        ):
            with self.assertRaisesRegex(ProjectSpecError, expected):
                self.publication(
                    checkout, native=native, objective=objective, basis=basis
                )
            self.assertFalse((checkout / "docs").exists())
        with self.assertRaisesRegex(ProjectSpecError, "accepted objective"):
            self.publication(integration, objective="0" * 64, basis=basis)
        with self.assertRaisesRegex(ProjectSpecError, "basis is stale"):
            self.publication(integration, objective=objective, basis="0" * 64)
        self.publication(integration, objective=objective, basis=basis)
        self.assertTrue((integration / "docs/design.md").is_file())
        self.assertFalse((self.project / "docs").exists())

    def test_delivered_source_rejects_prior_worker_publication(self):
        from project_specs_lib.contracts import ProjectSpecError
        self.deliver()
        with self.assertRaisesRegex(ProjectSpecError, "workers report gaps"):
            self.publication(self.project, native="native-stub-TASK-001")
        self.assertFalse((self.project / "docs").exists())

    def test_completion_requires_authoritative_outer_lease_release(self):
        self.deliver()
        import workflow_interop
        with mock.patch.object(workflow_interop, "load", return_value={"mode": "managed", "released": True}):
            with mock.patch.object(workflow_interop, "_reconcile", return_value={"state": "active"}):
                with self.assertRaisesRegex(WorkflowError, "outer lease"):
                    objectives.complete_run(self.run_dir, self.session)
            self.assertEqual(read(self.run_dir / "run.json")["status"], "accepted")
            with mock.patch.object(workflow_interop, "_reconcile", return_value={"state": "released"}):
                self.assertEqual(objectives.complete_run(self.run_dir, self.session)["status"], "COMPLETE")

    def test_delivered_source_allows_independent_spec_maintenance(self):
        self.deliver()
        self.publication(self.project, native="another-root")
        self.assertTrue((self.project / "docs/design.md").is_file())

    def test_delivered_feature_retains_canonical_publication_ownership(self):
        from project_specs_lib.contracts import ProjectSpecError

        git(
            self.project,
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/feature/source",
        )
        coordinator = self.deliver()
        with self.assertRaisesRegex(ProjectSpecError, "delivered checkout"):
            self.publication(self.project, native="another-root")
        self.publication(
            Path(coordinator["integration_worktree"]), native="another-root"
        )

    def test_no_impact_needs_no_spec_documents(self):
        self.prepare()
        self.assertFalse((self.project / "docs").exists())
        self.assertEqual(
            read(self.run_dir / "objective-impact/attempt-0001.json")[
                "requirements_sha256"
            ],
            "absent",
        )

    def test_dispatch_rejects_changed_or_missing_execution_contracts(self):
        catalog = self.root / "catalog"
        for name in (
            "worktree", "implement", "sdlc-start", "sdlc-prepare-execution",
            "sdlc-implement-plan", "maintain-project-specs",
            "global-context-management", "code-review", "align",
        ):
            shutil.copytree(
                Path(__file__).resolve().parents[2] / name,
                catalog / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
        with mock.patch.object(
            impact, "__file__", str(catalog / "worktree/scripts/workflow_impact.py")
        ):
            coordinator = self.prepare()
            refs = git(self.project, "show-ref")
            worktrees = git(self.project, "worktree", "list", "--porcelain")
            execution = self.run_dir / "execution/FEAT-001"
            before = {
                path.relative_to(execution): path.read_bytes()
                for path in execution.rglob("*") if path.is_file()
            }
            for relative in (
                "implement/references/implementation-loop.md",
                "implement/references/objective-contract.md",
                "worktree/assets/worker-result.schema.json",
                "global-context-management/references/agent-hosts.md",
                "maintain-project-specs/SKILL.md",
                "code-review/SKILL.md",
                "align/SKILL.md",
                "sdlc-prepare-execution/references/execution-plane.md",
            ):
                path = catalog / relative
                original = path.read_bytes()
                for change in ("changed", "missing", "symlink"):
                    with self.subTest(path=relative, change=change):
                        if change == "changed":
                            path.write_bytes(original + b"\n")
                        else:
                            path.unlink()
                            if change == "symlink":
                                path.symlink_to(Path(__file__).resolve())
                        try:
                            with self.assertRaises(engine.ExecutionError) as caught:
                                engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
                            self.assertEqual(
                                caught.exception.code,
                                "REPLAN_REQUIRED" if change == "changed" else "BASIS_INVALID",
                            )
                            self.assertEqual(git(self.project, "show-ref"), refs)
                            self.assertEqual(
                                git(self.project, "worktree", "list", "--porcelain"), worktrees
                            )
                            self.assertEqual(
                                {p.relative_to(execution): p.read_bytes()
                                 for p in execution.rglob("*") if p.is_file()},
                                before,
                            )
                        finally:
                            if path.is_symlink():
                                path.unlink()
                            path.write_bytes(original)
                impact.verify_execution(
                    self.run_dir, "FEAT-001", coordinator["plan_digest"], self.project
                )

    def test_refill_waits_for_admission_and_does_not_wait_for_peer(self):
        self.prepare()
        first, peer = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.start(first)
        self.start(peer)
        self.finish(first, admit=False)
        self.assertEqual(engine.refill_wave(self.run_dir, "FEAT-001", "WAVE-001"), [])
        engine.admit_task(
            self.run_dir,
            "FEAT-001",
            "WAVE-001",
            first["task_id"],
            self.observation(first),
        )
        [replacement] = engine.refill_wave(self.run_dir, "FEAT-001", "WAVE-001")
        self.assertEqual(replacement["task_id"], "TASK-003")
        self.assertEqual(first["base_head"], replacement["base_head"])
        self.assertEqual(
            read(
                engine.task_path(self.run_dir, "FEAT-001", "WAVE-001", peer["task_id"])
            )["status"],
            "running",
        )
        with self.assertRaises(engine.ExecutionError):
            engine.integrate_wave(self.run_dir, "FEAT-001", "WAVE-001")

    def test_unobserved_capacity_uses_one_slot(self):
        (self.run_dir / "host-capacity.json").unlink()
        self.assertEqual(self.prepare()["capacity"], 4)
        self.assertEqual(
            len(engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")), 1
        )

    def test_cross_session_continuation_preserves_prepared_basis(self):
        coordinator = self.prepare()
        pointer = (
            self.run_dir / "objective-impact/execution/FEAT-001.json"
        ).read_bytes()
        objectives.continue_run(
            self.project, self.home, "implement", "new-native-session"
        )
        self.assertEqual(
            (self.run_dir / "objective-impact/execution/FEAT-001.json").read_bytes(),
            pointer,
        )
        impact.verify_execution(
            self.run_dir, "FEAT-001", coordinator["plan_digest"], self.project
        )
        with self.assertRaises(WorkflowError):
            objectives.authorize_coordinator(self.run_dir, self.session)
        self.assertEqual(
            len(engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")), 1
        )

    def test_cancellation_drains_writers_without_admitting_results(self):
        self.prepare()
        assignments = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start(assignment)
        objectives.fence(self.run_dir, self.session, "cancelled")
        with self.assertRaises(WorkflowError):
            reconcile.cancel_complete(self.run_dir, self.session)
        for assignment in assignments:
            with self.assertRaises(engine.ExecutionError):
                self.finish(assignment, admit=False)
            self.assertEqual(
                git(Path(assignment["worktree"]), "rev-parse", "HEAD"),
                assignment["base_head"],
            )
            with self.assertRaises(engine.ExecutionError):
                engine.admit_task(
                    self.run_dir,
                    "FEAT-001",
                    "WAVE-001",
                    assignment["task_id"],
                    self.observation(assignment),
                )
            reconcile.reconcile_worker(
                self.run_dir,
                "FEAT-001",
                "WAVE-001",
                assignment["task_id"],
                self.observation(assignment),
                self.session,
            )
        cancelled = reconcile.cancel_complete(self.run_dir, self.session)
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertGreaterEqual(len(cancelled["retained_effects"]), 5)
        self.assertTrue(
            any(
                row.get("kind") == "integration"
                for row in cancelled["retained_effects"]
            )
        )
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base)
        self.assertEqual(
            reconcile.cancel_complete(self.run_dir, self.session), cancelled
        )

    def test_clean_nondefault_delivery_and_completion(self):
        result = self.deliver()
        self.assertEqual(result["status"], "done")
        self.assertEqual(
            git(self.project, "rev-parse", "HEAD"), result["promoted_head"]
        )
        self.assertFalse(Path(result["integration_worktree"]).exists())
        objectives.complete_run(self.run_dir, self.session)
        self.assertEqual(
            objectives.observe_binding(self.home, "implement", self.session)[1][
                "status"
            ],
            "done",
        )

    def test_steered_unknown_default_run_delivers_with_retained_history(self):
        git(self.project, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        original_plan = self.plan.read_text().split("### TASK-002")[0]
        self.plan.write_text(original_plan)
        validation_path = self.plan.with_suffix(".md.validation.json")
        validation = read(validation_path)
        validation["task_checks"] = {"TASK-001": validation["task_checks"]["TASK-001"]}
        write_private(validation_path, validation)
        self.prepare()
        first = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        self.start(first)
        objectives.accept(self.project, self.home, "implement", self.session,
                          {**self.objective, "outcome": "Write the revised task identifier"}, revise=True)
        reconcile.reconcile_worker(self.run_dir, "FEAT-001", "WAVE-001", first["task_id"],
                                   self.observation(first), self.session)
        reconcile.retire_fenced_waves(self.run_dir, self.session)
        self.settle()
        replacement = self.plan.with_name("FEAT-001.plan.v2.md")
        replacement.write_text(original_plan.replace("Plan v1", "Plan v2") +
                               "### TASK-002" + original_plan.split("### TASK-001", 1)[1])
        replacement.with_suffix(".md.lock").write_text("locked\n")
        fixture_replan_future(self.run_dir, "FEAT-001", replacement, 2)
        reconcile.resume_replanned(self.run_dir, self.session)
        second = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-002")[0]
        self.start(second, wave="WAVE-002")
        self.finish(second, wave="WAVE-002")
        engine.integrate_wave(self.run_dir, "FEAT-001", "WAVE-002")
        fixture_complete_wave(self.run_dir, "FEAT-001", "WAVE-002", "revised identifier checked", session=self.session)
        engine.seal_feature(self.run_dir, "FEAT-001", "combined validation and alignment", "final")
        result = engine.promote_feature(self.run_dir, "FEAT-001", "delivered identifier checked")
        self.assertEqual(result["delivery_result"], "feature-checkout")
        self.assertEqual(len(result["cleanup_retained"]), 1)
        self.assertEqual(result["cleanup_retained"][0]["kind"], "worker")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base)
        self.assertEqual((Path(result["delivered_checkout"]) / "a.txt").read_text(), "TASK-002\n")
        self.assertEqual(read(engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001"))["status"], "superseded")

    def test_unknown_default_delivers_feature_checkout(self):
        git(self.project, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
        result = self.deliver()
        self.assertEqual(result["delivery_result"], "feature-checkout")
        self.assertEqual(git(self.project, "rev-parse", "HEAD"), self.base)
        self.assertTrue(Path(result["delivered_checkout"]).is_dir())
        self.assertEqual(
            impact.spec_checkout(self.run_dir, self.project),
            Path(result["delivered_checkout"]),
        )
        self.assertEqual(
            engine.promote_feature(
                self.run_dir, "FEAT-001", "exact delivered files checked"
            )["promoted_head"],
            result["promoted_head"],
        )

    def test_dirty_source_requires_dependencies_and_preserves_unrelated_content(self):
        stray = self.project / "unrelated file.txt"
        stray.write_text("preserve\n")
        with self.assertRaises(engine.ExecutionError) as raised:
            self.prepare()
        self.assertEqual(raised.exception.code, "UNCOMMITTED_INPUT")
        write(
            self.run_dir / "input-dependencies.json",
            {
                "schema": "workflow/input-dependencies-v1",
                "base_head": self.base,
                "complete": True,
                "claims": [{"kind": "exact", "path": "README.md"}],
            },
        )
        self.prepare()
        self.assertEqual(stray.read_text(), "preserve\n")
        self.assertFalse(
            (
                Path(
                    read(engine.coordinator_path(self.run_dir, "FEAT-001"))[
                        "integration_worktree"
                    ]
                )
                / stray.name
            ).exists()
        )

    def test_retired_wave_keeps_exact_native_reconciliation_evidence(self):
        self.prepare()
        assignments = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")
        for assignment in assignments:
            self.start(assignment)
        objectives.fence(self.run_dir, self.session, "cancelled")
        for assignment in assignments:
            reconcile.reconcile_worker(self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"],
                                       self.observation(assignment), self.session)
        reconcile.retire_fenced_waves(self.run_dir, self.session)
        wave = read(engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001"))
        receipt = reconcile.validate_retained_wave(self.run_dir, "FEAT-001", wave)
        self.assertEqual(len(receipt["assignments"]), len(assignments))
        proof = self.run_dir / receipt["assignments"][0]["proof_path"]
        original = read(proof)
        write(proof, {**original, "termination_sha256": "0"*64})
        with self.assertRaisesRegex(WorkflowError, "evidence changed"):
            reconcile.validate_retained_wave(self.run_dir, "FEAT-001", wave)
        write(proof, original)
        # Crash after retention publication but before the mutable wave update.
        wave["status"] = "running"
        write(engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001"), wave)
        reconcile.retire_fenced_waves(self.run_dir, self.session)
        self.assertEqual(read(engine.wave_path(self.run_dir, "FEAT-001", "WAVE-001"))["status"], "superseded")

    def test_dirty_read_dependency_blocks_before_worktree(self):
        (self.project / "README.md").write_text("uncommitted dependency\n")
        write(
            self.run_dir / "input-dependencies.json",
            {
                "schema": "workflow/input-dependencies-v1",
                "base_head": self.base,
                "complete": True,
                "claims": [{"kind": "exact", "path": "README.md"}],
            },
        )
        with self.assertRaises(engine.ExecutionError):
            self.prepare()
        self.assertFalse((self.run_dir / "worktrees").exists())

    def test_runtime_consumption_blocks_finish_before_git_or_state_mutation(self):
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        session = self.start(assignment)
        path = engine.assignment_path(self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"])
        resources.acquire(self.home, path, "test-a", session)
        worker = Path(assignment["worktree"])
        (worker / "a.txt").write_text("TASK-001\n")
        termination = self.observation(assignment)
        before_head, before_index = git(worker, "rev-parse", "HEAD"), git(worker, "write-tree")
        execution_root = self.run_dir / "execution"
        before = {p: p.read_bytes() for p in execution_root.rglob("*") if p.is_file()}
        with resources.consume(self.home, path, "test-a"):
            with self.assertRaisesRegex(engine.ExecutionError, "release assignment runtime leases"):
                engine.finish_task(self.run_dir, "FEAT-001", "WAVE-001", "TASK-001",
                                   self.run_dir / "absent-receipt.json", "test: task",
                                   termination=termination, session_identity=self.session)
        self.assertEqual(git(worker, "rev-parse", "HEAD"), before_head)
        self.assertEqual(git(worker, "write-tree"), before_index)
        self.assertEqual({p: p.read_bytes() for p in execution_root.rglob("*") if p.is_file()}, before)

    def test_runtime_consumption_blocks_release_without_blocking_heartbeat(self):
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        session = self.start(assignment)
        path = engine.assignment_path(self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"])
        resources.acquire(self.home, path, "test-a", session)
        attempted, finished = threading.Event(), threading.Event()
        errors = []

        def release():
            attempted.set()
            try:
                resources.release(self.home, path, "test-a", session)
            except Exception as error:
                errors.append(error)
            finally:
                finished.set()

        with resources.consume(self.home, path, "test-a"):
            thread = threading.Thread(target=release)
            thread.start()
            self.assertTrue(attempted.wait(2))
            self.assertFalse(finished.wait(0.2))
            engine.heartbeat_task(self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"],
                                  assignment["assignment_digest"], "validating", session,
                                  Path(assignment["scope_cwd"]))
            self.assertEqual(engine.watch_task(self.run_dir, "FEAT-001", "WAVE-001",
                                              assignment["task_id"], assignment["assignment_digest"])["status"], "ACTIVE")
            self.assertEqual(resources.verify(self.home, path, "test-a")["status"], "active")
        thread.join(3)
        self.assertTrue(finished.is_set())
        self.assertFalse(errors)
        with self.assertRaises(WorkflowError):
            resources.verify(self.home, path, "test-a")

    def test_runtime_lease_requires_exact_worker_and_exclusive_identity(self):
        self.prepare()
        assignment = engine.prepare_wave(self.run_dir, "FEAT-001", "WAVE-001")[0]
        session = self.start(assignment)
        path = engine.assignment_path(
            self.run_dir, "FEAT-001", "WAVE-001", assignment["task_id"]
        )
        lease = resources.acquire(self.home, path, "test-a", session)
        self.assertEqual(
            resources.verify(self.home, path, "test-a", session)["status"], "active"
        )
        with self.assertRaises(WorkflowError):
            resources.acquire(self.home, path, "test-a", "foreign-worker")
        with self.assertRaises(WorkflowError):
            resources.acquire(self.home, path, "test-a", None)
        with self.assertRaises(WorkflowError):
            resources.acquire(self.home, path, "foreign-runtime", session)
        occupied = read(Path(lease["lease_path"]))
        occupied["assignment_digest"] = "b" * 64
        write(Path(lease["lease_path"]), occupied)
        with self.assertRaises(WorkflowError) as raised:
            resources.acquire(self.home, path, "test-a", session)
        self.assertEqual(raised.exception.code, "RESOURCE_BUSY")
        with self.assertRaises(WorkflowError):
            resources.verify(self.home, path, "test-a")

    def test_worker_cannot_invoke_coordinator_promotion(self):
        import workflow_transition

        args = workflow_transition.parser().parse_args(
            [
                "promote",
                "--run-dir",
                str(self.run_dir),
                "--execution",
                "FEAT-001",
                "--evidence",
                "not authority",
            ]
        )
        with mock.patch.object(
            workflow_transition,
            "runtime_session_identity",
            return_value="foreign-worker",
        ):
            with self.assertRaises(WorkflowError):
                workflow_transition.execute(args)


class ManagedSdlcCompletionTests(unittest.TestCase):
    def fixture(self, owner="agentic-sdlc"):
        f = DirectExecutionTests(
            "test_completion_requires_authoritative_outer_lease_release"
        )
        f.workflow_owner = owner
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.addCleanup(f.tearDown)
        f.deliver()
        return f

    def state(self, integrated=False):
        return {
            "mode": "managed",
            "name": "test-outer",
            "released": True,
            "promoted_head": "a" * 40,
            "outer_integration_status": "integrated" if integrated else "pending",
            "source_integration_head": "b" * 40 if integrated else None,
        }

    def observed(self):
        return {
            "lifecycle_status": "integrated",
            "head": "a" * 40,
            "integration_head": "b" * 40,
            "source_contains_integration": True,
        }

    def test_pending_blocks_before_terminal_writes(self):
        f = self.fixture()
        before = (f.run_dir / "run.json").read_bytes()
        with (
            mock.patch.object(interop, "load", return_value=self.state()),
            mock.patch.object(
                interop, "_reconcile", return_value={"state": "released"}
            ),
            mock.patch.object(issue_resolution, "write_completion") as terminal,
            mock.patch.object(objectives, "_bind") as binding,
            mock.patch.object(interop, "_call") as inspect,
        ):
            with self.assertRaisesRegex(WorkflowError, "source integration") as error:
                objectives.complete_run(f.run_dir, f.session)
            self.assertEqual(error.exception.code, "COMPLETION_BLOCKED")
            terminal.assert_not_called()
            binding.assert_not_called()
            inspect.assert_not_called()
        self.assertEqual((f.run_dir / "run.json").read_bytes(), before)

    def test_stale_or_missing_authoritative_proof_blocks(self):
        f = self.fixture()
        before = (f.run_dir / "run.json").read_bytes()
        for key, value in [
            ("lifecycle_status", "released"),
            ("head", "c" * 40),
            ("integration_head", "c" * 40),
            ("source_contains_integration", False),
            ("integration_head", None),
        ]:
            observed = self.observed() | {key: value}
            with (
                self.subTest(key=key, value=value),
                mock.patch.object(interop, "load", return_value=self.state(True)),
                mock.patch.object(
                    interop, "_reconcile", return_value={"state": "released"}
                ),
                mock.patch.object(interop, "_call", return_value=observed),
                mock.patch.object(issue_resolution, "write_completion") as terminal,
                mock.patch.object(objectives, "_bind") as binding,
            ):
                with self.assertRaises(WorkflowError) as error:
                    objectives.complete_run(f.run_dir, f.session)
                self.assertEqual(error.exception.code, "COMPLETION_BLOCKED")
                terminal.assert_not_called()
                binding.assert_not_called()
                self.assertEqual((f.run_dir / "run.json").read_bytes(), before)

    def test_matching_recorded_and_authoritative_proof_completes(self):
        f = self.fixture()
        state = self.state(True)
        with (
            mock.patch.object(interop, "load", return_value=state),
            mock.patch.object(
                interop, "_reconcile", return_value={"state": "released"}
            ),
            mock.patch.object(
                interop, "_call", return_value=self.observed()
            ) as inspect,
            mock.patch.object(interop, "_write") as publish,
        ):
            for _ in range(2):
                self.assertEqual(
                    objectives.complete_run(f.run_dir, f.session)["status"], "COMPLETE"
                )
            self.assertEqual(inspect.call_count, 2)
            publish.assert_not_called()
            self.assertEqual(
                inspect.call_args.args[1],
                ["inspect", "--name", "test-outer", "--require-clean"],
            )
        self.assertEqual(read(f.run_dir / "run.json")["status"], "done")
        self.assertEqual(state["source_integration_head"], "b" * 40)

    def test_authoritative_inspection_failure_blocks_without_terminal_writes(self):
        f = self.fixture()
        before = (f.run_dir / "run.json").read_bytes()
        with (
            mock.patch.object(interop, "load", return_value=self.state(True)),
            mock.patch.object(
                interop, "_reconcile", return_value={"state": "released"}
            ),
            mock.patch.object(
                interop,
                "_call",
                side_effect=interop.ExecutionInteropError("inspection unavailable"),
            ),
            mock.patch.object(issue_resolution, "write_completion") as terminal,
            mock.patch.object(objectives, "_bind") as binding,
        ):
            with self.assertRaisesRegex(
                WorkflowError, "inspection unavailable"
            ) as error:
                objectives.complete_run(f.run_dir, f.session)
            self.assertEqual(error.exception.code, "COMPLETION_BLOCKED")
            terminal.assert_not_called()
            binding.assert_not_called()
        self.assertEqual((f.run_dir / "run.json").read_bytes(), before)

    def test_disappearing_managed_proof_blocks_completion(self):
        f = self.fixture()
        before = (f.run_dir / "run.json").read_bytes()
        with mock.patch.object(interop, "load", side_effect=[self.state(True), None]), \
                mock.patch.object(interop, "_reconcile", return_value={"state": "released"}), \
                mock.patch.object(issue_resolution, "write_completion") as terminal:
            with self.assertRaisesRegex(WorkflowError, "source integration") as error:
                objectives.complete_run(f.run_dir, f.session)
            self.assertEqual(error.exception.code, "COMPLETION_BLOCKED")
            terminal.assert_not_called()
        self.assertEqual((f.run_dir / "run.json").read_bytes(), before)

    def test_implement_released_lease_still_completes(self):
        f = self.fixture("implement")
        with (
            mock.patch.object(interop, "load", return_value=self.state()),
            mock.patch.object(
                interop, "_reconcile", return_value={"state": "released"}
            ),
            mock.patch.object(interop, "complete_source_integration") as integration,
        ):
            self.assertEqual(
                objectives.complete_run(f.run_dir, f.session)["status"], "COMPLETE"
            )
            integration.assert_not_called()

    def test_unmanaged_sdlc_still_completes(self):
        f = self.fixture()
        self.assertEqual(
            objectives.complete_run(f.run_dir, f.session)["status"], "COMPLETE"
        )

    def test_cli_reports_pending_as_structured_block(self):
        f = self.fixture()
        output = io.StringIO()
        with (
            mock.patch.object(intake, "complete_run", objectives.complete_run),
            mock.patch.object(intake, "agent_home", return_value=f.home),
            mock.patch.object(intake, "native_session_id", return_value=f.session),
            mock.patch.object(interop, "load", return_value=self.state()),
            mock.patch.object(
                interop, "_reconcile", return_value={"state": "released"}
            ),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(
                intake.main(
                    ["--owner", "agentic-sdlc", "complete", "--run-dir", str(f.run_dir)]
                ),
                2,
            )
        self.assertEqual(json.loads(output.getvalue())["code"], "COMPLETION_BLOCKED")
        self.assertEqual(read(f.run_dir / "run.json")["status"], "accepted")


if __name__ == "__main__":
    unittest.main()
