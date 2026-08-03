#!/usr/bin/env python3
"""Deterministic tests for Agentic SDLC failure and repair control."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import multiprocessing
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("repair_control.py")
SPEC = importlib.util.spec_from_file_location("sdlc_repair_control", MODULE_PATH)
assert SPEC and SPEC.loader
repair_control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair_control)


def digest(character: str) -> str:
    return f"sha256:{character * 64}"


def failure_payload(
    *,
    proposed: str | None = "IMPLEMENTATION_DEFECT",
    cause_status: str = "proven",
    commit: str = "a" * 40,
    created_at: str = "2026-07-28T10:00:00Z",
    lifecycle: str = "waves_completed",
    component: str = "api",
    operation: str = "evaluate response",
    error_class: str = "assertion mismatch",
    source_boundary: str = "service boundary",
    independent: str | None = None,
) -> dict[str, object]:
    return {
        "schema": repair_control.FAILURE_EVENT_SCHEMA,
        "feature_id": "FEAT-001",
        "phase": "evaluation",
        "criterion_id": "AC-001",
        "expected": "The response contains the accepted value.",
        "observed": "The response contains the rejected value.",
        "evidence_digests": {"evaluate": digest("1")},
        "reproduction": "Run the bounded acceptance oracle for AC-001.",
        "integration_commit": commit,
        "fingerprints": {
            "requirements": digest("2"),
            "design": digest("3"),
            "plan": digest("4"),
        },
        "component": component,
        "operation": operation,
        "error_class": error_class,
        "source_boundary": source_boundary,
        "cause_status": cause_status,
        "proposed_classification": proposed,
        "execution_lifecycle": lifecycle,
        "independent_blocker_evidence": independent,
        "created_at": created_at,
    }


def localized_diagnosis(
    event: dict[str, object],
    *,
    created_at: str = "2026-07-28T10:05:00Z",
) -> dict[str, object]:
    return {
        "schema": repair_control.DIAGNOSIS_SCHEMA,
        "feature_id": "FEAT-001",
        "event_id": event["event_id"],
        "blocker_key": event["blocker_key"],
        "result": "localized_implementation_defect",
        "confidence": "proven",
        "expected": event["expected"],
        "observed": event["observed"],
        "earliest_divergence": {
            "component": "response mapper",
            "operation": "map accepted value",
            "source_boundary": "service response",
        },
        "violated_invariant": "The response mapper preserves the accepted value.",
        "causal_chain": [
            "The accepted value enters the response mapper.",
            "The mapper selects the stale field.",
            "Evaluation observes the rejected value.",
        ],
        "bounded_repair_target": "The response mapper field selection only.",
        "counterfactual": "Selecting the accepted field makes AC-001 pass.",
        "alternatives_eliminated": [
            "The evaluator reads the response field correctly.",
            "The fixture contains the accepted input.",
        ],
        "affected_files": ["src/response_mapper.py"],
        "regression_oracle": "Run the AC-001 acceptance oracle.",
        "required_regression_test": "Add a response-mapper regression for AC-001.",
        "evidence_references": [
            "evidence/FEAT-001/evaluate.md#AC-001",
            "evidence/FEAT-001/diagnosis.md#earliest-divergence",
        ],
        "constraints": [
            "Preserve the public response schema.",
            "Do not weaken AC-001.",
        ],
        "created_at": created_at,
    }


def design_gate(*, broader: bool = False) -> dict[str, object]:
    return {
        "requirements_stable": True,
        "evaluator_valid": True,
        "environment_valid": True,
        "reproducible": True,
        "system_contract_changes": ["component_responsibility"],
        "violated_design_contract": "The mapper cannot own canonical value selection.",
        "localized_repair_insufficient_evidence": [
            "Both existing owners must mutate the same canonical value."
        ],
        "affected_features": ["FEAT-001"],
        "invalidation_scope": ["FEAT-001 design and downstream evidence"],
        "estimated_work": "One corrective design and plan revision.",
        "rollback_path": "Revert to the recorded design fingerprint.",
        "external_change_flags": {
            "requirements": False,
            "public_contracts": broader,
            "data_lifecycle": False,
            "security": False,
            "permissions": False,
            "deployment_scope": False,
            "external_behavior": False,
        },
        "human_approval_id": "approval-001" if broader else None,
    }


def design_diagnosis(event: dict[str, object]) -> dict[str, object]:
    value = localized_diagnosis(event)
    value.update(
        {
            "result": "design_defect",
            "bounded_repair_target": None,
            "affected_files": [],
            "required_regression_test": None,
            "design_gate": design_gate(),
        }
    )
    return value


def unresolved_diagnosis(event: dict[str, object]) -> dict[str, object]:
    return {
        "schema": repair_control.DIAGNOSIS_SCHEMA,
        "feature_id": "FEAT-001",
        "event_id": event["event_id"],
        "blocker_key": event["blocker_key"],
        "result": "unresolved",
        "confidence": "unknown",
        "created_at": "2026-07-28T10:06:00Z",
    }


def approval_payload(
    event: dict[str, object], gate: dict[str, object]
) -> dict[str, object]:
    draft = dict(gate)
    draft["human_approval_id"] = None
    statement = "I approve this broader public-contract design change."
    return {
        "schema": repair_control.DESIGN_APPROVAL_SCHEMA,
        "feature_id": "FEAT-001",
        "event_id": event["event_id"],
        "decision": "approved",
        "design_gate": draft,
        "approver": "authorized-reviewer",
        "approval_evidence_reference": "inputs/r0002/prompt.md",
        "approval_evidence_digest": hashlib.sha256(
            statement.encode("utf-8")
        ).hexdigest(),
        "approval_statement": statement,
        "approved_at": "2026-07-28T10:04:00Z",
    }


def concurrent_dispatch(
    run_dir: str,
    classification_id: str,
    barrier: object,
    queue: object,
) -> None:
    barrier.wait()
    try:
        result = repair_control.begin_remediation(
            Path(run_dir),
            "FEAT-001",
            classification_id,
            "localized",
            "The mapper selects the stale field.",
            "evidence/FEAT-001/evaluate.md#AC-001",
        )
        queue.put(("ok", result["created"]))
    except Exception as exc:  # pragma: no cover - child process diagnostic
        queue.put(("error", repr(exc)))


def revalidation_payload(
    run_dir: Path,
    surface: str,
    index: int,
    *,
    commit: str = "b" * 40,
    fingerprints: dict[str, str] | None = None,
    status: str = "passed",
) -> dict[str, object]:
    current_fingerprints = fingerprints or {
        "requirements": digest("2"),
        "design": digest("3"),
        "plan": digest("4"),
    }
    gate = {
        "schema": "agentic-sdlc/gate-evidence-v1",
        "feature_id": "FEAT-001",
        "surface": surface,
        "owner_skill": repair_control.REVALIDATION_ROUTES[surface],
        "status": status,
        "integration_commit": commit,
        "fingerprints": current_fingerprints,
        "evidence": [f"{surface} deterministic check {status}"],
    }
    content = json.dumps(gate, sort_keys=True, indent=2) + "\n"
    path = run_dir / "evidence" / "FEAT-001" / f"{surface}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {
        "schema": repair_control.REVALIDATION_EVIDENCE_SCHEMA,
        "feature_id": "FEAT-001",
        "surface": surface,
        "integration_commit": commit,
        "fingerprints": current_fingerprints,
        "evidence_reference": str(path.relative_to(run_dir)),
        "evidence_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "recorded_at": f"2026-07-28T10:1{index}:00Z",
    }


class RepairControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temporary.name) / "run"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record_event(self, payload: dict[str, object] | None = None) -> dict[str, object]:
        result = repair_control.record_failure_event(
            self.run_dir, payload or failure_payload()
        )
        return result["event"]

    def record_localized(
        self, event: dict[str, object]
    ) -> dict[str, object]:
        return repair_control.record_diagnosis(
            self.run_dir, localized_diagnosis(event)
        )["diagnosis"]

    def classify(
        self,
        event: dict[str, object],
        diagnosis: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return repair_control.classify_failure(
            self.run_dir,
            "FEAT-001",
            str(event["event_id"]),
            str(diagnosis["diagnosis_id"]) if diagnosis else None,
        )["classification"]

    def test_proven_implementation_failure_bypasses_troubleshooting(self) -> None:
        event = self.record_event()
        classification = self.classify(event)
        self.assertEqual(classification["classification"], "IMPLEMENTATION_DEFECT")
        self.assertEqual(classification["next_recommended_skill"], "sdlc-create-plan")
        self.assertEqual(classification["corrective_mode"], "corrective_plan_v_next")
        self.assertNotEqual(classification["next_recommended_skill"], "troubleshoot")

    def test_ambiguous_evaluation_routes_to_troubleshoot_exactly_once(self) -> None:
        event = self.record_event(
            failure_payload(proposed=None, cause_status="ambiguous")
        )
        first = repair_control.classify_failure(
            self.run_dir, "FEAT-001", str(event["event_id"])
        )
        second = repair_control.classify_failure(
            self.run_dir, "FEAT-001", str(event["event_id"])
        )
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(
            first["classification"]["next_recommended_skill"], "troubleshoot"
        )
        self.assertEqual(len(second["control"]["route_history"]), 1)

    def test_timestamp_and_checkpoint_churn_are_duplicate_events(self) -> None:
        first_payload = failure_payload()
        first_payload["checkpoint_id"] = "checkpoint-0001"
        first = repair_control.record_failure_event(self.run_dir, first_payload)
        second_payload = failure_payload(created_at="2026-07-28T10:01:00Z")
        second_payload["checkpoint_id"] = "checkpoint-0999"
        second = repair_control.record_failure_event(self.run_dir, second_payload)
        self.assertEqual(first["event"]["event_id"], second["event"]["event_id"])
        self.assertEqual(first["event"]["blocker_key"], second["event"]["blocker_key"])
        self.assertFalse(second["created"])

    def test_commit_changes_event_id_not_blocker_key(self) -> None:
        first = repair_control.build_failure_event(failure_payload())
        second = repair_control.build_failure_event(
            failure_payload(commit="b" * 40)
        )
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["blocker_key"], second["blocker_key"])

    def test_failed_direct_repair_requires_troubleshooting_before_attempt_two(self) -> None:
        event = self.record_event()
        classification = self.classify(event)
        first = repair_control.begin_remediation(
            self.run_dir,
            "FEAT-001",
            str(classification["classification_id"]),
            "localized",
            "The mapper selects the stale field.",
            "evidence/FEAT-001/evaluate.md#AC-001",
        )
        repair_control.complete_remediation(
            self.run_dir,
            "FEAT-001",
            first["attempt"]["dispatch_id"],
            "failed_same_blocker",
            "The AC-001 oracle still failed.",
        )
        with self.assertRaises(repair_control.RepairControlError) as blocked:
            repair_control.begin_remediation(
                self.run_dir,
                "FEAT-001",
                str(classification["classification_id"]),
                "localized",
                "The serializer chooses the stale alias.",
                "evidence/FEAT-001/second-observation.md",
            )
        self.assertEqual(blocked.exception.code, "TROUBLESHOOT_REQUIRED")

        diagnosis = self.record_localized(event)
        diagnosed_classification = self.classify(event, diagnosis)
        second = repair_control.begin_remediation(
            self.run_dir,
            "FEAT-001",
            str(diagnosed_classification["classification_id"]),
            "localized",
            "The mapper's canonical-field branch is inverted.",
            "evidence/FEAT-001/diagnosis.md#branch-proof",
        )
        self.assertEqual(second["attempt"]["ordinal"], 2)

    def test_localized_diagnosis_has_complete_repair_handoff(self) -> None:
        event = self.record_event(
            failure_payload(proposed=None, cause_status="ambiguous")
        )
        diagnosis = self.record_localized(event)
        classification = self.classify(event, diagnosis)
        self.assertEqual(classification["classification"], "IMPLEMENTATION_DEFECT")
        self.assertEqual(classification["next_recommended_skill"], "sdlc-create-plan")
        for field in (
            "expected",
            "observed",
            "earliest_divergence",
            "violated_invariant",
            "causal_chain",
            "bounded_repair_target",
            "counterfactual",
            "alternatives_eliminated",
            "affected_files",
            "regression_oracle",
            "required_regression_test",
            "evidence_references",
            "constraints",
        ):
            self.assertTrue(diagnosis[field], field)

    def test_no_bug_found_remains_unresolved_never_design(self) -> None:
        event = self.record_event(
            failure_payload(proposed=None, cause_status="ambiguous")
        )
        diagnosis = repair_control.record_diagnosis(
            self.run_dir, unresolved_diagnosis(event)
        )["diagnosis"]
        classification = self.classify(event, diagnosis)
        self.assertEqual(classification["classification"], "UNKNOWN_DEFECT")
        self.assertEqual(classification["status"], "unresolved")
        self.assertIsNone(classification["next_recommended_skill"])

    def test_design_requires_positive_system_contract_proof(self) -> None:
        event = self.record_event(
            failure_payload(proposed=None, cause_status="ambiguous")
        )
        diagnosis = repair_control.record_diagnosis(
            self.run_dir, design_diagnosis(event)
        )["diagnosis"]
        classification = self.classify(event, diagnosis)
        self.assertEqual(classification["classification"], "DESIGN_DEFECT")
        self.assertEqual(classification["next_recommended_skill"], "sdlc-create-design")
        self.assertEqual(
            classification["design_admission"]["approval_mode"],
            "automatic_internal_reconsideration",
        )

        incomplete = design_diagnosis(event)
        incomplete["design_gate"] = {"requirements_stable": True}
        with self.assertRaises(repair_control.RepairControlError) as blocked:
            repair_control.build_diagnosis(incomplete, event)
        self.assertEqual(blocked.exception.code, "DESIGN_ADMISSION_DENIED")

    def test_broader_design_requires_durable_human_approval(self) -> None:
        event = self.record_event(
            failure_payload(proposed=None, cause_status="ambiguous")
        )
        gate = design_gate(broader=True)
        gate["human_approval_id"] = None
        with self.assertRaises(repair_control.RepairControlError) as blocked:
            repair_control._validate_design_gate(gate)
        self.assertEqual(blocked.exception.code, "RECORD_INVALID")

        fabricated = design_diagnosis(event)
        fabricated["design_gate"] = design_gate(broader=True)
        fabricated_diagnosis = repair_control.record_diagnosis(
            self.run_dir, fabricated
        )["diagnosis"]
        with self.assertRaises(repair_control.RepairControlError) as unbound:
            self.classify(event, fabricated_diagnosis)
        self.assertEqual(unbound.exception.code, "STATE_INVALID")

        approval_statement = (
            "I approve this broader public-contract design change."
        )
        approval_input = self.run_dir / "inputs" / "r0002" / "prompt.md"
        approval_input.parent.mkdir(parents=True)
        approval_input.write_text(approval_statement, encoding="utf-8")
        approval = repair_control.record_design_approval(
            self.run_dir, approval_payload(event, design_gate(broader=True))
        )["approval"]
        authorized = design_diagnosis(event)
        authorized_gate = design_gate(broader=True)
        authorized_gate["human_approval_id"] = approval["approval_id"]
        authorized["design_gate"] = authorized_gate
        diagnosis = repair_control.record_diagnosis(
            self.run_dir, authorized
        )["diagnosis"]
        classification = self.classify(event, diagnosis)
        self.assertEqual(
            classification["design_admission"]["approval_mode"], "human"
        )
        approval_input.write_text(
            "I revoke this broader design approval.", encoding="utf-8"
        )
        with self.assertRaises(repair_control.RepairControlError) as tampered:
            self.classify(event, diagnosis)
        self.assertEqual(tampered.exception.code, "STATE_TAMPERED")

    def test_design_gate_requires_every_external_change_flag(self) -> None:
        gate = design_gate()
        del gate["external_change_flags"]["permissions"]
        with self.assertRaises(repair_control.RepairControlError) as blocked:
            repair_control._validate_design_gate(gate)
        self.assertEqual(blocked.exception.code, "DESIGN_ADMISSION_DENIED")

    def test_probable_diagnosis_cannot_authorize_repair_or_design(self) -> None:
        event = self.record_event(
            failure_payload(proposed=None, cause_status="ambiguous")
        )
        diagnosis = localized_diagnosis(event)
        diagnosis["confidence"] = "probable"
        with self.assertRaises(repair_control.RepairControlError) as blocked:
            repair_control.build_diagnosis(diagnosis, event)
        self.assertEqual(blocked.exception.code, "DIAGNOSIS_INCOMPLETE")

    def test_taxonomy_routes_test_spec_evaluator_environment_policy_and_human(self) -> None:
        cases = (
            ("TEST_DEFECT", "sdlc-tdd", "routed"),
            ("SPEC_GAP", "sdlc-create-requirements", "routed"),
            ("EVALUATION_DEFECT", "sdlc-evaluate", "routed"),
            ("ENVIRONMENT_DEFECT", None, "blocked"),
            ("POLICY_BLOCK", None, "blocked"),
            ("HUMAN_INPUT_REQUIRED", None, "blocked"),
            ("WORKTREE_CONFLICT", "sdlc-prepare-execution", "routed"),
            ("REPLAN_REQUIRED", "sdlc-create-plan", "routed"),
            ("UAT_DEFECT", "sdlc-uat-tests", "routed"),
        )
        for index, (classification_name, route, status) in enumerate(cases, start=1):
            with self.subTest(classification=classification_name):
                with tempfile.TemporaryDirectory() as temporary:
                    run_dir = Path(temporary) / "run"
                    event = repair_control.record_failure_event(
                        run_dir,
                        failure_payload(
                            proposed=classification_name,
                            component=f"component-{index}",
                        ),
                    )["event"]
                    classification = repair_control.classify_failure(
                        run_dir, "FEAT-001", str(event["event_id"])
                    )["classification"]
                    self.assertEqual(classification["next_recommended_skill"], route)
                    self.assertEqual(classification["status"], status)

    def test_duplicate_dispatch_and_completion_do_not_double_count(self) -> None:
        event = self.record_event()
        classification = self.classify(event)
        arguments = (
            self.run_dir,
            "FEAT-001",
            str(classification["classification_id"]),
            "localized",
            "The mapper selects the stale field.",
            "evidence/FEAT-001/evaluate.md#AC-001",
        )
        first = repair_control.begin_remediation(*arguments)
        second = repair_control.begin_remediation(*arguments)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(second["control"]["active_blocker"]["total_attempts"], 1)
        completed = repair_control.complete_remediation(
            self.run_dir,
            "FEAT-001",
            first["attempt"]["dispatch_id"],
            "succeeded",
            "The AC-001 oracle passed.",
        )
        repeated = repair_control.complete_remediation(
            self.run_dir,
            "FEAT-001",
            first["attempt"]["dispatch_id"],
            "succeeded",
            "The AC-001 oracle passed.",
        )
        self.assertTrue(completed["updated"])
        self.assertFalse(repeated["updated"])

    def test_concurrent_duplicate_dispatch_is_counted_once(self) -> None:
        event = self.record_event()
        classification = self.classify(event)
        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(4)
        queue = context.Queue()
        processes = [
            context.Process(
                target=concurrent_dispatch,
                args=(
                    str(self.run_dir),
                    str(classification["classification_id"]),
                    barrier,
                    queue,
                ),
            )
            for _ in range(4)
        ]
        for process in processes:
            process.start()
        results = [queue.get(timeout=10) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
        self.assertFalse([result for result in results if result[0] == "error"])
        self.assertEqual(sum(created is True for _, created in results), 1)
        control = repair_control._load_control(self.run_dir, "FEAT-001")
        self.assertEqual(control["active_blocker"]["total_attempts"], 1)
        self.assertEqual(control["feature_dispatches"], 1)

    def test_duplicate_failure_replay_preserves_diagnosis_and_classification(
        self,
    ) -> None:
        payload = failure_payload()
        event = self.record_event(payload)
        diagnosis = self.record_localized(event)
        classification = self.classify(event, diagnosis)
        replay = repair_control.record_failure_event(self.run_dir, payload)
        self.assertFalse(replay["created"])
        self.assertEqual(
            replay["control"]["current_diagnosis_id"],
            diagnosis["diagnosis_id"],
        )
        self.assertEqual(
            replay["control"]["current_classification_id"],
            classification["classification_id"],
        )

    def test_success_requires_ordered_commit_bound_revalidation(self) -> None:
        event = self.record_event()
        classification = self.classify(event)
        dispatched = repair_control.begin_remediation(
            self.run_dir,
            "FEAT-001",
            str(classification["classification_id"]),
            "localized",
            "Repair the stale response mapping.",
            "evidence/diagnosis-proof",
        )
        completed = repair_control.complete_remediation(
            self.run_dir,
            "FEAT-001",
            str(dispatched["attempt"]["dispatch_id"]),
            "succeeded",
            "The original oracle passed in the worker result.",
        )
        control = completed["control"]
        self.assertEqual(control["status"], "revalidation_required")
        self.assertEqual(
            control["revalidation"]["required"][0],
            {
                "surface": "validation",
                "next_recommended_skill": "sdlc-validate-codes",
            },
        )

        integration = (
            self.run_dir / "worktrees" / "FEAT-001" / "integration"
        )
        integration.mkdir(parents=True)
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=integration,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Repair Test"],
            cwd=integration,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "repair@example.com"],
            cwd=integration,
            check=True,
        )
        (integration / "result.txt").write_text("repaired\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=integration, check=True)
        subprocess.run(
            ["git", "commit", "-m", "repair"],
            cwd=integration,
            check=True,
            capture_output=True,
        )
        integration_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=integration,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        coordinator_path = (
            self.run_dir
            / "execution"
            / "FEAT-001"
            / "coordinator.json"
        )
        coordinator_path.parent.mkdir(parents=True)
        coordinator_path.write_text(
            json.dumps(
                {
                    "schema": "agentic-sdlc/execution-coordinator-v5",
                    "integration_worktree": str(integration),
                }
            ),
            encoding="utf-8",
        )
        current_fingerprints = {
            "requirements": digest("2"),
            "design": digest("3"),
            "plan": digest("4"),
        }
        (self.run_dir / "current-state.json").write_text(
            json.dumps(
                {
                    "fingerprint_ids": [
                        f"{name}:{value}"
                        for name, value in current_fingerprints.items()
                    ]
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaises(repair_control.RepairControlError) as stale:
            repair_control.record_revalidation(
                self.run_dir,
                revalidation_payload(
                    self.run_dir,
                    "validation",
                    0,
                    commit="b" * 40,
                    fingerprints=current_fingerprints,
                ),
            )
        self.assertEqual(stale.exception.code, "REVALIDATION_INVALID")
        with self.assertRaises(repair_control.RepairControlError) as failed:
            repair_control.record_revalidation(
                self.run_dir,
                revalidation_payload(
                    self.run_dir,
                    "validation",
                    0,
                    commit=integration_commit,
                    fingerprints=current_fingerprints,
                    status="failed",
                ),
            )
        self.assertEqual(failed.exception.code, "REVALIDATION_INVALID")
        with self.assertRaises(repair_control.RepairControlError) as drifted:
            repair_control.record_revalidation(
                self.run_dir,
                revalidation_payload(
                    self.run_dir,
                    "validation",
                    0,
                    commit=integration_commit,
                    fingerprints={
                        **current_fingerprints,
                        "plan": digest("9"),
                    },
                ),
            )
        self.assertEqual(drifted.exception.code, "REVALIDATION_INVALID")

        with self.assertRaises(repair_control.RepairControlError) as out_of_order:
            repair_control.record_revalidation(
                self.run_dir,
                revalidation_payload(
                    self.run_dir,
                    "evaluation",
                    0,
                    commit=integration_commit,
                    fingerprints=current_fingerprints,
                ),
            )
        self.assertEqual(
            out_of_order.exception.code, "REVALIDATION_OUT_OF_ORDER"
        )

        first_payload = None
        first_revalidation_id = None
        promoted_project = Path(self.temporary.name) / "promoted-project"
        for index, required in enumerate(
            control["revalidation"]["required"], start=1
        ):
            if required["surface"] == "commit":
                subprocess.run(
                    ["git", "clone", str(integration), str(promoted_project)],
                    check=True,
                    capture_output=True,
                )
                shutil.rmtree(integration)
                coordinator_path.write_text(
                    json.dumps(
                        {
                            "schema": "agentic-sdlc/execution-coordinator-v5",
                            "status": "done",
                            "base_branch": "main",
                            "project_root": str(promoted_project),
                            "selected_project_root": str(promoted_project),
                            "integration_branch": "repair-integration",
                            "integration_worktree": str(integration),
                            "integration_head": integration_commit,
                            "promoted_head": integration_commit,
                            "cleanup_retained": [],
                        }
                    ),
                    encoding="utf-8",
                )
            payload = revalidation_payload(
                self.run_dir,
                required["surface"],
                index,
                commit=integration_commit,
                fingerprints=current_fingerprints,
            )
            if required["surface"] == "commit":
                subprocess.run(
                    ["git", "checkout", "--detach", integration_commit],
                    cwd=promoted_project,
                    check=True,
                    capture_output=True,
                )
                with self.assertRaises(
                    repair_control.RepairControlError
                ) as detached:
                    repair_control.record_revalidation(self.run_dir, payload)
                self.assertEqual(detached.exception.code, "REVALIDATION_INVALID")
                subprocess.run(
                    ["git", "switch", "main"],
                    cwd=promoted_project,
                    check=True,
                    capture_output=True,
                )
            if first_payload is None:
                first_payload = payload
            result = repair_control.record_revalidation(self.run_dir, payload)
            if first_revalidation_id is None:
                first_revalidation_id = result["revalidation"]["revalidation_id"]
        self.assertFalse(integration.exists())
        self.assertEqual(
            subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=promoted_project,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            integration_commit,
        )
        self.assertEqual(result["control"]["status"], "resolved")
        self.assertEqual(result["control"]["revalidation"]["status"], "complete")
        assert first_payload is not None
        assert first_revalidation_id is not None
        journal_path = (
            self.run_dir / "repairs" / "FEAT-001" / "repair-journal.jsonl"
        )
        lines = [
            line
            for line in journal_path.read_text(encoding="utf-8").splitlines()
            if not (
                '"event":"revalidation-recorded"' in line
                and first_revalidation_id in line
            )
        ]
        journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        replay = repair_control.record_revalidation(self.run_dir, first_payload)
        self.assertFalse(replay["created"])
        self.assertIn(
            first_revalidation_id,
            journal_path.read_text(encoding="utf-8"),
        )

    def test_interrupted_transition_replay_restores_journal_without_recount(
        self,
    ) -> None:
        event = self.record_event()
        classification = self.classify(event)
        arguments = (
            self.run_dir,
            "FEAT-001",
            str(classification["classification_id"]),
            "localized",
            "The mapper selects the stale field.",
            "evidence/FEAT-001/evaluate.md#AC-001",
        )
        first = repair_control.begin_remediation(*arguments)
        journal_path = (
            self.run_dir / "repairs" / "FEAT-001" / "repair-journal.jsonl"
        )
        lines = [
            line
            for line in journal_path.read_text(encoding="utf-8").splitlines()
            if '"event":"remediation-dispatch-committed"' not in line
        ]
        journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        replay = repair_control.begin_remediation(*arguments)
        self.assertFalse(replay["created"])
        self.assertEqual(replay["control"]["active_blocker"]["total_attempts"], 1)
        self.assertIn(
            '"event":"remediation-dispatch-committed"',
            journal_path.read_text(encoding="utf-8"),
        )

        repair_control.complete_remediation(
            self.run_dir,
            "FEAT-001",
            first["attempt"]["dispatch_id"],
            "succeeded",
            "The AC-001 oracle passed.",
        )
        lines = [
            line
            for line in journal_path.read_text(encoding="utf-8").splitlines()
            if '"event":"remediation-completion-committed"' not in line
        ]
        journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        repeated = repair_control.complete_remediation(
            self.run_dir,
            "FEAT-001",
            first["attempt"]["dispatch_id"],
            "succeeded",
            "The AC-001 oracle passed.",
        )
        self.assertFalse(repeated["updated"])
        self.assertIn(
            '"event":"remediation-completion-committed"',
            journal_path.read_text(encoding="utf-8"),
        )

    def test_failure_and_diagnosis_crash_replay_restore_journal(self) -> None:
        payload = failure_payload()
        event = repair_control.build_failure_event(payload)
        repair_control._store_immutable(
            repair_control._event_path(
                self.run_dir, "FEAT-001", str(event["event_id"])
            ),
            event,
            "failure event",
        )
        failure_replay = repair_control.record_failure_event(self.run_dir, payload)
        self.assertFalse(failure_replay["created"])
        journal_path = (
            self.run_dir / "repairs" / "FEAT-001" / "repair-journal.jsonl"
        )
        self.assertIn(
            '"event":"failure-recorded"',
            journal_path.read_text(encoding="utf-8"),
        )

        diagnosis_payload = localized_diagnosis(event)
        diagnosis = repair_control.build_diagnosis(diagnosis_payload, event)
        repair_control._store_immutable(
            repair_control._diagnosis_path(
                self.run_dir, "FEAT-001", str(diagnosis["diagnosis_id"])
            ),
            diagnosis,
            "diagnosis",
        )
        diagnosis_replay = repair_control.record_diagnosis(
            self.run_dir, diagnosis_payload
        )
        self.assertFalse(diagnosis_replay["created"])
        self.assertIn(
            '"event":"diagnosis-recorded"',
            journal_path.read_text(encoding="utf-8"),
        )

    def test_two_localized_and_one_design_attempt_are_hard_ceilings(self) -> None:
        event = self.record_event()
        direct = self.classify(event)
        first = repair_control.begin_remediation(
            self.run_dir,
            "FEAT-001",
            str(direct["classification_id"]),
            "localized",
            "First localized hypothesis.",
            "evidence/first",
        )
        repair_control.complete_remediation(
            self.run_dir,
            "FEAT-001",
            first["attempt"]["dispatch_id"],
            "failed_same_blocker",
            "First oracle failure.",
        )
        localized = self.record_localized(event)
        second_classification = self.classify(event, localized)
        second = repair_control.begin_remediation(
            self.run_dir,
            "FEAT-001",
            str(second_classification["classification_id"]),
            "localized",
            "Second localized hypothesis.",
            "evidence/second",
        )
        repair_control.complete_remediation(
            self.run_dir,
            "FEAT-001",
            second["attempt"]["dispatch_id"],
            "failed_same_blocker",
            "Second oracle failure.",
        )
        another_localized = localized_diagnosis(
            event, created_at="2026-07-28T10:07:00Z"
        )
        another_localized["causal_chain"] = [
            "A new observation enters the mapper.",
            "A different branch selects the stale field.",
            "Evaluation still fails.",
        ]
        another_localized["evidence_references"] = [
            "evidence/FEAT-001/third-diagnosis.md"
        ]
        third_diagnosis = repair_control.record_diagnosis(
            self.run_dir, another_localized
        )["diagnosis"]
        third_classification = self.classify(event, third_diagnosis)
        with self.assertRaises(repair_control.RepairControlError) as localized_block:
            repair_control.begin_remediation(
                self.run_dir,
                "FEAT-001",
                str(third_classification["classification_id"]),
                "localized",
                "Third localized hypothesis.",
                "evidence/third",
            )
        self.assertEqual(
            localized_block.exception.code, "REPAIR_BUDGET_EXHAUSTED"
        )

        design = repair_control.record_diagnosis(
            self.run_dir, design_diagnosis(event)
        )["diagnosis"]
        design_classification = self.classify(event, design)
        third = repair_control.begin_remediation(
            self.run_dir,
            "FEAT-001",
            str(design_classification["classification_id"]),
            "design",
            "The ownership boundary violates the accepted invariant.",
            "evidence/design-proof",
        )
        self.assertEqual(third["attempt"]["ordinal"], 3)
        repair_control.complete_remediation(
            self.run_dir,
            "FEAT-001",
            third["attempt"]["dispatch_id"],
            "failed_same_blocker",
            "The design-scale oracle still failed.",
        )
        control = repair_control._load_control(self.run_dir, "FEAT-001")
        self.assertEqual(control["status"], "blocked_after_design_remediation")
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.begin_remediation(
                self.run_dir,
                "FEAT-001",
                str(design_classification["classification_id"]),
                "design",
                "A repeated design hypothesis.",
                "evidence/repeated-design",
            )

    def test_sixty_minute_ceiling_stops_before_dispatch(self) -> None:
        event = self.record_event()
        classification = self.classify(event)
        with self.assertRaises(repair_control.RepairControlError) as blocked:
            repair_control.begin_remediation(
                self.run_dir,
                "FEAT-001",
                str(classification["classification_id"]),
                "localized",
                "A bounded hypothesis.",
                "evidence/time-boundary",
                active_seconds_delta=3600,
            )
        self.assertEqual(blocked.exception.code, "REPAIR_BUDGET_EXHAUSTED")
        control = repair_control._load_control(self.run_dir, "FEAT-001")
        self.assertEqual(control["active_blocker"]["stop_trigger"], "time_limit")

    def test_four_feature_dispatch_ceiling_spans_independent_blockers(self) -> None:
        for index in range(1, 6):
            payload = failure_payload(
                component=f"component-{index}",
                operation=f"operation-{index}",
                independent=(
                    f"Evidence proves blocker {index} is causally independent."
                    if index > 1
                    else None
                ),
                created_at=f"2026-07-28T10:0{index}:00Z",
            )
            event = repair_control.record_failure_event(self.run_dir, payload)["event"]
            classification = repair_control.classify_failure(
                self.run_dir, "FEAT-001", str(event["event_id"])
            )["classification"]
            if index == 5:
                with self.assertRaises(repair_control.RepairControlError) as blocked:
                    repair_control.begin_remediation(
                        self.run_dir,
                        "FEAT-001",
                        str(classification["classification_id"]),
                        "localized",
                        f"Independent hypothesis {index}.",
                        f"evidence/independent-{index}",
                    )
                self.assertEqual(
                    blocked.exception.code, "REPAIR_BUDGET_EXHAUSTED"
                )
                break
            attempt = repair_control.begin_remediation(
                self.run_dir,
                "FEAT-001",
                str(classification["classification_id"]),
                "localized",
                f"Independent hypothesis {index}.",
                f"evidence/independent-{index}",
            )
            repair_control.complete_remediation(
                self.run_dir,
                "FEAT-001",
                attempt["attempt"]["dispatch_id"],
                "succeeded",
                f"Independent oracle {index} passed.",
            )

    def test_three_low_information_experiments_require_model_rebuild(self) -> None:
        self.record_event()
        for index in range(3):
            repair_control.record_experiment(
                self.run_dir,
                "FEAT-001",
                f"Low-information question {index}.",
                f"Low-information hypothesis {index}.",
                f"evidence/experiment-{index}",
                "low",
            )
        with self.assertRaises(repair_control.RepairControlError) as blocked:
            repair_control.record_experiment(
                self.run_dir,
                "FEAT-001",
                "A fourth unchanged question.",
                "A fourth unchanged hypothesis.",
                "evidence/experiment-4",
                "low",
            )
        self.assertEqual(blocked.exception.code, "MODEL_REBUILD_REQUIRED")
        rebuilt = repair_control.record_experiment(
            self.run_dir,
            "FEAT-001",
            "Which boundary first diverges in the rebuilt model?",
            "The response mapper is the first divergent boundary.",
            "evidence/rebuilt-model",
            "decision_changing",
            model_rebuilt=True,
        )
        self.assertFalse(
            rebuilt["control"]["active_blocker"]["model_rebuild_required"]
        )

    def test_semantic_a_b_a_cycle_without_new_evidence_stops(self) -> None:
        event = self.record_event()
        first = self.classify(event)
        diagnosis = repair_control.record_diagnosis(
            self.run_dir, unresolved_diagnosis(event)
        )["diagnosis"]
        second = self.classify(event, diagnosis)
        third = self.classify(event)
        self.assertEqual(first["next_recommended_skill"], "sdlc-create-plan")
        self.assertIsNone(second["next_recommended_skill"])
        self.assertEqual(third["status"], "blocked_semantic_cycle")
        self.assertIsNone(third["next_recommended_skill"])

    def test_tampered_stale_malformed_and_secret_diagnoses_fail_closed(self) -> None:
        event = self.record_event()
        diagnosis = self.record_localized(event)
        path = repair_control._diagnosis_path(
            self.run_dir, "FEAT-001", str(diagnosis["diagnosis_id"])
        )
        tampered = json.loads(path.read_text(encoding="utf-8"))
        tampered["violated_invariant"] = "Tampered invariant."
        path.write_text(json.dumps(tampered), encoding="utf-8")
        with self.assertRaises(repair_control.RepairControlError) as changed:
            self.classify(event, diagnosis)
        self.assertEqual(changed.exception.code, "STATE_TAMPERED")

        stale = localized_diagnosis(event)
        stale["event_id"] = "0" * 64
        with self.assertRaises(repair_control.RepairControlError) as stale_error:
            repair_control.build_diagnosis(stale, event)
        self.assertEqual(stale_error.exception.code, "DIAGNOSIS_INVALID")

        malformed = localized_diagnosis(event)
        malformed.pop("earliest_divergence")
        with self.assertRaises(repair_control.RepairControlError) as malformed_error:
            repair_control.build_diagnosis(malformed, event)
        self.assertEqual(malformed_error.exception.code, "DIAGNOSIS_INCOMPLETE")

        secret = localized_diagnosis(event)
        secret["evidence_references"] = ["api_key=do-not-store"]
        with self.assertRaises(repair_control.RepairControlError) as secret_error:
            repair_control.build_diagnosis(secret, event)
        self.assertEqual(secret_error.exception.code, "SECURITY_BLOCKER")

    def test_symlinked_diagnosis_state_fails_closed(self) -> None:
        event = self.record_event()
        diagnosis_payload = localized_diagnosis(event)
        diagnosis = repair_control.build_diagnosis(diagnosis_payload, event)
        outside = Path(self.temporary.name) / "outside-diagnosis.json"
        outside.write_text(json.dumps(diagnosis), encoding="utf-8")
        path = repair_control._diagnosis_path(
            self.run_dir, "FEAT-001", str(diagnosis["diagnosis_id"])
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(outside)
        with self.assertRaises(repair_control.RepairControlError) as blocked:
            repair_control.record_diagnosis(self.run_dir, diagnosis_payload)
        self.assertEqual(blocked.exception.code, "STATE_INVALID")

    def test_invalidation_contract_matches_responsible_phase(self) -> None:
        event = self.record_event()
        classification = self.classify(event)
        self.assertEqual(
            classification["invalidates"],
            [
                "validation",
                "tests",
                "evaluation",
                "documentation",
                "alignment",
                "commit",
            ],
        )
        control = repair_control._load_control(self.run_dir, "FEAT-001")
        self.assertEqual(
            [item["surface"] for item in control["invalidations"]],
            classification["invalidates"],
        )

    def test_post_promotion_defect_starts_new_corrective_run(self) -> None:
        event = self.record_event(failure_payload(lifecycle="promoted"))
        classification = self.classify(event)
        self.assertEqual(classification["next_recommended_skill"], "sdlc-create-plan")
        self.assertEqual(
            classification["corrective_mode"],
            "new_corrective_run_from_promoted_commit",
        )


if __name__ == "__main__":
    unittest.main()
