from __future__ import annotations

import re
from pathlib import Path

from nebius_cxcli.wizard_profiles import builtin_wizard_profile_names

REPO_ROOT = Path(__file__).resolve().parents[1]
MONOREPO_ROOT = REPO_ROOT.parents[1]


def _section(text: str, start: str, end: str) -> str:
    return text.split(start, maxsplit=1)[1].split(end, maxsplit=1)[0]


def _squash(text: str) -> str:
    return " ".join(text.split())


def test_readme_quick_start_uses_current_create_target_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    quick_start = _section(readme, "## Quick Start Guide", "## Core Concepts")

    assert "nebius-cxcli create <deployments-root>" in quick_start
    assert "nebius-cxcli create <target-path>" not in quick_start
    assert "creates it before writing the tenant/project scaffold" in readme


def test_design_architecture_summary_matches_upgrade_surface() -> None:
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    architecture = _section(design, "## Architecture Summary", "## How Flux Works")
    architecture_flat = _squash(architecture)
    design_flat = _squash(design)

    assert (
        "node-template rolling updates for Kubernetes version, OS image, and Nebius-image GPU stack"
        in architecture_flat
    )
    legacy_scope_label = "V" + "1"
    assert legacy_scope_label not in architecture_flat
    assert (
        "explicit MK8s node-group migration, and non-Soperator target-scoped Helm chart upgrades"
        in architecture_flat
    )
    assert "later GPU stack, platform, hardware preset, and app/chart upgrades" not in (
        architecture_flat
    )
    assert "managed `soperator upgrade`" in design_flat
    assert "Standalone `soperator backup` / `soperator restore`" in design_flat
    assert "external backup can use an accepted onboarded target or run before onboarding" in (
        design_flat
    )
    assert "external-soperator-backup-" in design_flat
    assert "restore-ready YAML for namespaced in-cluster material" in design_flat
    assert "Restore is archive-driven and dry-run by default, and it is DR/new-empty-target only" in (
        design_flat
    )
    assert "It is not same-cluster rollback" in design_flat
    assert "operators must not point restore at the original/source cluster" in design_flat
    assert (
        "fails fast for `apps:soperator@<target>` with the canonical `soperator upgrade` command"
        in (design_flat)
    )
    assert "pending ActiveChecks restore is still completed" in design_flat
    assert "External Soperator adoption, storage/compute remediation" in design_flat


def test_readme_documents_redacted_guided_create_prefill_example() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    flat = _squash(readme)

    assert (
        "nebius-cxcli create /path/to/deployments-root --client-name client-slug "
        "--tenant-id TENANT_ID --project-id PROJECT_ID "
        "--infra mk8s,vm,wireguard-gw,ssh-jumphost "
        "--no-validate-sources --no-validate-config"
    ) in flat
    assert "still runs the wizard for remaining prompts such as region" in flat
    assert (
        "These flags skip source validation and post-write config validation; "
        "they do not skip the warning-only live quota/capacity assessment."
    ) in flat
    assert "Add `--app none` when you also want to skip the app-selection prompt." in flat
    assert "`--infra` and `--app` can each be repeated or passed as comma-separated lists" in flat
    assert "`--infra mk8s,vm --infra wireguard-gw,ssh-jumphost`" in readme
    assert "`--app n8n,gateway-helm --app cert-manager`" in readme
    assert "# Guided create with multiple infra and app choices preselected" in readme
    assert (
        "--infra mk8s,vm" in readme
        and "--infra wireguard-gw,ssh-jumphost" in readme
        and "--app n8n,gateway-helm" in readme
        and "--app cert-manager" in readme
    )
    assert "App chart dependencies can still add required chart rows automatically." in flat
    assert (
        "`release.namespace`, `release.name`: default Helm namespace and release name "
        "used during `create` and `component add`."
    ) in readme
    assert (
        "`release.namespace` and `release.name` are the default Helm namespace and "
        "release name used by `create` and `component add`."
    ) in readme
    assert re.search(r"\btenant-[a-z0-9]{16,}\b", readme) is None
    assert re.search(r"\bproject-[a-z0-9]{16,}\b", readme) is None


def test_readme_render_warning_lives_in_recommended_workflow() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    intro = readme.split("## Table of Contents", maxsplit=1)[0]
    workflow = _section(readme, "## Recommended Workflow", "## Upgrade")
    workflow_flat = _squash(workflow)

    warning = "After any manual or wizard change to `config.yaml`, run"
    assert warning not in intro
    assert warning in workflow
    assert "`nebius-cxcli render <config.yaml>` again before" in workflow
    assert "`nebius-cxcli terraform plan`" in workflow
    assert "`nebius-cxcli flux bootstrap`" in workflow
    assert "Passing `config.yaml` to `deploy` only locates the" in workflow
    assert "`deploy` runs generated-bundle preflight and Terraform validation before" in workflow
    assert "it does not rerender `config.yaml`" in workflow
    assert "terminal output prints a copy-paste deploy helper" in workflow_flat
    assert "`Next step: deploy the rendered bundle:`" in workflow_flat
    assert "colored `nebius-cxcli deploy <config.yaml>` command line" in workflow_flat
    assert "terraform validate` after render" not in readme


def test_docs_document_managed_tool_checksum_verification() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    combined = _squash(f"{readme}\n{design}")

    assert "Managed Terraform downloads verify HashiCorp's published SHA256 manifest" in readme
    assert "Managed Flux downloads verify the published Flux checksum manifest" in readme
    assert "Managed downloads verify the official release SHA256 manifest" in readme
    assert (
        "Managed Terraform and Flux downloads verify the official release SHA256 manifest "
        "before installation"
    ) in combined
    assert "local checksum sidecar still matches the binary" in combined


def test_readme_mk8s_gpu_workload_validation_defaults_include_soperator() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    readme_flat = _squash(readme)
    design_flat = _squash(design)
    acceptance = _section(readme, "## Acceptance Testing", "## Soperator Commands")
    acceptance_flat = _squash(acceptance)

    assert (
        "GPU visibility is enabled by default for GPU-backed MK8s deploys, including Soperator production targets"
        in readme
    )
    assert (
        "MK8s node inventory smoke is required for every MK8s deploy target as a fast read-only all-node gate"
        in readme
    )
    assert "minimum expected Ready GPU node counts" in readme
    assert "groups node details by node group in the JSON detail report" in readme
    assert "Rendered MK8s node groups carry the canonical `nebius.com/node-group` label" in readme
    assert (
        "NCCL settings are command-only benchmark settings for explicit `nebius-cxcli acceptance-test benchmark` runs"
        in readme
    )
    assert "See [Acceptance Testing](#acceptance-testing)" in readme
    assert "### Smoke Tests" in acceptance
    assert "### Benchmark Tests" in acceptance
    assert "### NCCL Suite Selection" in acceptance
    assert "acceptance-test smoke" in acceptance
    assert "acceptance-test benchmark" in acceptance
    assert "--suite k8s-cuda" in acceptance
    assert "--suite slurm" in acceptance
    assert "--suite k8s-nccl" in acceptance
    assert "--suite slurm-nccl" in acceptance
    assert "schedules CUDA validation pods across every currently" in acceptance
    assert "scheduler-free Ready GPU node" in acceptance
    assert "bounded CUDA validation pods" not in acceptance
    assert "Smoke commands require `--suite`" in acceptance
    assert "omitted `--suite` fails fast" in acceptance
    assert "`--suite soperator-nccl`" not in acceptance
    assert "temporary `MPIJob`" in acceptance
    assert "Soperator login pod" in acceptance
    assert "Slurm allocation" in acceptance
    assert "do not read Terraform state or initialize the Terraform backend" in acceptance
    assert "Common benchmark commands:" in acceptance
    assert "nebius-cxcli acceptance-test benchmark <config.yaml> --suite k8s-nccl" in acceptance
    assert "nebius-cxcli acceptance-test benchmark <config.yaml> --suite slurm-nccl" in acceptance
    assert (
        "nebius-cxcli acceptance-test benchmark <config.yaml> --target mk8s-prod "
        "--suite k8s-nccl --max-nodes 4 --timeout 20m "
        "--average-bus-bandwidth-threshold-gbps 300"
    ) in acceptance
    assert (
        "nebius-cxcli acceptance-test benchmark <config.yaml> --target sop-cluster1 "
        "--suite slurm-nccl"
    ) in acceptance
    assert (
        "nebius-cxcli acceptance-test benchmark <config.yaml> --target sop-cluster1 "
        "--suite slurm-nccl --max-nodes 2 --timeout 5m "
        "--average-bus-bandwidth-threshold-gbps 300"
    ) in acceptance
    assert "Benchmark commands require `--suite`" in acceptance_flat
    assert "fails fast instead of choosing a K8s suite" in acceptance_flat
    assert "every generated target, equivalent to `--all-targets`" in acceptance
    assert "all schedulable GPU nodes, equivalent to omitting `--max-nodes`" in acceptance
    assert "no cxcli benchmark timeout" in acceptance
    assert "`acceptance-smoke-report-<target>.json`" in acceptance
    assert "`acceptance-benchmark-report-<target>.json`" in acceptance
    assert "one concise result line with `PASSED`, `FAILED`, or `SKIPPED`" in acceptance_flat
    assert (
        "the suite scope, target, and the most relevant summary or skip reason" in acceptance_flat
    )
    assert "elapsed time in `hh:mm:ss`" in acceptance_flat
    assert "`elapsed_seconds`" in acceptance
    assert "`elapsed_time`" in acceptance
    assert "green for `PASSED`, red for `FAILED`, yellow for `SKIPPED`" in acceptance_flat
    assert "default-color labels, summaries, skip reasons, and elapsed times stay unbolded" in (
        acceptance_flat
    )
    assert "`k8s-nccl` and `slurm-nccl` both run NCCL `all_reduce_perf`" in acceptance
    assert "Socket/TCPIP transport for Ethernet-only shapes" in acceptance
    assert "RDMA transport for GPU-cluster / InfiniBand shapes" in acceptance
    assert "below-threshold average bandwidth is recorded as a comment" in acceptance
    assert "runs `/usr/bin/all_reduce_perf_mpi`" in acceptance
    assert (
        "multiple idle one-GPU nodes run a multi-node NCCL benchmark capped at a 2G message size"
    ) in acceptance
    assert "one total GPU runs a launch/smoke check" in acceptance
    assert "Slurm-level NCCL evidence for a Soperator target" in acceptance_flat
    assert "soperator-nccl" not in acceptance
    assert "Smoke tests answer whether" in acceptance_flat
    assert "Benchmark tests answer whether" in acceptance_flat
    assert "On Ethernet-only and 1-GPU shapes" in readme
    assert "Soperator ActiveChecks remain opt-in benchmark/diagnostic workloads" in readme
    assert "NCCL is a separate acceptance benchmark, not deploy smoke" in design
    assert "Slurm NCCL prefers 8-GPU Slurm nodes when available" in design
    assert (
        "multiple idle one-GPU Slurm nodes run a multi-node NCCL benchmark capped at "
        "a 2G message size"
    ) in design
    assert "below-threshold bandwidth is recorded as an informational report comment" in design
    assert (
        "Acceptance-test terminal output prints a concise `PASSED`, `FAILED`, or `SKIPPED` result line"
        in design
    )
    assert "elapsed duration as `elapsed_seconds` and `elapsed_time`" in design
    assert "formatted `elapsed_time` value in `hh:mm:ss`" in design
    assert "color-capable terminals render `PASSED` green, `FAILED` red, `SKIPPED` yellow" in design
    assert (
        "default-color labels, summaries, skip reasons, and elapsed times stay unbolded" in design
    )
    assert "Render materializes `nebius.com/node-group` on each MK8s node group" in design
    assert "grouped node details" in design
    assert "minimum expected Ready GPU node counts" in design
    assert (
        "required read-only all-node Kubernetes inventory gate generated for every MK8s target"
        in design
    )
    assert "NCCL is not configured in `config.yaml`" in design_flat
    assert "CUDA smoke pods use the cxcli-owned `cuda-smoke-validation`" in readme
    assert "`cuda-smoke-validation` ServiceAccount with token automount disabled" in design
    assert "leave that generic NCCL path off" not in readme
    assert "Soperator targets suppress this generic workload prompt" not in readme
    assert "Soperator targets suppress the generic deploy-time NCCL workload" not in readme
    assert (
        "generic MK8s NCCL validation remains the default deploy-time workload check" not in readme
    )
    assert "internal to the deploy-time validation runner" not in readme
    assert "records NCCL as skipped in the JSON report and `deploy-report.md`" not in readme
    assert "internal to the deploy-time validation runner" not in design
    assert (
        "run the benchmark explicitly with `nebius-cxcli acceptance-test benchmark`" in readme_flat
    )
    assert "they do not read Terraform state or initialize the Terraform backend" in readme_flat
    assert (
        "It is selected through `nebius-cxcli acceptance-test benchmark --suite ...`" in design_flat
    )
    assert "omitted `--suite` fails fast instead of defaulting to the K8s NCCL suite" in design_flat
    assert "they do not read Terraform state or initialize the Terraform backend" in design_flat


def test_readme_features_include_concise_grafana_command_summary() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    features = _section(readme, "## Features", "## Prerequisites and Installation")

    assert (
        "- `grafana` exports or normalizes dashboard JSON and can attach "
        "deploy-ready imports to `component_sources.yaml`."
    ) in features
    assert "grafana.nebius.dev" not in features
    assert "--catalog-folder" not in features


