"""Offline source/scoped-history oracles; these tests are not native qualification."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import three_tier_program as program
import three_tier_lifecycle as lifecycle


class PlanningFixtureTests(unittest.TestCase):
    def test_derived_fixture_contains_obligations_without_a_phase_answer(self):
        supplied, derived = program.source_files("supplied"), program.source_files("derived")
        self.assertEqual(set(supplied) - set(derived), {"docs/contracts/supplied-phases.json"})
        self.assertNotIn('"phases"', b"".join(derived.values()).decode())
        self.assertEqual(supplied["docs/contracts/task-board-source.json"],
                         derived["docs/contracts/task-board-source.json"])
        self.assertEqual(len(json.loads(next(iter(derived.values())))["acceptance"]), 6)

    def test_fixture_seed_is_hash_bound_and_invalid_mode_has_no_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(program.target.TargetError):
                program.seed(root, "invented")
            self.assertEqual(list(root.iterdir()), [])
            frozen = program.seed(root, "derived")
            self.assertEqual(frozen["files"], {p: hashlib.sha256((root / p).read_bytes()).hexdigest()
                                             for p in program.source_files("derived")})

    def test_startup_revision_and_slice_have_distinct_observation_keys(self):
        self.assertEqual(program.observation_key("sdlc-start", "old", None),
                         program.observation_key("sdlc-start", "new", None))
        for changed in (("new", "SLICE-001"), ("old", "SLICE-002")):
            self.assertNotEqual(program.observation_key("sdlc-tdd", "old", "SLICE-001"),
                                program.observation_key("sdlc-tdd", *changed))


    def test_program_requires_normative_frozen_sources_and_all_obligations(self):
        frozen = {"mode": "derived", "files": {p: hashlib.sha256(b).hexdigest()
                  for p, b in program.source_files("derived").items()}}
        fingerprint = frozen["files"]["docs/contracts/task-board-source.json"]
        bundle = {"planning": {"mode": "derived", "sources": list(frozen["files"])},
                  "paths": list(frozen["files"]),
                  "coverage": [{"source_id": row["id"], "disposition": "required"}
                               for row in program.REQUIREMENTS["acceptance"]],
                  "obligation_basis": {row["id"]: {"source_sha256": fingerprint}
                                       for row in program.REQUIREMENTS["acceptance"]}}
        program._validate_fixture_provenance({"bundle": bundle}, frozen)
        for kind in ("context-only", "deferred", "rejected", "foreign-source", "missing"):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(bundle)
                if kind == "context-only":
                    changed["planning"]["sources"] = []
                elif kind in {"deferred", "rejected"}:
                    changed["coverage"][0]["disposition"] = kind
                elif kind == "foreign-source":
                    changed["obligation_basis"]["AC-CREATE"]["source_sha256"] = "f" * 64
                else:
                    changed["coverage"].pop()
                with self.assertRaises(program.target.TargetError):
                    program._validate_fixture_provenance({"bundle": changed}, frozen)


class ScopedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.state = {"phases": []}
        self.summary = {"slices": [
            {"id": "SLICE-001", "objective_sha256": "a" * 64},
            {"id": "SLICE-002", "objective_sha256": "b" * 64},
            {"id": "SLICE-003", "objective_sha256": "b" * 64},
        ]}
        self.enterContext(mock.patch.object(program, "observe", return_value=self.summary))
        self.validated = self.enterContext(mock.patch.object(lifecycle, "validate_phase_pass_artifact"))
        self.add("sdlc-start", "initial", None)
        for revision in ("a" * 64, "b" * 64):
            for phase in program.REVISION_PHASES:
                self.add(phase, revision, None)
        for selected in self.summary["slices"]:
            for phase in program.SLICE_PHASES:
                self.add(phase, selected["objective_sha256"], selected["id"])
        for phase in program.FINAL_PHASES:
            self.add(phase, "b" * 64, "SLICE-003")

    def add(self, phase, revision, execution):
        previous = [p for p in self.state["phases"] if p["phase"] == phase]
        key = program.observation_key(phase, revision, execution)
        scoped = [p for p in previous if lifecycle.phase_entry_key(p) == key]
        fingerprint = hashlib.sha256(str((phase, revision, execution, len(previous))).encode()).hexdigest()
        row = {"phase": phase, "objective_sha256": revision, "execution_id": execution,
               "status": "PASS", "attempt": len(previous) + 1,
               "supersedes_attempt": scoped[-1]["attempt"] if scoped else None,
               "artifact_sha256": fingerprint,
               "evidence": [f"evidence/phases/attempts/{phase}/{fingerprint}.json"]}
        self.state["phases"].append(row)
        return row

    def test_all_slices_and_both_revisions_are_validated(self):
        lifecycle.validate_phase_history(self.state)
        self.assertEqual(self.validated.call_count, len(self.state["phases"]))

    def test_later_slice_does_not_mask_missing_first_slice(self):
        first = next(r for r in self.state["phases"] if r["phase"] == "sdlc-tdd" and r["execution_id"] == "SLICE-001")
        first["status"] = "NOT_RUN"
        with self.assertRaisesRegex(lifecycle.LifecycleError, "every slice"):
            lifecycle.validate_phase_history(self.state)

    def test_cross_slice_supersession_is_rejected(self):
        second = next(r for r in self.state["phases"] if r["phase"] == "sdlc-tdd" and r["execution_id"] == "SLICE-002")
        second["supersedes_attempt"] = 1
        with self.assertRaisesRegex(lifecycle.LifecycleError, "supersession crosses"):
            lifecycle.validate_phase_history(self.state)

    def test_same_scope_revalidation_preserves_earlier_attempt(self):
        latest = self.add("sdlc-tdd", "a" * 64, "SLICE-001")
        self.assertEqual(latest["attempt"], 4)
        self.assertEqual(latest["supersedes_attempt"], 1)
        lifecycle.validate_phase_history(self.state)

    def test_second_startup_cannot_be_used_as_continuation(self):
        self.add("sdlc-start", "another", None)
        with self.assertRaisesRegex(lifecycle.LifecycleError, "exactly one"):
            lifecycle.validate_phase_history(self.state)


class ContinuationProofTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory).resolve()
        self.state = {"run_root": str(self.root), "verification_id": "a" * 32}

    def test_missing_or_changed_checkpoint_cannot_qualify(self):
        with self.assertRaisesRegex(program.target.TargetError, "missing"):
            program.read_checkpoint(self.state, "interrupted")
        path = self.root / "evidence/program/interrupted.json"
        lifecycle.private_json(path, {"schema": "agentic-sdlc/program-checkpoint-v1",
            "verification_id": "a" * 32, "stage": "interrupted"})
        self.state["program_checkpoints"] = {"interrupted": {
            "path": "evidence/program/interrupted.json", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
        program.read_checkpoint(self.state, "interrupted")
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(program.target.TargetError, "changed"):
            program.read_checkpoint(self.state, "interrupted")

    def test_final_status_cannot_replace_changed_plan_and_preserved_completion(self):
        before = {"owner_session_hash": "a" * 64, "objective_sha256": "b" * 64,
                  "inputs": {"program_sha256": "c" * 64}, "slices": [{"id": "SLICE-001"}]}
        after = {**before, "owner_session_hash": "d" * 64}
        snapshots = {"interrupted": {"run_id": "run-test", "program": before},
                     "continued": {"run_id": "run-test", "program": after}}
        for mutation in ({}, {"objective_sha256": "e" * 64},
                         {"objective_sha256": "e" * 64, "inputs": {"program_sha256": "f" * 64}, "slices": []}):
            final = {**copy.deepcopy(after), **mutation}
            with mock.patch.object(program, "observe", return_value=final), mock.patch.object(
                    program, "read_checkpoint", side_effect=lambda state, name: snapshots[name]):
                with self.assertRaisesRegex(program.target.TargetError, "accepted changed program"):
                    program.qualify(self.state, agent="codex")


if __name__ == "__main__":
    unittest.main()
