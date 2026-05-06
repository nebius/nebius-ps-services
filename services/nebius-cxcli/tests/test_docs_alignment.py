from __future__ import annotations

from pathlib import Path

from nebius_cxcli.wizard_profiles import builtin_wizard_profile_names

REPO_ROOT = Path(__file__).resolve().parents[1]


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]


def test_readme_quick_start_uses_current_create_target_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = _section(readme, "## Quick Start Guide", "## Core Concepts")

    assert "nebius-cxcli create <deployments-root>" in quick_start
    assert "nebius-cxcli create <target-path>" not in quick_start


def test_readme_supporting_commands_include_current_quota_and_target_flags() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    supporting = _section(readme, "### Supporting Commands", "## Auth Workflow")

    assert "nebius-cxcli quota-request /path/to/config.yaml" in supporting
    assert (
        "`component`, `validate`, `validate-dashboards`, `quota-check`, "
        "`quota-request`, `render`, `bootstrap-ci`, `deploy`, `destroy`, `email`"
    ) in supporting
    assert "- `quota-request <config.yaml>`" in supporting

    common_flags = supporting.split("Common command flags:", maxsplit=1)[1]
    assert (
        "- `deploy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, "
        "`--skip-validations`, `--skip-validation`, `--target`, `--all-targets`"
    ) in common_flags
    assert (
        "- `flux apply`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, "
        "`--target`, `--all-targets`"
    ) in common_flags
    assert (
        "- `flux destroy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, "
        "`--yes`, `--target`, `--all-targets`"
    ) in common_flags
    assert (
        "- `flux bootstrap`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, "
        "`--target`, `--all-targets`"
    ) in common_flags


def test_docs_define_discover_and_bootstrap_ci_boundaries() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert (
        "Nebius API credentials/profile are required for commands that talk to Nebius APIs "
        "such as `validate`, `quota-check`, `quota-request`, `render`, `deploy`, and `auth`."
    ) in readme
    assert "deploy`, `discover`, and `auth`" not in readme
    assert (
        "`discover` is local git/filesystem discovery over readable project `config.yaml` files; "
        "it does not need Nebius API credentials."
    ) in readme
    assert (
        "Uses local git/filesystem discovery over readable project `config.yaml` files "
        "and does not call Nebius APIs."
    ) in design

    for text in (readme, design):
        assert "`NEBIUS_DISCOVER_TARGET: .`" in text
        assert "`*/*/generated/**`" in text


def test_docs_define_auth_target_modes() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert (
        "`--project-config <config.yaml>` resolves both `project_id` and `client_name` "
        "from the config and must not be combined with `--project-id` or `--client-name`."
    ) in readme
    assert (
        "`--project-id <id>` is the manual target mode; use `--client-name <name>` "
        "when creating or when the project id cannot be mapped to exactly one cached profile."
    ) in readme
    assert "Omitting both target options is valid only for global `--validate-profile`" in readme
    assert (
        "Targets either `--project-config <config.yaml>` or `--project-id`; "
        "`--client-name` belongs only to the manual `--project-id` path."
    ) in design


def test_docs_define_destroy_as_project_wide_destructive_teardown() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert (
        "`destroy` is the project-wide destructive path: it tears down all rendered "
        "resources represented by that generated bundle/runtime snapshot"
    ) in readme
    assert (
        "`destroy` is the destructive inverse of `deploy` and is intentionally project-wide"
        in readme
    )
    assert (
        "Destroys all rendered project resources represented by the existing generated bundle"
        in design
    )
    assert "### `destroy <config.yaml>`" in design
    assert "Project-wide destructive teardown from the generated bundle" in design


def test_design_uses_config_yaml_for_project_runtime_command_headings() -> None:
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert "### `deploy <config.yaml>`" in design
    assert "### `destroy <config.yaml>`" in design
    assert "`deploy <config-path>`" not in design
    assert "`destroy <config-path>`" not in design


def test_docs_define_app_instance_id_as_cluster_binding() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert (
        "For app rows, `id` names the chart type and `instance_id` names the chart instance"
        in readme
    )
    assert "`nvidia-gpu-operator@cluster2`" in readme
    assert (
        "Authored `config.yaml` does not use `apps.charts[].target_ref`; any internal generated "
        "`target_ref` is derived from and must equal the same target `instance_id`."
    ) in readme
    assert "target-bound app rows use the target id as `instance_id`" in design
    assert (
        "Internal generated rows may also carry `target_ref`, but that field is a derived "
        "runtime alias for the same target `instance_id`, not a second user-facing binding."
    ) in design
    assert "`infra.components[]`: `id`, `instance_id`, `enabled`, `inputs`" in design


def test_docs_define_component_selector_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert "`component add <config.yaml> [component-selector...]`" in design
    assert "`component remove <config.yaml> [component-selector...]`" in design
    assert (
        "`<component-id>`, `infra:<component-id>`, `apps:<component-id>`, `all`, `none`" in readme
    )
    assert "`<instance-id>`, or `<component-id>@<instance-id>`" in readme
    assert "becomes `apps.charts[].instance_id`" in readme
    assert "removes app chart rows and `deploy.targets[]` settings" in design


