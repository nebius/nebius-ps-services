#!/usr/bin/env python3
"""Offline tests for the Agentic SDLC private prompt workspace."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).with_name("prompt_workspace.py")
SPEC = importlib.util.spec_from_file_location("sdlc_prompt_workspace", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(workspace)


def write_private(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    path.chmod(0o600)


class PromptWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.codex_home = self.root / "codex-home"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def initialize(self) -> dict[str, object]:
        return workspace.initialize(self.project, self.codex_home, False, "code")

    def prompt_path(self) -> Path:
        path = Path(str(self.initialize()["starter_prompt"]))
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "<!-- Required: describe what must be true when the SDLC run is complete. -->",
            "The requested behavior is implemented and verified.",
        )
        text = text.replace(
            "- [ ] <!-- Required: add an observable, testable completion criterion. -->",
            "- [ ] The requested behavior meets its observable acceptance criteria.",
        )
        text = text.replace(
            "<!-- Required: name expected checks or ask Codex to derive them. -->",
            "Run focused tests and the Agentic SDLC verification checks.",
        )
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        return path

    def intake(self, prompt: Path | str) -> dict[str, object]:
        return workspace.intake(str(prompt), self.project, self.codex_home)

    def set_run_status(self, result: dict[str, object], status: str) -> None:
        run_dir = Path(str(result["snapshot"])).parents[2]
        write_private(run_dir / "run.json", {"status": status})

    def edit_prompt(self, path: Path, marker: str) -> None:
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("## Steering\n", f"## Steering\n\n{marker}\n", 1),
            encoding="utf-8",
        )
        path.chmod(0o600)

    def test_init_is_idempotent_and_survives_git_init(self) -> None:
        first = self.initialize()
        prompt = Path(str(first["starter_prompt"]))
        before = prompt.read_bytes()
        before_mtime = prompt.stat().st_mtime_ns
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        second = self.initialize()
        self.assertEqual(first["workspace"], second["workspace"])
        self.assertFalse(second["starter_created"])
        self.assertEqual(prompt.read_bytes(), before)
        self.assertEqual(prompt.stat().st_mtime_ns, before_mtime)
        self.assertEqual(list(self.project.iterdir()), [self.project / ".git"])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(prompt.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(prompt.parent.stat().st_mode), 0o700)

    def test_concurrent_init_creates_one_starter_prompt(self) -> None:
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(executor.map(lambda _index: self.initialize(), range(6)))
        self.assertEqual(
            [result["starter_created"] for result in results].count(True), 1
        )
        self.assertEqual(len({str(result["starter_prompt"]) for result in results}), 1)
        prompt_root = Path(str(results[0]["starter_prompt"])).parent
        self.assertEqual(len(list(prompt_root.glob("*.md"))), 1)

    def test_untouched_starter_fails_before_creating_a_run(self) -> None:
        initialized = self.initialize()
        project_dir = Path(str(initialized["workspace"])).parent
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            self.intake(Path(str(initialized["starter_prompt"])))
        self.assertEqual(caught.exception.code, "PROMPT_INPUT_INVALID")
        self.assertFalse((project_dir / "active-run.json").exists())

    def test_new_resume_steering_resolve_and_completed_rerun(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt.name)
        self.assertEqual(first["action"], "new")
        self.assertEqual(first["revision"], "r0001")
        self.assertEqual(Path(str(first["snapshot"])).read_bytes(), prompt.read_bytes())
        resumed = self.intake(prompt)
        self.assertEqual(resumed["action"], "resume")
        self.assertEqual(resumed["run_id"], first["run_id"])
        self.edit_prompt(prompt, "Prefer the smallest vertical slice.")
        steered = self.intake(prompt.name)
        self.assertEqual(steered["action"], "steering")
        self.assertEqual(steered["revision"], "r0002")
        repeated = self.intake(prompt.name)
        self.assertEqual(repeated["revision"], "r0002")
        manifest = Path(str(first["snapshot"])).parents[2].parent / "workspace.json"
        resolved = workspace.steering_resolve(
            manifest, str(first["run_id"]), "r0002", "applied"
        )
        self.assertEqual(resolved["disposition"], "applied")
        self.assertEqual(self.intake(prompt.name)["action"], "resume")
        self.set_run_status(first, "complete")
        done = self.intake(prompt.name)
        self.assertEqual(done["outcome"], "ALREADY_COMPLETE")
        self.edit_prompt(prompt, "Add one follow-up requirement.")
        next_run = self.intake(prompt.name)
        self.assertEqual(next_run["action"], "new")
        self.assertNotEqual(next_run["run_id"], first["run_id"])

    def test_active_prompt_conflict_preserves_existing_run(self) -> None:
        first_prompt = self.prompt_path()
        first = self.intake(first_prompt)
        second_prompt = first_prompt.with_name("second.md")
        raw = first_prompt.read_text(encoding="utf-8").replace(
            str(workspace.parse_prompt(first_prompt)["prompt_id"]),
            "prompt-" + "1" * 32,
            1,
        )
        second_prompt.write_text(raw, encoding="utf-8")
        second_prompt.chmod(0o600)
        with self.assertRaisesRegex(
            workspace.PromptWorkspaceError, "another prompt"
        ) as caught:
            self.intake(second_prompt)
        self.assertEqual(caught.exception.code, "ACTIVE_RUN_CONFLICT")
        self.assertEqual(self.intake(first_prompt)["run_id"], first["run_id"])

    def test_legacy_unfinished_run_fails_closed_and_completed_is_readable(self) -> None:
        prompt = self.prompt_path()
        initialized = self.initialize()
        project_dir = Path(str(initialized["workspace"])).parent
        run_id = "run-legacy"
        run_dir = project_dir / run_id
        run_dir.mkdir(mode=0o700)
        write_private(run_dir / "run.json", {"status": "running"})
        write_private(project_dir / "active-run.json", {"run_id": run_id})
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            self.intake(prompt)
        self.assertEqual(caught.exception.code, "WORKFLOW_UPGRADE_REQUIRED")
        write_private(run_dir / "run.json", {"status": "complete"})
        result = self.intake(prompt)
        self.assertEqual(result["action"], "new")
        self.assertTrue(run_dir.exists())

    def test_foreign_symlink_tamper_and_sensitive_input_are_rejected(self) -> None:
        prompt = self.prompt_path()
        foreign = self.root / "foreign.md"
        foreign.write_bytes(prompt.read_bytes())
        foreign.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError):
            self.intake(foreign)
        link = prompt.with_name("linked.md")
        link.symlink_to(prompt)
        with self.assertRaises(workspace.PromptWorkspaceError):
            self.intake(link)
        with self.assertRaises(workspace.PromptWorkspaceError):
            self.intake(link.name)
        link.unlink()
        original = prompt.read_text(encoding="utf-8")
        assignments = (
            "GITHUB" + "_TOKEN = ghp_" + "x" * 24,
            "GITHUB" + "_TOKEN = ghp_" + "x" * 24 + " # example",
            "OPENAI" + "_API_KEY = sk-" + "x" * 24,
            "token = " + "x" * 16,
        )
        for sensitive_assignment in assignments:
            with self.subTest(secret_kind=sensitive_assignment.split(" ", 1)[0]):
                text = original.replace(
                    "## Steering\n", f"## Steering\n\n{sensitive_assignment}\n", 1
                )
                prompt.write_text(text, encoding="utf-8")
                prompt.chmod(0o600)
                with self.assertRaises(workspace.PromptWorkspaceError) as caught:
                    self.intake(prompt)
                self.assertEqual(caught.exception.code, "PROMPT_SENSITIVE_INPUT")

    def test_public_nebius_metadata_is_not_secret_input(self) -> None:
        prefix = "NEBIUS" + "_"
        assignments = (
            prefix + "PROFILE = codex-agent-project-1234567890",
            prefix + "PROJECT_ID = project-1234567890",
            prefix
            + "AUTH_CREDENTIALS_FILE = "
            + "/tmp/codex-agent-authkey.project-1234567890.json",
        )

        for assignment in assignments:
            with self.subTest(variable=assignment.split(" ", 1)[0]):
                self.assertFalse(workspace.contains_secret(assignment))

    def test_public_nebius_metadata_cannot_mask_secret_input(self) -> None:
        prefix = "NEBIUS" + "_"
        text = (
            prefix
            + "PROFILE = codex-agent-project-1234567890 "
            + prefix
            + "IAM_"
            + "TOKEN = "
            + "x" * 32
        )

        self.assertTrue(workspace.contains_secret(text))

    def test_snapshot_tamper_fails_closed(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        snapshot = Path(str(first["snapshot"]))
        snapshot.write_text("tampered\n", encoding="utf-8")
        snapshot.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            self.intake(prompt)
        self.assertEqual(caught.exception.code, "RUN_STATE_INVALID")

    def test_concurrent_unchanged_intake_creates_one_run_and_revision(self) -> None:
        prompt = self.prompt_path()
        with ThreadPoolExecutor(max_workers=6) as executor:
            results = list(
                executor.map(lambda _index: self.intake(prompt.name), range(6))
            )
        self.assertEqual(len({str(result["run_id"]) for result in results}), 1)
        self.assertEqual([result["action"] for result in results].count("new"), 1)
        self.assertTrue(all(result["revision"] == "r0001" for result in results))

    def test_missing_active_pointer_is_repaired_from_one_bound_run(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        project_dir = Path(str(first["snapshot"])).parents[3]
        (project_dir / "active-run.json").unlink()
        resumed = self.intake(prompt.name)
        self.assertEqual(resumed["action"], "resume")
        self.assertEqual(resumed["run_id"], first["run_id"])
        pointer = json.loads(
            (project_dir / "active-run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(pointer["run_id"], first["run_id"])

    def test_multiple_unfinished_runs_fail_closed(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        project_dir = Path(str(first["snapshot"])).parents[3]
        other = project_dir / "run-conflict"
        other.mkdir(mode=0o700)
        write_private(other / "run.json", {"status": "running"})
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            self.intake(prompt.name)
        self.assertEqual(caught.exception.code, "RUN_STATE_INVALID")

    def test_interrupted_revision_snapshot_is_adopted_once(self) -> None:
        prompt = self.prompt_path()
        first = self.intake(prompt)
        self.edit_prompt(prompt, "Recover this interrupted steering revision.")
        run_dir = Path(str(first["snapshot"])).parents[2]
        revision_dir = run_dir / "inputs" / "r0002"
        revision_dir.mkdir(mode=0o700)
        snapshot = revision_dir / "prompt.md"
        snapshot.write_bytes(prompt.read_bytes())
        snapshot.chmod(0o600)
        recovered = self.intake(prompt.name)
        self.assertEqual(recovered["action"], "steering")
        self.assertEqual(recovered["revision"], "r0002")
        self.assertEqual(self.intake(prompt.name)["revision"], "r0002")

    def test_cli_intake_does_not_expose_internal_ids_or_snapshot_paths(self) -> None:
        prompt = self.prompt_path()
        result = subprocess.run(
            [
                "python3",
                str(MODULE_PATH),
                "intake",
                str(prompt),
                "--project-path",
                str(self.project),
                "--codex-home",
                str(self.codex_home),
                "--json",
            ],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        value = json.loads(result.stdout)
        self.assertNotIn("project_id", value)
        self.assertNotIn("run_id", value)
        self.assertNotIn("sha256", value)
        self.assertNotIn("snapshot", value)

    def test_malformed_oversize_and_unsafe_mode_fail_before_run_creation(self) -> None:
        prompt = self.prompt_path()
        project_dir = Path(str(self.initialize()["workspace"])).parent
        original = prompt.read_bytes()
        prompt.write_bytes(b"\xff\xfe")
        prompt.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as invalid_utf8:
            self.intake(prompt)
        self.assertEqual(invalid_utf8.exception.code, "PROMPT_INPUT_INVALID")
        self.assertFalse((project_dir / "active-run.json").exists())
        prompt.write_bytes(original + b"x" * workspace.MAX_PROMPT_BYTES)
        prompt.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as oversize:
            self.intake(prompt)
        self.assertEqual(oversize.exception.code, "PROMPT_INPUT_INVALID")
        self.assertFalse((project_dir / "active-run.json").exists())
        prompt.write_bytes(original)
        prompt.chmod(0o644)
        with self.assertRaises(workspace.PromptWorkspaceError) as permissions:
            self.intake(prompt)
        self.assertEqual(permissions.exception.code, "WORKSPACE_PERMISSION_INVALID")
        self.assertFalse((project_dir / "active-run.json").exists())

    def test_editor_workspace_new_prompt_history_and_metadata_listing(self) -> None:
        initialized = self.initialize()
        editor = json.loads(
            Path(str(initialized["editor_workspace"])).read_text(encoding="utf-8")
        )
        labels = [task["label"] for task in editor["tasks"]["tasks"]]
        self.assertEqual(
            labels,
            ["Agentic SDLC: New Prompt", "Agentic SDLC: Prompt History"],
        )
        manifest = Path(str(initialized["workspace"]))
        created_at = datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc)
        first = workspace.create_prompt(
            manifest,
            "Add reports; $(touch nope) `unsafe`",
            clock=lambda: created_at,
            id_factory=lambda: "1" * 32,
        )
        second = workspace.create_prompt(
            manifest,
            "Add reports; $(touch nope) `unsafe`",
            clock=lambda: created_at,
            id_factory=lambda: "2" * 32,
        )
        self.assertNotEqual(first["path"], second["path"])
        self.assertTrue(str(second["path"]).endswith("--02.md"))
        self.assertFalse((self.project / "nope").exists())
        rows = workspace.prompt_rows(manifest, "reports", "2026-07-16")
        self.assertEqual(len(rows), 2)
        serialized = json.dumps(rows)
        self.assertNotIn("prompt-" + "1" * 32, serialized)
        self.assertNotIn("sha256", serialized)
        self.assertNotIn("Add reports; $(touch nope) `unsafe`\n\n##", serialized)
        self.assertTrue(all(row["status"] == "draft" for row in rows))

    def test_activity_is_monotonic_and_rejected_intake_does_not_reorder(self) -> None:
        prompt = self.prompt_path()
        result = self.intake(prompt)
        project_dir = Path(str(result["snapshot"])).parents[3]
        prompt_id = str(workspace.prompt_metadata(prompt)["prompt_id"])
        future = datetime(2030, 1, 1, tzinfo=timezone.utc)
        workspace.update_activity(project_dir, prompt_id, future)
        workspace.update_activity(
            project_dir, prompt_id, datetime(2020, 1, 1, tzinfo=timezone.utc)
        )
        before = (project_dir / "activity.json").read_bytes()
        safe = prompt.read_bytes()
        self.edit_prompt(prompt, "token = " + "x" * 16)
        with self.assertRaises(workspace.PromptWorkspaceError):
            self.intake(prompt)
        self.assertEqual((project_dir / "activity.json").read_bytes(), before)
        prompt.write_bytes(safe)
        prompt.chmod(0o600)
        rows = workspace.prompt_rows(project_dir / "workspace.json", None, None)
        row = next(item for item in rows if item["path"] == str(prompt.resolve()))
        self.assertEqual(row["last_invoked_at"], future.isoformat(timespec="seconds"))

    def test_exact_manual_rename_repairs_binding_and_run_mirror(self) -> None:
        prompt = self.prompt_path()
        result = self.intake(prompt)
        run_dir = Path(str(result["snapshot"])).parents[2]
        write_private(
            run_dir / "run.json",
            {
                "status": "running",
                "prompt": {"filename": prompt.name},
                "prompt_filename": prompt.name,
            },
        )
        renamed = prompt.with_name("renamed-product-prompt.md")
        prompt.rename(renamed)
        accepted = self.intake(renamed.name)
        self.assertTrue(accepted["renamed"])
        binding = json.loads((run_dir / "prompt.json").read_text(encoding="utf-8"))
        mirror = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(binding["prompt_filename"], renamed.name)
        self.assertEqual(mirror["prompt_filename"], renamed.name)
        self.assertEqual(mirror["prompt"]["filename"], renamed.name)

    def test_rename_and_edit_or_stale_copy_fails_closed(self) -> None:
        prompt = self.prompt_path()
        result = self.intake(prompt)
        project_dir = Path(str(result["snapshot"])).parents[3]
        renamed = prompt.with_name("renamed-and-edited.md")
        prompt.rename(renamed)
        self.edit_prompt(renamed, "Change during rename.")
        before = (project_dir / "activity.json").read_bytes()
        with self.assertRaises(workspace.PromptWorkspaceError) as drift:
            self.intake(renamed.name)
        self.assertEqual(drift.exception.code, "PROMPT_DRIFT")
        self.assertEqual((project_dir / "activity.json").read_bytes(), before)
        renamed.write_bytes(Path(str(result["snapshot"])).read_bytes())
        renamed.chmod(0o600)
        stale = renamed.with_name("stale-copy.md")
        stale.write_bytes(renamed.read_bytes())
        stale.chmod(0o600)
        with self.assertRaises(workspace.PromptWorkspaceError) as conflict:
            self.intake(renamed.name)
        self.assertEqual(conflict.exception.code, "PROMPT_CONFLICT")

    def test_verify_detects_active_binding_drift(self) -> None:
        prompt = self.prompt_path()
        result = self.intake(prompt)
        run_dir = Path(str(result["snapshot"])).parents[2]
        manifest = run_dir.parent / "workspace.json"
        verified = workspace.verify_command(manifest, prompt, run_dir.name)
        self.assertEqual(verified["action"], "verified")
        self.edit_prompt(prompt, "Unaccepted drift.")
        with self.assertRaises(workspace.PromptWorkspaceError) as caught:
            workspace.verify_command(manifest, prompt, run_dir.name)
        self.assertEqual(caught.exception.code, "PROMPT_DRIFT")


if __name__ == "__main__":
    unittest.main()
