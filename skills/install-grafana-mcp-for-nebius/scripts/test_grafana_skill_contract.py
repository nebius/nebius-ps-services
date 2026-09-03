#!/usr/bin/env python3
"""Cross-skill contract tests for Nebius Grafana setup and querying."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "install-grafana-mcp-for-nebius"
QUERY = ROOT / "nebius-grafana-query"
REPORT = QUERY / "references" / "result-reporting.md"


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
        self.assertIn("Do not call the", skill)
        self.assertIn("Nebius CLI", skill)
        self.assertIn("`CODEX_NEBIUS_PROJECT_ID`", skill)
        self.assertIn("Never retry with another", skill)
        self.assertIn("$nebius-audit-log", skill)

    def test_installer_owns_setup_and_hands_off_queries(self) -> None:
        skill = self.read(INSTALLER / "SKILL.md")
        readme = self.read(INSTALLER / "README.md")
        setup_guide = self.read(INSTALLER / "references" / "setup-guide.md")
        evals = self.read(INSTALLER / "evals" / "process-cases.md")
        helper = self.read(INSTALLER / "scripts" / "ensure-local-config.sh")

        self.assertIn("Install, configure, validate, or repair", skill)
        self.assertIn("one pinned human Nebius CLI", skill)
        self.assertIn(".user_profile.id", skill)
        self.assertIn("--user-profile", skill)
        self.assertIn("--apply --replace-existing", skill)
        self.assertIn("--user-profile", helper)
        self.assertIn("NEBIUS_GRAFANA_IDENTITY_FILE", helper)
        self.assertIn('codex mcp get "$mcp_server_name" --json', helper)
        self.assertIn("registration_values_match", helper)
        self.assertIn("registration_replacement_shape_supported", helper)
        self.assertIn("no prior values were replayed", helper)
        self.assertNotIn("restore_codex_mcp", helper)
        self.assertIn("trusted_wrapper_path", helper)
        self.assertIn("ensure_startup_token", helper)
        self.assertIn("GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE", helper)
        self.assertIn('codex_startup_timeout_seconds="300"', helper)
        self.assertIn("set_codex_mcp_startup_timeout", helper)
        self.assertIn("hashlib.sha256", helper)
        self.assertIn("expected_digest", helper)
        self.assertIn(
            "startup_timeout != float(expected_timeout)",
            helper,
        )
        self.assertIn("`startup_timeout_sec = 300.0`", skill)
        self.assertIn("300-second startup", readme)
        self.assertIn("startup_timeout_sec = 300.0", setup_guide)
        self.assertIn("browser authentication", setup_guide)
        self.assertIn("Invoke the setup helper as its own Bash command", skill)
        self.assertIn("command-shape coordination event", skill)
        self.assertIn("Hook-Safe Invocation Boundary", setup_guide)
        self.assertIn("expected command-shape enforcement", evals)
        self.assertNotIn("set-codex-mcp-timeouts", readme)
        self.assertIn("list Grafana datasources", skill)
        self.assertIn("$nebius-grafana-query", skill)
        self.assertIn("$nebius-grafana-query", readme)
        self.assertNotIn("Show CPU and memory usage", readme)

    def test_wrapper_enforces_human_auth_and_read_only_flags(self) -> None:
        wrapper = self.read(INSTALLER / "scripts" / "run-nebius-grafana-mcp.sh")

        for variable in (
            "CODEX_NEBIUS_PROJECT_ID",
            "NEBIUS_AUTH_CREDENTIALS_FILE",
            "NEBIUS_IAM_TOKEN",
            "NEBIUS_PROFILE",
            "TOKEN",
        ):
            self.assertIn(f"-u {variable}", wrapper)
        self.assertIn("validate_identity_binding", wrapper)
        self.assertIn("--disable-write --max-loki-log-limit 20", wrapper)
        self.assertIn("--disable-proxied", wrapper)
        self.assertIn("GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE", wrapper)
        self.assertIn("NEBIUS_GRAFANA_LOCK_WAIT_SECONDS:=90", wrapper)
        self.assertIn(
            "NEBIUS_GRAFANA_STARTUP_LOCK_WAIT_SECONDS:=210",
            wrapper,
        )
        self.assertIn("started_at_epoch=", wrapper)
        self.assertIn("process_started_at=", wrapper)
        self.assertIn("unsupported mcp-grafana argument", wrapper)
        self.assertIn("cleanup_worker_timer", wrapper)
        self.assertIn("cleanup_refresh_worker", wrapper)
        self.assertIn("refresh_token interactive", wrapper)
        self.assertIn("renewing it before MCP startup", wrapper)
        self.assertIn("refresh_token noninteractive", wrapper)
        self.assertIn(
            "start a new chat or restart Codex before using grafana-nebius",
            wrapper,
        )

    def test_query_has_fail_closed_multi_scope_contract(self) -> None:
        skill = self.read(QUERY / "SKILL.md")
        guide = self.read(QUERY / "references" / "query-guide.md")
        normalized_skill = " ".join(skill.split())

        self.assertIn("tenant IDs", skill)
        self.assertIn("project IDs", skill)
        self.assertIn("resource IDs", skill)
        self.assertIn("different kinds with\n  `AND`", skill)
        self.assertIn("within one kind with `OR`", skill)
        self.assertIn("“all,”", skill)
        self.assertIn("“every,”", skill)
        self.assertIn("“across everything I can access.”", skill)
        self.assertIn("Bare “any” is\n  existential", skill)
        self.assertIn("at most 20 scopes", skill)
        self.assertIn("instead of dropping the predicate", normalized_skill)
        self.assertIn("Do not infer federation from a datasource name", guide)
        self.assertIn("mark that datasource unscopable and skip", guide)
        self.assertIn("trusted branch\n   provenance", guide)

    def test_query_pins_window_and_decomposes_without_changing_semantics(
        self,
    ) -> None:
        skill = self.read(QUERY / "SKILL.md")
        guide = self.read(QUERY / "references" / "query-guide.md")
        normalized_skill = " ".join(skill.split())
        normalized_guide = " ".join(guide.split())

        self.assertIn("one absolute UTC start/end pair", normalized_skill)
        self.assertIn("Do not recompute `now` between branches", normalized_guide)
        self.assertIn("disjoint, exactly recomposable branches", normalized_skill)
        self.assertIn("same absolute window", normalized_skill)
        self.assertIn("datasource path, and credential", normalized_skill)
        self.assertIn("global quantile, ratio, or ranking", normalized_skill)
        self.assertIn("This is not fallback or retry", normalized_skill)
        self.assertIn("deduplicate on the full attribution key", normalized_skill)
        self.assertIn("failed branch makes the report partial", normalized_skill)
        self.assertIn("Do not call decomposition a retry or fallback", normalized_guide)

    def test_query_proves_federation_and_bounds_cardinality(self) -> None:
        skill = self.read(QUERY / "SKILL.md")
        guide = self.read(QUERY / "references" / "query-guide.md")
        normalized_skill = " ".join(skill.split())
        normalized_guide = " ".join(guide.split())

        self.assertIn(
            "federated fast path only after a bounded preflight",
            normalized_skill,
        )
        self.assertIn("Never infer federation from a datasource", normalized_skill)
        self.assertIn("represents every requested scope", normalized_guide)
        self.assertIn(
            "authoritative labels/tags needed for attribution",
            normalized_guide,
        )
        self.assertIn("cardinality preflight", normalized_skill)
        self.assertIn("100 attributed metric series", normalized_skill)
        self.assertIn("display at most 20 ranked result rows", normalized_skill)
        self.assertIn("never present a truncated subset", normalized_skill)
        self.assertIn("not a global ranking", normalized_guide)
        self.assertIn(
            "explicit result-display limit may reduce the 20-row ceiling but never raise it",
            normalized_guide,
        )

    def test_query_report_is_signal_aware_and_stably_ordered(self) -> None:
        skill = self.read(QUERY / "SKILL.md")
        report = self.read(REPORT)
        normalized_report = " ".join(report.split())

        for heading in (
            "### Outcome",
            "### Query scope",
            "### Results",
            "### Coverage and access",
            "### Method and limitations",
        ):
            self.assertIn(heading, report)

        self.assertIn("## Compact Template", report)
        self.assertIn("| Item | Value |", report)
        self.assertIn("| Status | Count | Notes |", report)
        self.assertIn(
            "requested field is also the primary sort key",
            normalized_report,
        )
        self.assertIn("Sort problem-first", normalized_report)
        self.assertIn("Break ties with", normalized_report)
        self.assertIn("Display at most 20 rows", normalized_report)
        self.assertIn("minimum over the pinned window", normalized_report)
        self.assertIn("average over the pinned window", normalized_report)
        self.assertIn("maximum over the pinned window", normalized_report)
        self.assertIn(
            "Never average, minimize, or maximize a raw",
            normalized_report,
        )
        self.assertIn("Use window `increase`", normalized_report)
        self.assertIn("Use a derived `rate`", normalized_report)
        self.assertIn(
            "exact first seen when a supported bounded method",
            normalized_report,
        )
        self.assertIn(
            "exact last seen under the same condition",
            normalized_report,
        )
        self.assertIn(
            "Do not retrieve or return raw log bodies merely to satisfy first/last fields",
            normalized_report,
        )
        self.assertIn(
            "omit those fields and state the limitation",
            normalized_report,
        )
        self.assertIn("Never average log text", normalized_report)
        self.assertIn(
            "minimum, average, and maximum duration",
            normalized_report,
        )
        self.assertIn("p95 duration", normalized_report)
        self.assertIn(
            "Do not label an approximate quantile as exact",
            normalized_report,
        )
        self.assertIn(
            "disclose its approximation or error semantics instead of calling it exact",
            " ".join(self.read(QUERY / "references" / "query-guide.md").split()),
        )
        self.assertIn(
            "TraceQL over-time functions aggregate per query step",
            normalized_report,
        )
        self.assertIn(
            "derive full-window duration statistics from the bounded 20-summary fallback subset",
            normalized_report,
        )
        self.assertIn("without fabricated statistics", normalized_report)
        self.assertIn(
            "Keep coverage and authorization evidence separate",
            normalized_report,
        )
        self.assertIn("references/result-reporting.md", skill)

    def test_query_covers_metrics_logs_and_guarded_traces(self) -> None:
        skill = self.read(QUERY / "SKILL.md")
        guide = self.read(QUERY / "references" / "query-guide.md")

        self.assertIn("native Prometheus tools", skill)
        self.assertIn("native Loki tools", skill)
        self.assertIn("`tempo_*` proxied tools", skill)
        self.assertIn("GET-only `grafana_api_request`", skill)
        self.assertIn("/api/v2/traces/<trace-id>", skill)
        self.assertIn("/api/v1` datasource-proxy paths", skill)
        self.assertIn("/loki/api/v1` datasource-proxy paths", skill)
        self.assertIn("caller-supplied endpoints", skill)
        self.assertIn("Never fall back after a 401/403", skill)
        self.assertIn("Failure And Fallback Matrix", guide)
        self.assertIn("switch credentials", guide)

    def test_root_catalog_and_evals_match_the_split(self) -> None:
        readme_path = ROOT / "README.md"
        installer_evals = self.read(INSTALLER / "evals" / "process-cases.md")
        query_evals = self.read(QUERY / "evals" / "process-cases.md")

        if readme_path.is_file():
            readme = self.read(readme_path)
            self.assertIn(
                "`install-grafana-mcp-for-nebius` | Explicit only",
                readme,
            )
            self.assertIn("`nebius-grafana-query` | Implicit allowed", readme)
            self.assertIn("default table displays up to 20 rows", readme)

        self.assertIn("route to `$nebius-grafana-query`", installer_evals)
        self.assertIn(
            "invoke `$install-grafana-mcp-for-nebius` explicitly", query_evals
        )
        self.assertIn(
            "Ask for authoritative tenant/project/resource scope", query_evals
        )
        self.assertIn("fail closed", query_evals)
        self.assertIn("`tempo_*`", query_evals)
        self.assertIn("bare “any” as existential", query_evals)
        self.assertIn("globally complete", query_evals)
        self.assertIn("hard 20-row display ceiling", query_evals)
        self.assertIn("approximation semantics", query_evals)
        self.assertIn("Do not average bucket averages or quantiles", query_evals)
        self.assertIn(
            "Do not derive or label duration statistics for the complete window",
            query_evals,
        )

    def test_query_skill_does_not_claim_installation_or_writes(self) -> None:
        query_description = self.read(QUERY / "SKILL.md").split("---", 2)[1]
        metadata = self.read(QUERY / "agents" / "openai.yaml")

        self.assertIn("Never install or configure MCP", query_description)
        self.assertNotIn("allow_implicit_invocation: false", metadata)
        self.assertNotIn("GRAFANA_SERVICE_ACCOUNT_TOKEN", metadata)
        self.assertNotIn("NEBIUS_IAM_TOKEN", metadata)


if __name__ == "__main__":
    unittest.main()
