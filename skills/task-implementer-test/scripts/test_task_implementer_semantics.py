#!/usr/bin/env python3

import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import task_implementer_semantics as semantics

from task_implementer_semantics import (
    APPLICATION_KEYS,
    ORCHESTRATION_KEYS,
    _assignment_context_errors,
    _dependency_contract_errors,
    validate_results,
)


def manifest(generation: str, digest: str, status: str = "PARTIAL") -> dict:
    return {
        "schema": "task-implementer-test/live-results-v1",
        "generation_id": generation,
        "status": status,
        "project_head": "a" * 40,
        "orchestration": {key: True for key in ORCHESTRATION_KEYS},
        "application": {key: True for key in APPLICATION_KEYS},
        "artifacts": {
            "application": {
                "path": "evidence/application.json",
                "sha256": digest,
            }
        },
    }


def application(generation: str) -> dict:
    task = {"id": 7, "title": "Verifier task", "completed": True}
    return {
        "schema": "task-implementer-test/application-evidence-v1",
        "generation_id": generation,
        "services": ["api", "db", "frontend"],
        "web_port": 49152,
        "frontend_body_sha256": "b" * 64,
        "created_task": task,
        "database_task": task,
        "persisted_after_restart": True,
    }


class SemanticsTests(unittest.TestCase):
    def test_assignment_context_is_bound_to_the_canonical_task(self) -> None:
        task = {
            "dependencies": ["task-1"],
            "goal": "Integrate the tiers",
            "plan": "Wire the runtime",
            "implementation_steps": ["write compose.yaml", "add runtime checks"],
            "validation": ["validate strict JSON"],
            "end_to_end_validation": ["exercise the complete task flow"],
            "done_criteria": ["all tiers are connected"],
        }
        liveness = {
            "worker_profile": "integration",
            "read_only_warning_seconds": 360,
            "read_only_seconds": 420,
        }
        assignment = {
            **task,
            **liveness,
        }
        self.assertEqual(_assignment_context_errors(task, assignment, liveness), [])
        assignment["dependencies"] = []
        assignment["implementation_steps"] = ["reread coordinator state"]
        errors = _assignment_context_errors(task, assignment, liveness)
        self.assertIn(
            "assignment dependencies does not match its canonical task", errors
        )
        self.assertIn(
            "assignment implementation_steps does not match its canonical task",
            errors,
        )

    def test_dependency_contract_requires_disjoint_tiers_and_integration_dependencies(
        self,
    ) -> None:
        tiers = [
            {
                "task_id": f"task-{index}",
                "dependencies": [],
                "write_claims": [{"kind": "prefix", "path": path}],
            }
            for index, path in enumerate(
                ("app/frontend", "app/api", "app/database"), start=1
            )
        ]
        integration = {
            "task_id": "task-4",
            "dependencies": ["task-1", "task-2", "task-3"],
            "write_claims": [{"kind": "exact", "path": "compose.yaml"}],
        }
        waves = [{"tasks": tiers}, {"tasks": [integration]}]
        self.assertEqual(_dependency_contract_errors(waves), [])
        integration["dependencies"] = ["task-1"]
        self.assertIn(
            "integration task must depend on every first-wave tier task",
            _dependency_contract_errors(waves),
        )
        integration["dependencies"] = ["task-1", "task-2", "task-3"]
        tiers[1]["write_claims"] = [{"kind": "prefix", "path": "app/frontend/api"}]
        self.assertIn(
            "first-wave task write claims overlap",
            _dependency_contract_errors(waves),
        )
        tiers[0]["write_claims"] = [{"kind": "prefix", "path": "app/frontend/nested"}]
        tiers[1]["write_claims"] = [{"kind": "prefix", "path": "app/frontend"}]
        self.assertIn(
            "first-wave task write claims overlap",
            _dependency_contract_errors(waves),
        )

    def test_dependency_contract_requires_three_distinct_tier_owners(self) -> None:
        combined = {
            "task_id": "task-1",
            "dependencies": [],
            "write_claims": [
                {"kind": "prefix", "path": path}
                for path in ("app/frontend", "app/api", "app/database")
            ],
        }
        integration = {
            "task_id": "task-2",
            "dependencies": ["task-1"],
            "write_claims": [{"kind": "exact", "path": "compose.yaml"}],
        }
        self.assertIn(
            "first wave must have three distinct tier task owners",
            _dependency_contract_errors(
                [{"tasks": [combined]}, {"tasks": [integration]}]
            ),
        )

    def write_evidence(self, run: Path, generation: str) -> tuple[Path, str]:
        evidence = run / "evidence"
        evidence.mkdir()
        artifact = evidence / "application.json"
        artifact.write_text(
            json.dumps(application(generation), sort_keys=True), encoding="utf-8"
        )
        return evidence, hashlib.sha256(artifact.read_bytes()).hexdigest()

    def test_valid_partial_requires_structured_digest_bound_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            generation = str(uuid.uuid4())
            evidence, digest = self.write_evidence(Path(temp), generation)
            self.assertEqual(
                validate_results(manifest(generation, digest), evidence, generation), []
            )

    def test_pass_requires_direct_orchestration_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            generation = str(uuid.uuid4())
            evidence, digest = self.write_evidence(Path(temp), generation)
            errors = validate_results(
                manifest(generation, digest, "PASS"), evidence, generation
            )
            self.assertIn(
                "PASS requires direct project and orchestration state validation",
                errors,
            )

    def test_rejects_self_attested_empty_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            generation = str(uuid.uuid4())
            evidence = Path(temp) / "evidence"
            evidence.mkdir()
            artifact = evidence / "application.json"
            artifact.write_text("{}", encoding="utf-8")
            digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
            errors = validate_results(
                manifest(generation, digest), evidence, generation
            )
            self.assertIn("application evidence has an invalid shape", errors)

    def test_rejects_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            generation = str(uuid.uuid4())
            evidence, _digest = self.write_evidence(Path(temp), generation)
            errors = validate_results(
                manifest(generation, "0" * 64), evidence, generation
            )
            self.assertIn("application artifact digest does not match", errors)

    def test_pass_accepts_helper_bound_application_and_canonical_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generation = str(uuid.uuid4())
            evidence, digest = self.write_evidence(root, generation)
            project = root / "project"
            run_dir = root / "run"
            scripts = root / "scripts"
            for directory in (project, run_dir, scripts):
                directory.mkdir()
            prompt = root / "prompt.md"
            prompt.write_text("managed\n", encoding="utf-8")
            lifecycle = root / "lifecycle.json"
            lifecycle.write_text(
                json.dumps(
                    {
                        "owner": "task-implementer-test",
                        "generation_id": generation,
                        "live_started": True,
                        "web_port": 49152,
                        "application_evidence_sha256": digest,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(semantics, "_validate_orchestration", return_value=[]):
                errors = validate_results(
                    manifest(generation, digest, "PASS"),
                    evidence,
                    generation,
                    project_root=project,
                    run_dir=run_dir,
                    task_implementer_scripts=scripts,
                    managed_prompt=prompt,
                    lifecycle_state=lifecycle,
                )
            self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
