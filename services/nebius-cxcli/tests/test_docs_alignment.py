from __future__ import annotations

import re
import struct
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
    assert (
        "Restore is archive-driven and dry-run by default, and it is DR/new-empty-target only"
        in (design_flat)
    )
    assert "It is not same-cluster rollback" in design_flat
    assert "operators must not point restore at the original/source cluster" in design_flat
    assert (
        "fails fast for `apps:soperator@<target>` with the canonical `soperator upgrade` command"
        in (design_flat)
    )
    assert "pending ActiveChecks restore is still completed" in design_flat
    assert (
        "External Soperator adoption, storage remediation, control-plane-only upgrades, "
        "and accepted in-place or blue-green compute migration"
    ) in design_flat
    assert "  - [Soperator Lifecycle Boundaries](#soperator-lifecycle-boundaries)" in design
    assert "  - [Soperator Profile Model](#soperator-profile-model)" in design
    assert "  - [Jail Upgrade](#jail-upgrade)" in design
    assert "### Jail Upgrade" in design
    assert "active/passive rootfs slots" in design_flat
    assert "stable persistent-mount directories on the same physical jail SFS" in design_flat
    assert "checkpointed host/PVC readiness contract at two boundaries" in design_flat
    assert "`in_place_persistent_mount_adoption.status=pending`" in design_flat
    assert "backup is a restore precondition around mutation, not an upgrade phase" in (design_flat)
    assert (
        "a compatible rerun reuses the checkpointed `UP -> DOWN` partition records" in design_flat
    )
    assert "Slurm worker status can still report deferred/upgrading from checkpoint state" in (
        design_flat
    )
    assert (
        "legacy-rootfs compatibility bridge suppresses target-only OpenMetrics and "
        "`PluginDir` config"
    ) in design_flat
    assert "sets the target SConfig writer size to zero" in design_flat
    assert "actual target service account and active slot" in design_flat
    assert "non-v6 journal evidence fails closed instead of using a markerless fallback" in (
        design_flat
    )
    assert "falls back to the controller `slurmctld` container" in design_flat
    assert "replacement login and worker pod evidence" in design_flat
    assert "Slurm configuration and accounting database state are protected customer state" in (
        design_flat
    )
    assert "/mnt/jail-store/shared/data" in design_flat
    assert "The one-time migration runs only while" in design_flat
    assert "preserves ownership and permissions, symlinks, ACLs, and xattrs where supported" in (
        design_flat
    )
    assert (
        "The switch-over is not a live bind-mount flip inside an already-running consumer container"
    ) in design_flat
    assert "Prompt for a ChatGPT-generated infographic" not in design
    assert "#### In-place upgrade workflow" in design
    assert "#### Blue-green upgrade workflow" in design
    assert "same accepted service and worker node groups" in design_flat
    assert "keeps source node-group templates immutable" in design_flat
    workflow_images = {
        "soperator-in-place-upgrade-workflow.png": "Soperator In-Place Upgrade Workflow",
        "soperator-blue-green-upgrade-workflow.png": "Soperator Blue-Green Upgrade Workflow",
    }
    for image_name, title in workflow_images.items():
        assert f"]({image_name})" in design
        workflow_image = REPO_ROOT / "docs" / image_name
        assert workflow_image.is_file()
        assert workflow_image.stat().st_size > 0
        workflow_source = workflow_image.with_suffix(".svg")
        assert workflow_source.is_file()
        workflow_source_text = workflow_source.read_text(encoding="utf-8")
        assert f'<title id="title">{title}</title>' in workflow_source_text
        for component_label in (
            "CONTROLLER",
            "LOGIN",
            "ACCOUNTING",
            "SYSTEM",
            "WORKERS",
            "JAIL ROOTFS",
        ):
            assert component_label in workflow_source_text

        if image_name == "soperator-blue-green-upgrade-workflow.png":
            assert "CAPACITY PREFLIGHT" in workflow_source_text
            assert (
                "every target replacement group must fit quota before mutation"
                in workflow_source_text
            )
            assert "Full-size source/target overlap is about 2× per role" in workflow_source_text
            assert "worker targets may bootstrap incrementally" in workflow_source_text

    focused_continuity_images = {
        "soperator-controller-bridge-ha-continuity.png": (
            "Soperator Controller Bridge HA Continuity",
            (
                "source singleton",
                "target-version HA proven",
                "only UP controller",
                "scheduling restored first",
                "After Login Continuity releases its",
            ),
        ),
        "soperator-login-node-continuity.png": (
            "Soperator Login Node Continuity",
            (
                "same UID, address, allocation",
                "2 Ready replicas on distinct nodes",
                "≥ 1 exact backend",
                "Active SSH sockets do not gate",
                "may drop old connections",
                "source availability hold",
            ),
        ),
    }
    for image_name, (title, contract_texts) in focused_continuity_images.items():
        image = REPO_ROOT / "docs" / image_name
        source = image.with_suffix(".svg")
        assert image.is_file()
        assert image.stat().st_size > 0
        assert source.is_file()
        source_text = source.read_text(encoding="utf-8")
        assert f'<title id="title">{title}</title>' in source_text
        for contract_text in contract_texts:
            assert contract_text in source_text
        assert f"]({source.name})" in design

    controller_source = (
        REPO_ROOT / "docs" / "soperator-controller-bridge-ha-continuity.svg"
    ).read_text(encoding="utf-8")
    assert (
        controller_source.index("Login Continuity releases its")
        < (controller_source.index("restore exact partitions"))
        < controller_source.index("delete the stopped bridge")
    )
    login_source = (REPO_ROOT / "docs" / "soperator-login-node-continuity.svg").read_text(
        encoding="utf-8"
    )
    assert login_source.index("may drop old connections") < login_source.index(
        "the stable Service and target backend remain"
    )
    assert (
        login_source.index("Prove a distinct Ready target peer")
        < login_source.index("Require preserved host key and stable Service")
        < login_source.index("source availability hold")
    )


