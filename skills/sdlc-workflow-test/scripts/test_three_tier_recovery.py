#!/usr/bin/env python3
"""Inventory recovery must consume existing owner records, never infer success."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(ROOT / "sdlc-classify-failure/scripts"))
import repair_control as owner  # noqa: E402
import three_tier_target as target  # noqa: E402


def private_json(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_text(json.dumps(value))
    path.chmod(0o600)


class InventoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.temporary.cleanup)
        boundary = Path(self.temporary.name).resolve()
        self.run = boundary / "workflow/run"
        self.run.mkdir(parents=True, mode=0o700)
        self.state = {"run_root": str(boundary)}
        self.digest = "a" * 64
        event = owner.record_failure_event(
            self.run,
            {
                "schema": owner.FAILURE_EVENT_SCHEMA,
                "execution_id": "FEAT-001",
                "phase": "sdlc-implement-plan",
                "criterion_id": "AC-001",
                "expected": "Owned services share a network.",
                "observed": "The network differs.",
                "evidence_digests": {"inventory": self.digest},
                "reproduction": "Observe the owned runtime.",
                "integration_commit": "b" * 40,
                "fingerprints": {
                    "requirements": "c" * 64,
                    "design": "d" * 64,
                    "plan": "e" * 64,
                },
                "component": "runtime",
                "operation": "inventory",
                "error_class": "network mismatch",
                "source_boundary": "container network",
                "cause_status": "proven",
                "proposed_classification": "ENVIRONMENT_DEFECT",
                "execution_lifecycle": "active_task",
                "independent_blocker_evidence": None,
                "created_at": "2026-11-03T10:00:00Z",
            },
        )["event"]
        classification = owner.classify_failure(
            self.run, "FEAT-001", event["event_id"]
        )["classification"]
        attempt = owner.begin_remediation(
            self.run,
            "FEAT-001",
            classification["classification_id"],
            "localized",
            "Restore the declared service network.",
            "evidence/diagnosis.json",
        )["attempt"]
        self.dispatch = attempt["dispatch_id"]
        self.verification = self.run / "evidence/recovery.json"
        private_json(
            self.verification,
            {"outcome": "passed", "operation": "original inventory oracle"},
        )
        self.failed = [
            (
                {"sha256": self.digest},
                {
                    "target": {
                        "head": "b" * 40,
                        "run_id": "run",
                        "execution_id": "FEAT-001",
                    }
                },
            )
        ]
        self.registration = mock.patch.object(
            target,
            "_registration",
            return_value=(
                None,
                None,
                None,
                None,
                self.run,
                {"current_execution": "FEAT-002"},
                {},
            ),
        )
        self.registration.start()
        self.addCleanup(self.registration.stop)

    def resolve(self):
        return target.inventory_recovery_evidence(
            self.state, self.failed, skills_root=ROOT, agent="codex"
        )

    def complete(self):
        return owner.complete_remediation(
            self.run, "FEAT-001", self.dispatch, "succeeded", "evidence/recovery.json"
        )

    def test_success_requires_owner_completion_and_tracks_original_feature(self):
        with self.assertRaises(target.TargetError):
            self.resolve()
        self.complete()
        result = self.resolve()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["observation_sha256"], self.digest)
        self.assertEqual(result[0]["dispatch_id"], self.dispatch)
        self.assertEqual(
            owner._load_control(self.run, "FEAT-001")["status"], "revalidation_required"
        )

    def test_tampered_recovery_evidence_cannot_be_rebound(self):
        self.complete()
        self.state["runtime_recoveries"] = self.resolve()
        private_json(self.verification, {"outcome": "changed"})
        with self.assertRaisesRegex(target.TargetError, "changed after admission"):
            self.resolve()

    def test_fabricated_success_without_completion_journal_is_rejected(self):
        self.complete()
        journal = self.run / "repairs/FEAT-001/repair-journal.jsonl"
        lines = [
            line
            for line in journal.read_text().splitlines()
            if json.loads(line)["event"] != "remediation-completion-committed"
        ]
        journal.write_text("\n".join(lines) + "\n")
        with self.assertRaisesRegex(target.TargetError, "completion is unproven"):
            self.resolve()

    def test_wrong_receipt_digest_and_tampered_classification_fail(self):
        self.complete()
        self.failed[0][0]["sha256"] = "f" * 64
        with self.assertRaises(target.TargetError):
            self.resolve()
        self.failed[0][0]["sha256"] = self.digest
        path = next((self.run / "repairs/FEAT-001/classifications").glob("*.json"))
        data = json.loads(path.read_text())
        data["status"] = "blocked"
        private_json(path, data)
        with self.assertRaises(target.TargetError):
            self.resolve()


if __name__ == "__main__":
    unittest.main()
