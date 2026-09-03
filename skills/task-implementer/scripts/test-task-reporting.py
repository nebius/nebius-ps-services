#!/usr/bin/env python3
"""Regression tests for deterministic Task Implementer reporting."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import prompt_workspace_reporting as reporting  # noqa: E402


def git(*arguments: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


class ReportingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git("init", "-q", cwd=self.repo)
        git("config", "user.name", "Reporting Test", cwd=self.repo)
        git("config", "user.email", "reporting@example.invalid", cwd=self.repo)
        (self.repo / "scope").mkdir()
        (self.repo / "outside").mkdir()
        (self.repo / "scope" / "keep.txt").write_text("old\n", encoding="utf-8")
        (self.repo / "scope" / "delete.txt").write_text("gone\n", encoding="utf-8")
        (self.repo / "scope" / "type").write_text("file\n", encoding="utf-8")
        (self.repo / "outside" / "move.txt").write_text("move\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", "base", cwd=self.repo)
        self.base = git("rev-parse", "HEAD", cwd=self.repo)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def commit(self, message: str) -> str:
        git("add", "-A", cwd=self.repo)
        git("commit", "-qm", message, cwd=self.repo)
        return git("rev-parse", "HEAD", cwd=self.repo)

    def summary(self) -> dict[str, object]:
        delta = reporting.diff_statistics(
            self.repo, self.base, self.base, "scope", require_ancestry=True
        )
        return {
            "schema": reporting.SUMMARY_SCHEMA,
            "status": "done",
            "work": {
                "tasks": {"total": 0, "implementation": 0, "corrections": 0},
                "waves": 0,
                "temporary_worker_worktrees": 0,
                "temporary_resources": {"total": 0, "removed": 0, "retained": 0},
            },
            "outcomes": {
                "validation": {
                    "status": "passed",
                    "waves": 0,
                    "evidence_sha256": "0" * 64,
                },
                "review": {
                    "status": "passed",
                    "waves": 0,
                    "evidence_sha256": "0" * 64,
                },
            },
            "lane": {
                "promotion": "promoted",
                "promoted_head": self.base,
                "generation_release": "released",
            },
            "source_observation": {
                "status": "unchanged",
                "source_branch": "main",
                "source_head_at_open": self.base,
                "source_head_at_completion": self.base,
            },
            "changes": {
                "run_local": {"label": "final integration result", **delta},
                "accumulated_pending": {
                    "label": "source-to-lane comparison",
                    "relationship": "equal",
                    **delta,
                },
            },
            "queued_prompt": {"status": "none", "pending": 0},
            "next_action": {
                "action": "integrate",
                "readiness": "ready",
                "invocation": '$task-implementer integrate "/project"',
                "instruction": '$task-implementer integrate "/project"',
            },
        }

    def coordinator_projection(
        self,
        states: list[tuple[str, str | None]],
        *,
        wave_status: str = "running",
        coordinator_status: str = "running",
        promoted: bool = False,
    ) -> tuple[dict[str, object], str]:
        task_ids = [f"task-{index}" for index in range(1, len(states) + 1)]
        tasks = [{"task_id": task_id} for task_id in task_ids]
        batches = [task_ids]
        coordinator = {
            "status": coordinator_status,
            "active_wave": (
                "wave-001" if coordinator_status == "running" else None
            ),
            "waves": [
                {
                    "wave_id": "wave-001",
                    "tasks": tasks,
                    "batches": batches,
                }
            ],
            "plan_sha256": reporting.sha256_json([tasks]),
        }
        wave = {
            "wave_id": "wave-001",
            "status": wave_status,
            "task_ids": task_ids,
            "task_states": {
                task_id: state
                for task_id, (state, _dispatched_at) in zip(task_ids, states)
            },
            "batches": batches,
            "promoted_head": self.base if promoted else None,
        }
        planes = {
            task_id: {"state": state, "dispatched_at": dispatched_at}
            for task_id, (state, dispatched_at) in zip(task_ids, states)
        }
        with (
            mock.patch(
                "prompt_workspace_waves._load_wave", return_value=wave
            ),
            mock.patch(
                "prompt_workspace_waves._load_task_plane",
                side_effect=lambda _run, _wave, task_id: planes[task_id],
            ),
        ):
            return reporting._coordinator_progress(self.root / "run", coordinator)

    def lane_report_fixture(self) -> dict[str, object]:
        return {
            "schema": reporting.LANE_REPORT_SCHEMA,
            "status": "managed",
            "lane": {"state": "active"},
            "generations": {
                "active": 1,
                "released_total": 3,
                "integrated_total": 2,
                "pending_integration": 1,
                "finalization_pending": 0,
            },
            "current_run": {
                "status": "running",
                "phase": "workers_running",
                "progress": {
                    "tasks": {
                        "total": 1,
                        "promoted": 0,
                        "pending": 0,
                        "in_progress": 1,
                        "blocked": 0,
                        "superseded": 0,
                        "remaining": 1,
                    },
                    "workers": {
                        "total": 1,
                        "planned": 0,
                        "created": 0,
                        "queued": 0,
                        "active": 1,
                        "finished": 0,
                        "failed": 0,
                        "superseded": 0,
                    },
                    "waves": {
                        "total": 1,
                        "promoted": 0,
                        "active": 1,
                        "active_ordinal": 1,
                        "remaining": 1,
                    },
                },
            },
            "current_step": "Temporary workers are executing the active wave.",
            "remaining_steps": ["Finish 1 remaining task"],
            "next_action": {
                "action": "run",
                "readiness": "in_progress",
                "invocation": '$task-implementer run "<prompt-file>"',
                "instruction": "Continue the active Task Implementer run.",
            },
        }

    def test_task_plane_states_map_to_disjoint_worker_and_task_counts(self) -> None:
        cases = [
            ("planned", None, "pending", "planned"),
            ("assigned", None, "in_progress", "created"),
            ("assigned", "2026-08-15T12:00:00+00:00", "in_progress", "queued"),
            ("running", "2026-08-15T12:00:00+00:00", "in_progress", "active"),
            ("committed", "2026-08-15T12:00:00+00:00", "in_progress", "finished"),
            ("merged", "2026-08-15T12:00:00+00:00", "in_progress", "finished"),
            ("failed", "2026-08-15T12:00:00+00:00", "blocked", "failed"),
            (
                "superseded",
                "2026-08-15T12:00:00+00:00",
                "superseded",
                "superseded",
            ),
        ]
        task_keys = {"pending", "in_progress", "blocked", "superseded"}
        worker_keys = {
            "planned",
            "created",
            "queued",
            "active",
            "finished",
            "failed",
            "superseded",
        }
        for state, dispatched_at, task_key, worker_key in cases:
            with self.subTest(state=state, dispatched_at=dispatched_at):
                progress, _phase = self.coordinator_projection(
                    [(state, dispatched_at)]
                )
                tasks = progress["tasks"]
                workers = progress["workers"]
                self.assertEqual(tasks["promoted"], 0)
                self.assertEqual(tasks["remaining"], state != "superseded")
                self.assertEqual(
                    {key: tasks[key] for key in task_keys},
                    {key: int(key == task_key) for key in task_keys},
                )
                self.assertEqual(
                    {key: workers[key] for key in worker_keys},
                    {key: int(key == worker_key) for key in worker_keys},
                )

    def test_wave_promotion_precedes_task_promotion(self) -> None:
        for state in ("committed", "merged"):
            with self.subTest(state=state, promoted=False):
                progress, _phase = self.coordinator_projection(
                    [(state, "2026-08-15T12:00:00+00:00")]
                )
                self.assertEqual(progress["tasks"]["promoted"], 0)
                self.assertEqual(progress["tasks"]["in_progress"], 1)
            with self.subTest(state=state, promoted=True):
                progress, _phase = self.coordinator_projection(
                    [(state, "2026-08-15T12:00:00+00:00")],
                    wave_status="promoted",
                    promoted=True,
                )
                self.assertEqual(progress["tasks"]["promoted"], 1)
                self.assertEqual(progress["tasks"]["remaining"], 0)

        progress, _phase = self.coordinator_projection(
            [("superseded", "2026-08-15T12:00:00+00:00")],
            wave_status="promoted",
            promoted=True,
        )
        self.assertEqual(progress["tasks"]["promoted"], 0)
        self.assertEqual(progress["tasks"]["superseded"], 1)

    def test_lane_report_requires_matching_observation_pairs(self) -> None:
        first = self.lane_report_fixture()
        latest = json.loads(json.dumps(first))
        latest["generations"]["released_total"] = 4
        with mock.patch.object(
            reporting,
            "_lane_report_once",
            side_effect=[(first, "a"), (first, "a")],
        ) as observe:
            self.assertEqual(reporting.lane_report(self.root / "workspace.json"), first)
        self.assertEqual(observe.call_count, 2)

        with mock.patch.object(
            reporting,
            "_lane_report_once",
            side_effect=[
                (first, "a"),
                (latest, "b"),
                (latest, "c"),
                (latest, "c"),
            ],
        ) as observe:
            self.assertEqual(
                reporting.lane_report(self.root / "workspace.json"), latest
            )
        self.assertEqual(observe.call_count, 4)

        with mock.patch.object(
            reporting,
            "_lane_report_once",
            side_effect=[
                (first, "a"),
                (latest, "b"),
                (first, "c"),
                (latest, "d"),
            ],
        ) as observe:
            with self.assertRaises(reporting.PromptWorkspaceError) as raised:
                reporting.lane_report(self.root / "workspace.json")
        self.assertEqual(raised.exception.code, "WORKSPACE_BUSY")
        self.assertEqual(observe.call_count, 4)

    def test_active_run_projection_covers_planning_blocked_and_finalization(
        self,
    ) -> None:
        planning = reporting._active_run_projection(
            {
                "run_dir": self.root / "run-planning",
                "coordinator": None,
                "interop": None,
                "summary_phase": None,
            }
        )
        self.assertEqual(
            planning,
            {"status": "planning", "phase": "planning", "progress": None},
        )

        progress = self.lane_report_fixture()["current_run"]["progress"]
        cases = [
            ("blocked", False, None, "blocked", "blocked"),
            ("done", False, None, "finalizing", "finalizing"),
            ("done", True, "prepared", "finalizing", "finalizing"),
            ("done", True, "complete", "complete", "complete"),
        ]
        for coordinator_status, released, phase, status, expected_phase in cases:
            with (
                self.subTest(
                    coordinator_status=coordinator_status,
                    released=released,
                    phase=phase,
                ),
                mock.patch.object(
                    reporting,
                    "_coordinator_progress",
                    return_value=(progress, coordinator_status),
                ),
            ):
                projection = reporting._active_run_projection(
                    {
                        "run_dir": self.root / "run-active",
                        "coordinator": {"status": coordinator_status},
                        "interop": {"released": released},
                        "summary_phase": phase,
                    }
                )
                self.assertEqual(projection["status"], status)
                self.assertEqual(projection["phase"], expected_phase)
                self.assertEqual(projection["progress"], progress)

    def test_current_run_selection_does_not_read_prompt_snapshots(self) -> None:
        runs_root = self.root / "runs-metadata-only"
        run_dir = runs_root / "run-metadata-only"
        run_dir.mkdir(parents=True)
        manifest = {
            "schema": reporting.RUN_SCHEMA,
            "run_id": run_dir.name,
            "project_id": "project",
            "scope_id": "scope",
            "prompt_id": "prompt-" + "1" * 32,
            "revisions": [
                {
                    "revision": "r0001",
                    "sha256": "2" * 64,
                }
            ],
        }
        workspace = {
            "runs_root": str(runs_root),
            "project_id": "project",
            "scope_id": "scope",
        }
        with (
            mock.patch(
                "prompt_workspace_runs.load_run_manifests",
                return_value=[(run_dir, manifest)],
            ),
            mock.patch(
                "prompt_workspace_runs.verify_run",
                side_effect=AssertionError("lane status must not read prompt snapshots"),
            ) as verify_run,
        ):
            selected = reporting._current_run(workspace, runs_root)
        self.assertIsNotNone(selected)
        self.assertEqual(selected["verified"]["status"], "snapshot_only")
        verify_run.assert_not_called()

        private_run_id = "run-private-status-identity"
        with (
            mock.patch(
                "prompt_workspace_runs.load_run_manifests",
                side_effect=reporting.PromptWorkspaceError(
                    "RUN_STATE_INVALID", f"invalid run: {private_run_id}"
                ),
            ),
            self.assertRaises(reporting.PromptWorkspaceError) as private_error,
        ):
            reporting._current_run(workspace, runs_root)
        self.assertEqual(private_error.exception.code, "RUN_STATE_INVALID")
        self.assertNotIn(private_run_id, private_error.exception.message)

    def test_lane_presence_transition_is_an_unstable_observation(self) -> None:
        manifest_path = self.root / "workspace-race.json"
        lane_root = self.root / "lane-race"
        manifest_path.write_text(
            json.dumps(
                {
                    "primary_root": str(self.repo),
                    "scope": ".",
                    "repo_root": str(lane_root),
                }
            ),
            encoding="utf-8",
        )

        lane_root.mkdir()

        def remove_lane(*_args: object, **_kwargs: object) -> dict[str, object]:
            lane_root.rmdir()
            raise reporting.PromptWorkspaceError(
                "WORKSPACE_MISMATCH", "lane disappeared"
            )

        with (
            mock.patch("prompt_workspace_core.verify_workspace", side_effect=remove_lane),
            self.assertRaises(reporting._LaneReportUnstable),
        ):
            reporting._lane_report_once(manifest_path)

        def restore_lane(*_args: object, **_kwargs: object) -> dict[str, object]:
            lane_root.mkdir()
            raise reporting.PromptWorkspaceError(
                "WORKSPACE_MISMATCH", "lane reappeared"
            )

        with (
            mock.patch(
                "prompt_workspace_core.verify_workspace_for_removal",
                side_effect=restore_lane,
            ),
            self.assertRaises(reporting._LaneReportUnstable),
        ):
            reporting._lane_report_once(manifest_path)

        runs_root = self.root / "runs-race"
        runs_root.mkdir()
        workspace = {
            **json.loads(manifest_path.read_text(encoding="utf-8")),
            "runs_root": str(runs_root),
        }

        def disappear_at_anchor(
            *_args: object, **_kwargs: object
        ) -> dict[str, object]:
            lane_root.rmdir()
            raise reporting.PromptWorkspaceError(
                "WORKTREE_CONFLICT", "lane disappeared during anchor inspection"
            )

        with (
            mock.patch("prompt_workspace_core.verify_workspace", return_value=workspace),
            mock.patch(
                "prompt_workspace_interop.inspect_anchor",
                side_effect=disappear_at_anchor,
            ),
            self.assertRaises(reporting._LaneReportUnstable),
        ):
            reporting._lane_report_once(manifest_path)

    def test_sealed_moved_source_warns_before_public_integration(self) -> None:
        summary = self.summary()
        summary["source_observation"]["status"] = "moved"
        projection = reporting._sealed_run_projection(summary)
        self.assertEqual(projection["source_status"], "moved")

        generations = {
            "active": 0,
            "released_total": 1,
            "integrated_total": 0,
            "pending_integration": 1,
            "finalization_pending": 0,
        }
        action, _evidence = reporting._next_action(
            status="managed",
            project=self.repo,
            primary=self.repo,
            lane_root=self.repo,
            active=None,
            current_run=projection,
            generations=generations,
        )
        self.assertIn("Source moved", action["instruction"])
        self.assertIn("rebuild and revalidate", action["instruction"])

        report = self.lane_report_fixture()
        report["current_run"] = projection
        report["next_action"] = action
        self.assertIn("Source since workers started: moved", reporting.render_lane_report(report))

    def test_remaining_steps_are_bounded_and_actionable(self) -> None:
        current = self.lane_report_fixture()["current_run"]
        generations = {
            "active": 1,
            "released_total": 3,
            "integrated_total": 2,
            "pending_integration": 1,
            "finalization_pending": 0,
        }
        steps = reporting._remaining_steps("managed", current, generations)
        self.assertEqual(
            steps,
            [
                "Finish 1 remaining task",
                "Promote 1 remaining wave",
                "Finalize the active generation",
                "Integrate pending generations into the source branch",
            ],
        )
        self.assertEqual(
            reporting._remaining_steps("removed", None, generations),
            ["Initialize the managed workspace"],
        )

    def test_lane_state_digest_rejects_unsafe_and_oversized_evidence(self) -> None:
        runs_root = self.root / "runs-status"
        run_dir = runs_root / "run-status"
        run_dir.mkdir(parents=True)
        manifest = run_dir / "manifest.json"
        manifest.write_text("{}\n", encoding="utf-8")
        first = reporting._lane_state_digest(runs_root)
        manifest.write_text('{"changed":true}\n', encoding="utf-8")
        self.assertNotEqual(reporting._lane_state_digest(runs_root), first)

        target = run_dir / "real-handoff"
        target.write_text("unsafe\n", encoding="utf-8")
        (run_dir / "handoff.md").symlink_to(target)
        with self.assertRaises(reporting.PromptWorkspaceError) as unsafe:
            reporting._lane_state_digest(runs_root)
        self.assertEqual(unsafe.exception.code, "RUN_STATE_INVALID")
        (run_dir / "handoff.md").unlink()

        with mock.patch.object(reporting, "LANE_REPORT_BYTES_LIMIT", 1):
            with self.assertRaises(reporting.PromptWorkspaceError) as oversized:
                reporting._lane_state_digest(runs_root)
        self.assertEqual(oversized.exception.code, "RUN_STATE_INVALID")

        (run_dir / "ignored.bin").write_bytes(b"ignored")
        with mock.patch.object(reporting, "LANE_REPORT_MAX_STATE_FILES", 2):
            with self.assertRaises(reporting.PromptWorkspaceError) as crowded:
                reporting._lane_state_digest(runs_root)
        self.assertEqual(crowded.exception.code, "RUN_STATE_INVALID")

    def test_lane_report_projection_excludes_private_and_change_data(self) -> None:
        report = self.lane_report_fixture()
        encoded = json.dumps(report, sort_keys=True)
        human = reporting.render_lane_report(report)
        for forbidden in (
            "comparison",
            "files",
            "insertions",
            "deletions",
            "commit",
            "branch",
            "prompt_id",
            "run_id",
            "lane_id",
            "workspace.json",
            "summary",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, encoded)
                self.assertNotIn(forbidden, human)
        self.assertLessEqual(
            len(human.encode("utf-8")), reporting.LANE_REPORT_HUMAN_BYTES_LIMIT
        )

    def test_no_change_statistics_are_stable(self) -> None:
        first = reporting.diff_statistics(
            self.repo, self.base, self.base, "scope", require_ancestry=True
        )
        second = reporting.diff_statistics(
            self.repo, self.base, self.base, "scope", require_ancestry=True
        )
        self.assertEqual(first, second)
        self.assertEqual(first["full_repository"]["files"], 0)
        self.assertEqual(first["files"], [])

    def test_full_scope_binary_type_and_cross_scope_counts(self) -> None:
        (self.repo / "scope" / "keep.txt").write_text("new\nmore\n", encoding="utf-8")
        (self.repo / "scope" / "delete.txt").unlink()
        (self.repo / "scope" / "added.bin").write_bytes(b"\x00\xff\x00")
        (self.repo / "scope" / "type").unlink()
        (self.repo / "scope" / "type").symlink_to("keep.txt")
        (self.repo / "outside" / "move.txt").rename(self.repo / "scope" / "move.txt")
        head = self.commit("mixed")
        report = reporting.diff_statistics(
            self.repo, self.base, head, "scope", require_ancestry=True
        )
        full = report["full_repository"]
        self.assertEqual(full["modified"], 1)
        self.assertEqual(full["deleted"], 1)
        self.assertEqual(full["renamed"], 1)
        self.assertEqual(full["type_changed"], 1)
        self.assertEqual(full["binary_files"], 1)
        self.assertEqual(report["cross_scope"]["files"], 1)
        self.assertEqual(report["cross_scope"]["renamed"], 1)
        self.assertEqual(report["outside_scope"]["files"], 1)
        self.assertEqual(report["outside_scope"]["renamed"], 1)
        self.assertEqual(report["selected_scope"]["binary_files"], 1)
        self.assertEqual(report["selected_scope"]["insertions"], 3)
        self.assertEqual(
            reporting.commit_relationship(self.repo, self.base, head), "lane_ahead"
        )

    def test_copy_detection_and_unsafe_path_escaping(self) -> None:
        source = self.repo / "scope" / "keep.txt"
        (self.repo / "scope" / "copy.txt").write_bytes(source.read_bytes())
        raw_name = b"scope/control-\x01"
        descriptor = os.open(
            os.fsencode(self.repo) + b"/" + raw_name, os.O_WRONLY | os.O_CREAT, 0o600
        )
        try:
            os.write(descriptor, b"unsafe\n")
        finally:
            os.close(descriptor)
        head = self.commit("copies and unsafe paths")
        report = reporting.diff_statistics(
            self.repo, self.base, head, "scope", require_ancestry=True
        )
        self.assertGreaterEqual(report["full_repository"]["copied"], 1)
        rendered = json.dumps(report["files"], ensure_ascii=True)
        self.assertIn("\\\\x01", rendered)
        self.assertEqual(reporting.escape_git_path(b"non-utf8-\xff"), "non-utf8-\\xff")
        self.assertEqual(
            reporting._name_status(b"A\0non-utf8-\xff\0")[0][1][0], b"non-utf8-\xff"
        )

    def test_prepared_and_sealed_summary_bytes_are_immutable(self) -> None:
        run_dir = self.root / "run"
        (run_dir / "orchestration").mkdir(parents=True)
        summary = self.summary()
        raw = reporting.prepare_run_summary(run_dir, summary)
        prepared_path = run_dir / "orchestration" / reporting.PREPARED_NAME
        prepared_path.unlink()
        self.assertEqual(reporting.load_prepared_summary(run_dir), summary)
        self.assertEqual(prepared_path.read_bytes(), raw)
        queue = {"entries": [], "history": []}
        reporting.bind_summary_queue_head(run_dir, queue)
        sealed = reporting.seal_prepared_summary(run_dir)
        self.assertEqual(sealed, summary)
        self.assertEqual(reporting.sealed_summary_bytes(run_dir), raw)
        reporting.mark_handoff_published(run_dir)
        reporting.mark_finalization_complete(run_dir)
        self.assertEqual(reporting.summary_phase(run_dir), "complete")
        self.assertEqual(reporting.public_summary_response(run_dir), summary)

    def test_corrupt_prepared_and_sealed_summary_bytes_fail_closed(self) -> None:
        prepared_run = self.root / "prepared-corrupt"
        (prepared_run / "orchestration").mkdir(parents=True)
        reporting.prepare_run_summary(prepared_run, self.summary())
        (prepared_run / "orchestration" / reporting.PREPARED_NAME).write_bytes(b"{")
        with self.assertRaises(reporting.PromptWorkspaceError) as prepared_error:
            reporting.seal_prepared_summary(prepared_run)
        self.assertEqual(prepared_error.exception.code, "RUN_STATE_INVALID")

        non_object_run = self.root / "prepared-non-object"
        (non_object_run / "orchestration").mkdir(parents=True)
        reporting.prepare_run_summary(non_object_run, self.summary())
        (non_object_run / "orchestration" / reporting.PREPARED_NAME).write_bytes(
            b"null"
        )
        with self.assertRaises(reporting.PromptWorkspaceError) as non_object_error:
            reporting.seal_prepared_summary(non_object_run)
        self.assertEqual(non_object_error.exception.code, "RUN_STATE_INVALID")

        sealed_run = self.root / "sealed-corrupt"
        (sealed_run / "orchestration").mkdir(parents=True)
        reporting.prepare_run_summary(sealed_run, self.summary())
        reporting.seal_prepared_summary(sealed_run)
        (sealed_run / "orchestration" / reporting.SEALED_NAME).write_bytes(b"\xff")
        with self.assertRaises(reporting.PromptWorkspaceError) as sealed_error:
            reporting.public_summary_response(sealed_run)
        self.assertEqual(sealed_error.exception.code, "RUN_STATE_INVALID")

    def test_source_movement_is_observed_from_persisted_open_head(self) -> None:
        run_dir = self.root / "run"
        (run_dir / "orchestration").mkdir(parents=True)
        branch = git("branch", "--show-current", cwd=self.repo)
        workspace = {
            "source_ref": f"refs/heads/{branch}",
            "source_branch": branch,
        }
        reporting.record_source_head_at_open(
            run_dir, workspace["source_ref"], self.base
        )
        (self.repo / "source-moved.txt").write_text("moved\n", encoding="utf-8")
        moved = self.commit("source moved")
        repeated = reporting.record_source_head_at_open(
            run_dir, workspace["source_ref"], moved
        )
        self.assertEqual(repeated["source_head_at_open"], self.base)
        observation = reporting._source_observation(workspace, run_dir, self.repo)
        self.assertEqual(observation["status"], "moved")
        self.assertEqual(observation["source_head_at_open"], self.base)
        self.assertEqual(observation["source_head_at_completion"], moved)

    def test_queue_activation_binding_does_not_skip_generations(self) -> None:
        run_dir = self.root / "run"
        (run_dir / "orchestration").mkdir(parents=True)
        summary = self.summary()
        entry = {"queue_id": "queued-" + "1" * 32}
        queue = {"entries": [entry], "history": []}
        summary["queued_prompt"] = {"status": "activation_scheduled", "pending": 1}
        reporting.prepare_run_summary(run_dir, summary, queue)
        self.assertTrue(reporting.queue_activation_pending(run_dir, queue))
        resolved = {
            "entries": [],
            "history": [{**entry, "disposition": "activated"}],
        }
        self.assertFalse(reporting.queue_activation_pending(run_dir, resolved))

    def test_sealed_summary_rejects_unversioned_extra_structure(self) -> None:
        summary = self.summary()
        summary["private_run_id"] = "run-" + "1" * 32
        with self.assertRaises(reporting.PromptWorkspaceError) as raised:
            reporting.prepare_run_summary(self.root / "run", summary)
        self.assertEqual(raised.exception.code, "RUN_STATE_INVALID")

    def test_correction_task_count_comes_from_immutable_pending_plans(self) -> None:
        run_dir = self.root / "run"
        root = run_dir / "orchestration" / "pending-plans" / "wave-001"
        root.mkdir(parents=True)
        (root / "plan.json").write_text(
            json.dumps(
                {
                    "tasks": [
                        {"task_id": "task-2"},
                        {"task_id": "task-3"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(reporting._correction_ids(run_dir), {"task-2", "task-3"})

    def test_git_report_rejects_output_above_bound(self) -> None:
        with mock.patch.object(reporting, "GIT_OUTPUT_LIMIT", 1):
            with self.assertRaises(reporting.PromptWorkspaceError) as raised:
                reporting._git_bytes(
                    self.repo, ["rev-parse", "HEAD"], "read an oversized report"
                )
        self.assertEqual(raised.exception.code, "GIT_REPORT_FAILED")

    def test_git_report_timeout_terminates_the_process_group(self) -> None:
        sentinel = self.root / "late-child-output"
        child = (
            "import time; time.sleep(0.2); "
            f"open({str(sentinel)!r}, 'w', encoding='utf-8').write('ran')"
        )
        executable = self.root / "slow-git"
        executable.write_text(
            "#!/usr/bin/env python3\n"
            "import subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        executable.chmod(0o700)
        with (
            mock.patch.object(reporting, "GIT_EXECUTABLE", executable),
            mock.patch.object(reporting, "GIT_TIMEOUT_SECONDS", 0.05),
            self.assertRaises(reporting.PromptWorkspaceError) as raised,
        ):
            reporting._git_bytes(self.repo, [], "run a slow report")
        self.assertEqual(raised.exception.code, "GIT_REPORT_FAILED")
        time.sleep(0.35)
        self.assertFalse(sentinel.exists())

    def test_project_invocation_escapes_shell_metacharacters(self) -> None:
        invocation = reporting._quoted_invocation(
            Path('/project/$(touch sentinel)-`command`-"quoted"')
        )
        self.assertEqual(
            invocation,
            '$task-implementer integrate "/project/\\$(touch sentinel)-\\`command\\`-\\"quoted\\""',
        )
        with self.assertRaises(reporting.PromptWorkspaceError) as raised:
            reporting._quoted_invocation(Path("/project/line\nbreak"))
        self.assertEqual(raised.exception.code, "WORKSPACE_PATH_INVALID")

    def test_report_git_is_index_neutral_and_suppresses_configured_helpers(
        self,
    ) -> None:
        sentinel = self.root / "helper-ran"
        helper = self.root / "configured-helper.sh"
        helper.write_text(
            f"#!/bin/sh\nprintf ran > '{sentinel}'\n",
            encoding="utf-8",
        )
        helper.chmod(0o700)
        git("config", "core.fsmonitor", str(helper), cwd=self.repo)
        index = self.repo / ".git" / "index"
        before = (index.read_bytes(), index.stat().st_mtime_ns)
        with mock.patch.dict(
            os.environ,
            {"GIT_EXTERNAL_DIFF": str(helper), "GIT_OPTIONAL_LOCKS": "1"},
            clear=False,
        ):
            self.assertTrue(reporting._clean(self.repo))
            reporting.diff_statistics(
                self.repo, self.base, self.base, "scope", require_ancestry=True
            )
        self.assertFalse(sentinel.exists())
        self.assertEqual((index.read_bytes(), index.stat().st_mtime_ns), before)

    def test_lane_summary_budget_applies_only_to_requested_pending_generations(
        self,
    ) -> None:
        runs_root = self.root / "runs"
        runs_root.mkdir()
        for generation in range(1, reporting.PENDING_GENERATIONS_LIMIT + 2):
            (runs_root / f"run-history-{generation}").mkdir()

        def load_interop(run_dir: Path, *, required: bool = True) -> dict[str, object]:
            del required
            return {
                "mode": "lane",
                "released": True,
                "generation": int(run_dir.name.rsplit("-", 1)[1]),
            }

        latest = reporting.PENDING_GENERATIONS_LIMIT + 1
        with (
            mock.patch(
                "prompt_workspace_interop.load_interop",
                side_effect=load_interop,
            ),
            mock.patch.object(
                reporting,
                "_run_matches_workspace_incarnation",
                return_value=True,
            ),
            mock.patch.object(
                reporting,
                "load_sealed_summary",
                return_value=self.summary(),
            ) as load_summary,
            mock.patch.object(reporting, "_validate_run_binding"),
        ):
            rows = list(reporting.sealed_summaries(runs_root, {}, {latest}))

        self.assertEqual([generation for generation, _ in rows], [latest])
        load_summary.assert_called_once()

    def test_current_identity_mismatch_fails_closed_but_prior_history_is_skipped(
        self,
    ) -> None:
        runs_root = self.root / "runs"
        current = runs_root / "run-current"
        current.mkdir(parents=True)
        incomplete = {"mode": "lane", "released": False, "generation": 2}
        with (
            mock.patch(
                "prompt_workspace_interop.load_interop",
                return_value=incomplete,
            ),
            mock.patch.object(reporting, "summary_phase", return_value="prepared"),
            mock.patch.object(
                reporting,
                "_run_matches_workspace_incarnation",
                return_value=False,
            ),
            self.assertRaises(reporting.PromptWorkspaceError) as pending_error,
        ):
            reporting.pending_finalization_generations(runs_root, {})
        self.assertEqual(pending_error.exception.code, "RUN_STATE_INVALID")

        released = {"mode": "lane", "released": True, "generation": 2}
        with (
            mock.patch(
                "prompt_workspace_interop.load_interop",
                return_value=released,
            ),
            mock.patch.object(
                reporting,
                "_run_matches_workspace_incarnation",
                return_value=False,
            ),
            self.assertRaises(reporting.PromptWorkspaceError) as summary_error,
        ):
            list(reporting.sealed_summaries(runs_root, {}, {2}))
        self.assertEqual(summary_error.exception.code, "RUN_STATE_INVALID")

        prior = {"mode": "lane", "released": True, "generation": 1}
        with (
            mock.patch(
                "prompt_workspace_interop.load_interop",
                return_value=prior,
            ),
            mock.patch.object(reporting, "summary_phase", return_value="complete"),
            mock.patch.object(
                reporting,
                "_run_matches_workspace_incarnation",
                return_value=False,
            ),
        ):
            self.assertEqual(
                reporting.pending_finalization_generations(runs_root, {}), set()
            )
            self.assertEqual(list(reporting.sealed_summaries(runs_root, {}, {2})), [])

    def test_removed_lane_loads_only_unique_latest_historical_summary(self) -> None:
        runs_root = self.root / "removed-runs"
        runs_root.mkdir()
        for generation in range(1, reporting.PENDING_GENERATIONS_LIMIT + 2):
            orchestration = runs_root / f"run-removed-{generation}" / "orchestration"
            orchestration.mkdir(parents=True)
            (orchestration / reporting.SEALED_NAME).touch()

        def load_interop(run_dir: Path, *, required: bool = True) -> dict[str, object]:
            del required
            return {
                "mode": "lane",
                "released": True,
                "generation": int(run_dir.name.rsplit("-", 1)[1]),
            }

        with (
            mock.patch(
                "prompt_workspace_interop.load_interop",
                side_effect=load_interop,
            ),
            mock.patch.object(
                reporting,
                "_run_matches_workspace_incarnation",
                return_value=True,
            ),
            mock.patch.object(
                reporting,
                "load_sealed_summary",
                return_value=self.summary(),
            ) as load_summary,
            mock.patch.object(reporting, "_validate_run_binding"),
        ):
            self.assertEqual(
                reporting._latest_sealed_summary(runs_root, {}), self.summary()
            )
        load_summary.assert_called_once()

        duplicate_root = self.root / "duplicate-runs"
        for name in ("run-duplicate-a", "run-duplicate-b"):
            orchestration = duplicate_root / name / "orchestration"
            orchestration.mkdir(parents=True)
            (orchestration / reporting.SEALED_NAME).touch()
        duplicate = {"mode": "lane", "released": True, "generation": 7}
        with (
            mock.patch("prompt_workspace_interop.load_interop", return_value=duplicate),
            mock.patch.object(
                reporting,
                "_run_matches_workspace_incarnation",
                return_value=True,
            ),
            self.assertRaises(reporting.PromptWorkspaceError) as duplicate_error,
        ):
            reporting._latest_sealed_summary(duplicate_root, {})
        self.assertEqual(duplicate_error.exception.code, "RUN_STATE_INVALID")


if __name__ == "__main__":
    unittest.main()
