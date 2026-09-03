from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "path",
    (
        PROJECT_ROOT / "scripts" / "generate_soperator_migration_profiles.py",
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_controller_bridge.py",
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_controller_fencing.py",
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_migration.py",
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_migration_profiles.yaml",
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_onboarding.py",
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_upgrade_campaign.py",
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_jail_capacity.py",
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_jail_gpu_validation.py",
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_scaling.py",
        PROJECT_ROOT / "tests" / "test_soperator_jail_capacity.py",
        PROJECT_ROOT / "tests" / "test_soperator_jail_gpu_validation.py",
        PROJECT_ROOT / "tests" / "test_soperator_scaling.py",
        PROJECT_ROOT / "tests" / "test_external_soperator_jobs.py",
        REPOSITORY_ROOT / ".github" / "workflows" / "soperator-upstream-verifier.yml",
        REPOSITORY_ROOT / "helm-charts" / "soperator",
        REPOSITORY_ROOT / "helm-charts" / "soperator-activechecks",
        REPOSITORY_ROOT / "helm-charts" / "soperator-backup-config",
        REPOSITORY_ROOT / "helm-charts" / "soperator-checks",
        REPOSITORY_ROOT / "helm-charts" / "soperator-dcgm-exporter",
        REPOSITORY_ROOT / "helm-charts" / "soperator-notifier",
    ),
)
def test_retired_soperator_delivery_paths_are_absent(path: Path) -> None:
    assert not path.exists(), f"retired Soperator delivery path remains: {path}"


def test_runtime_does_not_import_retired_soperator_engines() -> None:
    runtime_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "src" / "nebius_cxcli").rglob("*.py")
    )

    for module_name in (
        "soperator_controller_bridge",
        "soperator_migration",
        "soperator_onboarding",
        "soperator_upgrade_campaign",
    ):
        assert f"import {module_name}" not in runtime_text
        assert f"from nebius_cxcli.{module_name}" not in runtime_text


@pytest.mark.parametrize(
    "module_name",
    (
        "soperator_migration",
        "soperator_onboarding",
        "soperator_upgrade_campaign",
        "soperator_controller_bridge",
        "soperator_controller_fencing",
        "soperator_scaling",
        "soperator_jail_capacity",
        "soperator_jail_gpu_validation",
    ),
)
def test_retired_soperator_modules_are_not_importable(module_name: str) -> None:
    assert importlib.util.find_spec(f"nebius_cxcli.{module_name}") is None


def test_package_data_has_no_historical_soperator_profile() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "soperator_migration_profiles.yaml" not in pyproject


def test_active_runtime_and_publication_have_no_retired_delivery_terms() -> None:
    runtime_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "src" / "nebius_cxcli").rglob("*.py")
    ).lower()
    delivery_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            REPOSITORY_ROOT / ".github" / "workflows" / "nebius-cxcli-ci.yml",
            REPOSITORY_ROOT / ".github" / "workflows" / "nebius-cxcli-release.yml",
            REPOSITORY_ROOT / ".github" / "helm-chart-publish.json",
        )
    ).lower()

    retired_command = "-".join(("ext", "soperator"))
    assert retired_command not in runtime_text
    assert "controller_bridge_source" not in runtime_text
    assert "soperator-upstream-verifier" not in delivery_text
    assert "helm-charts/soperator" not in delivery_text


def test_runtime_has_no_retired_soperator_command_internals() -> None:
    runtime_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (PROJECT_ROOT / "src" / "nebius_cxcli").glob("*.py")
    )

    for symbol in (
        "_SoperatorUpgradeValueSnapshot",
        "_SoperatorUpgradeBackupResult",
        "_ExternalJailSfsApi",
        "SOPERATOR_UPGRADE_CHECKPOINT_SCHEMA",
        "_SOPERATOR_POPULATE_JAIL_REFRESH_HELP",
        "_SOPERATOR_BACKUP_KUBERNETES_RESOURCES",
        "_SoperatorDeployOwnedRoute",
        "_collect_provider_mk8s_template_snapshot",
        "_soperator_profile_by_name",
        "_kubectl_json_for_upgrade",
        "soperator_cluster_backup_dir",
        "soperator_discovery_manifest_path",
        "PopulateJailRefreshPlan",
        "parse_jail_persistent_mount_spec",
        "slurm_job_nodes",
        "_slurm_allocation_unavailable",
        "wait_for_login_service_ready_endpoints",
        "wait_for_login_statefulset_rollout_with_ready_endpoint_guard",
        "begin_mutation_intent",
        "complete_mutation_intent",
        "soperator-mutation-intent.v1",
        "supervisor_attempt",
    ):
        assert symbol not in runtime_text, f"retired Soperator internal remains: {symbol}"


def test_protected_rootfs_admission_has_no_reference_scratch_contract() -> None:
    protected_text = (
        PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_protected_data_plane.py"
    ).read_text(encoding="utf-8")
    cli_text = (PROJECT_ROOT / "src" / "nebius_cxcli" / "cli.py").read_text(encoding="utf-8")

    assert "RootfsScratchIdentity" not in protected_text
    assert "rootfs_scratch_pvc_manifest" not in protected_text
    assert "compute-csi-default-sc" not in protected_text
    assert "nebius-cxcli.soperator-rootfs-admission.v1" in cli_text
    assert '"mode": "target-wins"' in cli_text
    assert '"targetProvisioner"' in cli_text


def test_upgrade_campaign_and_node_group_migration_keep_separate_lifecycles() -> None:
    cli_text = (PROJECT_ROOT / "src" / "nebius_cxcli" / "cli.py").read_text(encoding="utf-8")
    migration = cli_text.split("def migrate_node_group_command(", maxsplit=1)[1].split(
        "def upgrade_helm_chart_command(", maxsplit=1
    )[0]
    upgrade = cli_text.split("def soperator_upgrade_command(", maxsplit=1)[1].split(
        "def _soperator_upgrade_flux_bundle_sha256(", maxsplit=1
    )[0]
    status_text = (PROJECT_ROOT / "src" / "nebius_cxcli" / "soperator_status.py").read_text(
        encoding="utf-8"
    )

    assert "_run_common_soperator_release_upgrade(" not in migration
    assert "apply_staged_soperator_release(" in migration
    assert "_SOPERATOR_PARENT_OPERATION_LEASE" not in migration
    assert "supervise_committed_soperator_upgrade(" in upgrade
    assert "supervise=False" in upgrade
    assert "mk8s_node_group_migration" not in status_text
