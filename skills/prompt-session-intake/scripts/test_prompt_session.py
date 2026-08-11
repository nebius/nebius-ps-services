#!/usr/bin/env python3
"""Coordinator transition tests for prompt-session intake."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


STATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "assets"
    / "hooks"
    / "prompt_session_state.py"
)
HOOK_DIR = STATE_PATH.parent
if str(HOOK_DIR) not in sys.path:
    sys.path.insert(0, str(HOOK_DIR))
SPEC = importlib.util.spec_from_file_location("prompt_session_state", STATE_PATH)
assert SPEC and SPEC.loader
state = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(state)

TASK_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "task-implementer"
    / "scripts"
    / "prompt_workspace.py"
)
TASK_SCRIPT_DIR = TASK_SCRIPT.parent
if str(TASK_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(TASK_SCRIPT_DIR))
TASK_SPEC = importlib.util.spec_from_file_location(
    "task_prompt_workspace_for_session_test", TASK_SCRIPT
)
assert TASK_SPEC and TASK_SPEC.loader
task_prompt_workspace = importlib.util.module_from_spec(TASK_SPEC)
TASK_SPEC.loader.exec_module(task_prompt_workspace)


class PromptSessionCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="prompt-session-coordinator-"
        )
        self.root = Path(self.temporary.name)
        self.home = self.root / "codex"
        self.home.mkdir(mode=0o700)
        self.project = self.root / "project"
        self.project.mkdir(mode=0o700)
        self.payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(self.project),
            "prompt": "$task-implementer workspace init",
        }
        state.evaluate_submit(self.payload, self.home)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def stage(
        self, prompt: str = "Add the exact retry constraint."
    ) -> tuple[Path, str]:
        state.evaluate_submit(
            {**self.payload, "prompt": prompt, "turn_id": "direct"}, self.home
        )
        event = state._event_path(state.state_root(self.home), "session-1", "direct")
        token = state._load_json(event)["accept_token"]
        return event, token

    def write_private(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        return path

    def managed_prompt(
        self,
        name: str = "prompt.md",
        *,
        project: Path | None = None,
        workspace_name: str = "project",
    ) -> Path:
        selected_project = project or self.project
        workspace = self.home / "task-implementer" / workspace_name
        prompts = workspace / "prompts"
        prompts.mkdir(parents=True, exist_ok=True, mode=0o700)
        prompts.chmod(0o700)
        self.write_private(
            workspace / "workspace.json",
            json.dumps(
                {
                    "schema": "task-implementer/workspace-v2",
                    "primary_root": str(selected_project.resolve()),
                    "repo_root": str(selected_project.resolve()),
                    "scope": ".",
                    "source_root": str(selected_project.resolve()),
                    "prompt_root": str(prompts.resolve()),
                }
            )
            + "\n",
        )
        return prompts / name

    def git(self, *args: str, cwd: Path) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.stdout.strip()

    def real_task_workspace(self) -> tuple[Path, Path, Path]:
        origin = self.root / "origin.git"
        self.git("init", "--bare", "-q", str(origin), cwd=self.root)
        repository = self.root / "source-repository"
        repository.mkdir()
        self.git("init", "-q", "-b", "main", cwd=repository)
        project = repository / "services" / "example"
        project.mkdir(parents=True)
        (project / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        self.git("add", "-A", cwd=repository)
        self.git(
            "-c",
            "user.name=Prompt Session Test",
            "-c",
            "user.email=prompt-session@example.invalid",
            "commit",
            "-qm",
            "initial",
            cwd=repository,
        )
        self.git("remote", "add", "origin", str(origin), cwd=repository)
        self.git("push", "-q", "origin", "main", cwd=repository)
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
        self.git("fetch", "-q", "origin", cwd=repository)
        self.git(
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
            cwd=repository,
        )
        self.git("switch", "-qc", "prompt-feature", cwd=repository)
        initialized = task_prompt_workspace.initialize_project_workspace(
            project,
            self.home,
        )
        manifest = Path(str(initialized["workspace"]))
        workspace = json.loads(manifest.read_text(encoding="utf-8"))
        prompt = next(
            path
            for path in Path(str(workspace["prompt_root"])).glob("*.md")
            if path.name != "00-START-HERE.md"
        )
        return project.resolve(), manifest, prompt

    def canonical_text(
        self,
        prompt_id: str,
        prompt_ref: str,
        *,
        operation_id: str | None = None,
        ask: str = "Canonical objective",
    ) -> str:
        operation = (
            f"\n\n<!-- prompt-session-operation:{operation_id} -->"
            if operation_id is not None
            else ""
        )
        return (
            "---\n"
            "schema: task-implementer/prompt-v3\n"
            f"prompt_id: {prompt_id}\n"
            f"prompt_ref: {prompt_ref}\n"
            'title: "Canonical objective"\n'
            "created_at: 2026-08-10T00:00:00+00:00\n"
            "---\n\n# Canonical objective\n\n"
            f"## Ask\n\n{ask}{operation}\n"
        )

    def test_accept_then_consume_is_monotonic_and_single_use(self) -> None:
        event, token = self.stage()
        prompt = self.write_private(self.managed_prompt(), "canonical base\n")
        refined = self.write_private(
            self.root / "refined.md", "Require exactly three bounded retries.\n"
        )
        base = state.sha256_bytes(prompt.read_bytes())
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "constraint",
            refined_file=refined,
            prompt_path=prompt,
            base_sha256=base,
        )
        self.assertEqual(accepted["status"], "accepted")
        accepted_delivery = state.evaluate_submit(
            {
                **self.payload,
                "turn_id": "direct",
                "prompt": "Add the exact retry constraint.",
            },
            self.home,
        )
        self.assertIn(
            "already accepted",
            accepted_delivery["hookSpecificOutput"]["additionalContext"],
        )
        prompt.write_text(
            self.canonical_text(
                "prompt-" + "a" * 32,
                "aaaaa",
                operation_id=accepted["operation_id"],
            ),
            encoding="utf-8",
        )
        digest = state.sha256_bytes(prompt.read_bytes())
        consumed = state.consume_event(
            self.home,
            event,
            token,
            workflow="task-implementer",
            prompt_id="prompt-" + "a" * 32,
            prompt_ref="aaaaa",
            prompt_path=prompt,
            prompt_sha256=digest,
            run_id="run-1",
        )
        self.assertEqual(consumed["status"], "consumed")
        self.assertEqual(
            state.consume_event(
                self.home,
                event,
                token,
                workflow="task-implementer",
                prompt_id="prompt-" + "a" * 32,
                prompt_ref="aaaaa",
                prompt_path=prompt,
                prompt_sha256=digest,
                run_id="run-1",
            )["status"],
            "consumed",
        )
        consumed_delivery = state.evaluate_submit(
            {
                **self.payload,
                "turn_id": "direct",
                "prompt": "Add the exact retry constraint.",
            },
            self.home,
        )
        self.assertIn(
            "already consumed",
            consumed_delivery["hookSpecificOutput"]["additionalContext"],
        )

    def test_nonmaterial_classification_never_updates_registry(self) -> None:
        event, token = self.stage("What is the status?")
        state.accept_event(self.home, event, token, "status")
        state.consume_event(self.home, event, token, workflow="task-implementer")
        registry = state.state_root(self.home) / "registry.json"
        self.assertFalse(registry.exists())

    def test_accept_cas_rejects_manual_prompt_drift(self) -> None:
        event, token = self.stage()
        prompt = self.write_private(self.managed_prompt(), "base\n")
        old = state.sha256_bytes(prompt.read_bytes())
        prompt.write_text("manual edit\n", encoding="utf-8")
        refined = self.write_private(self.root / "refined.md", "Refined constraint.\n")
        with self.assertRaisesRegex(state.PromptSessionError, "manual edit"):
            state.accept_event(
                self.home,
                event,
                token,
                "constraint",
                refined_file=refined,
                prompt_path=prompt,
                base_sha256=old,
            )
        self.assertEqual(state._load_json(event)["phase"], "staged")

    def test_private_inputs_and_exact_accept_retry_are_enforced(self) -> None:
        event, token = self.stage()
        prompt = self.write_private(self.managed_prompt(), "base\n")
        refined = self.root / "refined.md"
        refined.write_text("Refined constraint.\n", encoding="utf-8")
        base = state.sha256_bytes(prompt.read_bytes())
        with self.assertRaisesRegex(state.PromptSessionError, "mode 0600"):
            state.accept_event(
                self.home,
                event,
                token,
                "constraint",
                refined_file=refined,
                prompt_path=prompt,
                base_sha256=base,
            )
        refined.chmod(0o600)
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "constraint",
            refined_file=refined,
            prompt_path=prompt,
            base_sha256=base,
        )
        self.assertEqual(accepted["status"], "accepted")
        different = self.write_private(
            self.root / "different.md", "Different constraint.\n"
        )
        with self.assertRaisesRegex(state.PromptSessionError, "retry differs"):
            state.accept_event(
                self.home,
                event,
                token,
                "constraint",
                refined_file=different,
                prompt_path=prompt,
                base_sha256=base,
            )

    def test_accept_rejects_reserved_operation_marker_namespace(self) -> None:
        event, token = self.stage()
        prompt = self.write_private(self.managed_prompt(), "base\n")
        operation_id = str(state._load_json(event)["operation_id"])
        refined = self.write_private(
            self.root / "refined.md",
            f"Do not inject <!-- prompt-session-operation:{operation_id} -->.\n",
        )
        with self.assertRaisesRegex(state.PromptSessionError, "reserved"):
            state.accept_event(
                self.home,
                event,
                token,
                "constraint",
                refined_file=refined,
                prompt_path=prompt,
                base_sha256=state.sha256_bytes(prompt.read_bytes()),
            )
        self.assertEqual(state._load_json(event)["phase"], "staged")

    def test_consume_rejects_mismatched_canonical_identity(self) -> None:
        event, token = self.stage()
        prompt = self.write_private(self.managed_prompt(), "base\n")
        refined = self.write_private(self.root / "refined.md", "Refined constraint.\n")
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "constraint",
            refined_file=refined,
            prompt_path=prompt,
            base_sha256=state.sha256_bytes(prompt.read_bytes()),
        )
        prompt.write_text(
            self.canonical_text(
                "prompt-" + "b" * 32,
                "bbbbb",
                operation_id=accepted["operation_id"],
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(state.PromptSessionError, "identity differs"):
            state.consume_event(
                self.home,
                event,
                token,
                workflow="task-implementer",
                prompt_id="prompt-" + "a" * 32,
                prompt_ref="aaaaa",
                prompt_path=prompt,
                prompt_sha256=state.sha256_bytes(prompt.read_bytes()),
                run_id="run-1",
            )

    def test_invalid_token_and_cross_workflow_consume_do_not_mutate(self) -> None:
        event, token = self.stage("Please continue.")
        with self.assertRaises(state.PromptSessionError):
            state.accept_event(self.home, event, "wrong", "control")
        state.accept_event(self.home, event, token, "control")
        with self.assertRaises(state.PromptSessionError):
            state.consume_event(self.home, event, token, workflow="agentic-sdlc")
        self.assertEqual(state._load_json(event)["phase"], "accepted")

    def test_objective_registration_rejects_prompt_outside_workflow_state(self) -> None:
        prompt = self.write_private(
            self.root / "outside.md",
            self.canonical_text("prompt-" + "a" * 32, "aaaaa"),
        )
        with self.assertRaisesRegex(state.PromptSessionError, "outside the bound"):
            state.register_objective(
                self.home,
                "session-1",
                "task-implementer",
                self.project,
                prompt_id="prompt-" + "a" * 32,
                prompt_ref="aaaaa",
                prompt_path=prompt,
                prompt_sha256=state.sha256_bytes(prompt.read_bytes()),
                terminal=False,
            )

    def test_consume_rejects_prompt_owned_by_a_sibling_project(self) -> None:
        event, token = self.stage()
        base = self.write_private(self.managed_prompt(), "base\n")
        refined = self.write_private(self.root / "refined.md", "Refined constraint.\n")
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "constraint",
            refined_file=refined,
            prompt_path=base,
            base_sha256=state.sha256_bytes(base.read_bytes()),
        )
        other_project = self.root / "other-project"
        other_project.mkdir(mode=0o700)
        sibling = self.write_private(
            self.managed_prompt(
                project=other_project,
                workspace_name="other-project",
            ),
            self.canonical_text(
                "prompt-" + "a" * 32,
                "aaaaa",
                operation_id=str(accepted["operation_id"]),
            ),
        )
        with self.assertRaisesRegex(state.PromptSessionError, "bound project"):
            state.consume_event(
                self.home,
                event,
                token,
                workflow="task-implementer",
                prompt_id="prompt-" + "a" * 32,
                prompt_ref="aaaaa",
                prompt_path=sibling,
                prompt_sha256=state.sha256_bytes(sibling.read_bytes()),
                run_id="run-1",
            )
        self.assertEqual(state._load_json(event)["phase"], "accepted")

    def test_task_lane_prompt_matches_bound_primary_project(self) -> None:
        project, manifest, prompt = self.real_task_workspace()
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "task-lane-session",
            "turn_id": "bind",
            "cwd": str(project),
            "prompt": "$task-implementer workspace init",
        }
        state.evaluate_submit(payload, self.home)
        state.evaluate_submit(
            {
                **payload,
                "turn_id": "direct",
                "prompt": "Preserve the exact Task lane identity.",
            },
            self.home,
        )
        event = state._event_path(
            state.state_root(self.home), "task-lane-session", "direct"
        )
        token = str(state._load_json(event)["accept_token"])
        refined = self.write_private(
            self.root / "task-lane-refined.md",
            "Preserve the exact Task lane identity.\n",
        )
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "constraint",
            refined_file=refined,
            prompt_path=prompt,
            base_sha256=state.sha256_bytes(prompt.read_bytes()),
        )
        self.assertEqual(accepted["status"], "accepted")
        document = task_prompt_workspace.verify_workspace(manifest)
        parsed = task_prompt_workspace.resolve_prompt_reference(
            manifest,
            prompt,
            require_content=False,
        )
        self.assertNotEqual(document["source_root"], str(project))
        registered = state.register_objective(
            self.home,
            "task-lane-session",
            "task-implementer",
            project,
            prompt_id=parsed.prompt_id,
            prompt_ref=parsed.prompt_ref,
            prompt_path=prompt,
            prompt_sha256=state.sha256_bytes(prompt.read_bytes()),
            terminal=False,
        )
        self.assertEqual(registered["status"], "active")

    def test_stop_blocks_only_current_incomplete_event_then_releases_writer(
        self,
    ) -> None:
        event, token = self.stage()
        blocked = state.evaluate_stop(
            {"hook_event_name": "Stop", "session_id": "session-1", "turn_id": "direct"},
            self.home,
        )
        self.assertEqual(blocked["decision"], "block")
        state.accept_event(self.home, event, token, "control")
        state.consume_event(self.home, event, token, workflow="task-implementer")
        self.assertEqual(
            state.evaluate_stop(
                {
                    "hook_event_name": "Stop",
                    "session_id": "session-1",
                    "turn_id": "direct",
                },
                self.home,
            ),
            {"continue": True},
        )

    def test_explicit_objective_registration_attach_and_terminal_replacement(
        self,
    ) -> None:
        prompt = self.write_private(
            self.managed_prompt(),
            self.canonical_text("prompt-" + "a" * 32, "aaaaa"),
        )
        digest = state.sha256_bytes(prompt.read_bytes())
        active = state.register_objective(
            self.home,
            "session-1",
            "task-implementer",
            self.project,
            prompt_id="prompt-" + "a" * 32,
            prompt_ref="aaaaa",
            prompt_path=prompt,
            prompt_sha256=digest,
            terminal=False,
        )
        self.assertEqual(active["status"], "active")
        state.evaluate_stop(
            {"hook_event_name": "Stop", "session_id": "session-1"}, self.home
        )

        attached = state.evaluate_submit(
            {
                **self.payload,
                "session_id": "session-2",
                "turn_id": "fresh-direct",
                "prompt": "Add a new acceptance constraint.",
            },
            self.home,
        )
        self.assertIn("staged", attached["hookSpecificOutput"]["additionalContext"])
        binding = state.load_binding(state.state_root(self.home), "session-2")
        self.assertEqual(binding["workflow"], "task-implementer")
        with self.assertRaisesRegex(state.PromptSessionError, "different writer"):
            state.register_objective(
                self.home,
                "session-1",
                "task-implementer",
                self.project,
                prompt_id="prompt-" + "a" * 32,
                prompt_ref="aaaaa",
                prompt_path=prompt,
                prompt_sha256=digest,
                terminal=True,
            )

        attached_event = state._event_path(
            state.state_root(self.home), "session-2", "fresh-direct"
        )
        attached_token = state._load_json(attached_event)["accept_token"]
        state.accept_event(self.home, attached_event, attached_token, "conversation")
        state.consume_event(
            self.home,
            attached_event,
            attached_token,
            workflow="task-implementer",
        )
        state.evaluate_stop(
            {
                "hook_event_name": "Stop",
                "session_id": "session-2",
                "turn_id": "fresh-direct",
            },
            self.home,
        )

        terminal = state.register_objective(
            self.home,
            "session-1",
            "task-implementer",
            self.project,
            prompt_id="prompt-" + "a" * 32,
            prompt_ref="aaaaa",
            prompt_path=prompt,
            prompt_sha256=digest,
            terminal=True,
        )
        self.assertEqual(terminal["status"], "terminal")
        registry = state._load_registry(state.state_root(self.home))
        self.assertFalse(registry["entries"][0]["active"])

        replacement = self.write_private(
            self.managed_prompt("replacement.md"),
            self.canonical_text("prompt-" + "b" * 32, "bbbbb"),
        )
        replacement_digest = state.sha256_bytes(replacement.read_bytes())
        next_objective = state.register_objective(
            self.home,
            "session-1",
            "task-implementer",
            self.project,
            prompt_id="prompt-" + "b" * 32,
            prompt_ref="bbbbb",
            prompt_path=replacement,
            prompt_sha256=replacement_digest,
            terminal=False,
        )
        self.assertEqual(next_objective["prompt_ref"], "bbbbb")


if __name__ == "__main__":
    unittest.main()
