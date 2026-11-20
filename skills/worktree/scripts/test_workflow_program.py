"""Large portable inputs, phased ordering and bounded task scheduling."""

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock

from workflow_program import validate_definition
from workflow_state import WorkflowError
from workflow_execution import (
    TaskPlan,
    WriteClaim,
    build_dependency_waves,
    ExecutionError,
)
from project_specs_lib.source_bundle import validate_bundle, ProjectSpecError
from test_workflow_execution import REQUIREMENTS, DESIGN


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name).resolve()
        (self.project / "docs/contracts").mkdir(parents=True)
        (self.project / "docs/requirements.md").write_text(REQUIREMENTS)
        (self.project / "docs/design.md").write_text(DESIGN)
        body = json.dumps([{"id": f"AC-{i:04d}"} for i in range(601)]).encode()
        (self.project / "docs/contracts/acceptance.json").write_bytes(body)
        self.ids = [f"AC-{i:04d}" for i in range(601)]
        self.coverage = [
            {
                "source_id": i,
                "kind": "acceptance",
                "disposition": "required",
                "requirements": ["REQ-001"],
                "features": ["FEAT-001"],
                "reason": "Required behavior",
            }
            for i in self.ids
        ]
        self.manifest = {
            "schema": "maintain-project-specs/source-bundle-v2",
            "conflicts": [],
            "planning": None,
            "sources": [
                {
                    "path": "docs/contracts/acceptance.json",
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "authority": "normative",
                    "role": "acceptance",
                    "ids": self.ids,
                    "references": [],
                    "id_field": "id",
                }
            ],
            "coverage": self.coverage,
        }

    def validate(self):
        raw = json.dumps(self.manifest).encode()
        (self.project / "docs/contracts/manifest.json").write_bytes(raw)
        return validate_bundle(
            self.project,
            "docs/contracts/manifest.json",
            hashlib.sha256(raw).hexdigest(),
        )

    def test_large_package_has_complete_canonical_coverage(self):
        self.assertEqual(self.validate()["obligation_count"], 601)

    def test_missing_catalog_id_is_not_compressed_away(self):
        self.manifest["sources"][0]["ids"] = self.ids[:-1]
        with self.assertRaises(ProjectSpecError):
            self.validate()

    def test_cannot_opt_out_of_catalog_extraction(self):
        self.manifest["sources"][0]["id_field"] = None
        with self.assertRaises(ProjectSpecError):
            self.validate()

    def test_source_drift_conflicts_and_missing_references_fail(self):
        for mutate in (
            lambda: self.manifest["conflicts"].append("contradiction"),
            lambda: self.manifest["sources"][0]["references"].append("missing.json"),
            lambda: self.manifest["sources"][0].update(sha256="f" * 64),
        ):
            original = copy.deepcopy(self.manifest)
            mutate()
            with self.assertRaises(ProjectSpecError):
                self.validate()
            self.manifest = original

    def test_malformed_and_unmapped_dispositions_fail(self):
        for field, value in (
            ("features", [{}]),
            ("requirements", ["REQ-999"]),
            ("source_id", {}),
        ):
            original = copy.deepcopy(self.manifest)
            self.manifest["coverage"][0][field] = value
            with self.assertRaises(ProjectSpecError):
                self.validate()
            self.manifest = original

    def program(self):
        return {
            "schema": "workflow/program-v2", "uat_criteria": [],
            "phases": [
                {"id": "Phase0", "depends_on": [], "exit_criteria": [self.ids[0]]},
                {"id": "W01", "depends_on": ["Phase0"], "exit_criteria": self.ids[1:]},
            ],
            "slices": [
                {
                    "id": "SLICE-001",
                    "phase": "W01",
                    "depends_on": [],
                    "feature_ids": ["FEAT-001"],
                    "requirement_ids": ["REQ-001"],
                    "criteria": self.ids[1:],
                    "regression_criteria": [self.ids[0]],
                    "change_kind": "runtime",
                    "implementation_coverage": {c: ["core-check"] for c in self.ids},
                }
            ],
        }

    def planning(self, value=None, *, mode="supplied"):
        return {
            "mode": mode, "sources": ["docs/contracts/acceptance.json"],
            "rationale": "Reviewed source milestones" if mode == "supplied" else
                         "Derived outcome milestones from prerequisite and acceptance analysis",
            "phases": copy.deepcopy((value or self.program())["phases"]),
        }

    def test_external_phase_and_full_obligation_plan(self):
        value = self.program()
        self.assertEqual(validate_definition(value, self.coverage, self.planning(value)), value)

    def phased_scenario(self, mode):
        """Reviewed planning fixtures exercise admission, not model inference."""
        phases, slices = [], []
        for index in range(4):
            phase_id = f"Milestone{index + 1}"
            criteria = self.ids[index * 150:(index + 1) * 150] if index < 3 else self.ids[450:]
            phases.append({"id": phase_id, "depends_on": [phases[-1]["id"]] if phases else [],
                           "exit_criteria": criteria})
            for part in (criteria[:75], criteria[75:]):
                slices.append({"id": f"SLICE-{len(slices) + 1:03d}", "phase": phase_id,
                               "depends_on": [slices[-1]["id"]] if slices else [],
                               "feature_ids": ["FEAT-001"], "requirement_ids": ["REQ-001"],
                               "criteria": part, "regression_criteria": [], "change_kind": "runtime",
                               "implementation_coverage": {c: ["core-check"] for c in part}})
        value = {"schema": "workflow/program-v2", "uat_criteria": [], "phases": phases, "slices": slices}
        if mode == "supplied":
            body = "# Reviewed roadmap\n" + "\n".join(
                f"{p['id']} after {', '.join(p['depends_on']) or 'none'}: {', '.join(p['exit_criteria'])}"
                for p in phases)
            (self.project / "docs/contracts/roadmap.md").write_text(body)
            self.manifest["sources"].append({
                "path": "docs/contracts/roadmap.md", "sha256": hashlib.sha256(body.encode()).hexdigest(),
                "authority": "normative", "role": "design", "ids": [], "references": [], "id_field": None})
        self.manifest["planning"] = self.planning(value, mode=mode)
        if mode == "supplied":
            self.manifest["planning"]["sources"] = ["docs/contracts/roadmap.md"]
        return value

    def test_supplied_phases_split_into_multiple_bounded_slices(self):
        value = self.phased_scenario("supplied")
        bundle = self.validate()
        self.assertEqual(validate_definition(value, bundle["coverage"], bundle["planning"]), value)
        self.assertEqual(len(value["slices"]), 8)
        self.assertEqual({c for row in value["slices"] for c in row["criteria"]}, set(self.ids))

    def test_unphased_source_accepts_reviewed_derived_milestones(self):
        value = self.phased_scenario("derived")
        bundle = self.validate()
        # The sole source is a flat acceptance catalog, with no supplied phases.
        self.assertEqual(bundle["source_count"], 1)
        self.assertEqual(bundle["planning"]["mode"], "derived")
        self.assertEqual(validate_definition(value, bundle["coverage"], bundle["planning"]), value)

    def test_supplied_phase_identity_dependency_and_exit_drift_rejected(self):
        value = self.phased_scenario("supplied")
        planning = self.validate()["planning"]
        for field, changed in (("id", "InventedPhase"), ("depends_on", []), ("exit_criteria", [self.ids[150]])):
            with self.subTest(field=field):
                candidate = copy.deepcopy(value)
                candidate["phases"][1][field] = changed
                if field == "id":
                    for row in candidate["slices"]:
                        if row["phase"] == "Milestone2":
                            row["phase"] = changed
                    candidate["phases"][2]["depends_on"] = [changed]
                with self.assertRaisesRegex(WorkflowError, "reviewed source phase plan"):
                    validate_definition(candidate, self.coverage, planning)

    def test_missing_planning_allows_inventory_but_not_program_admission(self):
        bundle = self.validate()
        self.assertIsNone(bundle["planning"])
        with self.assertRaisesRegex(WorkflowError, "reviewed supplied or derived"):
            validate_definition(self.program(), bundle["coverage"], bundle["planning"])

    def test_phase_planning_rejects_missing_provenance_and_unknown_sources(self):
        for field, value in (("mode", "automatic"), ("sources", []),
                             ("sources", ["docs/contracts/unread.md"]), ("rationale", " "),
                             ("phases", [])):
            with self.subTest(field=field, value=value):
                self.manifest["planning"] = self.planning()
                self.manifest["planning"][field] = value
                with self.assertRaises(ProjectSpecError):
                    self.validate()

    def test_contradictory_reviewed_phase_order_is_not_silently_repaired(self):
        value = self.phased_scenario("derived")
        value["phases"][0]["depends_on"] = [value["phases"][-1]["id"]]
        self.manifest["planning"]["phases"] = copy.deepcopy(value["phases"])
        bundle = self.validate()
        with self.assertRaisesRegex(WorkflowError, "dependency cycle"):
            validate_definition(value, bundle["coverage"], bundle["planning"])

    def test_derived_plan_cannot_drop_future_obligations(self):
        value = self.phased_scenario("derived")
        value["phases"].pop()
        value["slices"] = value["slices"][:-2]
        self.manifest["planning"]["phases"] = copy.deepcopy(value["phases"])
        bundle = self.validate()
        with self.assertRaisesRegex(WorkflowError, "missing from program coverage"):
            validate_definition(value, bundle["coverage"], bundle["planning"])

    def test_changed_planning_requires_new_accepted_manifest_digest(self):
        self.phased_scenario("derived")
        bundle = self.validate()
        self.manifest["planning"]["rationale"] = "Changed milestone decision"
        (self.project / "docs/contracts/manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaisesRegex(ProjectSpecError, "new objective revision"):
            validate_bundle(self.project, "docs/contracts/manifest.json", bundle["sha256"])

    def test_phase_plan_is_enforced_on_admission_and_revalidation(self):
        from workflow_program import admit, _verify_inputs
        from workflow_state import digest, write

        for missing in (False, True):
            with self.subTest(missing=missing):
                value = self.program()
                self.manifest["planning"] = None if missing else self.planning(value)
                bundle = self.validate()
                value["phases"][1]["depends_on"] = []
                inputs = {"manifest": "docs/contracts/manifest.json", "manifest_sha256": bundle["sha256"],
                          "program_sha256": digest(value)}
                run_dir = self.project / ("missing" if missing else "drifted")
                run = {"owner": "agentic-sdlc", "status": "running", "fence": None,
                       "project_root": str(self.project)}
                with (mock.patch("workflow_program.accepted", return_value=(run, {}, {"inputs": inputs, "completion": {"boundary": "reviewed-pr"}})),
                      mock.patch("workflow_impact.spec_checkout", return_value=self.project)):
                    with self.assertRaisesRegex(WorkflowError, "reviewed"):
                        admit(run_dir, value)
                self.assertFalse((run_dir / "program").exists())
                # A correctly hashed file is still insufficient at subsequent verification.
                write(run_dir / "program" / (digest(value) + ".json"), value)
                with self.assertRaisesRegex(WorkflowError, "reviewed"):
                    _verify_inputs(run_dir, self.project, inputs)

    def test_obsolete_source_manifest_is_rejected_without_conversion(self):
        self.manifest["schema"] = "maintain-project-specs/source-bundle-v1"
        with self.assertRaises(ProjectSpecError):
            self.validate()

    def test_future_slice_and_phase_dependencies_rejected(self):
        value = self.program()
        second = {**value["slices"][0], "id": "SLICE-002"}
        value["slices"].append(second)
        value["slices"][0]["depends_on"] = ["SLICE-002"]
        with self.assertRaises(WorkflowError):
            validate_definition(value, self.coverage, self.planning(value))
        value["slices"][0]["depends_on"] = []
        second["phase"] = "Phase0"
        with self.assertRaises(WorkflowError):
            validate_definition(value, self.coverage, self.planning(value))

    def test_all_source_obligations_remain_scheduled(self):
        value = self.program()
        value["slices"][0]["criteria"] = self.ids[1:-1]
        value["phases"][1]["exit_criteria"] = self.ids[1:-1]
        with self.assertRaises(WorkflowError):
            validate_definition(value, self.coverage, self.planning(value))

    def test_phase_only_exit_without_a_runner_route_is_rejected(self):
        value = self.program()
        value["slices"][0]["criteria"] = self.ids[1:-1]
        value["slices"][0]["implementation_coverage"].pop(self.ids[-1])
        with self.assertRaisesRegex(WorkflowError, "phase exit criteria"):
            validate_definition(value, self.coverage, self.planning(value))

    def test_fresh_input_admission_stages_exact_snapshot_and_pair(self):
        self.validate()
        manifest = (self.project / "docs/contracts/manifest.json").read_bytes()

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(self.project), *args],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()

        git("init", "-b", "candidate")
        git("config", "user.email", "test@example.com")
        git("config", "user.name", "Fixture")
        git("commit", "--allow-empty", "-m", "initial")
        head = git("rev-parse", "HEAD")
        snapshot = {
            "inputs": {
                "manifest": "docs/contracts/manifest.json",
                "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
            }
        }
        run = {
            "owner": "agentic-sdlc",
            "project_root": str(self.project),
            "git_root": str(self.project),
        }
        from workflow_program import admit_inputs

        with (
            mock.patch("workflow_program.accepted", return_value=(run, {}, snapshot)),
            mock.patch("workflow_objectives.check_dispatch"),
        ):
            (self.project / "unrelated.txt").write_text("preserve\n")
            with self.assertRaisesRegex(WorkflowError, "unrelated"):
                admit_inputs(self.project / "private")
            self.assertEqual(git("diff", "--cached", "--name-only"), "")
            (self.project / "unrelated.txt").unlink()
            result = admit_inputs(self.project / "private")
        self.assertEqual(git("rev-parse", "HEAD"), head)
        self.assertEqual(
            set(git("diff", "--cached", "--name-only").splitlines()),
            set(result["paths"]),
        )


