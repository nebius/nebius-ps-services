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
            "$sdlc-start run <prompt-ref-or-file>",
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
                "agentic-sdlc/prompt-v3",
                "agentic-sdlc/prompt-binding-v2",
                "ALREADY_COMPLETE",
                "private FIFO",
                "QUEUED_PROMPT_DRIFT",
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
                "run <prompt-ref-or-file>",
                "allow_implicit_invocation: false",
            ],
        )

    def test_project_instructions_route_after_design(self) -> None:
        self.assert_terms(
            "sdlc-start/SKILL.md",
            [
                "`scripts/validate_project_specs.py`",
                "project-agent-instructions.spec-validation.v3",
                "`project-agent-instructions` before auto-steering",
                "`maintain-project-specs` ownership",
                "`reload_required: true`",
                "`project-agent-instructions-change`",
            ],
        )
        self.assert_terms(
            "sdlc-start/references/state-schema.md",
            [
                "4. project-agent-instructions",
                "17. managed child only: outer-integration-pending",
                "20. sdlc-merge-pr",
                '"project-agent-instructions": 0',
            ],
        )
        self.assert_terms(
            "sdlc-prepare-execution/SKILL.md",
            [
                "ownership-receipted v3 selected-project",
                "Reject an\n   unverified, reload-pending, edited, or human-owned `AGENTS.md`",
            ],
        )

    def test_no_current_bare_continuation_contract(self) -> None:
        hook = self.text("sdlc-start/assets/hooks/stop_sdlc_continue.py")
        self.assertIn("run {shlex.quote(prompt_filename)}", hook)
        self.assertNotIn('f"Use ${COORDINATOR_SKILL}."', hook)

    def test_managed_outer_handoff_is_exact_and_local(self) -> None:
        self.assert_terms(
            "sdlc-start/SKILL.md",
            [
                "`outer-integration-pending`",
                "`$worktree integrate <generated-name>`",
                "recorded primary path/source branch",
                "fresh user invocation from that primary checkout",
                "auto-continue the explicit-only",
                "never push the\n  child or open a PR from it",
                "`complete-outer-integration` to bind",
            ],
        )
        self.assert_terms(
            "sdlc-start/references/state-schema.md",
            [
                "recorded\n    primary path and await a fresh",
                "`$worktree integrate` from that primary checkout",
                "recorded primary path plus\nthe exact `$worktree integrate <generated-name>` command and stops",
                "The child is never\npublished",
                "create-pr` is publication-only",
            ],
        )

    def test_private_completion_helpers_do_not_expand_public_surface(self) -> None:
        skill = self.text("sdlc-start/SKILL.md")
        helper = self.text("sdlc-start/scripts/prompt_workspace.py")
        for term in (
            "Agentic SDLC: New Prompt",
            "Agentic SDLC: Prompt History",
            '"new"',
            '"list"',
            '"queue-list"',
            '"queue-cancel"',
            '"queue-next"',
            '"verify"',
            '"refinement-verify"',
        ):
            self.assertIn(term, helper)
        self.assertIn(
            "private `new`, `list`, `queue-list`, `queue-cancel`, `queue-next`, and",
            skill,
        )
        self.assertIn("requirements lock helper", skill)
        self.assertIn("Expose exactly these two actions", skill)

    def test_requirements_refinement_is_mechanically_locked(self) -> None:
        self.assert_terms(
            "sdlc-start/SKILL.md",
            [
                "prompt_workspace.py refinement-verify",
                "exact current `docs/requirements.md`",
            ],
        )
        self.assert_terms(
            "sdlc-create-requirements/SKILL.md",
            [
                "private `refinement-verify` action owned by `sdlc-start`",
                "latest accepted prompt identity",
            ],
        )
        self.assert_terms(
            "sdlc-start/scripts/prompt_workspace.py",
            [
                "def verify_requirements_refinement_contract",
                "REQUIREMENTS_REFINEMENT_REQUIRED",
            ],
        )


if __name__ == "__main__":
    unittest.main()
