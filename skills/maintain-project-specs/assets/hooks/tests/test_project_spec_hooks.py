#!/usr/bin/env python3
"""Focused tests for project-spec lifecycle hooks and Stop arbitration."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


HOOK_DIR = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lifecycle = load_module(
    "project_specs_lifecycle", HOOK_DIR / "project_specs_lifecycle.py"
)
arbiter = load_module("stop_lifecycle_arbiter", HOOK_DIR / "stop_lifecycle_arbiter.py")


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class ProjectSpecHooksTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        git(self.project, "init", "-q")
        git(self.project, "config", "user.email", "test@example.com")
        git(self.project, "config", "user.name", "Test User")
        (self.project / "README.md").write_text("# Project\n")
        git(self.project, "add", "README.md")
        git(self.project, "commit", "-qm", "baseline")
        self.previous = os.environ.get("CODEX_HOME")
        os.environ["CODEX_HOME"] = str(self.root / "codex")
        self.user_skills_root = self.root / "home/.agents/skills"
        self.skills_root_patch = mock.patch.object(
            lifecycle,
            "_user_skills_root",
            return_value=self.user_skills_root,
        )
        self.skills_root_patch.start()
        self.base = {
            "cwd": str(self.project),
            "session_id": "session-1",
            "turn_id": "turn-1",
        }

    def tearDown(self) -> None:
        self.skills_root_patch.stop()
        if self.previous is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = self.previous
        self.temp.cleanup()

    def bind_empty_rules(self, state_path: Path, state: dict[str, object]) -> None:
        raw = b""
        rules = state_path.parent / lifecycle.RULES_NAME
        rules.write_bytes(raw)
        rules.chmod(0o600)
        state.update(
            {
                "rules_path": lifecycle.RULES_NAME,
                "rules_sha256": hashlib.sha256(raw).hexdigest(),
            }
        )

    def test_trusted_python_accepts_only_exact_path_canonical_python_family(
        self,
    ) -> None:
        hook_python = self.root / "hook-bin" / "python3.12"
        canonical_python = self.root / "path-bin" / "python3.14"
        alternate_python = self.root / "alternate-bin" / "python3.14"
        for executable in (hook_python, canonical_python, alternate_python):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)

        def which(value: str) -> str | None:
            return str(canonical_python) if value == "python3.14" else None

        with (
            mock.patch.object(lifecycle.sys, "executable", str(hook_python)),
            mock.patch.object(lifecycle.shutil, "which", side_effect=which),
        ):
            self.assertTrue(lifecycle._trusted_python(str(canonical_python)))
            self.assertTrue(lifecycle._trusted_python("python3.14"))
            self.assertFalse(lifecycle._trusted_python(str(alternate_python)))
            self.assertFalse(lifecycle._trusted_python("python2"))

    def test_prompt_blocks_code_until_planned(self) -> None:
        result = lifecycle.evaluate(
            {**self.base, "hook_event_name": "UserPromptSubmit"}
        )
        self.assertIn(
            "Project contract lifecycle",
            result["hookSpecificOutput"]["additionalContext"],
        )
        denied = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": "*** Update File: src/app.py\n",
            }
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_non_git_directory_is_outside_managed_project_scope(self) -> None:
        non_project = self.root / "non-project"
        non_project.mkdir()
        base = {**self.base, "cwd": str(non_project)}
        started = lifecycle.evaluate({**base, "hook_event_name": "UserPromptSubmit"})
        self.assertIn(
            "not active outside a Git worktree",
            started["hookSpecificOutput"]["additionalContext"],
        )
        self.assertEqual(
            lifecycle.evaluate(
                {
                    **base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "git init"},
                }
            ),
            {},
        )
        self.assertEqual(
            lifecycle.evaluate({**base, "hook_event_name": "Stop"}),
            {"continue": True},
        )

    def test_broken_git_marker_does_not_fail_open_as_non_project(self) -> None:
        broken = self.root / "broken-project"
        broken.mkdir()
        (broken / ".git").write_text("gitdir: missing\n", encoding="utf-8")
        with self.assertRaisesRegex(lifecycle.HookError, "Git worktree"):
            lifecycle.evaluate(
                {
                    **self.base,
                    "cwd": str(broken),
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "touch x"},
                }
            )

    def test_second_implementation_edit_remains_allowed(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "reconciliation-required",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        patch = {
            **self.base,
            "tool_name": "apply_patch",
            "tool_input": "*** Update File: src/app.py\n",
        }
        self.assertEqual(
            lifecycle.evaluate({**patch, "hook_event_name": "PreToolUse"}), {}
        )
        lifecycle.evaluate({**patch, "hook_event_name": "PostToolUse"})
        self.assertEqual(
            lifecycle.evaluate({**patch, "hook_event_name": "PreToolUse"}), {}
        )

    def test_successful_project_writes_accumulate_without_context(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "reconciliation-required",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        patch = {
            **self.base,
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "tool_input": "*** Update File: src/app.py\n",
            "tool_response": {"success": True},
        }

        self.assertEqual(lifecycle.evaluate(patch), {})
        self.assertEqual(lifecycle.evaluate(patch), {})

        current = lifecycle._load(state_path)
        assert current is not None
        self.assertEqual(current["phase"], "reconciliation-required")
        self.assertEqual(current["write_epoch"], 2)

    def test_concurrent_project_writes_each_advance_the_epoch(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        payload = {
            **self.base,
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "tool_input": "*** Update File: src/app.py\n",
            "tool_response": {"success": True},
        }
        original_write = lifecycle._write
        first_attempts = threading.Barrier(2)

        def synchronize_first_attempt(path, value):
            if value.get("write_epoch") == 1:
                first_attempts.wait(timeout=5)
            return original_write(path, value)

        with (
            mock.patch.object(lifecycle, "_write", synchronize_first_attempt),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(lifecycle.evaluate, (payload, payload)))

        self.assertEqual(results, [{}, {}])
        current = lifecycle._load(state_path)
        assert current is not None
        self.assertEqual(current["phase"], "reconciliation-required")
        self.assertEqual(current["write_epoch"], 2)

    def test_late_posttool_success_invalidates_later_lifecycle_evidence(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        payload = {
            **self.base,
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "tool_input": "*** Update File: src/app.py\n",
            "tool_response": {"success": True},
        }

        for phase in ("planned", "seal-armed", "sealed"):
            with self.subTest(phase=phase):
                state = lifecycle._load(state_path)
                assert state is not None
                state.update(
                    {
                        "phase": phase,
                        "receipt_sha256": "a" * 64,
                        "requirements_sha256": "b" * 64,
                        "design_sha256": "c" * 64,
                        "project_instructions_state_sha256": (
                            "d" * 64 if phase == "sealed" else None
                        ),
                        "project_instructions_reload_required": (
                            False if phase == "sealed" else None
                        ),
                        "planned_write_epoch": 0,
                        "write_epoch": 0,
                    }
                )
                self.bind_empty_rules(state_path, state)
                lifecycle._write(state_path, state)

                self.assertEqual(lifecycle.evaluate(payload), {})
                current = lifecycle._load(state_path)
                assert current is not None
                self.assertEqual(current["phase"], "reconciliation-required")
                self.assertEqual(current["write_epoch"], 1)

    def test_canonical_spec_reconciliation_is_epoch_neutral(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        payload = {
            **self.base,
            "tool_name": "apply_patch",
            "tool_input": "*** Update File: docs/design.md\n",
            "tool_response": {"success": True},
        }

        for phase in ("planning-required", "reconciliation-required"):
            with self.subTest(phase=phase):
                state = lifecycle._load(state_path)
                assert state is not None
                state.update(
                    {
                        "phase": phase,
                        "planned_write_epoch": None,
                        "write_epoch": 3,
                    }
                )
                lifecycle._write(state_path, state)
                self.assertEqual(
                    lifecycle.evaluate({**payload, "hook_event_name": "PreToolUse"}),
                    {},
                )
                self.assertEqual(
                    lifecycle.evaluate({**payload, "hook_event_name": "PostToolUse"}),
                    {},
                )
                current = lifecycle._load(state_path)
                assert current is not None
                self.assertEqual(current["phase"], phase)
                self.assertEqual(current["write_epoch"], 3)

    def test_posttool_registration_is_silent(self) -> None:
        manifest_path = HOOK_DIR.parent / "hooks.json.template"
        if not manifest_path.is_file():
            manifest_path = HOOK_DIR.parent / "hooks.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        registrations = [
            registration
            for registration in manifest["hooks"]["PostToolUse"]
            if any(
                "project_specs_lifecycle.py" in str(handler.get("command", ""))
                for handler in registration.get("hooks", [])
            )
        ]
        self.assertEqual(len(registrations), 1)
        handlers = registrations[0]["hooks"]
        self.assertEqual(len(handlers), 1)
        self.assertNotIn("statusMessage", handlers[0])

    def test_posttool_recording_failure_remains_visible(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        payload = {
            **self.base,
            "hook_event_name": "PostToolUse",
            "tool_name": "apply_patch",
            "tool_input": "*** Update File: src/app.py\n",
            "tool_response": {"success": True},
        }
        output = io.StringIO()

        with (
            mock.patch.object(lifecycle.sys, "stdin", io.StringIO(json.dumps(payload))),
            mock.patch.object(
                lifecycle,
                "_write",
                side_effect=lifecycle.HookError("simulated recording failure"),
            ),
            redirect_stdout(output),
        ):
            self.assertEqual(lifecycle.main(), 0)

        result = json.loads(output.getvalue())
        context = result["hookSpecificOutput"]["additionalContext"]
        self.assertIn("impact recording failed", context)
        self.assertIn("Completion will fail closed", context)

    def test_stop_requires_one_reconciliation_then_terminates(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state["phase"] = "reconciliation-required"
        lifecycle._write(state_path, state)
        first = lifecycle.evaluate_stop({**self.base, "hook_event_name": "Stop"})
        self.assertEqual(first["decision"], "block")
        self.assertIn("accumulated implementation delta", first["reason"])
        self.assertIn("leave both documents unchanged", first["reason"])
        self.assertIn("project-instructions decision", first["reason"])
        self.assertIn(
            "not-needed leaves a missing project AGENTS.md absent", first["reason"]
        )
        second = lifecycle.evaluate_stop(
            {**self.base, "hook_event_name": "Stop", "stop_hook_active": True}
        )
        self.assertFalse(second["continue"])

    def test_stop_opens_reconciliation_before_synthetic_continuation(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "requirements_sha256": "b" * 64,
                "design_sha256": "c" * 64,
                "planned_write_epoch": 1,
                "write_epoch": 1,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)

        first = lifecycle.evaluate_stop({**self.base, "hook_event_name": "Stop"})

        self.assertEqual(first["decision"], "block")
        current = lifecycle._load(state_path)
        assert current is not None
        self.assertEqual(current["phase"], "reconciliation-required")
        self.assertEqual(current["write_epoch"], 1)
        for field in (
            "receipt_sha256",
            "requirements_sha256",
            "design_sha256",
            "rules_path",
            "rules_sha256",
            "planned_write_epoch",
        ):
            self.assertIsNone(current[field])

        spec_patch = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": "*** Update File: docs/design.md\n",
            }
        )
        self.assertEqual(spec_patch, {})

        recursive = lifecycle.evaluate_stop(
            {**self.base, "hook_event_name": "Stop", "stop_hook_active": True}
        )
        self.assertFalse(recursive["continue"])

    def test_stop_reconciliation_retries_a_concurrent_material_write(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "requirements_sha256": "b" * 64,
                "design_sha256": "c" * 64,
                "planned_write_epoch": 1,
                "write_epoch": 1,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        original_write = lifecycle._write
        raced = False

        def concurrent_write(path, value):
            nonlocal raced
            if not raced:
                raced = True
                current = lifecycle._load(path)
                assert current is not None
                current["phase"] = "reconciliation-required"
                current["write_epoch"] = int(current["write_epoch"]) + 1
                original_write(path, current)
                raise lifecycle.HookError(
                    "project spec lifecycle changed before transition"
                )
            return original_write(path, value)

        with mock.patch.object(lifecycle, "_write", side_effect=concurrent_write):
            result = lifecycle.evaluate_stop({**self.base, "hook_event_name": "Stop"})

        self.assertEqual(result["decision"], "block")
        current = lifecycle._load(state_path)
        assert current is not None
        self.assertEqual(current["phase"], "reconciliation-required")
        self.assertEqual(current["write_epoch"], 2)
        for field in (
            "receipt_sha256",
            "requirements_sha256",
            "design_sha256",
            "rules_path",
            "rules_sha256",
            "planned_write_epoch",
        ):
            self.assertIsNone(current[field])

    def test_arbiter_aggregates_every_initial_continuation(self) -> None:
        delegates = self.root / "delegates"
        delegates.mkdir()
        for name, output in (
            ("remediation_attempt_guard.py", {"continue": True}),
            ("project_specs_lifecycle.py", {"decision": "block", "reason": "spec"}),
            ("stop_sdlc_continue.py", {"decision": "block", "reason": "sdlc"}),
            (
                "stop_prompt_session_intake.py",
                {"decision": "block", "reason": "prompt-intake"},
            ),
        ):
            path = delegates / name
            path.write_text(
                "import json\nprint(json.dumps(" + repr(output) + "))\n",
                encoding="utf-8",
            )
        result = arbiter.evaluate({"hook_event_name": "Stop"}, delegates)
        self.assertEqual(result["decision"], "block")
        self.assertIn("project_specs_lifecycle.py: spec", result["reason"])
        self.assertIn("stop_sdlc_continue.py: sdlc", result["reason"])
        self.assertIn("stop_prompt_session_intake.py: prompt-intake", result["reason"])

    def test_arbiter_terminal_result_overrides_an_earlier_block(self) -> None:
        delegates = self.root / "delegates"
        delegates.mkdir()
        for name, output in (
            ("remediation_attempt_guard.py", {"decision": "block", "reason": "fix"}),
            (
                "project_specs_lifecycle.py",
                {"continue": False, "stopReason": "terminal"},
            ),
            ("stop_sdlc_continue.py", {"continue": True}),
            ("stop_prompt_session_intake.py", {"continue": True}),
        ):
            path = delegates / name
            path.write_text(
                "import json\nprint(json.dumps(" + repr(output) + "))\n",
                encoding="utf-8",
            )
        result = arbiter.evaluate({"hook_event_name": "Stop"}, delegates)
        self.assertEqual(result, {"continue": False, "stopReason": "terminal"})

    def test_arbiter_bounds_every_delegate_by_one_shared_deadline(self) -> None:
        delegates = self.root / "delegates"
        delegates.mkdir()
        for name in arbiter.DELEGATES:
            (delegates / name).write_text("# test delegate\n", encoding="utf-8")
        deadlines: list[float] = []

        def run_delegate(
            _path: Path,
            _payload: dict[str, object],
            deadline: float,
        ) -> dict[str, object]:
            deadlines.append(deadline)
            return {"continue": True}

        with mock.patch.object(arbiter.time, "monotonic", return_value=100.0):
            with mock.patch.object(
                arbiter,
                "_run_delegate",
                side_effect=run_delegate,
            ):
                result = arbiter.evaluate({"hook_event_name": "Stop"}, delegates)
        self.assertEqual(result, {"continue": True})
        self.assertEqual(
            deadlines,
            [100.0 + arbiter.ARBITER_BUDGET_SECONDS] * len(arbiter.DELEGATES),
        )

    def test_delegate_timeout_never_exceeds_remaining_arbiter_budget(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"continue": true}\n',
            stderr="",
        )
        with mock.patch.object(arbiter.time, "monotonic", return_value=100.0):
            with mock.patch.object(
                arbiter.subprocess,
                "run",
                return_value=completed,
            ) as invoked:
                self.assertEqual(
                    arbiter._run_delegate(
                        self.root / "delegate.py",
                        {},
                        deadline=103.5,
                    ),
                    {"continue": True},
                )
        self.assertEqual(invoked.call_args.kwargs["timeout"], 3.5)

    def test_untracked_policy_cannot_disable_hook(self) -> None:
        policy = self.project / ".codex/project-specs.json"
        policy.parent.mkdir()
        policy.write_text(
            '{"schema":"maintain-project-specs.project.v1",'
            '"mode":"disabled","scope":"."}\n',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(lifecycle.HookError, "tracked in Git"):
            lifecycle._disabled(self.project)
        git(self.project, "add", ".codex/project-specs.json")
        with self.assertRaisesRegex(lifecycle.HookError, "committed Git blob"):
            lifecycle._disabled(self.project)

    def test_substring_and_scope_change_do_not_bypass_planning(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        for tool_input in (
            {"command": "touch x # project_specs.py"},
            {
                "command": f"touch {self.project / 'x'}",
                "workdir": str(self.root),
            },
            {"command": "cd src && touch x"},
        ):
            denied = lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": tool_input,
                }
            )
            self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_planned_phase_rejects_direct_agents_write(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "planned",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        denied = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": "*** Update File: AGENTS.md\n",
            }
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_exact_installed_coordinator_is_recognized(self) -> None:
        helper = (
            self.user_skills_root / "maintain-project-specs/scripts/project_specs.py"
        )
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        command = f"python3 {helper} validate --project-root {self.project}"
        self.assertEqual(
            lifecycle._bound_coordinator_command(command, self.project),
            ("project-specs", "validate"),
        )
        malicious_python = self.root / "python3"
        malicious_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        malicious_python.chmod(0o755)
        self.assertIsNone(
            lifecycle._bound_coordinator_command(
                f"{malicious_python} {helper} validate --project-root {self.project}",
                self.project,
            )
        )
        linked = self.user_skills_root / "project-agent-instructions"
        outside = self.root / "outside-project-agent-instructions"
        (outside / "scripts").mkdir(parents=True)
        outside_helper = outside / "scripts/project_agent_instructions.py"
        outside_helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        linked.symlink_to(outside, target_is_directory=True)
        self.assertIsNone(
            lifecycle._bound_coordinator_command(
                f"python3 {linked / 'scripts/project_agent_instructions.py'} "
                f"inspect --project-root {self.project}",
                self.project,
            )
        )

    def test_copied_hook_trusts_only_installed_coordinator_surface(self) -> None:
        installed_hook = Path(os.environ["CODEX_HOME"]) / "hooks/lifecycle.py"
        installed_hook.parent.mkdir(parents=True)
        shutil.copyfile(HOOK_DIR / "project_specs_lifecycle.py", installed_hook)
        installed = load_module("installed_project_specs_lifecycle", installed_hook)
        installed_skills = self.root / "installed-home/.agents/skills"
        helper = installed_skills / "maintain-project-specs/scripts/project_specs.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        command = f"python3 {helper} validate --project-root {self.project}"
        with mock.patch.object(
            installed, "_user_skills_root", return_value=installed_skills
        ):
            self.assertEqual(
                installed._bound_coordinator_command(command, self.project),
                ("project-specs", "validate"),
            )
        source_helper = (
            self.root / "checkout/maintain-project-specs/scripts/project_specs.py"
        )
        source_helper.parent.mkdir(parents=True)
        source_helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        source_command = (
            f"python3 {source_helper} validate --project-root {self.project}"
        )
        self.assertIsNone(
            installed._bound_coordinator_command(source_command, self.project)
        )
        self.assertTrue(installed._coordinator_command_shape(source_command))

    def test_read_commands_and_compositions_remain_available(self) -> None:
        for command in (
            "cat README.md",
            "find . -name '*.md' -print",
            "grep -n Project README.md",
            "nl -ba README.md",
            "rg pattern README.md",
            "git status --short",
            "git diff --stat HEAD",
            "git log -1",
            "git branch --show-current",
            r"find . -type f -print -exec stat -f '%Sp %N' {} \;",
            "rg pattern README.md | head -n 5",
            "git status --short && git diff --stat HEAD",
            r"rg Project README.md && find . -type f -exec stat -f '%Sp %N' {} \;",
            "rg -n 'quoted|install|mutation' README.md",
            "rg -n 'literal > text' README.md",
            "rg -n 'literal $() text' README.md",
            "rg -n 'literal ` text' README.md",
        ):
            self.assertFalse(
                lifecycle._potential_write("Bash", {"command": command}), command
            )

        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        self.assertEqual(
            lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {
                        "command": (
                            r"rg Project README.md && find . -type f -print "
                            r"-exec stat -f '%Sp %N' {} \;"
                        )
                    },
                }
            ),
            {},
        )

    def test_planning_allows_only_canonical_intent_to_add(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        for command in (
            f"git -C {self.project} add -N -- docs/requirements.md",
            f"git -C {self.project} add --intent-to-add -- "
            "docs/requirements.md docs/design.md",
        ):
            with self.subTest(command=command):
                self.assertEqual(
                    lifecycle.evaluate(
                        {
                            **self.base,
                            "hook_event_name": "PreToolUse",
                            "tool_name": "Bash",
                            "tool_input": {"command": command},
                        }
                    ),
                    {},
                )

        for command in (
            f"git -C {self.project} add -- docs/requirements.md",
            f"git -C {self.project} add -N docs/requirements.md",
            f"git -C {self.project} add -N -- README.md",
            f"git -C {self.project} add -N -- docs/requirements.md README.md",
            f"{self.root / 'git'} -C {self.project} add -N -- docs/requirements.md",
            f"git -C {self.project} add -N -- docs/requirements.md && touch changed",
        ):
            with self.subTest(command=command):
                denied = lifecycle.evaluate(
                    {
                        **self.base,
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_explicit_shell_writers_remain_material(self) -> None:
        for command in (
            "git diff --output docs/design.md HEAD~1 HEAD",
            "git diff --output=docs/design.md HEAD~1 HEAD",
            "git diff --ext-diff HEAD~1 HEAD",
            "git show --textconv HEAD:README.md",
            "git cat-file --filters HEAD:README.md",
            "rg --pre 'touch escaped' pattern .",
            "rg --hostname-bin='touch escaped' pattern .",
            "rg --search-zip pattern archive.gz",
            "rg -z pattern archive.gz",
            "rg --no-config -Sz pattern archive.gz",
            "cat README.md > docs/design.md",
            "cat README.md | tee docs/design.md",
            "git status --short && touch changed",
            "git branch lifecycle-repair",
            "find . -delete",
            r"find . -exec touch {} \;",
            r"find . -exec ./stat {} \;",
            r"find . -exec /tmp/stat {} \;",
            r"find . -exec env PATH=. stat {} \;",
            r"find . -execdir stat {} \;",
            "printf x >> docs/design.md",
            "sort -o docs/design.md README.md",
            "bash mutate.sh",
            "awk 'BEGIN { system(\"touch x\") }'",
        ):
            self.assertTrue(
                lifecycle._potential_write("Bash", {"command": command}), command
            )

    def test_external_codex_home_writes_pass_through_and_stay_epoch_neutral(
        self,
    ) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        current = lifecycle._load(state_path)
        assert current is not None
        before = dict(current)
        workspace_name = lifecycle._task_state_workspace(git_root)
        task_state = (
            Path(os.environ["CODEX_HOME"])
            / "task-state"
            / workspace_name
            / "session-1/current.md"
        )
        runtime = state_path.parent / "runtime-config.json"
        decision = state_path.parent / "project-instructions/decision.json"
        external_targets = (
            Path(os.environ["CODEX_HOME"]) / "config.toml",
            Path(os.environ["CODEX_HOME"]) / "hooks.json",
            Path(os.environ["CODEX_HOME"]) / "hooks/custom_guard.py",
            Path(os.environ["CODEX_HOME"])
            / "task-state"
            / workspace_name
            / "session-2/current.md",
            self.user_skills_root / "maintain-project-specs/SKILL.md",
            self.user_skills_root / "project-agent-instructions/SKILL.md",
            self.root / "ordinary-user-file.txt",
        )
        for target in (task_state, *external_targets, runtime, decision):
            payload = {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": f"*** Add File: {target}\n",
            }
            self.assertEqual(lifecycle.evaluate(payload), {}, str(target))
            lifecycle.evaluate({**payload, "hook_event_name": "PostToolUse"})
        after = lifecycle._load(state_path)
        assert after is not None
        self.assertEqual(after["phase"], before["phase"])
        self.assertEqual(after["write_epoch"], before["write_epoch"])

        for target in (task_state.with_name("other.md"),):
            self.assertEqual(
                lifecycle.evaluate(
                    {
                        **self.base,
                        "hook_event_name": "PreToolUse",
                        "tool_name": "apply_patch",
                        "tool_input": f"*** Add File: {target}\n",
                    }
                ),
                {},
            )

        for target in (
            state_path,
            state_path.parent / "spec-receipt.json",
            state_path.parent / "project-instructions/manifest.json",
        ):
            denied = lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "apply_patch",
                    "tool_input": f"*** Add File: {target}\n",
                }
            )
            self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_private_mode_tightening_is_exact_and_epoch_neutral(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        runtime = state_path.parent / "runtime-config.json"
        decision = state_path.parent / "project-instructions/decision.json"
        decision.parent.mkdir(mode=0o700)
        for target in (runtime, decision):
            target.write_text("{}\n", encoding="utf-8")
            target.chmod(0o644)
        before = lifecycle._load(state_path)
        assert before is not None

        for mode, target in (("600", runtime), ("0600", decision)):
            payload = {
                **self.base,
                "tool_name": "Bash",
                "tool_input": {"command": f"chmod {mode} {target}"},
            }
            self.assertEqual(
                lifecycle.evaluate({**payload, "hook_event_name": "PreToolUse"}),
                {},
            )
            lifecycle.evaluate(
                {
                    **payload,
                    "hook_event_name": "PostToolUse",
                    "tool_response": {"exit_code": 0},
                }
            )

        after = lifecycle._load(state_path)
        assert after is not None
        self.assertEqual(after["phase"], before["phase"])
        self.assertEqual(after["write_epoch"], before["write_epoch"])

        other_session = state_path.parent.parent / "session-2/runtime-config.json"
        task_state = (
            Path(os.environ["CODEX_HOME"])
            / "task-state"
            / lifecycle._task_state_workspace(git_root)
            / "session-1/current.md"
        )
        task_state.parent.mkdir(parents=True)
        task_state.write_text("# State\n", encoding="utf-8")
        self.assertEqual(
            lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": f"chmod 600 {task_state}"},
                }
            ),
            {},
        )
        for command in (
            f"chmod 644 {runtime}",
            f"chmod -R 600 {runtime}",
            f"chmod 600 {runtime} {decision}",
            f"{self.root / 'chmod'} 600 {runtime}",
            f"chmod 600 {state_path}",
            f"chmod 600 {other_session}",
            f"chmod 600 {runtime} && touch changed",
        ):
            with self.subTest(command=command):
                denied = lifecycle.evaluate(
                    {
                        **self.base,
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )

        project_file = self.project / "project-mode-source"
        project_file.write_text("project\n", encoding="utf-8")
        authoritative = state_path.parent / "spec-receipt.json"
        authoritative.write_text("{}\n", encoding="utf-8")
        authoritative.chmod(0o600)
        runtime.unlink()
        os.link(project_file, runtime)
        decision.unlink()
        os.link(authoritative, decision)
        for target in (runtime, decision):
            with self.subTest(hard_link_target=target):
                denied = lifecycle.evaluate(
                    {
                        **self.base,
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": f"chmod 600 {target}"},
                    }
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_explicit_external_mutations_are_lifecycle_neutral(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        external = self.root / "external"
        external.mkdir()
        payloads = (
            (
                "Bash",
                {"command": f"touch {external / 'artifact'}"},
            ),
            (
                "Bash",
                {"command": "touch artifact", "workdir": str(external)},
            ),
            (
                "mcp__server__delete_file",
                {"path": str(external / "artifact")},
            ),
        )
        for tool_name, tool_input in payloads:
            with self.subTest(tool_name=tool_name, tool_input=tool_input):
                self.assertEqual(
                    lifecycle.evaluate(
                        {
                            **self.base,
                            "hook_event_name": "PreToolUse",
                            "tool_name": tool_name,
                            "tool_input": tool_input,
                        }
                    ),
                    {},
                )

        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "sealed",
                "receipt_sha256": "a" * 64,
                "requirements_sha256": "b" * 64,
                "design_sha256": "c" * 64,
                "project_instructions_state_sha256": "d" * 64,
                "project_instructions_reload_required": False,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        self.assertEqual(
            lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": f"touch {external / 'terminal'}"},
                }
            ),
            {},
        )

    def test_exact_external_find_delete_cleanup_is_scoped_and_epoch_neutral(
        self,
    ) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        external = self.root / "task-owned-cleanup"
        external.mkdir()
        command = f"find {external} -depth -delete"
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        before = lifecycle._load(state_path)
        assert before is not None

        payload = {
            **self.base,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        self.assertEqual(
            lifecycle.evaluate({**payload, "hook_event_name": "PreToolUse"}), {}
        )
        lifecycle.evaluate(
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_response": {"exit_code": 0},
            }
        )
        after = lifecycle._load(state_path)
        assert after is not None
        self.assertEqual(after["phase"], before["phase"])
        self.assertEqual(after["write_epoch"], before["write_epoch"])

        external_link = self.root / "external-project-link"
        external_link.symlink_to(self.project, target_is_directory=True)
        external_target = self.root / "external-target"
        external_target.mkdir()
        external_only_link = self.root / "external-only-link"
        external_only_link.symlink_to(external_target, target_is_directory=True)
        for denied_command in (
            f"find {self.project} -depth -delete",
            f"find {external_link} -depth -delete",
            f"find {external_only_link} -depth -delete",
            f"find {external} -delete",
            f"find -L {external} -depth -delete",
            rf"find {external} -depth -exec rm -r {{}} \;",
            f"find {external} {self.root / 'second'} -depth -delete",
            f"find {external}/* -depth -delete",
            f"find {external}/{{one,two}} -depth -delete",
            f"find {self.project} -depth -{{delete,print}}",
            f"find {self.project} -depth -{{d{{elete,ummy}},print}}",
            f"find {self.project} -depth -{{de,xx}}{{lete,yy}}",
            f"find {self.project} -depth {{-,x}}delete",
            f"find {self.project} -depth -del*",
            f"env find {external} -depth -delete",
            f"(find {external} -depth -delete)",
            f"! find {external} -depth -delete",
            f"! command find {external} -depth -delete",
            f"time find {external} -depth -delete",
            f"time -f format find {external} -depth -delete",
            f"if true; then find {external} -depth -delete; fi",
            f"{{ find {external} -depth -delete; }}",
            f"find {external} -depth -delete && true",
            f"find {external} -depth -delete | head -n 1",
            "find $TARGET -depth -delete",
            "find $(pwd) -depth -delete",
            f"find {state_path.parent} -depth -delete",
            f"find {Path(tempfile.gettempdir()).resolve()} -depth -delete",
            "find /tmp -depth -delete",
            "find /private/tmp -depth -delete",
            "find / -depth -delete",
        ):
            with self.subTest(command=denied_command):
                denied = lifecycle.evaluate(
                    {
                        **self.base,
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": denied_command},
                    }
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_external_post_tool_does_not_advance_project_epoch(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        external = self.root / "external"
        external.mkdir()
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        payload = {
            **self.base,
            "tool_name": "Bash",
            "tool_input": {"command": f"touch {external / 'artifact'}"},
        }
        self.assertEqual(
            lifecycle.evaluate({**payload, "hook_event_name": "PreToolUse"}), {}
        )
        lifecycle.evaluate(
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_response": {"exit_code": 0},
            }
        )
        after = lifecycle._load(state_path)
        assert after is not None
        self.assertEqual(after["phase"], "implementation-open")
        self.assertEqual(after["write_epoch"], 0)

        mixed_command = f"cp -vt {self.project} {external / 'source'}"
        lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": mixed_command},
                "tool_response": {"exit_code": 0},
            }
        )
        after_mixed = lifecycle._load(state_path)
        assert after_mixed is not None
        self.assertEqual(after_mixed["phase"], "reconciliation-required")
        self.assertEqual(after_mixed["write_epoch"], 1)

    def test_mixed_ambiguous_and_control_plane_mutations_stay_denied(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        external = self.root / "external"
        external.mkdir()
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        external_link = external / "project-link"
        external_link.symlink_to(self.project, target_is_directory=True)
        project_link = self.project / "external-link"
        project_link.symlink_to(external, target_is_directory=True)
        project_source = self.project / "source"
        project_source.write_text("source\n", encoding="utf-8")
        for command in (
            f"touch {external / 'artifact'} {self.project / 'artifact'}",
            f"touch {external_link / 'artifact'}",
            f"touch {project_link / 'artifact'}",
            f"ln {project_source} {external / 'hard-link'}",
            f"install -d {self.project / 'one'} {external / 'two'}",
            f"install -d {external / 'one'} {self.project / 'two'}",
            f"cp -t {self.project} {external / 'source'}",
            f"cp -vt {self.project} {external / 'source'}",
            f"cp -vt{self.project} {external / 'source'}",
            f"cp --target {self.project} {external / 'source'}",
            f"cp --target-directory {self.project} {external / 'source'}",
            f"install -t {self.project} {external / 'source'}",
            f"install --target-directory {self.project} {external / 'source'}",
            "touch ../project/artifact",
            "touch $TARGET",
            "bash mutate.sh",
            f"touch {state_path}",
            f"mv {Path(os.environ['CODEX_HOME'])} {external / 'codex-home'}",
        ):
            with self.subTest(command=command):
                denied = lifecycle.evaluate(
                    {
                        **self.base,
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {
                            "command": command,
                            "workdir": str(external)
                            if command == "touch ../project/artifact"
                            else str(self.project),
                        },
                    }
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_start_prompt_requires_current_hook_identity(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        helper = (
            self.user_skills_root / "maintain-project-specs/scripts/project_specs.py"
        )
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        command = (
            f"python3 {helper} start-prompt --project-root {self.project} "
            "--session-id session-1 --turn-id turn-1"
        )
        allowed = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
        )
        self.assertEqual(allowed, {})
        denied = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command.replace("turn-1", "other")},
            }
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_receipt_output_is_bound_to_current_hook_session(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        helper = (
            self.user_skills_root / "maintain-project-specs/scripts/project_specs.py"
        )
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        command = (
            f"python3 {helper} validate --project-root {self.project} "
            f"--output {state_path.parent / 'spec-receipt.json'} "
            "--session-id session-1"
        )
        allowed = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
        )
        self.assertEqual(allowed, {})
        denied = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command.replace("session-1", "session-2")},
            }
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_planning_coordinators_require_current_session_bundle(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        session_root = state_path.parent
        private_root = session_root / "project-instructions"
        project_helper = (
            self.user_skills_root / "maintain-project-specs/scripts/project_specs.py"
        )
        project_helper.parent.mkdir(parents=True)
        project_helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        plan_command = (
            f"python3 {project_helper} plan --project-root {self.project} "
            "--session-id session-1 "
            f"--turn-token {state['turn_sha256']} "
            f"--rules-file {private_root / 'rules.md'} "
            f"--render-state-file {private_root / 'render-state.json'} "
            f"--project-instructions-private-root {private_root}"
        )
        payload = {
            **self.base,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
        }
        self.assertEqual(
            lifecycle.evaluate({**payload, "tool_input": {"command": plan_command}}),
            {},
        )
        alternate = self.root / "alternate-bundle"
        denied = lifecycle.evaluate(
            {
                **payload,
                "tool_input": {
                    "command": plan_command.replace(str(private_root), str(alternate))
                },
            }
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

        instruction_helper = (
            self.user_skills_root
            / "project-agent-instructions/scripts/project_agent_instructions.py"
        )
        instruction_helper.parent.mkdir(parents=True)
        instruction_helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        inspect_command = (
            f"python3 {instruction_helper} inspect --project-root {self.project} "
            "--spec-owner maintain-project-specs "
            "--requirements docs/requirements.md --design docs/design.md "
            f"--spec-receipt {session_root / 'spec-receipt.json'} "
            f"--runtime-config {session_root / 'runtime-config.json'} "
            f"--codex-home {Path(os.environ['CODEX_HOME'])} "
            f"--private-root {private_root} "
            f"--output {private_root / 'manifest.json'}"
        )
        self.assertEqual(
            lifecycle.evaluate({**payload, "tool_input": {"command": inspect_command}}),
            {},
        )
        for malformed_command in (
            inspect_command.replace("docs/design.md", "docs/alternate.md"),
            inspect_command.replace(
                f" --codex-home {Path(os.environ['CODEX_HOME'])}", ""
            ),
            inspect_command.replace(
                str(private_root / "manifest.json"), "manifest.json"
            ),
        ):
            with self.subTest(malformed_command=malformed_command):
                denied = lifecycle.evaluate(
                    {
                        **payload,
                        "tool_input": {"command": malformed_command},
                    }
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecisionReason"],
                    lifecycle.COORDINATOR_BINDING_REASON,
                )

    def test_task_implementer_run_bundle_uses_attested_adapter_boundary(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "reconciliation-required",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        helper = (
            self.user_skills_root
            / "project-agent-instructions/scripts/project_agent_instructions.py"
        )
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        private_root = (
            Path(os.environ["CODEX_HOME"])
            / "task-implementer/projects/project/scopes/scope/runs"
            / "run-20260811t135023z-caac975f/orchestration"
            / "project-agent-instructions"
        )
        command = (
            f"python3 {helper} inspect --project-root {self.root / 'integration'} "
            "--spec-owner maintain-project-specs "
            "--requirements docs/requirements.md --design docs/design.md "
            f"--spec-receipt {private_root.parent / 'project-agent-spec-receipt.json'} "
            f"--runtime-config {private_root.parent / 'project-agent-runtime.json'} "
            f"--codex-home {Path(os.environ['CODEX_HOME'])} "
            f"--private-root {private_root} "
            f"--output {private_root / 'manifest.json'}"
        )
        adapter = {
            "status": "authorized",
            "action": "inspect",
            "outer_project_root": str(self.project),
            "project_root": str(self.root / "integration"),
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        }
        payload = {
            **self.base,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        with mock.patch.object(
            lifecycle, "_task_implementer_coordinator", return_value=adapter
        ):
            for phase in ("reconciliation-required", "implementation-open"):
                with self.subTest(phase=phase):
                    state = lifecycle._load(state_path)
                    assert state is not None
                    state["phase"] = phase
                    lifecycle._write(state_path, state)
                    self.assertEqual(lifecycle.evaluate(payload), {})
            denied = lifecycle.evaluate(
                {
                    **payload,
                    "tool_input": {"command": command + " --unexpected value"},
                }
            )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

        validate_command = (
            f"python3 {self.user_skills_root / 'maintain-project-specs/scripts/project_specs.py'} "
            f"validate --project-root {self.root / 'integration'} "
            f"--output {private_root.parent / 'project-agent-spec-receipt.json'} "
            f"--session-id {self.base['session_id']} "
            f"--task-implementer-workspace {private_root.parents[3] / 'workspace.json'} "
            f"--task-implementer-run-id run-20260811t135023z-caac975f"
        )
        validate_adapter = {
            **adapter,
            "action": "validate",
            "command_sha256": hashlib.sha256(validate_command.encode()).hexdigest(),
        }
        state = lifecycle._load(state_path)
        assert state is not None
        state["phase"] = "implementation-open"
        lifecycle._write(state_path, state)
        validate_payload = {
            **payload,
            "tool_input": {"command": validate_command},
        }
        with mock.patch.object(
            lifecycle,
            "_task_implementer_coordinator",
            return_value=validate_adapter,
        ):
            self.assertEqual(lifecycle.evaluate(validate_payload), {})
            wrong_session = validate_command.replace(
                self.base["session_id"], "019ff65c-3e02-7780-8f24-448c391b5f66"
            )
            denied = lifecycle.evaluate(
                {**payload, "tool_input": {"command": wrong_session}}
            )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_task_implementer_run_bundle_rejects_terminal_actions(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "reconciliation-required",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        helper = (
            self.user_skills_root
            / "project-agent-instructions/scripts/project_agent_instructions.py"
        )
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        private_root = (
            Path(os.environ["CODEX_HOME"])
            / "task-implementer/projects/project/scopes/scope/runs"
            / "run-20260811t135023z-caac975f/orchestration"
            / "project-agent-instructions"
        )
        for action in ("apply", "verify"):
            command = f"python3 {helper} {action} --private-root {private_root}"
            adapter = {
                "status": "authorized",
                "action": action,
                "outer_project_root": str(self.project),
                "project_root": str(self.root / "integration"),
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            }
            with (
                self.subTest(action=action),
                mock.patch.object(
                    lifecycle, "_task_implementer_coordinator", return_value=adapter
                ),
            ):
                denied = lifecycle.evaluate(
                    {
                        **self.base,
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Bash",
                        "tool_input": {"command": command},
                    }
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_task_implementer_wave_plan_records_hidden_checkpoint_write(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        command = "python3 /trusted/prompt_workspace.py wave-plan --workspace /private/workspace.json --run-id run-20260811t135023z-caac975f --capacity 2 --json"
        impact = {
            "status": "authorized",
            "action": "wave-plan",
            "outer_project_root": str(self.project),
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "checkpoint_head": "b" * 40,
            "review_correction": False,
        }
        payload = {
            **self.base,
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": 0},
        }
        with mock.patch.object(
            lifecycle, "_task_implementer_impact", return_value=impact
        ):
            lifecycle.evaluate(payload)
        current = lifecycle._load(state_path)
        assert current is not None
        self.assertEqual(current["phase"], "reconciliation-required")
        self.assertEqual(current["write_epoch"], 1)

    def test_task_review_correction_reopens_only_zero_write_promotion_waiver(
        self,
    ) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update({"phase": "waived", "waiver": "non-project"})
        lifecycle._write(state_path, state)
        command = "python3 /trusted/prompt_workspace.py wave-plan --workspace /private/workspace.json --run-id run-20260811t135023z-caac975f --capacity 1 --json"
        impact = {
            "status": "authorized",
            "action": "wave-plan",
            "outer_project_root": str(self.project),
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            "checkpoint_head": "b" * 40,
            "review_correction": True,
        }
        pre_payload = {
            **self.base,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        with mock.patch.object(
            lifecycle, "_task_implementer_impact", return_value=impact
        ):
            self.assertEqual(lifecycle.evaluate(pre_payload), {})
            lifecycle.evaluate(
                {
                    **pre_payload,
                    "hook_event_name": "PostToolUse",
                    "tool_response": {"exit_code": 0},
                }
            )

        current = lifecycle._load(state_path)
        assert current is not None
        self.assertEqual(current["phase"], "reconciliation-required")
        self.assertIsNone(current["waiver"])
        self.assertEqual(current["write_epoch"], 1)

        current.update({"phase": "waived", "waiver": "non-project", "write_epoch": 0})
        lifecycle._write(state_path, current)
        with mock.patch.object(
            lifecycle,
            "_task_implementer_impact",
            return_value={**impact, "review_correction": False},
        ):
            denied = lifecycle.evaluate(pre_payload)
        self.assertEqual(
            denied["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_task_implementer_coordinator_contract_is_bound_during_reconciliation(
        self,
    ) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state["phase"] = "reconciliation-required"
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        for action in ("coordinator-stage", "coordinator-commit"):
            command = (
                f"python3 /trusted/prompt_workspace.py {action} "
                "--workspace /private/workspace.json "
                "--run-id run-20260811t135023z-caac975f --json"
            )
            evidence = {
                "status": "authorized",
                "action": action,
                "outer_project_root": str(self.project),
                "project_root": str(self.root / "integration"),
                "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
            }
            payload = {
                **self.base,
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
            with self.subTest(action=action), mock.patch.object(
                lifecycle, "_task_implementer_coordinator", return_value=evidence
            ):
                allowed = lifecycle.evaluate(
                    {**payload, "hook_event_name": "PreToolUse"}
                )
                completed = lifecycle.evaluate(
                    {
                        **payload,
                        "hook_event_name": "PostToolUse",
                        "tool_response": {"exit_code": 0},
                    }
                )
                self.assertNotIn("hookSpecificOutput", allowed)
                self.assertNotIn("hookSpecificOutput", completed)
        current = lifecycle._load(state_path)
        assert current is not None
        self.assertEqual(current["phase"], "reconciliation-required")

    def test_task_implementer_wave_plan_rejects_basename_lookalike(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        workspace = Path(os.environ["CODEX_HOME"]) / "task-implementer/workspace.json"
        workspace.parent.mkdir(parents=True)
        workspace.write_text("{}\n", encoding="utf-8")
        helper = self.root / "lookalike/task-implementer/scripts/prompt_workspace.py"
        helper.parent.mkdir(parents=True)
        helper.write_text(
            "#!/usr/bin/env python3\n"
            "import hashlib, json, sys\n"
            "command = sys.stdin.read()\n"
            "print(json.dumps({\n"
            "    'status': 'authorized',\n"
            "    'action': 'wave-plan',\n"
            f"    'outer_project_root': {str(self.project)!r},\n"
            "    'command_sha256': hashlib.sha256(command.encode()).hexdigest(),\n"
            "    'checkpoint_head': None,\n"
            "}))\n",
            encoding="utf-8",
        )
        helper.chmod(0o755)
        command = shlex.join(
            [
                sys.executable,
                str(helper),
                "wave-plan",
                "--workspace",
                str(workspace),
                "--run-id",
                "run-20260811t135023z-caac975f",
                "--capacity",
                "1",
                "--json",
            ]
        )

        denied = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": command},
            }
        )

        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_project_instructions_apply_arms_seal_and_blocks_new_open(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "planned",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        private_root = state_path.parent / "project-instructions"
        private_root.mkdir(mode=0o700)
        manifest = private_root / "manifest.json"
        manifest.write_text(
            '{"project_root":' + json.dumps(str(self.project)) + "}\n",
            encoding="utf-8",
        )
        manifest.chmod(0o600)
        helper = (
            self.user_skills_root
            / "project-agent-instructions/scripts/project_agent_instructions.py"
        )
        helper.parent.mkdir(parents=True)
        helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
        command = (
            f"python3 {helper} apply --manifest {manifest} "
            f"--decision {private_root / 'decision.json'} "
            f"--ownership {private_root / 'ownership.json'} "
            f"--state {private_root / 'state.json'} --private-root {private_root}"
        )
        payload = {
            **self.base,
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        self.assertEqual(
            lifecycle.evaluate({**payload, "hook_event_name": "PreToolUse"}), {}
        )

        alternate_root = self.root / "alternate-pai"
        alternate_root.mkdir(mode=0o700)
        alternate_manifest = alternate_root / "manifest.json"
        alternate_manifest.write_text(
            '{"project_root":' + json.dumps(str(self.project)) + "}\n",
            encoding="utf-8",
        )
        alternate_manifest.chmod(0o600)
        alternate_command = command.replace(str(private_root), str(alternate_root))
        denied = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": alternate_command},
            }
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

        malformed = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"python3 {helper} apply --manifest {manifest}"
                },
            }
        )
        self.assertEqual(malformed["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(
            lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": f"python3 {helper} --help"},
                }
            ),
            {},
        )
        with mock.patch.object(lifecycle, "_verified_apply_state"):
            lifecycle.evaluate(
                {
                    **payload,
                    "hook_event_name": "PostToolUse",
                    "tool_response": {"exit_code": 0},
                }
            )
        armed = lifecycle._load(state_path)
        assert armed is not None
        self.assertEqual(armed["phase"], "seal-armed")
        project_helper = HOOK_DIR.parents[1] / "scripts/project_specs.py"
        denied = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "Bash",
                "tool_input": {
                    "command": f"python3 {project_helper} open --project-root {self.project} --session-id session-1 --turn-id turn-1"
                },
            }
        )
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_failed_project_instruction_apply_reopens_reconciliation(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "planned",
                "receipt_sha256": "a" * 64,
                "requirements_sha256": "b" * 64,
                "design_sha256": "c" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        helper = HOOK_DIR.parents[2] / (
            "project-agent-instructions/scripts/project_agent_instructions.py"
        )
        private_root = state_path.parent / "project-instructions"
        command = (
            f"python3 {helper} apply --manifest {private_root / 'manifest.json'} "
            f"--decision {private_root / 'decision.json'} "
            f"--ownership {private_root / 'ownership.json'} "
            f"--state {private_root / 'state.json'} --private-root {private_root}"
        )
        with (
            mock.patch.object(
                lifecycle,
                "_bound_coordinator_command",
                return_value=("project-instructions", "apply"),
            ),
            mock.patch.object(
                lifecycle,
                "_verified_apply_state",
                side_effect=lifecycle.HookError(
                    "project-instructions apply did not produce verified state"
                ),
            ),
            self.assertRaisesRegex(
                lifecycle.HookError, "did not produce verified state"
            ),
        ):
            lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PostToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": command},
                    "tool_response": {"exit_code": 0},
                }
            )
        reopened = lifecycle._load(state_path)
        assert reopened is not None
        self.assertEqual(reopened["phase"], "reconciliation-required")
        self.assertEqual(reopened["write_epoch"], 1)

    def test_documentation_waiver_is_narrow(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update({"phase": "waived", "waiver": "documentation-only"})
        lifecycle._write(state_path, state)
        allowed = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": "*** Update File: README.md\n",
            }
        )
        denied = lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PreToolUse",
                "tool_name": "apply_patch",
                "tool_input": "*** Update File: src/app.py\n",
            }
        )
        self.assertEqual(allowed, {})
        self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_shell_and_mcp_mutators_are_not_misclassified_read_only(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        for tool_name, tool_input in (
            ("Bash", {"command": "bash mutate.sh"}),
            ("Bash", {"command": "git add README.md"}),
            ("Bash", {"command": "sed -i.bak s/a/b/ README.md"}),
            ("Bash", {"command": "sort -o README.md README.md"}),
            ("Bash", {"command": "awk 'BEGIN { system(\"touch x\") }'"}),
            ("mcp__get_server__delete_file", {"path": "README.md"}),
            ("mcp__server__read_then_delete", {"path": "README.md"}),
        ):
            with self.subTest(tool_name=tool_name, tool_input=tool_input):
                denied = lifecycle.evaluate(
                    {
                        **self.base,
                        "hook_event_name": "PreToolUse",
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                    }
                )
                self.assertEqual(
                    denied["hookSpecificOutput"]["permissionDecision"], "deny"
                )
        self.assertFalse(
            lifecycle._potential_write("mcp__server__get_file", {"path": "README.md"})
        )

    def test_exact_commit_helper_is_bound_but_raw_git_stays_denied(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        helper = HOOK_DIR.parents[2] / "commit/scripts/commit_transaction.py"
        self.assertEqual(
            hashlib.sha256(helper.read_bytes()).hexdigest(),
            lifecycle.COMMIT_HELPER_SHA256,
        )
        _project, git_root, _scope = lifecycle._project(str(self.project))
        common = Path(
            subprocess.run(
                ["git", "-C", str(git_root), "rev-parse", "--git-common-dir"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.strip()
        )
        if not common.is_absolute():
            common = git_root / common
        common = common.resolve()
        reference = subprocess.run(
            ["git", "-C", str(git_root), "symbolic-ref", "-q", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        authorization, claim = lifecycle._commit_private_paths(
            git_root, self.base["session_id"]
        )
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        authorization.parent.mkdir(parents=True, mode=0o700)
        authorization.write_text(
            json.dumps(
                {
                    "schema": "commit-transaction.authorization.v1",
                    "state": "AUTHORIZED",
                    "repo_root": str(git_root),
                    "worktree": str(git_root),
                    "common_dir": str(common),
                    "ref": reference,
                    "base_head": head,
                    "session_sha256": hashlib.sha256(b"session-1").hexdigest(),
                    "turn_sha256": state["turn_sha256"],
                    "prompt_sha256": "b" * 64,
                    "owner": "direct",
                    "owner_evidence_path": None,
                    "owner_evidence_sha256": None,
                    "allow_default_branch": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        authorization.chmod(0o600)
        command = (
            f"{sys.executable} {helper} prepare --repo-root {git_root} "
            f"--session-id session-1 --authorization {authorization} --claim {claim}"
        )
        payload = {
            **self.base,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        nonterminal = lifecycle.evaluate(payload)
        self.assertEqual(
            nonterminal["hookSpecificOutput"]["permissionDecision"], "deny"
        )
        state.update({"phase": "waived", "waiver": "non-project"})
        lifecycle._write(state_path, state)
        allowed = lifecycle.evaluate(
            {
                **payload,
            }
        )
        self.assertEqual(allowed, {})
        (git_root / "README.md").write_text("# Prepared\n", encoding="utf-8")
        completed = subprocess.run(
            shlex.split(command),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ.copy(),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        consumed = json.loads(authorization.read_text(encoding="utf-8"))
        self.assertEqual(consumed["state"], "CONSUMED")
        before_post = lifecycle._load(state_path)
        assert before_post is not None
        post_result = lifecycle.evaluate(
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_response": {"exit_code": 0},
            }
        )
        self.assertEqual(post_result, {})
        after_post = lifecycle._load(state_path)
        assert after_post is not None
        self.assertEqual(after_post["phase"], "waived")
        self.assertEqual(after_post["write_epoch"], before_post["write_epoch"])
        for denied_command in (
            "git add -A",
            command + " && git status",
            command.replace(str(helper), "/tmp/commit_transaction.py"),
        ):
            denied = lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": denied_command},
                }
            )
            self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_commit_execute_requires_exact_claim_token_and_reviewed_tree(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        helper = HOOK_DIR.parents[2] / "commit/scripts/commit_transaction.py"
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update({"phase": "waived", "waiver": "non-project"})
        lifecycle._write(state_path, state)
        _authorization, claim = lifecycle._commit_private_paths(
            git_root, self.base["session_id"]
        )
        common = lifecycle._git_common_dir(git_root)
        reference = lifecycle._git_symbolic_ref(git_root)
        head = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        candidate = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "HEAD^{tree}"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        claim.parent.mkdir(parents=True, mode=0o700)
        claim.write_text(
            json.dumps(
                {
                    "schema": "commit-transaction.claim.v1",
                    "state": "PREPARED",
                    "repo_root": str(git_root),
                    "common_dir": str(common),
                    "ref": reference,
                    "session_sha256": hashlib.sha256(b"session-1").hexdigest(),
                    "turn_sha256": state["turn_sha256"],
                    "token_sha256": hashlib.sha256(("c" * 64).encode()).hexdigest(),
                    "candidate_tree": candidate,
                    "authorization_owner": "direct",
                    "owner_evidence_path": None,
                    "owner_evidence_sha256": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        claim.chmod(0o600)
        command = (
            f"{sys.executable} {helper} execute --repo-root {git_root} "
            f"--session-id session-1 --claim {claim} --token {'c' * 64} "
            f"--reviewed-tree {candidate} --message 'Bound transaction'"
        )
        payload = {
            **self.base,
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        self.assertEqual(lifecycle.evaluate(payload), {})
        for malformed in (
            command.replace("c" * 64, "d" * 64),
            command.replace(candidate, "e" * 40),
        ):
            denied = lifecycle.evaluate(
                {**payload, "tool_input": {"command": malformed}}
            )
            self.assertEqual(denied["hookSpecificOutput"]["permissionDecision"], "deny")

        review_claim = json.loads(claim.read_text(encoding="utf-8"))
        review_claim["turn_sha256"] = "f" * 64
        claim.write_text(json.dumps(review_claim) + "\n", encoding="utf-8")
        stale_turn = lifecycle.evaluate(payload)
        self.assertEqual(stale_turn["hookSpecificOutput"]["permissionDecision"], "deny")
        review_claim["turn_sha256"] = state["turn_sha256"]
        review_claim.update(
            {
                "state": "REVIEW_REQUIRED",
                "commit_head": head,
                "commit_tree": candidate,
            }
        )
        claim.write_text(
            json.dumps(review_claim) + "\n",
            encoding="utf-8",
        )
        review_command = (
            f"{sys.executable} {helper} review --repo-root {git_root} "
            f"--session-id session-1 --claim {claim} --token {'c' * 64} "
            f"--reviewed-commit {head} --reviewed-tree {candidate}"
        )
        self.assertEqual(
            lifecycle.evaluate({**payload, "tool_input": {"command": review_command}}),
            {},
        )
        denied_review = lifecycle.evaluate(
            {
                **payload,
                "tool_input": {
                    "command": review_command.replace(
                        f"--reviewed-commit {head}",
                        f"--reviewed-commit {'f' * 40}",
                    )
                },
            }
        )
        self.assertEqual(
            denied_review["hookSpecificOutput"]["permissionDecision"], "deny"
        )

    def test_task_commit_prepare_uses_delegated_owner_in_nonterminal_phase(
        self,
    ) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        helper = HOOK_DIR.parents[2] / "commit/scripts/commit_transaction.py"
        _project, git_root, _scope = lifecycle._project(str(self.project))
        authorization, claim = lifecycle._commit_private_paths(
            git_root, self.base["session_id"]
        )
        common = lifecycle._git_common_dir(git_root)
        reference = lifecycle._git_symbolic_ref(git_root)
        head = subprocess.run(
            ["git", "-C", str(git_root), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        plane = Path(os.environ["CODEX_HOME"]) / "task-implementer/run/plane.json"
        plane.parent.mkdir(parents=True, mode=0o700)
        plane.write_text(
            json.dumps(
                {
                    "state": "running",
                    "base_commit": head,
                    "worker_session_sha256": hashlib.sha256(b"session-1").hexdigest(),
                    "assignment_sha256": "a" * 64,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        plane.chmod(0o600)
        authorization.parent.mkdir(parents=True, mode=0o700)
        authorization.write_text(
            json.dumps(
                {
                    "schema": "commit-transaction.authorization.v1",
                    "state": "AUTHORIZED",
                    "repo_root": str(git_root),
                    "worktree": str(git_root),
                    "common_dir": str(common),
                    "ref": reference,
                    "base_head": head,
                    "session_sha256": hashlib.sha256(b"session-1").hexdigest(),
                    "turn_sha256": "a" * 64,
                    "prompt_sha256": "a" * 64,
                    "owner": "task-implementer",
                    "owner_evidence_path": str(plane),
                    "owner_evidence_sha256": "a" * 64,
                    "allow_default_branch": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        authorization.chmod(0o600)
        hook_python = self.root / "hook-bin/python3.12"
        canonical_python = self.root / "path-bin/python3.14"
        for executable in (hook_python, canonical_python):
            executable.parent.mkdir(parents=True, exist_ok=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
        command = (
            f"{canonical_python} {helper} prepare --repo-root {git_root} "
            f"--session-id session-1 --authorization {authorization} --claim {claim}"
        )
        original_which = shutil.which

        def which(value: str) -> str | None:
            if value == "python3.14":
                return str(canonical_python)
            return original_which(value)

        with (
            mock.patch.object(lifecycle.sys, "executable", str(hook_python)),
            mock.patch.object(lifecycle.shutil, "which", side_effect=which),
        ):
            allowed = lifecycle._bound_commit_command(
                command,
                git_root,
                self.base["session_id"],
            )
        self.assertIsNotNone(allowed)
        self.assertEqual(allowed[0], "prepare")

    def test_task_commit_uses_attested_worker_session_not_outer_payload_session(
        self,
    ) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        worker_root = self.root / "worker"
        worker_session = "worker-session"
        command = (
            "python3 /trusted/commit_transaction.py prepare "
            f"--repo-root {worker_root} --session-id {worker_session} "
            "--authorization /private/authorization.json "
            "--claim /private/claim.json"
        )
        delegated = {
            "status": "authorized",
            "action": "prepare",
            "outer_project_root": str(self.project),
            "worker_root": str(worker_root),
            "worker_session_id": worker_session,
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        }
        calls: list[tuple[Path, object]] = []

        def bind(
            _command: str,
            root: Path,
            session_id: object,
            *,
            completed: bool = False,
        ) -> tuple[str, Path, str, str] | None:
            del completed
            calls.append((root, session_id))
            if root == worker_root and session_id == worker_session:
                return (
                    "prepare",
                    Path("/private/claim.json"),
                    "task-implementer",
                    "a" * 64,
                )
            return None

        with (
            mock.patch.object(lifecycle, "_bound_commit_command", side_effect=bind),
            mock.patch.object(
                lifecycle, "_task_implementer_commit", return_value=delegated
            ),
        ):
            allowed = lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": command},
                }
            )

        self.assertEqual(allowed, {})
        self.assertEqual(
            calls,
            [(git_root, self.base["session_id"]), (worker_root, worker_session)],
        )

    def test_task_commit_adapter_session_must_match_command_session(self) -> None:
        command = (
            "python3 /trusted/commit_transaction.py prepare "
            f"--repo-root {self.project} --session-id worker-session "
            "--authorization /private/authorization.json "
            "--claim /private/claim.json"
        )
        adapter = {
            "status": "authorized",
            "action": "prepare",
            "outer_project_root": str(self.project),
            "worker_root": str(self.project),
            "worker_session_id": "different-session",
            "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
        }
        evidence = {
            "owner": "task-implementer",
            "owner_evidence_path": str(
                Path(os.environ["CODEX_HOME"]) / "task-implementer/run/plane.json"
            ),
        }
        with (
            mock.patch.object(
                lifecycle, "_read_regular", return_value=json.dumps(evidence).encode()
            ),
            mock.patch.object(
                lifecycle, "_task_implementer_adapter", return_value=adapter
            ),
            mock.patch.object(lifecycle, "_private_path_is_safe", return_value=True),
        ):
            self.assertIsNone(lifecycle._task_implementer_commit(command, self.project))

    def test_task_commit_owner_remains_bound_for_exact_post_commit_child(self) -> None:
        base_head = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        (self.project / "worker.txt").write_text("worker\n", encoding="utf-8")
        git(self.project, "add", "worker.txt")
        git(self.project, "commit", "-qm", "worker")
        commit_head = subprocess.run(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        plane = Path(os.environ["CODEX_HOME"]) / "task-implementer/run/plane.json"
        plane.parent.mkdir(parents=True, mode=0o700)
        plane.write_text(
            json.dumps(
                {
                    "state": "running",
                    "base_commit": base_head,
                    "worker_session_sha256": hashlib.sha256(b"session-1").hexdigest(),
                    "assignment_sha256": "a" * 64,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        plane.chmod(0o600)
        evidence = {
            "authorization_owner": "task-implementer",
            "owner_evidence_path": str(plane),
            "owner_evidence_sha256": "a" * 64,
            "turn_sha256": "a" * 64,
            "state": "COMMITTED",
            "base_head": base_head,
            "commit_head": commit_head,
        }
        self.assertTrue(
            lifecycle._commit_owner_matches(
                evidence, self.project, "session-1", claim=True
            )
        )
        evidence["state"] = "REVIEW_REQUIRED"
        self.assertTrue(
            lifecycle._commit_owner_matches(
                evidence, self.project, "session-1", claim=True
            )
        )

    def test_failed_write_does_not_mark_reconciliation(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        lifecycle.evaluate(
            {
                **self.base,
                "hook_event_name": "PostToolUse",
                "tool_name": "Bash",
                "tool_input": {"command": "touch x"},
                "tool_response": {"exit_code": 1},
            }
        )
        current = lifecycle._load(state_path)
        assert current is not None
        self.assertEqual(current["phase"], "implementation-open")

    def test_tampered_pending_rules_block_material_implementation(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        state = lifecycle._load(state_path)
        assert state is not None
        state.update(
            {
                "phase": "implementation-open",
                "receipt_sha256": "a" * 64,
                "planned_write_epoch": 0,
            }
        )
        self.bind_empty_rules(state_path, state)
        lifecycle._write(state_path, state)
        (state_path.parent / lifecycle.RULES_NAME).write_text(
            "- tampered\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(
            lifecycle.HookError, "pending project rules changed"
        ):
            lifecycle.evaluate(
                {
                    **self.base,
                    "hook_event_name": "PreToolUse",
                    "tool_name": "apply_patch",
                    "tool_input": "*** Update File: src/app.py\n",
                }
            )

    def test_private_state_directories_are_owner_only(self) -> None:
        lifecycle.evaluate({**self.base, "hook_event_name": "UserPromptSubmit"})
        _project, git_root, _scope = lifecycle._project(str(self.project))
        state_path = lifecycle._state_path(git_root, self.base["session_id"])
        current = state_path.parent
        private_root = Path(os.environ["CODEX_HOME"]) / "project-specs"
        while True:
            self.assertEqual(current.stat().st_mode & 0o777, 0o700)
            if current == private_root:
                break
            current = current.parent

    def test_missing_managed_state_blocks_stop(self) -> None:
        result = lifecycle.evaluate_stop({**self.base, "hook_event_name": "Stop"})
        self.assertEqual(result["decision"], "block")


if __name__ == "__main__":
    unittest.main()
