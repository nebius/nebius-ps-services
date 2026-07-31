#!/usr/bin/env python3
"""Static contract checks for prompt-bound Agentic SDLC entry and steering."""

from __future__ import annotations

from pathlib import Path
import unittest


SKILLS = Path(__file__).resolve().parents[2]


class SdlcStartContractTests(unittest.TestCase):
    def text(self, relative: str) -> str:
        return (SKILLS / relative).read_text(encoding="utf-8")

    def assert_terms(self, relative: str, terms: list[str]) -> None:
        text = self.text(relative)
        for term in terms:
            with self.subTest(path=relative, term=term):
                self.assertIn(term, text)

    def test_public_interface_is_mirrored(self) -> None:
        terms = [
            "$sdlc-start workspace init [project-folder]",
            "$sdlc-start run <prompt-path-or-unique-filename>",
        ]
        for relative in (
            "sdlc-start/SKILL.md",
            "sdlc-start/README.md",
            "sdlc-start/references/prompt-workspace.md",
        ):
            self.assert_terms(relative, terms)
        for relative in ("docs/agentic-sdlc-design.md", "README.md"):
            if (SKILLS / relative).is_file():
                self.assert_terms(relative, terms)

    def test_state_and_steering_contract_is_mirrored(self) -> None:
        self.assert_terms(
            "sdlc-start/references/prompt-workspace.md",
            [
                "agentic-sdlc/prompt-v1",
                "agentic-sdlc/prompt-binding-v1",
                "ALREADY_COMPLETE",
                "ACTIVE_RUN_CONFLICT",
                "WORKFLOW_UPGRADE_REQUIRED",
            ],
        )
        self.assert_terms(
            "sdlc-auto-steering/SKILL.md",
            ["prompt ID", "revision", "digest", "snapshot", "steering-resolve"],
        )
        self.assert_terms(
            "sdlc-auto-steering/assets/templates/auto-steering.json.template",
            [
                "prompt_id",
                "prompt_revision",
                "prompt_sha256",
                "prompt_snapshot",
                "compact redacted summary only",
            ],
        )
        steering_template = self.text(
            "sdlc-auto-steering/assets/templates/STEERING.md.template"
        )
        self.assertIn("accepted managed prompt revision", steering_template)
        self.assertIn("compact redacted summary", steering_template)
        self.assertNotIn("raw prompt when safe", steering_template)

    def test_workspace_identity_is_helper_owned(self) -> None:
        skill = self.text("sdlc-start/SKILL.md")
        self.assertIn("project ID returned by the validated workspace", skill)
        self.assertIn("Never recompute prompt", skill)
        self.assertNotIn("Resolve the project ID from the repository identity", skill)

    def test_metadata_stays_explicit_only(self) -> None:
        self.assert_terms(
            "sdlc-start/agents/openai.yaml",
            [
                "workspace init [project-folder]",
                "run <prompt-path-or-unique-filename>",
                "allow_implicit_invocation: false",
            ],
        )

    def test_project_instructions_route_after_design(self) -> None:
        self.assert_terms(
            "sdlc-start/SKILL.md",
            [
                "route to\n  `project-agent-instructions` before auto-steering",
                "`agentic-sdlc` ownership",
                "`created`,\n  `refreshed`, `existing-sufficient`, or `not-needed`",
                "`project-agent-instructions-change`",
            ],
        )
        self.assert_terms(
            "sdlc-start/references/state-schema.md",
            [
                "4. project-agent-instructions",
                "19. sdlc-merge-pr",
                '"project-agent-instructions": 0',
            ],
        )
        self.assert_terms(
            "sdlc-prepare-execution/SKILL.md",
            [
                "provenance-owned generated project-root",
                "Reject an\n   unverified or human-owned `AGENTS.md`",
            ],
        )

    def test_no_current_bare_continuation_contract(self) -> None:
        hook = self.text("sdlc-start/assets/hooks/stop_sdlc_continue.py")
        self.assertIn("run {shlex.quote(prompt_filename)}", hook)
        self.assertNotIn('f"Use ${COORDINATOR_SKILL}."', hook)

    def test_private_completion_helpers_do_not_expand_public_surface(self) -> None:
        skill = self.text("sdlc-start/SKILL.md")
        helper = self.text("sdlc-start/scripts/prompt_workspace.py")
        for term in (
            "Agentic SDLC: New Prompt",
            "Agentic SDLC: Prompt History",
            '"new"',
            '"list"',
            '"verify"',
        ):
            self.assertIn(term, helper)
        self.assertIn("private `new`, `list`, and `verify`", skill)
        self.assertIn("Expose exactly these two actions", skill)


if __name__ == "__main__":
    unittest.main()
