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