def test_docs_define_validation_command_boundaries() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert "- `validate-sources`" in readme
    assert "active `component_sources.yaml` catalog" in readme
    assert "- `validate <config.yaml>`" in readme
    assert "project config contract and deployment-readiness shape" in readme
    assert "Use `--target <instance-id>` to validate one target-scoped Grafana row" in readme
    assert "each target must resolve an explicit kube context" in readme
    assert "accepted only when its generated Nebius name matches that target" in readme
    assert "Use `--target <target_ref>`" not in readme
    assert "- `validate-generated <generated-dir>`" in readme
    assert "existing generated bundle without rerendering it" in readme
    assert (
        "strict readiness, MK8s preflight, backend auth/bootstrap, "
        "live quota/capacity, Terraform validation"
    ) in readme
    assert "### `validate-sources [component_sources.yaml]`" in design
    assert "### `validate <config.yaml>`" in design
    assert "Supports `--target <instance-id>` for multi-target configs." in design
    assert "Target-scoped rows must resolve an explicit kube context" in design
    assert "current kubeconfig context is accepted only when its generated Nebius name" in (
        " ".join(design.split())
    )
    assert "### `validate-generated <generated-path>`" in design
    assert (
        "strict readiness, MK8s preflight, backend auth preparation, "
        "live quota/capacity, Terraform validation"
    ) in design


def test_docs_define_target_scoped_deploy_validation_report_filtering() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert (
        "When a run selects one target with `--target <instance-id>`, the refreshed "
        "validation section is scoped to that selected target instead of marking "
        "unselected target validations as not run; `--all-targets` reports every "
        "selected target."
    ) in readme
    assert (
        "When a run selects one target with `--target <instance-id>`, the refreshed "
        "validation section includes only that target's validations; `--all-targets` "
        "reports every selected target."
    ) in design


def test_docs_define_mysterybox_eso_name_resolution_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    for text in (readme, design):
        assert "Secret names are rejected" not in text
        assert "secret names are rejected" not in text
        assert "`secret_name`" in text
        assert "Terraform-created `mbsec-...` ID" in text or "Terraform `secret_ids` output" in text
        assert "`mysterybox_instance_id`" in text
        assert "externally managed MysteryBox Secrets" in text


def test_docs_define_wizard_string_lists_as_comma_separated() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert "`list(string)` / `set(string)`" in readme
    assert "`ns1,ns2`" in readme
    assert "simple string lists prompt for comma-separated values" in design
    assert "simple `list(string)` prompts use comma-separated input" in design


def test_docs_list_all_builtin_wizard_profiles_and_static_sources() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    design_profile_list = _section(
        design,
        "Built-in infra `wizard_profile` names currently include:",
        "Component output and handoff contract:",
    )

    for profile_name in builtin_wizard_profile_names():
        assert f"`{profile_name}`" in readme
        assert f"`{profile_name}`" in design_profile_list

    for text in (readme, design):
        assert "`wizard.<field>.options`" in text
        assert "`wizard.<field>.sources`" in text
        assert "`source: static`" in text
        assert "`{value, label}`" in text


def test_design_documents_grafana_dashboard_binding_workflow() -> None:
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    normalized_design = " ".join(design.split())

    assert "- [Grafana Dashboards](#grafana-dashboards)" in design
    assert "### Grafana Dashboards" in design
    assert "The bundled Grafana contract is the binding chain" in design
    assert "`observability.endpoints.read.<key>` declares a Nebius read endpoint" in design
    assert "`components.apps.grafana.cli.datasources.<id>` binds" in design
    assert (
        "`components.apps.grafana.defaults.values.dashboards.<folder>.<dashboard>`"
        in design
    )
    assert "`components.apps.grafana.cli.dashboard_signals.<signal>` is not" in design
    assert "components.apps.grafana.cli.grafana" not in design
    assert "Dashboard source materialization workflow:" in design
    assert "does not dynamically generate or rewrite dashboards" in design
    assert "It does not mutate, regenerate, or repair dashboard JSON." in normalized_design
    assert "Dashboard generation and materialization workflow:" not in design
    assert "`load_component_sources()` resolves the `json_file` relative" in design
    assert "`validate-dashboards <config.yaml>` checks the live post-deploy state" in design
    assert "Current bundled package dashboards:" in design
    assert "generated `deploy-report.md` bundled-dashboard list" in design
    assert "`kubernetes_io_hostname`" in design
    assert "`k8s_namespace_name` plus `k8s_pod_name`" in design
    assert "Live fit validation rules:" in design


def test_design_supporting_commands_include_quota_request_and_flux_targets() -> None:
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    supporting = _section(design, "## Supporting Commands", "## Idempotency Rules")

    assert "- `quota-request <config.yaml>`" in supporting
    assert "`QuotaAllowance` reads separate from `QuotaRequest` submission" in supporting
    assert "- `flux apply <generated-path>`" in supporting
    assert "- `flux destroy <generated-path>`" in supporting
    assert "- `flux bootstrap <generated-path>`" in supporting
    assert "--target <instance-id>` / `--all-targets`" in supporting


def test_customer_docs_use_exact_generated_bundle_wrapper_commands() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    for text in (readme, design):
        assert "terraform plan/apply" not in text
        assert "nebius-cxcli terraform plan/apply" not in text

    assert "`nebius-cxcli terraform plan <generated>`" in readme
    assert "`nebius-cxcli terraform apply <generated>`" in readme
    assert "`nebius-cxcli deploy <config.yaml>`" in readme
    assert "`nebius-cxcli terraform plan` and `nebius-cxcli terraform apply`" in design