def test_architecture_diagrams_are_referenced_once_and_explained() -> None:
    diagram_explanations = {
        "soperator-in-place-upgrade-workflow": (
            "The in-place diagram shows how cxcli establishes controller and login continuity"
        ),
        "soperator-blue-green-upgrade-workflow": (
            "The blue-green diagram shows how cxcli keeps source node-group templates immutable"
        ),
        "soperator-controller-bridge-ha-continuity": (
            "The controller diagram traces single-writer authority"
        ),
        "soperator-login-node-continuity": ("The login diagram shows the availability handoff"),
        "jail-rootfs-active-passive-storage": (
            "The Jail storage diagram separates replaceable rootfs generations"
        ),
    }
    expected_assets = {
        f"{basename}.{suffix}" for basename in diagram_explanations for suffix in ("png", "svg")
    }
    for asset_name in expected_assets:
        asset = REPO_ROOT / "docs" / asset_name
        assert asset.is_file()
        assert asset.stat().st_size > 0

    documents = (
        ((REPO_ROOT / "README.md").read_text(encoding="utf-8"), "docs/"),
        ((REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8"), ""),
    )
    for document, relative_prefix in documents:
        flattened = _squash(document)
        for basename, explanation in diagram_explanations.items():
            assert document.count(f"{relative_prefix}{basename}.png") == 1
            assert document.count(f"{relative_prefix}{basename}.svg") == 1
            assert explanation in flattened


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
    assert (
        "- `soperator scale-down`: `--target`, `--nodeset`, `--to-workers`, "
        "`--worker-ordinal`, `--namespace`, `--kube-context`, `--job-policy`, "
        "`--cancel-job`, `--requeue-job`, `--job-wait-timeout`, "
        "`--job-refresh-interval`, `--dry-run/--execute`, `--approve/--no-approve`, "
        "`--interactive/--no-interactive`"
    ) in common_flags_flat
    assert (
        "- `soperator scale-up`: `--target`, `--nodeset`, `--to-workers`, "
        "`--worker-ordinal`, `--namespace`, `--kube-context`, "
        "`--dry-run/--execute`, `--approve/--no-approve`, "
        "`--interactive/--no-interactive`"
    ) in common_flags_flat
    assert "Node-layer upgrades" not in common_flags
    assert "--disruption-policy" not in supporting
    assert "allow-unavailable" not in supporting
    assert (
        "- `soperator upgrade`: `--target`, `--to-chart-version`, `--to-k8s-version`, "
        "`--to-os`, `--to-gpu-stack-preset`, `--node-group-strategy`, "
        "`--zero-surge-max-unavailable`, `--strategy-max-surge-count`, "
        "`--worker-drain-timeout`, `--max-parallel-worker-groups`, `--backup-dir`, "
        "`--populate-jail-refresh`, "
        "`--jail-sfs-resize-policy`, `--jail-sfs-resize-to-gib`, "
        "`--job-policy`, `--cancel-job`, `--requeue-job`, `--job-wait-timeout`, "
        "`--job-refresh-interval`, `--dry-run`, `--execute`, `--approve/--no-approve`, "
        "`--approve-remediation/--no-approve-remediation`, "
        "`--interactive/--no-interactive`"
    ) in common_flags_flat
    assert (
        "- `soperator jobs`: `--target`, `--acknowledge-login-exit`, "
        "`--authorize-login-timeout-continuation`"
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
        "- `ext-soperator scale-down`: `--project-id`, `--cluster-id`, `--target`, "
        "`--namespace`, `--kube-context`, `--nodeset`, `--to-workers`, "
        "`--worker-ordinal`, `--job-policy`, `--cancel-job`, `--requeue-job`, "
        "`--job-wait-timeout`, `--job-refresh-interval`, `--dry-run/--execute`, "
        "`--approve/--no-approve`, `--interactive/--no-interactive`"
    ) in common_flags_flat
    assert (
        "- `ext-soperator scale-up`: `--project-id`, `--cluster-id`, `--target`, "
        "`--namespace`, `--kube-context`, `--nodeset`, `--to-workers`, "
        "`--worker-ordinal`, `--dry-run/--execute`, `--approve/--no-approve`"
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
        "`--to-k8s-version`, `--source-version`, "
        "`--validate-sources/--no-validate-sources`, "
        "`--no-interactive`"
    ) in common_flags_flat
    assert (
        "- `ext-soperator upgrade`: `--target`, `--backup-dir`, "
        "`--populate-jail-refresh`, `--jail-persistent-mount`, "
        "`--jail-sfs-resize-policy`, `--jail-sfs-resize-to-gib`, "
        "`--job-policy`, `--cancel-job`, `--requeue-job`, `--job-wait-timeout`, "
        "`--job-refresh-interval`, `--dry-run/--execute`, "
        "`--approve/--no-approve`, "
        "`--approve-remediation/--no-approve-remediation`, "
        "`--slurm-scheduling-pause/--no-slurm-scheduling-pause`, "
        "`--interactive/--no-interactive`"
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
        "| Jail rootfs refresh or active/passive switch-over | [Jail Upgrade](#jail-upgrade) |"
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
    assert "  - [CXCLI Managed Soperator Upgrade](#cxcli-managed-soperator-upgrade)" in toc
    assert "  - [External Soperator Onboarding](#external-soperator-onboarding)" in toc
    assert "  - [External Soperator Upgrade](#external-soperator-upgrade)" in toc
    assert "  - [Jail Upgrade](#jail-upgrade)" in toc
    assert (
        "  - [Soperator Slurm Scheduling And Command Examples](#soperator-slurm-scheduling-and-command-examples)"
        in toc
    )
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
    assert "### CXCLI Managed Soperator Upgrade" in soperator
    assert "### External Soperator Onboarding" in soperator
    assert "### External Soperator Upgrade" in soperator
    assert "### Jail Upgrade" in soperator
    assert "### Soperator Slurm Scheduling And Command Examples" in soperator
    assert "### Soperator Rules and Safety Checks" in soperator
    managed_upgrade_section = _section(
        soperator,
        "### CXCLI Managed Soperator Upgrade",
        "### External Soperator Onboarding",
    )
    external_onboarding_section = _section(
        soperator,
        "### External Soperator Onboarding",
        "### External Soperator Upgrade",
    )
    external_upgrade_section = _section(
        soperator,
        "### External Soperator Upgrade",
        "### Jail Upgrade",
    )
    managed_upgrade_flat = _squash(managed_upgrade_section)
    assert "For external Soperator clusters" not in managed_upgrade_section
    assert "`ext-soperator onboard` plus `ext-soperator upgrade`" not in (managed_upgrade_section)
    assert "For external Soperator clusters, start with onboarding" in (external_onboarding_section)
    assert "Underlying MK8s upgrade ownership is different" in external_onboarding_section
    assert "--login-session-policy" not in managed_upgrade_section
    assert "--login-session-drain-timeout" not in managed_upgrade_section
    assert "same unconditional login-availability contract" in managed_upgrade_flat
    assert "without waiting for established SSH" in managed_upgrade_section
    assert "--login-session-policy" not in external_upgrade_section
    assert "--login-session-drain-timeout" not in external_upgrade_section
    assert "External login availability is mandatory and has no session-drain gate" in (
        external_upgrade_section
    )
    assert "connections may drop and can reconnect through the stable Service" in (
        external_upgrade_section
    )
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
    assert (
        ".nebius-cxcli/soperator-clusters/<cluster-key>/ext-soperator-upgrade/campaigns/<campaign-id>/checkpoint.json"
        in soperator
    )
    assert (
        ".nebius-cxcli/soperator-clusters/<cluster-key>/soperator-upgrade/checkpoint.json"
        in soperator
    )
    assert "these journals stay local" in soperator_flat
    assert "completed campaign remains the audit record" in soperator_flat
    assert "`nebius-cxcli ext-soperator onboard <config.yaml-or-deployments-root>`" in soperator
    assert (
        "`nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run`"
        in soperator
    )
    assert (
        "The command has no implicit execution mode: pass `--dry-run` to inspect the "
        "plan, or pass `--execute --approve` for an approved mutating run." in soperator_flat
    )
    assert "`nebius-cxcli soperator backup <config.yaml> --target <target>`" in soperator
    assert "`nebius-cxcli soperator restore <backup.tar.gz> --execute --approve`" in (soperator)
    assert "nebius-cxcli soperator scale-down <config.yaml> --target <target>" in soperator
    assert "Ephemeral NodeSets change active worker ordinals through `NodeSetPowerState`" in (
        soperator_flat
    )
    assert "without leaving live Slurm nodes drained" in soperator_flat
    assert "they do not live-patch around Flux/Terraform ownership" in soperator_flat
    assert "reconcile the live NodeSet and managed MK8s host capacity" in soperator_flat
    assert "Explicit non-ephemeral ordinal removal is tail-only" in soperator_flat
    assert "nebius-cxcli ext-soperator backup <config.yaml> --target <target>" in soperator
    assert "nebius-cxcli ext-soperator scale-down --project-id <project-id>" in soperator
    assert "`--kube-context` is still required for Kubernetes and Slurm access" in (soperator_flat)
    assert "replace worker node groups externally, scale back up" in soperator_flat
    assert (
        "Core external Soperator operations stay SSH-free from the operator workstation"
        in soperator_flat
    )
    assert (
        "`ext-soperator onboard`, `ext-soperator backup`, and `ext-soperator upgrade` "
        "use project-scoped Nebius API access for cloud resources and Kubernetes "
        "API/kubeconfig access for cluster, Soperator, and Slurm work" in soperator_flat
    )
    assert (
        "Slurm commands such as `scontrol`, `squeue`, and `sacctmgr` run through "
        "`kubectl exec` into the Soperator login or controller pods" in soperator_flat
    )
    assert (
        "Login-node SSH keys remain a separate human access contract for operator "
        "sessions and manual smoke checks" in soperator_flat
    )
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
    assert soperator.index("### Soperator Command Map") < soperator.index(
        "### CXCLI Managed Soperator Clusters"
    )
    assert soperator.index("### CXCLI Managed Soperator Clusters") < soperator.index(
        "### CXCLI Managed Soperator Upgrade"
    )
    assert soperator.index("### CXCLI Managed Soperator Upgrade") < (
        soperator.index("### External Soperator Onboarding")
    )
    assert soperator.index("### External Soperator Onboarding") < soperator.index(
        "### External Soperator Upgrade"
    )
    assert soperator.index("### External Soperator Upgrade") < soperator.index("### Jail Upgrade")
    assert soperator.index("### Jail Upgrade") < soperator.index(
        "### Soperator Slurm Scheduling And Command Examples"
    )
    assert soperator.index("### Soperator Slurm Scheduling And Command Examples") < (
        soperator.index("### Soperator Rules and Safety Checks")
    )
    assert "Read-only against live resources" in soperator
    assert "compute_migration.mode: in-place\\|blue-green" in soperator
    assert "max_unavailable: all" in soperator
    assert "non-interactive onboarding" in soperator
    assert "strict net-new upgrade quota preflight" in soperator_flat
    assert "Reconcile live state and inspect the first unmet segment" in soperator_flat
    assert "A complete campaign is a live-verified no-op" in soperator_flat
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
    assert "recreation/recreation-coverage.json" in soperator_flat
    assert "raw bound PV manifests and reclaim-policy evidence" in soperator_flat
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
    assert (
        "generated/reports/soperator-clusters/<cluster-key>/soperator-upgrade/report.md"
        in soperator_flat
    )
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
    assert "Plan and dry run: load the v6 campaign from `config.yaml`, refresh all live" in (
        soperator_flat
    )
    assert "Execute preflight: verify the campaign fingerprint and live source" in (soperator_flat)
    assert (
        "Validation hold: verify external MK8s control-plane and replacement-group readiness"
        in soperator_flat
    )
    assert (
        "Segment completion: write the latest `ext-soperator-upgrade/report.md` / `report.json`"
        in (soperator_flat)
    )
    assert (
        "write the segment snapshot under `generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/segments/<segment-id>/`"
        in (soperator_flat)
    )
    assert "Campaign completion: after the last segment reports `Pending phase: none`" in (
        soperator_flat
    )
    assert "For Kubernetes minor changes, run provider-supported hops" in soperator_flat
    assert "upgrade a managed cluster from `1.31` to `1.34` as" in soperator_flat
    assert "`1.31 -> 1.33` and `1.31 -> 1.34` requests" in soperator_flat
    assert "Managed upgrades do not persist a locked multi-run path" in soperator_flat
    assert (
        "For managed `mk8s-node-template` resume, cxcli performs internal Nebius API resume reconciliation"
        in soperator_flat
    )
    assert (
        "the checkpointed target, the current command target, the live MK8s control plane"
        in soperator_flat
    )
    assert (
        "provider state, API-reported Kubernetes version, total, upgraded, upgrading, remaining"
        in soperator_flat
    )
    assert "blocks the combined run and prints a chart-first command" in soperator_flat
    assert "Run the Soperator chart upgrade while Kubernetes stays at `1.32`" in (soperator_flat)
    assert "External v6 campaigns are strictly catalog-pinned and fail closed" in (soperator_flat)
    assert "The managed `soperator upgrade` command retains its explicit" in soperator_flat
    assert "Paths marked `supported_with_warning` continue with the warning" in soperator_flat
    assert "CXCLI-managed Soperator upgrade follows these stages:" in soperator_flat
    assert "Preflight and backup: validate the current bundle" in soperator_flat
    assert "Controller authority and serial MK8s rollout: verify the fixed controller" in (
        soperator_flat
    )
    assert "The two domains never roll concurrently" in soperator_flat
    assert "no provider node-group create/delete is permitted" in soperator_flat
    assert "Jail Upgrade: when the target populate-jail image changed" in soperator_flat
    assert "require post-rootfs `scontrol`, `sbatch`, and accounting/QOS smoke" in (soperator_flat)
    assert "The Soperator jail is the shared Slurm runtime filesystem" in soperator_flat
    assert "it is not the Kubernetes container's literal `/`" in soperator_flat
    assert (
        "contract covers the controller, login, enabled REST (`slurmrestd`), "
        "SConfigController, and every worker NodeSet"
    ) in soperator_flat
    assert "Accounting is deliberately outside the Jail slot contract" in soperator_flat
    assert "neither mounts `/mnt/jail`" in soperator_flat
    assert "MariaDB data lives on the dedicated accounting PVC" in soperator_flat
    assert "contents are not copied" in soperator_flat
    assert "only an explicitly relocated, non-overlapping mapping" in soperator_flat
    assert "Jail Upgrade follows the Soperator chart/rootfs activation boundary" in (soperator_flat)
    assert "then later Kubernetes-only hops after Slurm has passed post-Jail smoke" in (
        soperator_flat
    )
    assert "The refresh uses an active/passive rootfs model" in soperator_flat
    assert "contains two logical rootfs slots plus stable persistent-mount directories" in (
        soperator_flat
    )
    assert "In-place first adoption has a checkpointed two-boundary readiness gate" in (
        soperator_flat
    )
    assert "legacy, slot-a, slot-b, and every persistent-mount PVC" in soperator_flat
    assert "/mnt/jail-store/shared/data" in soperator_flat
    assert "/mnt/jail-store/shared/scripts" in soperator_flat
    assert "capacity preflight" in soperator_flat
    assert "stable persistent paths from the replaceable-rootfs estimate" in soperator_flat
    assert "Completion markers live under `/store/.cxcli/persistent-migrations/`" in (
        soperator_flat
    )
    assert "instead of reopening legacy-rootfs writes that would make the shared copy stale" in (
        soperator_flat
    )
    assert (
        "creates a Kubernetes Job named like "
        "`<target>-populate-jail-passive-<slot>-<attempt-token>`"
    ) in soperator_flat
    assert "an `sbatch --test-only` configuration parse" in soperator_flat
    assert "keep the partitions DOWN" in soperator_flat
    assert "submits a bounded live `sbatch` job exactly once" in soperator_flat
    assert "resumes the same smoke state without repopulating the active PVC" in soperator_flat
    assert (
        "the cluster can briefly contain old non-SConfig pods using the old slot" in soperator_flat
    )
    assert (
        "A single consumer pod is not expected to run with both slot-a and slot-b "
        "mounted as two active root filesystems"
    ) in soperator_flat
    assert "Infographic prompt for ChatGPT" not in soperator
    assert "Create a multi-step technical infographic as six sequential panels" not in (
        soperator_flat
    )
    assert "### In-place upgrade workflow" in soperator
    assert "### Blue-green upgrade workflow" in soperator
    assert "](docs/soperator-in-place-upgrade-workflow.png)" in soperator
    assert "](docs/soperator-blue-green-upgrade-workflow.png)" in soperator
    assert "](docs/soperator-controller-bridge-ha-continuity.svg)" in soperator
    assert "](docs/soperator-login-node-continuity.svg)" in soperator
    assert "prepares and verifies the passive Jail rootfs slot" in soperator_flat
    assert "Source groups are retired independently" in soperator_flat
    assert (
        "operator-facing top-level stage (`MK8s Node Upgrades`, `Soperator Upgrade`, "
        "or `Jail Upgrade`)"
    ) in soperator_flat
    assert "Fast stage verification gates: after ActiveChecks suspension" in soperator_flat
    assert "post-MK8s validation, Soperator chart apply, Jail Upgrade" in soperator_flat
    assert "postflight validation, and shared safety verification" in soperator_flat
    assert "Singleton takeover, postflight validation, and restore: start the target" in (
        soperator_flat
    )
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
    assert "SlurmCluster.spec.populateJail.image" in upgrade_flat
    assert "`--populate-jail-refresh auto|force|manual`" in upgrade_flat
    assert "`--jail-persistent-mount <mountPath>=<localPath>`" in soperator_flat
    assert "`--jail-sfs-resize-policy fail|prompt|apply`" in soperator_flat
    assert "`--confirm-jail-rootfs-overwrite`" not in soperator_flat
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
    assert "checkpointed in-place adoption gate before rootfs consumer switch" in (unreleased_flat)
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
    assert "Focused Soperator README and design navigation" in unreleased_flat
    assert "Only managed upgrades now expose `--jail-persistent-mount`" in unreleased_flat
    assert "External upgrade exposes no login policy or drain-timeout flag" in unreleased_flat
    assert "external `wait-active`" not in unreleased_flat
    assert "`--login-session-policy wait-active`" not in unreleased_flat
    assert "absent-source persistent mount behavior for future writes such as `/models`" in (
        unreleased_flat
    )
    assert "internal Nebius API resume reconciliation for managed `soperator upgrade`" in (
        unreleased_flat
    )
    assert "retrying the Terraform-managed workflow" in unreleased_flat


def test_jail_upgrade_architecture_covers_runtime_consumers() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    design = (REPO_ROOT / "docs" / "design.md").read_text(encoding="utf-8")
    readme_flat = _squash(readme)
    design_flat = _squash(design)

    assert "chat-gpt-prompt.md" not in readme
    assert "chat-gpt-prompt.md" not in design
    assert (
        "contract covers the controller, login, enabled REST (`slurmrestd`), "
        "SConfigController, and every worker NodeSet"
    ) in design_flat
    assert "Accounting is deliberately outside the Jail slot contract" in design_flat
    assert "neither mounts `/mnt/jail`" in design_flat
    assert "active/passive rootfs slots" in design_flat
    assert "stable persistent-mount directories on the same physical jail SFS" in design_flat
    assert "does not restore the normal target writer contract or full target config" in design_flat
    assert "persistent submounts such as `/home`, `/data`, `/scripts`, and `/models`" in (
        design_flat
    )
    assert "attached back into that rootfs" in design_flat
    assert (
        "![Active/passive Jail rootfs slots sharing stable persistent mounts]"
        "(docs/jail-rootfs-active-passive-storage.png)"
    ) in readme
    assert (
        "![Active/passive Jail rootfs slots sharing stable persistent mounts]"
        "(jail-rootfs-active-passive-storage.png)"
    ) in design
    assert "[Editable SVG source](docs/jail-rootfs-active-passive-storage.svg)" in readme
    assert "[Editable SVG source](jail-rootfs-active-passive-storage.svg)" in design
    for flat_doc in (readme_flat, design_flat):
        assert "Slot images may contain mount-point directories" in flat_doc
        assert "persistent backing directories do not overlap either rootfs slot" in flat_doc
        assert "passive populate Job mounts only the passive slot PVC" in flat_doc
        assert "Retaining `legacy-rootfs` is the rollback policy" in flat_doc
        assert "Retaining the legacy PVC alone preserves the old rootfs source" in flat_doc
        assert "current target persistent PVCs or reject that rollback path" in flat_doc
    topology = REPO_ROOT / "docs" / "jail-rootfs-active-passive-storage.png"
    assert topology.is_file()
    assert topology.stat().st_size > 0
    header = topology.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    assert header[12:16] == b"IHDR"
    assert struct.unpack(">II", header[16:24]) == (1536, 1024)
    topology_svg = REPO_ROOT / "docs" / "jail-rootfs-active-passive-storage.svg"
    assert topology_svg.is_file()
    topology_svg_text = topology_svg.read_text(encoding="utf-8")
    assert '<svg xmlns="http://www.w3.org/2000/svg" width="1536" height="1024"' in (
        topology_svg_text
    )
    for label in (
        "Jail Rootfs Active/Passive Storage",
        "legacy-rootfs",
        "/mnt/jail-store/rootfs/slot-a",
        "/mnt/jail/.cxcli/rootfs/slot-b",
        "AUTOMATIC · /home /data /scripts /models",
        "DISCOVERED · existing submount / external NFS",
        "EXPLICIT · unmodeled paths, e.g. /checkpoints",
        "The passive populate Job mounts only its destination rootfs PVC",
    ):
        assert label in topology_svg_text


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
    assert "fixes `controller` at two nodes and `system` at three nodes" in readme_flat
    assert "disables autoscaling for both" in readme_flat
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
    assert "Day-2 worker scale commands preserve this split" in design_flat
    assert "ephemeral workers patch `NodeSetPowerState.activeNodes`" in design_flat
    assert "non-ephemeral scale changes NodeSet replicas and matching worker host capacity" in (
        design_flat
    )
    assert "Scale-to-zero on non-ephemeral workers is maintenance mode" in design_flat
    assert "Explicit non-ephemeral ordinal removal is tail-only" in design_flat
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
    assert "`slurmNodes.controller.openMetrics.enabled`" in chart_readme
    assert "defaults to `true` for the pinned Slurm 25.11 images" in chart_readme
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
        "  --to-k8s-version <major.minor> \\\n"
        "  --compute-migration-mode in-place \\\n"
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
        "Register a new target, report its active campaign, or propose the next campaign after completion"
        in readme_flat
    )
    assert (
        "Register a new target, report its active campaign, or propose the next campaign after completion"
        in design_flat
    )
    assert "`config.yaml` is the only desired-path authority" in readme_flat
    assert (
        "Execute or reconcile every remaining approved campaign segment in dependency order"
        in readme_flat
    )
    assert "Healthy evidence is reported as `gpu-stack: verified`" in readme_flat
    assert "`gpu-rdma: validation-planned`" in readme_flat
    assert "Soperator/operator pins" in readme_flat
    assert (
        "`ext-soperator upgrade --execute` verifies the live source release, creates one campaign-owned restore-capable backup before the first mutation, and reuses that same verified archive across every retry and locked segment"
        in readme_flat
    )
    assert "`--approve-backup-recovery` / `--no-approve-backup-recovery`" in readme_flat
    assert "`completed-with-degraded-protection`" in readme_flat
    assert "replacement-only repair and does not rerun upgrade phases" in readme_flat
    assert "Each executed stage runs a fast stage-scoped verification" in readme_flat
    assert "leaves that same phase pending" in readme_flat
    assert "completed campaign remains the audit record" in readme_flat
    assert (
        "`generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md` reports `Pending phase: none`"
        in readme_flat
    )
    assert "Rerendering preserves command-owned runtime reports" in readme_flat
    assert "All lifecycle reports stay in the single `generated/reports/` folder" in readme_flat
    assert "Soperator lifecycle output is grouped by cluster identity" in readme_flat
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
        "`generated/reports/soperator-clusters/<cluster-key>/discovery/manifest.json`",
        "`generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`",
        "`upgrade-node-template-report.md`",
        "`upgrade-node-template-report.json`",
        "`upgrade-node-group-report.md`",
        "`upgrade-node-group-report.json`",
        "`generated/reports/soperator-clusters/<cluster-key>/soperator-upgrade/report.md`",
        "`generated/reports/soperator-clusters/<cluster-key>/soperator-upgrade/report.json`",
    ):
        assert report_name in readme_flat
    assert "JSON detail reports referenced from those Markdown reports" in readme_flat
    assert "`Stage Fast Verification` rollup" in readme_flat
    assert "JSON `stage_verification` array" in readme_flat
    assert "Setup phases validate evidence only" in readme_flat
    assert "No-op phases are recorded as `SKIP`" in readme_flat
    assert "Soperator/Slurm validation suite still runs at validation hold" in readme_flat
    assert "per-phase `phase_state[<phase>].fast_verification` proof" in design_flat
    assert "targeted phase-fast smoke" in design_flat
    assert "Rerun the same upgrade command while a phase is pending" in readme_flat
    assert "The journal never stores or reconstructs the desired path" in readme_flat
    assert "The external Soperator steady-state handoff is:" in readme_flat
    assert (
        "`ext-soperator onboard` discovers the live cluster and locks the full path under `deploy.targets[].soperator_onboarding.upgrade_path`"
        in readme_flat
    )
    assert "`deploy.targets[].soperator_onboarding.upgrade_path`" in readme_flat
    assert "complete provider-supported campaign" in readme_flat
    assert (
        "One `ext-soperator upgrade --execute --approve` invocation advances every remaining locked segment"
        in readme_flat
    )
    assert "continues across every successful remaining segment" in readme_flat
    assert "reports `Pending phase: none` and the completed campaign remains" in readme_flat
    assert (
        "With no later provider-supported or catalog-pinned target, config bytes remain unchanged"
        in readme_flat
    )
    assert "reconcile-target-gpu-stack" in readme_flat
    assert "target GPU stack reconciliation" in readme_flat
    assert "target-gpu-stack-remediation" in readme_flat
    assert (
        "`nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run`" in readme
    )
    assert "Reconcile live state and inspect the first unmet segment" in readme_flat
    assert "complete end-to-end `nebius-cxcli-ext-soperator-upgrade-campaign/v6` campaign" in (
        readme_flat
    )
    assert (
        "accepted onboarding actions, persistent jail mounts when a rootfs refresh "
        "or explicit mount input makes them relevant, external control-plane and accepted "
        "compute-migration phases"
    ) in design_flat
    assert "The operation journal cannot recreate or replace the desired campaign" in readme_flat
    assert "Do not onboard the cluster again between segments" in readme_flat
    assert "The SDK-generated cluster access is canonical" in readme_flat
    assert "must expose the same `kube-system` UID" in readme_flat
    assert "Fresh-live observation may close only an exact provider-only MK8s segment" in (
        design_flat
    )
    assert "requires the exact journaled operation kind, operation ID, and resource ID" in (
        readme_flat
    )
    assert "rerun the same idempotent `onboard` command only to check for a later" in readme_flat
    assert "`ext-soperator onboard` is read-only against live cluster state" in readme_flat
    assert "The initial discovery summary is read-only" in readme_flat
    assert "does not list future upgrade phases as live onboarding actions" in readme_flat
    assert "prints the accepted layout decisions explicitly" in readme_flat
    assert "no aligned SFS creation or storage data migration is planned" in readme_flat
    assert (
        "`--compute-migration-mode` choice determines whether those accepted groups are updated in place or exchanged through blue/green target groups"
        in readme_flat
    )
    assert "runs the supported phases in order" in readme_flat
    assert "Onboarding asks for two independent layers" in readme_flat
    assert "compute mode is `keep-existing-compute` or `create-aligned-node-groups`" in readme_flat
    assert "Keeping existing compute preserves discovered role" in readme_flat
    assert (
        "The Soperator chart/app target is always the exact `component_sources.yaml` catalog pin"
        in readme_flat
    )
    assert "External onboarding has no chart-version override" in readme_flat
    assert "`--to-k8s-version`: target Kubernetes `major.minor` version" in readme
    assert "recommends the highest endpoint reachable by every concrete node group" in readme_flat
    assert "omission prints the recommendation without writing a target or campaign" in (
        readme_flat
    )
    assert "`summary.md` includes `Upgrade Guidance` without gating discovery" in readme_flat
    assert (
        "that section shows Kubernetes minor hops, the current/target Soperator chart package state"
    ) in readme_flat
    assert "Support-policy evidence validates the path but does not by itself mean" in readme_flat
    assert "canonical ordering across the Kubernetes `1.33+` boundary" in readme_flat
    assert "External v6 campaigns are strictly catalog-pinned and fail closed" in readme_flat
    assert "they have no support-policy bypass" in readme_flat
    assert "discovered storage sizes are lower bounds" in readme_flat
    assert "Render/deploy must not request a smaller PVC/PV size" in readme_flat
    assert "from the live node-group ids" in readme_flat
    assert "preserves the live `SlurmCluster` resource name as `values.clusterName`" in readme_flat
    assert "accepted campaign fingerprint and source release" in readme_flat
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
    assert (
        "`.nebius-cxcli/soperator-clusters/<cluster-key>/ext-soperator-upgrade/campaigns/<campaign-id>/checkpoint.json`"
        in readme_flat
    )
    assert "`--approve` / `--no-approve`: record customer approval" in readme_flat
    assert "auto-detects source worker node groups" in readme_flat
    assert "`slurm.nebius.ai/nodeset` worker labels" in readme_flat
    assert "net-new upgrade quota preflight before any SFS or node-group mutation" in readme_flat
    assert "two temporary, fixed one-node CPU controller groups" in readme_flat
    assert "every target replacement node group while its source group remains retained" in (
        readme_flat
    )
    assert "preflight checks the selected mode's net-new quota and GPU capacity" in readme_flat
    assert (
        "require every updated or replacement group to become Ready and schedulable" in readme_flat
    )
    assert "checks immutable source workers and blue/green target replacements" in readme_flat
    assert "newly submitted jobs queue, while running and `COMPLETING` jobs continue" in (
        readme_flat
    )
    assert "`--slurm-scheduling-pause / --no-slurm-scheduling-pause`" in readme_flat
    assert "Its states are `Queued -> Dispatching -> Applied | Rejected | Indeterminate`" in (
        readme_flat
    )
    assert "exact observed held state for every cxcli-held Slurm job" in readme_flat
    assert "Successful upgrades release only jobs that cxcli requeue-held" in readme_flat
    assert "scontrol show job <jobid> -o" in readme_flat
    assert "This does not use `scontrol hold all`" in readme_flat
    assert "each affected running job" not in readme_flat
    assert "each displayed affected job" in readme_flat
    assert "external `ext-soperator upgrade` always defaults to `preserve`" in readme_flat
    assert "neither waits for nor mutates it" in readme_flat
    assert "Managed `soperator upgrade` still defaults to `interactive`" in readme_flat
    assert "to `fail` in non-TTY or `--no-interactive` automation" in readme_flat
    assert "Local `deploy` and `flux apply` still default" in readme_flat
    assert "to `wait-then-cancel` in non-TTY automation" in readme_flat
    assert "The default wait timeout is `1h`" in readme_flat
    assert "check every live worker NodeSet before target Soperator reconciliation" in (readme_flat)
    assert (
        "normalizes the Soperator manager and Soperator checks `kube-rbac-proxy` image values to "
        "`registry.k8s.io/kubebuilder/kube-rbac-proxy:v0.15.0`"
    ) in design_flat
    assert "Slurm rejects the scoped node filter" in readme_flat
    assert "unfiltered cluster-wide job list" in readme_flat
    assert "Slurm can show a cancelled job as `COMPLETING`" in readme_flat
    assert "waits for the selected jobs to leave the affected node list" in readme_flat
    assert "The v6 campaign and each segment's selected" in readme_flat
    assert "actions` projection are the desired external upgrade contract" in readme_flat
    assert "`approve-external-soperator-upgrade`" in readme_flat
    assert "approve-soperator-migration" not in readme
    assert "approve-soperator-migration" not in design
    assert "The operation journal cannot recreate or replace the desired campaign" in readme_flat
    assert "live Nebius, Kubernetes, Soperator, Jail, Slurm, and SSH observations prove" in (
        readme_flat
    )
    assert (
        "Rerunning `ext-soperator onboard` is safe and does not mutate the cluster" in readme_flat
    )
    assert "Identical input is a byte-for-byte config no-op" in readme_flat
    assert "An executing or pending campaign is reported but never rewritten" in readme_flat
    assert "Provider API failure or incomplete compatibility data is blocked/unknown" in readme_flat
    assert (
        "External Soperator upgrade owns each accepted external Kubernetes control-plane minor hop"
        in readme_flat
    )
    assert "In-place updates the same fixed-size node groups" in readme_flat
    assert "Blue/green creates accepted bootstrap groups" in readme_flat
    assert "provider-active target replacement renders as `upgrading`" in readme_flat
    assert "`--approve-service-role-downtime`" not in readme_flat
    assert "`--approve-service-role-downtime`" not in design_flat
    assert "Controller continuity has no downtime-approval flag" in readme_flat
    assert "managed fast-Pod bridge: controller + system domains" in readme_flat
    assert "external isolated bridge: temporary node groups" in readme_flat
    assert "node-group scheduling domains" in readme_flat
    assert "bounded interruption" in readme_flat
    assert "fixed `controller` and `system`" in design_flat
    assert "provider node-group create/delete operations" in design_flat
    assert "normal `--execute --approve` mutation authorization" in readme_flat
    assert "cloned from the exact source controller template" in readme_flat
    assert "two distinct immutable Nebius node-group IDs" in readme_flat
    assert "does not claim that they are separate provider availability zones" in readme_flat
    assert "including any hostname/address alias" in readme_flat
    assert "reset to the exact prior UP record is immediately reasserted DOWN" in readme_flat
    assert "exact source controller template" in design_flat
    assert "does not infer provider availability-zone separation" in design_flat
    assert "path is `/etc/slurm/slurm.conf`" in design_flat
    assert "exact prior UP partition record" in design_flat
    assert "zero `slurmctld` processes, zero writable state mounts" in design_flat
    assert "UID/resourceVersion compare-and-swap" in design_flat
    assert "resumable backup/restore intents" in design_flat
    assert "one active and one standby `slurmctld`" in design_flat
    assert "proves that no third writer exists" in design_flat
    assert "recovery is roll-forward only" in design_flat
    assert "proven final singleton takeover" in design_flat
    assert "bypasses no continuity gate" in design_flat
    assert "in-place zero-surge and safe-surge expose only the accepted unavailable" in design_flat
    assert "cxcli fails fast rather than assuming a vanilla cluster is safe to adopt" in readme_flat
    assert "The cxcli-managed deployments `.gitignore` excludes `.nebius-cxcli/`" in (readme_flat)
    assert "these journals stay local" in readme_flat
    assert "creates or reuses aligned controller-spool and accounting SFS" in readme_flat
    assert (
        "keeps the existing physical jail SFS for single-SFS active/passive rootfs adoption"
        in readme_flat
    )
    assert (
        "automatically models `/home`, `/data`, `/scripts`, and `/models` as persistent jail mounts"
    ) in readme_flat
    assert "The chart creates an absent automatic directory before mounting it" in readme_flat
    assert "future files written under `/models` therefore land in `/mnt/jail/models`" in (
        readme_flat
    )
    assert "First adoption keeps the automatic paths at their existing" in readme_flat
    assert (
        "Quota must cover spare target storage for non-jail storage while source storage remains mounted"
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
    assert (
        "`generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`"
        in readme
    )
    assert (
        "Phases complete only when their live prerequisites are absent or satisfied" in readme_flat
    )
    assert "External targets are registered only through `nebius-cxcli ext-soperator" in readme
    assert "`component add` does not act as an external onboarding alias" in readme_flat
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
    assert "Kubernetes minor hops, current/target Soperator chart package state" in design_flat
    assert "current/target Jail rootfs image-tag state" in design_flat
    assert "canonical ordering across the Kubernetes `1.33+` boundary" in design_flat
    assert "Soperator chart `1.22.3 -> 4.0.2-ps.4`" in design_flat
    assert "Jail rootfs image tag `1.22.3-slurm23.11.6-cuda12.4.0 ->" in design_flat
    assert "This ordering is intentional" in design_flat
    assert "the required Nebius GPU image, CUDA stack, OS, and driver targets" in design_flat
    assert "are staged on replacement groups" in design_flat
    assert "before the cluster reaches the Kubernetes `1.33+` boundary" in design_flat
    assert "`procMount: Unmasked` admission now depends on `hostUsers: false`" in (design_flat)
    assert "user-namespace/idmap and NFS behavior must match the target chart contract" in (
        design_flat
    )
    assert "old source webhooks and controllers must stop reconciling target objects" in (
        design_flat
    )
    assert "stale Flux/Helm records must be retired before final validation" in design_flat
    assert (
        "releases newer than the cxcli pin, such as `4.1.1`, are deliberately not advertised"
        in (design_flat)
    )
    assert "remain `not_validated` until cxcli has an explicit tested policy rule" in (design_flat)
    assert (
        "defaults to the highest endpoint reachable under the committed compatibility policy"
        in (design_flat)
    )
    assert "does not present external upgrade phases as actions taken by the onboard command" in (
        design_flat
    )
    assert "the matched upgrade-path rule" in design_flat
    assert "Target-compatible storage can omit aligned SFS migration" in design_flat
    assert "Compute migration is an explicit schema-v6 choice" in design_flat
    assert "`keep-existing-compute` or `create-aligned-node-groups`" in design_flat
    assert "preserves discovered role, NodeSet, partition, placement, and topology mappings" in (
        design_flat
    )
    assert "The layout choice never changes the separately accepted migration mode" in design_flat
    assert "cloning the accepted source platform/preset" in design_flat
    assert "This is primarily a day-2 Soperator management and upgrade path" in design_flat
    assert "not a Terraform-managed MK8s row" in design_flat
    assert "Healthy evidence is reported as `gpu-stack: verified`" in design_flat
    assert "`gpu-rdma: validation-planned` evidence" in design_flat
    assert "Target GPU stack reconciliation is represented in the same external campaign" in (
        design_flat
    )
    assert "fast stage gates record `fast_verification`" in design_flat
    assert "`ext-soperator upgrade` returns a complete no-op" in design_flat
    assert "Use upgrade for reruns/resume while those actions remain selected" in design_flat
    assert "It does not rewrite desired state from live observations" in design_flat
    assert "completed immutable campaign remains in `config.yaml`" in design_flat
    assert (
        "`generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md` shows `Pending phase: none`"
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
        "`generated/reports/soperator-clusters/<cluster-key>/discovery/manifest.json`",
        "`generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`",
        "`upgrade-node-template-report.md`",
        "`upgrade-node-template-report.json`",
        "`upgrade-node-group-report.md`",
        "`upgrade-node-group-report.json`",
        "`generated/reports/soperator-clusters/<cluster-key>/soperator-upgrade/report.md`",
        "`generated/reports/soperator-clusters/<cluster-key>/soperator-upgrade/report.json`",
    ):
        assert report_name in design_flat
    assert "JSON `stage_verification` details" in design_flat
    assert "JSON detail files referenced from those Markdown reports" in design_flat
    assert "If the report still shows any pending phase other than `none`" in design_flat
    assert "rerun the same `ext-soperator upgrade ... --execute --approve` command" in design_flat
    assert "completed immutable campaign remains in `config.yaml`" in design_flat
    assert (
        "`nebius-cxcli ext-soperator upgrade <config.yaml> --target <target> --dry-run`" in design
    )
    assert (
        "`ext-soperator upgrade` requires an explicit mode flag; omitting both "
        "`--dry-run` and `--execute` fails before discovery or mutation." in design_flat
    )
    assert (
        "dry-run plan groups target discovery, versions, the full locked path, completed/current/remaining segments"
    ) in design_flat
    assert (
        "persistent jail mounts when a rootfs refresh or explicit mount input "
        "makes them relevant, external control-plane and accepted compute-migration phases"
    ) in design_flat
    assert "execution contracts so operators can scan the plan" in design_flat
    assert (
        "`--execute --approve` refreshes discovery, validates the accepted onboarding analysis"
        in design_flat
    )
    assert (
        "rechecks the live source release and full discovery fingerprint, creates a restore-capable backup before the first mutation for new/replacement-cluster restore only"
        in design_flat
    )
    assert "binds it to the campaign rather than one segment" in design_flat
    assert "Execution identity stays segment-local" in design_flat
    assert "the next segment is blocked until a newly created archive" in design_flat
    assert "replacement-only repair without rerunning campaign phases" in design_flat
    assert "The external stage model is explicit" in design_flat
    assert (
        "execute preflight refreshes live discovery, verifies source release/fingerprint"
        in design_flat
    )
    assert "Jail Upgrade follows the Soperator chart/rootfs activation boundary" in (design_flat)
    assert "handoff plus Jail rootfs refresh in that same segment" in design_flat
    assert "requires post-Jail `scontrol`, `sbatch`, and accounting/QOS smoke" in (design_flat)
    assert "validation hold verifies MK8s, target Soperator" in design_flat
    assert "every executed stage runs a fast stage-scoped verification" in design_flat
    assert "including the post-MK8s validation and Jail Upgrade boundaries" in design_flat
    assert "final post-upgrade MK8s and Helm readiness checks" in design_flat
    assert "`phase_state[<stage>].fast_verification`" in design_flat
    assert "JSON `stage_verification` array" in design_flat
    assert "completion writes the external upgrade reports" in design_flat
    assert "Managed `soperator scale-up` and `soperator scale-down`" in design_flat
    assert "without leaving live Slurm nodes drained" in design_flat
    assert "leaves live reconciliation to the managed deploy/apply path" in design_flat
    assert "does not bypass Terraform/Flux ownership for the live NodeSet" in design_flat
    assert "controller-safe `reserveOrdinals` path" in design_flat
    assert (
        "keeps the core external onboarding/backup/upgrade path SSH-free from the "
        "operator workstation" in design_flat
    )
    assert (
        "using Nebius SDK/API calls for cloud resources and Kubernetes API/kubeconfig "
        "access for cluster, Soperator, and Slurm operations" in design_flat
    )
    assert (
        "Slurm CLI probes and decisions run through `kubectl exec` into the login or "
        "controller pods" in design_flat
    )
    assert "The managed stage model is explicit" in design_flat
    assert "planning/dry-run resolves chart and MK8s target intent" in design_flat
    assert (
        "Managed `mk8s-node-template` resume performs internal Nebius API resume reconciliation"
        in design_flat
    )
    assert (
        "live Nebius API state is the machine source for current MK8s infrastructure reality"
        in design_flat
    )
    assert (
        'records managed MK8s reconciliation diagnostics under `mk8s` and `phase_state["mk8s-node-template"]`'
        in design_flat
    )
    assert "Kubernetes minor upgrades must follow provider-supported hops" in design_flat
    assert (
        "target GPU stack reconciliation phase when paired with external upgrade work"
        in design_flat
    )
    assert (
        "advances the selected accepted external MK8s control-plane-only hop, target GPU stack reconciliation phase when paired with external upgrade work, storage, copy, compute"
        in design_flat
    )
    assert (
        "External control-plane work is one Kubernetes minor hop per accepted locked-path segment"
        in (design_flat)
    )
    assert (
        "A single approved `ext-soperator upgrade --execute --approve` invocation drives every remaining segment in order"
        in (design_flat)
    )
    assert "live discovery is observation authority for the current segment only" in design_flat
    assert "derive the committed support-policy rule for every locked segment" in design_flat
    assert "A fresh `ext-soperator onboard` decision is only for proposing a later campaign" in (
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
    assert (
        "`.nebius-cxcli/soperator-clusters/<cluster-key>/ext-soperator-upgrade/campaigns/<campaign-id>/checkpoint.json` operation journal"
        in design_flat
    )
    assert (
        ".nebius-cxcli/soperator-clusters/<cluster-key>/soperator-upgrade/checkpoint.json" in design
    )
    assert (
        "finish `ext-soperator upgrade` and checkpointed `soperator upgrade` runs from the same laptop"
        in design_flat
    )
    assert (
        "checkpoints are local under `.nebius-cxcli/soperator-clusters/<cluster-key>/ext-soperator-upgrade/campaigns/<campaign-id>/`"
        in design_flat
    )
    assert "The journal never preserves or reconstructs the desired phase plan" in design_flat
    assert "pauses affected Slurm scheduling partitions" in design_flat
    assert "changes only currently `UP` partitions to `DOWN`" in design_flat
    assert "Already non-schedulable states are preserved" in design_flat
    assert "New jobs queue while running allocations continue unchanged" in design_flat
    assert "each busy source node and node group remains until its job and epilog finish" in (
        design_flat
    )
    assert "Successful upgrades release only jobs that cxcli requeue-held" in design_flat
    assert "fresh observation still matches that cxcli-owned post-state" in design_flat
    assert (
        "same stable job lineage, a reset `SubmitTime`, `Restarts` incremented by exactly one"
        in design_flat
    )
    assert "settled unallocated held state" in design_flat
    assert "release-intent resume that observes an unheld job" in design_flat
    assert "Recovery guidance is inspection-only" in design_flat
    assert "External upgrade defaults to `preserve` in both TTY and non-TTY execution" in (
        design_flat
    )
    assert "neither waits for nor mutates those allocations" in design_flat
    assert "Managed upgrade retains its TTY `interactive` and non-TTY `fail` defaults" in (
        design_flat
    )
    assert "The login handoff journal owns exact source Pod protection" in design_flat
    assert "Current managed and external upgrades do not wait for SSH" in design_flat
    assert "active SSH sockets do not gate the subsequent serial replacement" in design_flat
    assert "No mechanism moves an established TCP connection between Pods" in design_flat
    assert "inspection or repair of an older checkpoint" in design_flat
    assert "--acknowledge-login-exit FINGERPRINT" in design_flat
    assert "--authorize-login-timeout-continuation FINGERPRINT" in design_flat
    assert "Neither legacy acknowledgement authorizes release of a current target-ready" in (
        design_flat
    )
    assert "external continuous-endpoint contract rejects that layout before mutation" in (
        design_flat
    )
    assert "The `?` key opens a scrollable help overlay" in design_flat
    assert (
        "canonical action keys are `r` refresh, `w` wait, `c` cancel, `q` requeue, "
        "`h` hold, uppercase `H` requeue-and-hold, and `u` release"
    ) in design_flat
    assert "broker is accept-only" in design_flat
    assert "uncertain dispatch is never retried blindly" in design_flat
    assert "Slurm may report cancelled jobs as `COMPLETING`" in design_flat
    assert "populates the passive rootfs slot with the target populate-jail image" in design_flat
    assert "login Service has ready EndpointSlice endpoints" in design_flat
    assert "Nebius LoadBalancer public or internal address" in design_flat
    assert "`nebius.com/load-balancer-allocation-id`" in design
    assert "`slurmNodes.login.sshdServiceAnnotations`" in design
    assert "cannot be converted into a reusable Nebius allocation" in design_flat
    assert "keeps automatic external persistent paths in place without a login writer hold" in (
        design_flat
    )
    assert (
        "models `/home`, `/data`, `/scripts`, `/models`, plus explicitly declared "
        "additional customer paths as persistent jail mounts"
    ) in design_flat
    assert "If an automatic path was absent in the legacy rootfs" in design_flat
    assert "the chart creates the stable directory before mounting it" in design_flat
    assert "Later `/models` writes land in `/mnt/jail/models`" in design_flat
    assert "provides ad hoc `ext-soperator scale-up` and `ext-soperator scale-down`" in (
        design_flat
    )
    assert "`--project-id`/`--cluster-id` for node-group lookup and `--kube-context`" in (
        design_flat
    )
    assert "Slurm rejects the scoped node filter" in design_flat
    assert (
        "`generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.json`"
        in design_flat
    )
    assert "auto-detects source worker node groups" in design_flat
    assert "`slurm.nebius.ai/nodeset` worker labels" in design_flat
    assert "Before the first mutation, quota preflight requires two temporary fixed" in (
        design_flat
    )
    assert "one-node CPU bridge groups cloned from the exact source controller template" in (
        design_flat
    )
    assert "every target replacement group while its source remains retained" in design_flat
    assert "Reruns are live-reconciled" in design_flat
    assert "the accepted v6 campaign defines the desired work" in design_flat
    assert "Rerunning `ext-soperator onboard` remains read-only while a campaign is active" in (
        design_flat
    )
    assert "Missing, partial, or errored provider evidence is blocked/unknown" in design_flat
    assert "If a target replacement create or provider readiness observation times out" in (
        design_flat
    )
    assert "next identical execute command reconciles that same replacement" in design_flat
    assert "`config.yaml` owns path and phase order" in design_flat
    assert "The journal owns operation intent and identities" in design_flat
    assert "never authorizes a duplicate mutation" in readme_flat
    assert "provider state, API-reported Kubernetes version" in design_flat
    assert "Before completion, cxcli verifies the external MK8s control plane" in design_flat
    assert "target replacement-group readiness" in design_flat
    assert "before validation-and-rollback hold" in readme_flat
    assert "runs before validation hold and again before completion" in design_flat
    assert (
        "normalizes the Soperator manager and Soperator checks `kube-rbac-proxy` image values to "
        "`registry.k8s.io/kubebuilder/kube-rbac-proxy:v0.15.0`"
    ) in design_flat
    assert "deletes suspended old source-family Flux HelmRelease records" in readme_flat
    assert "deletes suspended old source Flux HelmRelease records" in design_flat
    assert "legacy source-family ActiveChecks CronJobs/jobs/pods" in readme_flat
    assert "legacy source-family ActiveChecks CronJobs/jobs/pods" in design_flat
    assert "retired by stale Helm storage revision before target readiness lookup" in design_flat
    assert "compute_migration.mode` is exactly `in-place` or `blue-green`" in design_flat
    assert "rolling compute follows the fingerprinted v6 mode" in design_flat
    assert "workers remain blocked until jobs and epilogs finish" in design_flat
    assert "without waiting for established SSH sessions to drain" in design_flat
    assert "in-place zero-surge and safe-surge expose only the accepted unavailable" in design_flat
    assert "mode: in-place" in design
    assert "max_unavailable: all" in design
    assert "max_parallel_groups: 32" in design
    assert "32 parallel clear groups (maximum 64)" in design_flat
    assert "cumulative active execute time" in design_flat
    assert "offline gaps between resumptions do not inflate" in design_flat
    assert "`elapsed_seconds`" in readme
    assert "`elapsed_time` in `hh:mm:ss`" in readme
    assert "Time between resumptions is excluded" in readme_flat
    assert "worker_wave_percent" not in readme
    assert "worker_group_strategy:" not in readme
    assert "worker_wave_percent" not in design
    assert "worker_group_strategy:" not in design
    assert "resource version CAS" in design_flat
    assert "accepted bootstrap count" in design_flat
    assert "retains busy source workers until jobs and epilogs finish" in design_flat
    assert "source availability hold remains until a distinct Ready target peer" in design_flat
    assert "phase end, and pending gates" in design_flat
    assert "ignored by cxcli-managed deployments `.gitignore` files" in design_flat
    assert "creates or reuses aligned controller-spool and accounting SFS" in design_flat
    assert "keeps the existing physical jail SFS for single-SFS rootfs slot adoption" in design_flat
    assert (
        "models `/home`, `/data`, `/scripts`, `/models`, plus explicitly declared "
        "additional customer paths as persistent jail mounts"
    ) in design_flat
    assert "adopts the automatic legacy paths in place without a data copy" in (design_flat)
    assert "Automatic external adoption keeps those paths in place" in readme_flat
    assert "records it as `jailRootfs.adoption.legacyPvcName`" in readme_flat
    assert "keeps every Jail consumer on that PVC" in readme_flat
    assert "preserves both sides of SSH identity" in readme_flat
    assert "secret-bearing last-applied annotation" in readme_flat
    assert "checkpointed passive-slot Job" in readme_flat
    assert "instead of reopening legacy-rootfs writes that would make the shared copy stale" in (
        design_flat
    )
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
    assert (
        "`generated/reports/soperator-clusters/<cluster-key>/ext-soperator-upgrade/report.md`"
        in design
    )
    assert "resume timeout-guarded phases from checkpoints" in design_flat
    assert "interactive spinner backed by phase-aware status snapshots" in design_flat
    assert (
        "canonical phase id, human-readable phase label, and overall phase health before component details"
        in readme_flat
    )
    assert (
        "canonical phase id, human-readable phase label, and overall phase health before component details"
        in design_flat
    )
    assert "suppresses stray key echo so pressing Enter does not leave duplicate status rows" in (
        readme_flat
    )
    assert "suppresses stray key echo while no prompt is active" in design_flat
    assert "Storage phases show aligned SFS/PVC copy progress" in readme_flat
    assert "Nebius API-backed replacement node-group table" in readme_flat
    assert "one node-group snapshot per refresh" in readme_flat
    assert (
        "provider state, API-reported Kubernetes version, total, provider-current, "
        "provider-updating, provider-outdated, ready/current"
    ) in readme_flat
    assert "without mixing in Kubernetes registered-node counts" in readme_flat
    assert "separate `MK8s Control Plane` signal" in readme_flat
    assert "Nebius API-backed replacement node-group table" in design_flat
    assert "one node-group snapshot per refresh" in design_flat
    assert (
        "provider state, API-reported Kubernetes version, total, provider-current, "
        "provider-updating, provider-outdated" in design_flat
    )
    assert "without mixing in Kubernetes registered-node counts" in design_flat
    assert "separate `MK8s Control Plane` signal" in design_flat
    assert "missing provider fields render as `unknown`" in readme_flat
    assert "omitted `outdated_node_count` on a fully ready provider-active group" in readme_flat
    assert "fully ready non-active `RUNNING` group" in readme_flat
    assert "Terminal output highlights provider table labels and states" in readme_flat
    assert "missing provider fields render as `unknown`" in design_flat
    assert "omitted `outdated_node_count` on a fully ready provider-active group" in design_flat
    assert "fully ready non-active `RUNNING` group" in design_flat
    assert "Terminal output highlights provider table labels and states" in design_flat
    assert "Slurm worker names/states" in readme_flat
    assert "timeout-guarded checkpoints" in design_flat
    assert "remains blocked until the explicit migration executor is implemented" not in design_flat
    assert "`component add` does not infer external onboarding" in design_flat
    assert "External targets are registered only by `ext-soperator onboard`" in design_flat
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
    assert "### Soperator Lifecycle Boundaries" in design
    assert "### Soperator Profile Model" in design
    assert "The Soperator lifecycle surface is split by ownership" in design
    assert "External upgrade-owned work stays under `ext-soperator upgrade`" in design
    assert "Jail Upgrade is a shared Soperator lifecycle boundary" in design
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
    assert "fixes `system` at three nodes and `controller` at two" in section_flat
    assert "`login` and `accounting` default to two fixed nodes" in section_flat
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
    assert "./examples/slurm-jobs/submit-job-test.sh --login <login-external-ip>" in section
    assert "scp -r examples/slurm-jobs root@<login-external-ip>:/shared/slurm-jobs" not in section
    assert "cd /shared/slurm-jobs" not in section
    assert "bash ./submit-job-test.sh\n" in section
    assert "bash ./submit-job-test.sh --part-type cpu --partition cpu --count 10" in section
    assert "bash ./submit-job-test.sh --watch-jobs" in section
    assert "timestamped `squeue` snapshots" in section_flat
    assert "a real terminal defaults to the interactive job policy" in section_flat
    assert "nebius-cxcli soperator upgrade CONFIG_YAML --target TARGET" in section_flat
    assert "--to-chart-version TARGET_VERSION" in section_flat
    assert "--job-policy interactive" not in section_flat
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
