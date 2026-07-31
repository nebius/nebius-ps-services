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

    def test_diagnose_is_implicit_and_setup_is_explicit(self) -> None:
        diagnose = self.read(DIAGNOSE / "agents" / "openai.yaml")
        setup = self.read(SETUP / "agents" / "openai.yaml")

        self.assertIn("allow_implicit_invocation: true", diagnose)
        self.assertIn("allow_implicit_invocation: false", setup)

    def test_diagnose_carries_explicit_task_project_into_sensitive_payloads(
        self,
    ) -> None:
        skill = self.read(DIAGNOSE / "SKILL.md")
        metadata = self.read(DIAGNOSE / "agents" / "openai.yaml")
        evals = self.read(DIAGNOSE / "evals" / "trigger-prompts.md")
        readme = self.read(ROOT / "README.md")

        self.assertIn(
            "Project selection is task execution context, not a typed skill argument.",
            skill,
        )
        self.assertIn("current task only", skill)
        self.assertIn("raw shell token at byte zero", skill)
        self.assertIn("prefix the entire outer Bash payload once", skill)
        self.assertIn("split it into separate Bash calls", skill)
        self.assertIn("leave local-only commands", skill)
        self.assertIn("retry the corrected payload once", skill)
        self.assertIn("prefer an explicit task selector", metadata)
        self.assertIn("config-owned default profile project", metadata)
        self.assertIn("split mixed local/Nebius payloads", metadata)
        self.assertIn(
            "Known task project, then missing-selector hook denial", evals
        )
        self.assertIn(
            "later user turn explicitly selects project B", evals
        )
        self.assertIn(
            "Proposed compound payload mixes a local-only probe", evals
        )
        self.assertIn("Local-only `git`, `rg`, or unrelated help command", evals)
        self.assertIn(
            "Project selection is task execution context, not a typed skill argument.",
            readme,
        )
        self.assertIn(
            "explicit or config-owned default-profile Nebius project", readme
        )
        self.assertIn("Mixed local/Nebius payloads are split", readme)

    def test_default_profile_project_fallback_is_aligned(self) -> None:
        diagnose_skill = self.read(DIAGNOSE / "SKILL.md")
        diagnose_evals = self.read(DIAGNOSE / "evals" / "trigger-prompts.md")
        setup_skill = self.read(SETUP / "SKILL.md")
        setup_readme = self.read(SETUP / "README.md")
        setup_evals = self.read(SETUP / "evals" / "trigger-prompts.md")
        hook = self.read(
            SETUP / "assets" / "hooks" / "pre_tool_use_nebius_auth.py"
        )
        readme = self.read(ROOT / "README.md")

        for text in (diagnose_skill, setup_skill, setup_readme, readme):
            self.assertIn("nebius profile current", text)
            self.assertIn("parent-id", text)
            self.assertIn("config-owned", text)
            self.assertIn("default", text)

        self.assertIn("An explicit task project always wins", diagnose_skill)
        self.assertIn(
            "Raw-token helper children always require an explicit leading selector",
            diagnose_skill,
        )
        self.assertIn(
            "Raw-token helper child has no leading selector", diagnose_evals
        )
        self.assertIn("No explicit task project", setup_evals)
        self.assertIn("discover_default_profile_project", hook)
        self.assertIn("default_profile_discovery_environment", hook)

    def test_explicit_setup_needs_no_second_confirmation(self) -> None:
        skill = self.read(SETUP / "SKILL.md")
        script = self.read(SETUP / "scripts" / "agent-nebius-auth-setup.sh")

        self.assertIn("Direct invocation authorizes one bounded", skill)
        self.assertIn("Do not ask for another confirmation", skill)
        self.assertNotIn("--confirm", script)
        self.assertNotIn("replace-credential", script)
        self.assertNotIn("compute_plan_digest", script)

    def test_diagnose_never_invokes_setup_implicitly(self) -> None:
        skill = self.read(DIAGNOSE / "SKILL.md")

        self.assertIn("Do not invoke setup implicitly", skill)
        self.assertIn("invoke `$agent-nebius-auth-setup` explicitly", skill)
        self.assertIn("$CODEX_NEBIUS_TOKEN_HELPER", skill)
        self.assertIn("retry-idempotent", skill)
        self.assertIn("blocked-admin-auth", skill)

    def test_managed_iam_is_one_group_with_two_fixed_permits(self) -> None:
        skill = self.read(SETUP / "SKILL.md")
        script = self.read(SETUP / "scripts" / "agent-nebius-auth-setup.sh")

        self.assertIn("one deterministic tenant-parented custom group", skill)
        self.assertIn("`admin` on the selected project", skill)
        self.assertIn("`viewer` on the authoritative parent tenant", skill)
        self.assertIn('PROJECT_ROLE="admin"', script)
        self.assertIn('TENANT_ROLE="viewer"', script)
        self.assertIn('SA_NAME="codex-agent-sa"', script)
        self.assertIn('GROUP_NAME="codex-agent-${project_hash}"', script)
        self.assertIn(
            'get_or_create_group "$GROUP_NAME" "$TENANT_ID"', script
        )
        self.assertIn("ensure_exact_agent_group_permits", script)
        self.assertNotIn("TENANT_QUOTA_GROUP", script)
        self.assertNotIn("--role)", script)
        self.assertNotIn("--service-account-name)", script)

    def test_managed_group_rejects_extra_or_duplicate_permits(self) -> None:
        skill = self.read(SETUP / "SKILL.md")
        script = self.read(SETUP / "scripts" / "agent-nebius-auth-setup.sh")

        self.assertIn("other permit or\nduplicate", skill)
        self.assertIn("without duplicates; refusing mutation", script)
        self.assertIn("length == 2", script)
        self.assertIn("must contain only one membership", script)

    def test_setup_uses_one_live_convergence_pass(self) -> None:
        skill = self.read(SETUP / "SKILL.md")
        script = self.read(SETUP / "scripts" / "agent-nebius-auth-setup.sh")

        self.assertIn("performs one convergence pass", skill)
        main = script.split("main() {", maxsplit=1)[1]
        self.assertEqual(main.count("resolve_project_metadata"), 1)
        self.assertEqual(main.count("build_setup_plan"), 1)
        self.assertIn("if is_dry_run; then\n    build_setup_plan", main)

    def test_credential_replacement_is_bounded_inside_ensure(self) -> None:
        skill = self.read(SETUP / "SKILL.md")
        script = self.read(SETUP / "scripts" / "agent-nebius-auth-setup.sh")

        self.assertIn("canonical credential at most once", skill)
        self.assertIn("ensure_working_profile_with_one_replacement", script)
        self.assertIn("no second replacement was attempted", script)
        self.assertIn("credential_auth_failure_is_proven", script)
        self.assertIn("profile_matches_service_account", script)
        self.assertIn('profile_has_project_access "$service_account_id"', script)

    def test_deleted_credential_identity_has_one_strict_human_bootstrap_path(
        self,
    ) -> None:
        skill = self.read(SETUP / "SKILL.md")
        readme = self.read(SETUP / "README.md")
        script = self.read(SETUP / "scripts" / "agent-nebius-auth-setup.sh")

        self.assertIn("minting a human-user access token", skill)
        self.assertIn("never prints or\npersists that human token", skill)
        self.assertIn("provider-classified RPC/API `NotFound`", skill)
        self.assertIn('"not found" text', skill)
        self.assertIn("service_account_get_error_is_not_found", script)
        self.assertIn('credential_service_account_is_current', script)
        self.assertIn('ensure_deleted_service_account_credential_flow', script)
        self.assertIn("len(codes) == 1", script)
        self.assertIn('[[ "$rpc_code" == "notfound" ]]', script)
        self.assertIn("Authentication, authorization, transient", script)
        self.assertIn("never prints or persists the human\ntoken", readme)

        recovery = script.split(
            "ensure_deleted_service_account_credential_flow() {", maxsplit=1
        )[1].split("\n}\n", maxsplit=1)[0]
        self.assertLess(
            recovery.index("ensure_iam_shape_for_service_account"),
            recovery.index("replace_credential_file"),
        )
        self.assertLess(
            recovery.index("replace_credential_file"),
            recovery.index("ensure_profile_binding"),
        )
        self.assertNotIn("ensure_working_profile_with_one_replacement", recovery)

    def test_dry_run_discloses_profile_and_conditional_key_repair(self) -> None:
        script = self.read(SETUP / "scripts" / "agent-nebius-auth-setup.sh")

        self.assertIn("profile_needs_rebind", script)
        self.assertIn(
            "if the rebuilt profile still has a classified credential-authentication failure",
            script,
        )
        self.assertIn("CODEX_NEBIUS_PROJECT_ID=$PROJECT_ID nebius", script)

    def test_root_readme_and_evals_match_the_contract(self) -> None:
        readme = self.read(ROOT / "README.md")
        setup_evals = self.read(SETUP / "evals" / "trigger-prompts.md")
        diagnose_evals = self.read(DIAGNOSE / "evals" / "trigger-prompts.md")

        self.assertIn("`agent-nebius-auth-setup` | Explicit only", readme)
        self.assertIn("One\ndeterministic group is parented", readme)
        self.assertIn("without another confirmation", setup_evals)
        self.assertIn("invoke setup explicitly", diagnose_evals)


if __name__ == "__main__":
    unittest.main()