def test_readme_supporting_commands_include_current_quota_and_target_flags() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    supporting = _section(readme, "### Supporting Commands", "## Auth Workflow")

    assert "nebius-cxcli quota-request /path/to/config.yaml" in supporting
    assert (
        "nebius-cxcli grafana --export-dashboard https://grafana.example.invalid/ "
        "--folder-uid folder-uid --dashboard-uid dashboard-uid "
        '--dashboard-folder mk8s --datasource "Nebius User Metrics" --attach'
    ) in supporting
    assert (
        "nebius-cxcli grafana --dashboard-json ./dashboards/mk8s/custom.json "
        '--dashboard-folder mk8s --datasource "Nebius User Metrics" --attach'
    ) in supporting
    assert (
        "`component`, `validate`, `validate-dashboards`, `quota-check`, "
        "`quota-request`, `render`, `deploy`, `soperator`, `upgrade`, "
        "`bootstrap-ci`, `wireguard`, `ssh-jumphost`, `destroy`, `email`"
    ) in supporting
    assert (
        "- `grafana`: no positional path; use `--export-dashboard <grafana-base-or-folder-url>` "
        "or `--dashboard-json <path>` and optional `--component-sources` with `--attach`."
    ) in supporting
    assert (
        "- `grafana --export-dashboard <grafana-base-or-folder-url>` / "
        "`grafana --dashboard-json <path>`"
    ) in supporting
    assert "- `quota-request <config.yaml>`" in supporting
    assert "See [Upgrade](#upgrade)" in supporting
    assert "drain-timeout defaults" in supporting
    assert "- `acceptance-test smoke <config.yaml>`" in supporting
    assert "Runs explicit post-deploy acceptance smoke suites" in supporting
    assert (
        "Requires `--suite`; omitted `--suite` fails fast instead of choosing a K8s or Slurm suite"
        in supporting
    )
    assert "Use `--suite slurm` for Slurm all-node smoke" in supporting
    assert (
        "After a suite is selected, defaults to every generated target when `--target` is omitted"
        in supporting
    )
    assert "Runs explicit post-deploy benchmark suites" in supporting
    assert "Requires `--suite`; omitted `--suite` fails fast" in supporting
    assert "Use `--suite k8s-nccl` for the Kubernetes NCCL benchmark" in supporting
    assert "`--suite slurm-nccl` for the Slurm NCCL benchmark" in supporting
    assert "--max-nodes 4 --timeout 20m --average-bus-bandwidth-threshold-gbps 300" in supporting

    common_flags = supporting.split("Common command flags:", maxsplit=1)[1]
    common_flags_flat = _squash(common_flags)
    assert (
        "- `component add`: `--no-interactive`, `--app-namespace`, "
        "`--app-releasename`, `--app-version`, `--network-id`, `--subnet-id`, "
        "`--network-ref`, `--subnet-ref`, "
        "`--validate-sources/--no-validate-sources`"
    ) in common_flags_flat
    assert (
        "- `create`:\n  `--client-name`, `--tenant-id`, `--project-id`, `--region-id`, "
        "`--email`, `--infra`, `--app`, `--app-namespace`, `--app-releasename`, "
        "`--app-version`, `--network-id`, `--subnet-id`, `--network-ref`, "
        "`--subnet-ref`, `--validate-sources/--no-validate-sources`, "
        "`--validate-config/--no-validate-config`, `--no-interactive`, `--force`"
    ) in common_flags
    assert (
        "- `deploy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, "
        "`--skip-validations`, `--skip-validation`, `--target`, `--all-targets`, "
        "`--job-policy`, `--cancel-job`, `--requeue-job`, `--job-wait-timeout`, "
        "`--job-refresh-interval`"
    ) in common_flags_flat
    assert (
        "- `acceptance-test smoke`: `--target`, `--all-targets`, `--suite`,\n"
        "  `--batch-size`, `--concurrency`,\n"
        "  `--continue-on-failure/--fail-fast`"
    ) in common_flags
    assert (
        "- `acceptance-test benchmark`: `--target`, `--all-targets`, `--suite`,\n"
        "  `--continue-on-failure/--fail-fast`,\n"
        "  `--max-nodes`, `--timeout`, `--average-bus-bandwidth-threshold-gbps`"
    ) in common_flags
    assert (
        "- `upgrade node-template`: `--to-version`, `--to-os`, "
        "`--to-gpu-stack-preset`, `--node-group`, `--dry-run`, "
        "`--strategy`, `--strategy-max-surge-count`, `--drain-timeout`, "
        "`--auto-auth-bootstrap/--no-auto-auth-bootstrap`, "
        "`--skip-validations`, `--skip-validation`, `--interactive/--no-interactive`"
    ) in common_flags
    assert (
        "- `upgrade node-group`: `--node-group`, `--to-platform`, "
        "`--to-preset`, `--to-os`, `--to-gpu-stack-preset`, `--to-fabric`, "
        "`--dry-run/--execute`, `--approve/--no-approve`"
    ) in common_flags
    assert (
        "- `soperator backup`: `--target`, `--backup-dir`, `--namespace`, "
        "`--release-name`, `--kube-context`, `--dry-run`, "
        "`--interactive/--no-interactive`"
    ) in common_flags_flat
    assert (
        "- `soperator discover`: `--target`, `--output-dir`, `--namespace`, "
        "`--release-name`, `--kube-context`, `--to-chart-version`, "
        "`--to-k8s-version`, `--to-os`, `--to-gpu-stack-preset`, "
        "`--redaction`, `--interactive/--no-interactive`"
    ) in common_flags_flat
    assert (
        "- `soperator restore`: `--target`, `--namespace`, `--kube-context`, "
        "`--dry-run/--execute`, `--approve/--no-approve`, "
        "`--restore-accounting-db/--no-restore-accounting-db`"
    ) in common_flags_flat
    assert "Node-layer upgrades" not in common_flags
    assert "--disruption-policy" not in supporting
    assert "allow-unavailable" not in supporting
    assert (
        "- `soperator upgrade`: `--target`, `--to-chart-version`, `--to-k8s-version`, "
        "`--to-os`, `--to-gpu-stack-preset`, `--node-group`, `--strategy`, "
        "`--strategy-max-surge-count`, `--drain-timeout`, `--backup-dir`, "
        "`--job-policy`, `--cancel-job`, `--requeue-job`, `--job-wait-timeout`, "
        "`--job-refresh-interval`, `--dry-run`, "
        "`--approve-remediation/--no-approve-remediation`, "
        "`--allow-unsupported-soperator-upgrade-path`, "
        "`--interactive/--no-interactive`"
    ) in common_flags_flat
    assert (
        "- `ext-soperator backup`: `--client-name`, `--tenant-id`, `--project-id`, "
        "`--target`, `--backup-dir`, `--namespace`, `--release-name`, "
        "`--kube-context`, `--cluster-id`, `--access`, `--dry-run`"
    ) in common_flags_flat
    assert (
        "- `ext-soperator restore`: `--target`, `--namespace`, `--kube-context`, "
        "`--dry-run/--execute`, `--approve/--no-approve`, "
        "`--restore-accounting-db/--no-restore-accounting-db`"
    ) in common_flags_flat
    assert (
        "- `ext-soperator discover`: `--client-name`, `--tenant-id`, `--project-id`, "
        "`--target`, `--output-dir`, `--namespace`, `--release-name`, "
        "`--kube-context`, `--cluster-id`, `--access`, "
        "`--to-chart-version`, `--to-k8s-version`, `--to-os`, "
        "`--to-gpu-stack-preset`, `--redaction`"
    ) in common_flags_flat
    assert (
        "- `ext-soperator onboard`: `--client-name`, `--tenant-id`, `--project-id`, "
        "`--region-id`, `--email`, `--cluster-id`, `--target-id`, "
        "`--kube-context`, `--access`, `--storage-mode`, `--compute-mode`, "
        "`--to-chart-version`, `--to-k8s-version`, `--source-version`, "
        "`--allow-unsupported-soperator-upgrade-path`, `--worker-rollout-strategy`, "
        "`--worker-wave-groups`, `--worker-wave-percent`, `--max-parallel-worker-groups`, "
        "`--strategy-max-surge-count`, `--strategy-max-unavailable-count`, "
        "`--strategy-drain-timeout`, `--validate-sources/--no-validate-sources`, "
        "`--no-interactive`"
    ) in common_flags_flat
    assert (
        "- `ext-soperator upgrade`: `--target`, `--backup-dir`, `--job-policy`, "
        "`--cancel-job`, `--requeue-job`, `--job-wait-timeout`, `--job-refresh-interval`, "
        "`--dry-run/--execute`, `--approve/--no-approve`, "
        "`--approve-remediation/--no-approve-remediation`, "
        "`--allow-unsupported-soperator-upgrade-path`, "
        "`--interactive/--no-interactive`, `--worker-rollout-strategy`, "
        "`--worker-wave-groups`, `--worker-wave-percent`, "
        "`--max-parallel-worker-groups`, `--strategy-max-surge-count`, "
        "`--strategy-max-unavailable-count`, `--strategy-drain-timeout`"
    ) in common_flags_flat
    assert (
        "squeue --states=PD -h -o '%A|%r' | awk -F'|' "
        "'$2 == \"JobHeldAdmin\" { print $1 }' | xargs -r scontrol release"
    ) in readme
    assert (
        "- `upgrade helm-chart`: `--to-version`, `--dry-run`, "
        "`--interactive/--no-interactive` (non-Soperator app charts only)"
    ) in common_flags_flat
    assert (
        "- `grafana`: `--export-dashboard`, `--dashboard-json`, `--output-dir`, `--folder-uid`, "
        "`--dashboard-uid`, `--overwrite`, `--attach`, `--component-sources`, "
        "`--dashboard-folder`, `--datasource`, `--token-env`, `--username`, "
        "`--password-env`"
    ) in common_flags
    assert (
        "- `flux apply`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, "
        "`--target`, `--all-targets`, `--job-policy`, `--cancel-job`, "
        "`--requeue-job`, `--job-wait-timeout`, `--job-refresh-interval`"
    ) in common_flags_flat
    assert (
        "- `flux destroy`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, "
        "`--yes`, `--target`, `--all-targets`"
    ) in common_flags
    assert (
        "- `flux bootstrap`: `--auto-auth-bootstrap/--no-auto-auth-bootstrap`, "
        "`--target`, `--all-targets`"
    ) in common_flags


