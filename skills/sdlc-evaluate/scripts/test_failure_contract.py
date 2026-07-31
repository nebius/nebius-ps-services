#!/usr/bin/env python3
"""Tests for normalized Agentic SDLC evaluation failure emission."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("failure_contract.py")
SPEC = importlib.util.spec_from_file_location("evaluation_failure_contract", MODULE_PATH)
assert SPEC and SPEC.loader
failure_contract = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(failure_contract)


def digest(character: str) -> str:
    return f"sha256:{character * 64}"


def payload(
    *,
    cause_status: str = "ambiguous",
    proven_cause: str | None = None,
    commit: str = "a" * 40,
) -> dict[str, object]:
    return {
        "schema": failure_contract.INPUT_SCHEMA,
        "feature_id": "FEAT-001",
        "criterion_id": "AC-001",
        "expected": "The user-visible response is accepted.",
        "observed": "The user-visible response is rejected.",
        "evidence_digests": {"evaluate": digest("1")},
        "reproduction": "Run the bounded AC-001 evaluator oracle.",
        "integration_commit": commit,
        "fingerprints": {
            "requirements": digest("2"),
            "design": digest("3"),
            "plan": digest("4"),
        },
        "component": "evaluation adapter",
        "operation": "grade AC-001",
        "error_class": "acceptance mismatch",
        "source_boundary": "user-visible response",
        "cause_status": cause_status,
        "proven_cause": proven_cause,
        "execution_lifecycle": "waves_completed",
        "created_at": "2026-07-28T12:00:00Z",
    }


class FailureContractTests(unittest.TestCase):
    def test_ambiguous_failure_requires_diagnosis(self) -> None:
        result = failure_contract.normalize_evaluation_failure(payload())
        self.assertEqual(result["disposition"], "diagnosis_required")
        self.assertIsNone(
            result["failure_event"]["proposed_classification"]
        )

    def test_proven_mechanical_owner_bypasses_diagnosis(self) -> None:
        result = failure_contract.normalize_evaluation_failure(
            payload(cause_status="proven", proven_cause="IMPLEMENTATION_DEFECT")
        )
        self.assertEqual(result["disposition"], "proven_owner")
        self.assertEqual(
            result["failure_event"]["proposed_classification"],
            "IMPLEMENTATION_DEFECT",
        )

    def test_event_binds_commit_evidence_and_fingerprints(self) -> None:
        result = failure_contract.normalize_evaluation_failure(payload())
        event = result["failure_event"]
        self.assertEqual(event["integration_commit"], "a" * 40)
        self.assertEqual(event["evidence_digests"]["evaluate"], digest("1"))
        self.assertEqual(event["fingerprints"]["requirements"], digest("2"))
        changed = failure_contract.normalize_evaluation_failure(
            payload(commit="b" * 40)
        )["failure_event"]
        self.assertNotEqual(event["event_id"], changed["event_id"])
        self.assertEqual(event["blocker_key"], changed["blocker_key"])

    def test_proven_cause_must_name_supported_owner(self) -> None:
        with self.assertRaisesRegex(ValueError, "supported owner"):
            failure_contract.normalize_evaluation_failure(
                payload(cause_status="proven", proven_cause=None)
            )

    def test_emit_is_idempotent_and_creates_repair_control(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            first = failure_contract.emit_evaluation_failure(run_dir, payload())
            second = failure_contract.emit_evaluation_failure(run_dir, payload())
            self.assertTrue(first["created"])
            self.assertFalse(second["created"])
            self.assertEqual(
                first["failure_event"]["event_id"],
                second["failure_event"]["event_id"],
            )
            self.assertEqual(
                second["repair_control"]["schema"],
                "agentic-sdlc/repair-control-v1",
            )


if __name__ == "__main__":
    unittest.main()