def task(i, dependencies=(), path=None):
    return TaskPlan(
        f"TASK-{i:04d}",
        i,
        ("REQ-001",),
        "implement",
        tuple(dependencies),
        (WriteClaim("exact", path or f"src/{i}.py"),),
        (),
        "tests",
        "passes",
        "stop",
        None,
        None,
        True,
        ("FEAT-001",),
    )


class ScaleTests(unittest.TestCase):
    def test_more_than_two_thousand_tasks_in_both_chain_orders(self):
        for forward in (False, True):
            tasks = [
                task(
                    i,
                    [f"TASK-{i + 1:04d}"]
                    if forward and i < 2100
                    else [f"TASK-{i - 1:04d}"]
                    if not forward and i > 1
                    else [],
                )
                for i in range(1, 2101)
            ]
            waves = build_dependency_waves(tasks)
            self.assertEqual(len(waves), 2100)
            self.assertTrue(all(len(w) == 1 for w in waves))

    def test_fanout_fanin_bounded_batches_and_cross_feature_mapping(self):
        tasks = [task(1)] + [
            replace(task(i, ["TASK-0001"]), feature_ids=(f"FEAT-{i % 2 + 1:03d}",))
            for i in range(2, 2101)
        ]
        tasks.append(task(2101, [t.task_id for t in tasks[1:]]))
        waves = build_dependency_waves(tasks)
        self.assertLessEqual(max(map(len, waves)), 8)
        self.assertEqual(waves[-1][0].task_id, "TASK-2101")
        positions = {t.task_id: i for i, wave in enumerate(waves) for t in wave}
        self.assertTrue(
            all(
                positions[d] < positions[t.task_id]
                for t in tasks
                for d in t.dependencies
            )
        )

    def test_cycles_and_conflicting_claims(self):
        with self.assertRaises(ExecutionError):
            build_dependency_waves([task(1, ["TASK-0002"]), task(2, ["TASK-0001"])])
        waves = build_dependency_waves(
            [task(i, path="shared.py") for i in range(1, 2101)]
        )
        self.assertEqual(len(waves), 2100)


