#!/usr/bin/env python3
"""Coverage, source isolation, timing and supplementary publication oracles."""

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import design_intake as intake


def fixture():
    rows = [{"path": "draft.md", "status": "source", "sha256": "a" * 64}]
    inventory = {
        "schema": "design/source-inventory-v1",
        "sources": rows,
        "snapshot_sha256": intake.canonical_digest(rows),
    }
    review = {
        "schema": "design/review-v1",
        "snapshot_sha256": inventory["snapshot_sha256"],
        "source_reviews": {
            "draft.md": {"disposition": "reviewed", "reason": "All obligations mapped."}
        },
        "evidence": [
            {
                "id": "EV-001",
                "kind": "local",
                "reference": "draft.md#behavior",
                "claim": "Product intent",
                "checked_at": "2026-11-23",
                "version": "frozen source",
            }
        ],
        "items": [
            {
                "id": "ITEM-001",
                "kind": "requirement",
                "layer": "API",
                "summary": "Create project",
                "status": "supported",
                "rationale": "Accepted draft behavior",
                "validation": "Integration check",
                "sources": [{"path": "draft.md", "locator": "#behavior"}],
                "evidence": ["EV-001"],
                "requirements": ["REQ-001"],
                "features": ["FEAT-001"],
            }
        ],
        "findings": [],
        "roadmap": {
            "mode": "derived",
            "rationale": "First observable outcome",
            "phases": [{"id": "Core", "depends_on": [], "exit_criteria": ["ITEM-001"]}],
            "slices": [
                {
                    "id": "SLICE-001",
                    "phase": "Core",
                    "depends_on": [],
                    "criteria": ["ITEM-001"],
                }
            ],
        },
    }
    return inventory, review


class DesignIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.source.mkdir()
        self.target = self.root / "target"
        self.target.mkdir()
        self.env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / "home")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_snapshot_preserves_sources_and_records_exclusions(self):
        (self.source / "draft.md").write_text("# Behavior\nCreate projects.\n")
        (self.source / "diagram.unknown").write_text("unreadable format")
        (self.source / ".env").write_text("not for extraction")
        generated = self.source / "docs/contracts/design-extractions"
        generated.mkdir(parents=True)
        (generated / "old.md").write_text("old output")
        before = (self.source / "draft.md").read_bytes()
        result = intake.snapshot(self.source, self.target)
        snapshot = Path(result["snapshot"])
        self.addCleanup(shutil.rmtree, snapshot.parent)
        value = json.loads(snapshot.read_bytes())
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["omission_count"], 3)
        self.assertEqual((snapshot.parent / "sources/draft.md").read_bytes(), before)
        self.assertEqual((self.source / "draft.md").read_bytes(), before)
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertFalse((snapshot.parent / "sources/.env").exists())
        self.assertEqual(
            intake.canonical_digest(value["sources"]), value["snapshot_sha256"]
        )

    def test_shared_evidence_and_stable_revision(self):
        inventory, review = fixture()
        second = copy.deepcopy(review["items"][0])
        second.update(id="ITEM-002", kind="design")
        review["items"].append(second)
        review["roadmap"]["phases"][0]["exit_criteria"].append("ITEM-002")
        review["roadmap"]["slices"][0]["criteria"].append("ITEM-002")
        result = intake.validate_review(inventory, review)
        self.assertEqual(result["items_reviewed"], 2)
        self.assertEqual(result, intake.validate_review(inventory, review))

    def test_invalid_inventory_rejected_before_review(self):
        for value in (
            [],
            {},
            {"schema": "design/source-inventory-v1", "sources": [None]},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                intake.validate_review(value, {})
        inventory, review = fixture()
        inventory["sources"].append(copy.deepcopy(inventory["sources"][0]))
        inventory["snapshot_sha256"] = intake.canonical_digest(inventory["sources"])
        with self.assertRaisesRegex(ValueError, "uniqueness"):
            intake.validate_review(inventory, review)

    def test_failed_capture_cleans_owned_scratch(self):
        scratch = self.root / "scratch"
        scratch.mkdir()
        with mock.patch.object(intake.tempfile, "mkdtemp", return_value=str(scratch)):
            with mock.patch.object(
                intake, "_capture_snapshot", side_effect=ValueError("changed")
            ):
                with self.assertRaisesRegex(ValueError, "changed"):
                    intake.snapshot(self.source, self.target)
        self.assertFalse(scratch.exists())

    def test_coverage_gaps_rejected(self):
        for change in ("evidence", "source", "roadmap", "locator"):
            with self.subTest(change=change):
                inventory, review = fixture()
                if change == "evidence":
                    review["items"][0]["evidence"] = []
                if change == "source":
                    review["source_reviews"] = {}
                if change == "roadmap":
                    review["roadmap"]["slices"][0]["criteria"] = []
                if change == "locator":
                    review["items"][0]["sources"][0]["locator"] = ""
                with self.assertRaises(ValueError):
                    intake.validate_review(inventory, review)

    def test_unresolved_findings_do_not_claim_readiness(self):
        inventory, review = fixture()
        review["items"][0]["status"] = "review-required"
        review["items"][0]["evidence"] = []
        with self.assertRaises(ValueError):
            intake.validate_review(inventory, review)
        review["findings"] = [
            {
                "id": "GAP-001",
                "kind": "decision",
                "status": "review-required",
                "items": ["ITEM-001"],
                "sources": ["draft.md"],
                "description": "Contradictory limit",
                "research_summary": "Original sources do not settle product intent.",
                "next_action": "Choose the limit.",
            }
        ]
        self.assertEqual(
            intake.validate_review(inventory, review)["status"], "review-required"
        )
        review["items"][0]["status"] = "supported"
        review["items"][0]["evidence"] = ["EV-001"]
        with self.assertRaises(ValueError):
            intake.validate_review(inventory, review)

    def test_dependency_cycle_and_future_obligation_omission(self):
        inventory, review = fixture()
        review["roadmap"]["phases"][0]["depends_on"] = ["Core"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            intake.validate_review(inventory, review)
        inventory, review = fixture()
        review["items"].append({**review["items"][0], "id": "ITEM-002"})
        with self.assertRaisesRegex(ValueError, "missing"):
            intake.validate_review(inventory, review)

    def test_progress_uses_wall_time_and_distinguishes_unknown_total(self):
        state, text = intake.progress(None, "inventory", reviewed=2, now=100)
        self.assertIn("total pending", text)
        state, text = intake.progress(
            state, "research", "API", reviewed=3, total=10, findings=1, now=165
        )
        self.assertIn("3/10 items reviewed", text)
        self.assertIn("00:01:05", text)
        state, _ = intake.progress(state, "review", now=175)
        self.assertEqual(sum(state["stage_seconds"].values()), 75)
        with self.assertRaises(ValueError):
            intake.progress(state, "review", now=174)
        with self.assertRaises(ValueError):
            intake.progress(state, "review", reviewed=2, total=1, now=176)
        state, text = intake.progress(state, "review", now=275, resume=True)
        self.assertEqual(sum(state["stage_seconds"].values()), 75)
        self.assertEqual(state["unobserved_seconds"], 100)
        self.assertIn("00:02:55", text)

    def test_source_level_findings_need_locators_and_roadmap_needs_planning_gap(self):
        inventory, review = fixture()
        finding = {
            "id": "GAP-001",
            "kind": "evidence",
            "status": "review-required",
            "items": [],
            "sources": ["draft.md"],
            "description": "An external claim is unconfirmed.",
            "research_summary": "No authoritative evidence was available.",
            "next_action": "Confirm the stated assumption.",
        }
        review["findings"] = [finding]
        with self.assertRaisesRegex(ValueError, "precise original-source locators"):
            intake.validate_review(inventory, review)
        finding["locators"] = [{"path": "draft.md", "locator": "#behavior"}]
        self.assertEqual(
            intake.validate_review(inventory, review)["status"], "review-required"
        )
        review["roadmap"] = None
        with self.assertRaisesRegex(ValueError, "planning finding"):
            intake.validate_review(inventory, review)
        finding["kind"] = "planning"
        self.assertEqual(
            intake.validate_review(inventory, review)["status"], "review-required"
        )
        finding["status"] = "resolved"
        with self.assertRaisesRegex(ValueError, "planning finding"):
            intake.validate_review(inventory, review)

    def test_canonical_mapping_uses_feature_union_and_keeps_readiness_checks(self):
        inventory, review = fixture()
        review["items"][0].update(
            requirements=["REQ-001", "REQ-002"], features=["FEAT-001", "FEAT-002"]
        )
        pair = {
            "requirements": {"records": [{"id": "REQ-001"}, {"id": "REQ-002"}]},
            "design": {
                "records": [
                    {"id": "FEAT-001", "requirements": ["REQ-001"], "status": "ready"},
                    {"id": "FEAT-002", "requirements": ["REQ-002"], "status": "ready"},
                ]
            },
        }
        self.assertEqual(
            intake.validate_review(inventory, review, pair)["status"], "reviewed"
        )
        pair["design"]["records"][1]["requirements"] = []
        with self.assertRaisesRegex(ValueError, "do not cover"):
            intake.validate_review(inventory, review, pair)
        pair["design"]["records"][1]["requirements"] = ["REQ-002"]
        review["items"][0]["status"] = "review-required"
        pair["design"]["records"][0]["status"] = "blocked"
        with self.assertRaisesRegex(ValueError, "ready canonical"):
            intake.validate_review(inventory, review, pair)

    def test_progress_cli_preserves_omitted_fields_and_explicit_unknown_total(self):
        path = self.root / "progress.json"
        script = Path(intake.__file__).resolve()
        command = [sys.executable, "-B", str(script), "progress", "--state", str(path)]
        subprocess.run(
            command
            + [
                "--stage",
                "research",
                "--layer",
                "API",
                "--reviewed",
                "12",
                "--total",
                "40",
                "--findings",
                "2",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            command + ["--stage", "review", "--resume"], check=True, capture_output=True
        )
        state = json.loads(path.read_text())
        self.assertEqual(
            {k: state[k] for k in ("layer", "reviewed", "total", "findings")},
            {"layer": "API", "reviewed": 12, "total": 40, "findings": 2},
        )
        subprocess.run(
            command + ["--stage", "review", "--unknown-total"],
            check=True,
            capture_output=True,
        )
        state = json.loads(path.read_text())
        self.assertIsNone(state["total"])
        self.assertEqual(state["reviewed"], 12)

    def test_assets_recover_interrupted_atomic_link(self):
        package = self.root / "package"
        (package / "contracts").mkdir(parents=True)
        (package / "contracts/a.md").write_text("contract")
        basis = intake.resolve_target(self.target).basis
        with mock.patch.object(intake.os, "unlink", side_effect=OSError("interrupted")):
            with self.assertRaisesRegex(OSError, "interrupted"):
                intake.publish_assets(
                    package, self.target, "a" * 64, expected_target_basis=basis
                )
        final = self.target / "docs/contracts/design-extractions" / ("a" * 64) / "a.md"
        self.assertEqual(final.stat().st_nlink, 2)
        intake.publish_assets(
            package, self.target, "a" * 64, expected_target_basis=basis
        )
        self.assertEqual(final.stat().st_nlink, 1)
        self.assertEqual(final.read_text(), "contract")
        self.assertEqual(list(final.parent.glob(".pending-*")), [])
        os.link(final, self.root / "unrelated")
        with self.assertRaisesRegex(ValueError, "unrelated hard link"):
            intake.publish_assets(
                package, self.target, "a" * 64, expected_target_basis=basis
            )

    def test_assets_are_immutable_replayable_and_do_not_publish_specs(self):
        package = self.root / "package"
        (package / "contracts").mkdir(parents=True)
        (package / "reports").mkdir()
        (package / "contracts/a.md").write_text("contract")
        (package / "reports/summary.md").write_text("summary")
        first = intake.publish_assets(
            package,
            self.target,
            "a" * 64,
            expected_target_basis=intake.resolve_target(self.target).basis,
        )
        self.assertEqual(
            first,
            intake.publish_assets(
                package,
                self.target,
                "a" * 64,
                expected_target_basis=intake.resolve_target(self.target).basis,
            ),
        )
        self.assertFalse((self.target / "docs/design.md").exists())
        (package / "contracts/a.md").write_text("changed")
        with self.assertRaises(ValueError):
            intake.publish_assets(
                package,
                self.target,
                "a" * 64,
                expected_target_basis=intake.resolve_target(self.target).basis,
            )
        self.assertEqual(
            (
                self.target / "docs/contracts/design-extractions" / ("a" * 64) / "a.md"
            ).read_text(),
            "contract",
        )

    def test_oversized_asset_package_stops_reading_before_publication(self):
        package = self.root / "package"
        (package / "contracts").mkdir(parents=True)
        for name in ("a.md", "b.md", "c.md"):
            (package / "contracts" / name).write_bytes(b"data")
        basis = intake.resolve_target(self.target).basis
        with mock.patch.object(intake, "MAX_BUNDLE", 6):
            with mock.patch.object(intake, "read_source", wraps=intake.read_source) as read:
                with self.assertRaisesRegex(ValueError, "too large"):
                    intake.publish_assets(
                        package, self.target, "a" * 64, expected_target_basis=basis
                    )
        self.assertEqual(
            [call.args[1] for call in read.call_args_list],
            ["contracts/a.md", "contracts/b.md"],
        )
        self.assertEqual(list(self.target.iterdir()), [])

    def test_unreadable_directory_is_inventory_evidence(self):
        def walk(root, **kwargs):
            kwargs["onerror"](PermissionError(13, "denied", str(root / "restricted")))
            return iter([(str(root), [], [])])

        with mock.patch.object(intake.os, "walk", side_effect=walk):
            self.assertEqual(intake.listing(self.source)[0]["status"], "unreadable")

    def test_failed_read_cannot_be_relabelled_as_safe_exclusion(self):
        inventory, review = fixture()
        inventory["sources"].append({"path": "security.pdf", "status": "unreadable"})
        inventory["snapshot_sha256"] = intake.canonical_digest(inventory["sources"])
        review["snapshot_sha256"] = inventory["snapshot_sha256"]
        review["source_reviews"]["security.pdf"] = {
            "disposition": "excluded",
            "reason": "Could not read security.",
        }
        with self.assertRaisesRegex(ValueError, "end-of-run finding"):
            intake.validate_review(inventory, review)
        review["findings"] = [
            {
                "id": "GAP-001",
                "kind": "coverage",
                "status": "review-required",
                "items": [],
                "sources": ["security.pdf"],
                "locators": [
                    {
                        "path": "security.pdf",
                        "locator": "Whole file; unreadable, page unknown.",
                    }
                ],
                "description": "Security requirements could not be read.",
                "research_summary": "File access failed; contents remain unreviewed.",
                "next_action": "Provide a readable source revision.",
            }
        ]
        self.assertEqual(
            intake.validate_review(inventory, review)["status"], "review-required"
        )

    def test_nested_generated_output_is_excluded(self):
        generated = self.source / "project/docs/reports/design-extractions/old"
        generated.mkdir(parents=True)
        (generated / "report.md").write_text("old report")
        rows = intake.listing(self.source)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "excluded")

    def test_cli_snapshot_review_assets_and_pair_publication(self):
        source_body = "# Behavior\nCreate projects.\n"
        (self.source / "draft.md").write_text(source_body)
        script = Path(intake.__file__)

        def run(*arguments):
            result = subprocess.run(
                [sys.executable, "-B", str(script), *map(str, arguments)],
                capture_output=True,
                text=True,
                check=True,
            )
            return json.loads(result.stdout)

        captured = run(
            "snapshot", "--source-folder", self.source, "--project-root", self.target
        )
        snapshot = Path(captured["snapshot"])
        self.addCleanup(shutil.rmtree, snapshot.parent)
        inventory = json.loads(snapshot.read_text())
        _, review = fixture()
        review["snapshot_sha256"] = inventory["snapshot_sha256"]
        review_path = self.root / "review.json"
        review_path.write_text(json.dumps(review))
        from project_specs_lib.contracts import canonical_document
        from test_project_specs_transaction import REQUIREMENTS, DESIGN

        requirements = self.root / "requirements.md"
        design = self.root / "design.md"
        requirements.write_bytes(canonical_document("requirements", REQUIREMENTS))
        design.write_bytes(canonical_document("design", DESIGN))
        result = run(
            "validate-review",
            "--snapshot",
            snapshot,
            "--review",
            review_path,
            "--requirements-candidate",
            requirements,
            "--design-candidate",
            design,
        )
        package = self.root / "package"
        (package / "contracts").mkdir(parents=True)
        (package / "reports").mkdir()
        (package / "contracts/draft.md").write_text(source_body)
        (package / "reports/review.json").write_text(json.dumps(review))
        assets = run(
            "publish-assets",
            "--package",
            package,
            "--project-root",
            self.target,
            "--revision",
            result["revision"],
            "--snapshot",
            snapshot,
            "--review",
            review_path,
        )
        self.assertEqual(assets["canonical_pair"], "not-published-by-this-helper")
        owner = intake.CATALOG / "maintain-project-specs/scripts/project_specs.py"
        output = subprocess.run(
            [
                sys.executable,
                "-B",
                str(owner),
                "publish",
                "--project-root",
                str(self.target),
                "--requirements-candidate",
                str(requirements),
                "--design-candidate",
                str(design),
                "--expected-filesystem-basis",
                inventory["target_basis"],
                "--expected-requirements-sha256",
                "absent",
                "--expected-design-sha256",
                "absent",
                "--operation-id",
                "extraction-e2e",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(output.stdout)["contract_status"], "current")
        self.assertFalse((self.target / ".git").exists())
        self.assertEqual((self.source / "draft.md").read_text(), source_body)
        self.assertEqual({p.name for p in self.target.iterdir()}, {"docs"})
        state = snapshot.parent / "progress.json"
        self.assertIn(
            "total pending",
            run("progress", "--state", state, "--stage", "inventory")["display"],
        )
        self.assertIn(
            "1/1",
            run(
                "progress",
                "--state",
                state,
                "--stage",
                "publication",
                "--reviewed",
                "1",
                "--total",
                "1",
            )["display"],
        )

    def test_canonical_candidate_must_not_mark_gap_ready(self):
        inventory, review = fixture()
        review["items"][0]["status"] = "review-required"
        review["findings"] = [
            {
                "id": "GAP-001",
                "kind": "decision",
                "status": "review-required",
                "items": ["ITEM-001"],
                "sources": ["draft.md"],
                "description": "Unknown decision",
                "research_summary": "Evidence exhausted",
                "next_action": "Review source intent",
            }
        ]
        pair = {
            "requirements": {"records": [{"id": "REQ-001"}]},
            "design": {
                "records": [
                    {"id": "FEAT-001", "requirements": ["REQ-001"], "status": "ready"}
                ]
            },
        }
        with self.assertRaisesRegex(ValueError, "ready canonical"):
            intake.validate_review(inventory, review, pair)

    def test_asset_target_cannot_rebind_after_snapshot(self):
        expected = intake.resolve_target(self.target).basis
        self.target.rename(self.root / "old-target")
        self.target.mkdir()
        with self.assertRaisesRegex(ValueError, "identity"):
            intake.publish_assets(
                self.root, self.target, "a" * 64, expected_target_basis=expected
            )
        self.assertEqual(list(self.target.iterdir()), [])

    def test_asset_symlink_cannot_escape(self):
        package = self.root / "package"
        (package / "contracts").mkdir(parents=True)
        (package / "contracts/a.md").write_text("contract")
        outside = self.root / "outside"
        outside.mkdir()
        (self.target / "docs").symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, intake.ProjectSpecError)):
            intake.publish_assets(
                package, self.target, "b" * 64, expected_target_basis="b" * 64
            )
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
