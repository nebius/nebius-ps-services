#!/usr/bin/env python3
"""Deterministic tests for Agentic SDLC failure and repair control."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import multiprocessing
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


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
        "execution_id": "FEAT-001",
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
        "execution_id": "FEAT-001",
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


def validation_diagnosis(event: dict[str, object]) -> dict[str, object]:
    value = localized_diagnosis(event)
    value.update({
        "result": "validation_defect",
        "earliest_divergence": {
            "component": "design candidate",
            "operation": "write candidate text",
            "source_boundary": "terminal newline",
        },
        "violated_invariant": "The candidate has exactly one terminal newline.",
        "causal_chain": ["The repair appended a second newline.", "The original Markdown check rejects it."],
        "bounded_repair_target": "The candidate terminal newline only.",
        "counterfactual": "One terminal newline passes the unchanged Markdown check.",
        "alternatives_eliminated": ["The check correctly reports the candidate bytes."],
        "affected_files": ["evidence/design-candidate.md"],
        "regression_oracle": "Rerun the original candidate Markdown check.",
        "required_regression_test": "Reject a candidate with two terminal newlines.",
        "evidence_references": ["evidence/terminal-newline-proof.json"],
        "constraints": ["Preserve design decisions, publication ownership and repair counters."],
    })
    return value


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
        "execution_id": "FEAT-001",
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
        "execution_id": "FEAT-001",
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
        "schema": "agentic-sdlc/gate-evidence-v2",
        "execution_id": "FEAT-001",
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
        "execution_id": "FEAT-001",
        "surface": surface,
        "integration_commit": commit,
        "fingerprints": current_fingerprints,
        "evidence_reference": str(path.relative_to(run_dir)),
        "evidence_digest": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "recorded_at": f"2026-07-28T10:1{index}:00Z",
    }


class RepairControlTests(unittest.TestCase):
    def interposed_repair(self, child_type="VALIDATION_DEFECT"):
        event = self.record_event(failure_payload(proposed="PLAN_DEFECT", lifecycle="integrated_wave"))
        diagnosis = localized_diagnosis(event)
        diagnosis["result"] = "plan_defect"
        diagnosis = repair_control.record_diagnosis(self.run_dir, diagnosis)["diagnosis"]
        parent = self.classify(event, diagnosis)
        first = repair_control.begin_remediation(
            self.run_dir, "FEAT-001", parent["classification_id"], "localized",
            "Correct only the frozen command directory.", "evidence/command-counterfactual.json",
            active_seconds_delta=17,
        )
        payload = failure_payload(
            proposed=child_type, lifecycle="active_task", component="review command",
            operation="inspect existing files", error_class="optional path absent",
            source_boundary="auxiliary review", independent="Review fails after frozen checks pass.",
        )
        payload["phase"] = "sdlc-validate-codes"
        child = self.classify(self.record_event(payload))
        second = repair_control.begin_remediation(
            self.run_dir, "FEAT-001", child["classification_id"], "localized",
            "Inspect only paths independently proven to exist.", "evidence/path-observation.json",
            active_seconds_delta=11,
        )
        return parent, first, child, second

    def test_interposed_repair_completion_restores_counted_parent(self):
        parent, first, _child, second = self.interposed_repair()
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.integrated_wave_authority(self.run_dir, "FEAT-001",
                parent["classification_id"], first["attempt"]["dispatch_id"])
        result = repair_control.complete_remediation(self.run_dir, "FEAT-001",
            second["attempt"]["dispatch_id"], "succeeded", "evidence/fresh-worker-oracle.json", 13)
        control = result["control"]
        self.assertEqual(control["current_classification_id"], parent["classification_id"])
        self.assertEqual(control["active_blocker"], first["control"]["active_blocker"])
        self.assertEqual(control["status"], "remediating")
        self.assertEqual(control["feature_dispatches"], 2)
        self.assertEqual(control["route_history"][-1]["classification_id"], parent["classification_id"])
        self.assertEqual(control["route_history"][-1]["resumed_from_dispatch_id"], second["attempt"]["dispatch_id"])
        repair_control.integrated_wave_authority(self.run_dir, "FEAT-001",
            parent["classification_id"], first["attempt"]["dispatch_id"])
        finished = repair_control.complete_remediation(self.run_dir, "FEAT-001",
            first["attempt"]["dispatch_id"], "succeeded", "evidence/corrective-wave.json")
        self.assertEqual(finished["control"]["status"], "revalidation_required")
        self.assertEqual(finished["control"]["invalidations"], control["invalidations"])
        replay = repair_control.complete_remediation(self.run_dir, "FEAT-001",
            second["attempt"]["dispatch_id"], "succeeded", "evidence/fresh-worker-oracle.json", 13)
        self.assertFalse(replay["updated"])
        self.assertEqual(replay["control"], finished["control"])

    def test_interposed_observation_preserves_nested_budgets_and_mutation_fence(self):
        parent, first, _child, second = self.interposed_repair()
        attempts = [second["attempt"]]
        for ordinal in (3, 4):
            payload = failure_payload(
                proposed="VALIDATION_DEFECT", lifecycle="integrated_wave",
                component=f"authoring step {ordinal}", operation="inspect helper",
                independent=f"Distinct authoring failure {ordinal} before any product mutation.",
            )
            child = self.classify(self.record_event(payload))
            result = repair_control.begin_remediation(
                self.run_dir, "FEAT-001", child["classification_id"], "localized",
                "Use the verified helper contract.", "evidence/helper-contract.json",
            )
            attempts.append(result["attempt"])
        before = {p: p.read_bytes() for p in self.run_dir.rglob("*") if p.is_file()}
        observed = repair_control.observe_integrated_wave_repair(
            self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])
        self.assertEqual(observed["dispatch_id"], first["attempt"]["dispatch_id"])
        self.assertEqual({p: p.read_bytes() for p in self.run_dir.rglob("*") if p.is_file()}, before)
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.integrated_wave_authority(
                self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])
        for attempt in reversed(attempts):
            result = repair_control.complete_remediation(
                self.run_dir, "FEAT-001", attempt["dispatch_id"], "succeeded", "evidence/verified-child.json")
            self.assertEqual(result["control"]["feature_dispatches"], 4)
            repair_control.observe_integrated_wave_repair(
                self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])
        repair_control.complete_remediation(self.run_dir, "FEAT-001", first["attempt"]["dispatch_id"],
                                           "succeeded", "evidence/verified-parent.json")
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.observe_integrated_wave_repair(
                self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])

    def test_interposed_observation_rejects_unlinked_or_mutated_lineage(self):
        parent, first, _child, _second = self.interposed_repair()
        path = repair_control._control_path(self.run_dir, "FEAT-001")
        original = path.read_bytes()
        control = json.loads(original)
        mutations = [
            {"suspended_repairs": []},
            {"feature_dispatches": 1},
            {"feature_dispatches": 5},
            {"feature_dispatch_limit": 5},
            {"status": "blocked_feature_dispatch_limit"},
            {"current_classification_id": parent["classification_id"]},
            {"invalidations": control["invalidations"][1:]},
            {"active_blocker": {**control["active_blocker"],
                                "active_seconds": control["active_blocker"]["time_limit_seconds"]}},
        ]
        for changes in mutations:
            try:
                repair_control._write_json_atomic(path, {**control, **changes})
                with self.subTest(changes=tuple(changes)), self.assertRaises(repair_control.RepairControlError):
                    repair_control.observe_integrated_wave_repair(
                        self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])
            finally:
                path.write_bytes(original)
        snapshot_path = path.parent / "suspended" / (control["suspended_repairs"][0] + ".json")
        snapshot = json.loads(snapshot_path.read_text())
        snapshot["interruption_event_id"] = parent["event_id"]
        identity = repair_control._digest(snapshot)
        forged = snapshot_path.with_name(identity + ".json")
        repair_control._write_json_atomic(forged, snapshot)
        try:
            repair_control._write_json_atomic(path, {**control, "suspended_repairs": [identity]})
            with self.assertRaises(repair_control.RepairControlError):
                repair_control.observe_integrated_wave_repair(
                    self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])
        finally:
            path.write_bytes(original)
            forged.unlink()
        repair_control.observe_integrated_wave_repair(
            self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])

    def test_interposed_observation_rejects_reordered_chain_and_changed_commit(self):
        parent, first, _child, _second = self.interposed_repair()
        for ordinal, commit in ((3, "a" * 40), (4, "b" * 40)):
            payload = failure_payload(
                proposed="VALIDATION_DEFECT", lifecycle="integrated_wave", commit=commit,
                component=f"nested author {ordinal}", operation="inspect helper",
                independent=f"Independent authoring failure {ordinal}.",
            )
            child = self.classify(self.record_event(payload))
            repair_control.begin_remediation(
                self.run_dir, "FEAT-001", child["classification_id"], "localized",
                "Use the verified helper contract.", "evidence/helper-contract.json")
            if ordinal == 3:
                path = repair_control._control_path(self.run_dir, "FEAT-001")
                before = path.read_bytes()
                control = json.loads(before)
                try:
                    repair_control._write_json_atomic(path, {
                        **control, "suspended_repairs": list(reversed(control["suspended_repairs"]))})
                    with self.assertRaises(repair_control.RepairControlError):
                        repair_control.observe_integrated_wave_repair(
                            self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])
                finally:
                    path.write_bytes(before)
                repair_control.observe_integrated_wave_repair(
                    self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.observe_integrated_wave_repair(
                self.run_dir, "FEAT-001", parent["classification_id"], first["attempt"]["dispatch_id"])

    def test_interposed_repair_rejects_failed_broader_and_tampered_continuation(self):
        for mode in ("failed", "broader", "snapshot", "dispatch", "invalidations", "time", "schema"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                self.run_dir = Path(temporary) / "run"
                parent, first, _child, second = self.interposed_repair(
                    "SPEC_GAP" if mode == "broader" else "VALIDATION_DEFECT")
                control_path = repair_control._control_path(self.run_dir, "FEAT-001")
                control = repair_control._load_control(self.run_dir, "FEAT-001")
                if mode in {"snapshot", "dispatch"}:
                    if mode == "snapshot":
                        path = repair_control._repair_root(self.run_dir, "FEAT-001") / "suspended" / (control["suspended_repairs"][-1] + ".json")
                    else:
                        path = repair_control._repair_root(self.run_dir, "FEAT-001") / "dispatches" / (first["attempt"]["dispatch_id"] + ".json")
                    value = json.loads(path.read_text())
                    value["changed"] = True
                    repair_control._write_json_atomic(path, value)
                if mode == "schema":
                    control["schema"] = "agentic-sdlc/repair-control-v2"
                    repair_control._write_json_atomic(control_path, control)
                if mode == "invalidations":
                    control["invalidations"] = []
                    repair_control._write_json_atomic(control_path, control)
                before = control_path.read_bytes()
                args = (self.run_dir, "FEAT-001", second["attempt"]["dispatch_id"],
                        "failed_same_blocker" if mode == "failed" else "succeeded",
                        "evidence/worker-oracle.json", 3600 if mode == "time" else 0)
                if mode == "failed":
                    result = repair_control.complete_remediation(*args)
                    self.assertEqual(result["control"]["status"], "diagnosis_required")
                    self.assertEqual(len(result["control"]["suspended_repairs"]), 1)
                else:
                    with self.assertRaises(repair_control.RepairControlError):
                        repair_control.complete_remediation(*args)
                    if mode == "time":
                        exhausted = repair_control._load_control(self.run_dir, "FEAT-001")
                        self.assertEqual(exhausted["active_blocker"]["active_seconds"], 3611)
                        self.assertEqual(exhausted["active_blocker"]["stop_trigger"], "time_limit")
                        self.assertEqual(exhausted["status"], "exhausted")
                        self.assertEqual(exhausted["suspended_repairs"], control["suspended_repairs"])
                        self.assertEqual(exhausted["feature_dispatches"], 2)
                        self.assertEqual(exhausted["resumptions"], {})
                        self.assertIsNone(exhausted["active_blocker"]["attempts"][-1]["result"])
                        stopped = control_path.read_bytes()
                        with self.assertRaises(repair_control.RepairControlError):
                            repair_control.complete_remediation(*args[:-1], 0)
                        self.assertEqual(control_path.read_bytes(), stopped)
                    else:
                        self.assertEqual(control_path.read_bytes(), before)
                with self.assertRaises(repair_control.RepairControlError):
                    repair_control.integrated_wave_authority(self.run_dir, "FEAT-001",
                        parent["classification_id"], first["attempt"]["dispatch_id"])

    def test_interposed_completion_recovers_atomic_publication_without_recharge(self):
        for boundary in ("control", "journal"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as temporary:
                self.run_dir = Path(temporary) / "run"
                parent, _first, _child, second = self.interposed_repair()
                args = (self.run_dir, "FEAT-001", second["attempt"]["dispatch_id"],
                        "succeeded", "evidence/fresh-worker-oracle.json", 13)
                writer = repair_control._write_json_atomic
                journal = repair_control._append_journal
                def interrupt_write(path, value):
                    if path == repair_control._control_path(self.run_dir, "FEAT-001"):
                        raise RuntimeError("simulated interruption")
                    return writer(path, value)
                def interrupt_journal(*args, **kwargs):
                    if args[2] == "remediation-completion-committed":
                        raise RuntimeError("simulated interruption")
                    return journal(*args, **kwargs)
                name, side_effect = (("_write_json_atomic", interrupt_write) if boundary == "control"
                                     else ("_append_journal", interrupt_journal))
                with mock.patch.object(repair_control, name, side_effect=side_effect), self.assertRaises(RuntimeError):
                    repair_control.complete_remediation(*args)
                restored = repair_control.complete_remediation(*args)["control"]
                self.assertEqual(restored["current_classification_id"], parent["classification_id"])
                self.assertEqual(restored["feature_dispatches"], 2)
                identity = restored["resumptions"][second["attempt"]["dispatch_id"]]
                receipt = json.loads((repair_control._repair_root(self.run_dir, "FEAT-001") / "resumptions" / (identity + ".json")).read_text())
                self.assertEqual(receipt["completed_context"]["active_blocker"]["active_seconds"], 24)
                self.assertEqual(len(list((repair_control._repair_root(self.run_dir, "FEAT-001") / "resumptions").glob("*.json"))), 1)

    def test_interposed_nested_and_fourth_dispatch_preserve_all_budgets(self):
        parent, first, child, second = self.interposed_repair()
        payload = failure_payload(proposed="VALIDATION_DEFECT", lifecycle="active_task",
            component="second checker", independent="Separate checker fails before reading product.")
        payload["phase"] = "sdlc-validate-codes"
        third_class = self.classify(self.record_event(payload))
        third = repair_control.begin_remediation(self.run_dir, "FEAT-001", third_class["classification_id"],
            "localized", "Fix the second checker input.", "evidence/second-checker.json")["attempt"]
        restored = repair_control.complete_remediation(self.run_dir, "FEAT-001", third["dispatch_id"],
            "succeeded", "evidence/second-checker-pass.json")["control"]
        self.assertEqual(restored["current_classification_id"], child["classification_id"])
        restored = repair_control.complete_remediation(self.run_dir, "FEAT-001", second["attempt"]["dispatch_id"],
            "succeeded", "evidence/first-checker-pass.json")["control"]
        self.assertEqual(restored["active_blocker"], first["control"]["active_blocker"])
        payload["component"] = "third checker"
        fourth_class = self.classify(self.record_event(payload))
        fourth = repair_control.begin_remediation(self.run_dir, "FEAT-001", fourth_class["classification_id"],
            "localized", "Fix the third checker input.", "evidence/third-checker.json")["attempt"]
        restored = repair_control.complete_remediation(self.run_dir, "FEAT-001", fourth["dispatch_id"],
            "succeeded", "evidence/third-checker-pass.json")["control"]
        self.assertEqual(restored["feature_dispatches"], 4)
        self.assertEqual(restored["current_classification_id"], parent["classification_id"])
        self.assertEqual(restored["active_blocker"], first["control"]["active_blocker"])
        self.assertEqual(restored["suspended_repairs"], [])
        self.assertEqual(len(restored["resumptions"]), 3)

    def test_interposed_failure_replay_cannot_reset_or_reactivate_a_blocker(self):
        parent, _first, child, second = self.interposed_repair()
        original = repair_control._load_event(self.run_dir, "FEAT-001", parent["event_id"])
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.record_failure_event(self.run_dir, original)
        restored = repair_control.complete_remediation(self.run_dir, "FEAT-001", second["attempt"]["dispatch_id"],
            "succeeded", "evidence/worker-pass.json")["control"]
        completed = repair_control._load_event(self.run_dir, "FEAT-001", child["event_id"])
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.record_failure_event(self.run_dir, completed)
        self.assertEqual(repair_control._load_control(self.run_dir, "FEAT-001"), restored)

    def test_timestamp_guidance_matches_existing_accepted_format(self) -> None:
        canonical = "2026-11-03T10:00:00Z"
        self.assertEqual(repair_control._now(canonical), canonical)
        for value in (
            "2026-11-03T10:00:00.123456+00:00",
            "2026-11-03T10:00:00+00:00",
            "2026-11-03T10:00:00.123456Z",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                repair_control.RepairControlError, "YYYY-MM-DDTHH:MM:SSZ"
            ):
                repair_control._now(value)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temporary.name) / "run"
        # These isolate the repair state machine with synthetic events. Live
        # worker binding is covered by the real-Git WorkerResultContractCases.
        guard = mock.patch.object(repair_control, "_validate_worker_failure")
        locks = mock.patch.object(repair_control, "_failure_transition_lock",
                                  side_effect=lambda run, execution, **_: repair_control._transition_lock(run, execution))
        guard.start()
        locks.start()
        self.addCleanup(guard.stop)
        self.addCleanup(locks.stop)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record_event(
        self, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        result = repair_control.record_failure_event(
            self.run_dir, payload or failure_payload()
        )
        return result["event"]

    def record_localized(self, event: dict[str, object]) -> dict[str, object]:
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

    def test_active_environment_failure_uses_existing_task_attempt(self) -> None:
        for confidence in ("proven", "high_confidence"):
            with self.subTest(confidence=confidence):
                payload = failure_payload(
                    proposed="ENVIRONMENT_DEFECT", lifecycle="active_task",
                    cause_status=confidence,
                )
                payload["phase"] = "sdlc-implement-plan"
                classification = self.classify(self.record_event(payload))
                self.assertEqual(classification["classification"], "ENVIRONMENT_DEFECT")
                self.assertEqual(classification["status"], "routed")
                self.assertEqual(classification["next_recommended_skill"], "sdlc-implement-plan")
                self.assertEqual(classification["corrective_mode"], "existing_task_attempt")
                self.assertEqual(classification["invalidates"], [
                    "validation", "tests", "evaluation", "documentation", "alignment", "commit",
                ])

    def worker_recovery_fixture(self, *, proposed="IMPLEMENTATION_DEFECT", lifecycle="active_task",
                                charged=True, prior_dispatches=0):
        self.run_dir = self.run_dir.resolve()
        payload = failure_payload(proposed=proposed, lifecycle=lifecycle)
        payload["phase"] = "sdlc-implement-plan"
        payload["evidence_digests"]["worker_result"] = digest("5")
        event = self.record_event(payload)
        classification = self.classify(event)
        (self.run_dir / "current-state.json").write_text(json.dumps({
            "fingerprint_ids": [key + ":" + value for key, value in event["fingerprints"].items()],
        }))
        if not charged:
            return event, classification, None
        if prior_dispatches:
            # Synthetic earlier-feature history isolates redemption at the ceiling.
            control = repair_control._load_control(self.run_dir, "FEAT-001")
            control["feature_dispatches"] = prior_dispatches
            repair_control._write_json_atomic(repair_control._control_path(self.run_dir, "FEAT-001"), control)
        attempt = repair_control.begin_remediation(
            self.run_dir, "FEAT-001", classification["classification_id"], "localized",
            "Repair the classified active worker failure.", "evidence/worker-failure.json",
        )["attempt"]
        return event, classification, attempt

    def test_worker_recovery_accepts_exact_counted_same_assignment_routes(self):
        for proposed in ("IMPLEMENTATION_DEFECT", "ENVIRONMENT_DEFECT", "VALIDATION_DEFECT"):
            with self.subTest(proposed=proposed), tempfile.TemporaryDirectory() as directory:
                self.run_dir = Path(directory) / "run"
                event, classification, attempt = self.worker_recovery_fixture(proposed=proposed)
                before = repair_control._control_path(self.run_dir, "FEAT-001").read_bytes()
                authority = repair_control.worker_recovery_authority(self.run_dir, "FEAT-001", "5" * 64, "a" * 40)
                self.assertEqual(authority["event_id"], event["event_id"])
                self.assertEqual(authority["classification_id"], classification["classification_id"])
                self.assertEqual(authority["dispatch_id"], attempt["dispatch_id"])
                self.assertEqual(repair_control._control_path(self.run_dir, "FEAT-001").read_bytes(), before)

    def test_worker_recovery_rejects_uncharged_wrong_route_and_wrong_lifecycle(self):
        for options in (
            {"charged": False}, {"proposed": "TEST_DEFECT"}, {"proposed": "PLAN_DEFECT"},
            {"lifecycle": "integrated_wave"},
        ):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                self.run_dir = Path(directory) / "run"
                self.worker_recovery_fixture(**options)
                with self.assertRaises(repair_control.RepairControlError) as caught:
                    repair_control.worker_recovery_authority(self.run_dir, "FEAT-001", "5" * 64, "a" * 40)
                self.assertEqual(caught.exception.code, "REPAIR_NOT_AUTHORIZED")

    def test_worker_recovery_rejects_wrong_receipt_commit_and_current_fingerprints(self):
        self.worker_recovery_fixture()
        for receipt, commit in (("6" * 64, "a" * 40), ("5" * 64, "b" * 40)):
            with self.subTest(receipt=receipt, commit=commit), self.assertRaises(repair_control.RepairControlError):
                repair_control.worker_recovery_authority(self.run_dir, "FEAT-001", receipt, commit)
        (self.run_dir / "current-state.json").write_text(json.dumps({
            "fingerprint_ids": ["requirements:" + digest("2"), "design:" + digest("3"), "plan:" + digest("6")],
        }))
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.worker_recovery_authority(self.run_dir, "FEAT-001", "5" * 64, "a" * 40)

    def test_worker_recovery_rejects_completed_and_superseded_dispatch(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement), tempfile.TemporaryDirectory() as directory:
                self.run_dir = Path(directory) / "run"
                _, _, attempt = self.worker_recovery_fixture()
                if replacement:
                    payload = failure_payload(lifecycle="active_task", created_at="2026-07-28T10:01:00Z")
                    payload["phase"] = "sdlc-implement-plan"
                    payload["evidence_digests"]["worker_result"] = digest("6")
                    self.classify(self.record_event(payload))
                else:
                    repair_control.complete_remediation(self.run_dir, "FEAT-001", attempt["dispatch_id"],
                                                        "succeeded", "evidence/verification.json")
                with self.assertRaises(repair_control.RepairControlError):
                    repair_control.worker_recovery_authority(self.run_dir, "FEAT-001", "5" * 64, "a" * 40)

    def test_worker_recovery_redeems_fourth_dispatch_without_counting_again(self):
        self.worker_recovery_fixture(prior_dispatches=3)
        self.assertEqual(repair_control._load_control(self.run_dir, "FEAT-001")["feature_dispatches"], 4)
        repair_control.worker_recovery_authority(self.run_dir, "FEAT-001", "5" * 64, "a" * 40)
        self.assertEqual(repair_control._load_control(self.run_dir, "FEAT-001")["feature_dispatches"], 4)

    def test_worker_recovery_rejects_dispatch_receipt_tampering(self):
        _, _, attempt = self.worker_recovery_fixture()
        path = repair_control._repair_root(self.run_dir, "FEAT-001") / "dispatches" / (attempt["dispatch_id"] + ".json")
        repair_control._write_json_atomic(path, {**attempt, "hypothesis": "Changed immutable hypothesis."})
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.worker_recovery_authority(self.run_dir, "FEAT-001", "5" * 64, "a" * 40)

    def test_active_environment_dispatch_is_counted_and_requires_revalidation(self) -> None:
        payload = failure_payload(proposed="ENVIRONMENT_DEFECT", lifecycle="active_task")
        payload["phase"] = "sdlc-implement-plan"
        event = self.record_event(payload)
        classification = self.classify(event)
        args = (
            self.run_dir, "FEAT-001", classification["classification_id"], "localized",
            "Use the supported approved lookup while preserving the assignment.",
            "evidence/environment-permission-comparison.json",
        )
        dispatched = repair_control.begin_remediation(*args)
        duplicate = repair_control.begin_remediation(*args)
        self.assertFalse(duplicate["created"])
        self.assertEqual(duplicate["attempt"]["dispatch_id"], dispatched["attempt"]["dispatch_id"])
        completed = repair_control.complete_remediation(
            self.run_dir, "FEAT-001", dispatched["attempt"]["dispatch_id"],
            "succeeded", "evidence/original-operation-and-worker-validation.json",
        )
        control = completed["control"]
        self.assertEqual(control["status"], "revalidation_required")
        self.assertEqual(control["feature_dispatches"], 1)
        self.assertEqual(control["active_blocker"]["total_attempts"], 1)
        self.assertEqual([item["surface"] for item in control["revalidation"]["required"]], [
            "validation", "tests", "evaluation", "documentation", "alignment", "commit",
        ])
        self.assertEqual(control["revalidation"]["cursor"], 0)
        self.assertEqual(self.record_event(payload), event)
        self.assertEqual(self.classify(event), classification)
        self.assertEqual(repair_control._load_control(self.run_dir, "FEAT-001"), control)

    def test_active_environment_route_does_not_expand_other_failure_authority(self) -> None:
        cases = [
            ("ENVIRONMENT_DEFECT", lifecycle, "sdlc-implement-plan", "proven", "blocked")
            for lifecycle in repair_control.EXECUTION_LIFECYCLES - {"active_task"}
        ] + [
            ("ENVIRONMENT_DEFECT", "active_task", phase, "proven", "blocked")
            for phase in ("implementation", "evaluation", "sdlc-unit-tests", "unknown-phase")
        ] + [
            (name, "active_task", "sdlc-implement-plan", "proven", "blocked")
            for name in ("POLICY_BLOCK", "HUMAN_INPUT_REQUIRED")
        ] + [
            ("ENVIRONMENT_DEFECT", "active_task", "sdlc-implement-plan", confidence, "diagnosis_required")
            for confidence in ("probable", "unknown")
        ]
        for proposed, lifecycle, phase, confidence, expected in cases:
            with self.subTest(proposed=proposed, lifecycle=lifecycle, phase=phase, confidence=confidence):
                with tempfile.TemporaryDirectory() as temporary:
                    run = Path(temporary)
                    payload = failure_payload(proposed=proposed, lifecycle=lifecycle, cause_status=confidence)
                    payload["phase"] = phase
                    event = repair_control.record_failure_event(run, payload)["event"]
                    classification = repair_control.classify_failure(run, "FEAT-001", event["event_id"])["classification"]
                    self.assertEqual(classification["status"], expected)
                    self.assertNotEqual(classification["next_recommended_skill"], "sdlc-implement-plan")
                    with self.assertRaises(repair_control.RepairControlError) as denied:
                        repair_control.begin_remediation(
                            run, "FEAT-001", classification["classification_id"],
                            "localized", "Try a changed access path.", "evidence/access.json",
                        )
                    self.assertEqual(denied.exception.code, "REPAIR_NOT_AUTHORIZED")

    def test_failed_active_environment_retry_requires_new_diagnosis(self) -> None:
        payload = failure_payload(proposed="ENVIRONMENT_DEFECT", lifecycle="active_task")
        payload["phase"] = "sdlc-implement-plan"
        classification = self.classify(self.record_event(payload))
        first = repair_control.begin_remediation(
            self.run_dir, "FEAT-001", classification["classification_id"], "localized",
            "Use the authorized execution mechanism.", "evidence/access-comparison.json",
        )
        repair_control.complete_remediation(
            self.run_dir, "FEAT-001", first["attempt"]["dispatch_id"],
            "failed_same_blocker", "The original operation still failed.",
        )
        with self.assertRaises(repair_control.RepairControlError) as denied:
            repair_control.begin_remediation(
                self.run_dir, "FEAT-001", classification["classification_id"], "localized",
                "A changed dependency path might restore access.", "evidence/new-access.json",
            )
        self.assertEqual(denied.exception.code, "TROUBLESHOOT_REQUIRED")

    def test_unprepared_validation_returns_to_available_authoring_phase(self) -> None:
        phases = (
            "sdlc-create-requirements", "sdlc-gather-context", "sdlc-create-design",
            "sdlc-auto-steering", "sdlc-create-plan", "sdlc-prepare-execution",
        )
        for phase in phases:
            with self.subTest(phase=phase):
                payload = failure_payload(proposed="VALIDATION_DEFECT", lifecycle="unprepared")
                payload["phase"] = phase
                event = self.record_event(payload)
                classification = self.classify(event)
                self.assertEqual(classification["next_recommended_skill"], phase)
                self.assertEqual(classification["corrective_mode"], "phase_artifact_repair")
                self.assertEqual(classification["invalidates"], [])
                self.assertEqual(classification["status"], "routed")

    def test_failed_validation_repair_returns_from_diagnosis_without_reset(self) -> None:
        payload = failure_payload(proposed="VALIDATION_DEFECT", lifecycle="unprepared")
        payload["phase"] = "sdlc-create-design"
        event = self.record_event(payload)
        classification = self.classify(event)
        first = repair_control.begin_remediation(
            self.run_dir, "FEAT-001", classification["classification_id"], "localized",
            "Quote the path parameter to satisfy the original check.", "evidence/path-proof.json",
        )
        repair_control.complete_remediation(
            self.run_dir, "FEAT-001", first["attempt"]["dispatch_id"],
            "failed_same_blocker", "evidence/original-check-still-fails.json",
        )
        with self.assertRaises(repair_control.RepairControlError) as denied:
            repair_control.begin_remediation(
                self.run_dir, "FEAT-001", classification["classification_id"], "localized",
                "Remove the extra terminal newline.", "evidence/terminal-newline-proof.json",
            )
        self.assertEqual(denied.exception.code, "TROUBLESHOOT_REQUIRED")
        diagnosis_payload = validation_diagnosis(event)
        diagnosis = repair_control.record_diagnosis(self.run_dir, diagnosis_payload)["diagnosis"]
        self.assertFalse(repair_control.record_diagnosis(self.run_dir, diagnosis_payload)["created"])
        classified = self.classify(event, diagnosis)
        self.assertEqual(classified["classification"], "VALIDATION_DEFECT")
        self.assertEqual(classified["next_recommended_skill"], "sdlc-create-design")
        self.assertEqual(classified["corrective_mode"], "phase_artifact_repair")
        self.assertEqual(classified["invalidates"], [])
        self.assertEqual(self.classify(event, diagnosis), classified)
        args = (
            self.run_dir, "FEAT-001", classified["classification_id"], "localized",
            "Exactly one terminal newline satisfies the unchanged check.",
            "evidence/terminal-newline-proof.json",
        )
        second = repair_control.begin_remediation(*args)
        self.assertEqual(second["attempt"]["ordinal"], 2)
        self.assertFalse(repair_control.begin_remediation(*args)["created"])
        completed = repair_control.complete_remediation(
            self.run_dir, "FEAT-001", second["attempt"]["dispatch_id"],
            "succeeded", "evidence/original-check-passed.json",
        )["control"]
        self.assertEqual(completed["status"], "resolved")
        self.assertEqual(completed["feature_dispatches"], 2)
        self.assertEqual(completed["feature_dispatch_limit"], 4)
        self.assertEqual(completed["active_blocker"]["total_attempts"], 2)
        self.assertEqual(completed["active_blocker"]["localized_limit"], 2)
        self.assertEqual(completed["active_blocker"]["attempts"][0]["result"], "failed_same_blocker")
        self.assertEqual(self.record_event(payload), event)

    def test_diagnosis_contract_rejects_retired_active_schema(self) -> None:
        event = self.record_event()
        payload = localized_diagnosis(event)
        payload["schema"] = "agentic-sdlc/diagnosis-v2"
        with self.assertRaisesRegex(repair_control.RepairControlError, "diagnosis schema is invalid"):
            repair_control.record_diagnosis(self.run_dir, payload)

    def test_validation_diagnosis_requires_complete_bounded_proof(self) -> None:
        event = self.record_event()
        for field in (
            "earliest_divergence", "violated_invariant", "causal_chain", "counterfactual",
            "alternatives_eliminated", "affected_files", "bounded_repair_target",
            "regression_oracle", "required_regression_test", "evidence_references", "constraints",
        ):
            with self.subTest(field=field):
                payload = validation_diagnosis(event)
                payload.pop(field)
                with self.assertRaises(repair_control.RepairControlError) as denied:
                    repair_control.build_diagnosis(payload, event)
                expected = "DIAGNOSIS_INCOMPLETE" if field == "earliest_divergence" else "RECORD_INVALID"
                self.assertEqual(denied.exception.code, expected)
        for confidence in ("probable", "unknown"):
            payload = validation_diagnosis(event)
            payload["confidence"] = confidence
            with self.assertRaisesRegex(repair_control.RepairControlError, "cannot authorize"):
                repair_control.build_diagnosis(payload, event)

    def test_retired_active_diagnosis_cannot_authorize_or_complete_dispatch(self) -> None:
        for dispatched in (False, True):
            with self.subTest(dispatched=dispatched):
                run = self.run_dir / str(dispatched)
                with mock.patch.object(repair_control, "DIAGNOSIS_SCHEMA", "agentic-sdlc/diagnosis-v2"):
                    event = repair_control.record_failure_event(run, failure_payload())["event"]
                    diagnosis = repair_control.record_diagnosis(run, localized_diagnosis(event))["diagnosis"]
                    classification = repair_control.classify_failure(
                        run, "FEAT-001", event["event_id"], diagnosis["diagnosis_id"],
                    )["classification"]
                    args = (
                        run, "FEAT-001", classification["classification_id"], "localized",
                        "Use the proven canonical field.", "evidence/field-proof.json",
                    )
                    if dispatched:
                        attempt = repair_control.begin_remediation(*args)["attempt"]
                control_path = repair_control._control_path(run, "FEAT-001")
                before = control_path.read_bytes()
                with self.assertRaisesRegex(repair_control.RepairControlError, "diagnosis schema is invalid"):
                    if dispatched:
                        repair_control.complete_remediation(
                            run, "FEAT-001", attempt["dispatch_id"],
                            "succeeded", "evidence/check-passed.json",
                        )
                    else:
                        repair_control.begin_remediation(*args)
                self.assertEqual(control_path.read_bytes(), before)

    def test_validation_diagnosis_retains_route_and_integrity_boundaries(self) -> None:
        for phase, lifecycle, route in (
            ("sdlc-create-design", "unprepared", "sdlc-create-design"),
            ("unknown-phase", "unprepared", None),
            ("sdlc-validate-codes", "unprepared", None),
            ("sdlc-validate-codes", "waves_completed", "sdlc-validate-codes"),
        ):
            with self.subTest(phase=phase, lifecycle=lifecycle):
                run = self.run_dir / (phase + lifecycle)
                payload = failure_payload(proposed="VALIDATION_DEFECT", lifecycle=lifecycle)
                payload["phase"] = phase
                event = repair_control.record_failure_event(run, payload)["event"]
                diagnosis = repair_control.record_diagnosis(run, validation_diagnosis(event))["diagnosis"]
                classified = repair_control.classify_failure(
                    run, "FEAT-001", event["event_id"], diagnosis["diagnosis_id"],
                )["classification"]
                self.assertEqual(classified["next_recommended_skill"], route)
                self.assertEqual(classified["status"], "routed" if route else "blocked")
                if route is None:
                    with self.assertRaises(repair_control.RepairControlError):
                        repair_control.begin_remediation(
                            run, "FEAT-001", classified["classification_id"], "localized",
                            "Repair the proven artifact.", "evidence/next-proof.json",
                        )
                path = repair_control._diagnosis_path(run, "FEAT-001", diagnosis["diagnosis_id"])
                tampered = json.loads(path.read_text())
                tampered["bounded_repair_target"] = "A different artifact."
                path.write_text(json.dumps(tampered))
                with self.assertRaisesRegex(repair_control.RepairControlError, "STATE_TAMPERED"):
                    repair_control.classify_failure(run, "FEAT-001", event["event_id"], diagnosis["diagnosis_id"])
        event = self.record_event()
        for field, value, code in (
            ("event_id", "0" * 64, "DIAGNOSIS_INVALID"),
            ("execution_id", "FEAT-002", "DIAGNOSIS_INVALID"),
            ("evidence_references", ["api_key=do-not-store"], "SECURITY_BLOCKER"),
            ("result", "validation", "DIAGNOSIS_INVALID"),
        ):
            payload = validation_diagnosis(event)
            payload[field] = value
            with self.subTest(field=field), self.assertRaises(repair_control.RepairControlError) as denied:
                repair_control.build_diagnosis(payload, event)
            self.assertEqual(denied.exception.code, code)

    def test_active_diagnosis_references_reject_invalid_ids_before_lookup(self) -> None:
        event = self.record_event()
        self.record_localized(event)
        path = repair_control._control_path(self.run_dir, "FEAT-001")
        control = json.loads(path.read_text())
        original_read = repair_control._read_json
        allowed = {path, repair_control._event_path(self.run_dir, "FEAT-001", event["event_id"])}

        def bounded_read(candidate):
            self.assertIn(candidate, allowed, "invalid identity reached a record lookup")
            return original_read(candidate)

        for key, value in (
            ("current_event_id", "../../outside"),
            ("current_event_id", None),
            ("current_diagnosis_id", "../../outside"),
            ("current_diagnosis_id", ["not-an-identity"]),
        ):
            with self.subTest(key=key, value=value):
                path.write_text(json.dumps({**control, key: value}))
                with mock.patch.object(repair_control, "_read_json", side_effect=bounded_read):
                    with self.assertRaises(repair_control.RepairControlError) as denied:
                        repair_control._load_control(self.run_dir, "FEAT-001")
                self.assertEqual(denied.exception.code, "STATE_INVALID")

    def test_historical_retired_diagnosis_cannot_authorize_new_dispatch(self) -> None:
        with mock.patch.object(repair_control, "DIAGNOSIS_SCHEMA", "agentic-sdlc/diagnosis-v2"):
            event = self.record_event()
            diagnosis = self.record_localized(event)
            classification = self.classify(event, diagnosis)
            self.record_event(failure_payload(commit="b" * 40))
        path = repair_control._control_path(self.run_dir, "FEAT-001")
        before = path.read_bytes()
        self.assertIsNone(json.loads(before)["current_diagnosis_id"])
        with self.assertRaisesRegex(repair_control.RepairControlError, "diagnosis schema is invalid"):
            repair_control.begin_remediation(
                self.run_dir, "FEAT-001", classification["classification_id"], "localized",
                "Use the proven canonical field.", "evidence/field-proof.json",
            )
        self.assertEqual(path.read_bytes(), before)

    def test_historical_retired_diagnosis_cannot_complete_dispatch(self) -> None:
        for result in ("succeeded", "failed_same_blocker"):
            with self.subTest(result=result):
                run = self.run_dir / result
                with mock.patch.object(repair_control, "DIAGNOSIS_SCHEMA", "agentic-sdlc/diagnosis-v2"):
                    event = repair_control.record_failure_event(run, failure_payload())["event"]
                    diagnosis = repair_control.record_diagnosis(run, localized_diagnosis(event))["diagnosis"]
                    classification = repair_control.classify_failure(
                        run, "FEAT-001", event["event_id"], diagnosis["diagnosis_id"],
                    )["classification"]
                    attempt = repair_control.begin_remediation(
                        run, "FEAT-001", classification["classification_id"], "localized",
                        "Use the proven canonical field.", "evidence/field-proof.json",
                    )["attempt"]
                    repair_control.record_failure_event(run, failure_payload(commit="b" * 40))
                paths = (
                    repair_control._control_path(run, "FEAT-001"),
                    repair_control._journal_path(run, "FEAT-001"),
                )
                before = [path.read_bytes() for path in paths]
                with self.assertRaisesRegex(repair_control.RepairControlError, "diagnosis schema is invalid"):
                    repair_control.complete_remediation(
                        run, "FEAT-001", attempt["dispatch_id"], result, "evidence/check.json",
                    )
                self.assertEqual([path.read_bytes() for path in paths], before)

    def test_active_diagnosis_is_bound_to_control_blocker(self) -> None:
        event = self.record_event()
        self.record_localized(event)
        path = repair_control._control_path(self.run_dir, "FEAT-001")
        control = json.loads(path.read_text())
        control["active_blocker"]["blocker_key"] = "foreign|operation|error|boundary"
        path.write_text(json.dumps(control))
        with self.assertRaisesRegex(repair_control.RepairControlError, "diagnosis blocker"):
            repair_control._load_control(self.run_dir, "FEAT-001")

    def test_unprepared_validation_rejects_unavailable_phase_without_dispatch(self) -> None:
        for phase in ("unknown-phase", "design", "sdlc-validate-codes", "sdlc-start"):
            with self.subTest(phase=phase):
                payload = failure_payload(proposed="VALIDATION_DEFECT", lifecycle="unprepared")
                payload["phase"] = phase
                event = self.record_event(payload)
                classification = self.classify(event)
                self.assertEqual(classification["status"], "blocked")
                self.assertIsNone(classification["next_recommended_skill"])
                with self.assertRaises(repair_control.RepairControlError):
                    repair_control.begin_remediation(
                        self.run_dir, "FEAT-001", classification["classification_id"],
                        "localized", "Repair the phase artifact.", "evidence/validation.md",
                    )

    def test_preparation_artifact_repair_preserves_identity_and_counted_completion(self) -> None:
        payload = failure_payload(proposed="VALIDATION_DEFECT", lifecycle="unprepared")
        payload["phase"] = "sdlc-create-design"
        event = self.record_event(payload)
        classification = self.classify(event)
        self.assertEqual(classification["next_recommended_skill"], "sdlc-create-design")
        args = (
            self.run_dir, "FEAT-001", classification["classification_id"], "localized",
            "Correct the proven candidate syntax without changing its architecture.",
            "evidence/candidate-validation.md",
        )
        dispatched = repair_control.begin_remediation(*args)
        repeated = repair_control.begin_remediation(*args)
        self.assertFalse(repeated["created"])
        self.assertEqual(repeated["attempt"]["dispatch_id"], dispatched["attempt"]["dispatch_id"])
        completed = repair_control.complete_remediation(
            self.run_dir, "FEAT-001", dispatched["attempt"]["dispatch_id"],
            "succeeded", "evidence/original-candidate-check-passed.md",
        )
        self.assertEqual(completed["control"]["status"], "resolved")
        self.assertEqual(completed["control"]["feature_dispatches"], 1)
        self.assertEqual(completed["control"]["active_blocker"]["total_attempts"], 1)
        self.assertEqual(self.record_event(payload), event)
        self.assertEqual(self.classify(event), classification)
        self.assertEqual(repair_control._load_control(self.run_dir, "FEAT-001")["status"], "resolved")

    def test_validation_route_retains_prepared_and_unproven_controls(self) -> None:
        payload = failure_payload(proposed="VALIDATION_DEFECT", lifecycle="waves_completed")
        payload["phase"] = "sdlc-validate-codes"
        classification = self.classify(self.record_event(payload))
        self.assertEqual(classification["next_recommended_skill"], "sdlc-validate-codes")
        self.assertIsNone(classification["corrective_mode"])
        payload = failure_payload(
            proposed="VALIDATION_DEFECT", lifecycle="unprepared", cause_status="probable",
        )
        payload["phase"] = "sdlc-create-design"
        classification = self.classify(self.record_event(payload))
        self.assertEqual(classification["status"], "diagnosis_required")
        self.assertEqual(classification["next_recommended_skill"], "troubleshoot")

    def test_execution_cli_classifies_recorded_failure(self) -> None:
        event = self.record_event()
        output = io.StringIO()
        with mock.patch.object(sys, "argv", [
            "repair_control.py", "classify", "--run-dir", str(self.run_dir),
            "--execution", "FEAT-001", "--event", event["event_id"],
        ]), mock.patch("sys.stdout", output):
            self.assertEqual(repair_control.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["classification"]["classification"], "IMPLEMENTATION_DEFECT")
        self.assertEqual(result["classification"]["execution_id"], "FEAT-001")

    def test_execution_cli_routes_repair_actions(self) -> None:
        cases = [
            ("refresh-revalidation-routes", "refresh_revalidation_routes",
             ["--expected-cursor", "cursor", "--evidence-reference", "receipt"]),
            ("dispatch", "begin_remediation", ["--classification", "classification",
             "--remedy-scale", "localized", "--hypothesis", "proven owner defect",
             "--evidence-reference", "receipt"]),
            ("complete", "complete_remediation", ["--dispatch", "dispatch",
             "--result", "succeeded", "--verification-reference", "receipt"]),
        ]
        for command, owner, options in cases:
            with self.subTest(command=command), mock.patch.object(sys, "argv", [
                "repair_control.py", command, "--run-dir", str(self.run_dir),
                "--execution", "SLICE-002", *options,
            ]), mock.patch.object(repair_control, owner, return_value={}) as call, \
                    mock.patch("sys.stdout", io.StringIO()):
                self.assertEqual(repair_control.main(), 0)
                self.assertEqual(call.call_args.args[:2], (self.run_dir, "SLICE-002"))

    def environment_recovery_fixture(
        self, proposed="ENVIRONMENT_DEFECT", *, phase="evaluation", surface="evaluation"
    ):
        integration = self.run_dir / "worktrees" / "FEAT-001" / "integration"
        integration.mkdir(parents=True)
        for arguments in (
            ["init", "-b", "integration"],
            ["config", "user.name", "Repair Test"],
            ["config", "user.email", "repair@example.com"],
            ["commit", "--allow-empty", "-m", "baseline"],
        ):
            subprocess.run(
                ["git", *arguments], cwd=integration, check=True, capture_output=True
            )
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=integration, text=True
        ).strip()
        failure = failure_payload(proposed=proposed, commit=head)
        failure["phase"] = phase
        event = self.record_event(failure)
        classification = self.classify(event)
        coordinator = self.run_dir / "execution/FEAT-001/coordinator.json"
        coordinator.parent.mkdir(parents=True)
        coordinator.write_text(
            json.dumps(
                {
                    "schema": "workflow/execution-coordinator-v3",
                    "status": "integrated",
                    "active_wave": None,
                    "cleanup_retained": [],
                    "integration_worktree": str(integration),
                    "integration_head": head,
                    "integration_branch": "integration",
                }
            )
        )
        (self.run_dir / "current-state.json").write_text(
            json.dumps(
                {
                    "fingerprint_ids": [
                        f"{k}:{v}" for k, v in event["fingerprints"].items()
                    ]
                }
            )
        )
        payload = revalidation_payload(self.run_dir, surface, 0, commit=head)
        payload.update(
            schema="agentic-sdlc/environment-recovery-v2",
            event_id=event["event_id"],
            classification_id=classification["classification_id"],
        )
        return event, classification, payload, integration

    def test_environment_phase_name_can_recover_its_canonical_gate(self):
        event, classification, payload, _ = self.environment_recovery_fixture(
            phase="sdlc-unit-tests", surface="tests"
        )
        result = repair_control.record_environment_recovery(self.run_dir, payload)
        self.assertEqual(classification["invalidates"], ["tests"])
        self.assertEqual(result["control"]["status"], "resolved")
        self.assertEqual(result["recovery"]["next_recommended_skill"], "sdlc-unit-tests")
        self.assertEqual(
            repair_control._load_event(self.run_dir, "FEAT-001", event["event_id"]),
            event,
        )
        self.assertEqual(event["phase"], "sdlc-unit-tests")

    def test_environment_phase_name_preserves_gate_on_independent_failure(self):
        failure = failure_payload(proposed="ENVIRONMENT_DEFECT")
        failure["phase"] = "sdlc-unit-tests"
        event = self.record_event(failure)
        original = self.classify(event)
        payload = failure_payload(
            proposed="VALIDATION_DEFECT", component="checker",
            operation="parse checker options", error_class="invalid boolean",
            source_boundary="checker invocation",
            independent="The checker rejects its options before reading product code.",
        )
        payload["phase"] = "sdlc-validate-codes"
        classification = self.classify(self.record_event(payload))
        self.assertEqual(classification["invalidates"], ["tests"])
        self.assertEqual(classification["inherited_invalidations"], [{
            "event_id": event["event_id"],
            "classification_id": original["classification_id"],
            "surface": "tests",
        }])
        self.assertEqual(
            repair_control._load_classification(
                self.run_dir, "FEAT-001", original["classification_id"]
            ), original,
        )

    def test_unsupported_environment_phase_retains_prior_obligations(self):
        for phase in ("sdlc-tdd", "sdlc-gui-test", "sdlc-uat-tests", "unknown-phase"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                run = Path(temporary)
                first = repair_control.record_failure_event(run, failure_payload())["event"]
                original = repair_control.classify_failure(run, "FEAT-001", first["event_id"])["classification"]
                prior = repair_control._load_control(run, "FEAT-001")["invalidations"]
                payload = failure_payload(
                    proposed="ENVIRONMENT_DEFECT", component="environment",
                    operation="open fixture", error_class="fixture unavailable",
                    source_boundary="fixture host",
                    independent="The fixture is independently unavailable.",
                )
                payload["phase"] = phase
                event = repair_control.record_failure_event(run, payload)["event"]
                blocked = repair_control.classify_failure(run, "FEAT-001", event["event_id"])["classification"]
                self.assertEqual(blocked["status"], "blocked")
                self.assertIsNone(blocked["corrective_mode"])
                self.assertEqual(blocked["invalidates"], [])
                self.assertEqual(blocked["inherited_invalidations"], prior)
                self.assertEqual(repair_control._load_control(run, "FEAT-001")["invalidations"], prior)
                diagnosis = repair_control.record_diagnosis(run, localized_diagnosis(event))["diagnosis"]
                resumed = repair_control.classify_failure(
                    run, "FEAT-001", event["event_id"], diagnosis["diagnosis_id"]
                )["classification"]
                self.assertEqual(resumed["invalidates"], original["invalidates"])
                self.assertEqual(resumed["inherited_invalidations"], prior)

    def test_environment_gate_recovery_preserves_failure_and_budget(self):
        event, classification, payload, _ = self.environment_recovery_fixture()
        before = repair_control._load_control(self.run_dir, "FEAT-001")
        result = repair_control.record_environment_recovery(self.run_dir, payload)
        self.assertTrue(result["created"])
        self.assertEqual(result["control"]["status"], "resolved")
        for key in ("route_history", "feature_dispatches"):
            self.assertEqual(result["control"][key], before[key])
        self.assertEqual(result["recovery"]["invalidations"], before["invalidations"])
        self.assertEqual(result["control"]["invalidations"], [])
        self.assertEqual(result["control"]["active_blocker"]["total_attempts"], 0)
        self.assertEqual(
            repair_control._load_event(self.run_dir, "FEAT-001", event["event_id"]),
            event,
        )
        self.assertEqual(
            repair_control._load_classification(
                self.run_dir, "FEAT-001", classification["classification_id"]
            ),
            classification,
        )
        replay = repair_control.record_environment_recovery(self.run_dir, payload)
        self.assertFalse(replay["created"])
        self.assertEqual(replay["control"], result["control"])

        # Exercise the actual existing Stop consumer with a real repair pointer.
        hook = MODULE_PATH.parents[2] / "sdlc-start/assets/hooks/stop_sdlc_continue.py"
        code = (
            "import importlib.util,sys,types; "
            "s=importlib.util.spec_from_file_location('environment_stop_probe',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "a=types.SimpleNamespace(run_dir=m.Path(sys.argv[2])); "
            "state={'current_execution':'FEAT-001','next_recommended_skill':'sdlc-update-documents',"
            "'repair':{'schema':'agentic-sdlc/repair-state-pointer-v1',"
            "'control':'repairs/FEAT-001/repair-control.json','failure_event':sys.argv[3]}}; "
            "assert m._repair_stop_reason(a,state) is None"
        )
        subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(hook),
                str(self.run_dir),
                f"repairs/FEAT-001/events/{event['event_id']}.json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_environment_recovery_rejects_non_environment_classification(self):
        _, _, payload, _ = self.environment_recovery_fixture("POLICY_BLOCK")
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.record_environment_recovery(self.run_dir, payload)

    def test_environment_recovery_preserves_other_pending_invalidations(self):
        _, _, payload, _ = self.environment_recovery_fixture()
        path = repair_control._control_path(self.run_dir, "FEAT-001")
        control = json.loads(path.read_text())
        control["invalidations"].append({"surface": "tests", "event_id": "other"})
        path.write_text(json.dumps(control))
        before = path.read_bytes()
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.record_environment_recovery(self.run_dir, payload)
        self.assertEqual(path.read_bytes(), before)

    def test_environment_recovery_rejects_stale_identity_and_dirty_git(self):
        _, _, payload, integration = self.environment_recovery_fixture()
        for key, value in (
            ("event_id", "f" * 64),
            ("classification_id", "f" * 64),
            ("integration_commit", "f" * 40),
            ("surface", "validation"),
            ("evidence_digest", "f" * 64),
        ):
            with (
                self.subTest(key=key),
                self.assertRaises(repair_control.RepairControlError),
            ):
                repair_control.record_environment_recovery(
                    self.run_dir, dict(payload, **{key: value})
                )
        (integration / "dirty.txt").write_text("uncommitted")
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.record_environment_recovery(self.run_dir, payload)

    def test_environment_recovery_rejects_failed_gate_and_changed_fingerprints(self):
        _, _, payload, _ = self.environment_recovery_fixture()
        evidence = self.run_dir / payload["evidence_reference"]
        gate = json.loads(evidence.read_text())
        gate["status"] = "failed"
        evidence.write_text(json.dumps(gate))
        payload["evidence_digest"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.record_environment_recovery(self.run_dir, payload)
        gate["status"] = "passed"
        evidence.write_text(json.dumps(gate))
        payload["evidence_digest"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
        state = self.run_dir / "current-state.json"
        state.write_text(state.read_text().replace(digest("2"), digest("9")))
        with self.assertRaises(repair_control.RepairControlError):
            repair_control.record_environment_recovery(self.run_dir, payload)

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
        second = repair_control.build_failure_event(failure_payload(commit="b" * 40))
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["blocker_key"], second["blocker_key"])

    def test_failed_direct_repair_requires_troubleshooting_before_attempt_two(
        self,
    ) -> None:
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

        approval_statement = "I approve this broader public-contract design change."
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
        diagnosis = repair_control.record_diagnosis(self.run_dir, authorized)[
            "diagnosis"
        ]
        classification = self.classify(event, diagnosis)
        self.assertEqual(classification["design_admission"]["approval_mode"], "human")
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

    def test_taxonomy_routes_test_spec_evaluator_environment_policy_and_human(
        self,
    ) -> None:
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
        self._exercise_ordered_revalidation()

    def test_independent_checker_repair_retains_prior_gate_obligations(self) -> None:
        original = self.classify(self.record_event())
        dispatched = repair_control.begin_remediation(
            self.run_dir, "FEAT-001", original["classification_id"], "localized",
            "Repair the response mapping.", "evidence/mapping-proof.json",
        )
        first = repair_control.complete_remediation(
            self.run_dir, "FEAT-001", dispatched["attempt"]["dispatch_id"],
            "succeeded", "evidence/mapping-oracle.json",
        )["control"]
        old_cursor = first["revalidation"]
        payload = failure_payload(
            proposed="VALIDATION_DEFECT", component="checker",
            operation="parse checker options", error_class="invalid boolean",
            source_boundary="checker invocation",
            independent="The checker rejects its options before reading product code.",
        )
        payload["phase"] = "sdlc-validate-codes"
        event = self.record_event(payload)
        second = self.classify(event)
        self.assertEqual(second["classification"], "VALIDATION_DEFECT")
        self.assertEqual(second["invalidates"], original["invalidates"])
        self.assertTrue(second["inherited_invalidations"])
        dispatched = repair_control.begin_remediation(
            self.run_dir, "FEAT-001", second["classification_id"], "localized",
            "Correct the checker boolean option.", "evidence/checker-options.json",
        )
        result = repair_control.complete_remediation(
            self.run_dir, "FEAT-001", dispatched["attempt"]["dispatch_id"],
            "succeeded", "evidence/original-checker-oracle.json",
        )
        control = result["control"]
        self.assertEqual(control["status"], "revalidation_required")
        self.assertEqual(control["revalidation"]["required"], old_cursor["required"])
        self.assertNotEqual(control["revalidation"]["cursor_id"], old_cursor["cursor_id"])
        self.assertEqual(control["revalidation"]["completed_revalidation_ids"], [])
        self.assertIsNone(control["revalidation"]["integration_commit"])
        self.assertEqual(control["feature_dispatches"], 2)
        self.assertEqual(control["active_blocker"]["total_attempts"], 1)
        archives = list((self.run_dir / "repairs/FEAT-001/cursor-archives").glob("*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(json.loads(archives[0].read_text())["previous"], old_cursor)
        self.assertEqual(self.classify(event), second)
        self.assertEqual(repair_control._load_control(self.run_dir, "FEAT-001"), control)

    def _complete_localized(self, classification, label, result="succeeded"):
        dispatched = repair_control.begin_remediation(
            self.run_dir, "FEAT-001", classification["classification_id"], "localized",
            f"Repair the independently established {label} cause.", f"evidence/{label}.json",
        )
        return repair_control.complete_remediation(
            self.run_dir, "FEAT-001", dispatched["attempt"]["dispatch_id"],
            result, f"evidence/{label}-verification.json",
        )

    def test_prior_partial_and_complete_receipts_remain_historical(self) -> None:
        for count in (1, 6):
            with self.subTest(completed_gates=count), tempfile.TemporaryDirectory() as temporary:
                self.run_dir = Path(temporary) / "run"
                payload = failure_payload()
                original = self.classify(self.record_event(payload))
                first = self._complete_localized(original, "mapping")["control"]
                with mock.patch.object(repair_control, "_current_revalidation_commit", return_value="a" * 40), mock.patch.object(
                    repair_control, "_current_fingerprints", return_value=payload["fingerprints"]
                ):
                    for index, required in enumerate(first["revalidation"]["required"][:count]):
                        first = repair_control.record_revalidation(self.run_dir, revalidation_payload(
                            self.run_dir, required["surface"], index,
                            commit="a" * 40, fingerprints=payload["fingerprints"],
                        ))["control"]
                    old_cursor = first["revalidation"]
                    old_bytes = {identity: repair_control._revalidation_path(
                        self.run_dir, "FEAT-001", identity
                    ).read_bytes() for identity in old_cursor["completed_revalidation_ids"]}
                    # The same causal blocker recurs with a new event. Its budget is retained.
                    payload["observed"] = "A distinct response branch violates the same mapping invariant."
                    event = self.record_event(payload)
                    new = self.classify(event)
                    result = self._complete_localized(new, "second-mapping")
                    control = result["control"]
                    self.assertEqual(control["active_blocker"]["total_attempts"], 2)
                    self.assertEqual(control["revalidation"]["cursor"], 0)
                    self.assertIsNone(control["revalidation"]["integration_commit"])
                    self.assertEqual(control["revalidation"]["completed_revalidation_ids"], [])
                    current = repair_control.record_revalidation(self.run_dir, revalidation_payload(
                        self.run_dir, "validation", 7, commit="a" * 40,
                        fingerprints=payload["fingerprints"],
                    ))["control"]
                    self.assertEqual(self.record_event(payload), event)
                    self.assertEqual(self.classify(event), new)
                    replay = self._complete_localized(new, "second-mapping")
                    self.assertFalse(replay["updated"])
                    self.assertEqual(replay["control"], current)
                archives = list((self.run_dir / "repairs/FEAT-001/cursor-archives").glob("*.json"))
                self.assertEqual(len(archives), 1)
                self.assertEqual(json.loads(archives[0].read_text())["previous"], old_cursor)
                for identity, data in old_bytes.items():
                    self.assertEqual(repair_control._revalidation_path(
                        self.run_dir, "FEAT-001", identity
                    ).read_bytes(), data)

    def test_new_earlier_gates_are_ordered_and_failed_repair_keeps_obligations(self) -> None:
        original = self.classify(self.record_event())
        self._complete_localized(original, "mapping")
        payload = failure_payload(
            proposed="PLAN_DEFECT", component="plan",
            independent="A missing plan dependency is independent of the response mapper.",
        )
        new = self.classify(self.record_event(payload))
        self.assertEqual(new["invalidates"], [
            "execution_preparation", "implementation", "validation", "tests",
            "evaluation", "documentation", "alignment", "commit",
        ])
        result = self._complete_localized(new, "plan-dependency", "failed_same_blocker")
        self.assertEqual(result["control"]["status"], "diagnosis_required")
        self.assertTrue(result["control"]["invalidations"])
        self.assertEqual(len(list((self.run_dir / "repairs/FEAT-001/cursor-archives").glob("*.json"))), 1)

    def test_tampered_inherited_reference_is_rejected_before_classification(self) -> None:
        original = self.classify(self.record_event())
        self._complete_localized(original, "mapping")
        event = self.record_event(failure_payload(
            proposed="VALIDATION_DEFECT", component="checker",
            independent="Checker parsing fails independently before reading source.",
        ))
        path = repair_control._control_path(self.run_dir, "FEAT-001")
        baseline = path.read_bytes()
        for field, value in (("surface", "requirements"), ("event_id", "f" * 64),
                             ("classification_id", "../outside")):
            with self.subTest(field=field):
                control = json.loads(baseline)
                control["invalidations"][0][field] = value
                path.write_text(json.dumps(control))
                with self.assertRaises(repair_control.RepairControlError) as denied:
                    self.classify(event)
                self.assertEqual(denied.exception.code, "STATE_TAMPERED")
                self.assertEqual(json.loads(path.read_text()), control)
        path.write_bytes(baseline)

    def test_new_diagnosis_archives_cursor_before_reclassification(self) -> None:
        event = self.record_event()
        original = self.classify(event)
        before = self._complete_localized(original, "mapping")["control"]
        diagnosis = self.record_localized(event)
        new = self.classify(event, diagnosis)
        self.assertEqual(new["invalidates"], original["invalidates"])
        archives = list((self.run_dir / "repairs/FEAT-001/cursor-archives").glob("*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(json.loads(archives[0].read_text())["previous"], before["revalidation"])

    def test_route_refresh_invalidates_progress_preserves_audit_and_replays(
        self,
    ) -> None:
        self._exercise_ordered_revalidation(route_refresh=True)

    def _exercise_ordered_revalidation(self, route_refresh: bool = False) -> None:
        self.assertEqual(repair_control.REVALIDATION_ROUTES["alignment"], "align")
        old_policy = mock.patch.dict(
            repair_control.REVALIDATION_ROUTES, {"alignment": "retired-test-owner"}
        )
        if route_refresh:
            old_policy.start()
            self.addCleanup(old_policy.stop)
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

        integration = self.run_dir / "worktrees" / "FEAT-001" / "integration"
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
        coordinator_path = self.run_dir / "execution" / "FEAT-001" / "coordinator.json"
        coordinator_path.parent.mkdir(parents=True)
        coordinator_path.write_text(
            json.dumps(
                {
                    "schema": "workflow/execution-coordinator-v3",
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
        self.assertEqual(out_of_order.exception.code, "REVALIDATION_OUT_OF_ORDER")

        if route_refresh:
            original = repair_control.record_revalidation(
                self.run_dir,
                revalidation_payload(
                    self.run_dir,
                    "validation",
                    1,
                    commit=integration_commit,
                    fingerprints=current_fingerprints,
                ),
            )
            old_control = original["control"]
            old_cursor = old_control["revalidation"]["cursor_id"]
            arguments = (
                self.run_dir,
                "FEAT-001",
                old_cursor,
                "evidence/route-repair.md",
            )
            # No route drift is not a general-purpose reset API.
            with self.assertRaises(repair_control.RepairControlError):
                repair_control.refresh_revalidation_routes(*arguments)
            old_policy.stop()
            with self.assertRaises(repair_control.RepairControlError):
                repair_control.record_revalidation(
                    self.run_dir,
                    revalidation_payload(
                        self.run_dir,
                        "tests",
                        2,
                        commit=integration_commit,
                        fingerprints=current_fingerprints,
                    ),
                )
            control_path = repair_control._control_path(self.run_dir, "FEAT-001")
            for mutation in ("digest", "attempt", "progress"):
                tampered = json.loads(json.dumps(old_control))
                if mutation == "digest":
                    tampered["revalidation"]["required"][0]["surface"] = "commit"
                elif mutation == "attempt":
                    tampered["active_blocker"]["attempts"][0]["result"] = (
                        "failed_same_blocker"
                    )
                else:
                    tampered["revalidation"]["cursor"] = 3
                control_path.write_text(json.dumps(tampered), encoding="utf-8")
                with self.assertRaises(repair_control.RepairControlError):
                    repair_control.refresh_revalidation_routes(*arguments)
                control_path.write_text(json.dumps(old_control), encoding="utf-8")
            refreshed = repair_control.refresh_revalidation_routes(*arguments)
            control = refreshed["control"]
            self.assertTrue(refreshed["updated"])
            self.assertEqual(control["active_blocker"], old_control["active_blocker"])
            self.assertEqual(
                control["feature_dispatches"], old_control["feature_dispatches"]
            )
            self.assertEqual(control["revalidation"]["cursor"], 0)
            self.assertEqual(control["revalidation"]["completed_revalidation_ids"], [])
            archive = (
                self.run_dir
                / "repairs"
                / "FEAT-001"
                / "route-refreshes"
                / f"{old_cursor}.json"
            )
            self.assertEqual(
                json.loads(archive.read_text())["previous"], old_control["revalidation"]
            )
            self.assertTrue(
                repair_control._revalidation_path(
                    self.run_dir,
                    "FEAT-001",
                    original["revalidation"]["revalidation_id"],
                ).is_file()
            )
            with self.assertRaises(repair_control.RepairControlError):
                repair_control.refresh_revalidation_routes(
                    self.run_dir, "FEAT-001", "0" * 64, "evidence/route-repair.md"
                )

        first_payload = None
        first_revalidation_id = None
        promoted_project = Path(self.temporary.name) / "promoted-project"
        for index, required in enumerate(control["revalidation"]["required"], start=1):
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
                            "schema": "workflow/execution-coordinator-v3",
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
                with self.assertRaises(repair_control.RepairControlError) as detached:
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
            if route_refresh:
                replay_refresh = repair_control.refresh_revalidation_routes(*arguments)
                self.assertFalse(replay_refresh["updated"])
                self.assertEqual(replay_refresh["control"], result["control"])
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
        journal_path = self.run_dir / "repairs" / "FEAT-001" / "repair-journal.jsonl"
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
        journal_path = self.run_dir / "repairs" / "FEAT-001" / "repair-journal.jsonl"
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
        journal_path = self.run_dir / "repairs" / "FEAT-001" / "repair-journal.jsonl"
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
        self.assertEqual(localized_block.exception.code, "REPAIR_BUDGET_EXHAUSTED")

        design = repair_control.record_diagnosis(self.run_dir, design_diagnosis(event))[
            "diagnosis"
        ]
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
                self.assertEqual(blocked.exception.code, "REPAIR_BUDGET_EXHAUSTED")
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

    def test_exhausted_observation_replay_is_atomic(self) -> None:
        self.record_event()
        args = (self.run_dir, "FEAT-001", "Which boundary?", "Mapper defect.",
                "evidence/observation-1", "decision_changing")
        for _ in range(2):
            with self.assertRaises(repair_control.RepairControlError) as error:
                repair_control.record_experiment(*args, active_seconds_delta=3600)
            self.assertEqual(error.exception.code, "REPAIR_BUDGET_EXHAUSTED")
            control = repair_control._load_control(self.run_dir, "FEAT-001")
            self.assertEqual(control["active_blocker"]["active_seconds"], 3600)
            self.assertEqual(len(control["active_blocker"]["experiments"]), 1)
            self.assertEqual(control["active_blocker"]["experiments"][0]["outcome"], "exhausted")

    def test_diagnostic_time_rejects_malformed_and_changed_replay(self) -> None:
        self.record_event()
        args = (self.run_dir, "FEAT-001", "Which boundary?", "Mapper defect.",
                "evidence/observation-1", "decision_changing")
        path = repair_control._control_path(self.run_dir, "FEAT-001")
        before = path.read_bytes()
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(repair_control.RepairControlError):
                repair_control.record_experiment(*args, active_seconds_delta=value)
            self.assertEqual(path.read_bytes(), before)
        repair_control.record_experiment(*args, active_seconds_delta=5)
        with self.assertRaisesRegex(repair_control.RepairControlError, "observation accounting changed"):
            repair_control.record_experiment(*args, active_seconds_delta=6)

    def test_diagnostic_intervals_charge_union(self) -> None:
        self.record_event()
        for index, intervals in enumerate(([[10, 40], [20, 50]], [[30, 60]])):
            result = repair_control.record_experiment(
                self.run_dir, "FEAT-001", f"Question {index}.", "Mapper defect.",
                f"evidence/observation-{index}", "decision_changing",
                active_intervals=intervals,
            )
        self.assertEqual(result["control"]["active_blocker"]["active_seconds"], 50)

    def test_third_low_information_observation_replays(self) -> None:
        self.record_event()
        for index in (1, 2, 3, 3):
            result = repair_control.record_experiment(
                self.run_dir, "FEAT-001", f"Question {index}.", "Mapper defect.",
                f"evidence/observation-{index}", "low", active_seconds_delta=2,
            )
        self.assertFalse(result["created"])
        self.assertEqual(result["control"]["active_blocker"]["active_seconds"], 6)

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
        self.assertFalse(rebuilt["control"]["active_blocker"]["model_rebuild_required"])

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

    def test_documentation_drift_respects_terminal_execution(self) -> None:
        for lifecycle in ("sealed", "promoted", "completed"):
            classification = repair_control._classification_from(
                failure_payload(proposed="DOCUMENTATION_DRIFT", lifecycle=lifecycle), None)
            with self.subTest(lifecycle=lifecycle):
                self.assertEqual(classification["next_recommended_skill"], "sdlc-create-plan")
                self.assertEqual(classification["corrective_mode"], "new_corrective_run_from_promoted_commit")
                self.assertIn("documentation", classification["invalidates"])
                self.assertIn("commit", classification["invalidates"])
        before = repair_control._classification_from(
            failure_payload(proposed="DOCUMENTATION_DRIFT", lifecycle="waves_completed"), None)
        self.assertEqual(before["next_recommended_skill"], "sdlc-update-documents")

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
