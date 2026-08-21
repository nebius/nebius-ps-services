#!/usr/bin/env python3
"""Static contract checks for non-blocking prompt-session capture."""

from __future__ import annotations

from pathlib import Path
import unittest


SKILLS = Path(__file__).resolve().parents[2]


class PromptSessionSkillContractTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (SKILLS / relative).read_text(encoding="utf-8")

    def assert_terms(self, relative: str, terms: tuple[str, ...]) -> None:
        text = self.text(relative)
        for term in terms:
            with self.subTest(path=relative, term=term):
                self.assertIn(term, text)

    def test_direct_delivery_and_prompt_only_capture_are_mirrored(self) -> None:
        self.assert_terms(
            "prompt-session-intake/SKILL.md",
            (
                "current agent always handles the delivered request normally",
                "Capture never selects, starts, or resumes a workflow",
                "writer-session identity as provenance, never an admission",
                "prompt-session Stop delegate always",
            ),
        )
        self.assert_terms(
            "prompt-session-intake/README.md",
            (
                "direct prompt always reaches the current agent",
                "Capture never starts or resumes a workflow",
                "Stop delegate never blocks completion",
                "Only an explicit",
            ),
        )
        self.assert_terms(
            "prompt-session-intake/references/state-contract.md",
            (
                "Every `UserPromptSubmit` result is non-blocking",
                "managed-lane project are aliases",
                "prompt-session delegate always passes Stop",
                "never persists the submitted",
            ),
        )
        self.assert_terms(
            "task-implementer/references/prompt-workspace.md",
            (
                "proceeds normally in the current agent",
                "Capture does not route",
                "metadata-only event-v2",
            ),
        )
        self.assert_terms(
            "sdlc-start/references/prompt-workspace.md",
            (
                "current agent handles it normally",
                "captured updates and manual changes require explicit",
                "persists the submitted body",
            ),
        )

    def test_selective_metadata_only_capture_contract_is_mirrored(self) -> None:
        for relative in (
            "prompt-session-intake/SKILL.md",
            "prompt-session-intake/README.md",
            "prompt-session-intake/references/state-contract.md",
            "task-implementer/SKILL.md",
            "task-implementer/README.md",
            "task-implementer/references/prompt-workspace.md",
            "sdlc-start/SKILL.md",
            "sdlc-start/README.md",
            "sdlc-start/references/prompt-workspace.md",
        ):
            self.assert_terms(
                relative,
                (
                    "project-intent",
                    "operation",
                    "projection",
                    "direct",
                ),
            )
        self.assert_terms(
            "prompt-session-intake/SKILL.md",
            (
                "`merge`, `noop`, or `sensitive`",
                "workflow or skill invocation",
                "shell/tool action",
                "mixed turns",
                "defines a project",
                "Never write the submitted prompt body",
                "Event-v1",
            ),
        )
        self.assert_terms(
            "prompt-session-intake/evals/process-cases.md",
            (
                "prompt-intake-positive-11",
                "prompt-intake-positive-15",
                "secret plus valid project intent",
                "one wins and one reports prompt",
            ),
        )
        self.assert_terms(
            "prompt-session-intake/evals/trigger-prompts.csv",
            (
                "Add configurable timeout, then run pytest -q.",
                "tool check --json",
            ),
        )

    def test_event_and_adapter_code_have_one_v2_projection_path(self) -> None:
        storage = self.text(
            "prompt-session-intake/assets/hooks/prompt_session_storage.py"
        )
        state = self.text("prompt-session-intake/assets/hooks/prompt_session_state.py")
        coordinator = self.text("prompt-session-intake/scripts/prompt_session.py")
        self.assertIn('EVENT_SCHEMA = "prompt-session-intake/event-v2"', storage)
        self.assertIn('/ "events-v2"', storage)
        self.assertIn('DISPOSITIONS = {"merge", "noop", "sensitive"}', storage)
        self.assertIn('path.parent / "project-intent.md"', state)
        self.assertNotIn('path.parent / "raw.md"', state)
        self.assertIn('add_argument("--projection-file"', coordinator)
        self.assertNotIn('add_argument("--refined-file"', coordinator)
        for relative in (
            "task-implementer/scripts/prompt_workspace_runs.py",
            "sdlc-start/scripts/prompt_workspace.py",
        ):
            adapter = self.text(relative)
            self.assertIn("def merge_session_projection", adapter)
            self.assertIn("prompt-session-operation:v2:", adapter)
            self.assertIn("projection_sha256", adapter)
            self.assertNotIn("def merge_session_refinement", adapter)

    def test_hook_entrypoints_have_no_prompt_session_block_result(self) -> None:
        for relative in (
            "prompt-session-intake/assets/hooks/prompt_session_intake.py",
            "prompt-session-intake/assets/hooks/stop_prompt_session_intake.py",
        ):
            text = self.text(relative)
            with self.subTest(path=relative):
                self.assertNotIn('"continue": False', text)
                self.assertNotIn('"stopReason"', text)
        coordinator = self.text("prompt-session-intake/scripts/prompt_session.py")
        self.assertNotIn('add_argument("--run-id")', coordinator)
        self.assertNotIn('add_argument("--objective-terminal"', coordinator)

    def test_stale_implicit_execution_contract_is_absent(self) -> None:
        stale = (
            "execute its normal run/resume path once",
            "this same run path executes once",
            "blocks only the exact current unfinished event",
            "writer lease is",
        )
        for relative in (
            "prompt-session-intake/SKILL.md",
            "prompt-session-intake/README.md",
            "prompt-session-intake/references/state-contract.md",
            "task-implementer/SKILL.md",
            "task-implementer/README.md",
            "task-implementer/references/prompt-workspace.md",
            "sdlc-start/SKILL.md",
            "sdlc-start/README.md",
            "sdlc-start/references/prompt-workspace.md",
        ):
            text = self.text(relative)
            for phrase in stale:
                with self.subTest(path=relative, phrase=phrase):
                    self.assertNotIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
