#!/usr/bin/env python3
"""Disposable functional tests for the private prompt workspace helper."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import prompt_workspace_intake as intake
import prompt_workspace_core as core
import prompt_workspace_lanes as lanes
import prompt_workspace_runs as runs


SCRIPT = Path(__file__).resolve().with_name("prompt_workspace.py")
SPEC = importlib.util.spec_from_file_location("prompt_workspace", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import invariant.
    raise RuntimeError("could not load prompt_workspace.py")
pw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pw
SPEC.loader.exec_module(pw)


FIXED_LOCAL = datetime(2026, 7, 12, 14, 30, tzinfo=timezone.utc)
FIXED_UTC = datetime(2026, 7, 12, 21, 30, tzinfo=timezone.utc)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class PromptWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.origin = self.root / "origin.git"
        git("init", "--bare", "-q", str(self.origin), cwd=self.root)
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        git("init", "-q", "-b", "main", cwd=self.repo)
        self.scope = self.repo / "services" / "example"
        self.scope.mkdir(parents=True)
        (self.repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        (self.scope / "scope.txt").write_text("scope\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git(
            "-c",
            "user.name=Prompt Test",
            "-c",
            "user.email=prompt@example.invalid",
            "commit",
            "-qm",
            "initial",
            cwd=self.repo,
        )
        git("remote", "add", "origin", str(self.origin), cwd=self.repo)
        git("push", "-q", "origin", "main", cwd=self.repo)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.origin)
        git("fetch", "-q", "origin", cwd=self.repo)
        git(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
            cwd=self.repo,
        )
        git("switch", "-qc", "prompt-feature", cwd=self.repo)
        self.codex_home = self.root / "private codex"
        self.lane = lanes.ensure_project_lane(self.scope)
        self.lane_root = Path(str(self.lane["worktree"]))
        self.lane_scope = Path(str(self.lane["scope_cwd"]))
        self.workspace_result = pw.init_workspace(
            self.lane_root,
            "services/example",
            self.codex_home,
            lane=self.lane,
            clock=lambda: FIXED_LOCAL,
        )
        self.workspace = Path(self.workspace_result["workspace"])
        self.prompt_root = Path(self.workspace_result["prompt_root"])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_task_result_publish_emits_without_optional_json_flag(self) -> None:
        published = {"status": "published", "result_sha256": "a" * 64}
        assignment = self.root / "assignment.json"
        draft = self.root / "draft.json"
        result = self.root / "result.json"

        with (
            mock.patch.object(pw, "publish_task_result", return_value=published),
            mock.patch.object(pw, "emit") as emit,
        ):
            status = pw.main(
                [
                    "task-result-publish",
                    "--assignment",
                    str(assignment),
                    "--draft",
                    str(draft),
                    "--result",
                    str(result),
                ]
            )

        self.assertEqual(status, 0)
        emit.assert_called_once_with(published, False)

    def new_prompt(
        self,
        ask: str = "Add prompt workspace support",
        prompt_hex: str = "a" * 32,
    ) -> Path:
        result = pw.create_prompt(
            self.workspace,
            ask,
            clock=lambda: FIXED_LOCAL,
            id_factory=lambda: prompt_hex,
        )
        return Path(result["path"])

    def complete_prompt(self, path: Path, *, sentinel: str = "") -> None:
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "<!-- Required: replace this comment with your Ask. -->",
            "Implement the requested prompt workflow behavior.",
        )
        text = text.rstrip() + (
            "\n\n## Outcome\n\nThe private prompt workflow is usable.\n"
            "\n## Acceptance criteria\n\n- [ ] The focused tests pass.\n"
            "\n## Verification\n\nRun the focused prompt workspace tests.\n"
        )
        if sentinel:
            text += f"\n## Context\n\n{sentinel}\n"
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)

    def write_handoff(
        self,
        run_id: str,
        *,
        status: str = "prepared",
        bound_revision: str | None = None,
        last_invoked_at: datetime | None = None,
    ) -> None:
        run_dir = (
            Path(json.loads(self.workspace.read_text(encoding="utf-8"))["runs_root"])
            / run_id
        )
        manifest_path = run_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if bound_revision is None:
            bound = manifest["revisions"][-1]
        else:
            bound = next(
                revision
                for revision in manifest["revisions"]
                if revision["revision"] == bound_revision
            )
        handoff = run_dir / "handoff.md"
        run_lines = [
            "# Task Implementer Handoff",
            "",
            "## Run",
            "",
            f"- Run ID: {run_id}",
            f"- Run manifest: {manifest_path}",
            f"- Prompt ID: {manifest['prompt_id']}",
            f"- Bound revision: {bound['revision']}",
            f"- Bound SHA-256: {bound['sha256']}",
        ]
        if last_invoked_at is not None:
            run_lines.append(
                f"- Last invoked at: {last_invoked_at.isoformat(timespec='seconds')}"
            )
        run_lines.extend(
            [
                f"- Overall status: {status}",
                "",
                "## Checkpoints",
                "",
                f"- Bound revision: {bound['revision']}",
                "",
            ]
        )
        handoff.write_text(
            "\n".join(run_lines),
            encoding="utf-8",
        )
        handoff.chmod(0o600)

    def mark_run_done(self, run_id: str) -> None:
        self.write_handoff(run_id, status="done")

    def resolve_steering(
        self,
        run_id: str,
        revision: str,
        disposition: str = "applied",
    ) -> None:
        runs_root = Path(
            json.loads(self.workspace.read_text(encoding="utf-8"))["runs_root"]
        )
        run_dir = runs_root / run_id
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        pw.resolve_steering_revision(
            run_dir,
            manifest["revisions"],
            revision,
            disposition,
            clock=lambda: FIXED_UTC + timedelta(minutes=1),
        )

    def assert_error(
        self, code: str, function: object, *args: object, **kwargs: object
    ) -> None:
        with self.assertRaises(pw.PromptWorkspaceError) as context:
            function(*args, **kwargs)
        self.assertEqual(context.exception.code, code)

    def test_init_is_idempotent_external_and_git_clean(self) -> None:
        status_before = git("status", "--porcelain=v1", cwd=self.repo)
        manifest_before = self.workspace.read_bytes()
        vscode = Path(self.workspace_result["vscode_workspace"])
        vscode_before = vscode.read_bytes()

        repeated = pw.init_workspace(
            self.lane_root,
            "services/example",
            self.codex_home,
            lane=self.lane,
            clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(repeated, self.workspace_result)
        self.assertEqual(self.workspace.read_bytes(), manifest_before)
        self.assertEqual(vscode.read_bytes(), vscode_before)
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), status_before)
        self.assertFalse(self.workspace.is_relative_to(self.repo))

        if os.name == "posix":
            alias = self.root / "repo-alias"
            alias.symlink_to(self.lane_root, target_is_directory=True)
            aliased = pw.init_workspace(
                alias,
                "services/example",
                self.codex_home,
                lane=self.lane,
                clock=lambda: FIXED_LOCAL,
            )
            self.assertEqual(aliased["workspace"], str(self.workspace))

    def test_init_detects_primary_legacy_workspace_from_source_or_lane(self) -> None:
        legacy_home = self.root / "legacy codex home"
        from_source = core.legacy_project_workspace_manifest(self.scope, legacy_home)
        from_lane = core.legacy_project_workspace_manifest(self.lane_scope, legacy_home)
        self.assertEqual(from_source, from_lane)
        from_source.parent.mkdir(parents=True)
        from_source.write_text("{}\n", encoding="utf-8")

        for invocation in (self.scope, self.lane_scope):
            with self.subTest(invocation=invocation):
                self.assert_error(
                    "WORKFLOW_UPGRADE_REQUIRED",
                    pw.initialize_project_workspace,
                    invocation,
                    legacy_home,
                )

    def test_project_init_defaults_to_cwd_and_preserves_starter(self) -> None:
        project_home = self.root / "project init home"
        command = [
            sys.executable,
            str(SCRIPT),
            "init",
            "--codex-home",
            str(project_home),
            "--no-open",
            "--json",
        ]
        first = subprocess.run(
            command,
            cwd=self.scope,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        first_result = json.loads(first.stdout)
        self.assertNotIn("project_id", first_result)
        self.assertNotIn("scope_id", first_result)
        self.assertNotIn("lane_id", first_result)
        starter = Path(first_result["starter_prompt"])
        self.assertTrue(first_result["starter_created"])
        self.assertTrue(starter.is_file())
        starter_bytes = starter.read_bytes()
        starter_mtime = starter.stat().st_mtime_ns

        repeated = subprocess.run(
            [*command[:3], str(self.scope), *command[3:]],
            cwd=self.repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        repeated_result = json.loads(repeated.stdout)
        self.assertEqual(repeated_result["workspace"], first_result["workspace"])
        self.assertFalse(repeated_result["starter_created"])
        self.assertEqual(starter.read_bytes(), starter_bytes)
        self.assertEqual(starter.stat().st_mtime_ns, starter_mtime)
        self.assertEqual(len(list(starter.parent.glob("*.md"))), 2)
        self.assertTrue((starter.parent / core.HUB_FILENAME).is_file())

        manifest = json.loads(
            Path(first_result["workspace"]).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["source_root"], str(self.lane_scope))
        vscode = Path(manifest["vscode_workspace"])
        vscode_bytes = vscode.read_bytes()
        vscode.write_text("{}\n", encoding="utf-8")
        vscode.chmod(0o600)
        relative = subprocess.run(
            [*command[:3], "services/example", *command[3:]],
            cwd=self.repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(relative.returncode, 0, relative.stdout + relative.stderr)
        relative_result = json.loads(relative.stdout)
        self.assertEqual(relative_result["workspace"], first_result["workspace"])
        self.assertEqual(vscode.read_bytes(), vscode_bytes)
        self.assertEqual(starter.read_bytes(), starter_bytes)
        self.assertEqual(starter.stat().st_mtime_ns, starter_mtime)
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), "")

    def test_project_init_succeeds_when_editor_is_unavailable(self) -> None:
        project_home = self.root / "missing editor home"
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "init",
                str(self.scope),
                "--codex-home",
                str(project_home),
                "--editor",
                "task-implementer-editor-that-does-not-exist",
                "--json",
            ],
            cwd=self.repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WARN editor executable is unavailable", result.stderr)
        self.assertTrue(Path(json.loads(result.stdout)["workspace"]).is_file())
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), "")

    def test_workspace_reuse_resolves_source_and_lane_without_mutation(self) -> None:
        dirty = self.lane_root / "reuse-dirty.txt"
        dirty.write_text("preserve\n", encoding="utf-8")
        vscode = Path(self.workspace_result["vscode_workspace"])
        workspace_before = self.workspace.read_bytes()
        workspace_mtime = self.workspace.stat().st_mtime_ns
        vscode_before = vscode.read_bytes()
        vscode_mtime = vscode.stat().st_mtime_ns
        prompt_entries = sorted(path.name for path in self.prompt_root.iterdir())

        from_source = pw.reuse_project_workspace(self.scope, self.codex_home)
        from_lane = pw.reuse_project_workspace(self.lane_scope, self.codex_home)

        self.assertEqual(from_source, from_lane)
        self.assertEqual(from_source["status"], "reused")
        self.assertEqual(from_source["workspace"], str(self.workspace))
        self.assertEqual(from_source["vscode_workspace"], str(vscode))
        self.assertEqual(from_source["lane_state"], "idle")
        self.assertEqual(from_source["lane_worktree"], str(self.lane_root))
        self.assertEqual(self.workspace.read_bytes(), workspace_before)
        self.assertEqual(self.workspace.stat().st_mtime_ns, workspace_mtime)
        self.assertEqual(vscode.read_bytes(), vscode_before)
        self.assertEqual(vscode.stat().st_mtime_ns, vscode_mtime)
        self.assertEqual(
            sorted(path.name for path in self.prompt_root.iterdir()), prompt_entries
        )
        self.assertEqual(
            git("status", "--porcelain=v1", cwd=self.lane_root),
            "?? reuse-dirty.txt",
        )

    def test_workspace_reuse_blocks_missing_and_mismatched_state(self) -> None:
        missing_home = self.root / "missing reuse home"
        self.assert_error(
            "WORKSPACE_NOT_FOUND",
            pw.reuse_project_workspace,
            self.scope,
            missing_home,
        )
        self.assertFalse(missing_home.exists())

        manifest = json.loads(self.workspace.read_text(encoding="utf-8"))
        manifest["lane_incarnation"] = int(manifest["lane_incarnation"]) + 1
        self.workspace.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.workspace.chmod(0o600)
        self.assert_error(
            "WORKTREE_CONFLICT",
            pw.reuse_project_workspace,
            self.scope,
            self.codex_home,
        )

    def test_workspace_reuse_rejects_an_unrelated_worktree_spoof(self) -> None:
        unrelated_root = self.root / "unrelated worktree"
        git(
            "worktree",
            "add",
            "-q",
            "-b",
            "unrelated-feature",
            str(unrelated_root),
            cwd=self.repo,
        )
        unrelated_scope = unrelated_root / "services" / "example"
        git(
            "config",
            "--local",
            "branch.unrelated-feature.worktreeSkillTaskLaneSourceRef",
            "refs/heads/prompt-feature",
            cwd=self.repo,
        )
        self.assertEqual(
            core.project_workspace_manifest(unrelated_scope, self.codex_home),
            self.workspace,
        )

        self.assert_error(
            "WORKSPACE_MISMATCH",
            pw.reuse_project_workspace,
            unrelated_scope,
            self.codex_home,
        )

    def test_workspace_reuse_rejects_a_deleted_source_ref_before_editor(self) -> None:
        git("switch", "-q", "main", cwd=self.repo)
        git("branch", "-D", "prompt-feature", cwd=self.repo)
        stderr = io.StringIO()

        with (
            mock.patch.object(pw, "open_in_editor") as open_editor,
            mock.patch.object(pw.sys, "stderr", stderr),
        ):
            result = pw.main(
                [
                    "reuse",
                    str(self.lane_scope),
                    "--codex-home",
                    str(self.codex_home),
                    "--json",
                ]
            )

        self.assertEqual(result, 2)
        self.assertIn("WORKTREE_CONFLICT", stderr.getvalue())
        open_editor.assert_not_called()

    def test_workspace_reuse_rejects_an_unsafe_manifest_before_editor(self) -> None:
        real_manifest = self.workspace.with_name("workspace-real.json")
        self.workspace.rename(real_manifest)
        self.workspace.symlink_to(real_manifest)
        stderr = io.StringIO()

        with (
            mock.patch.object(pw, "open_in_editor") as open_editor,
            mock.patch.object(pw.sys, "stderr", stderr),
        ):
            result = pw.main(
                [
                    "reuse",
                    str(self.scope),
                    "--codex-home",
                    str(self.codex_home),
                    "--json",
                ]
            )

        self.assertEqual(result, 2)
        self.assertIn("WORKSPACE_PATH_INVALID", stderr.getvalue())
        open_editor.assert_not_called()

    def test_workspace_reuse_rejects_a_removed_lane_before_editor(self) -> None:
        workspace = json.loads(self.workspace.read_text(encoding="utf-8"))
        removed = lanes.remove_lane(workspace)
        self.assertEqual(removed["status"], "removed")
        stderr = io.StringIO()

        with (
            mock.patch.object(pw, "open_in_editor") as open_editor,
            mock.patch.object(pw.sys, "stderr", stderr),
        ):
            result = pw.main(
                [
                    "reuse",
                    str(self.scope),
                    "--codex-home",
                    str(self.codex_home),
                    "--json",
                ]
            )

        self.assertEqual(result, 2)
        self.assertIn("REPO_ROOT_INVALID", stderr.getvalue())
        open_editor.assert_not_called()

    def test_workspace_reuse_succeeds_when_editor_is_unavailable(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "reuse",
                str(self.scope),
                "--codex-home",
                str(self.codex_home),
                "--editor",
                "task-implementer-editor-that-does-not-exist",
                "--json",
            ],
            cwd=self.repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        reuse_result = json.loads(result.stdout)
        self.assertEqual(reuse_result["status"], "reused")
        self.assertNotIn("project_id", reuse_result)
        self.assertNotIn("scope_id", reuse_result)
        self.assertNotIn("lane_id", reuse_result)
        self.assertIn("WARN editor executable is unavailable", result.stderr)
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), "")

    def test_workspace_reuse_human_output_redacts_ids_and_defaults_to_cwd(
        self,
    ) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "reuse",
                "--codex-home",
                str(self.codex_home),
                "--no-open",
            ],
            cwd=self.scope,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("status: reused", result.stdout)
        self.assertNotIn("project_id", result.stdout)
        self.assertNotIn("scope_id", result.stdout)
        self.assertNotIn("lane_id", result.stdout)

    def test_open_in_editor_reuses_last_active_window(self) -> None:
        target = self.root / "workspace with spaces.code-workspace"
        cases = (
            (True, ["code", "--reuse-window", str(target)]),
            (False, ["code", "--reuse-window", "--goto", str(target)]),
        )

        for workspace, expected in cases:
            with self.subTest(workspace=workspace):
                with mock.patch.object(pw.subprocess, "run") as run:
                    run.return_value = subprocess.CompletedProcess(expected, 0)
                    pw.open_in_editor("code", target, workspace=workspace)

                run.assert_called_once_with(expected, check=False, timeout=15)

    def test_open_in_editor_warns_for_timeout_and_nonzero_status(self) -> None:
        target = self.root / "workspace.code-workspace"
        cases = (
            (
                PermissionError("editor is not executable"),
                "WARN editor could not be launched",
            ),
            (
                subprocess.TimeoutExpired(["code", str(target)], 15),
                "WARN editor did not return promptly",
            ),
            (
                subprocess.CompletedProcess(["code", str(target)], 7),
                "WARN editor exited with status 7",
            ),
        )

        for outcome, warning in cases:
            with self.subTest(warning=warning):
                stderr = io.StringIO()
                with (
                    mock.patch.object(pw.subprocess, "run") as run,
                    mock.patch.object(pw.sys, "stderr", stderr),
                ):
                    if isinstance(outcome, BaseException):
                        run.side_effect = outcome
                    else:
                        run.return_value = outcome
                    pw.open_in_editor("code", target, workspace=True)

                self.assertIn(warning, stderr.getvalue())

    def test_project_init_preserves_prompts_and_run_history(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(
            snapshot["run_id"],
            status="done",
            last_invoked_at=FIXED_UTC,
        )
        prompt_bytes = prompt.read_bytes()
        prompt_mtime = prompt.stat().st_mtime_ns
        manifest_path = Path(snapshot["manifest"])
        manifest_bytes = manifest_path.read_bytes()
        handoff = manifest_path.parent / "handoff.md"
        handoff_bytes = handoff.read_bytes()

        result = pw.initialize_project_workspace(
            self.scope,
            self.codex_home,
            clock=lambda: FIXED_LOCAL.replace(second=5),
        )

        self.assertFalse(result["starter_created"])
        self.assertEqual(prompt.read_bytes(), prompt_bytes)
        self.assertEqual(prompt.stat().st_mtime_ns, prompt_mtime)
        self.assertEqual(manifest_path.read_bytes(), manifest_bytes)
        self.assertEqual(handoff.read_bytes(), handoff_bytes)
        self.assertEqual(result["prompts"][0]["path"], str(prompt))
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), "")

    def test_workspace_remove_then_init_rebinds_lane_and_preserves_prompts(
        self,
    ) -> None:
        prompt = self.new_prompt()
        prompt_bytes = prompt.read_bytes()
        before = json.loads(self.workspace.read_text(encoding="utf-8"))

        removed = lanes.remove_lane(before)
        self.assertEqual(removed["status"], "removed")
        self.assertFalse(Path(str(before["repo_root"])).exists())

        initialized = pw.initialize_project_workspace(
            self.scope,
            self.codex_home,
            clock=lambda: FIXED_LOCAL + timedelta(minutes=1),
        )
        after = json.loads(self.workspace.read_text(encoding="utf-8"))
        self.assertEqual(initialized["workspace"], str(self.workspace))
        self.assertFalse(initialized["starter_created"])
        self.assertEqual(prompt.read_bytes(), prompt_bytes)
        self.assertEqual(after["lane_id"], before["lane_id"])
        self.assertGreater(after["lane_incarnation"], before["lane_incarnation"])
        self.assertNotEqual(after["repo_root"], before["repo_root"])

    def test_workspace_remove_is_idempotent_after_lane_path_is_absent(self) -> None:
        command = [
            sys.executable,
            str(SCRIPT),
            "remove",
            str(self.scope),
            "--codex-home",
            str(self.codex_home),
            "--json",
        ]

        first = subprocess.run(
            command,
            cwd=self.repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(json.loads(first.stdout)["status"], "removed")
        self.assertFalse(self.lane_root.exists())

        repeated = subprocess.run(
            command,
            cwd=self.repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
        self.assertEqual(json.loads(repeated.stdout)["status"], "already-removed")

    def test_workspace_remove_rejects_tampered_destructive_identity(self) -> None:
        original = self.workspace.read_bytes()
        cases = {
            "lane_id": "0" * 32,
            "primary_root": str(self.root / "other-repository"),
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                manifest = json.loads(original)
                manifest[field] = value
                self.workspace.write_bytes(core.stable_json(manifest))
                self.workspace.chmod(0o600)
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT),
                        "remove",
                        str(self.scope),
                        "--codex-home",
                        str(self.codex_home),
                        "--json",
                    ],
                    cwd=self.repo,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("WORKSPACE_MISMATCH", result.stderr)
                self.assertTrue(self.lane_root.is_dir())
        self.workspace.write_bytes(original)
        self.workspace.chmod(0o600)

    def test_concurrent_project_init_is_idempotent(self) -> None:
        project_home = self.root / "concurrent init home"
        command = [
            sys.executable,
            str(SCRIPT),
            "init",
            str(self.scope),
            "--codex-home",
            str(project_home),
            "--no-open",
            "--json",
        ]
        processes = [
            subprocess.Popen(
                command,
                cwd=self.repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for _ in range(2)
        ]
        results = [process.communicate(timeout=15) for process in processes]
        for process, (stdout, stderr) in zip(processes, results, strict=True):
            self.assertEqual(process.returncode, 0, stdout + stderr)
        parsed = [json.loads(stdout) for stdout, _ in results]
        self.assertEqual(parsed[0]["workspace"], parsed[1]["workspace"])
        self.assertEqual(
            sum(bool(result["starter_created"]) for result in parsed),
            1,
        )
        prompt_root = Path(parsed[0]["prompt_root"])
        self.assertEqual(len(list(prompt_root.glob("*.md"))), 2)
        self.assertTrue((prompt_root / core.HUB_FILENAME).is_file())
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), "")

    def test_init_isolates_clones_and_scopes(self) -> None:
        root_lane = lanes.ensure_project_lane(self.repo)
        root_scope = pw.init_workspace(
            Path(str(root_lane["worktree"])),
            ".",
            self.codex_home,
            lane=root_lane,
            clock=lambda: FIXED_LOCAL,
        )
        self.assertNotEqual(root_scope["scope_id"], self.workspace_result["scope_id"])

        second_parent = self.root / "second"
        second_repo = second_parent / self.repo.name
        second_parent.mkdir()
        git("clone", "-q", str(self.origin), str(second_repo), cwd=self.root)
        git("switch", "-qc", "clone-feature", cwd=second_repo)
        clone_lane = lanes.ensure_project_lane(second_repo)
        clone = pw.init_workspace(
            Path(str(clone_lane["worktree"])),
            ".",
            self.codex_home,
            lane=clone_lane,
            clock=lambda: FIXED_LOCAL,
        )
        self.assertNotEqual(clone["project_id"], self.workspace_result["project_id"])

        manifest = json.loads(self.workspace.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], pw.WORKSPACE_SCHEMA)
        self.assertEqual(manifest["repo_root"], str(self.lane_root))
        self.assertEqual(manifest["scope"], "services/example")
        self.assertEqual(manifest["source_root"], str(self.lane_scope))
        self.assertEqual(manifest["prompt_root"], str(self.prompt_root.resolve()))

    def test_init_rejects_unsafe_locations(self) -> None:
        self.assert_error(
            "SCOPE_INVALID",
            pw.init_workspace,
            self.repo,
            "../outside",
            self.codex_home,
        )
        self.assert_error(
            "SCOPE_INVALID",
            pw.init_workspace,
            self.repo,
            "missing",
            self.codex_home,
        )
        self.assert_error(
            "WORKSPACE_PATH_INVALID",
            pw.init_workspace,
            self.lane_root,
            "services/example",
            self.repo / ".codex",
            lane=self.lane,
        )
        self.assert_error(
            "WORKSPACE_PATH_INVALID",
            pw.init_workspace,
            self.lane_root,
            "services/example",
            self.repo / ".git" / "private-codex",
            lane=self.lane,
        )

        if os.name == "posix":
            outside = self.root / "outside"
            outside.mkdir()
            escaped = self.repo / "escaped"
            escaped.symlink_to(outside, target_is_directory=True)
            self.assert_error(
                "SCOPE_INVALID",
                pw.init_workspace,
                self.repo,
                "escaped",
                self.codex_home,
            )

    def test_init_rejects_storage_inside_another_git_worktree(self) -> None:
        foreign_repo = self.root / "unrelated worktree"
        foreign_repo.mkdir()
        git("init", "-q", cwd=foreign_repo)
        foreign_home = foreign_repo / "private codex"

        self.assert_error(
            "WORKSPACE_PATH_INVALID",
            pw.init_workspace,
            self.lane_root,
            "services/example",
            foreign_home,
            lane=self.lane,
        )
        self.assertFalse((foreign_home / "task-implementer").exists())
        self.assertEqual(git("status", "--porcelain=v1", cwd=foreign_repo), "")

    @unittest.skipUnless(os.name == "posix", "POSIX symlink check")
    def test_init_and_verify_reject_storage_symlinks(self) -> None:
        other_home = self.root / "other codex"
        other_home.mkdir()
        external_state = self.root / "external state"
        external_state.mkdir()
        (other_home / "task-implementer").symlink_to(
            external_state, target_is_directory=True
        )
        self.assert_error(
            "WORKSPACE_PATH_INVALID",
            pw.init_workspace,
            self.lane_root,
            "services/example",
            other_home,
            lane=self.lane,
        )

        shutil.rmtree(self.prompt_root)
        external_prompts = self.root / "external prompts"
        external_prompts.mkdir(mode=0o700)
        self.prompt_root.symlink_to(external_prompts, target_is_directory=True)
        self.assert_error(
            "WORKSPACE_PATH_INVALID",
            pw.verify_command,
            self.workspace,
            None,
            None,
        )
        self.assert_error(
            "WORKSPACE_PATH_INVALID",
            pw.create_prompt,
            self.workspace,
            "Must not escape",
        )
        self.assertEqual(list(external_prompts.iterdir()), [])

    def test_workspace_json_contract(self) -> None:
        vscode_path = Path(self.workspace_result["vscode_workspace"])
        value = json.loads(vscode_path.read_text(encoding="utf-8"))
        self.assertEqual(
            value["folders"],
            [
                {"name": "CODE", "path": str(self.lane_scope)},
                {"name": "PROMPTS", "path": "prompts"},
            ],
        )
        tasks = value["tasks"]
        self.assertEqual(tasks["version"], "2.0.0")
        self.assertEqual(len(tasks["tasks"]), 3)
        self.assertEqual(
            [item["label"] for item in tasks["tasks"]],
            [
                "Task Implementer: New Prompt",
                "Task Implementer: Prompt Queue",
                "Task Implementer: Cancel Queued Prompt",
            ],
        )
        task = tasks["tasks"][0]
        self.assertEqual(task["label"], "Task Implementer: New Prompt")
        self.assertEqual(task["type"], "process")
        self.assertEqual(Path(task["args"][0]).name, "prompt_workspace.py")
        self.assertIsInstance(task["args"], list)
        self.assertEqual(task["group"], {"kind": "build", "isDefault": True})
        self.assertNotIn("runOptions", task)
        self.assertNotIn("extensions", value)
        self.assertNotIn("security.workspace.trust", json.dumps(value))
        self.assertEqual(tasks["inputs"][0]["type"], "promptString")
        self.assertRegex(self.workspace_result["scope_id"], r"-[0-9a-f]{8}$")
        with mock.patch.object(core.sys, "executable", "/bin/sh"):
            verified = pw.verify_workspace(self.workspace)
        self.assertEqual(verified["scope"], "services/example")

    def test_slug_rules(self) -> None:
        self.assertEqual(
            pw.prompt_slug("Add prompt workspace support"),
            "add-prompt-workspace-support",
        )
        self.assertEqual(pw.prompt_slug("déjà vu 🔒"), "deja-vu")
        self.assertEqual(pw.prompt_slug("🔒"), "prompt")
        self.assertEqual(
            pw.prompt_slug("one two three four five six seven eight nine ten"),
            "one-two-three-four-five-six-seven-eight",
        )
        self.assertLessEqual(len(pw.prompt_slug("x" * 100)), 60)

    @unittest.skipUnless(os.name == "posix", "POSIX mode checks")
    def test_new_naming_template_permissions_and_collision(self) -> None:
        first = self.new_prompt()
        second = self.new_prompt(prompt_hex="b" * 32)
        self.assertEqual(
            first.name,
            "aaaaa--2026-07-12_1430--add-prompt-workspace-support.md",
        )
        self.assertEqual(
            second.name,
            "bbbbb--2026-07-12_1430--add-prompt-workspace-support.md",
        )
        text = first.read_text(encoding="utf-8")
        self.assertIn(f"schema: {pw.PROMPT_SCHEMA}", text)
        self.assertIn(f"prompt_id: prompt-{'a' * 32}", text)
        self.assertIn("prompt_ref: aaaaa", text)
        self.assertIn('title: "Add prompt workspace support"', text)
        self.assertIn("created_at: 2026-07-12T14:30:00+00:00", text)
        self.assertNotIn("## Acceptance criteria", text)
        self.assertIn("Only Ask is required", text)

        manifest = json.loads(self.workspace.read_text(encoding="utf-8"))
        for directory in (
            self.workspace.parent,
            Path(manifest["prompt_root"]),
            Path(manifest["runs_root"]),
        ):
            self.assertEqual(mode(directory), 0o700)
        for file_path in (
            self.workspace,
            Path(manifest["vscode_workspace"]),
            first,
            second,
        ):
            self.assertEqual(mode(file_path), 0o600)

    def test_v2_prompt_migrates_once_and_resolves_by_ref_or_full_id(self) -> None:
        prompt = self.new_prompt()
        original = prompt.read_text(encoding="utf-8")
        v2 = original.replace(
            "schema: task-implementer/prompt-v3",
            "schema: task-implementer/prompt-v2",
        ).replace("prompt_ref: aaaaa\n", "") + (
            "\n## Context\n\n"
            "schema: body-schema-must-stay\n"
            "prompt_id: body-identity-must-stay\n"
        )
        legacy_name = self.prompt_root / prompt.name.removeprefix("aaaaa--")
        prompt.unlink()
        legacy_name.write_text(v2, encoding="utf-8")
        legacy_name.chmod(0o600)

        migrated = core.migrate_prompt_files_v2(self.prompt_root)
        self.assertEqual(len(migrated), 1)
        target = self.prompt_root / migrated[0]["new_name"]
        document = core.read_prompt(target, self.prompt_root, require_content=True)
        self.assertEqual(document.prompt_ref, "aaaaa")
        self.assertEqual(document.prompt_id, "prompt-" + "a" * 32)
        self.assertIn("schema: body-schema-must-stay", document.text)
        self.assertIn("prompt_id: body-identity-must-stay", document.text)
        self.assertEqual(document.text.count("prompt_ref: aaaaa"), 1)
        self.assertFalse(legacy_name.exists())
        self.assertEqual(core.migrate_prompt_files_v2(self.prompt_root), migrated)
        core.complete_prompt_files_v3_migration(self.prompt_root)
        self.assertEqual(core.migrate_prompt_files_v2(self.prompt_root), [])
        self.assertEqual(
            core.resolve_prompt_reference(
                self.workspace, "aaaaa", require_content=True
            ).path,
            target,
        )
        self.assertEqual(
            core.resolve_prompt_reference(
                self.workspace, "prompt-" + "a" * 32, require_content=True
            ).path,
            target,
        )
        history_root = self.root / "immutable-history"
        history_root.mkdir(mode=0o700)
        history = history_root / "prompt.md"
        history.write_text(v2, encoding="utf-8")
        history.chmod(0o600)
        with self.assertRaises(core.PromptWorkspaceError) as blocked:
            core.read_prompt(history, history_root, require_content=True)
        self.assertEqual(blocked.exception.code, "WORKFLOW_UPGRADE_REQUIRED")
        historical = core.read_prompt(
            history,
            history_root,
            require_content=True,
            allow_migration_history=True,
        )
        self.assertEqual(historical.prompt_id, document.prompt_id)
        self.assertEqual(historical.prompt_ref, "")

    def test_prompt_ref_collision_extends_new_ref_without_changing_identity(
        self,
    ) -> None:
        first = self.new_prompt(prompt_hex="abcde0" + "0" * 26)
        second = self.new_prompt(prompt_hex="abcde1" + "1" * 26)
        first_document = core.read_prompt(first, self.prompt_root, require_content=True)
        second_document = core.read_prompt(
            second, self.prompt_root, require_content=True
        )
        self.assertEqual(first_document.prompt_ref, "abcde")
        self.assertEqual(second_document.prompt_ref, "abcde1")
        self.assertEqual(
            core.resolve_prompt_reference(
                self.workspace, second_document.prompt_ref, require_content=True
            ).prompt_id,
            second_document.prompt_id,
        )

    def test_v2_migration_recovers_from_journaled_source_and_rejects_escape(
        self,
    ) -> None:
        prompt = self.new_prompt()
        original = prompt.read_bytes()
        v2 = original.replace(
            b"schema: task-implementer/prompt-v3",
            b"schema: task-implementer/prompt-v2",
        ).replace(b"prompt_ref: aaaaa\n", b"")
        legacy = self.prompt_root / prompt.name.removeprefix("aaaaa--")
        prompt.unlink()
        legacy.write_bytes(v2)
        legacy.chmod(0o600)
        migrated = core._render_migrated_prompt(v2, "aaaaa")
        item = {
            "prompt_id": "prompt-" + "a" * 32,
            "prompt_ref": "aaaaa",
            "old_name": legacy.name,
            "new_name": f"aaaaa--{legacy.name}",
            "old_sha256": hashlib.sha256(v2).hexdigest(),
            "new_sha256": hashlib.sha256(migrated).hexdigest(),
        }
        marker = self.prompt_root.parent / "prompt-v3-migration.json"
        core.write_atomic(
            marker,
            core.stable_json(
                {"schema": core.PROMPT_V3_MIGRATION_SCHEMA, "migrations": [item]}
            ),
        )
        self.assertEqual(core.migrate_prompt_files_v2(self.prompt_root), [item])
        self.assertFalse(legacy.exists())
        self.assertEqual((self.prompt_root / item["new_name"]).read_bytes(), migrated)

        escaped = {**item, "new_name": "../outside.md"}
        core.write_atomic(
            marker,
            core.stable_json(
                {"schema": core.PROMPT_V3_MIGRATION_SCHEMA, "migrations": [escaped]}
            ),
        )
        with self.assertRaises(core.PromptWorkspaceError) as caught:
            core.migrate_prompt_files_v2(self.prompt_root)
        self.assertEqual(caught.exception.code, "WORKSPACE_STATE_INVALID")

    def test_v3_queue_pointer_rewrite_is_replay_safe(self) -> None:
        prompt = self.new_prompt(ask="Queued migration objective")
        document = core.read_prompt(prompt, self.prompt_root, require_content=True)
        scope_dir = self.workspace.parent
        runs.enqueue_prompt_unlocked(scope_dir, document, FIXED_UTC)
        v2 = (
            prompt.read_bytes()
            .replace(
                b"schema: task-implementer/prompt-v3",
                b"schema: task-implementer/prompt-v2",
            )
            .replace(f"prompt_ref: {document.prompt_ref}\n".encode(), b"")
        )
        legacy = prompt.with_name(prompt.name.removeprefix(f"{document.prompt_ref}--"))
        prompt.unlink()
        legacy.write_bytes(v2)
        legacy.chmod(0o600)
        queue = runs.load_prompt_queue(scope_dir)
        queue["entries"][0]["source_path"] = legacy.name
        runs._save_prompt_queue(scope_dir, queue)

        migrations = core.migrate_prompt_files_v2(self.prompt_root)
        runs._rewrite_prompt_v3_references(scope_dir, self.prompt_root, migrations)
        runs._rewrite_prompt_v3_references(scope_dir, self.prompt_root, migrations)
        repaired = runs.load_prompt_queue(scope_dir)["entries"][0]
        self.assertEqual(repaired["source_path"], migrations[0]["new_name"])
        self.assertEqual(
            Path(scope_dir / str(repaired["snapshot"])).read_bytes(),
            (self.prompt_root / migrations[0]["new_name"]).read_bytes(),
        )

    def test_session_projection_creates_lossless_prompt_and_rejects_stale_merge(
        self,
    ) -> None:
        refined = self.root / "refined.md"
        refinement = (
            "Implement automatic prompt intake.\n\n"
            "- Preserve exact session provenance.\n"
            "- Keep manual edits inert until explicit run.\n"
        )
        refined.write_text(refinement, encoding="utf-8")
        refined.chmod(0o600)
        created = runs.merge_session_projection(
            self.workspace,
            refined,
            prompt_reference=None,
            expected_sha256=None,
            new_objective=True,
            operation_id="1" * 64,
            projection_sha256=file_sha256(refined),
            clock=lambda: FIXED_UTC,
        )
        prompt = Path(str(created["path"]))
        self.assertIn(refinement.strip(), prompt.read_text(encoding="utf-8"))
        base = core.read_prompt(prompt, self.prompt_root, require_content=True)

        delta = self.root / "delta.md"
        delta.write_text(
            "Also retain collision-safe prompt references.", encoding="utf-8"
        )
        delta.chmod(0o600)
        merged = runs.merge_session_projection(
            self.workspace,
            delta,
            prompt_reference=base.prompt_ref,
            expected_sha256=base.sha256,
            new_objective=False,
            operation_id="2" * 64,
            projection_sha256=file_sha256(delta),
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(merged["prompt_id"], base.prompt_id)
        self.assertIn("Also retain collision-safe", prompt.read_text(encoding="utf-8"))
        duplicate = runs.merge_session_projection(
            self.workspace,
            delta,
            prompt_reference=base.prompt_ref,
            expected_sha256=base.sha256,
            new_objective=False,
            operation_id="3" * 64,
            projection_sha256=file_sha256(delta),
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["sha256"], merged["sha256"])
        other_delta = self.root / "other-delta.md"
        other_delta.write_text(
            "Require a distinct second constraint.\n", encoding="utf-8"
        )
        other_delta.chmod(0o600)
        self.assert_error(
            "PROMPT_DRIFT",
            runs.merge_session_projection,
            self.workspace,
            other_delta,
            prompt_reference=base.prompt_ref,
            expected_sha256=base.sha256,
            new_objective=False,
            operation_id="3" * 64,
            projection_sha256=file_sha256(other_delta),
        )

        retried = runs.merge_session_projection(
            self.workspace,
            delta,
            prompt_reference=base.prompt_ref,
            expected_sha256=base.sha256,
            new_objective=False,
            operation_id="2" * 64,
            projection_sha256=file_sha256(delta),
        )
        self.assertEqual(retried["sha256"], merged["sha256"])
        recreated = runs.merge_session_projection(
            self.workspace,
            refined,
            prompt_reference=None,
            expected_sha256=None,
            new_objective=True,
            operation_id="1" * 64,
            projection_sha256=file_sha256(refined),
        )
        self.assertEqual(recreated["prompt_id"], created["prompt_id"])

    def test_session_projection_new_objective_recovers_after_create_interruption(
        self,
    ) -> None:
        refined = self.root / "interrupted-refinement.md"
        refined.write_text(
            "Create exactly one crash-safe objective.\n", encoding="utf-8"
        )
        refined.chmod(0o600)
        before = set(self.prompt_root.glob("*.md"))
        original_write = core.write_exclusive

        def interrupt_after_write(path: Path, data: bytes) -> None:
            original_write(path, data)
            raise KeyboardInterrupt("simulated process termination")

        with mock.patch.object(
            core, "write_exclusive", side_effect=interrupt_after_write
        ):
            with self.assertRaises(KeyboardInterrupt):
                runs.merge_session_projection(
                    self.workspace,
                    refined,
                    prompt_reference=None,
                    expected_sha256=None,
                    new_objective=True,
                    operation_id="4" * 64,
                    projection_sha256=file_sha256(refined),
                    clock=lambda: FIXED_UTC,
                )

        recovered = runs.merge_session_projection(
            self.workspace,
            refined,
            prompt_reference=None,
            expected_sha256=None,
            new_objective=True,
            operation_id="4" * 64,
            projection_sha256=file_sha256(refined),
            clock=lambda: FIXED_UTC,
        )
        created = set(self.prompt_root.glob("*.md")) - before
        self.assertEqual(len(created), 1)
        self.assertEqual(
            Path(str(recovered["path"]))
            .read_text(encoding="utf-8")
            .count(
                "<!-- prompt-session-operation:v2:"
                f"{'4' * 64}:{file_sha256(refined)} -->"
            ),
            1,
        )

    def test_managed_lane_capture_merges_once_without_implicit_run(self) -> None:
        original_ask = "Keep the managed lane direct prompt authoritative"
        prompt = self.new_prompt(ask=original_ask)
        self.complete_prompt(prompt)
        base = core.read_prompt(prompt, self.prompt_root, require_content=True)
        refinement = self.root / "managed-lane-refinement.md"
        new_constraint = "Also capture this managed-lane constraint exactly once."
        refinement.write_text(new_constraint + "\n", encoding="utf-8")
        refinement.chmod(0o600)
        operation_id = "6" * 64
        workspace = core.verify_workspace(self.workspace)
        self.assertEqual(Path(str(workspace["source_root"])), self.lane_scope)
        runs_root = Path(str(workspace["runs_root"]))
        self.assertEqual(runs.load_run_manifests(runs_root), [])

        merged = runs.merge_session_projection(
            self.workspace,
            refinement,
            prompt_reference=base.prompt_ref,
            expected_sha256=base.sha256,
            new_objective=False,
            operation_id=operation_id,
            projection_sha256=file_sha256(refinement),
            clock=lambda: FIXED_UTC,
        )
        text = prompt.read_text(encoding="utf-8")
        self.assertIn(original_ask, text)
        self.assertIn(new_constraint, text)
        self.assertEqual(
            text.count(
                "<!-- prompt-session-operation:v2:"
                f"{operation_id}:{file_sha256(refinement)} -->"
            ),
            1,
        )
        self.assertEqual(runs.load_run_manifests(runs_root), [])

        retried = runs.merge_session_projection(
            self.workspace,
            refinement,
            prompt_reference=base.prompt_ref,
            expected_sha256=base.sha256,
            new_objective=False,
            operation_id=operation_id,
            projection_sha256=file_sha256(refinement),
        )
        self.assertEqual(retried["sha256"], merged["sha256"])
        self.assertEqual(runs.load_run_manifests(runs_root), [])

        first_run = pw.route_project_prompt(
            self.lane_scope,
            self.codex_home,
            base.prompt_ref,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        second_run = pw.route_project_prompt(
            self.lane_scope,
            self.codex_home,
            base.prompt_ref,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertEqual(first_run["prompt"], str(prompt))
        self.assertEqual(second_run["prompt"], str(prompt))
        self.assertEqual(
            first_run["_internal"]["run_id"], second_run["_internal"]["run_id"]
        )

    def test_session_projection_rejects_reserved_operation_marker(self) -> None:
        refined = self.root / "reserved-marker.md"
        refined.write_text(
            f"Do not inject <!-- prompt-session-operation:{'5' * 64} -->.\n",
            encoding="utf-8",
        )
        refined.chmod(0o600)
        self.assert_error(
            "PROMPT_INPUT_INVALID",
            runs.merge_session_projection,
            self.workspace,
            refined,
            prompt_reference=None,
            expected_sha256=None,
            new_objective=True,
            operation_id="5" * 64,
            projection_sha256=file_sha256(refined),
        )

    def test_session_projection_rejects_symlink_input(self) -> None:
        target = self.root / "projection-target.md"
        target.write_text("Require one durable constraint.\n", encoding="utf-8")
        target.chmod(0o600)
        projection = self.root / "projection-link.md"
        projection.symlink_to(target)

        self.assert_error(
            "PROMPT_PATH_INVALID",
            runs.merge_session_projection,
            self.workspace,
            projection,
            prompt_reference=None,
            expected_sha256=None,
            new_objective=True,
            operation_id="a" * 64,
            projection_sha256=file_sha256(target),
        )

    def test_session_projection_rejects_substitution_and_serializes_same_base(
        self,
    ) -> None:
        prompt = self.new_prompt(ask="Keep the accepted base stable")
        base = core.read_prompt(prompt, self.prompt_root, require_content=True)
        first = self.root / "first-project-intent.md"
        second = self.root / "second-project-intent.md"
        first.write_text("Require the first durable constraint.\n", encoding="utf-8")
        second.write_text("Require the second durable constraint.\n", encoding="utf-8")
        first.chmod(0o600)
        second.chmod(0o600)
        before = prompt.read_bytes()
        self.assert_error(
            "PROMPT_DRIFT",
            runs.merge_session_projection,
            self.workspace,
            second,
            prompt_reference=base.prompt_ref,
            expected_sha256=base.sha256,
            new_objective=False,
            operation_id="7" * 64,
            projection_sha256=file_sha256(first),
        )
        self.assertEqual(prompt.read_bytes(), before)

        def merge(path: Path, operation_id: str) -> tuple[str, object]:
            try:
                result = runs.merge_session_projection(
                    self.workspace,
                    path,
                    prompt_reference=base.prompt_ref,
                    expected_sha256=base.sha256,
                    new_objective=False,
                    operation_id=operation_id,
                    projection_sha256=file_sha256(path),
                    clock=lambda: FIXED_UTC,
                )
                return "ok", result
            except runs.PromptWorkspaceError as error:
                return error.code, error

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda item: merge(*item),
                    ((first, "8" * 64), (second, "9" * 64)),
                )
            )
        self.assertEqual(sorted(code for code, _ in results), ["PROMPT_DRIFT", "ok"])
        workspace = core.verify_workspace(self.workspace)
        self.assertEqual(runs.load_run_manifests(Path(str(workspace["runs_root"]))), [])

    def test_new_treats_metacharacters_as_data(self) -> None:
        ask = "Add '$() ; & pipes' and café 🔒 safely"
        prompt = self.new_prompt(ask=ask)
        text = prompt.read_text(encoding="utf-8")
        self.assertIn(ask, text)
        self.assertFalse((self.root / "pipes").exists())
        self.assert_error("PROMPT_INPUT_INVALID", self.new_prompt, ask="first\nsecond")
        self.assert_error(
            "PROMPT_INPUT_INVALID", self.new_prompt, ask="first\u2028second"
        )
        self.assert_error(
            "PROMPT_INPUT_INVALID", self.new_prompt, ask="unsafe\x7ftitle"
        )

    def test_prompt_validation_matrix(self) -> None:
        prompt = self.new_prompt()
        ask_only = pw.verify_command(self.workspace, prompt, None)
        self.assertEqual(ask_only["prompt_id"], f"prompt-{'a' * 32}")
        self.complete_prompt(prompt)
        verified = pw.verify_command(self.workspace, prompt, None)
        self.assertEqual(verified["prompt_id"], f"prompt-{'a' * 32}")

        if os.name == "posix":
            prompt.chmod(0o644)
            self.assert_error(
                "WORKSPACE_PERMISSION_INVALID",
                pw.verify_command,
                self.workspace,
                prompt,
                None,
            )
            prompt.chmod(0o600)

        duplicate = self.prompt_root / "duplicate.md"
        shutil.copyfile(prompt, duplicate)
        duplicate.chmod(0o600)
        self.assert_error(
            "PROMPT_CONFLICT",
            pw.verify_command,
            self.workspace,
            prompt,
            None,
        )
        duplicate.unlink()

        malformed = self.prompt_root / "malformed.md"
        malformed.write_text("not a prompt\n", encoding="utf-8")
        malformed.chmod(0o600)
        isolated = pw.verify_command(self.workspace, prompt, None)
        self.assertEqual(isolated["prompt_id"], f"prompt-{'a' * 32}")
        malformed_row = next(
            row
            for row in pw.prompt_rows(self.workspace, None, None)
            if row["path"] == str(malformed)
        )
        self.assertEqual(malformed_row["status"], "invalid")
        malformed.unlink()

        original = prompt.read_bytes()
        prompt.write_bytes(b"\xff\xfe")
        prompt.chmod(0o600)
        self.assert_error(
            "PROMPT_INPUT_INVALID",
            pw.verify_command,
            self.workspace,
            prompt,
            None,
        )
        prompt.write_bytes(original + b"\x00")
        self.assert_error(
            "PROMPT_INPUT_INVALID",
            pw.verify_command,
            self.workspace,
            prompt,
            None,
        )
        prompt.write_bytes(original + b"x" * pw.MAX_PROMPT_BYTES)
        self.assert_error(
            "PROMPT_INPUT_INVALID",
            pw.verify_command,
            self.workspace,
            prompt,
            None,
        )

        outside = self.root / "outside.md"
        outside.write_bytes(original)
        self.assert_error(
            "PROMPT_PATH_INVALID",
            pw.verify_command,
            self.workspace,
            outside,
            None,
        )

    def test_scope_allows_only_one_active_run(self) -> None:
        first_prompt = self.new_prompt(prompt_hex="a" * 32)
        second_prompt = self.new_prompt(
            ask="Implement another independent ask", prompt_hex="b" * 32
        )
        self.complete_prompt(first_prompt)
        self.complete_prompt(second_prompt)
        first = pw.snapshot_prompt(
            self.workspace,
            first_prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.assert_error(
            "ACTIVE_RUN_EXISTS",
            pw.snapshot_prompt,
            self.workspace,
            second_prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.mark_run_done(first["run_id"])
        second = pw.snapshot_prompt(
            self.workspace,
            second_prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertNotEqual(first["run_id"], second["run_id"])

    def test_run_intake_queues_other_prompt_and_requires_explicit_drift_update(
        self,
    ) -> None:
        first_prompt = self.new_prompt(prompt_hex="a" * 32)
        second_prompt = self.new_prompt(
            ask="Implement a queued objective", prompt_hex="b" * 32
        )
        self.complete_prompt(first_prompt)
        self.complete_prompt(second_prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            first_prompt.name,
            clock=lambda: FIXED_UTC,
        )
        queued = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            second_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(queued["action"], "queued")
        self.assertEqual(queued["queue_position"], 1)
        self.assertEqual(
            pw.queue_rows(self.workspace)[0]["source_path"], second_prompt.name
        )

        second_prompt.write_text(
            second_prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The edited queued objective is usable.",
            ),
            encoding="utf-8",
        )
        second_prompt.chmod(0o600)
        self.mark_run_done(str(first["_internal"]["run_id"]))
        self.assert_error(
            "QUEUED_PROMPT_DRIFT", pw.activate_next_queued_prompt, self.workspace
        )
        updated = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            second_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertEqual(updated["action"], "new")
        self.assertEqual(updated["_internal"]["status"], "activated")
        self.assertEqual(updated["_internal"]["prompt_id"], "prompt-" + "b" * 32)
        self.assertEqual(pw.queue_rows(self.workspace), [])

    def test_queued_prompt_raw_drift_requires_explicit_rerun(self) -> None:
        first_prompt = self.new_prompt(prompt_hex="a" * 32)
        second_prompt = self.new_prompt(
            ask="Implement a queued objective", prompt_hex="b" * 32
        )
        self.complete_prompt(first_prompt)
        self.complete_prompt(second_prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            first_prompt.name,
            clock=lambda: FIXED_UTC,
        )
        pw.route_project_prompt(
            self.scope,
            self.codex_home,
            second_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )

        second_prompt.write_text(
            second_prompt.read_text(encoding="utf-8").replace(
                "## Ask", "<!-- formatting after acceptance -->\n\n## Ask"
            ),
            encoding="utf-8",
        )
        second_prompt.chmod(0o600)
        self.mark_run_done(str(first["_internal"]["run_id"]))
        self.assert_error(
            "QUEUED_PROMPT_DRIFT", pw.activate_next_queued_prompt, self.workspace
        )

        activated = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            second_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertEqual(activated["action"], "new")
        self.assertEqual(activated["_internal"]["status"], "activated")
        self.assertEqual(pw.queue_rows(self.workspace), [])

    def test_completed_follow_up_queues_until_resources_release(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        first_run = str(first["_internal"]["run_id"])
        self.mark_run_done(first_run)
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The follow-up waits for resource release.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)

        with mock.patch.object(intake, "_run_resources_active", return_value=True):
            queued = pw.route_project_prompt(
                self.scope,
                self.codex_home,
                prompt.name,
                clock=lambda: FIXED_UTC.replace(second=1),
            )
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["queue_position"], 1)

        activated = pw.activate_next_queued_prompt(
            self.workspace, clock=lambda: FIXED_UTC.replace(second=2)
        )
        self.assertEqual(activated["status"], "activated")
        manifest = json.loads(
            Path(str(activated["manifest"])).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["predecessor"]["run_id"], first_run)
        self.assertEqual(manifest["revisions"][0]["kind"], "completed_follow_up")

    def test_queue_activation_recovers_after_interrupted_dequeue(self) -> None:
        first_prompt = self.new_prompt(prompt_hex="a" * 32)
        second_prompt = self.new_prompt(
            ask="Implement a queued objective", prompt_hex="b" * 32
        )
        self.complete_prompt(first_prompt)
        self.complete_prompt(second_prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            first_prompt.name,
            clock=lambda: FIXED_UTC,
        )
        pw.route_project_prompt(
            self.scope,
            self.codex_home,
            second_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.mark_run_done(str(first["_internal"]["run_id"]))

        with mock.patch.object(
            runs,
            "_resolve_queue_entry_unlocked",
            side_effect=RuntimeError("injected dequeue interruption"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected dequeue"):
                pw.activate_next_queued_prompt(
                    self.workspace, clock=lambda: FIXED_UTC.replace(second=2)
                )

        recovered = pw.activate_next_queued_prompt(
            self.workspace, clock=lambda: FIXED_UTC.replace(second=3)
        )
        self.assertEqual(recovered["status"], "activated")
        self.assertTrue(recovered["recovered"])
        self.assertEqual(recovered["prompt_id"], "prompt-" + "b" * 32)
        self.assertEqual(pw.queue_rows(self.workspace), [])

    def test_queue_cancel_reports_already_committed_activation(self) -> None:
        first_prompt = self.new_prompt(prompt_hex="a" * 32)
        second_prompt = self.new_prompt(
            ask="Implement a queued objective", prompt_hex="b" * 32
        )
        self.complete_prompt(first_prompt)
        self.complete_prompt(second_prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            first_prompt.name,
            clock=lambda: FIXED_UTC,
        )
        pw.route_project_prompt(
            self.scope,
            self.codex_home,
            second_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.mark_run_done(str(first["_internal"]["run_id"]))
        with mock.patch.object(
            runs,
            "_resolve_queue_entry_unlocked",
            side_effect=RuntimeError("injected dequeue interruption"),
        ):
            with self.assertRaises(RuntimeError):
                pw.activate_next_queued_prompt(
                    self.workspace, clock=lambda: FIXED_UTC.replace(second=2)
                )

        canceled = pw.cancel_queued_prompt(
            self.workspace,
            second_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=3),
        )
        self.assertEqual(canceled["status"], "already_activated")
        self.assertEqual(canceled["prompt_id"], "prompt-" + "b" * 32)
        self.assertEqual(pw.queue_rows(self.workspace), [])

    def test_semantic_no_effect_and_completed_follow_up_lineage(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        first_run = str(first["_internal"]["run_id"])
        text = prompt.read_text(encoding="utf-8")
        prompt.write_text(
            text.replace("## Outcome", "<!-- formatting -->\n\n## Outcome"),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        unchanged = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(unchanged["_internal"]["revision"], "r0001")
        unchanged_snapshot = Path(str(unchanged["_internal"]["snapshot"]))
        self.assertEqual(
            unchanged["_internal"]["sha256"],
            hashlib.sha256(unchanged_snapshot.read_bytes()).hexdigest(),
        )
        self.mark_run_done(first_run)
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The linked follow-up objective is usable.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        follow_up = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        manifest_path = Path(str(follow_up["_internal"]["manifest"]))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["revisions"][0]["kind"], "completed_follow_up")
        self.assertEqual(manifest["predecessor"]["run_id"], first_run)
        self.assertEqual(manifest["lineage_root"], first_run)
        self.assertFalse((manifest_path.parent / "steering.json").exists())

    def test_fenced_code_indentation_is_semantic(self) -> None:
        prompt = self.new_prompt()
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "## Ask\n\nAdd prompt workspace support",
                "## Ask\n\nImplement this YAML exactly:\n\n```yaml\nroot:\n  child: value\n```",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "  child: value", "    child: value"
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        changed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(changed["_internal"]["run_id"], first["_internal"]["run_id"])
        self.assertEqual(changed["_internal"]["revision"], "r0002")

    def test_fenced_markdown_headings_remain_inside_ask(self) -> None:
        prompt = self.new_prompt()
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "## Ask\n\nAdd prompt workspace support",
                "## Ask\n\nDocument these examples:\n\n"
                "```markdown\n## Ask\nNested backtick example\n```\n\n"
                "~~~markdown\n## Context\nNested tilde example\n~~~",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        document = core.read_prompt(prompt, self.prompt_root, require_content=True)
        self.assertIn("## Ask", document.sections["Ask"])
        self.assertIn("## Context", document.sections["Ask"])
        self.assertNotIn("Context", document.sections)

    def test_run_intake_refreshes_stale_generated_python_launcher(self) -> None:
        prompt = self.new_prompt()
        workspace_manifest = json.loads(self.workspace.read_text(encoding="utf-8"))
        vscode_path = Path(workspace_manifest["vscode_workspace"])
        vscode = json.loads(vscode_path.read_text(encoding="utf-8"))
        stale_python = self.root / "removed-python3.12"
        for task in vscode["tasks"]["tasks"]:
            task["command"] = str(stale_python)
        core.write_atomic(vscode_path, core.stable_json(vscode))

        with self.assertRaises(core.PromptWorkspaceError) as blocked:
            core.verify_workspace(self.workspace)
        self.assertEqual(blocked.exception.code, "WORKSPACE_STATE_INVALID")
        self.assertEqual(
            blocked.exception.message, "VS Code workspace command is unsafe"
        )

        routed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )

        self.assertEqual(routed["status"], "snapshot_only")
        refreshed = json.loads(vscode_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {task["command"] for task in refreshed["tasks"]["tasks"]},
            {str(Path(sys.executable).resolve())},
        )
        self.assertEqual(
            core.verify_workspace(self.workspace)["scope"], "services/example"
        )

    def test_interrupted_refinement_reset_retries_from_manifest_commit_point(
        self,
    ) -> None:
        prompt = self.new_prompt()
        first = pw.route_project_prompt(
            self.scope, self.codex_home, prompt.name, clock=lambda: FIXED_UTC
        )
        prompt.write_text(
            prompt.read_text(encoding="utf-8").rstrip()
            + "\n\n## Steering\n\nApply the accepted revision atomically.\n",
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        with mock.patch.object(
            runs,
            "begin_requirements_refinement",
            side_effect=OSError("injected refinement write failure"),
        ):
            with self.assertRaises(OSError):
                pw.route_project_prompt(
                    self.scope,
                    self.codex_home,
                    prompt.name,
                    clock=lambda: FIXED_UTC.replace(second=1),
                )
        manifest_path = Path(str(first["_internal"]["manifest"]))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["revisions"]), 1)

        retried = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertEqual(retried["_internal"]["revision"], "r0002")
        refinement = json.loads(
            (manifest_path.parent / "requirements-refinement.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(refinement["revision"], "r0002")

        prompt.write_text(
            prompt.read_text(encoding="utf-8").rstrip()
            + "\nAdd one more accepted change.\n",
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        original_write = runs.write_atomic

        def fail_manifest_commit(path: Path, content: bytes) -> None:
            if path.name == "manifest.json":
                raise OSError("injected manifest commit failure")
            original_write(path, content)

        with mock.patch.object(runs, "write_atomic", side_effect=fail_manifest_commit):
            with self.assertRaises(OSError):
                pw.route_project_prompt(
                    self.scope,
                    self.codex_home,
                    prompt.name,
                    clock=lambda: FIXED_UTC.replace(second=3),
                )
        self.assertEqual(
            len(json.loads(manifest_path.read_text(encoding="utf-8"))["revisions"]),
            2,
        )
        committed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=4),
        )
        self.assertEqual(committed["_internal"]["revision"], "r0003")

    def test_verification_allows_repeated_bytes_in_linked_abandoned_run(self) -> None:
        prompt = self.new_prompt()
        first = pw.route_project_prompt(
            self.scope, self.codex_home, prompt.name, clock=lambda: FIXED_UTC
        )
        self.write_handoff(str(first["_internal"]["run_id"]), status="abandoned")
        second = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertNotEqual(second["_internal"]["run_id"], first["_internal"]["run_id"])
        verified = runs.verify_command(
            self.workspace, None, str(second["_internal"]["run_id"])
        )
        self.assertEqual(verified["run"]["sha256"], second["_internal"]["sha256"])

    def test_bound_pre_impact_status_is_pending_then_historical(self) -> None:
        prompt = self.new_prompt()
        first = pw.route_project_prompt(
            self.scope, self.codex_home, prompt.name, clock=lambda: FIXED_UTC
        )
        pending = next(
            item
            for item in pw.prompt_rows(self.workspace, None, None)
            if item.get("path") == str(prompt)
        )
        self.assertEqual(pending["impact"]["classification"], "pending")
        self.assertEqual(
            pending["impact"]["plan_action"],
            "clarification_or_reconciliation_required",
        )

        self.write_handoff(str(first["_internal"]["run_id"]), status="done")
        historical = next(
            item
            for item in pw.prompt_rows(self.workspace, None, None)
            if item.get("path") == str(prompt)
        )
        self.assertEqual(
            historical["impact"]["classification"], "historical_no_receipt"
        )
        self.assertEqual(historical["impact"]["plan_action"], "none")

    def test_run_validation_binds_intent_metadata_to_snapshot(self) -> None:
        prompt = self.new_prompt()
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        manifest_path = Path(str(first["_internal"]["manifest"]))
        original = json.loads(manifest_path.read_text(encoding="utf-8"))
        tampered = json.loads(json.dumps(original))
        tampered["revisions"][0]["intent_sha256"] = "0" * 64
        core.write_atomic(manifest_path, core.stable_json(tampered))
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.verify_command,
            self.workspace,
            None,
            str(first["_internal"]["run_id"]),
        )

    def test_run_validation_rejects_mixed_v1_v2_revision_metadata(self) -> None:
        prompt = self.new_prompt()
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "Add prompt workspace support", "Add revised prompt workspace support"
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        changed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        manifest_path = Path(str(changed["_internal"]["manifest"]))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["revisions"][0].pop("intent_sha256")
        manifest["revisions"][0].pop("kind")
        core.write_atomic(manifest_path, core.stable_json(manifest))
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.verify_command,
            self.workspace,
            None,
            str(first["_internal"]["run_id"]),
        )

    def test_run_validation_rejects_lineage_cycle(self) -> None:
        prompt = self.new_prompt()
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        first_run = str(first["_internal"]["run_id"])
        self.mark_run_done(first_run)
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "Add prompt workspace support", "Add a linked objective"
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        second = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        second_run = str(second["_internal"]["run_id"])
        self.mark_run_done(second_run)
        first_manifest_path = Path(str(first["_internal"]["manifest"]))
        second_manifest_path = Path(str(second["_internal"]["manifest"]))
        first_manifest = json.loads(first_manifest_path.read_text(encoding="utf-8"))
        second_manifest = json.loads(second_manifest_path.read_text(encoding="utf-8"))
        first_manifest["lineage_root"] = first_run
        first_manifest["predecessor"] = {
            "run_id": second_run,
            "revision": "r0001",
            "sha256": second_manifest["revisions"][0]["sha256"],
        }
        first_manifest["revisions"][0]["kind"] = "completed_follow_up"
        core.write_atomic(first_manifest_path, core.stable_json(first_manifest))
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.verify_command,
            self.workspace,
            None,
            second_run,
        )

    def test_run_intake_routes_without_user_run_ids_or_prompt_mutation(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        source_bytes = prompt.read_bytes()
        source_mtime = prompt.stat().st_mtime_ns

        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        self.assertEqual(first["action"], "new")
        self.assertEqual(first["status"], "snapshot_only")
        internal = first["_internal"]
        self.assertIsInstance(internal, dict)
        run_id = internal["run_id"]
        self.assertNotIn("run_id", first)
        self.assertEqual(prompt.read_bytes(), source_bytes)
        self.assertEqual(prompt.stat().st_mtime_ns, source_mtime)

        self.write_handoff(run_id, status="prepared")
        continued = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            str(prompt),
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(continued["action"], "continue")
        self.assertEqual(continued["_internal"]["run_id"], run_id)
        handoff = Path(continued["_internal"]["manifest"]).parent / "handoff.md"
        self.assertIn(
            "- Last invoked at: 2026-07-12T21:30:01+00:00",
            handoff.read_text(encoding="utf-8"),
        )

        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The private prompt workflow reconciles edits safely.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        edited_bytes = prompt.read_bytes()
        edited_mtime = prompt.stat().st_mtime_ns
        reconciled = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertEqual(reconciled["action"], "reconcile")
        self.assertEqual(reconciled["status"], "reconcile_pending")
        self.assertEqual(reconciled["_internal"]["revision"], "r0002")
        self.assertEqual(prompt.read_bytes(), edited_bytes)
        self.assertEqual(prompt.stat().st_mtime_ns, edited_mtime)

        self.write_handoff(run_id, status="done", bound_revision="r0002")
        self.resolve_steering(run_id, "r0002")
        completed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=3),
        )
        self.assertEqual(completed["action"], "done")
        self.assertEqual(completed["_internal"]["run_id"], run_id)

        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "reconciles edits safely", "starts a new run after completion"
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        restarted = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=4),
        )
        self.assertEqual(restarted["action"], "new")
        self.assertNotEqual(restarted["_internal"]["run_id"], run_id)

    def test_run_intake_rejects_invalid_or_foreign_prompt_references(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        self.assert_error(
            "PROMPT_PATH_INVALID",
            pw.route_project_prompt,
            self.scope,
            self.codex_home,
            "nested/prompt.md",
        )
        self.assert_error(
            "PROMPT_PATH_INVALID",
            pw.route_project_prompt,
            self.scope,
            self.codex_home,
            "missing.md",
        )
        outside = self.root / "outside.md"
        outside.write_bytes(prompt.read_bytes())
        outside.chmod(0o600)
        self.assert_error(
            "PROMPT_PATH_INVALID",
            pw.route_project_prompt,
            self.scope,
            self.codex_home,
            str(outside),
        )
        if os.name == "posix":
            linked = self.prompt_root / "linked.md"
            linked.symlink_to(outside)
            self.assert_error(
                "PROMPT_PATH_INVALID",
                pw.route_project_prompt,
                self.scope,
                self.codex_home,
                linked.name,
            )

        missing_home = self.root / "missing home"
        self.assert_error(
            "WORKSPACE_NOT_FOUND",
            pw.route_project_prompt,
            self.scope,
            missing_home,
            prompt.name,
        )

    def test_first_intake_activity_persists_and_retry_reuses_snapshot(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)

        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        run_id = first["_internal"]["run_id"]
        persisted = pw.prompt_rows(self.workspace, None, None)
        self.assertEqual(persisted[0]["path"], str(prompt))
        self.assertEqual(
            persisted[0]["last_invoked_at"],
            "2026-07-12T21:30:00+00:00",
        )
        if os.name == "posix":
            self.assertEqual(mode(self.workspace.parent / "activity.json"), 0o600)

        retried = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(retried["action"], "new")
        self.assertEqual(retried["_internal"]["run_id"], run_id)
        self.assertEqual(retried["_internal"]["revision"], "r0001")
        persisted = pw.prompt_rows(self.workspace, None, None)
        self.assertEqual(
            persisted[0]["last_invoked_at"],
            "2026-07-12T21:30:01+00:00",
        )

        run_dir = Path(retried["_internal"]["manifest"]).parent
        manifest = json.loads(
            Path(retried["_internal"]["manifest"]).read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest["revisions"]), 1)
        self.assertEqual(len(list(run_dir.parent.glob("run-*"))), 1)

    @unittest.skipUnless(os.name == "posix", "POSIX activity safety checks")
    def test_activity_state_rejects_permissions_and_symlinks(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        activity = self.workspace.parent / "activity.json"
        activity.chmod(0o644)
        self.assert_error(
            "WORKSPACE_PERMISSION_INVALID",
            pw.prompt_rows,
            self.workspace,
            None,
            None,
        )

        activity.chmod(0o600)
        outside = self.root / "outside activity.json"
        outside.write_bytes(activity.read_bytes())
        outside.chmod(0o600)
        activity.unlink()
        activity.symlink_to(outside)
        self.assert_error(
            "WORKSPACE_PATH_INVALID",
            pw.prompt_rows,
            self.workspace,
            None,
            None,
        )

    def test_edited_snapshot_only_intake_reuses_run_with_new_revision(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        run_id = first["_internal"]["run_id"]

        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The edited first intake is usable.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        resumed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(resumed["action"], "new")
        self.assertEqual(resumed["_internal"]["run_id"], run_id)
        self.assertEqual(resumed["_internal"]["revision"], "r0002")

        retry = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertEqual(retry["_internal"]["run_id"], run_id)
        self.assertEqual(retry["_internal"]["revision"], "r0002")
        manifest = json.loads(
            Path(retry["_internal"]["manifest"]).read_text(encoding="utf-8")
        )
        self.assertEqual(len(manifest["revisions"]), 2)

    def test_router_reuses_and_orders_multiple_pending_revisions(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        run_id = first["_internal"]["run_id"]
        self.write_handoff(
            run_id,
            status="prepared",
            last_invoked_at=FIXED_UTC,
        )
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The first reconciliation revision is usable.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)

        reconciled = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(reconciled["action"], "reconcile")
        self.assertEqual(reconciled["_internal"]["revision"], "r0002")
        resumed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertEqual(resumed["action"], "reconcile")
        self.assertEqual(resumed["_internal"]["run_id"], run_id)
        self.assertEqual(resumed["_internal"]["revision"], "r0002")
        manifest_path = Path(resumed["_internal"]["manifest"])
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["revisions"]), 2)
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The first reconciliation revision is usable.",
                "A second pending edit is ordered safely.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        second = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=3),
        )
        self.assertEqual(second["action"], "reconcile")
        self.assertEqual(second["_internal"]["revision"], "r0003")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["revisions"]), 3)
        steering = json.loads(
            (manifest_path.parent / "steering.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            [event["revision"] for event in steering["events"]],
            ["r0002", "r0003"],
        )

    def test_prompt_change_race_fails_before_snapshot_or_activity(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        original_snapshot = intake._snapshot_prompt_unlocked

        def mutate_then_snapshot(*args: object, **kwargs: object) -> dict[str, object]:
            prompt.write_text(
                prompt.read_text(encoding="utf-8").replace(
                    "The private prompt workflow is usable.",
                    "The prompt changed during intake.",
                ),
                encoding="utf-8",
            )
            prompt.chmod(0o600)
            return original_snapshot(*args, **kwargs)

        with mock.patch.object(
            intake,
            "_snapshot_prompt_unlocked",
            side_effect=mutate_then_snapshot,
        ):
            self.assert_error(
                "PROMPT_DRIFT",
                pw.route_project_prompt,
                self.scope,
                self.codex_home,
                prompt.name,
                clock=lambda: FIXED_UTC,
            )
        runs_root = Path(
            json.loads(self.workspace.read_text(encoding="utf-8"))["runs_root"]
        )
        self.assertEqual(list(runs_root.glob("run-*")), [])
        self.assertFalse((self.workspace.parent / "activity.json").exists())

    def test_rejected_intake_does_not_change_activity_order(self) -> None:
        active_prompt = self.new_prompt(prompt_hex="a" * 32)
        rejected_prompt = self.new_prompt(
            ask="Implement another independent ask",
            prompt_hex="b" * 32,
        )
        self.complete_prompt(active_prompt)
        self.complete_prompt(rejected_prompt)
        pw.route_project_prompt(
            self.scope,
            self.codex_home,
            active_prompt.name,
            clock=lambda: FIXED_UTC,
        )
        activity = self.workspace.parent / "activity.json"
        queued = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            rejected_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=5),
        )
        self.assertEqual(queued["status"], "queued")
        activity_before = activity.read_bytes()
        rows_before = pw.prompt_rows(self.workspace, None, None)

        self.assert_error(
            "PROMPT_PATH_INVALID",
            pw.route_project_prompt,
            self.scope,
            self.codex_home,
            "missing.md",
            clock=lambda: FIXED_UTC.replace(second=6),
        )
        self.assertEqual(activity.read_bytes(), activity_before)
        self.assertEqual(pw.prompt_rows(self.workspace, None, None), rows_before)

    def test_running_handoff_without_active_plane_reconciles_edit(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(
            first["_internal"]["run_id"],
            status="running",
            last_invoked_at=FIXED_UTC,
        )
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The running prompt now contradicts active work.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)

        routed = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=3),
        )
        self.assertEqual(routed["action"], "reconcile")
        self.assertEqual(routed["status"], "reconcile_pending")
        rows = pw.prompt_rows(self.workspace, None, None)
        self.assertEqual(
            rows[0]["last_invoked_at"],
            "2026-07-12T21:30:03+00:00",
        )

    def test_intake_json_redacts_internal_state(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        command = [
            sys.executable,
            str(SCRIPT),
            "intake",
            prompt.name,
            "--project-path",
            str(self.scope),
            "--codex-home",
            str(self.codex_home),
        ]
        public = subprocess.run(
            [*command, "--json"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(public.returncode, 0, public.stdout + public.stderr)
        public_result = json.loads(public.stdout)
        self.assertNotIn("_internal", public_result)
        self.assertNotIn("run_id", public.stdout)
        self.assertNotIn("prompt_id", public.stdout)

        internal = subprocess.run(
            [*command, "--internal-json"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(internal.returncode, 0, internal.stdout + internal.stderr)
        internal_result = json.loads(internal.stdout)
        self.assertIn("_internal", internal_result)
        self.assertIn("run_id", internal_result["_internal"])

    def test_prompt_activity_orders_by_last_invocation(self) -> None:
        first_prompt = self.new_prompt(prompt_hex="a" * 32)
        second_prompt = self.new_prompt(
            ask="Implement another independent ask",
            prompt_hex="b" * 32,
        )
        self.complete_prompt(first_prompt)
        self.complete_prompt(second_prompt)

        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            first_prompt.name,
            clock=lambda: FIXED_UTC,
        )
        first_run = first["_internal"]["run_id"]
        self.write_handoff(
            first_run,
            status="done",
            last_invoked_at=FIXED_UTC,
        )
        second = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            second_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        second_run = second["_internal"]["run_id"]
        self.write_handoff(
            second_run,
            status="done",
            last_invoked_at=FIXED_UTC.replace(second=1),
        )

        reordered = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            first_prompt.name,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertEqual(reordered["action"], "done")
        self.assertEqual(reordered["prompts"][0]["path"], str(first_prompt))
        self.assertEqual(
            reordered["prompts"][0]["last_invoked_at"],
            "2026-07-12T21:30:02+00:00",
        )
        self.assertNotIn("prompt_id", reordered["prompts"][0])
        self.assertNotIn("latest_run_id", reordered["prompts"][0])

    def test_prompt_activity_order_is_timezone_offset_independent(self) -> None:
        first_prompt = self.new_prompt(prompt_hex="a" * 32)
        second_prompt = self.new_prompt(
            ask="Implement another independent ask",
            prompt_hex="b" * 32,
        )
        self.complete_prompt(first_prompt)
        self.complete_prompt(second_prompt)
        first = pw.snapshot_prompt(
            self.workspace,
            first_prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(
            first["run_id"],
            status="done",
            last_invoked_at=datetime(
                2026,
                7,
                12,
                15,
                0,
                tzinfo=timezone(-timedelta(hours=7)),
            ),
        )
        second = pw.snapshot_prompt(
            self.workspace,
            second_prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.write_handoff(
            second["run_id"],
            status="done",
            last_invoked_at=datetime(2026, 7, 12, 21, 30, tzinfo=timezone.utc),
        )

        rows = pw.prompt_rows(self.workspace, None, None)
        self.assertEqual(rows[0]["path"], str(first_prompt))
        self.assertEqual(
            rows[0]["last_invoked_at"],
            "2026-07-12T22:00:00+00:00",
        )

    def test_prompt_activity_never_moves_backward(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        first = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC.replace(second=5),
        )
        self.write_handoff(
            first["_internal"]["run_id"],
            status="done",
            last_invoked_at=FIXED_UTC.replace(second=5),
        )
        repeated = pw.route_project_prompt(
            self.scope,
            self.codex_home,
            prompt.name,
            clock=lambda: FIXED_UTC,
        )
        self.assertEqual(
            repeated["last_invoked_at"],
            "2026-07-12T21:30:05+00:00",
        )
        rows = pw.prompt_rows(self.workspace, None, None)
        self.assertEqual(
            rows[0]["last_invoked_at"],
            "2026-07-12T21:30:05+00:00",
        )
        handoff = Path(repeated["_internal"]["manifest"]).parent / "handoff.md"
        self.assertIn(
            "- Last invoked at: 2026-07-12T21:30:05+00:00",
            handoff.read_text(encoding="utf-8"),
        )

    def test_prepare_retry_resumes_same_snapshot_only_run(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        first = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        resumed = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.assertEqual(resumed["revision"], "r0001")
        self.assertTrue(resumed["resumed_prepare"])
        runs_root = Path(
            json.loads(self.workspace.read_text(encoding="utf-8"))["runs_root"]
        )
        self.assertEqual(len(list(runs_root.glob("run-*"))), 1)
        manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["revisions"]), 1)

    def test_prepare_retry_rejects_handoff_claiming_snapshot_only(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(snapshot["run_id"], status="snapshot_only")
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.snapshot_prompt,
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX process-lock check")
    def test_concurrent_submissions_are_serialized(self) -> None:
        first_prompt = self.new_prompt(prompt_hex="c" * 32)
        second_prompt = self.new_prompt(
            ask="Create a concurrent prompt safely", prompt_hex="d" * 32
        )
        self.complete_prompt(first_prompt)
        self.complete_prompt(second_prompt)
        command = [
            sys.executable,
            str(SCRIPT),
            "snapshot",
            "--workspace",
            str(self.workspace),
            "--prompt",
        ]
        first = subprocess.Popen(
            [*command, str(first_prompt), "--json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        second = subprocess.Popen(
            [*command, str(second_prompt), "--json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        first_stdout, first_stderr = first.communicate(timeout=10)
        second_stdout, second_stderr = second.communicate(timeout=10)
        results = [
            (first.returncode, first_stdout, first_stderr),
            (second.returncode, second_stdout, second_stderr),
        ]
        self.assertEqual(sum(code == 0 for code, _, _ in results), 1, results)
        self.assertEqual(
            sum("ACTIVE_RUN_EXISTS" in stderr for _, _, stderr in results),
            1,
            results,
        )
        lock_path = self.workspace.parent / ".workspace.lock"
        self.assertEqual(mode(lock_path), 0o600)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink check")
    def test_reconcile_rejects_symlinked_inputs_ancestor(self) -> None:
        prompt = self.new_prompt(prompt_hex="e" * 32)
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        run_dir = Path(snapshot["manifest"]).parent
        inputs = run_dir / "inputs"
        shutil.rmtree(inputs)
        outside = self.root / "outside inputs"
        outside.mkdir()
        inputs.symlink_to(outside, target_is_directory=True)
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The private prompt workflow rejects symlink escapes.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.snapshot_prompt,
            self.workspace,
            prompt,
            run_id=snapshot["run_id"],
            force_new_run=False,
        )
        self.assertEqual(list(outside.iterdir()), [])
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.verify_command,
            self.workspace,
            None,
            snapshot["run_id"],
        )

    def test_snapshot_revision_resubmission_and_drift(self) -> None:
        sentinel = "PRIVATE_BODY_SENTINEL_92a4"
        prompt = self.new_prompt()
        self.complete_prompt(prompt, sentinel=sentinel)
        source_r1 = prompt.read_bytes()
        first = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        snapshot_r1 = Path(first["snapshot"])
        self.assertEqual(snapshot_r1.read_bytes(), source_r1)
        self.assertEqual(first["sha256"], hashlib.sha256(source_r1).hexdigest())
        manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
        self.assertNotIn("status", manifest)
        self.assertEqual(manifest["revisions"][0]["revision"], "r0001")
        self.write_handoff(first["run_id"], status="prepared")
        self.assert_error(
            "ACTIVE_RUN_EXISTS",
            pw.snapshot_prompt,
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
        )
        self.assert_error(
            "NO_CHANGES",
            pw.snapshot_prompt,
            self.workspace,
            prompt,
            run_id=first["run_id"],
            force_new_run=False,
        )

        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The private prompt workflow is reusable after edits.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        second = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=first["run_id"],
            force_new_run=False,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(second["revision"], "r0002")
        self.assertEqual(snapshot_r1.read_bytes(), source_r1)
        self.write_handoff(first["run_id"], status="prepared", bound_revision="r0002")
        pw.verify_command(self.workspace, prompt, first["run_id"])

        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "reusable after edits", "detects later drift"
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        self.assert_error(
            "PROMPT_DRIFT",
            pw.verify_command,
            self.workspace,
            prompt,
            first["run_id"],
        )
        self.assert_error(
            "PROMPT_DRIFT",
            pw.verify_command,
            self.workspace,
            None,
            first["run_id"],
        )

        self.mark_run_done(first["run_id"])
        next_run = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertNotEqual(next_run["run_id"], first["run_id"])
        self.assertEqual(next_run["prompt_id"], first["prompt_id"])
        self.mark_run_done(next_run["run_id"])
        self.assert_error(
            "NO_CHANGES",
            pw.snapshot_prompt,
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
        )
        exact_rerun = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=True,
            clock=lambda: FIXED_UTC.replace(second=3),
        )
        self.assertNotEqual(exact_rerun["run_id"], next_run["run_id"])

    def test_run_only_invalid_source_is_nonbinding_drift(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(snapshot["run_id"], status="prepared")
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.", "{{UNRESOLVED}}"
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        self.assert_error(
            "PROMPT_DRIFT",
            pw.verify_command,
            self.workspace,
            None,
            snapshot["run_id"],
        )

    def test_renamed_source_rejects_same_id_with_different_digest(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(snapshot["run_id"], status="prepared")
        prompt_ref = core.read_prompt(
            prompt, self.prompt_root, require_content=True
        ).prompt_ref
        exact = self.prompt_root / f"{prompt_ref}--renamed-exact.md"
        edited = self.prompt_root / f"{prompt_ref}--renamed-edited.md"
        shutil.copyfile(prompt, exact)
        shutil.copyfile(prompt, edited)
        exact.chmod(0o600)
        edited.write_text(
            edited.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The edited private prompt workflow is usable.",
            ),
            encoding="utf-8",
        )
        edited.chmod(0o600)
        prompt.unlink()
        self.assert_error(
            "PROMPT_CONFLICT",
            pw.verify_command,
            self.workspace,
            None,
            snapshot["run_id"],
        )

    def test_stale_copy_cannot_mask_source_drift(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(snapshot["run_id"], status="prepared")
        prompt_ref = core.read_prompt(
            prompt, self.prompt_root, require_content=True
        ).prompt_ref
        stale = self.prompt_root / f"{prompt_ref}--stale-copy.md"
        shutil.copyfile(prompt, stale)
        stale.chmod(0o600)
        self.assert_error(
            "PROMPT_CONFLICT",
            pw.verify_command,
            self.workspace,
            None,
            snapshot["run_id"],
        )

        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The edited private prompt workflow is usable.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        self.assert_error(
            "PROMPT_CONFLICT",
            pw.verify_command,
            self.workspace,
            None,
            snapshot["run_id"],
        )

    def test_exact_manual_rename_preserves_prompt_identity(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(snapshot["run_id"], status="prepared")
        prompt_ref = core.read_prompt(
            prompt, self.prompt_root, require_content=True
        ).prompt_ref
        renamed = self.prompt_root / f"{prompt_ref}--renamed-prompt.md"
        prompt.rename(renamed)
        verified = pw.verify_command(self.workspace, None, snapshot["run_id"])
        self.assertEqual(verified["run"]["prompt_id"], snapshot["prompt_id"])
        self.assertEqual(verified["run"]["sha256"], snapshot["sha256"])

    def test_reconcile_retry_resumes_unbound_revision(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        first = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(first["run_id"], status="prepared")
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The reconciled private prompt workflow is usable.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        second = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=first["run_id"],
            force_new_run=False,
            clock=lambda: FIXED_UTC.replace(second=1),
        )
        self.assertEqual(second["revision"], "r0002")

        pending = pw.verify_run(
            pw.verify_workspace(self.workspace), first["run_id"], None
        )
        self.assertEqual(pending["revision"], "r0001")
        self.assertEqual(pending["latest_revision"], "r0002")
        self.assertTrue(pending["reconciliation_pending"])
        rows = pw.prompt_rows(self.workspace, None, None)
        self.assertEqual(rows[0]["status"], "reconcile_pending")

        resumed = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=first["run_id"],
            force_new_run=False,
            clock=lambda: FIXED_UTC.replace(second=2),
        )
        self.assertEqual(resumed["revision"], "r0002")
        self.assertTrue(resumed["resumed_reconciliation"])
        manifest = json.loads(Path(first["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["revisions"]), 2)

        self.write_handoff(first["run_id"], status="prepared", bound_revision="r0002")
        settled = pw.verify_command(self.workspace, None, first["run_id"])
        self.assertFalse(settled["run"]["reconciliation_pending"])

    def test_reconcile_rejects_running_task(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        self.write_handoff(snapshot["run_id"], status="running")
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The edited private prompt workflow is usable.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.snapshot_prompt,
            self.workspace,
            prompt,
            run_id=snapshot["run_id"],
            force_new_run=False,
        )

    def test_reconcile_validates_prior_run_before_appending(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The edited private prompt workflow is usable.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        Path(snapshot["snapshot"]).write_text("tampered", encoding="utf-8")
        Path(snapshot["snapshot"]).chmod(0o600)
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.snapshot_prompt,
            self.workspace,
            prompt,
            run_id=snapshot["run_id"],
            force_new_run=False,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX symlink check")
    def test_reconcile_rejects_unsafe_handoff(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        run_dir = Path(snapshot["manifest"]).parent
        external_handoff = self.root / "external-handoff.md"
        external_handoff.write_text("- Overall status: prepared\n", encoding="utf-8")
        (run_dir / "handoff.md").symlink_to(external_handoff)
        prompt.write_text(
            prompt.read_text(encoding="utf-8").replace(
                "The private prompt workflow is usable.",
                "The reconciled private prompt workflow is usable.",
            ),
            encoding="utf-8",
        )
        prompt.chmod(0o600)
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.snapshot_prompt,
            self.workspace,
            prompt,
            run_id=snapshot["run_id"],
            force_new_run=False,
        )

    def test_verify_detects_snapshot_tampering_and_bad_modes(self) -> None:
        prompt = self.new_prompt()
        self.complete_prompt(prompt)
        snapshot = pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        Path(snapshot["snapshot"]).write_text("tampered", encoding="utf-8")
        Path(snapshot["snapshot"]).chmod(0o600)
        self.assert_error(
            "RUN_STATE_INVALID",
            pw.verify_command,
            self.workspace,
            None,
            snapshot["run_id"],
        )

        if os.name == "posix":
            self.workspace.chmod(0o644)
            self.assert_error(
                "WORKSPACE_PERMISSION_INVALID",
                pw.verify_command,
                self.workspace,
                None,
                None,
            )
            self.workspace.chmod(0o600)

    def test_list_filters_metadata_without_leaking_body(self) -> None:
        sentinel = "PRIVATE_BODY_SENTINEL_17fb"
        prompt = self.new_prompt()
        self.complete_prompt(prompt, sentinel=sentinel)
        pw.snapshot_prompt(
            self.workspace,
            prompt,
            run_id=None,
            force_new_run=False,
            clock=lambda: FIXED_UTC,
        )
        rows = pw.prompt_rows(self.workspace, "workspace", "2026-07-12")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "snapshot_only")
        self.assertEqual(rows[0]["last_invoked_at"], "2026-07-12T14:30:00+00:00")
        self.assertNotIn("prompt_id", rows[0])
        self.assertNotIn("latest_run_id", rows[0])
        self.assertNotIn(sentinel, json.dumps(rows))

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "list",
                "--workspace",
                str(self.workspace),
                "--query",
                "workspace",
                "--json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(sentinel, result.stdout + result.stderr)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed[0]["path"], str(prompt))
        self.assertNotIn("prompt_id", parsed[0])

        human = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "list",
                "--workspace",
                str(self.workspace),
                "--query",
                "workspace",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(human.returncode, 0, human.stdout + human.stderr)
        fields = human.stdout.rstrip("\n").split("\t")
        self.assertEqual(len(fields), 4)
        self.assertEqual(fields[0], rows[0]["last_invoked_at"])
        self.assertEqual(fields[1], rows[0]["status"])
        self.assertEqual(fields[2], rows[0]["title"])
        self.assertEqual(fields[3], rows[0]["path"])
        self.assertNotIn(sentinel, human.stdout + human.stderr)


if __name__ == "__main__":
    unittest.main()
