#!/usr/bin/env python3
"""Disposable functional tests for the private prompt workspace helper."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
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


SCRIPT = Path(__file__).resolve().with_name("prompt_workspace.py")
SPEC = importlib.util.spec_from_file_location("prompt_workspace", SCRIPT)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import invariant.
    raise RuntimeError("could not load prompt_workspace.py")
pw = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pw
SPEC.loader.exec_module(pw)


FIXED_LOCAL = datetime(2026, 7, 12, 14, 30, tzinfo=timezone.utc)
FIXED_UTC = datetime(2026, 7, 12, 21, 30, tzinfo=timezone.utc)


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
        self.repo = self.root / "repo with spaces"
        self.repo.mkdir()
        git("init", "-q", cwd=self.repo)
        (self.repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        git("add", "tracked.txt", cwd=self.repo)
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
        self.scope = self.repo / "services" / "example"
        self.scope.mkdir(parents=True)
        self.codex_home = self.root / "private codex"
        self.workspace_result = pw.init_workspace(
            self.repo,
            "services/example",
            self.codex_home,
            clock=lambda: FIXED_LOCAL,
        )
        self.workspace = Path(self.workspace_result["workspace"])
        self.prompt_root = Path(self.workspace_result["prompt_root"])

    def tearDown(self) -> None:
        self.temporary.cleanup()

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
            "<!-- Required: describe what must be true when the work is complete. -->",
            "The private prompt workflow is usable.",
        )
        text = text.replace(
            "- [ ] <!-- Required: add an observable, testable completion criterion. -->",
            "- [ ] The focused tests pass.",
        )
        text = text.replace(
            "<!-- Required: name expected checks or ask Codex to derive them from the repo. -->",
            "Run the focused prompt workspace tests.",
        )
        if sentinel:
            text = text.replace(
                "<!-- Optional: add relevant repository facts, paths, or background. -->",
                sentinel,
            )
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
            self.repo,
            "services/example",
            self.codex_home,
            clock=lambda: datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(repeated, self.workspace_result)
        self.assertEqual(self.workspace.read_bytes(), manifest_before)
        self.assertEqual(vscode.read_bytes(), vscode_before)
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), status_before)
        self.assertFalse(self.workspace.is_relative_to(self.repo))

        if os.name == "posix":
            alias = self.root / "repo-alias"
            alias.symlink_to(self.repo, target_is_directory=True)
            aliased = pw.init_workspace(
                alias,
                "services/example",
                self.codex_home,
                clock=lambda: FIXED_LOCAL,
            )
            self.assertEqual(aliased["workspace"], str(self.workspace))

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
        self.assertEqual(len(list(starter.parent.glob("*.md"))), 1)

        manifest = json.loads(
            Path(first_result["workspace"]).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["source_root"], str(self.scope.resolve()))
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
        self.assertEqual(len(list(prompt_root.glob("*.md"))), 1)
        self.assertEqual(git("status", "--porcelain=v1", cwd=self.repo), "")

    def test_init_isolates_clones_and_scopes(self) -> None:
        root_scope = pw.init_workspace(
            self.repo, ".", self.codex_home, clock=lambda: FIXED_LOCAL
        )
        self.assertNotEqual(root_scope["scope_id"], self.workspace_result["scope_id"])

        second_parent = self.root / "second"
        second_repo = second_parent / self.repo.name
        second_repo.mkdir(parents=True)
        git("init", "-q", cwd=second_repo)
        clone = pw.init_workspace(
            second_repo, ".", self.codex_home, clock=lambda: FIXED_LOCAL
        )
        self.assertNotEqual(clone["project_id"], self.workspace_result["project_id"])

        manifest = json.loads(self.workspace.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], pw.WORKSPACE_SCHEMA)
        self.assertEqual(manifest["repo_root"], str(self.repo.resolve()))
        self.assertEqual(manifest["scope"], "services/example")
        self.assertEqual(manifest["source_root"], str(self.scope.resolve()))
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
            self.repo,
            ".",
            self.repo / ".codex",
        )
        self.assert_error(
            "WORKSPACE_PATH_INVALID",
            pw.init_workspace,
            self.repo,
            ".",
            self.repo / ".git" / "private-codex",
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
            self.repo,
            ".",
            foreign_home,
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
            self.repo,
            ".",
            other_home,
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
                {"name": "CODE", "path": str(self.scope.resolve())},
                {"name": "PROMPTS", "path": "prompts"},
            ],
        )
        tasks = value["tasks"]
        self.assertEqual(tasks["version"], "2.0.0")
        self.assertEqual(len(tasks["tasks"]), 1)
        task = tasks["tasks"][0]
        self.assertEqual(task["label"], "Task Implementer: New Prompt")
        self.assertEqual(task["type"], "process")
        self.assertEqual(Path(task["args"][0]).name, "prompt_workspace.py")
        self.assertIsInstance(task["args"], list)
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
            "2026-07-12_1430--add-prompt-workspace-support.md",
        )
        self.assertEqual(
            second.name,
            "2026-07-12_1430--add-prompt-workspace-support--02.md",
        )
        text = first.read_text(encoding="utf-8")
        self.assertIn(f"schema: {pw.PROMPT_SCHEMA}", text)
        self.assertIn(f"prompt_id: prompt-{'a' * 32}", text)
        self.assertIn('title: "Add prompt workspace support"', text)
        self.assertIn("created_at: 2026-07-12T14:30:00+00:00", text)
        self.assertIn("## Acceptance criteria", text)

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
        self.assert_error(
            "PROMPT_INPUT_INVALID",
            pw.verify_command,
            self.workspace,
            prompt,
            None,
        )
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
        self.assert_error(
            "PROMPT_INPUT_INVALID",
            pw.verify_command,
            self.workspace,
            prompt,
            None,
        )
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
        activity_before = activity.read_bytes()
        rows_before = pw.prompt_rows(self.workspace, None, None)

        with self.assertRaises(pw.PromptWorkspaceError) as context:
            pw.route_project_prompt(
                self.scope,
                self.codex_home,
                rejected_prompt.name,
                clock=lambda: FIXED_UTC.replace(second=5),
            )
        self.assertEqual(context.exception.code, "ACTIVE_RUN_EXISTS")
        self.assertIn(str(active_prompt), context.exception.message)
        self.assertNotIn("run-", context.exception.message)
        self.assertEqual(activity.read_bytes(), activity_before)
        self.assertEqual(pw.prompt_rows(self.workspace, None, None), rows_before)

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
        exact = self.prompt_root / "renamed-exact.md"
        edited = self.prompt_root / "renamed-edited.md"
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
        stale = self.prompt_root / "stale-copy.md"
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
        renamed = self.prompt_root / "renamed-prompt.md"
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
