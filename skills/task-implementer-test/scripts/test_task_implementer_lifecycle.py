#!/usr/bin/env python3

import copy
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import task_implementer_lifecycle as lifecycle

from task_implementer_lifecycle import (
    GENERATION_LABEL,
    PROJECT_LABEL,
    CapabilityUnavailableError,
    LifecycleError,
    OwnershipBlockedError,
    _validate_compose_model,
    _owned_resource_inventory,
    _parse_compose_ps,
    _remove_owned_resources,
    _trusted_snapshot,
    collect_application,
    compose_up,
    destroy,
    finish,
    main,
    prepare,
    record_stage,
    status,
)
from task_implementer_reporting import build_report, default_live_stages


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        preflight_patcher = patch.object(lifecycle, "_validate_runtime_preflight")
        self.runtime_preflight = preflight_patcher.start()
        self.addCleanup(preflight_patcher.stop)

    def make_fixture(self, root: Path) -> Path:
        fixture = root / "fixture"
        fixture.mkdir()
        (fixture / "README.md").write_text("seed\n", encoding="utf-8")
        return fixture

    def mark_pass_ready(self, private: Path, result: dict) -> None:
        state_path = private / "runs" / result["generation_id"] / "lifecycle.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=result["project"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        state["application_evidence_sha256"] = "a" * 64
        state["semantic_status"] = "PASS"
        state["semantic_project_head"] = head
        for stage in state["stages"]:
            if stage["id"] != "cleanup":
                stage["status"] = "PASS"
                stage["detail"] = "Stage passed."
        state_path.write_text(json.dumps(state), encoding="utf-8")

    def make_report(self, private: Path, result: dict, outcome: str = "PASS") -> Path:
        stages = default_live_stages()
        for stage in stages:
            if outcome == "PASS" and stage["id"] != "cleanup":
                stage.update(status="PASS", detail="Stage passed.")
            if stage["id"] == "report-generation":
                stage.update(
                    status="PASS", detail="The complete stage report was generated."
                )
        report = Path(result["evidence"]) / "report.md"
        report.write_text(
            build_report(
                {
                    "mode": "create",
                    "overall": outcome,
                    "deterministic": "PASS",
                    "live": outcome,
                    "lifecycle": "CLEANUP_PENDING",
                    "report_path": str(private / "report.md"),
                    "stages": stages,
                    "next_action": "No action required.",
                }
            ),
            encoding="utf-8",
        )
        return report

    def test_prepare_creates_one_owned_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            self.assertEqual(result["status"], "PREPARED")
            current = status(base / "private")
            self.assertEqual(current["generation_id"], result["generation_id"])

    def test_second_prepare_replaces_and_archives_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            fixture = self.make_fixture(base)
            first = prepare(base / "private", fixture)
            second = prepare(base / "private", fixture)
            self.assertNotEqual(first["generation_id"], second["generation_id"])
            self.assertTrue(
                (
                    base
                    / "private"
                    / "archive"
                    / first["generation_id"]
                    / "lifecycle.json"
                ).is_file()
            )
            self.assertEqual(
                status(base / "private")["generation_id"], second["generation_id"]
            )

    def test_invalid_fixture_preserves_existing_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            first = prepare(base / "private", self.make_fixture(base))
            with self.assertRaises(LifecycleError):
                prepare(base / "private", base / "missing")
            self.assertEqual(
                status(base / "private")["generation_id"], first["generation_id"]
            )

    def test_symlinked_fixture_preserves_existing_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            first = prepare(base / "private", self.make_fixture(base))
            target = base / "target-fixture"
            target.mkdir()
            (target / "README.md").write_text("seed\n", encoding="utf-8")
            link = base / "linked-fixture"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(LifecycleError):
                prepare(base / "private", link)
            self.assertEqual(
                status(base / "private")["generation_id"], first["generation_id"]
            )

    def test_runtime_preflight_failure_preserves_existing_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            first = prepare(base / "private", self.make_fixture(base))
            self.runtime_preflight.side_effect = LifecycleError("Docker unavailable")
            with self.assertRaises(LifecycleError):
                prepare(base / "private", base / "fixture")
            self.assertEqual(
                status(base / "private")["generation_id"], first["generation_id"]
            )

    def test_cli_classifies_capability_action_and_ownership_failures(self) -> None:
        cases = (
            (CapabilityUnavailableError("Docker unavailable"), "PARTIAL"),
            (LifecycleError("attempted action failed"), "FAIL"),
            (OwnershipBlockedError("owner mismatch"), "OWNERSHIP_BLOCKED"),
            (TypeError("malformed input"), "FAIL"),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                output = StringIO()
                with (
                    patch.object(
                        sys, "argv", ["task_implementer_lifecycle.py", "prepare"]
                    ),
                    patch.object(lifecycle, "prepare", side_effect=error),
                    redirect_stdout(output),
                ):
                    self.assertEqual(main(), 1)
                self.assertEqual(json.loads(output.getvalue())["status"], expected)

    def test_subprocess_timeout_is_normalized_to_lifecycle_failure(self) -> None:
        with (
            patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["docker", "version"], 1),
            ),
            self.assertRaisesRegex(LifecycleError, "timed out"),
        ):
            lifecycle._run(["docker", "version"], timeout=1)

    def test_keep_then_destroy_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            report = self.make_report(base / "private", result)
            self.mark_pass_ready(base / "private", result)
            kept = finish(
                base / "private", result["generation_id"], "PASS", True, report
            )
            self.assertEqual(kept["status"], "KEPT")
            self.assertIn(
                "- Lifecycle: `RETAINED`",
                (base / "private" / "report.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(destroy(base / "private")["status"], "DESTROYED")
            self.assertIn(
                "- Lifecycle: `DESTROYED`",
                (base / "private" / "report.md").read_text(encoding="utf-8"),
            )
            destroyed_report = (base / "private" / "report.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("| Owned-resource cleanup | **PASS** |", destroyed_report)
            self.assertIn(
                "- No corrective action is required; exact cleanup is complete.",
                destroyed_report,
            )
            self.assertEqual(destroy(base / "private")["status"], "ALREADY_DESTROYED")
            self.assertTrue((base / "private" / "report.md").is_file())

    def test_plain_finish_records_cleaned_report_after_exact_destroy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            report = self.make_report(base / "private", result)
            self.mark_pass_ready(base / "private", result)
            finished = finish(
                base / "private", result["generation_id"], "PASS", False, report
            )
            self.assertEqual(finished["status"], "CLEANED")
            self.assertIn(
                "- Lifecycle: `CLEANED`",
                (base / "private" / "report.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "- No corrective action is required; exact cleanup is complete.",
                (base / "private" / "report.md").read_text(encoding="utf-8"),
            )

    def test_stale_generation_cannot_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            report = self.make_report(base / "private", result)
            with self.assertRaises(LifecycleError):
                finish(
                    base / "private",
                    "00000000-0000-0000-0000-000000000000",
                    "PASS",
                    True,
                    report,
                )

    def test_pass_finish_requires_canonical_semantic_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            report = self.make_report(base / "private", result)
            with self.assertRaises(LifecycleError):
                finish(
                    base / "private",
                    result["generation_id"],
                    "PASS",
                    True,
                    report,
                )

    def test_unowned_existing_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            private = base / "private"
            private.mkdir()
            with self.assertRaises(LifecycleError):
                prepare(private, self.make_fixture(base))

    def test_symlinked_private_root_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            target = base / "target"
            target.mkdir()
            link = base / "private"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(LifecycleError):
                prepare(link, self.make_fixture(base))

    def test_symlinked_lock_is_rejected_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            private = base / "private"
            prepare(private, self.make_fixture(base))
            lock = private / ".lifecycle.lock"
            lock.unlink()
            target = base / "target"
            target.write_text("unchanged", encoding="utf-8")
            lock.symlink_to(target)
            with self.assertRaises(LifecycleError):
                status(private)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged")

    def test_failed_live_cleanup_blocks_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            fixture = self.make_fixture(base)
            first = prepare(base / "private", fixture)
            state_path = (
                base / "private" / "runs" / first["generation_id"] / "lifecycle.json"
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["live_started"] = True
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(LifecycleError):
                prepare(base / "private", fixture)
            self.assertEqual(
                status(base / "private")["generation_id"], first["generation_id"]
            )

    def test_ownership_blocked_cleanup_preserves_error_classification(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            state_path = (
                base / "private" / "runs" / result["generation_id"] / "lifecycle.json"
            )
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["live_started"] = True
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with (
                patch.object(
                    lifecycle,
                    "_trusted_snapshot",
                    side_effect=OwnershipBlockedError("snapshot mismatch"),
                ),
                self.assertRaises(OwnershipBlockedError),
            ):
                destroy(base / "private")
            self.assertEqual(
                status(base / "private")["generation_id"], result["generation_id"]
            )

    def test_project_marker_mismatch_blocks_destroy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            marker = Path(result["project"]) / ".task-implementer-test.json"
            marker.write_text(
                json.dumps(
                    {"owner": "other", "generation_id": result["generation_id"]}
                ),
                encoding="utf-8",
            )
            with self.assertRaises(LifecycleError):
                destroy(base / "private")
            self.assertTrue((base / "private" / "active.json").is_file())

    def test_cli_classifies_malformed_project_marker_as_ownership_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            private = base / "private"
            result = prepare(private, self.make_fixture(base))
            marker = Path(result["project"]) / ".task-implementer-test.json"
            marker.write_text("{invalid", encoding="utf-8")
            output = StringIO()
            with (
                patch.object(
                    sys,
                    "argv",
                    [
                        "task_implementer_lifecycle.py",
                        "--root",
                        str(private),
                        "destroy",
                    ],
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(main(), 1)
            self.assertEqual(
                json.loads(output.getvalue())["status"], "OWNERSHIP_BLOCKED"
            )

    def test_kept_project_dirty_or_head_drift_can_be_explicitly_destroyed(self) -> None:
        for drift in ("dirty", "head"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as temp:
                base = Path(temp).resolve()
                result = prepare(base / "private", self.make_fixture(base))
                report = self.make_report(base / "private", result)
                self.mark_pass_ready(base / "private", result)
                finish(base / "private", result["generation_id"], "PASS", True, report)
                project = Path(result["project"])
                changed = project / "changed.txt"
                changed.write_text("drift\n", encoding="utf-8")
                if drift == "head":
                    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
                    subprocess.run(
                        ["git", "commit", "-m", "drift"], cwd=project, check=True
                    )
                self.assertEqual(destroy(base / "private")["status"], "DESTROYED")

    def test_failed_finish_cleans_even_with_linked_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            project = Path(result["project"])
            linked = project.parent / "codex-home" / "worker"
            subprocess.run(
                ["git", "worktree", "add", "-b", "worker", str(linked)],
                cwd=project,
                check=True,
                capture_output=True,
            )
            record_stage(
                base / "private",
                result["generation_id"],
                "deterministic-verification",
                "PASS",
                "All deterministic suites passed.",
            )
            record_stage(
                base / "private",
                result["generation_id"],
                "frontend-worker",
                "PASS",
                "Frontend task passed.",
            )
            record_stage(
                base / "private",
                result["generation_id"],
                "integration-runtime-worker",
                "FAIL",
                "WORKER_READ_ONLY_TIMEOUT after 123 seconds.",
            )
            finished = finish(
                base / "private",
                result["generation_id"],
                "FAIL",
                False,
                None,
                "worker failed",
                "integration-runtime-worker",
            )
            self.assertEqual(finished["status"], "CLEANED")
            self.assertEqual(status(base / "private")["status"], "ALREADY_DESTROYED")
            report_text = (base / "private" / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Stage Results", report_text)
            self.assertIn("| Frontend worker | **PASS** |", report_text)
            self.assertIn("| Integration/runtime worker | **FAIL** |", report_text)
            self.assertIn("WORKER_READ_ONLY_TIMEOUT after 123 seconds", report_text)
            self.assertIn("| Owned-resource cleanup | **PASS** |", report_text)

    def test_record_stage_is_generation_fenced_and_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            recorded = record_stage(
                base / "private",
                result["generation_id"],
                "workspace-initialization",
                "PASS",
                "Workspace initialized.",
            )
            self.assertEqual(recorded["status"], "STAGE_RECORDED")
            with self.assertRaises(LifecycleError):
                record_stage(
                    base / "private",
                    result["generation_id"],
                    "workspace-initialization",
                    "FAIL",
                    "Changed result.",
                )
            with self.assertRaises(OwnershipBlockedError):
                record_stage(
                    base / "private",
                    "00000000-0000-0000-0000-000000000000",
                    "workspace-initialization",
                    "PASS",
                    "Workspace initialized.",
                )

    def test_cleanup_rewrite_keeps_stage_totals_and_sections_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "report.md"
            stages = default_live_stages()
            for stage in stages:
                if stage["id"] == "integration-runtime-worker":
                    stage.update(status="FAIL", detail="Worker failed.")
                elif stage["id"] == "report-generation":
                    stage.update(status="PASS", detail="Report generated.")
            report.write_text(
                build_report(
                    {
                        "mode": "create",
                        "overall": "FAIL",
                        "deterministic": "PASS",
                        "live": "FAIL",
                        "lifecycle": "CLEANUP_PENDING",
                        "report_path": str(report),
                        "stages": stages,
                        "next_action": "Fix the worker.",
                    }
                ),
                encoding="utf-8",
            )
            lifecycle._rewrite_report_lifecycle(report, "CLEANED")
            cleaned = report.read_text(encoding="utf-8")
            self.assertIn("2 PASS, 1 FAIL, 0 PARTIAL, 14 NOT_RUN", cleaned)
            not_run_section = cleaned.split("## Not Run", 1)[1].split(
                "## Next Action", 1
            )[0]
            self.assertNotIn("Owned-resource cleanup", not_run_section)
            passed_section = cleaned.split("## Passed", 1)[1].split(
                "## Failure Analysis", 1
            )[0]
            self.assertIn("Owned-resource cleanup", passed_section)
            self.assertIn(
                "- No corrective action is required; exact cleanup is complete.",
                cleaned,
            )

            report.write_text(
                build_report(
                    {
                        "mode": "create",
                        "overall": "FAIL",
                        "deterministic": "PASS",
                        "live": "FAIL",
                        "lifecycle": "CLEANUP_PENDING",
                        "report_path": str(report),
                        "stages": stages,
                        "next_action": "Fix the worker.",
                    }
                ),
                encoding="utf-8",
            )
            lifecycle._rewrite_report_lifecycle(
                report, "CLEANUP_FAILED", overall="FAIL"
            )
            failed = report.read_text(encoding="utf-8")
            self.assertIn("1 PASS, 2 FAIL, 0 PARTIAL, 14 NOT_RUN", failed)
            self.assertIn("| Owned-resource cleanup | **FAIL** |", failed)
            self.assertIn("run $task-implementer-test --destroy", failed)

    def test_complete_report_must_match_recorded_stage_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report = Path(temp) / "report.md"
            stages = default_live_stages()
            for stage in stages:
                if stage["id"] == "integration-runtime-worker":
                    stage.update(status="FAIL", detail="Worker failed.")
                elif stage["id"] == "report-generation":
                    stage.update(status="PASS", detail="Report generated.")
            report.write_text(
                build_report(
                    {
                        "mode": "create",
                        "overall": "FAIL",
                        "deterministic": "PASS",
                        "live": "FAIL",
                        "lifecycle": "CLEANUP_PENDING",
                        "report_path": str(report),
                        "stages": stages,
                        "next_action": "Fix the worker.",
                    }
                ),
                encoding="utf-8",
            )
            recorded = copy.deepcopy(stages)
            for stage in recorded:
                if stage["id"] == "frontend-worker":
                    stage.update(status="PASS", detail="Frontend passed.")
            with self.assertRaisesRegex(
                LifecycleError, "do not match lifecycle evidence"
            ):
                lifecycle._validate_complete_report(report, "FAIL", recorded)

    def test_failed_finish_cleans_after_branch_and_head_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            project = Path(result["project"])
            subprocess.run(
                ["git", "checkout", "-b", "failed-worker"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            (project / "failed.txt").write_text("drift\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=project, check=True)
            subprocess.run(
                ["git", "commit", "-m", "failed worker drift"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            finished = finish(
                base / "private",
                result["generation_id"],
                "FAIL",
                False,
                None,
                "worker failed",
                "integration-runtime-worker",
            )
            self.assertEqual(finished["status"], "CLEANED")
            self.assertEqual(status(base / "private")["status"], "ALREADY_DESTROYED")

    def test_run_deletion_failure_retains_retryable_tombstone(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            private = base / "private"
            result = prepare(private, self.make_fixture(base))
            with (
                patch.object(shutil, "rmtree", side_effect=OSError("busy")),
                self.assertRaises(LifecycleError),
            ):
                destroy(private)
            self.assertEqual(status(private)["status"], "CLEANUP_PENDING")
            archived = json.loads(
                (
                    private / "archive" / result["generation_id"] / "lifecycle.json"
                ).read_text(encoding="utf-8")
            )
            self.assertFalse(archived["active"])
            self.assertEqual(archived["cleanup_status"], "PASS")
            self.assertEqual(destroy(private)["status"], "DESTROYED")

    def test_partial_run_deletion_resumes_without_project_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            private = base / "private"
            result = prepare(private, self.make_fixture(base))
            original_rmtree = shutil.rmtree

            def partially_remove(path, *args, **kwargs):
                project = Path(path) / "project"
                if project.exists():
                    original_rmtree(project)
                raise OSError("partial deletion")

            with (
                patch.object(shutil, "rmtree", side_effect=partially_remove),
                self.assertRaises(LifecycleError),
            ):
                destroy(private)
            tombstone = private / "deleting" / result["generation_id"]
            self.assertTrue(tombstone.is_dir())
            self.assertFalse((tombstone / "project").exists())
            self.assertEqual(status(private)["status"], "CLEANUP_PENDING")
            self.assertEqual(destroy(private)["status"], "DESTROYED")

    def test_pointer_deletion_failure_is_reconciled_on_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            private = base / "private"
            prepare(private, self.make_fixture(base))
            original_unlink = Path.unlink

            def fail_deleting(path, *args, **kwargs):
                if path.name == "deleting.json":
                    raise OSError("busy")
                return original_unlink(path, *args, **kwargs)

            with (
                patch.object(Path, "unlink", autospec=True, side_effect=fail_deleting),
                self.assertRaises(LifecycleError),
            ):
                destroy(private)
            self.assertEqual(status(private)["status"], "CLEANUP_PENDING")
            self.assertEqual(destroy(private)["status"], "DESTROYED")

    def test_failed_keep_retains_recovery_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            project = Path(result["project"])
            linked = project.parent / "codex-home" / "worker"
            subprocess.run(
                ["git", "worktree", "add", "-b", "worker", str(linked)],
                cwd=project,
                check=True,
                capture_output=True,
            )
            finished = finish(
                base / "private",
                result["generation_id"],
                "FAIL",
                True,
                None,
                "worker failed",
                "integration-runtime-worker",
            )
            self.assertEqual(finished["status"], "KEPT")
            self.assertEqual(destroy(base / "private")["status"], "DESTROYED")

    def test_failed_keep_and_status_tolerate_owned_branch_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            private = base / "private"
            result = prepare(private, self.make_fixture(base))
            project = Path(result["project"])
            subprocess.run(
                ["git", "checkout", "-b", "failed-worker"],
                cwd=project,
                check=True,
                capture_output=True,
            )
            finished = finish(
                private,
                result["generation_id"],
                "FAIL",
                True,
                None,
                "worker failed",
                "integration-runtime-worker",
            )
            self.assertEqual(finished["status"], "KEPT")
            self.assertEqual(status(private)["status"], "ACTIVE")
            self.assertEqual(destroy(private)["status"], "DESTROYED")

    def test_compose_model_requires_exact_safe_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            generation = "11111111-1111-4111-8111-111111111111"
            labels = {GENERATION_LABEL: generation}
            build_labels = {
                **labels,
                PROJECT_LABEL: f"task-implementer-test-{generation}",
            }
            model = {
                "services": {
                    "frontend": {
                        "labels": labels,
                        "networks": {"app": {}},
                        "build": {
                            "context": ".",
                            "dockerfile": "Dockerfile",
                            "labels": build_labels,
                        },
                        "ports": [
                            {
                                "target": 80,
                                "host_ip": "127.0.0.1",
                            }
                        ],
                    },
                    "api": {"labels": labels, "networks": {"app": {}}},
                    "db": {
                        "labels": labels,
                        "image": "postgres:16-alpine",
                        "networks": {"app": {}},
                        "volumes": [
                            {"type": "volume", "source": "data", "target": "/data"}
                        ],
                    },
                },
                "networks": {"app": {"labels": labels}},
                "volumes": {"data": {"labels": labels}},
            }
            model["services"]["api"]["build"] = {
                "context": ".",
                "dockerfile": "Dockerfile",
                "labels": build_labels,
            }
            _validate_compose_model(model, generation, project, raw=True)
            canonical = copy.deepcopy(model)
            canonical["name"] = f"task-implementer-test-{generation}"
            for service in canonical["services"].values():
                service["command"] = None
                service["entrypoint"] = None
            canonical["networks"]["app"]["name"] = (
                f"task-implementer-test-{generation}_app"
            )
            canonical["networks"]["app"]["ipam"] = {}
            canonical["volumes"]["data"]["name"] = (
                f"task-implementer-test-{generation}_data"
            )
            _validate_compose_model(canonical, generation, project)
            canonical_ipam = copy.deepcopy(canonical)
            canonical_ipam["networks"]["app"]["ipam"] = {
                "config": [{"subnet": "172.31.0.0/16"}]
            }
            with self.assertRaises(LifecycleError):
                _validate_compose_model(canonical_ipam, generation, project)
            for key in ("command", "entrypoint"):
                with self.subTest(canonical_override=key):
                    candidate = copy.deepcopy(canonical)
                    candidate["services"]["api"][key] = ["python", "server.py"]
                    with self.assertRaises(LifecycleError):
                        _validate_compose_model(candidate, generation, project)
            mutations = {
                "include": lambda value: value.update({"include": ["../other.yml"]}),
                "extends": lambda value: value["services"]["frontend"].update(
                    {"extends": {"file": "../other.yml", "service": "frontend"}}
                ),
                "label_file": lambda value: value["services"]["frontend"].update(
                    {"label_file": "../labels.env"}
                ),
                "raw_command": lambda value: value["services"]["frontend"].update(
                    {"command": None}
                ),
                "build_network": lambda value: value["services"]["frontend"][
                    "build"
                ].update({"network": "host"}),
                "build_privileged": lambda value: value["services"]["frontend"][
                    "build"
                ].update({"privileged": True}),
                "build_entitlements": lambda value: value["services"]["frontend"][
                    "build"
                ].update({"entitlements": ["security.insecure"]}),
                "build_cache": lambda value: value["services"]["frontend"][
                    "build"
                ].update({"cache_to": ["type=local,dest=../cache"]}),
                "invalid_port_target": lambda value: value["services"]["frontend"][
                    "ports"
                ][0].update({"target": {}}),
                "invalid_network_labels": lambda value: value["networks"]["app"].update(
                    {"labels": []}
                ),
                "raw_network_ipam": lambda value: value["networks"]["app"].update(
                    {"ipam": {}}
                ),
            }
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    candidate = copy.deepcopy(model)
                    mutate(candidate)
                    with self.assertRaises(LifecycleError):
                        _validate_compose_model(
                            candidate, generation, project, raw=True
                        )

    def test_compose_model_rejects_database_publication_and_bind_mount(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            (project / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            generation = "11111111-1111-4111-8111-111111111111"
            labels = {GENERATION_LABEL: generation}
            build_labels = {
                **labels,
                PROJECT_LABEL: f"task-implementer-test-{generation}",
            }
            model = {
                "services": {
                    "frontend": {
                        "labels": labels,
                        "networks": {"app": {}},
                        "build": {
                            "context": ".",
                            "dockerfile": "Dockerfile",
                            "labels": build_labels,
                        },
                        "ports": [
                            {
                                "target": 80,
                                "host_ip": "127.0.0.1",
                            }
                        ],
                    },
                    "api": {
                        "labels": labels,
                        "networks": {"app": {}},
                        "build": {
                            "context": ".",
                            "dockerfile": "Dockerfile",
                            "labels": build_labels,
                        },
                    },
                    "db": {
                        "labels": labels,
                        "image": "postgres:16-alpine",
                        "networks": {"app": {}},
                        "ports": [{"target": 5432, "host_ip": "127.0.0.1"}],
                        "volumes": [
                            {"type": "bind", "source": "/tmp", "target": "/data"}
                        ],
                    },
                },
                "networks": {"app": {"labels": labels}},
                "volumes": {"data": {"labels": labels}},
            }
            with self.assertRaises(LifecycleError):
                _validate_compose_model(model, generation, project, raw=True)

    def test_docker_inventory_requires_both_ownership_labels(self) -> None:
        generation = "11111111-1111-4111-8111-111111111111"
        state = {"generation_id": generation, "compose_project": "owned-project"}
        with (
            patch.object(lifecycle, "_resource_ids", return_value={"resource"}),
            patch.object(
                lifecycle,
                "_resource_labels",
                return_value={"com.docker.compose.project": "owned-project"},
            ),
            self.assertRaises(LifecycleError),
        ):
            _owned_resource_inventory(state)

    def test_cleanup_records_already_absent_and_preserves_ledger(self) -> None:
        generation = "11111111-1111-4111-8111-111111111111"
        state = {"generation_id": generation, "compose_project": "owned-project"}
        present = {
            "container": ["container-1"],
            "network": [],
            "volume": [],
            "image": [],
        }
        absent = {key: [] for key in present}
        with tempfile.TemporaryDirectory() as temp:
            run_path = Path(temp)
            with (
                patch.object(
                    lifecycle,
                    "_owned_resource_inventory",
                    side_effect=[present, absent, absent, absent],
                ),
                patch.object(
                    lifecycle,
                    "_run",
                    side_effect=LifecycleError("already removed"),
                ),
            ):
                _remove_owned_resources(state, run_path)
            self.assertEqual(
                state["cleanup_ledger"]["already_absent"],
                ["container:container-1"],
            )
            with patch.object(
                lifecycle, "_owned_resource_inventory", return_value=absent
            ):
                _remove_owned_resources(state, run_path)
            self.assertEqual(
                state["cleanup_ledger"]["already_absent"],
                ["container:container-1"],
            )

    def test_snapshot_digest_tamper_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run_path = Path(temp)
            snapshot = run_path / "compose.snapshot.json"
            snapshot.write_text("{}", encoding="utf-8")
            state = {
                "compose_snapshot_sha256": __import__("hashlib")
                .sha256(b"{}")
                .hexdigest()
            }
            self.assertEqual(_trusted_snapshot(state, run_path), snapshot)
            snapshot.write_text('{"changed":true}', encoding="utf-8")
            with self.assertRaises(LifecycleError):
                _trusted_snapshot(state, run_path)

    def test_compose_up_records_docker_assigned_port(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            run_path = base / "private" / "runs" / result["generation_id"]
            snapshot = run_path / "compose.snapshot.json"
            snapshot.write_text("{}", encoding="utf-8")
            state_path = run_path / "lifecycle.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["compose_snapshot_sha256"] = (
                __import__("hashlib").sha256(b"{}").hexdigest()
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            original_run = lifecycle._run

            def fake_run(command, cwd=None, timeout=120):
                if command[:2] == ["docker", "compose"]:
                    output = "127.0.0.1:49153\n" if "port" in command else ""
                    return subprocess.CompletedProcess(command, 0, output, "")
                return original_run(command, cwd=cwd, timeout=timeout)

            empty = {kind: [] for kind in ("container", "network", "volume", "image")}
            live_inventory = {
                "container": ["frontend", "api", "db"],
                "network": ["network"],
                "volume": ["volume"],
                "image": ["image-1", "image-2"],
            }

            def labels(_kind, resource_id):
                return {"com.docker.compose.service": resource_id}

            with (
                patch.object(
                    lifecycle,
                    "_owned_resource_inventory",
                    side_effect=[empty, live_inventory],
                ),
                patch.object(lifecycle, "_resource_labels", side_effect=labels),
                patch.object(lifecycle, "_run", side_effect=fake_run),
            ):
                live = compose_up(base / "private", result["generation_id"])
            self.assertEqual(live["web_port"], 49153)

    def test_compose_up_rejects_incomplete_post_start_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            result = prepare(base / "private", self.make_fixture(base))
            run_path = base / "private" / "runs" / result["generation_id"]
            snapshot = run_path / "compose.snapshot.json"
            snapshot.write_text("{}", encoding="utf-8")
            state_path = run_path / "lifecycle.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["compose_snapshot_sha256"] = (
                __import__("hashlib").sha256(b"{}").hexdigest()
            )
            state_path.write_text(json.dumps(state), encoding="utf-8")
            original_run = lifecycle._run

            def fake_run(command, cwd=None, timeout=120):
                if command[:2] == ["docker", "compose"]:
                    output = "127.0.0.1:49153\n" if "port" in command else ""
                    return subprocess.CompletedProcess(command, 0, output, "")
                return original_run(command, cwd=cwd, timeout=timeout)

            empty = {kind: [] for kind in ("container", "network", "volume", "image")}
            incomplete = {**empty, "container": ["frontend"]}
            with (
                patch.object(
                    lifecycle,
                    "_owned_resource_inventory",
                    side_effect=[empty, incomplete],
                ),
                patch.object(
                    lifecycle,
                    "_resource_labels",
                    return_value={"com.docker.compose.service": "frontend"},
                ),
                patch.object(lifecycle, "_run", side_effect=fake_run),
                self.assertRaises(LifecycleError),
            ):
                compose_up(base / "private", result["generation_id"])

    def test_compose_ps_accepts_document_and_json_lines_output(self) -> None:
        records = [{"Service": "api"}, {"Service": "db"}]
        self.assertEqual(_parse_compose_ps(json.dumps(records)), records)
        lines = "\n".join(json.dumps(record) for record in records)
        self.assertEqual(_parse_compose_ps(lines), records)

    def test_application_collection_holds_one_lifecycle_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            private = base / "private"
            result = prepare(private, self.make_fixture(base))
            run_path = private / "runs" / result["generation_id"]
            snapshot = run_path / "compose.snapshot.json"
            snapshot.write_text("{}", encoding="utf-8")
            state_path = run_path / "lifecycle.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["compose_snapshot_sha256"] = (
                __import__("hashlib").sha256(b"{}").hexdigest()
            )
            state["live_started"] = True
            state["web_port"] = 49153
            state_path.write_text(json.dumps(state), encoding="utf-8")

            def fake_collect(_root, _generation, **callbacks):
                descriptor = os.open(private / ".lifecycle.lock", os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    os.close(descriptor)
                self.assertEqual(callbacks["current"]["web_port"], 49153)
                return {
                    "schema": "task-implementer-test/application-evidence-v1",
                    "generation_id": result["generation_id"],
                }

            inventory = {
                "container": ["frontend", "api", "db"],
                "network": ["network"],
                "volume": ["volume"],
                "image": ["image-1", "image-2"],
            }
            with (
                patch("collect_live_evidence.collect", side_effect=fake_collect),
                patch.object(
                    lifecycle, "_owned_resource_inventory", return_value=inventory
                ),
                patch.object(lifecycle, "_validate_live_inventory"),
            ):
                collected = collect_application(private, result["generation_id"])
            self.assertEqual(collected["status"], "APPLICATION_VERIFIED")


if __name__ == "__main__":
    unittest.main()
