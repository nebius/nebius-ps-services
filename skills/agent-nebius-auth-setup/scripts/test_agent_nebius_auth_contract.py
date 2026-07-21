#!/usr/bin/env python3
"""Cross-skill contract tests for Nebius agent authentication."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
DIAGNOSE = ROOT / "agent-nebius-auth-diagnose"
SETUP = ROOT / "agent-nebius-auth-setup"


class AgentNebiusAuthContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_both_skills_remain_implicitly_selectable(self) -> None:
        for skill in (DIAGNOSE, SETUP):
            with self.subTest(skill=skill.name):
                metadata = self.read(skill / "agents" / "openai.yaml")
                self.assertIn("allow_implicit_invocation: true", metadata)

    def test_diagnose_is_read_only_but_may_handoff_to_setup_planning(self) -> None:
        skill = self.read(DIAGNOSE / "SKILL.md")

        self.assertIn("read-only dry-run plan", skill)
        self.assertIn("implicit setup never authorizes mutation", skill)
        self.assertIn("retry without setup or user confirmation", skill)
        self.assertIn("$CODEX_NEBIUS_TOKEN_HELPER", skill)
        self.assertIn("retry-idempotent", skill)
        self.assertIn("blocked-admin-auth", skill)

    def test_persistent_mutations_remain_confirmation_gated(self) -> None:
        skill = self.read(SETUP / "SKILL.md")

        self.assertIn("explicit current-turn confirmation", skill)
        self.assertIn("repair-lease", skill)
        self.assertIn("repair-local", skill)
        self.assertIn("not a cryptographic boundary", skill)
        for excluded in (
            "IAM",
            "credential generation/rotation",
            "identity",
            "hook",
        ):
            with self.subTest(excluded=excluded):
                self.assertIn(excluded, skill)

    def test_bootstrap_asks_once_and_revalidates_same_target_confirmation(self) -> None:
        setup = self.read(SETUP / "SKILL.md")
        diagnose = self.read(DIAGNOSE / "SKILL.md")
        evals = self.read(SETUP / "evals" / "trigger-prompts.md")

        self.assertIn("ask exactly once", setup)
        self.assertIn("versioned, state-bound plan", setup)
        self.assertIn("Do not ask again", setup)
        self.assertIn("Do not ask\nagain for partial convergence", diagnose)
        self.assertIn("without another user prompt", evals)
        self.assertIn(
            "Target/configuration drift, credential identity mismatch, or any action outside\nthe recorded envelope fails closed",
            setup,
        )

    def test_replacement_is_separate_and_marked_before_invocation(self) -> None:
        setup = self.read(SETUP / "SKILL.md")
        evals = self.read(SETUP / "evals" / "trigger-prompts.md")

        self.assertIn("observed service-account ID, group ID,\n   credential SHA-256", setup)
        self.assertIn("After an ID is observed, any change requires a new\nuser confirmation", setup)
        self.assertIn("ensure` never performs that replacement\ninternally", setup)
        self.assertIn("Mark the attempt before\ninvocation", setup)
        self.assertIn("failed or interrupted replacement ends that\nbootstrap attempt", setup)
        self.assertIn("same-name service account or group has a different ID", evals)

    def test_role_plan_stays_factual_without_extra_recommendation(self) -> None:
        skill = self.read(SETUP / "SKILL.md")

        self.assertIn("Role reconciliation is additive", skill)
        self.assertIn(
            "do not add broader- or\nnarrower-role advice unless the user asks for it",
            skill,
        )
        self.assertNotIn("Recommend a narrower role", skill)
        self.assertNotIn("Project-level `admin` is broad", skill)

    def test_managed_iam_is_strictly_project_scoped(self) -> None:
        setup = self.read(SETUP / "SKILL.md")
        diagnose = self.read(DIAGNOSE / "SKILL.md")
        script = self.read(SETUP / "scripts" / "agent-nebius-auth-setup.sh")
        evals = self.read(SETUP / "evals" / "trigger-prompts.md")

        self.assertIn("## Project Scope Invariant", setup)
        self.assertIn("never as the group parent or access-permit resource", setup)
        self.assertIn("tenant-scope denial is expected", diagnose)
        self.assertIn(
            'nebius iam group create \\\n      --parent-id "$PROJECT_ID"',
            script,
        )
        self.assertNotIn(
            'nebius iam group create \\\n      --parent-id "$TENANT_ID"',
            script,
        )
        self.assertIn('--resource-id "$PROJECT_ID"', script)
        self.assertNotIn('--resource-id "$TENANT_ID"', script)
        self.assertIn("same-name Codex group exists under the tenant", evals)

    def test_project_discovery_authority_is_aligned(self) -> None:
        diagnose = self.read(DIAGNOSE / "SKILL.md")
        setup = self.read(SETUP / "SKILL.md")
        concepts = (
            ("current turn", "current turn"),
            ("task-state", "task-state"),
            ("Persistent memory", "Persistent memory"),
            ("active profile", "active Nebius profile"),
            ("credential filename", "credential filename"),
            ("cwd", "working-directory"),
            ("legacy default selector", "codex-agent-default-project-id"),
        )
        for diagnose_phrase, setup_phrase in concepts:
            with self.subTest(concept=diagnose_phrase):
                self.assertIn(diagnose_phrase, diagnose)
                self.assertIn(setup_phrase, setup)

    def test_evals_cover_retry_and_confirmed_persistent_repair(self) -> None:
        evals = self.read(DIAGNOSE / "evals" / "trigger-prompts.md")

        self.assertIn("retry without setup or user confirmation", evals)
        self.assertIn("401/`UNAUTHENTICATED`", evals)
        self.assertIn("confirmed setup plan", evals)
        self.assertIn("valid matching repair lease", evals)

    def test_root_readme_documents_renewal_ownership(self) -> None:
        readme = self.read(ROOT / "README.md")

        for phrase in (
            "Normal CLI/profile",
            "supported SDK credential paths own renewal",
            "CODEX_NEBIUS_TOKEN_HELPER",
            "retry-idempotent",
            "Already-running",
            "blocked-admin-auth",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, readme)


if __name__ == "__main__":
    unittest.main()
