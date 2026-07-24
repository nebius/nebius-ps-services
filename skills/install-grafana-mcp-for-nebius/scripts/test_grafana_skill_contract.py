#!/usr/bin/env python3
"""Cross-skill contract tests for Nebius Grafana setup and querying."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "install-grafana-mcp-for-nebius"
QUERY = ROOT / "nebius-grafana-query"


class GrafanaSkillContractTest(unittest.TestCase):
    def read(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def test_query_is_implicit_and_installer_is_explicit(self) -> None:
        query = self.read(QUERY / "agents" / "openai.yaml")
        installer = self.read(INSTALLER / "agents" / "openai.yaml")

        self.assertIn("allow_implicit_invocation: true", query)
        self.assertIn("allow_implicit_invocation: false", installer)

    def test_query_is_read_only_and_routes_setup(self) -> None:
        skill = self.read(QUERY / "SKILL.md")

        self.assertIn("Require an already-configured `grafana-nebius`", skill)
        self.assertIn("Use only read operations", skill)
        self.assertIn("$install-grafana-mcp-for-nebius", skill)
        self.assertIn("Do not install, register, reconfigure, or repair", skill)
        self.assertIn("at most 20 sanitized examples", skill)
        self.assertIn("$nebius-audit-log", skill)

    def test_installer_owns_setup_and_hands_off_queries(self) -> None:
        skill = self.read(INSTALLER / "SKILL.md")
        readme = self.read(INSTALLER / "README.md")

        self.assertIn("Install, configure, validate, or repair", skill)
        self.assertIn("list Grafana datasources", skill)
        self.assertIn("$nebius-grafana-query", skill)
        self.assertIn("$nebius-grafana-query", readme)
        self.assertNotIn("Show CPU and memory usage", readme)

    def test_root_catalog_and_evals_match_the_split(self) -> None:
        readme = self.read(ROOT / "README.md")
        installer_evals = self.read(INSTALLER / "evals" / "trigger-prompts.md")
        query_evals = self.read(QUERY / "evals" / "trigger-prompts.md")

        self.assertIn(
            "`install-grafana-mcp-for-nebius` | Explicit only", readme
        )
        self.assertIn("`nebius-grafana-query` | Implicit allowed", readme)
        self.assertIn("route to `$nebius-grafana-query`", installer_evals)
        self.assertIn(
            "invoke `$install-grafana-mcp-for-nebius` explicitly", query_evals
        )
        self.assertIn(
            "Ask for authoritative project/resource scope", query_evals
        )

    def test_query_skill_does_not_claim_installation_or_writes(self) -> None:
        query_description = self.read(QUERY / "SKILL.md").split("---", 2)[1]
        metadata = self.read(QUERY / "agents" / "openai.yaml")

        self.assertIn("Never install or configure MCP", query_description)
        self.assertNotIn("allow_implicit_invocation: false", metadata)
        self.assertNotIn("GRAFANA_SERVICE_ACCOUNT_TOKEN", metadata)


if __name__ == "__main__":
    unittest.main()