def test_readme_upgrade_section_is_visible_and_consolidated() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    toc = _section(readme, "## Table of Contents", "## Quick Start Guide")
    quick_start = _section(readme, "## Quick Start Guide", "## Core Concepts")
    soperator = _section(readme, "## Soperator Commands", "## Upgrade")
    soperator_flat = _squash(soperator)
    upgrade = _section(readme, "## Upgrade", "## Releases")
    upgrade_flat = _squash(upgrade)
    commands = _section(readme, "## Commands", "## Auth Workflow")
    supporting = _section(readme, "### Supporting Commands", "## Auth Workflow")
    unreleased = changelog.split("## [Unreleased]", maxsplit=1)[1].split("\n## [", maxsplit=1)[0]
    unreleased_flat = _squash(unreleased)

    assert "Use this guide by task:" in toc
    assert "| First project or local run | [Quick Start Guide](#quick-start-guide)" in toc
    assert (
        "| Existing Soperator cluster or Soperator upgrade | "
        "[Soperator Commands](#soperator-commands) |"
    ) in toc
    assert (
        "| Post-deploy smoke or benchmark validation | [Acceptance Testing](#acceptance-testing) |"
    ) in toc
    assert ("| Command flags and generated-bundle operations | [Commands](#commands) |") in toc
    assert "- [Recommended Workflow](#recommended-workflow)" in toc
    assert "- [Acceptance Testing](#acceptance-testing)" in toc
    assert "  - [Smoke Tests](#smoke-tests)" in toc
    assert "  - [Benchmark Tests](#benchmark-tests)" in toc
    assert "  - [NCCL Suite Selection](#nccl-suite-selection)" in toc
    assert "- [Soperator Commands](#soperator-commands)" in toc
    assert toc.index("- [Recommended Workflow](#recommended-workflow)") < toc.index(
        "- [Acceptance Testing](#acceptance-testing)"
    )
    assert toc.index("- [Acceptance Testing](#acceptance-testing)") < toc.index(
        "- [Soperator Commands](#soperator-commands)"
    )
    assert "  - [Soperator Command Map](#soperator-command-map)" in toc
    assert "  - [CXCLI Managed Soperator Clusters](#cxcli-managed-soperator-clusters)" in toc
    assert (
        "  - [Soperator Slurm Scheduling And Command Examples](#soperator-slurm-scheduling-and-command-examples)"
        in toc
    )
    assert "  - [External Soperator Onboarding](#external-soperator-onboarding)" in toc
    assert "  - [External Soperator Upgrade](#external-soperator-upgrade)" in toc
    assert "  - [Soperator Cluster Upgrade](#soperator-cluster-upgrade)" in toc
    assert "  - [Soperator Rules and Safety Checks](#soperator-rules-and-safety-checks)" in toc
    assert "- [Upgrade](#upgrade)" in toc
    assert "  - [When To Use upgrade](#when-to-use-upgrade)" in toc
    assert "  - [Upgrade Principles](#upgrade-principles)" in toc
    assert "  - [Node Template Upgrade](#node-template-upgrade)" in toc
    assert "  - [Node-Group Migration](#node-group-migration)" in toc
    assert "  - [Upgrade Strategies](#upgrade-strategies)" in toc
    assert "  - [Upgrade Examples](#upgrade-examples)" in toc
    assert "  - [Helm Chart Upgrades](#helm-chart-upgrades)" in toc
    assert "  - [Command Examples](#command-examples)" in toc
    assert "- [Examples](#examples)" not in toc
    catalog_toc = toc.split("- [Catalog File Reference](#catalog-file-reference)", maxsplit=1)[
        1
    ].split(
        "- [Recommended Workflow](#recommended-workflow)",
        maxsplit=1,
    )[0]
    assert "Soperator Slurm Scheduling" not in catalog_toc

    assert "### Soperator Command Map" in soperator
    assert "### CXCLI Managed Soperator Clusters" in soperator
    assert "### Soperator Slurm Scheduling And Command Examples" in soperator
    assert "### External Soperator Onboarding" in soperator
    assert "### External Soperator Upgrade" in soperator
    assert "### Soperator Cluster Upgrade" in soperator
    assert "### Soperator Rules and Safety Checks" in soperator
    assert "`nebius-cxcli soperator` is for Soperator app rows that cxcli already manages" in (
        soperator_flat
    )
    assert (
        "`soperator backup` and `soperator restore` create or apply restore-capable archives"
        in (soperator_flat)
    )
    assert "Restore is DR/new-empty-target only" in soperator_flat
    assert "not same-cluster rollback" in soperator_flat
    assert "must not target the original/source cluster or an existing Soperator namespace" in (
        soperator_flat
    )
    assert "`nebius-cxcli soperator upgrade`" in soperator
    assert "`nebius-cxcli ext-soperator` is for existing Nebius MK8s clusters" in (soperator_flat)
    assert "`ext-soperator backup` and `ext-soperator restore` use the same archive contract" in (
        soperator_flat
    )
    assert "external-soperator-backup-*.tar.gz" in soperator
    assert "For later full-cluster or chart-only upgrades of that cxcli-managed row, use" in (
        soperator_flat
    )
    assert (
        "It is separate from the Managed Soperator service exposed through the Nebius Console."
        in (soperator_flat)
    )
    assert "`create` builds a new cxcli-managed project" in soperator_flat
    assert (
        "finish a running `ext-soperator upgrade` or `soperator upgrade` from the same laptop"
        in soperator_flat
    )
    assert "same laptop, workdir, and operator account that started it" in soperator_flat
    assert ".nebius-cxcli/ext-soperator-upgrades/<target>/checkpoint.json" in soperator
    assert ".nebius-cxcli/soperator-upgrades/<target>/checkpoint.json" in soperator
    assert "these checkpoints stay local" in soperator_flat
    assert "After the final locked segment completes" in soperator_flat
    assert "`nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>`" in soperator
    assert (
        "`nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run`"
        in soperator
    )
    assert "`nebius-cxcli soperator backup <config.yaml> --target <target>`" in soperator
    assert "`nebius-cxcli soperator restore <backup.tar.gz> --execute --approve`" in (soperator)
    assert "nebius-cxcli ext-soperator backup <config.yaml> --target <target>" in soperator
    assert (
        "nebius-cxcli ext-soperator backup \\ --project-id <project-id> \\ "
        "--cluster-id <mk8scluster-id> \\ --access internal"
    ) in soperator_flat
    assert "`--access external` selects the public control-plane endpoint" in soperator_flat
    assert "`--access internal` selects the private endpoint" in soperator_flat
    assert "VPN or another private network path is already available" in soperator_flat
    assert "`--access` is rejected with standalone `--kube-context`" in soperator_flat
    assert (
        "`nebius-cxcli ext-soperator restore <backup.tar.gz> --kube-context <new-context> --execute --approve`"
        in soperator
    )
    assert "Do not point restore at the original/source cluster" in soperator_flat
    assert "restore is not an in-place rollback" in soperator_flat
    assert soperator.index("`nebius-cxcli soperator upgrade") < soperator.index(
        "`nebius-cxcli ext-soperator onboard"
    )
    assert soperator.index("### CXCLI Managed Soperator Clusters") < soperator.index(
        "### Soperator Slurm Scheduling And Command Examples"
    )
    assert soperator.index("### Soperator Slurm Scheduling And Command Examples") < (
        soperator.index("### External Soperator Onboarding")
    )
    assert "Read-only against live cluster state" in soperator
    assert "`--worker-rollout-strategy`, `--worker-wave-groups`" in soperator
    assert "non-interactive onboarding" in soperator
    assert "verifies the needed quota and capacity during `--execute` preflight" in (soperator_flat)
    assert "prints a color-highlighted sectioned plan covering target discovery" in (
        soperator_flat
    )
    assert "refuses deploy-owned/no-upgrade action sets with render/deploy guidance" in (soperator)
    assert (
        "`nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --execute --approve`"
        in soperator
    )
    assert (
        "`nebius-cxcli soperator upgrade <config.yaml> --target <target> "
        "[--to-chart-version <chart-version>] [--to-k8s-version <major.minor>] "
        "[--to-os <image>] [--to-gpu-stack-preset <preset>]`" in soperator
    )
    assert "Compatibility entry point for Soperator chart upgrades" not in soperator
    assert "`nebius-cxcli upgrade helm-chart <config.yaml> apps:soperator@<target>`" not in (
        soperator
    )
    assert "External onboarding is not a Terraform import." in soperator
    assert "Use `soperator upgrade` when cxcli already manages the Soperator app row" in (soperator)
    assert "If the existing Soperator row uses `repo: ''`" in soperator
    assert "oci://cr.eu-north1.nebius.cloud/e00th0mgv3zddz7468/charts/soperator" in soperator
    assert "published parent OCI package" in soperator
    assert "static post-Flux manifest" in soperator
    assert "Helm chart downgrades are not guaranteed safe" in soperator_flat
    assert "canonical cxcli-managed Soperator upgrade path is `soperator upgrade`" in (
        soperator_flat
    )
    assert "restore-capable backup" in soperator_flat
    assert "raw Kubernetes Secret restore material" in soperator_flat
    assert "chart-managed MariaDB Slurm accounting DB dump" in soperator_flat
    assert "restore-ready Kubernetes manifests" in soperator_flat
    assert "Deployments, StatefulSets, DaemonSets, CronJobs, RBAC" in soperator_flat
    assert "The restore command is dry-run by default and requires `--execute --approve`" in (
        soperator_flat
    )
    assert "does not run raw `kubectl drain`" in soperator_flat
    assert "soperator.upgrade.to_chart_version" in soperator_flat
    assert "active `component_sources.yaml` Soperator chart pin as the default target version" in (
        soperator_flat
    )
    assert "checkpointed maintenance-window lifecycle" in soperator_flat
    assert "cxcli-managed upgrade fails closed before the chart upgrade" in soperator_flat
    assert "generated/reports/soperator-upgrade-report.md" in soperator_flat
    assert "does not silently disable arbitrary live external ActiveChecks" in (soperator_flat)
    assert (
        "Use `ext-soperator onboard` plus `ext-soperator upgrade` when the source cluster is not"
        in soperator_flat
    )
    assert "A cxcli-managed Soperator cluster upgrade can involve the underlying MK8s" in (
        soperator_flat
    )
    assert "Use `nebius-cxcli soperator upgrade` as the canonical maintenance-window command" in (
        soperator_flat
    )
    assert (
        "For external Soperator clusters, start with onboarding instead of the Terraform-managed MK8s upgrade commands"
        in soperator_flat
    )
    assert "External upgrade follows these stages:" in soperator_flat
    assert "Plan and dry run: load `config.yaml`, read the accepted discovery bundle" in (
        soperator_flat
    )
    assert "Execute preflight: refresh live discovery, verify the source release" in (
        soperator_flat
    )
    assert "Validation hold: verify external MK8s control-plane and node-group readiness" in (
        soperator_flat
    )
    assert "Segment completion: write `ext-soperator-upgrade-report.md` and JSON" in (
        soperator_flat
    )
    assert "Final handoff: after the last locked segment reports `Pending phase: none`" in (
        soperator_flat
    )
    assert "For Kubernetes minor changes, run provider-supported hops" in soperator_flat
    assert "upgrade a managed cluster from `1.31` to `1.34` as" in soperator_flat
    assert "`1.31 -> 1.33` and `1.31 -> 1.34` requests" in soperator_flat
    assert "Managed upgrades do not persist a locked multi-run path" in soperator_flat
    assert "blocks the combined run and prints a chart-first command" in soperator_flat
    assert "Run the Soperator chart upgrade while Kubernetes stays at `1.32`" in (
        soperator_flat
    )
    assert "unsupported` and `not_validated` paths fail fast unless" in soperator_flat
    assert "`supported_with_warning` continue without the override" in soperator_flat
    assert "CXCLI-managed Soperator upgrade follows these stages:" in soperator_flat
    assert "Preflight and backup: validate the current bundle" in soperator_flat
    assert "Slurm and MK8s rollout: when MK8s target flags are supplied" in soperator_flat
    assert "operator-facing top-level stage (`MK8s Node Upgrades` or `Soperator Upgrade`)" in (
        soperator_flat
    )
    assert "Fast stage verification gates: after ActiveChecks suspension" in soperator_flat
    assert "post-MK8s validation, Soperator chart apply" in soperator_flat
    assert "final post-upgrade MK8s and Helm readiness checks" in soperator_flat
    assert "Postflight validation and restore: restore Slurm node state" in soperator_flat
    assert "JSON `stage_verification` details" in soperator_flat
    assert (
        "`upgrade node-group --execute --approve` writes the approved pre-mutation checkpoint"
        in (soperator_flat)
    )
    assert "Onboarded external MK8s clusters are not Terraform-managed" in soperator
    assert "best-effort high availability" in soperator_flat

    assert "### When To Use upgrade" in upgrade
    assert "Use `upgrade` when the change is one of the covered operational upgrades" in (upgrade)
    assert "MK8s Kubernetes minor upgrades." in upgrade
    assert "MK8s node-template upgrades: Kubernetes version, OS, and GPU stack." in upgrade
    assert "MK8s node-group migrations: hardware platform, hardware preset, CPU/GPU" in upgrade
    assert "changes a generic VM image family" in upgrade
    assert "Target-scoped Helm chart version bumps." in upgrade
    assert "Edit `config.yaml` manually instead when the change" in upgrade
    assert "### Upgrade Principles" in upgrade
    assert "### Node Template Upgrade" in upgrade
    assert "### Upgrade Strategies" in upgrade
    assert "### Upgrade Examples" in upgrade
    assert "### Helm Chart Upgrades" in upgrade
    assert "non-Soperator target-scoped Helm chart version upgrades" in upgrade
    assert "reserves the command shape" not in upgrade
    assert "Terraform remains the mutation path for Terraform-managed infrastructure." in upgrade
    assert "The Nebius SDK is used for live discovery" in upgrade
    assert "zero-surge   -> 30m" in upgrade
    assert "safe-surge   -> 30m" in upgrade
    assert "force-delete -> 10m" in upgrade
    assert "`--strategy-max-surge-count <n>` to request `n`" in upgrade
    assert "not cxcli's whole rollout" in upgrade
    assert "max(1h, 10m * target node count)" in upgrade
    assert "copy/paste-ready repeat dry-run command" in upgrade_flat
    assert "removing only `--dry-run` keeps the apply command aligned" in upgrade_flat
    assert "live compatibility-matrix summary" in upgrade_flat
    assert "driver-preset choices for each selected platform" in upgrade_flat
    assert "Kubernetes preflight inspection failures block non-dry runs" in upgrade
    assert "Temporary node-group strategy" in upgrade
    assert "source/generated files through Terraform plan/apply" in upgrade
    assert "final MK8s readiness check" in upgrade
    assert "live control-plane version plus selected node-group version, OS" in upgrade_flat
    assert (
        "provider node-group status rather than accepting matching spec fields alone"
        in upgrade_flat
    )
    assert "Kubernetes version downgrade targets are refused" in upgrade_flat
    assert "lower target versions are allowed with an explicit warning" in upgrade_flat
    assert "Manual desired-state upgrades remain supported outside the `upgrade` command" in upgrade
    assert "Guided upgrade value prompts use the same reusable `OptionChoice` provider" in upgrade
    assert "live SDK-backed compatibility matrix" in upgrade
    assert "review the generated" in upgrade
    assert "Terraform plan" in upgrade
    assert "`deploy` runs the full generated-bundle preflight" in upgrade
    assert "`terraform apply` is the infra-only path" in upgrade
    assert "MK8s infra preflights plus Terraform/provider validation" in upgrade
    assert "guided MK8s node-template upgrade wizard" in upgrade
    assert "dry-run/apply choice, upgrade strategy, drain" in upgrade_flat
    assert "nebius-cxcli upgrade node-template <config.yaml>" in upgrade
    assert "The command can run as a guided wizard from only `config.yaml`" in upgrade_flat
    assert "`--no-interactive` fails fast if the target or all" in upgrade
    retired_k8s_command = "nebius-cxcli upgrade " + "k8s-" + "version"
    retired_os_command = "upgrade " + "os-" + "image"
    assert retired_k8s_command not in upgrade
    assert "nebius-cxcli upgrade node-template" in upgrade
    assert "--to-gpu-stack-preset" in upgrade
    assert "selected node group rolls once" in upgrade
    assert "Nebius SDK compatibility matrix" in upgrade
    assert "returned OS and driver-preset choices for each selected platform" in upgrade_flat
    assert (
        "Operator-managed GPU groups can still receive Kubernetes version and OS changes"
        in upgrade_flat
    )
    assert "prompt says blank selects all managed node groups" in upgrade_flat
    assert "safe-surge` strategy choice says it defaults to one spare node" in upgrade_flat
    assert "strategy_max_surge_count` prompt asks for temporary extra nodes" in upgrade_flat
    assert "drain_timeout` prompt shows all `auto` defaults" in upgrade_flat
    assert "`upgrade node-template` uses `--strategy` plus `--drain-timeout`" in upgrade
    assert "force-delete -> 10m" in upgrade
    assert "--disruption-policy" not in upgrade
    assert "allow-unavailable" not in upgrade
    assert retired_os_command not in upgrade
    assert (
        "Automation should pass the explicit target and at least one requested node-template field"
        in upgrade_flat
    )
    assert "Leave `--node-group` unset to select all managed node groups" in upgrade_flat
    assert (
        "node-group Kubernetes version, OS, and Nebius-image `gpu_stack_preset` together"
        in upgrade_flat
    )
    assert "`--node-group` unset to select all managed node groups" in upgrade
    assert (
        "control plane first, then selected node groups in the same CPU/system-before-GPU order"
        in upgrade_flat
    )
    assert (
        "final MK8s readiness check for the live control-plane version plus selected node-group version, OS"
        in upgrade_flat
    )
    assert "`generated/reports/upgrade-node-template-report.md`" in upgrade
    assert "`generated/reports/upgrade-node-template-report.json`" in upgrade
    assert "SSH to nodes and run apt-based Ubuntu upgrades" not in upgrade
    assert (
        "upgrade node-group <config.yaml> infra:mk8s@<target> --node-group system "
        "--to-platform cpu-d3 --to-preset <preset> --dry-run"
    ) in upgrade
    assert (
        "upgrade node-group <config.yaml> infra:mk8s@<target> --node-group worker "
        "--to-platform gpu-b200-sxm --to-preset 8gpu-160vcpu-1792gb "
        "--to-fabric fabric-6 --dry-run"
    ) in upgrade
    assert (
        "upgrade helm-chart <config.yaml> apps:<chart>@<target> --to-version <chart-version>"
    ) in upgrade
    assert (
        "soperator upgrade <config.yaml> --target <target> --to-chart-version <chart-version>"
    ) in upgrade
    assert "restore-capable backup, live Soperator/Slurm preflight" in upgrade_flat
    assert "protected config comparison" in upgrade_flat
    assert "Use `soperator upgrade` instead of the generic Helm path" in upgrade_flat
    assert "active `component_sources.yaml` Soperator chart pin as the default target version" in (
        upgrade_flat
    )
    assert "selected chart is `apps:soperator@<target>`, `upgrade helm-chart` fails fast" in (
        upgrade_flat
    )
    assert "local upgrade checkpoint to restore the original ActiveChecks values" in (upgrade_flat)
    assert (
        "does not switch a row between local static rendering and an OCI/HTTP/Git chart source"
        in (upgrade_flat)
    )
    assert "requires the selected generated target handoff" in upgrade
    assert "Node firmware is maintained by the Nebius hardware team" in upgrade_flat
    assert "not a customer upgrade responsibility" in upgrade_flat
    assert "For node-group hardware or fabric migration, pass the concrete source node group" in (
        upgrade_flat
    )
    assert "The dry-run plan prints current config fabric, current Terraform state fabric" in (
        upgrade_flat
    )
    assert "Current execute requires `--execute --approve`" in upgrade_flat
    assert "`generated/reports/upgrade-node-group-report.md`" in upgrade
    assert "`generated/reports/upgrade-node-group-report.json`" in upgrade
    assert retired_k8s_command not in quick_start
    assert "### Command Examples" in commands
    assert commands.index("### Command Examples") < commands.index("### Supporting Commands")
    assert "## Examples" not in readme
    assert "pass `config.yaml` alone in an interactive terminal" in supporting
    assert (
        "plus one or more of `--to-version <major.minor>`, `--to-os <os>`, "
        "and `--to-gpu-stack-preset <preset>` for automation"
    ) in supporting
    assert "single public command for Terraform-managed MK8s node-template" in unreleased_flat
    assert "`upgrade node-template <config.yaml>" in unreleased
    assert "--to-gpu-stack-preset <preset>" in unreleased
    assert "single MK8s node-group migration surface" in unreleased
    assert "blank `node_group` input says it selects" in unreleased
    assert "`Next step: deploy the rendered bundle:`" in unreleased
    assert "`Next step: nebius-cxcli deploy <config.yaml>`" not in unreleased
    assert "Documented when operators should use the structured `upgrade` command" in unreleased
    assert "reusable upgrade wizard choice builder" in unreleased_flat
    assert "Improved external Soperator upgrade completion handoff" in unreleased_flat
    assert "live post-upgrade discovery refresh" in unreleased_flat
    assert (
        "pending or still-external-upgrade-owned plans blocked from normal deploy"
        in unreleased_flat
    )
    assert "Documented the Soperator cluster upgrade split" in unreleased_flat
    assert "Reorganized the README navigation" in unreleased_flat
    assert "move Soperator Slurm scheduling guidance under `Soperator Commands`" in (
        unreleased_flat
    )


