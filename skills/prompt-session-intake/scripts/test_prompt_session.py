#!/usr/bin/env python3
"""Coordinator transition tests for prompt-session intake."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


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
        projection_sha256: str | None = None,
        ask: str = "Canonical objective",
    ) -> str:
        operation = (
            "\n\n<!-- prompt-session-operation:v2:"
            f"{operation_id}:{projection_sha256} -->"
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
        projection = self.write_private(
            self.root / "project-intent.md", "Require exactly three bounded retries.\n"
        )
        base = state.sha256_bytes(prompt.read_bytes())
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "merge",
            session_id="session-1",
            classification="constraint",
            projection_file=projection,
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
                projection_sha256=accepted["projection_sha256"],
            ),
            encoding="utf-8",
        )
        digest = state.sha256_bytes(prompt.read_bytes())
        consumed = state.consume_event(
            self.home,
            event,
            token,
            session_id="session-1",
            workflow="task-implementer",
            prompt_id="prompt-" + "a" * 32,
            prompt_ref="aaaaa",
            prompt_path=prompt,
            prompt_sha256=digest,
        )
        self.assertEqual(consumed["status"], "consumed")
        self.assertEqual(
            state.consume_event(
                self.home,
                event,
                token,
                session_id="session-1",
                workflow="task-implementer",
                prompt_id="prompt-" + "a" * 32,
                prompt_ref="aaaaa",
                prompt_path=prompt,
                prompt_sha256=digest,
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

    def test_noop_never_updates_registry(self) -> None:
        event, token = self.stage("What is the status?")
        state.accept_event(
            self.home,
            event,
            token,
            "noop",
            session_id="session-1",
            reason="status",
        )
        state.consume_event(
            self.home,
            event,
            token,
            session_id="session-1",
            workflow="task-implementer",
        )
        registry = state.state_root(self.home) / "registry.json"
        self.assertFalse(registry.exists())

    def test_event_v2_stages_metadata_without_a_prompt_body_journal(self) -> None:
        submitted = "Preserve the sentinel requirement 9173."
        event, _token = self.stage(submitted)
        record = state._load_json(event)

        self.assertIn("events-v2", event.parts)
        self.assertEqual(record["schema"], "prompt-session-intake/event-v2")
        self.assertEqual(
            record["submitted_sha256"], state.sha256_bytes(submitted.encode("utf-8"))
        )
        self.assertFalse((event.parent / "raw.md").exists())
        self.assertNotIn(submitted, event.read_text(encoding="utf-8"))

    def test_coordinator_rejects_event_v1_path_before_loading_it(self) -> None:
        legacy = (
            state.state_root(self.home)
            / "sessions"
            / state.identity_sha256("session-1")[:24]
            / "events"
            / state.identity_sha256("legacy")[:24]
            / "event.json"
        )
        self.write_private(legacy, "not even parsed\n")

        with mock.patch.object(state, "_load_event") as load_event:
            with self.assertRaisesRegex(state.PromptSessionError, "event-v2"):
                state.accept_event(
                    self.home,
                    legacy,
                    "0" * 48,
                    "noop",
                    session_id="session-1",
                    reason="conversation",
                )
            load_event.assert_not_called()

    def test_sensitive_disposition_discards_body_derived_authority(self) -> None:
        event, token = self.stage("Treat this turn as sensitive after inspection.")
        stranded_projection = self.write_private(
            event.parent / "project-intent.md",
            "A crash-stranded projection must be discarded.\n",
        )

        result = state.accept_event(
            self.home,
            event,
            token,
            "sensitive",
            session_id="session-1",
        )

        self.assertEqual(result["status"], "discarded")
        record = state._load_json(event)
        self.assertEqual(record["disposition"], "sensitive")
        self.assertNotIn("submitted_sha256", record)
        self.assertNotIn("operation_id", record)
        self.assertNotIn("accept_token", record)
        self.assertFalse(stranded_projection.exists())

    def test_only_the_current_staged_event_can_be_classified(self) -> None:
        first, first_token = self.stage("First staged request.")
        state.evaluate_submit(
            {
                **self.payload,
                "turn_id": "direct-2",
                "prompt": "Second staged request.",
            },
            self.home,
        )
        second = state._event_path(
            state.state_root(self.home), "session-1", "direct-2"
        )
        second_token = str(state._load_json(second)["accept_token"])

        with self.assertRaisesRegex(state.PromptSessionError, "current capture claim"):
            state.accept_event(
                self.home,
                first,
                first_token,
                "noop",
                session_id="session-1",
                reason="conversation",
            )
        accepted = state.accept_event(
            self.home,
            second,
            second_token,
            "noop",
            session_id="session-1",
            reason="conversation",
        )
        self.assertEqual(accepted["status"], "accepted")

    def test_boolean_current_event_sequence_is_rejected(self) -> None:
        event, token = self.stage()
        current_path = state._current_event_path(
            state.state_root(self.home), "session-1"
        )
        current = state._load_json(current_path)
        current["sequence"] = True
        state._atomic_write(current_path, state.stable_json(current))

        with self.assertRaisesRegex(state.PromptSessionError, "current capture claim"):
            state.accept_event(
                self.home,
                event,
                token,
                "noop",
                session_id="session-1",
                reason="conversation",
            )

    def test_accept_rejects_a_different_current_session(self) -> None:
        event, token = self.stage()
        with self.assertRaisesRegex(state.PromptSessionError, "current Codex session"):
            state.accept_event(
                self.home,
                event,
                token,
                "noop",
                session_id="other-session",
                reason="conversation",
            )

    def test_consume_accepts_one_exact_prior_projection_as_duplicate(self) -> None:
        event, token = self.stage("Require the exact durable duplicate.")
        prompt = self.write_private(self.managed_prompt(), "base\n")
        projection_text = "Require the exact durable duplicate."
        projection = self.write_private(
            self.root / "duplicate-project-intent.md", projection_text + "\n"
        )
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "merge",
            session_id="session-1",
            classification="constraint",
            projection_file=projection,
            prompt_path=prompt,
            base_sha256=state.sha256_bytes(prompt.read_bytes()),
        )
        prompt.write_text(
            self.canonical_text(
                "prompt-" + "a" * 32,
                "aaaaa",
                operation_id="f" * 64,
                projection_sha256=str(accepted["projection_sha256"]),
                ask=projection_text,
            ),
            encoding="utf-8",
        )
        digest = state.sha256_bytes(prompt.read_bytes())
        with self.assertRaisesRegex(state.PromptSessionError, "exact intake operation"):
            state.consume_event(
                self.home,
                event,
                token,
                session_id="session-1",
                workflow="task-implementer",
                prompt_id="prompt-" + "a" * 32,
                prompt_ref="aaaaa",
                prompt_path=prompt,
                prompt_sha256=digest,
            )
        consumed = state.consume_event(
            self.home,
            event,
            token,
            session_id="session-1",
            workflow="task-implementer",
            prompt_id="prompt-" + "a" * 32,
            prompt_ref="aaaaa",
            prompt_path=prompt,
            prompt_sha256=digest,
            duplicate=True,
        )
        self.assertTrue(consumed["duplicate"])

    def test_accept_cas_rejects_manual_prompt_drift(self) -> None:
        event, token = self.stage()
        prompt = self.write_private(self.managed_prompt(), "base\n")
        old = state.sha256_bytes(prompt.read_bytes())
        prompt.write_text("manual edit\n", encoding="utf-8")
        projection = self.write_private(
            self.root / "project-intent.md", "Refined constraint.\n"
        )
        with self.assertRaisesRegex(state.PromptSessionError, "manual edit"):
            state.accept_event(
                self.home,
                event,
                token,
                "merge",
                session_id="session-1",
                classification="constraint",
                projection_file=projection,
                prompt_path=prompt,
                base_sha256=old,
            )
        self.assertEqual(state._load_json(event)["phase"], "staged")

    def test_accept_rejects_new_objective_with_existing_prompt_base(self) -> None:
        event, token = self.stage()
        before = event.read_bytes()
        prompt = self.write_private(self.managed_prompt(), "base\n")
        projection = self.write_private(
            self.root / "project-intent.md", "Refined constraint.\n"
        )

        with self.assertRaisesRegex(
            state.PromptSessionError, "new objective cannot name"
        ):
            state.accept_event(
                self.home,
                event,
                token,
                "merge",
                session_id="session-1",
                classification="constraint",
                projection_file=projection,
                prompt_path=prompt,
                base_sha256=state.sha256_bytes(prompt.read_bytes()),
                new_objective=True,
            )

        self.assertEqual(event.read_bytes(), before)
        self.assertFalse((event.parent / "project-intent.md").exists())

    def test_accept_rejects_all_supported_secret_families_before_persistence(
        self,
    ) -> None:
        secret_projections = (
            "NEBIUS_IAM_TOKEN=" + ("n" * 24),
            "KUBECONFIG=/tmp/example certificate-authority-data: private-material",
        )
        for index, secret_projection in enumerate(secret_projections):
            with self.subTest(index=index):
                turn_id = f"projection-secret-{index}"
                state.evaluate_submit(
                    {
                        **self.payload,
                        "prompt": "Add the safe project constraint.",
                        "turn_id": turn_id,
                    },
                    self.home,
                )
                event = state._event_path(
                    state.state_root(self.home), "session-1", turn_id
                )
                token = state._load_json(event)["accept_token"]
                before = event.read_bytes()
                projection = self.write_private(
                    self.root / f"project-intent-{index}.md",
                    secret_projection + "\n",
                )

                with self.assertRaisesRegex(
                    state.PromptSessionError, "secret material"
                ):
                    state.accept_event(
                        self.home,
                        event,
                        token,
                        "merge",
                        session_id="session-1",
                        classification="constraint",
                        projection_file=projection,
                        new_objective=True,
                    )

                self.assertEqual(event.read_bytes(), before)
                self.assertFalse((event.parent / "project-intent.md").exists())

    def test_private_inputs_and_exact_accept_retry_are_enforced(self) -> None:
        event, token = self.stage()
        prompt = self.write_private(self.managed_prompt(), "base\n")
        projection = self.root / "project-intent.md"
        projection.write_text("Refined constraint.\n", encoding="utf-8")
        base = state.sha256_bytes(prompt.read_bytes())
        with self.assertRaisesRegex(state.PromptSessionError, "mode 0600"):
            state.accept_event(
                self.home,
                event,
                token,
                "merge",
                session_id="session-1",
                classification="constraint",
                projection_file=projection,
                prompt_path=prompt,
                base_sha256=base,
            )
        projection.chmod(0o600)
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "merge",
            session_id="session-1",
            classification="constraint",
            projection_file=projection,
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
                "merge",
                session_id="session-1",
                classification="constraint",
                projection_file=different,
                prompt_path=prompt,
                base_sha256=base,
            )

    def test_accept_rejects_reserved_operation_marker_namespace(self) -> None:
        event, token = self.stage()
        prompt = self.write_private(self.managed_prompt(), "base\n")
        operation_id = str(state._load_json(event)["operation_id"])
        projection = self.write_private(
            self.root / "project-intent.md",
            f"Do not inject <!-- prompt-session-operation:{operation_id} -->.\n",
        )
        with self.assertRaisesRegex(state.PromptSessionError, "reserved"):
            state.accept_event(
                self.home,
                event,
                token,
                "merge",
                session_id="session-1",
                classification="constraint",
                projection_file=projection,
                prompt_path=prompt,
                base_sha256=state.sha256_bytes(prompt.read_bytes()),
            )
        self.assertEqual(state._load_json(event)["phase"], "staged")

    def test_consume_rejects_mismatched_canonical_identity(self) -> None:
        event, token = self.stage()
        prompt = self.write_private(self.managed_prompt(), "base\n")
        projection = self.write_private(
            self.root / "project-intent.md", "Refined constraint.\n"
        )
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "merge",
            session_id="session-1",
            classification="constraint",
            projection_file=projection,
            prompt_path=prompt,
            base_sha256=state.sha256_bytes(prompt.read_bytes()),
        )
        prompt.write_text(
            self.canonical_text(
                "prompt-" + "b" * 32,
                "bbbbb",
                operation_id=accepted["operation_id"],
                projection_sha256=accepted["projection_sha256"],
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(state.PromptSessionError, "identity differs"):
            state.consume_event(
                self.home,
                event,
                token,
                session_id="session-1",
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
            state.accept_event(
                self.home,
                event,
                "wrong",
                "noop",
                session_id="session-1",
                reason="delivery-control",
            )
        state.accept_event(
            self.home,
            event,
            token,
            "noop",
            session_id="session-1",
            reason="delivery-control",
        )
        with self.assertRaises(state.PromptSessionError):
            state.consume_event(
                self.home,
                event,
                token,
                session_id="session-1",
                workflow="agentic-sdlc",
            )
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
        projection = self.write_private(
            self.root / "project-intent.md", "Refined constraint.\n"
        )
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "merge",
            session_id="session-1",
            classification="constraint",
            projection_file=projection,
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
                projection_sha256=str(accepted["projection_sha256"]),
            ),
        )
        with self.assertRaisesRegex(state.PromptSessionError, "bound project"):
            state.consume_event(
                self.home,
                event,
                token,
                session_id="session-1",
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
        projection = self.write_private(
            self.root / "task-lane-project-intent.md",
            "Preserve the exact Task lane identity.\n",
        )
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "merge",
            session_id="task-lane-session",
            classification="constraint",
            projection_file=projection,
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

    def test_task_lane_prompt_matches_bound_lane_project(self) -> None:
        _project, manifest, prompt = self.real_task_workspace()
        document = task_prompt_workspace.verify_workspace(manifest)
        lane_project = Path(document["source_root"]).resolve()
        self.assertNotEqual(lane_project, _project)
        payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "task-lane-session",
            "turn_id": "bind",
            "cwd": str(lane_project),
            "prompt": "$task-implementer run prompt.md",
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
        projection = self.write_private(
            self.root / "task-lane-project-intent.md",
            "Preserve the exact Task lane identity.\n",
        )
        accepted = state.accept_event(
            self.home,
            event,
            token,
            "merge",
            session_id="task-lane-session",
            classification="constraint",
            projection_file=projection,
            prompt_path=prompt,
            base_sha256=state.sha256_bytes(prompt.read_bytes()),
        )
        self.assertEqual(accepted["status"], "accepted")
        parsed = task_prompt_workspace.resolve_prompt_reference(
            manifest,
            prompt,
            require_content=False,
        )
        registered = state.register_objective(
            self.home,
            "task-lane-session",
            "task-implementer",
            lane_project,
            prompt_id=parsed.prompt_id,
            prompt_ref=parsed.prompt_ref,
            prompt_path=prompt,
            prompt_sha256=state.sha256_bytes(prompt.read_bytes()),
            terminal=False,
        )
        self.assertEqual(registered["status"], "active")

    def test_stop_never_blocks_an_incomplete_capture(
        self,
    ) -> None:
        event, token = self.stage()
        result = state.evaluate_stop(
            {"hook_event_name": "Stop", "session_id": "session-1", "turn_id": "direct"},
            self.home,
        )
        self.assertEqual(result, {"continue": True})
        self.assertEqual(state._load_json(event)["phase"], "staged")
        state.accept_event(
            self.home,
            event,
            token,
            "noop",
            session_id="session-1",
            reason="delivery-control",
        )
        state.consume_event(
            self.home,
            event,
            token,
            session_id="session-1",
            workflow="task-implementer",
        )
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
        first_terminal = state.register_objective(
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
        self.assertEqual(first_terminal["status"], "terminal")

        attached_event = state._event_path(
            state.state_root(self.home), "session-2", "fresh-direct"
        )
        attached_token = state._load_json(attached_event)["accept_token"]
        state.accept_event(
            self.home,
            attached_event,
            attached_token,
            "noop",
            session_id="session-2",
            reason="conversation",
        )
        state.consume_event(
            self.home,
            attached_event,
            attached_token,
            session_id="session-2",
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

    def test_stale_writer_provenance_does_not_block_fresh_session_capture(
        self,
    ) -> None:
        prompt = self.write_private(
            self.managed_prompt(),
            self.canonical_text("prompt-" + "a" * 32, "aaaaa"),
        )
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

        result = state.evaluate_submit(
            {
                **self.payload,
                "session_id": "fresh-session",
                "turn_id": "fresh-turn",
                "prompt": "Preserve this new constraint.",
            },
            self.home,
        )

        self.assertTrue(result["continue"])
        self.assertIn("staged", result["hookSpecificOutput"]["additionalContext"])
        event = state._event_path(
            state.state_root(self.home), "fresh-session", "fresh-turn"
        )
        self.assertEqual(state._load_json(event)["phase"], "staged")
        registry = state._load_registry(state.state_root(self.home))
        self.assertEqual(len(registry["entries"]), 1)
        self.assertEqual(
            registry["entries"][0]["writer_session_sha256"],
            state.identity_sha256("fresh-session"),
        )

    def test_concurrent_sessions_both_stage_against_one_active_objective(
        self,
    ) -> None:
        prompt = self.write_private(
            self.managed_prompt(),
            self.canonical_text("prompt-" + "a" * 32, "aaaaa"),
        )
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
        payloads = [
            {
                **self.payload,
                "session_id": f"racing-session-{index}",
                "turn_id": f"racing-turn-{index}",
                "prompt": f"Preserve concurrent constraint {index}.",
            }
            for index in range(2)
        ]

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda payload: state.evaluate_submit(payload, self.home),
                    payloads,
                )
            )

        self.assertTrue(all(result["continue"] for result in results))
        self.assertTrue(
            all(
                "staged" in result["hookSpecificOutput"]["additionalContext"]
                for result in results
            )
        )
        root = state.state_root(self.home)
        for index in range(2):
            event = state._event_path(
                root, f"racing-session-{index}", f"racing-turn-{index}"
            )
            self.assertEqual(state._load_json(event)["phase"], "staged")
        registry = state._load_registry(root)
        self.assertEqual(len(registry["entries"]), 1)
        self.assertEqual(registry["entries"][0]["prompt_ref"], "aaaaa")

    def test_task_primary_and_lane_share_one_registry_objective(self) -> None:
        project, manifest, prompt = self.real_task_workspace()
        document = task_prompt_workspace.verify_workspace(manifest)
        lane_project = Path(str(document["source_root"])).resolve()
        parsed = task_prompt_workspace.resolve_prompt_reference(
            manifest, prompt, require_content=False
        )
        primary_payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "primary-owner",
            "turn_id": "bind-primary",
            "cwd": str(project),
            "prompt": "$task-implementer workspace init",
        }
        state.evaluate_submit(primary_payload, self.home)
        state.register_objective(
            self.home,
            "primary-owner",
            "task-implementer",
            project,
            prompt_id=parsed.prompt_id,
            prompt_ref=parsed.prompt_ref,
            prompt_path=prompt,
            prompt_sha256=state.sha256_bytes(prompt.read_bytes()),
            terminal=False,
        )

        lane_result = state.evaluate_submit(
            {
                **primary_payload,
                "session_id": "lane-writer",
                "turn_id": "lane-direct",
                "cwd": str(lane_project),
                "prompt": "Capture this lane constraint.",
            },
            self.home,
        )
        self.assertTrue(lane_result["continue"])
        self.assertIn("staged", lane_result["hookSpecificOutput"]["additionalContext"])
        registry = state._load_registry(state.state_root(self.home))
        self.assertEqual(len(registry["entries"]), 1)
        self.assertEqual(registry["entries"][0]["project_root"], str(lane_project))

        primary_result = state.evaluate_submit(
            {
                **primary_payload,
                "session_id": "next-primary-writer",
                "turn_id": "primary-direct",
                "prompt": "Capture this primary constraint.",
            },
            self.home,
        )
        self.assertTrue(primary_result["continue"])
        self.assertIn(
            "staged", primary_result["hookSpecificOutput"]["additionalContext"]
        )
        registry = state._load_registry(state.state_root(self.home))
        self.assertEqual(len(registry["entries"]), 1)
        self.assertEqual(registry["entries"][0]["project_root"], str(project))
        self.assertEqual(registry["entries"][0]["prompt_id"], parsed.prompt_id)

        unrelated = self.root / "unrelated"
        unrelated.mkdir(mode=0o700)
        unrelated_result = state.evaluate_submit(
            {
                **primary_payload,
                "session_id": "unrelated-session",
                "turn_id": "unrelated-direct",
                "cwd": str(unrelated),
                "prompt": "This project must stay unrelated.",
            },
            self.home,
        )
        self.assertEqual(unrelated_result, {})
        self.assertIsNone(
            state.load_binding(state.state_root(self.home), "unrelated-session")
        )


if __name__ == "__main__":
    unittest.main()