class DeliveredProgramTests(unittest.TestCase):
    def test_completed_slice_cannot_acquire_new_obligations_or_move(self):
        import workflow_program as program
        from workflow_state import digest, write

        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary).resolve()
            original = {"slices": [{"id": "SLICE-001", "criteria": ["AC1"]}]}
            prior_digest = digest(original)
            write(run / "program" / (prior_digest + ".json"), original)
            completed = {"schema": "workflow/delivery-completion-v1", "execution_id": "SLICE-001", "review": {"admission": {}}}
            write(run / "delivery/SLICE-001/completion.json", completed)
            with (
                mock.patch(
                    "workflow_delivery.verify_completion", return_value=completed
                ),
                mock.patch(
                    "workflow_delivery_admission.verify_receipt",
                    return_value={"inputs": {"program_sha256": prior_digest}},
                ),
            ):
                program._preserve_delivered_slices(run, original)
                for candidate in (
                    {"slices": [{"id": "SLICE-001", "criteria": ["AC1", "AC2"]}]},
                    {"slices": []},
                    {"slices": [{"id": "SLICE-002"}, original["slices"][0]]},
                ):
                    with (
                        self.subTest(candidate=candidate),
                        self.assertRaises(WorkflowError),
                    ):
                        program._preserve_delivered_slices(run, candidate)
                program._preserve_delivered_slices(
                    run,
                    {
                        "slices": original["slices"]
                        + [{"id": "SLICE-002", "criteria": ["AC2"]}]
                    },
                )

    def test_full_completion_requires_current_evidence_beyond_phase_exit_subset(self):
        import workflow_program as program
        from workflow_state import write

        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary).resolve()
            write(
                run / "delivery/SLICE-001/completion.json",
                {"schema": "workflow/delivery-completion-v1", "review": {"admission": {}}},
            )
            definition = {
                "uat_criteria": [],
                "phases": [],
                "slices": [
                    {
                        "id": "SLICE-001",
                        "criteria": ["AC1", "AC2"],
                        "regression_criteria": [],
                    }
                ],
            }
            inputs = {
                "definition": definition,
                "bundle": {"obligation_basis": {"AC1": "current", "AC2": "current"}},
            }
            with (
                mock.patch.object(
                    program,
                    "accepted",
                    return_value=({"project_root": str(run)}, {}, {"inputs": {}}),
                ),
                mock.patch.object(program, "verify_inputs", return_value=inputs),
                mock.patch("workflow_impact.spec_checkout", return_value=run),
                mock.patch.object(program, "_delivery"),
                mock.patch("workflow_delivery_admission.verify_receipt") as receipt,
            ):
                receipt.return_value = {
                    "criteria": {"AC1": {}},
                    "obligation_basis": {"AC1": "current"},
                }
                with self.assertRaises(WorkflowError):
                    program.require_complete(run)
                receipt.return_value = {
                    "criteria": {"AC1": {}, "AC2": {}},
                    "obligation_basis": {"AC1": "current", "AC2": "old"},
                }
                with self.assertRaises(WorkflowError):
                    program.require_complete(run)
                receipt.return_value["obligation_basis"]["AC2"] = "current"
                program.require_complete(run)


if __name__ == "__main__":
    unittest.main()