def test_docs_define_discover_and_bootstrap_ci_boundaries() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert (
        "Nebius API credentials/profile are required for commands that talk to Nebius APIs "
        "such as `validate`, `quota-check`, `quota-request`, `render`, `deploy`, "
        "`upgrade`, and `auth`."
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
    readme_flat = _squash(readme)
    design_flat = _squash(design)

    assert (
        "`destroy` is the project-wide destructive path: it tears down all rendered "
        "resources represented by that generated bundle/runtime snapshot"
    ) in readme_flat
    assert (
        "`destroy` is the destructive inverse of `deploy` and is intentionally project-wide"
        in readme
    )
    assert (
        "the external cluster and its node groups stay outside Terraform ownership "
        "and are not destroyed"
    ) in readme_flat
    assert "`kubectl` is used for `validate-generated`, `deploy`, `destroy`" in readme
    assert (
        "Destroys all rendered project resources represented by the existing generated bundle"
        in design_flat
    )
    assert "locally applied post-Flux app resources first" in readme_flat
    assert "locally applied post-Flux app resources first" in design_flat
    assert "destroy` never destroys the external cluster or its node groups" in design_flat
    assert "Rendered app teardown failure is fatal before Terraform destroy" in readme
    assert "Rendered app teardown failure is fatal before Terraform destroy" in design
    assert "Rendered app teardown is best-effort when Terraform will destroy" not in readme
    assert "Rendered app teardown is best-effort when Terraform will destroy" not in design
    assert "### `destroy <config.yaml>`" in design
    assert "Project-wide destructive teardown from the generated bundle" in design
    assert "external or current cluster" not in readme
    assert "external or current cluster" not in design


def test_design_uses_config_yaml_for_project_runtime_command_headings() -> None:
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert "### `deploy <config.yaml>`" in design
    assert "### `destroy <config.yaml>`" in design
    assert "`deploy <config-path>`" not in design
    assert "`destroy <config-path>`" not in design


def test_docs_define_app_instance_id_as_cluster_binding() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    design_flat = _squash(design)

    assert (
        "For app rows, `id` names the chart type and `instance_id` names the MK8s target" in readme
    )
    assert "Enabled `apps.charts[]` rows require at least one enabled MK8s target" in readme
    assert "`nvidia-gpu-operator@cluster2`" in readme
    assert (
        "Authored `config.yaml` does not use `apps.charts[].target_ref`; any internal generated "
        "`target_ref` is derived from and must equal the same target `instance_id`."
    ) in readme
    assert "target-bound app rows use the target id as `instance_id`" in design_flat
    assert (
        "Internal generated rows may also carry `target_ref`, but that field is a derived "
        "runtime alias for the same target `instance_id`, not a second user-facing binding."
    ) in design_flat
    assert "`infra.components[]`: `id`, `instance_id`, `enabled`, `inputs`" in design


def test_design_documents_typed_mk8s_catalog_paths() -> None:
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert "inputs.cluster.kube_network.service_cidrs" in design
    assert "does not default `inputs.node_groups`" in design
    assert "inputs.node_groups: 2" not in design
    assert "defaults.inputs.kube_network_service_cidrs" not in design


def test_soperator_chart_docs_align_default_gpu_worker_name() -> None:
    chart_design = (MONOREPO_ROOT / "helm-charts" / "soperator" / "docs" / "design.md").read_text(
        encoding="utf-8"
    )
    production_core = (
        MONOREPO_ROOT / "helm-charts" / "soperator" / "examples" / "production-core-values.yaml"
    ).read_text(encoding="utf-8")
    gpu_only_section = _section(
        chart_design,
        "#### GPU-Only Workers",
        "#### Mixed CPU+GPU Workers",
    )

    assert "worker          -> default GPU worker nodes" in chart_design
    assert "name: worker" in production_core
    assert "slurm.nebius.ai/nodeset-name: worker" in production_core
    assert "worker-gpu" not in production_core
    assert "worker-gpu" not in gpu_only_section


def test_soperator_docs_define_worker_autoscaling_boundary() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    cli_text = (REPO_ROOT / "src" / "nebius_cxcli" / "cli.py").read_text(encoding="utf-8")
    chart_readme = (MONOREPO_ROOT / "helm-charts" / "soperator" / "README.md").read_text(
        encoding="utf-8"
    )
    chart_design = (MONOREPO_ROOT / "helm-charts" / "soperator" / "docs" / "design.md").read_text(
        encoding="utf-8"
    )
    chart_changelog = (MONOREPO_ROOT / "helm-charts" / "soperator" / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )

    chart_flat = _squash(chart_design)
    chart_readme_flat = _squash(chart_readme)
    readme_flat = _squash(readme)
    design_flat = _squash(design)

    assert "one Slurm worker pod aligned with one Kubernetes worker VM" in chart_flat
    assert "Soperator does not enforce this with a DaemonSet" in chart_flat
    assert "| `1gpu-*` | 5 | 1 | 5 | 5 |" in chart_design
    assert "| `8gpu-*` | 5 | 8 | 5 | 5 |" in chart_design
    assert "`inputs.soperator.worker_cpu_total_nodes` and" in chart_flat
    assert "`inputs.soperator.worker_gpu_total_nodes` mean Kubernetes worker hosts" in chart_flat
    assert "not total GPU count and not an aggregate CPU/GPU split" in chart_flat
    assert "# no gpu_clusters" in chart_design
    assert "gpu_cluster_key: workers" in chart_design
    assert "Meaning: 5 Slurm worker nodes, 1 GPU each, total 5 GPUs." in chart_design
    assert "Meaning: 5 Slurm worker nodes, 8 GPUs each, total 40 GPUs." in chart_design
    assert "That is maximum-capacity materialization, not Slurm-demand autoscaling" in chart_flat
    assert "the non-ephemeral NodeSet desires five worker pods" in chart_flat
    assert "`inputs.soperator.worker_node_groups.<worker>.ephemeral_nodes.enabled=true`" in (
        chart_flat
    )
    assert "worker_gpu_total_nodes: 5" in chart_design
    assert "ephemeralNodes: true" in chart_design
    assert "initialNumberEphemeralNodes: 1" in chart_design
    assert "not a permanent minimum" in chart_flat
    assert "`slurmConfig.suspendTime` must be finite and non-negative" in chart_flat
    assert "seed GPU libraries into the shared jail" in chart_flat

    assert "one Slurm worker pod equals one Kubernetes worker VM" in chart_readme_flat
    assert "`inputs.soperator.worker_cpu_total_nodes` and" in chart_readme_flat
    assert "`inputs.soperator.worker_gpu_total_nodes` are Kubernetes worker host counts" in (
        chart_readme_flat
    )
    assert (
        "without ephemeral NodeSets, a NodeSet with `replicas: 5` still desires five worker pods"
        in chart_readme_flat
    )
    assert "- [Soperator Autoscaling](#soperator-autoscaling)" in chart_readme
    assert "`inputs.soperator.worker_node_groups.<worker>.ephemeral_nodes.enabled=true`" in (
        chart_readme_flat
    )
    assert "worker_gpu_total_nodes: 5" in chart_readme
    assert "Added explicit chart schema, validation, and tests for upstream Soperator" in _squash(
        chart_changelog
    )
    assert "`initialNumberEphemeralNodes <= replicas`" in (_squash(chart_changelog))

    assert "maximum-capacity materialization, not Slurm-demand worker elasticity" in readme_flat
    assert "`soperator.worker_cpu_total_nodes`" in readme_flat
    assert "`soperator.worker_gpu_total_nodes`" in readme_flat
    assert "not total GPU count and not an aggregate CPU/GPU split" in readme_flat
    assert "worker_*_nodes_per_group` value must be less than or equal to" in readme_flat
    assert "cap worker shards at 100 MK8s nodes per generated group" in readme_flat
    assert "service-role autoscaling helpers" in readme_flat
    assert "Worker autoscaling is controlled per generated shard" in readme_flat
    assert "uses `autoscaling.enabled` as the per-shard Infra/MK8s worker" in (readme_flat)
    assert "answering `true` also writes same-shard `ephemeral_nodes.enabled=true`" in (readme_flat)
    assert "with max defaulting to that shard's generated capacity" in readme_flat
    assert "answering `false` clears same-shard autoscaling bounds" in readme_flat
    assert "synthetic bulk apply-to-all choice for all CPU worker shards" in readme_flat
    assert "all worker shards in mixed CPU+GPU layouts" in readme_flat
    assert "`all_worker_shards_apply_to_all` and defaults to `true`" in readme_flat
    assert "No bulk key is saved" in readme_flat
    assert "suspend_time_seconds` only after at least one shard has" in readme_flat
    assert "5 x `1gpu-*` hosts means five Slurm worker replicas with `gpu: 1`" in (readme_flat)
    assert "5 x `8gpu-*` hosts means five replicas with `gpu: 8` and 40 total GPUs" in (readme_flat)
    assert "`inputs.soperator.worker_node_groups.<worker>.ephemeral_nodes.enabled=true`" in (
        readme_flat
    )
    assert (
        "`initialNumberEphemeralNodes` is only the initial active Slurm worker pods" in readme_flat
    )
    assert "raises GPU worker shards to at least one initial active worker" in readme_flat
    assert "seed GPU libraries into the jail" in readme_flat
    assert "not upstream Soperator Slurm-demand elasticity" in design_flat
    assert "one-worker-pod to one-Kubernetes-worker-VM resource shape" in design_flat
    assert "`inputs.soperator.worker_node_groups.<worker>.ephemeral_nodes.enabled=true`" in (
        design_flat
    )
    assert "`inputs.soperator.worker_cpu_total_nodes`" in design_flat
    assert "`inputs.soperator.worker_gpu_total_nodes`" in design_flat
    assert "`inputs.soperator.worker_node_groups`" in design_flat
    assert "worker_*_nodes_per_group` value must be less than or equal to" in design_flat
    assert "cap worker shards at 100 MK8s nodes per generated group" in design_flat
    assert "Worker autoscaling is controlled per generated worker shard" in design_flat
    assert "wizard uses `autoscaling.enabled` as the per-shard Infra/MK8s worker" in (design_flat)
    assert "answering `true` writes same-shard `ephemeral_nodes.enabled=true`" in design_flat
    assert "with max defaulting to that shard's generated capacity" in design_flat
    assert "answering `false` clears same-shard autoscaling bounds" in design_flat
    assert "synthetic bulk apply-to-all choice for all CPU worker shards" in design_flat
    assert "`all_worker_shards_apply_to_all` and defaults to `true`" in design_flat
    assert "No bulk key is saved" in design_flat
    assert "only after at least one shard has autoscaling-backed ephemeral nodes" in design_flat
    assert "raises GPU worker shards to at least one initial active worker" in design_flat
    assert "seed GPU libraries into the jail" in design_flat
    assert "not total GPU count and not an aggregate CPU/GPU split" in design_flat
    assert "5 x `1gpu-*` hosts means five Slurm worker replicas with `gpu: 1`" in (design_flat)
    assert "5 x `8gpu-*` hosts means five replicas with `gpu: 8` and 40 total GPUs" in (design_flat)
    assert "per-generated-shard `worker_node_groups` controls" in (_squash(changelog))
    assert "as the per-shard Infra/MK8s worker autoscaling toggle" in _squash(changelog)
    assert "max prompt defaults to the generated shard capacity" in _squash(changelog)
    assert "synthetic bulk apply-to-all wizard choice" in _squash(changelog)
    assert "all_worker_shards_apply_to_all" in _squash(changelog)
    assert "defaults to `true`" in _squash(changelog)
    assert "saves no bulk key" in _squash(changelog)
    assert "fail fast when they exceed the selected profile's per-group limit" in _squash(changelog)
    assert "Changed Soperator production worker sizing to shape-specific fixed capacity" in (
        _squash(changelog)
    )
    assert "`initialNumberEphemeralNodes` from the shard's autoscaling" in _squash(changelog)
    assert "raises GPU worker shards to at least one initial active worker" in _squash(changelog)
    assert "Worker sizing " in cli_text
    assert "is shape-specific: worker_cpu_*" in cli_text
    assert "GPU count per host comes from the preset" in cli_text
    assert "selected profile's per-group limit" in cli_text
    for retired_helper in (
        "worker_total_nodes",
        "worker_nodes_per_group",
        "worker_autoscaling",
        "worker_cpu_autoscaling",
        "worker_gpu_autoscaling",
        "worker_ephemeral_nodes.enabled",
    ):
        assert retired_helper not in readme_flat
        assert retired_helper not in design_flat
        assert retired_helper not in chart_flat
        assert retired_helper not in chart_readme_flat


def test_soperator_docs_lock_production_training_child_chart_defaults() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    chart_readme = (MONOREPO_ROOT / "helm-charts" / "soperator" / "README.md").read_text(
        encoding="utf-8"
    )
    chart_changelog = (MONOREPO_ROOT / "helm-charts" / "soperator" / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )
    chart_design = (MONOREPO_ROOT / "helm-charts" / "soperator" / "docs" / "design.md").read_text(
        encoding="utf-8"
    )
    checks_readme = (MONOREPO_ROOT / "helm-charts" / "soperator-checks" / "README.md").read_text(
        encoding="utf-8"
    )
    activechecks_readme = (
        MONOREPO_ROOT / "helm-charts" / "soperator-activechecks" / "README.md"
    ).read_text(encoding="utf-8")

    for text in (readme, design):
        flat = _squash(text)
        assert "values.soperator-activechecks.enabled=false" in flat
        assert "values.soperator-activechecks.waitForChecks.enabled=false" in flat
        assert "values.soperator-checks.enabled=false" in flat
        assert "values.soperator-dcgm-exporter.enabled=false" in flat
        assert "values.soperator-notifier.enabled=false" in flat
        assert "values.soperator-backup-config.enabled=false" in flat
        assert (
            "no-GPU Soperator profiles force `values.soperator-dcgm-exporter.enabled=false`" in flat
        )
        assert "production-training best practice" in flat
        assert "not production training clusters" in flat
        assert "Slurm accounting, SlurmDBD, and chart-managed MariaDB enabled" in flat
        assert (
            "Slurm accounting, SlurmDBD, and the chart-managed accounting database stay enabled"
            in flat
        )
        assert "the partition profile does not toggle the accounting database" in flat
        assert (
            "catalog-owned QOS overlays keep the chart's multi-arch Slurm `PluginDir`" not in text
        )
        assert "Advanced production-maintenance mode" in flat
        assert "NebiusMaintenanceScheduled=True" in flat
        assert "graceful maintenance drain" in flat
        assert "node handoff" in flat
        assert "does not call" in flat
        assert "SlurmNodeReboot=True" in flat
        assert "Soperator host reboot after drain" in flat
        assert "already drained" in flat
        assert "values.sssd.enabled=false" in flat
    assert "cxcli-managed Nebius Soperator profiles pin" in design
    assert "catalog-owned QOS overlays leave that path unchanged" in design
    assert "standalone chart default still leaves `PluginDir` unset" in design
    assert "pinned-image Slurm plugin directory override" not in chart_changelog

    changelog_flat = _squash(changelog)
    assert "enable production child-chart defaults for checks, active checks, and DCGM" not in (
        changelog_flat
    )
    assert "keep production-impacting child-chart gates disabled by default" in changelog_flat
    for text in (chart_readme, chart_design):
        flat = _squash(text)
        assert "soperator-activechecks.enabled=false" in flat
        assert "soperator-activechecks.waitForChecks.enabled=false" in flat
        assert "soperator-checks.enabled=false" in flat
        assert "soperator-dcgm-exporter.enabled=false" in flat
        assert "soperator-notifier.enabled=false" in flat
        assert "soperator-backup-config.enabled=false" in flat
        assert "rebooter.enabled=false" in flat
        assert "does not create a reboot schedule" in flat
        assert "not a per-NodeSet" in flat
        assert "does not reboot nodes at install time" in flat or (
            "does not reboot nodes during chart install" in flat
        )
        assert "NoExecute" in flat
        assert "SlurmNodeReboot" in flat
        assert "does not prompt" in flat
        assert "NebiusMaintenanceScheduled=True" in flat
        assert "SoperatorChecksNodeMaintenance=True" in flat
        assert "SoperatorChecksNodeDegraded=True" in flat
        assert "slurmNodes.sssd.enabled=false" in flat
        assert "nodesets[].sssd.enabled=false" in flat
        assert "Advanced production-maintenance mode" in flat
        assert "graceful maintenance drain" in flat
        assert "node handoff" in flat
        assert "Soperator host reboot" in flat
        assert "already drained" in flat
    assert "override `waitForChecks.enabled=false`" in _squash(activechecks_readme)
    checks_flat = _squash(checks_readme)
    assert "NebiusMaintenanceScheduled=True" in checks_flat
    assert "graceful maintenance drain and node handoff" in checks_flat
    assert "SlurmNodeReboot=True" in checks_flat
    assert "actual Soperator host reboot path after drain" in checks_flat
    assert "SlurmNodeDrain=True" in checks_flat
    assert "not `SlurmNodeReboot=True`" in checks_flat


def test_soperator_chart_design_aligns_current_scheduling_qos_surfaces() -> None:
    chart_design = (MONOREPO_ROOT / "helm-charts" / "soperator" / "docs" / "design.md").read_text(
        encoding="utf-8"
    )
    flat = _squash(chart_design)

    assert "The top-level `schedulingConfig` block defines cluster-wide scheduling" in flat
    assert "`accountingStorageEnforce` | `AccountingStorageEnforce`" in flat
    assert "`enforcePartLimits` | `EnforcePartLimits`" in flat
    assert "set non-zero `schedulingConfig.priorityWeights.fairshare` / `qos` / `age`" in flat
    assert "For self-managed clusters, enable `qosConfiguration` to reconcile" in flat
    assert "For Managed Soperator, keep `qosConfiguration.enabled=false`" in flat
    assert "accountingStorageEnforce: - associations - limits - qos" in flat
    assert "enforcePartLimits: ANY" in flat
    assert "customSlurmConfig: | AccountingStorageEnforce=associations,limits,qos" not in flat
    assert "The chart does not create QOS objects" not in chart_design
    assert "managed via `sacctmgr` outside the chart" not in chart_design
    assert "slurmNodes.accounting.slurmConfig.priorityWeightFairshare" not in chart_design
    assert (
        "Archives under `helm-charts/soperator/charts/` are generated dependency artifacts" in flat
    )
    assert "Rebooter ServiceAccount, Role, and binding resources render only when" in flat


def test_docs_define_component_selector_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    readme_flat = _squash(readme)
    design_flat = _squash(design)

    assert "`component add [component-selector...] --config <config.yaml>`" in design
    assert "`component remove [component-selector...] --config <config.yaml>`" in design
    assert "`component list --config <config.yaml>`" in design
    assert (
        "`<component-id>`, `infra:<component-id>`, `apps:<component-id>`, `all`, `none`" in readme
    )
    assert "Scoped app selectors use the plural `apps:` prefix" in readme
    assert "singular `app:` is invalid" in readme
    assert "`apps:<chart-id>@<target-id>`" in readme
    assert "`apps:<chart-id>@<target-id>`" in design
    assert (
        "nebius-cxcli component add apps:external-secrets@mk8s --config <config.yaml> --no-interactive"
        in readme
    )
    assert "`<row-id>`, or `<component-id>@<resource-name-or-target-id>`" in readme
    assert "becomes" in readme
    assert "`apps.charts[].instance_id`" in readme
    assert "scalar named infra modules prompt for the resource name" in design
    assert "`instance_id` is derived from that normalized name" in design
    assert "bare infra selector creates the default named row when absent" in readme
    assert "For scalar named infra, the row id is the normalized resource name" in readme
    assert "non-interactive mode" in design
    assert "does not repeat final `Added infra/apps components` lines" in readme
    assert "summaries for categories that actually changed" in readme
    assert "skips the final redundant `Added infra/apps components` lines" in design
    assert "only for categories that actually changed" in design
    assert "removes app chart rows and `deploy.targets[]` settings" in design
    assert "`usage.lifecycle: transient`" in readme
    assert "`usage.config.ref`" in readme
    assert "`usage.lifecycle: transient`" in design
    assert "`usage.config.ref`" in design
    assert "`apps:soperator`" in readme
    assert "`install_mode`" in readme
    assert "`production-cluster` creates the complete MK8s+SFS+Soperator" in readme_flat
    assert "`nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>`" in readme
    assert (
        "a deployments root. In that case pass or answer `--client-name`, `--tenant-id`, `--project-id`, and `--region-id`"
        in readme_flat
    )
    assert (
        "`onboard-existing-cluster` is written by `nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>` for an external Nebius MK8s target"
        in readme_flat
    )
    assert "lists existing Nebius MK8s clusters in the selected project" in readme_flat
    assert "onboards one cluster per run" in readme_flat
    assert "records the selected Nebius `cluster_id` as the durable access handle" in readme_flat
    assert (
        "nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root> \\\n"
        "  --client-name <client-name> \\\n"
        "  --tenant-id <tenant-id> \\\n"
        "  --project-id <project-id> \\\n"
        "  --region-id <region-id> \\\n"
        "  --cluster-id <mk8scluster-id> \\\n"
        "  --target-id <logical-target-id> \\\n"
        "  --storage-mode keep-existing-storage \\\n"
        "  --compute-mode keep-existing-compute \\\n"
        "  --to-chart-version <soperator-chart-version> \\\n"
        "  --to-k8s-version <major.minor> \\\n"
        "  --no-interactive"
    ) in readme
    assert (
        "When the first argument is an existing project `config.yaml`, the "
        "`--client-name`, `--project-id`, and `--region-id` values can come from "
        "that file instead, and `tenant_id` is optional existing-config metadata"
    ) in readme_flat
    assert (
        "When the first argument is a deployments root, pass `--client-name`, "
        "`--tenant-id`, `--project-id`, and `--region-id` explicitly"
    ) in readme_flat
    assert "`--cluster-id`: Nebius MK8s cluster id to onboard" in readme_flat
    assert "saved under `deploy.targets[].cluster_id`" in readme_flat
    assert "fetch the cluster endpoint and CA with the Nebius Python SDK" in readme_flat
    assert "`--target-id`: optional cxcli logical target id" in readme_flat
    assert "It is not the Nebius MK8s `cluster_id`" in readme_flat
    assert "Use the same cxcli target id for upgrade" in readme_flat
    assert "Do not pass the raw Nebius MK8s `cluster_id`" in readme_flat
    assert "`--kube-context`: optional kubectl context override for discovery" in readme_flat
    assert "`--access`: endpoint to use when generating temporary kubeconfig" in readme_flat
    assert (
        "does not accept arbitrary vanilla Kubernetes clusters in the interactive flow"
        in readme_flat
    )
    assert "External onboarding is not a Terraform import" in readme_flat
    assert "remain outside Terraform ownership" in readme_flat
    assert (
        "If the accepted onboarding report says no external-upgrade-owned work is required"
        in readme_flat
    )
    assert "deploy the rendered desired state" in readme_flat
    assert "`deploy <config.yaml>` applies the generated desired state" in readme_flat
    assert "`nebius-cxcli deploy <config.yaml>`" in readme
    assert "Plain `deploy <config.yaml>` reconciles every generated target" in readme_flat
    assert (
        "Use `deploy --target <target-id>` only when you intentionally want to narrow"
        in readme_flat
    )
    assert "Healthy evidence is reported as `gpu-stack: verified`" in readme_flat
    assert "`gpu-rdma: validation-planned`" in readme_flat
    assert "Target GPU stack reconciliation alone is not external-upgrade-owned work" in readme_flat
    assert (
        "if no Soperator chart, storage, compute, or external node-template upgrade action is selected"
        in readme_flat
    )
    assert "`ext-soperator upgrade` fails fast" in readme_flat
    assert "do not run `deploy` before the external upgrade" in readme_flat
    assert (
        "`ext-soperator upgrade --execute` must first verify the live source release, create a restore-capable backup"
        in readme_flat
    )
    assert "Each executed stage runs a fast stage-scoped verification" in readme_flat
    assert "leaves that same phase pending" in readme_flat
    assert "After the final locked segment completes" in readme_flat
    assert (
        "`generated/reports/ext-soperator-upgrade-report.md` reports `Pending phase: none`"
        in readme_flat
    )
    assert "Rerendering preserves command-owned runtime reports" in readme_flat
    assert "All lifecycle reports stay in the single `generated/reports/` folder" in readme_flat
    assert "Each command owns a deterministic latest artifact" in readme_flat
    assert "`nebius-cxcli soperator discover <config.yaml> --target <target>`" in readme_flat
    assert (
        "`nebius-cxcli ext-soperator discover [<config.yaml-or-deployments-root>] --target <target>`"
        in readme_flat
    )
    assert (
        "`nebius-cxcli ext-soperator discover --project-id <project-id> --cluster-id <mk8scluster-id>`"
        in readme_flat
    )
    assert "`--tenant-id` is optional metadata" in readme_flat
    assert "`--client-name` selects a specific runtime-auth cache profile when needed" in (
        readme_flat
    )
    assert "project-scoped Nebius authentication" in design_flat
    for report_name in (
        "`soperator-discovery/<target>/manifest.json`",
        "`ext-soperator-upgrade-report.md`",
        "`upgrade-node-template-report.md`",
        "`upgrade-node-template-report.json`",
        "`upgrade-node-group-report.md`",
        "`upgrade-node-group-report.json`",
        "`soperator-upgrade-report.md`",
        "`soperator-upgrade-report.json`",
    ):
        assert report_name in readme_flat
    assert "JSON detail reports referenced from those Markdown reports" in readme_flat
    assert "`Stage Fast Verification` rollup" in readme_flat
    assert "JSON `stage_verification` array" in readme_flat
    assert (
        "the selected actions become deploy-owned for the next normal reconciliation" in readme_flat
    )
    assert "If the report still shows any pending phase other than `none`" in readme_flat
    assert "rerun the same `ext-soperator upgrade ... --execute --approve` command" in readme_flat
    assert (
        "If the final report shows `Pending phase: none` but the post-upgrade config refresh was skipped"
        in readme_flat
    )
    assert "`ext-soperator onboard` only as an intentional repair path" in readme_flat
    assert "The external Soperator steady-state handoff is:" in readme_flat
    assert (
        "decides whether the accepted `deploy.targets[].soperator_onboarding.actions` list contains external-upgrade-owned work"
        in readme_flat
    )
    assert "`deploy.targets[].soperator_onboarding.upgrade_path`" in readme_flat
    assert "Each `ext-soperator upgrade --execute --approve` run advances one locked segment" in readme_flat
    assert "keeps onboarding in place and prints the next same-command invocation" in readme_flat
    assert (
        "`generated/reports/ext-soperator-upgrade-report.md` shows `Pending phase: none`"
        in readme_flat
    )
    assert (
        "`config.yaml` and the `generated/reports/soperator-discovery/<target>/` bundle into the deploy-owned onboarding shape"
        in readme_flat
    )
    assert "edit `config.yaml`, run `render`, then run `deploy`" in readme_flat
    assert (
        "Rerunning `ext-soperator onboard` after a completed external upgrade is read-only"
        in readme_flat
    )
    assert "keep the target on the deploy path" in readme_flat
    assert "reconcile-target-gpu-stack" in readme_flat
    assert "target GPU stack reconciliation" in readme_flat
    assert "target-gpu-stack-remediation" in readme_flat
    assert (
        "`nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run`" in readme
    )
    assert "color-highlighted sectioned plan covering target discovery" in readme_flat
    assert "the full locked path, completed/current/remaining segments" in readme_flat
    assert "accepted onboarding actions, node-template rollout, phases, execution controls" in (
        readme_flat
    )
    assert "`ext-soperator onboard` is read-only against live cluster state" in readme_flat
    assert "The initial discovery summary is read-only" in readme_flat
    assert "does not list future upgrade phases as live onboarding actions" in readme_flat
    assert "prints the accepted layout decisions explicitly" in readme_flat
    assert "no aligned SFS creation or storage data migration is planned" in readme_flat
    assert "no replacement compute node groups or compute migration are planned" in readme_flat
    assert "runs the supported phases in order" in readme_flat
    assert "Onboarding asks for two independent layers" in readme_flat
    assert "compute mode is `keep-existing-compute` or `create-aligned-node-groups`" in readme_flat
    assert "Keeping existing compute preserves the discovered node groups" in readme_flat
    assert "`--to-chart-version`: target Soperator chart version" in readme
    assert "Defaults to the `component_sources.yaml` Soperator chart pin" in readme_flat
    assert "non-default versions must resolve from the configured Soperator chart source" in (
        readme_flat
    )
    assert "`deploy.targets[].soperator_onboarding.target_version`" in readme_flat
    assert "`render` and `ext-soperator upgrade` target the same version" in readme_flat
    assert "`--to-k8s-version`: target Kubernetes `major.minor` version" in readme
    assert "defaults this field to the next provider-supported minor hop" in readme_flat
    assert "does not jump straight to the latest supported minor" in readme_flat
    assert "`summary.md` includes `Upgrade Guidance` without gating discovery" in readme_flat
    assert (
        "that section shows Kubernetes minor hops, the one-shot Soperator hop "
        "to the cxcli-pinned target"
    ) in readme_flat
    assert "canonical ordering across the Kubernetes `1.33+` boundary" in readme_flat
    assert "print the matched Soperator/Kubernetes upgrade-path rule during the decision summary" in (
        readme_flat
    )
    assert "Unsupported accepted plans still require `--allow-unsupported-soperator-upgrade-path`" in (
        readme_flat
    )
    assert "discovered storage sizes are lower bounds" in readme_flat
    assert "Render/deploy must not request a smaller PVC/PV size" in readme_flat
    assert "from the live node-group ids" in readme_flat
    assert "preserves the live `SlurmCluster` resource name as `values.clusterName`" in readme_flat
    assert "accepted onboarding fingerprint and source release" in readme_flat
    assert (
        "choose a source version from the exact committed external-upgrade profile rows"
        in readme_flat
    )
    assert "known major-generation profile group" in readme_flat
    assert "`--source-version`: source Soperator version to use when discovery finds" in readme
    assert (
        "older same-name source-family Helm records are treated as informational stale discovery evidence"
        in readme_flat
    )
    assert "rather than as source-version uncertainty or selected onboarding work" in readme_flat
    assert "Discovery enumerates Helm releases across all namespaces" in readme_flat
    assert "stores only Soperator-like releases" in readme_flat
    assert "known Soperator release name in a non-standard namespace" in readme_flat
    assert "matched migration profile instead of requiring source-version input" in readme_flat
    assert "local `.nebius-cxcli/ext-soperator-upgrades/` timeout-guarded checkpoint" in readme_flat
    assert "`--approve` / `--no-approve`: record customer approval" in readme_flat
    assert "auto-detects source worker node groups" in readme_flat
    assert "`slurm.nebius.ai/nodeset` worker labels" in readme_flat
    assert "net-new upgrade quota preflight before any SFS or node-group mutation" in readme_flat
    assert "target service-role node groups that do not already exist" in readme_flat
    assert "Existing worker node groups are preserved in place" in readme_flat
    assert "checks the required spare quota and GPU capacity before mutation" in readme_flat
    assert "requires all selected worker nodes to start Ready and schedulable" in readme_flat
    assert "checks Slurm jobs on affected external node-template workers" in readme_flat
    assert "checks all live worker NodeSets before target Soperator chart reconciliation" in readme_flat
    assert "Slurm rejects the scoped node filter" in readme_flat
    assert "unfiltered cluster-wide job list" in readme_flat
    assert (
        "The selected `deploy.targets[].soperator_onboarding.actions` list is the desired external upgrade contract"
        in readme_flat
    )
    assert "`approve-external-soperator-upgrade`" in readme_flat
    assert "approve-soperator-migration" not in readme
    assert "approve-soperator-migration" not in design
    assert "Reruns are action-idempotent rather than checkpoint-only" in readme_flat
    assert "rechecks the corresponding live state" in readme_flat
    assert (
        "Rerunning `ext-soperator onboard` is safe and refreshes the source discovery bundle"
        in readme_flat
    )
    assert (
        "cxcli enriches the single bulk Kubernetes node inventory with Nebius control-plane and node-group template inventory by node group"
        in readme_flat
    )
    assert "omits the `upgrade-external-node-template` action" in readme_flat
    assert (
        "missing, partial, or errored provider inventory keeps that action selected" in readme_flat
    )
    assert (
        "still-rolling state is checkpointed as a pending external-node-template phase"
        in readme_flat
    )
    assert "does not create duplicate worker groups or require 2x worker quota" in readme_flat
    assert (
        "External Soperator upgrade owns external Kubernetes minor, node OS image, and Nebius-image GPU-stack upgrades selected by onboarding"
        in readme_flat
    )
    assert "`mk8s cluster update` and `mk8s node-group update` calls" in readme_flat
    assert (
        "external node-template and target GPU stack reconciliation as their own required actions"
        in readme_flat
    )
    assert "Worker groups default to zero-surge" in readme_flat
    assert (
        "safe-surge uses one temporary replacement node per active service or worker group"
        in readme_flat
    )
    assert "cxcli fails fast rather than assuming a vanilla cluster is safe to adopt" in readme_flat
    assert "ignored by cxcli-managed deployments `.gitignore` files" in readme_flat
    assert "creates or reuses aligned jail, controller-spool, and accounting SFS" in readme_flat
    assert (
        "Quota must cover this spare target storage while source storage remains mounted"
        in readme_flat
    )
    assert "never attempts to shrink adopted storage" in readme_flat
    assert "runs Kubernetes data-copy Jobs when old and target PVC pairs exist" in readme_flat
    assert "required Soperator deployment snapshot" in readme_flat
    assert "SlurmCluster, and NodeSet resources" in readme_flat
    assert "`nebius-cxcli acceptance-test smoke ... --suite slurm`" in readme_flat
    assert "`nebius-cxcli acceptance-test benchmark ... --suite slurm-nccl`" in readme_flat
    assert "`deploy-gpu-stack-readiness-report-<target>.json`" in readme_flat
    assert "`deploy-gpu-visibility-report-<target>.json`" in readme_flat
    assert "`acceptance-smoke-report-<target>.json`" in readme_flat
    assert "`acceptance-benchmark-report-<target>.json`" in readme_flat
    assert "`test_purpose`, `mode`, `scope`, `kind`, and `target_ref`" in readme_flat
    assert "deploy-time Soperator testing is deliberately fast" in readme_flat
    assert "Exhaustive all-node Slurm hostname/GPU smoke moves to" in readme_flat
    assert (
        "NCCL/performance validation is reserved for explicit `acceptance-test "
        "benchmark` runs" in readme_flat
    )
    assert "Both acceptance-test smoke and benchmark commands require `--suite`" in readme_flat
    assert (
        "after a suite is selected, they run all generated targets when `--target` is omitted"
        in readme_flat
    )
    assert "They resolve target handoff from `generated/reports/deploy-report.md`" in readme_flat
    assert "nebius-cxcli-soperator-cluster-validation/v2" in readme_flat
    assert "command `stdout`/`stderr` as arrays of lines" in readme_flat
    assert (
        "acceptance hostname, GPU driver-jail, and GPU allocation sub-checks write structured "
        "`partition_hostnames`, `gpu_driver_jail`, and `gpu_allocations` arrays with all-node evidence"
        in design_flat
    )
    assert "including the evidence source for each GPU allocation node" in design_flat
    assert "through NVIDIA proc-driver plus `/dev/nvidia*` device evidence" in readme_flat
    assert "Explicit `acceptance-test smoke --suite slurm` runs the Slurm CLI" in readme_flat
    assert "Slurm nodes reported as `inval` remain unhealthy there" in readme_flat
    assert "same catalog-owned post-render patches that Flux would apply" in readme_flat
    assert "`generated/reports/ext-soperator-upgrade-report.md`" in readme
    assert (
        "Phases complete only when their live prerequisites are absent or satisfied" in readme_flat
    )
    assert "Non-interactive `component add apps:soperator@<target>`" in readme
    assert "canonical initial onboarding command" in readme_flat
    assert "does not create Terraform-managed MK8s/SFS rows" in readme_flat
    assert "`apps.charts[].placements.*`" in readme
    assert "worker` onto GPU node groups" in readme_flat
    assert "worker labels distinguish `worker-cpu` and `worker-gpu`" in readme_flat
    assert "`apps.charts[].placements.worker-cpu`" in readme
    assert "`apps.charts[].placements.worker-gpu`" in readme
    assert "make Pyxis optional and clear the importer path" in readme_flat
    assert "`nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>`" in design
    assert "first-time onboarding can pass the deployments root" in design_flat
    assert "`onboard-existing-cluster` for an external Nebius MK8s target" in design_flat
    assert "lists existing Nebius MK8s clusters in the selected project" in design_flat
    assert "choose one cluster for that run" in design_flat
    assert (
        "stores the selected Nebius `cluster_id` as the durable target access handle" in design_flat
    )
    assert "Non-interactive onboarding uses `--cluster-id <mk8scluster-id>`" in design_flat
    assert "`--target-id` is only an optional cxcli logical alias" in design_flat
    assert "`--kube-context` is an explicit discovery override" in design_flat
    assert "does not accept arbitrary vanilla Kubernetes clusters" in design_flat
    assert "`deploy.targets[].inventory.node_groups`" in design
    assert "two independent layer choices" in design_flat
    assert "The discovery summary printed during onboarding is read-only" in design_flat
    assert "includes the discovered/current and target Kubernetes minor versions" in design_flat
    assert "plus `Upgrade Guidance`" in design_flat
    assert "Kubernetes minor hops, the one-shot Soperator hop" in design_flat
    assert "canonical ordering across the Kubernetes `1.33+` boundary" in design_flat
    assert "Soperator `1.22.3 -> 4.0.2-ps.3`" in design_flat
    assert "This ordering is intentional" in design_flat
    assert "where the required Nebius GPU image/CUDA stack targets" in design_flat
    assert "before the cluster reaches the Kubernetes `1.33+` boundary" in design_flat
    assert "`procMount: Unmasked` admission now depends on `hostUsers: false`" in (
        design_flat
    )
    assert "user-namespace/idmap and NFS behavior must match the target chart contract" in (
        design_flat
    )
    assert "old source webhooks and controllers must stop reconciling target objects" in (
        design_flat
    )
    assert "stale Flux/Helm records must be retired before final validation" in design_flat
    assert "releases newer than the cxcli pin, such as `4.1.1`, are deliberately not advertised" in (
        design_flat
    )
    assert "remain `not_validated` until cxcli has an explicit tested policy rule" in (
        design_flat
    )
    assert "defaults to one provider-supported minor hop from the discovered control-plane version" in (
        design_flat
    )
    assert "does not present external upgrade phases as actions taken by the onboard command" in (
        design_flat
    )
    assert "It also prints the matched Soperator/Kubernetes upgrade-path rule" in design_flat
    assert "onboarding prints the accepted layout decisions" in design_flat
    assert (
        "target-compatible storage means no aligned SFS creation or storage data migration is planned"
        in (design_flat)
    )
    assert (
        "target-compatible compute means no replacement compute node groups or compute migration are planned"
        in (design_flat)
    )
    assert "`keep-existing-compute` or `create-aligned-node-groups`" in design_flat
    assert "Keeping existing compute preserves discovered node groups" in design_flat
    assert "This is primarily a day-2 Soperator management and upgrade path" in design_flat
    assert "not a Terraform-managed MK8s row" in design_flat
    assert "If the accepted report says no external-upgrade-owned work is required" in design_flat
    assert "Healthy evidence is reported as `gpu-stack: verified`" in design_flat
    assert "`gpu-rdma: validation-planned` evidence" in design_flat
    assert "Target GPU stack reconciliation alone is not external-upgrade-owned work" in design_flat
    assert "fast stage gates record `fast_verification`" in design_flat
    assert "`ext-soperator upgrade` fails fast with the render/deploy route" in design_flat
    assert "plain `deploy <config.yaml>` reconciles the generated desired state" in design_flat
    assert "`deploy --target <target-id>` is only a narrowing selector" in design_flat
    assert "normal render/deploy applies it as desired state" in design_flat
    assert (
        "If the accepted onboarding report says external-upgrade-owned work is required"
        in design_flat
    )
    assert "skip normal deploy and continue with" in design_flat
    assert "After the final locked segment completes" in design_flat
    assert (
        "`generated/reports/ext-soperator-upgrade-report.md` shows `Pending phase: none`"
        in design_flat
    )
    assert (
        "The render-time `generated/` replacement preserves command-owned runtime reports"
        in design_flat
    )
    assert (
        "All lifecycle reports stay under `generated/reports/`, and command-specific reports use deterministic latest filenames"
        in design_flat
    )
    for report_name in (
        "`soperator-discovery/<target>/manifest.json`",
        "`ext-soperator-upgrade-report.md`",
        "`upgrade-node-template-report.md`",
        "`upgrade-node-template-report.json`",
        "`upgrade-node-group-report.md`",
        "`upgrade-node-group-report.json`",
        "`soperator-upgrade-report.md`",
        "`soperator-upgrade-report.json`",
    ):
        assert report_name in design_flat
    assert "JSON `stage_verification` details" in design_flat
    assert "JSON detail files referenced from those Markdown reports" in design_flat
    assert "external-upgrade-owned actions are no longer selected" in design_flat
    assert "future normal reconciliation can use render/deploy" in design_flat
    assert "If the report still shows any pending phase other than `none`" in design_flat
    assert "rerun the same `ext-soperator upgrade ... --execute --approve` command" in design_flat
    assert (
        "If the final report shows `Pending phase: none` but the post-upgrade config refresh was skipped"
        in design_flat
    )
    assert (
        "`nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run`" in design
    )
    assert (
        "dry-run plan groups target discovery, versions, the full locked path, completed/current/remaining segments"
    ) in design_flat
    assert "external node-template rollout, phases, execution controls" in design_flat
    assert (
        "`--execute --approve` refreshes discovery, validates the accepted onboarding analysis"
        in design_flat
    )
    assert (
        "rechecks the live source release and full discovery fingerprint, creates a restore-capable backup before the first mutation for new/replacement-cluster restore only"
        in design_flat
    )
    assert "The external stage model is explicit" in design_flat
    assert (
        "execute preflight refreshes live discovery, verifies source release/fingerprint"
        in design_flat
    )
    assert "validation hold verifies MK8s, target Soperator" in design_flat
    assert "every executed stage runs a fast stage-scoped verification" in design_flat
    assert "including the post-MK8s validation boundary" in design_flat
    assert "final post-upgrade MK8s and Helm readiness checks" in design_flat
    assert "`phase_state[<stage>].fast_verification`" in design_flat
    assert "JSON `stage_verification` array" in design_flat
    assert "completion writes the external upgrade reports" in design_flat
    assert "The managed stage model is explicit" in design_flat
    assert "planning/dry-run resolves chart and MK8s target intent" in design_flat
    assert "Kubernetes minor upgrades must follow provider-supported hops" in design_flat
    assert (
        "target GPU stack reconciliation phase when paired with external upgrade work"
        in design_flat
    )
    assert (
        "advances the selected accepted external MK8s control-plane/node-template hop, target GPU stack reconciliation phase when paired with external upgrade work, storage, copy, compute"
        in design_flat
    )
    assert "External node-template work is one Kubernetes minor hop per `ext-soperator upgrade` run" in (
        design_flat
    )
    assert "discovered PVC/PV sizes as lower bounds" in design_flat
    assert "does not attempt a storage shrink" in design_flat
    assert "persists `apps.charts[].placements.*` from discovered node-group ids" in design_flat
    assert "select the mixed Soperator profile" in design_flat
    assert "`apps.charts[].placements.worker-cpu`" in design
    assert "`apps.charts[].placements.worker-gpu`" in design
    assert "Pyxis to optional and clear the importer path" in design_flat
    assert "Chart-managed MariaDB adoption defaults to `compute-csi-default-sc`" in design_flat
    assert "Chart-managed MariaDB defaults to `compute-csi-default-sc`" in readme_flat
    assert "preserves the live `SlurmCluster` resource name in `values.clusterName`" in design_flat
    assert "no compatible Helm release version is detected" in design_flat
    assert "exact committed upgrade compatibility profile rows" in design_flat
    assert "known major-generation profile group" in design_flat
    assert (
        "older same-name source-family Helm records are treated as stale discovery evidence in the saved report"
        in design_flat
    )
    assert (
        "do not trigger the source-version recovery prompt or selected onboarding work"
        in design_flat
    )
    assert "Helm release discovery enumerates all namespaces" in design_flat
    assert "stores only Soperator-like releases in the discovery bundle" in design_flat
    assert "reports known Soperator release names from non-standard namespaces" in design_flat
    assert "`helm-release-detected`" in design
    assert "`soperator_migration_profiles.yaml`" in design
    assert "local `.nebius-cxcli/ext-soperator-upgrades/` timeout-guarded checkpoint" in design_flat
    assert ".nebius-cxcli/soperator-upgrades/<target>/checkpoint.json" in design
    assert (
        "finish `ext-soperator upgrade` and checkpointed `soperator upgrade` runs from the same laptop"
        in design_flat
    )
    assert (
        "resume checkpoints are local under `.nebius-cxcli/ext-soperator-upgrades/<target>/`"
        in design_flat
    )
    assert "normal `validate`, `render`, and `deploy` can run from any workstation" in design_flat
    assert (
        "handles Slurm jobs on affected external node-template workers and all live "
        "worker NodeSets before target chart reconciliation through the `--job-policy` "
        "wait, cancel, requeue, or requeue-hold decision state"
    ) in design_flat
    assert "Slurm rejects the scoped node filter" in design_flat
    assert "`generated/reports/ext-soperator-upgrade-report.json`" in design_flat
    assert "auto-detects source worker node groups" in design_flat
    assert "`slurm.nebius.ai/nodeset` worker labels" in design_flat
    assert "runs a strict net-new quota preflight before the first mutation" in design_flat
    assert "target service-role node groups that do not already exist" in design_flat
    assert "Existing worker node groups are preserved in place" in design_flat
    assert "Reruns are action-idempotent" in design_flat
    assert (
        "`deploy.targets[].soperator_onboarding.actions` list defines the desired work"
        in design_flat
    )
    assert "rechecks completed action phases against live state before skipping them" in design_flat
    assert "Rerunning `ext-soperator onboard` remains read-only" in design_flat
    assert "refreshes the source discovery bundle with provider template evidence" in design_flat
    assert "current/target Kubernetes version fields" in design_flat
    assert (
        "removes `upgrade-external-node-template` only when the live control plane and every discovered node-group template already match"
        in design_flat
    )
    assert "Missing, partial, or errored provider evidence remains conservative" in design_flat
    assert "`waiting-rollout` on the external-node-template checkpoint" in design_flat
    assert "Before completion, cxcli verifies the external MK8s control plane" in design_flat
    assert "discovered Nebius node-group provider readiness" in design_flat
    assert "before validation-and-rollback hold" in readme_flat
    assert "runs before validation hold and again before completion" in design_flat
    assert "deletes suspended old source-family Flux HelmRelease records" in readme_flat
    assert "deletes suspended old source Flux HelmRelease records" in design_flat
    assert "legacy source-family ActiveChecks CronJobs/jobs/pods" in readme_flat
    assert "legacy source-family ActiveChecks CronJobs/jobs/pods" in design_flat
    assert "retired by stale Helm storage revision before target readiness lookup" in design_flat
    assert (
        "external-upgrade-owned external node-group template changes, including Kubernetes version, node OS image, Nebius-image GPU stack, and aligned SFS filesystem attachments"
        in design_flat
    )
    assert "does not create parallel worker node groups" in design_flat
    assert (
        "zero-surge quiesces login workloads, one-node service workloads, "
        "and known drain-blocking webhook replicas"
    ) in design_flat
    assert "worker groups default to zero-surge" in design_flat
    assert "worker_wave_percent: 1" in readme
    assert "worker_group_strategy:" in readme
    assert "worker_wave_percent: 1" in design
    assert "worker_group_strategy:" in design
    assert "`max_surge_count` temporary surge node(s) per active service group" in design_flat
    assert "worker-health, and Slurm queue preflights pass" in design_flat
    assert "With safe-surge, remediation counts `max_surge_count`" in design_flat
    assert "requires the Slurm queue to be empty before mutation" in design_flat
    assert "the MK8s control plane first, then updates service-role node groups" in design_flat
    assert "phase end, and pending gates" in design_flat
    assert "configured node-group strategy is restored" in readme_flat
    assert "configured node-group strategy is restored" in design_flat
    assert "ignored by cxcli-managed deployments `.gitignore` files" in design_flat
    assert "creates or reuses aligned jail, controller-spool, and accounting SFS" in design_flat
    assert "runs Kubernetes data-copy Jobs when old and target PVC pairs exist" in design_flat
    assert "required Soperator deployment snapshot" in design_flat
    assert "does not start Slurm jobs" in design_flat
    assert "target `SlurmCluster`, and worker `NodeSet` resources" in design_flat
    assert "`acceptance-test smoke --suite slurm`" in design_flat
    assert "Acceptance smoke and benchmark commands require `--suite`" in design_flat
    assert (
        "after a suite is selected, they run all generated targets when `--target` is omitted"
        in design_flat
    )
    assert "They resolve target handoff from `generated/reports/deploy-report.md`" in design_flat
    assert "`acceptance-test benchmark`" in design_flat
    assert "`deploy-smoke-report-<target>.json`" in design_flat
    assert "`deploy-gpu-stack-readiness-report-<target>.json`" in design_flat
    assert "`deploy-gpu-visibility-report-<target>.json`" in design_flat
    assert "`cluster-inventory-report-<target>.json`" in design_flat
    assert "`test_purpose`, `mode`, `scope`, `kind`, and `target_ref`" in design_flat
    assert "nebius-cxcli-soperator-cluster-validation/v2" in design_flat
    assert "command `stdout`/`stderr` are arrays of lines" in design_flat
    assert (
        "structured `partition_hostnames`, `gpu_driver_jail`, and `gpu_allocations` "
        "arrays with all-node evidence" in design_flat
    )
    assert "including the evidence source for each GPU allocation node" in design_flat
    assert "Explicit `acceptance-test smoke --suite slurm` runs the Slurm CLI" in readme_flat
    assert "Slurm nodes reported as `inval` remain unhealthy there" in readme_flat
    assert "same catalog-owned post-render patches that Flux would apply" in design_flat
    assert "`generated/reports/ext-soperator-upgrade-report.md`" in design
    assert "resume relies on phase checkpoints" in design_flat
    assert "interactive spinner backed by phase-aware status snapshots" in design_flat
    assert (
        "canonical phase id, operator-facing top-level stage (`MK8s Node Upgrades` or `Soperator Upgrade`), human-readable phase label"
        in readme_flat
    )
    assert (
        "canonical phase id, operator-facing top-level stage (`MK8s Node Upgrades` or `Soperator Upgrade`), human-readable phase label"
        in design_flat
    )
    assert "Storage phases show aligned SFS/PVC copy progress" in readme_flat
    assert "separate `Node groups:` and `Nodes:` sections" in readme_flat
    assert "node-group readiness stays in the first section" in readme_flat
    assert "node-level rollout transitions such as `replacing (cordoned)`" in readme_flat
    assert "real problem-node details such as `NotReady (down)`" in readme_flat
    assert "Transition nodes and down states are highlighted" in readme_flat
    assert "Slurm worker names/states" in readme_flat
    assert "timeout-guarded checkpoints" in design_flat
    assert "remains blocked until the explicit migration executor is implemented" not in design_flat
    assert "component add apps:soperator@<target>" in design_flat
    assert "compatibility path" in design_flat
    assert "`production-cluster` materializes the complete MK8s+SFS+Soperator" in design
    assert (
        "ext-soperator onboard <config.yaml-or-deployments-root>` resolves the selected"
        in design_flat
    )
    assert "`apps.charts[].placements` from discovered inventory and the selected profile" in design
    assert "day-2 app edits and Soperator Helm chart version edits do not invalidate" in design_flat
    assert "Soperator upgrade profiles are the compatibility source of truth" in design
    assert "release-scoped and component-scoped" in design_flat
    assert (
        "generator_scope: chart-tarball-crd-template-image-and-slurm-contract-fingerprints"
        in design
    )
    assert "per-component chart archive, CRD, rendered-template source" in design_flat
    assert "per-component chart tarball, CRD, template, image" in readme_flat
    assert "Profile groups also declare the generation-level node label layout" in design_flat
    assert "Profile groups also own execution-time takeover differences" in design_flat
    assert "deletes the source Soperator admission webhooks declared by the profile" in (
        design_flat
    )
    assert "scales down the source Soperator controller deployment declared by the profile" in (
        design_flat
    )
    assert "Profile groups also record the node-role label layout" in readme_flat
    assert "Profile groups also declare release-family takeover behavior" in readme_flat
    assert "deletes the source Soperator admission webhooks declared by the profile" in (
        readme_flat
    )
    assert "source Soperator controller deployment declared by the profile" in readme_flat
    assert "soperator_migration_profiles.yaml" in readme
    assert "flux-system-soperator-fluxcd-*" in readme
    assert "soperator-fluxcd-values" in readme
    assert "old source-family Helm chart records" in design_flat
    assert "creates or reuses service-role groups for `system`, `controller`" in readme_flat
    assert "accepts either label key for source-era scheduling" in readme_flat
    assert "normalizes current Nodes toward `slurm.nebius.ai/nodeset-name`" in readme_flat
    assert (
        "Worker roles continue to map to the preserved detected worker node groups" in design_flat
    )
    assert "SlurmCluster`, `NodeSet`, `NodeConfigurator`" in design
    assert "In GPU profile-backed MK8s flows" in design
    assert "CPU-only Soperator profiles skip and prune the inactive GPU helper scope" in design
    assert "during runtime config normalization" in design_flat


def test_design_defines_soperator_profile_policy_model() -> None:
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    design_flat = _squash(design)

    assert "## Soperator" in design
    assert "`component_sources.yaml` keeps the app source" in design
    assert "A non-empty `repo` is an explicit Helm source override in the config row" in (
        design_flat
    )
    assert "change only the row `version`" in design_flat
    assert "Soperator is source-backed but not HelmRelease-backed" in design
    assert "`wizard_profile: soperator`" in design
    assert "`soperator_nodesets_profile` table stays in `component_cli_settings.yaml`" in design
    assert "NodeSet profile chooses the Slurm worker layout" in design
    assert "Partition profile chooses Slurm queues and scheduling policy" in design
    assert "Slurm accounting, SlurmDBD, and the chart-managed accounting database stay enabled" in (
        design_flat
    )
    assert "QoS reconciliation is separate from selecting a partition profile" in design
    assert "Topology profile controls Slurm locality scheduling" in design
    assert "Node group mapping connects Slurm roles to MK8s node groups" in design
    assert "curated CPU service-role count helpers" in design
    assert "`inputs.soperator.system_node_count`" in design
    assert "`worker_cpu_nodes_per_group` for CPU workers" in design_flat
    assert "`worker_gpu_nodes_per_group` for GPU workers" in design_flat
    assert "The Helm chart remains the Slurm resource owner" in design
    assert "moving the prompt map out of YAML does not change `config.yaml`" in design_flat


def test_readme_explains_soperator_slurm_concept_ownership() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("Slurm concepts used by the bundled profiles:", 1)[1].split(
        "The guided partition profiles are intentionally policy-sized:",
        1,
    )[0]
    section_flat = _squash(section)

    for term in (
        "`SlurmCluster`",
        "`NodeSet`",
        "`NodeConfigurator`",
        "`Partition`",
        "`PriorityTier`",
        "`PriorityJobFactor`",
        "`PriorityWeightPartition`",
        "Fairshare",
        "QOS",
        "`PreemptType`",
        "`PreemptMode`",
        "Niceness",
        "`AccountingStorageEnforce`",
        "`EnforcePartLimits`",
    ):
        assert term in section

    assert "The Helm chart owns persistent Slurm resources" in section_flat
    assert "cxcli selects bundled profiles and writes Helm values" in section_flat
    assert "per-job choices such as `--qos`, `--nice`, `--time`, and `--requeue`" in section_flat
    assert "The chart types `schedulingConfig.priorityWeights.partition`" in section_flat
    assert "does not currently type per-partition `PriorityJobFactor`" in section_flat
    assert "Use per-partition `config: PriorityJobFactor=<n>` today" in section_flat
    assert "`inputs.soperator.*_node_count` helpers" in section_flat
    assert "CPU service-role counts are independent of worker sharding" in section_flat
    assert "values.schedulingConfig.accountingStorageEnforce" in section_flat
    assert "values.schedulingConfig.enforcePartLimits" in section_flat
    assert "while partition `AllowQos` remains chart-rendered partition policy" in section_flat
    assert "Real fairshare is tenant policy" in section_flat
    assert "Managed Soperator cannot run this self-managed chart hook" in section_flat
    assert "there is no `PriorityWeightNice` setting to model in Helm" in section_flat
    assert "Actual preemption for these queues requires an explicit" not in section_flat


def test_readme_soperator_shape_default_partitions_match_render_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("How to read the common outputs:", 1)[1].split(
        "For operator changes, prefer profile-level config first:",
        1,
    )[0]
    section_flat = _squash(section)

    assert "`shape-default` should show only the selected worker-shape partitions" in section
    assert (
        "CPU-only shows `cpu*`, GPU-only shows `gpu*`, and mixed CPU+GPU shows `cpu*` plus `gpu`"
        in section_flat
    )
    assert "`shape-default` CPU profile should show `hidden` and `cpu` partitions" not in section
    assert "internal `hidden` readiness partition during render" in section_flat


def test_readme_guides_soperator_slurm_checks_through_login_service() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("After the cluster is provisioned, connect to the Slurm", 1)[1].split(
        "How to read the common outputs:",
        1,
    )[0]
    section_flat = _squash(section)

    assert "chart `clusterName` from the MK8s target id" in section_flat
    assert "service `soperator-cluster1-login-svc`" in section_flat
    assert "kubectl get svc soperator-cluster1-login-svc -n soperator" in section
    assert "replace `soperator-cluster1` with that value" in section
    assert "ssh root@<login-external-ip>" in section
    assert "Once connected, run the Slurm inspection commands directly" in section
    assert "scontrol show partition" in section
    assert "squeue -l" in section
    assert "Run these smoke checks from the same SSH session on the Slurm login node" in (
        section_flat
    )
    assert "srun -p cpu -N1 -n1 /bin/hostname" in section
    assert "examples/slurm-jobs/" in section
    assert "./submit-soperator-smoke.sh --kind cpu --partition cpu --count 10" in section
    assert (
        "./submit-soperator-smoke.sh --kind gpu --partition gpu --count 10 --gpus-per-job 1"
        in section
    )
    assert "nebius-cxcli soperator upgrade CONFIG_YAML --target TARGET" in section_flat
    assert "--to-chart-version TARGET_VERSION" in section_flat
    assert "--job-policy interactive" in section_flat
    assert "Do not prefix normal SSH-session commands with chroot" not in section
    assert "kubectl exec into the sshd container" not in section
    assert "chroot /mnt/jail srun" not in section
    assert "chroot /mnt/jail sbatch" not in section
    assert "kubectl -n soperator exec login-0 -c sshd --" not in section


def test_docs_define_validation_command_boundaries() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert "- `validate-sources`" in readme
    assert "active `component_sources.yaml` catalog" in readme
    assert "- `validate <config.yaml>`" in readme
    assert "project config contract and deployment-readiness shape" in readme
    assert "Use `--target <target-id>` to validate one target-scoped Grafana row" in readme
    assert "the target id is the normalized cluster resource name" in readme
    assert "each target must resolve an explicit kube context" in readme
    assert "accepted only when its generated Nebius name matches that target" in readme
    assert "Use `--target <target_ref>`" not in readme
    assert "- `validate-generated <generated-dir>`" in readme
    assert "existing generated bundle without rerendering it" in readme
    assert (
        "strict readiness, VPC networking preflight, backend auth/bootstrap, "
        "live quota/capacity, Terraform validation"
    ) in readme
    assert "### `validate-sources [component_sources.yaml]`" in design
    assert "### `validate <config.yaml>`" in design
    assert "Supports `--target <target-id>` for multi-target configs." in design
    assert "Target-scoped rows must resolve an explicit kube context" in design
    assert "current kubeconfig context is accepted only when its generated Nebius name" in (
        " ".join(design.split())
    )
    assert "### `validate-generated <generated-path>`" in design
    assert (
        "strict readiness, VPC networking preflight, backend auth preparation, "
        "live quota/capacity, Terraform validation"
    ) in design


def test_docs_define_target_scoped_deploy_validation_report_filtering() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert (
        "A plain deploy and `--all-targets` report every selected target. "
        "When a run selects one target with `--target <target-id>`, the refreshed "
        "validation section is scoped to that selected target instead of marking "
        "unselected target validations as not run."
    ) in readme
    assert (
        "Plain deploy and `--all-targets` report every selected target. "
        "When a run selects one target with `--target <target-id>`, the refreshed "
        "validation section includes only that target's validations."
    ) in design


def test_docs_define_deploy_default_all_targets_boundary() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert (
        "A plain `deploy <config.yaml>` reconciles every generated target by default; "
        "use `deploy --target <target-id>` to narrow one target or `deploy --all-targets` "
        "to spell out the default."
    ) in readme
    assert (
        "`flux apply`, `flux destroy`, and `flux bootstrap` still require "
        "`--target <target-id>` or `--all-targets`"
    ) in readme
    assert (
        "Plain `deploy <config.yaml>` reconciles every generated target by default; "
        "`deploy --target <target-id>` narrows to one target, and `deploy --all-targets` "
        "is an explicit spelling of the default."
    ) in design
    assert (
        "Direct Flux commands that need Kubernetes access, such as `flux apply`, "
        "`flux destroy`, or `flux bootstrap`, still select one target"
    ) in design


def test_docs_define_mysterybox_eso_name_resolution_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    for text in (readme, design):
        assert "Secret names are rejected" not in text
        assert "secret names are rejected" not in text
        assert "`secret_name`" in text
        assert "Terraform-created `mbsec-...` ID" in text or "Terraform `secret_ids` output" in text
        assert "auto-primary-version-pinning" in text
        assert "manual-version-pinning" in text
        assert "remoteRef.version" in text
        assert "refreshInterval: 15m" in text
        assert "spec.data[].remoteRef" in text
        assert "`mysterybox_instance_id`" in text
        assert "externally managed MysteryBox Secrets" in text


def test_docs_define_soperator_notifier_mysterybox_no_action_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    for text in (readme, design):
        assert "no-action deploy" in text
        assert "MysteryBox" in text
        assert "`values.soperator-notifier.slack.webhookSource`" in text
        assert "`values.soperator-notifier.slack.mysterybox.secretId`" in text
        assert "`values.soperator-notifier.slack.existingSecret`" in text
        assert "`values.soperator-notifier.slack.existingSecretKey`" in text
        assert "ExternalSecret" in text
        assert "primary MysteryBox version" in text


def test_docs_define_wizard_string_lists_as_comma_separated() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    assert "`list(string)` / `set(string)`" in readme
    assert "`ns1,ns2`" in readme
    assert "simple string lists prompt for comma-separated values" in design
    assert "simple `list(string)` prompts use comma-separated input" in design


def test_docs_define_mk8s_node_group_service_account_default() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    readme_flat = _squash(readme)
    design_flat = _squash(design)

    assert "Node groups default to no service account assignment" in readme_flat
    assert "use an existing service account ID or create one by name" in readme_flat
    assert "Node-group service-account assignment defaults to none" in design_flat
    assert (
        "only writes `service_account` when the operator selects an existing "
        "service account ID or a create-by-name path"
    ) in design_flat


def test_docs_define_create_app_selection_and_vpc_guided_subnet_contract() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")

    for text in (readme, design):
        normalized = " ".join(text.split())
        assert "opens app chart selection only after" in normalized
        assert "MK8s target" in normalized
        assert "skips the new-network name prompt" in normalized
        assert "network with no subnets" in normalized
        assert "inputs.network.existing_id" in normalized
        assert "Create a new VPC network" in normalized
        assert "recommend `default-network` when it exists" in normalized
        assert "inputs.network.ipv4_private_cidrs" in normalized
        assert "inputs.network.ipv4_private_pool_ids" in normalized
        assert "unassigned existing private pool" in normalized
        assert "inputs.network.ipv4_private_source_pool_id" in normalized
        assert "inputs.network.ipv4_public_pool_ids" in normalized
        assert "default public pool" in normalized
        assert "default route table" in normalized
        assert "prompt is live-only" in normalized
        assert "custom private" in normalized
        assert "10.8.0.0/13" in normalized
        assert "172.16.0.0/12" in normalized
        assert "172.16.0.0/13" not in normalized
        assert "192.168.0.0/16" in normalized
        assert "default private-pool" in normalized
        assert "subnet CIDRs" in normalized
        assert "Every declared subnet uses explicit private CIDRs" in normalized
        assert "one or more comma-separated explicit private CIDRs" in normalized
        assert "adds any out-of-parent custom subnet CIDR" in normalized
        assert "parent network IP space" in normalized
        assert "live private allocations" in normalized
        assert "bounded existing-network" in normalized
        assert "attached private pool on the selected live network" in normalized
        assert "use_network_private_pools=false" in normalized
        assert "same pool tree" not in normalized
        assert "without creating another detached root pool" not in normalized
        assert "immediately creates and attaches a live private VPC pool" not in normalized
        assert "asks for explicit confirmation" not in normalized
        assert "externally managed" in normalized
        assert "default-network ranges already attached" in normalized
        assert "Explicit subnet CIDRs must fit" in normalized
        assert "must not overlap" in normalized
        assert "selected network" in normalized
        assert "guided" in normalized
        assert "subnets" in normalized


def test_vpc_module_examples_use_explicit_subnet_private_cidrs() -> None:
    module_readme = (MONOREPO_ROOT / "platform-infra" / "modules" / "vpc" / "README.md").read_text(
        encoding="utf-8"
    )
    minimal_example = (
        MONOREPO_ROOT / "platform-infra" / "modules" / "vpc" / "examples" / "minimal" / "main.tf"
    ).read_text(encoding="utf-8")

    for text in (module_readme, minimal_example):
        assert "use_network_private_pools = false" in text
        assert "ipv4_private_cidrs" in text
    assert "fail instead of guessing containment" in module_readme


def test_docs_list_all_builtin_wizard_profiles_and_static_sources() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    design_profile_list = _section(
        design,
        "Built-in component `wizard_profile` names currently include:",
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
    assert "`components.apps.grafana.defaults.values.dashboards.<folder>.<dashboard>`" in design
    assert (
        "The `grafana` command is the operator workflow for bringing external "
        "dashboards into that catalog"
    ) in normalized_design
    assert "`components.apps.grafana.cli.dashboard_signals.<signal>` is not" in design
    assert "components.apps.grafana.cli.grafana" not in design
    assert "Dashboard source materialization workflow:" in design
    assert "`render`, `deploy`, and `validate-dashboards` do not dynamically generate" in design
    assert (
        "`grafana --export-dashboard --attach` and `grafana --dashboard-json --attach` "
        "workflows can rewrite"
    ) in normalized_design
    assert "refuses to attach JSON dashboards into a provider key" in normalized_design
    assert "It does not mutate, regenerate, or repair dashboard JSON." in normalized_design
    assert "Dashboard generation and materialization workflow:" not in design
    assert "`load_component_sources()` resolves the `json_file` relative" in design
    assert "`validate-dashboards <config.yaml>` checks the live post-deploy state" in design
    assert "Current bundled package dashboards:" in design
    assert "generated `deploy-report.md` bundled-dashboard list" in design
    assert "`kubernetes_io_hostname`" in design
    assert "`k8s_namespace_name` plus `k8s_pod_name`" in design
    assert "VM Logs binds to `Nebius Logs`, defaults to the `sp_serial` Loki bucket" in design
    assert "Live fit validation rules:" in design


def test_design_supporting_commands_include_quota_request_and_flux_targets() -> None:
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    supporting = _section(design, "## Supporting Commands", "## Idempotency Rules")
    supporting_flat = _squash(supporting)

    assert "- `quota-request <config.yaml>`" in supporting
    assert (
        "- `upgrade node-template <config.yaml> [infra:mk8s@<target>] "
        "[--to-version <major.minor>] [--to-os <os>] "
        "[--to-gpu-stack-preset <preset>]`"
    ) in supporting
    assert (
        "- `upgrade node-group <config.yaml> infra:mk8s@<target> --node-group <group>`"
        in supporting
    )
    assert "Prompts for the target selector" in supporting
    assert "one node group at a time in CPU/system-before-GPU order" in supporting_flat
    assert "copy/paste-ready repeat dry-run command" in supporting_flat
    assert "plain optional flag-value prompt" in supporting_flat
    assert "blank omits the flag and updates every managed node group" in supporting_flat
    assert "does not SSH to nodes, run apt-based Ubuntu" in supporting_flat
    assert (
        "Supports CPU node groups, GPU node groups without InfiniBand, and GPU-cluster / InfiniBand node groups through one command"
        in (supporting_flat)
    )
    assert "upgrade helm-chart <config.yaml> apps:<chart>@<target> --to-version" in supporting_flat
    assert "source-family change is the desired state" in supporting_flat
    assert "Node firmware is maintained by the Nebius hardware team" in supporting_flat
    assert "not a customer upgrade responsibility" in supporting_flat
    assert "- `ssh-jumphost <config.yaml>`" in supporting
    assert "`QuotaAllowance` reads separate from `QuotaRequest` submission" in supporting
    assert "--strategy zero-surge|safe-surge|force-delete" in supporting
    assert "--strategy-max-surge-count <n>" in supporting
    assert "Pod deletion and old-node deletion" in supporting
    assert "uses max(`1h`, `10m * target node count`)" in supporting
    assert "preflight inspection failures block non-dry runs" in supporting
    assert "Temporary node-group strategy settings" in supporting
    assert "raw config edits replace an existing node group" in supporting_flat
    assert "target quota/capacity" in supporting_flat
    assert "stops before live replacement/cutover/retirement" in supporting_flat
    assert "live executor is not enabled yet" in supporting_flat
    assert "source config is stale" in supporting
    assert "final MK8s readiness check re-reads the live control plane" in supporting_flat
    assert "requires provider node-group status" in supporting_flat
    assert (
        "verify Kubernetes version, OS, and Nebius `drivers_preset` / CUDA stack" in supporting_flat
    )
    assert "A valid row must match the requested OS" in supporting_flat
    assert "requested `drivers_preset`" in supporting_flat
    assert "- `flux apply <generated-path>`" in supporting
    assert "- `flux destroy <generated-path>`" in supporting
    assert "- `flux bootstrap <generated-path>`" in supporting
    assert "--target <target-id>` / `--all-targets`" in supporting


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
    assert "`Next step: deploy the rendered bundle:`" in design
    assert "distinct colored `nebius-cxcli deploy <config.yaml>` command line" in design
    assert "Internal rerenders used by upgrade flows suppress this helper" in design
