"""Real-Git acceptance and adversarial policy/evidence regressions."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import workflow_execution as execution
from workflow_state import read, write, WorkflowError
import workflow_task_acceptance as acceptance
import workflow_task_container as container
import test_workflow_contracts as fixtures


def criterion(name="content", disposition="check", code="from pathlib import Path; assert Path('a.txt').read_text() == 'RESULT = 1\\n'"):
    return {"id": name, "purpose": "implementation", "disposition": disposition, "reason": "Verify task-owned behavior",
            "prerequisite": "later product interface" if disposition == "deferred" else None,
            "resolution_stage": "before_delivery" if disposition == "deferred" else None,
            "check": {"kind": "command", "definition": {"id": name,
                "argv": ["{python}", "-I", "-c", code], "execution_policy": {"observation_interval_seconds": 30, "deadline": None}, "environment_keys": []}}}


class TaskAcceptanceTests(unittest.TestCase):
    def test_implement_does_not_require_container_skill_identity(self):
        import workflow_impact as impact
        missing = Path(impact.__file__).resolve().parents[2] / "test-software-in-container/SKILL.md"
        original = Path.is_file
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(Path, "is_file", lambda path: path != missing and original(path)):
            impact._identity_files(Path(temporary), owner="implement")
            impact._identity_files(Path(temporary), owner="agentic-sdlc")
            policy = {"tasks": {"TASK-001": {"criteria": [{"purpose": "implementation", "check": {"kind": "container"}}]}}}
            with self.assertRaises(impact.ObjectiveImpactError):
                impact._identity_files(Path(temporary), owner="agentic-sdlc", task_policy=policy)

    def fixture(self, *, mode="check", code=None):
        fixture = fixtures.DirectExecutionTests("test_aggregation_consumes_real_producer_basis_and_rejects_tampering")
        fixture.workflow_owner = "agentic-sdlc"
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        policy = read(acceptance.policy_path(fixture.plan))
        definition = criterion(disposition=mode)
        if code is not None:
            definition["check"]["definition"]["argv"][-1] = code
        policy["tasks"]["TASK-001"]["criteria"] = [definition]
        write(acceptance.policy_path(fixture.plan), policy)
        return fixture, policy

    def start(self, fixture):
        first, _ = fixture.result_fixture()
        fixture.result_write(first)
        path = execution.assignment_path(fixture.run_dir, "FEAT-001", "WAVE-001", first["task_id"])
        return first, path, "receipt-native-stub-" + first["task_id"]

    def test_missing_policy_fails_before_execution_allocation(self):
        fixture, _ = self.fixture()
        acceptance.policy_path(fixture.plan).unlink()
        with self.assertRaises(execution.ExecutionError):
            execution.prepare_execution(fixture.run_dir, fixture.project, "FEAT-001", fixture.plan, 2)
        self.assertFalse(execution.coordinator_path(fixture.run_dir, "FEAT-001").exists())

    def test_policy_and_assignment_are_frozen(self):
        fixture, _ = self.fixture()
        assignment, _, _ = self.start(fixture)
        changed = copy.deepcopy(assignment)
        changed["task_acceptance"]["criteria"][0]["reason"] = "changed"
        with self.assertRaises(WorkflowError):
            acceptance.assignment_policy(changed)

    def test_executed_receipt_binds_staged_and_historical_tree(self):
        fixture, _ = self.fixture()
        assignment, path, session = self.start(fixture)
        output = acceptance.produce(path, session)
        self.assertEqual(output["outcome"], "passed")
        receipt, observed, _ = fixture.result_ingest(assignment)
        result = fixture.result_finish(assignment, receipt, observed)
        self.assertIsNotNone(result["task_acceptance_receipt_sha256"])
        execution._validate_result_record(result, assignment)
        produced = Path(output["receipt_path"]).with_suffix(".produced.json")
        produced.unlink()
        with self.assertRaises((WorkflowError, OSError)):
            execution._validate_result_record(result, assignment)

    def test_text_success_and_changed_source_cannot_finish(self):
        fixture, _ = self.fixture()
        assignment, path, session = self.start(fixture)
        tree = acceptance.snapshot_tree(Path(assignment["worktree"]))
        with self.assertRaises((WorkflowError, OSError)):
            acceptance.verify_task(assignment, 1, tree)
        acceptance.produce(path, session)
        (Path(assignment["worktree"]) / "a.txt").write_text("changed\n")
        receipt, observed, _ = fixture.result_ingest(assignment)
        with self.assertRaises(execution.ExecutionError):
            fixture.result_finish(assignment, receipt, observed)

    def test_failed_assertion_is_immutable_and_cannot_be_deferred(self):
        fixture, policy = self.fixture(code="raise AssertionError('expected failure')")
        assignment, path, session = self.start(fixture)
        self.assertEqual(acceptance.produce(path, session)["outcome"], "failed")
        with mock.patch.object(acceptance, "_checks") as checks, self.assertRaises(WorkflowError):
            acceptance.produce(path, session)
        checks.assert_not_called()
        policy["tasks"]["TASK-001"]["criteria"] = [criterion(disposition="deferred")]
        with self.assertRaises(WorkflowError):
            acceptance.validate_replan(fixture.run_dir, "FEAT-001", policy)

    def test_interrupted_execution_fences_existing_quiescence_owner(self):
        from workflow_validation import require_quiescent
        fixture, _ = self.fixture()
        _, path, session = self.start(fixture)
        from workflow_process import ProcessCleanupError
        with mock.patch.object(acceptance, "_checks", side_effect=ProcessCleanupError("unknown exit")):
            with self.assertRaises(ProcessCleanupError):
                acceptance.produce(path, session)
        with self.assertRaises(WorkflowError):
            require_quiescent(fixture.run_dir)

    def test_interrupted_attempt_cannot_repeat_without_worker_recovery(self):
        fixture, _ = self.fixture()
        _, path, session = self.start(fixture)
        error = WorkflowError("FAILED", "missing runner receipt")
        error.observation = {"local_group_quiescent": True}
        with mock.patch.object(acceptance, "_checks", side_effect=error):
            with self.assertRaises(WorkflowError):
                acceptance.produce(path, session)
        acceptance.require_quiescent(fixture.run_dir)
        with mock.patch.object(acceptance, "_checks") as checks, self.assertRaises(WorkflowError):
            acceptance.produce(path, session)
        checks.assert_not_called()

    def test_publication_failure_does_not_strand_process_ownership(self):
        fixture, _ = self.fixture()
        _, path, session = self.start(fixture)
        with mock.patch.object(acceptance, "_publish", side_effect=OSError("publication unavailable")):
            with self.assertRaises(OSError):
                acceptance.produce(path, session)
        acceptance.require_quiescent(fixture.run_dir)
        with mock.patch.object(acceptance, "_checks") as checks, self.assertRaises(WorkflowError):
            acceptance.produce(path, session)
        checks.assert_not_called()

    def test_prelaunch_reservation_failure_is_quiescent_and_cannot_rerun(self):
        fixture, _ = self.fixture()
        _, path, session = self.start(fixture)
        original_write = acceptance.write

        def interrupted_write(destination, value, **options):
            if destination.parent.name == "tasks":
                raise OSError("reservation unavailable")
            return original_write(destination, value, **options)

        with mock.patch.object(acceptance, "write", side_effect=interrupted_write), mock.patch.object(acceptance, "_checks") as checks:
            with self.assertRaises(OSError):
                acceptance.produce(path, session)
        checks.assert_not_called()
        acceptance.require_quiescent(fixture.run_dir)
        with mock.patch.object(acceptance, "_checks") as checks, self.assertRaises(WorkflowError):
            acceptance.produce(path, session)
        checks.assert_not_called()

    def test_partial_deferred_reservation_retains_failed_trial_for_every_criterion(self):
        fixture, policy = self.fixture(mode="deferred", code="pass")
        policy["tasks"]["TASK-002"]["criteria"] = [criterion("future", "deferred", "pass")]
        write(acceptance.policy_path(fixture.plan), policy)
        fixture.prepare()
        original_write = acceptance.write
        reservations = []

        def interrupted_write(destination, value, **options):
            if destination.parent.name == "resolved":
                reservations.append(destination)
                if len(reservations) == 2:
                    raise OSError("second reservation unavailable")
            return original_write(destination, value, **options)

        with mock.patch.object(acceptance, "write", side_effect=interrupted_write), mock.patch.object(acceptance, "_checks") as checks:
            with self.assertRaises(OSError):
                acceptance.resolve(fixture.run_dir, fixture.project, fixture.session)
        checks.assert_not_called()
        acceptance.require_quiescent(fixture.run_dir)
        for name in ["content", "future"]:
            with mock.patch.object(acceptance, "_checks") as checks, self.assertRaises(WorkflowError):
                acceptance.resolve(fixture.run_dir, fixture.project, fixture.session, ["FEAT-001:" + name])
            checks.assert_not_called()

    def test_verified_receipt_repairs_index_without_reexecuting(self):
        fixture, _ = self.fixture()
        _, path, session = self.start(fixture)
        original_write = acceptance.write

        def interrupted_write(destination, value, **options):
            if destination.parent.name == "tasks" and "path" in value:
                raise OSError("index write interrupted")
            return original_write(destination, value, **options)

        with mock.patch.object(acceptance, "write", side_effect=interrupted_write):
            with self.assertRaises(OSError):
                acceptance.produce(path, session)
        acceptance.require_quiescent(fixture.run_dir)
        with mock.patch.object(acceptance, "_checks") as checks:
            self.assertEqual(acceptance.produce(path, session)["outcome"], "passed")
        checks.assert_not_called()

    def test_deferred_subset_does_not_run_future_criteria_and_replays_without_effects(self):
        fixture, policy = self.fixture(mode="deferred", code="pass")
        policy["tasks"]["TASK-002"]["criteria"] = [criterion("future", "deferred", "raise AssertionError('not ready')")]
        write(acceptance.policy_path(fixture.plan), policy)
        fixture.prepare()
        result = acceptance.resolve(fixture.run_dir, fixture.project, fixture.session, ["FEAT-001:content"])
        self.assertEqual(result["outcome"], "passed")
        with mock.patch.object(acceptance, "_checks") as checks:
            self.assertEqual(acceptance.resolve(fixture.run_dir, fixture.project, fixture.session,
                                               ["FEAT-001:content"])["status"], "already-resolved")
        checks.assert_not_called()
        with self.assertRaises((WorkflowError, OSError)):
            acceptance.require_final(fixture.run_dir, fixture.project, "HEAD")

    def test_subset_then_full_resolution_runs_only_remaining_checks(self):
        fixture, policy = self.fixture(mode="deferred", code="pass")
        policy["tasks"]["TASK-002"]["criteria"] = [criterion("future", "deferred", "pass")]
        write(acceptance.policy_path(fixture.plan), policy)
        fixture.prepare()
        acceptance.resolve(fixture.run_dir, fixture.project, fixture.session, ["FEAT-001:content"])
        with mock.patch.object(acceptance, "_checks", wraps=acceptance._checks) as checks:
            self.assertEqual(acceptance.resolve(fixture.run_dir, fixture.project, fixture.session)["outcome"], "passed")
        self.assertEqual([c["id"] for c in checks.call_args.args[0]], ["future"])
        acceptance.require_final(fixture.run_dir, fixture.project, "HEAD")

    def test_historical_final_snapshot_does_not_acquire_future_debt(self):
        fixture, _ = self.fixture(mode="deferred", code="pass")
        fixture.prepare()
        acceptance.resolve(fixture.run_dir, fixture.project, fixture.session)
        snapshot = acceptance.final_snapshot(fixture.run_dir, fixture.project, "HEAD")
        debt = acceptance.obligations(fixture.run_dir)
        with mock.patch.object(acceptance, "obligations", return_value={**debt, "FEAT-002:future": criterion("future", "deferred", "pass")}):
            acceptance.verify_final_snapshot(fixture.run_dir, fixture.project, "HEAD", snapshot, current=False)
            with self.assertRaises(WorkflowError):
                acceptance.verify_final_snapshot(fixture.run_dir, fixture.project, "HEAD", snapshot, current=True)

    def test_missing_or_changed_basis_is_not_zero_debt(self):
        fixture, _ = self.fixture(mode="not_applicable")
        policy = read(acceptance.policy_path(fixture.plan))
        policy["tasks"]["TASK-001"]["criteria"][0]["check"] = None
        write(acceptance.policy_path(fixture.plan), policy)
        fixture.prepare()
        pointer = read(fixture.run_dir / "objective-impact/execution/FEAT-001.json")
        path = fixture.run_dir / pointer["path"]
        basis = read(path)
        write(path, {**basis, "owner": "implement"})
        with self.assertRaises(WorkflowError):
            acceptance.require_final(fixture.run_dir, fixture.project, "HEAD")
        path.unlink()
        with self.assertRaises(WorkflowError):
            acceptance.require_final(fixture.run_dir, fixture.project, "HEAD")

    def test_missing_undispatched_replanned_basis_cannot_erase_deferred_debt(self):
        fixture, policy = self.fixture(mode="deferred", code="pass")
        policy["tasks"]["TASK-003"]["criteria"] = policy["tasks"]["TASK-001"]["criteria"]
        omitted = criterion("not-applicable", "not_applicable")
        omitted["check"] = None
        policy["tasks"]["TASK-001"]["criteria"] = [omitted]
        write(acceptance.policy_path(fixture.plan), policy)
        fixture.prepare()
        pointer = read(fixture.run_dir / "objective-impact/execution/FEAT-001.json")
        old_basis = fixture.run_dir / pointer["path"]
        replacement = fixture.plan.with_name("FEAT-001.plan.v2.md")
        replacement.write_text(fixture.plan.read_text().split("### TASK-003")[0].replace("Plan v1", "Plan v2"))
        replacement.with_suffix(replacement.suffix + ".lock").write_text("locked\n")
        policy["tasks"].pop("TASK-003")
        write(acceptance.policy_path(replacement), policy)
        fixtures.fixture_replan_future(fixture.run_dir, "FEAT-001", replacement, 2, session=fixture.session)
        self.assertEqual(set(acceptance.obligations(fixture.run_dir)), {"FEAT-001:content"})
        old_basis.unlink()
        with self.assertRaises(WorkflowError):
            acceptance.require_final(fixture.run_dir, fixture.project, "HEAD")

    def test_same_plan_refresh_cannot_change_acceptance_policy(self):
        fixture, policy = self.fixture(mode="deferred", code="pass")
        fixture.prepare()
        fixture.prepare()
        pointer = fixture.run_dir / "objective-impact/execution/FEAT-001.json"
        original = pointer.read_bytes()
        bases = set((fixture.run_dir / "bases/FEAT-001").glob("*.json"))
        omitted = criterion("content", "not_applicable")
        omitted["check"] = None
        policy["tasks"]["TASK-001"]["criteria"] = [omitted]
        write(acceptance.policy_path(fixture.plan), policy)
        with self.assertRaises(execution.ExecutionError):
            fixtures.fixture_replan_future(fixture.run_dir, "FEAT-001", fixture.plan, 2, session=fixture.session)
        with self.assertRaises(execution.ExecutionError):
            fixture.prepare()
        self.assertEqual(pointer.read_bytes(), original)
        self.assertEqual(set((fixture.run_dir / "bases/FEAT-001").glob("*.json")), bases)

    def test_history_requires_valid_journal_but_not_uncompleted_future_basis(self):
        fixture, policy = self.fixture(mode="not_applicable")
        policy["tasks"]["TASK-001"]["criteria"][0]["check"] = None
        write(acceptance.policy_path(fixture.plan), policy)
        coordinator = fixture.prepare()
        journal = execution.journal_path(fixture.run_dir, "FEAT-001", "coordinator")
        execution.append_journal(journal, {"event": "future_replan_intent",
            "old_digest": coordinator["plan_digest"], "new_digest": "a" * 64})
        acceptance.require_final(fixture.run_dir, fixture.project, "HEAD")
        journal.write_text("{malformed\n")
        with self.assertRaises(WorkflowError):
            acceptance.require_final(fixture.run_dir, fixture.project, "HEAD")
        journal.unlink()
        with self.assertRaises(WorkflowError):
            acceptance.require_final(fixture.run_dir, fixture.project, "HEAD")

    def test_pending_consumer_does_not_prevent_independent_dispatch(self):
        fixture, policy = self.fixture(mode="deferred", code="pass")
        policy["tasks"]["TASK-001"]["requires"] = ["content"]
        write(acceptance.policy_path(fixture.plan), policy)
        fixture.prepare()
        assignments = execution.prepare_wave(fixture.run_dir, "FEAT-001", "WAVE-001")
        self.assertTrue(assignments)
        self.assertNotIn("TASK-001", [a["task_id"] for a in assignments])
        self.assertIn("TASK-001", read(execution.wave_path(fixture.run_dir, "FEAT-001", "WAVE-001"))["ready_task_ids"])

    def environment_retry_fixture(self):
        fixture, policy = self.fixture(mode="deferred")
        marker = fixture.root / "environment-ready"
        policy["tasks"]["TASK-001"]["criteria"][0]["check"]["definition"]["argv"][-1] = (
            "from pathlib import Path; assert Path(" + repr(str(marker)) + ").exists()")
        write(acceptance.policy_path(fixture.plan), policy)
        fixture.prepare()
        first = acceptance.resolve(fixture.run_dir, fixture.project, fixture.session)
        self.assertEqual(first["outcome"], "failed")
        failed = read(Path(first["receipt_path"]))
        failure_sha = acceptance.digest(failed)
        owner = acceptance._repair_owner()
        import test_repair_control as repair_fixtures
        payload = repair_fixtures.failure_payload(proposed="ENVIRONMENT_DEFECT", lifecycle="promoted",
                                                  commit=acceptance._git(fixture.project, ["rev-parse", "HEAD"]))
        payload.update(phase="sdlc-uat-tests", evidence_digests={"task_acceptance": "sha256:" + failure_sha})
        event = owner.record_failure_event(fixture.run_dir, payload)["event"]
        classified = owner.classify_failure(fixture.run_dir, "FEAT-001", event["event_id"])["classification"]
        self.assertEqual(classified["classification"], "ENVIRONMENT_DEFECT")
        self.assertEqual(classified["status"], "routed")
        dispatch = owner.begin_remediation(fixture.run_dir, "FEAT-001", classified["classification_id"],
                                          "localized", "Restore the missing fixture prerequisite", first["receipt_path"])["attempt"]
        with self.assertRaises(owner.RepairControlError):
            owner.acceptance_retry_authority(fixture.run_dir, "FEAT-001", dispatch["dispatch_id"], "0" * 64)
        marker.write_text("ready\n")
        dispatch_path = fixture.run_dir / "repairs/FEAT-001/dispatches" / (dispatch["dispatch_id"] + ".json")
        return fixture, owner, first, failed, dispatch_path

    def test_environment_retry_consumes_real_classified_budget_and_preserves_failure(self):
        fixture, owner, first, failed, dispatch_path = self.environment_retry_fixture()
        original_write = acceptance.write

        def interrupted_write(destination, value, **options):
            if destination.parent.name == "resolved" and "path" in value:
                raise OSError("resolution index interrupted after remediation completion")
            return original_write(destination, value, **options)

        with mock.patch.object(acceptance, "write", side_effect=interrupted_write):
            with self.assertRaises(OSError):
                acceptance.resolve(fixture.run_dir, fixture.project, fixture.session, repair_dispatch=dispatch_path)
        with mock.patch.object(acceptance, "_checks") as checks:
            retried = acceptance.resolve(fixture.run_dir, fixture.project, fixture.session)
        self.assertEqual(retried["outcome"], "passed")
        checks.assert_not_called()
        self.assertEqual(read(Path(first["receipt_path"])), failed)
        control = owner._load_control(fixture.run_dir, "FEAT-001")
        self.assertEqual(control["feature_dispatches"], 1)
        self.assertEqual(control["status"], "resolved")
        acceptance.require_final(fixture.run_dir, fixture.project, "HEAD")

    def test_retry_publication_failure_completes_counted_remediation(self):
        fixture, owner, first, failed, dispatch_path = self.environment_retry_fixture()
        with mock.patch.object(acceptance, "_publish", side_effect=OSError("publication unavailable")):
            with self.assertRaises(OSError):
                acceptance.resolve(fixture.run_dir, fixture.project, fixture.session, repair_dispatch=dispatch_path)
        acceptance.require_quiescent(fixture.run_dir)
        control = owner._load_control(fixture.run_dir, "FEAT-001")
        self.assertEqual(control["feature_dispatches"], 1)
        self.assertNotEqual(control["status"], "remediating")
        self.assertEqual(control["active_blocker"]["attempts"][-1]["result"], "failed_same_blocker")
        self.assertGreater(control["active_blocker"]["active_seconds"], 0)
        self.assertEqual(read(Path(first["receipt_path"])), failed)

    def test_snapshot_preserves_index_modes_and_export_ignored_bytes(self):
        fixture, _ = self.fixture()
        from test_workflow_contracts import git
        root = fixture.project
        before = git(root, "write-tree")
        (root / "hidden.txt").write_text("must be tested\n")
        (root / ".gitattributes").write_text("hidden.txt export-ignore\n")
        (root / "executable").write_text("test\n")
        (root / "executable").chmod(0o755)
        tree = acceptance.snapshot_tree(root)
        self.assertEqual(git(root, "write-tree"), before)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            container._source(root, tree, target)
            self.assertEqual((target / "hidden.txt").read_text(), "must be tested\n")
            self.assertTrue((target / "executable").stat().st_mode & 0o100)

    def test_container_launch_uses_declared_host_owner_and_preserves_failed_tests(self):
        import json
        import os
        cid = "a" * 64
        client = ["/usr/local/bin/docker", "--config", "/private/docker", "--context", "local"]
        check = {"collect_argv": ["python"], "run_argv": ["python"], "platform": "linux/amd64", "uid": 10001,
                 "image": "sha256:" + "b" * 64, "suite_sha256": "c" * 64, "execution_policy": {"observation_interval_seconds": 30, "deadline": None}}
        original_umask = os.umask(0o077)
        self.addCleanup(os.umask, original_umask)
        for started, code in ((True, 0), (True, 1), (False, 75)):
            with self.subTest(started=started, exit_code=code), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary)
                removed = False

                def docker(*args, **options):
                    nonlocal removed
                    self.assertEqual(options.get("client"), client)
                    if args[0] == "create":
                        return cid
                    if args[0] == "inspect":
                        return json.dumps([{"Id": cid, "Image": check["image"], "Mounts": [],
                            "Config": {"Labels": {"workflow.acceptance": "owned"}},
                            "State": {"Running": False, "Status": "exited" if started else "created",
                                      "ExitCode": code if started else 0}}])
                    if args[0] == "cp":
                        if args[2].endswith(":/evidence/inventory.json"):
                            self.assertEqual(Path(args[1]).stat().st_mode & 0o777, 0o644)
                        return ""
                    if args[0] == "ps":
                        return "" if removed else cid + "\n"
                    if args[0] == "rm":
                        self.assertEqual(args, ("rm", "--force", cid))
                        removed = True
                        return cid
                    self.fail("unexpected Docker action")

                def launch(definition, cwd):
                    self.assertEqual(definition["argv"], [*client, "start", "--attach", cid])
                    self.assertEqual(definition["execution_policy"], check["execution_policy"])
                    self.assertEqual(cwd, directory)
                    return {"outcome": "passed" if code == 0 else "failed", "exit_code": code}

                result = {"failed": int(code != 0)}
                with mock.patch.object(container, "_docker", side_effect=docker), \
                        mock.patch.object(container, "_run_check", side_effect=launch), \
                        mock.patch.object(container, "_receipt", return_value=result) as receipt:
                    if started:
                        observed, proof = container._phase(
                            check, directory, ".", directory, "owned", "run", "d" * 64,
                            inventory={"cases": []}, client=client)
                        self.assertEqual(observed, result)
                        self.assertEqual(proof["exit_code"], code)
                        self.assertEqual(proof["process"]["exit_code"], code)
                        receipt.assert_called_once_with(cid, client=client)
                    else:
                        with self.assertRaisesRegex(WorkflowError, "container launch did not complete.*75"):
                            container._phase(
                                check, directory, ".", directory, "owned", "run", "d" * 64,
                                inventory={"cases": []}, client=client)
                        receipt.assert_not_called()
                self.assertTrue(removed, "the exact owned container must be removed on every outcome")

    def test_snapshot_directories_are_traversable_under_private_umask(self):
        import os
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary)
            original_umask = os.umask(0o077)
            try:
                source = private / "source"
                source.mkdir()
                entries = [("project-a/acceptance_fixture/check.py", "100644", b"pass\n")]
                with mock.patch.object(acceptance, "source_entries", return_value=entries):
                    container._source(private, "test-tree", source)
            finally:
                os.umask(original_umask)
            self.assertEqual(private.stat().st_mode & 0o777, 0o700)
            for directory in (source, source / "project-a", source / "project-a/acceptance_fixture"):
                self.assertEqual(directory.stat().st_mode & 0o777, 0o755)
            self.assertEqual((source / entries[0][0]).read_bytes(), b"pass\n")

    def test_lost_create_ack_and_empty_inventory_remain_unknown(self):
        from workflow_process import ProcessCleanupError
        check = {"collect_argv": ["python"], "platform": "linux/amd64", "uid": 10001,
                 "image": "sha256:" + "a" * 64, "suite_sha256": "b" * 64}
        def docker(*args, **kwargs):
            if args[0] == "create":
                raise WorkflowError("BLOCKED", "lost acknowledgement")
            if args[0] == "ps":
                return ""
            self.fail("ambiguous creation must not be retried or cleaned by a broad selector")
        with mock.patch.object(container, "_docker", side_effect=docker):
            with self.assertRaises(ProcessCleanupError) as raised:
                container._phase(check, Path("/unused"), ".", Path("/unused"), "owned", "collect", "c" * 64)
        self.assertFalse(raised.exception.observation["cleanup_proven"])

    def test_daemon_failure_is_not_cleanup_proof(self):
        from workflow_process import ProcessCleanupError
        check = {"collect_argv": ["python"], "platform": "linux/amd64", "uid": 10001,
                 "image": "sha256:" + "a" * 64, "suite_sha256": "b" * 64}
        with mock.patch.object(container, "_docker", side_effect=WorkflowError("BLOCKED", "daemon unavailable")):
            with self.assertRaises(ProcessCleanupError):
                container._phase(check, Path("/unused"), ".", Path("/unused"), "owned", "collect", "c" * 64)

    def test_oversized_container_evidence_is_bounded_before_host_copy(self):
        import json
        import sys
        cid = "a" * 64
        removed = False

        def docker(*args, **options):
            nonlocal removed
            if args[0] == "create":
                return cid
            if args[0] == "inspect":
                return json.dumps([{"Id": cid, "Image": "sha256:" + "b" * 64, "Mounts": [],
                    "Config": {"Labels": {"workflow.acceptance": "owned"}},
                    "State": {"Running": False, "Status": "exited", "ExitCode": 0}}])
            if args[0] == "cp":
                if args[1].startswith(cid + ":"):
                    Path(args[2]).write_bytes(b"x" * (1048576 + 1))
                return ""
            if args[0] == "ps":
                return "" if removed else cid + "\n"
            if args[0] == "rm":
                removed = True
                return cid
            self.fail("unexpected Docker action")

        output = getattr(container, "_docker_output", None)
        def oversized(*args, **options):
            return output(client=[sys.executable, "-I", "-c",
                "import os; os.write(1, b'x' * (2 * 1048576))"], limit=options["limit"])

        check = {"collect_argv": ["python"], "platform": "linux/amd64", "uid": 10001,
                 "image": "sha256:" + "b" * 64, "suite_sha256": "c" * 64, "execution_policy": {"observation_interval_seconds": 30, "deadline": None}}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with mock.patch.object(container, "_docker", side_effect=docker), \
                    mock.patch.object(container, "_docker_output", side_effect=oversized, create=True) as stream, \
                    mock.patch.object(container, "_run_check", return_value={"outcome": "passed"}):
                with self.assertRaises(WorkflowError):
                    container._phase(check, directory, ".", directory, "owned", "collect", "d" * 64)
            self.assertTrue(removed)
            self.assertFalse((directory / "collect.json").exists(), "oversized evidence reached host storage")
            stream.assert_called_once()

    def test_container_receipt_archive_rejects_nonregular_or_oversized_members(self):
        import io
        import tarfile
        def archive(name="result.json", kind=tarfile.REGTYPE, content=b'{"ok": true}'):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as output:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = "/outside" if kind != tarfile.REGTYPE else ""
                member.size = len(content) if kind == tarfile.REGTYPE else 0
                output.addfile(member, io.BytesIO(content) if kind == tarfile.REGTYPE else None)
            return stream.getvalue()

        with mock.patch.object(container, "_docker_output", return_value=archive()):
            self.assertEqual(container._receipt("a" * 64), {"ok": True})
        for raw in (archive("../result.json"), archive(kind=tarfile.SYMTYPE),
                    archive(kind=tarfile.LNKTYPE), archive(content=b"x" * (1048576 + 1))):
            with self.subTest(archive_size=len(raw)), mock.patch.object(container, "_docker_output", return_value=raw):
                with self.assertRaises(WorkflowError):
                    container._receipt("a" * 64)

    def test_docker_client_cleanup_uncertainty_fences_every_exit(self):
        from workflow_process import ProcessCleanupError
        with mock.patch.object(container, "run_owned", side_effect=ProcessCleanupError("unknown")):
            with self.assertRaises(ProcessCleanupError):
                container._docker_output("version")

    def test_cleanup_uses_known_id_even_after_container_is_renamed(self):
        import json
        cid = "a" * 64
        inspected = False
        removed = False

        def docker(*args, **options):
            nonlocal inspected, removed
            if args[0] == "create":
                return cid
            if args[0] == "inspect":
                if not inspected:
                    inspected = True
                    raise WorkflowError("FAILED", "source inspection failed")
                return json.dumps([{"Id": cid, "Config": {"Labels": {"workflow.acceptance": "owned"}}}])
            if args[0] == "ps":
                self.assertIn("id=" + cid, args)
                return "" if removed else cid + "\n"
            if args[0] == "rm":
                self.assertEqual(args, ("rm", "--force", cid))
                removed = True
                return cid
            self.fail("unexpected Docker action")

        check = {"collect_argv": ["python"], "platform": "linux/amd64", "uid": 10001,
                 "image": "sha256:" + "b" * 64, "suite_sha256": "c" * 64}
        with mock.patch.object(container, "_docker", side_effect=docker), self.assertRaises(WorkflowError):
            container._phase(check, Path("/unused"), ".", Path("/unused"), "owned", "collect", "d" * 64)
        self.assertTrue(removed)


if __name__ == "__main__":
    unittest.main()
