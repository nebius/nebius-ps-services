from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from nebius_cxcli import cli, soperator_public_discovery
from nebius_cxcli.paths import resolve_project_paths
from nebius_cxcli.soperator_full_stack_upgrade import (
    FrozenCompatibilityRow,
    FrozenNodeGroupTarget,
)


@pytest.mark.parametrize("directory", [False, True])
def test_upgrade_reports_unreadable_source_before_discovery(tmp_path, directory):
    config = tmp_path / "config.yaml"
    if directory:
        config.mkdir()
    result = CliRunner().invoke(
        cli.app,
        [
            "soperator",
            "upgrade",
            str(config),
            "--to-release",
            "4.1.7",
            "--to-k8s-version",
            "1.35",
            "--to-os",
            "auto",
            "--to-gpu-stack-preset",
            "auto",
            "--no-interactive",
            "--dry-run",
        ],
    )
    assert result.exit_code == 1
    assert "could not read configuration" in result.output


@pytest.mark.parametrize("failure_stage", ["project", "cluster"])
def test_public_discovery_provider_failure_does_not_expose_transport_details(
    tmp_path, monkeypatch, failure_stage
):
    import nebius.api.nebius.iam.v1 as iam_v1

    closed = []
    sdk = SimpleNamespace(sync_close=lambda: closed.append(True))

    def fail():
        raise RuntimeError("transport-detail-sentinel")

    project = SimpleNamespace(metadata=SimpleNamespace(id="project-a", parent_id="tenant-a"))
    request = SimpleNamespace(wait=fail if failure_stage == "project" else lambda: project)
    monkeypatch.setattr(soperator_public_discovery, "init_nebius_sdk", lambda **kw: sdk)
    monkeypatch.setattr(
        iam_v1, "ProjectServiceClient", lambda sdk: SimpleNamespace(get=lambda *a, **kw: request)
    )
    monkeypatch.setattr(
        soperator_public_discovery,
        "Mk8sKubernetesVersionExecutor",
        lambda sdk: SimpleNamespace(get_cluster=lambda cluster_id: fail()),
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "soperator",
            "discover",
            str(tmp_path / "reports"),
            "--tenant-id",
            "tenant-a",
            "--project-id",
            "project-a",
            "--cluster-id",
            "mk8scluster-a",
        ],
    )

    assert result.exit_code == 1
    assert "does not exist or is not accessible" in result.output
    assert "transport-detail-sentinel" not in result.output
    assert closed == [True]
    assert not (tmp_path / "reports").exists()


@pytest.mark.parametrize(
    "edit_phase", [None, "source-load", "provider-discovery", "release-admission"]
)
@pytest.mark.parametrize("dry_run", [False, True])
def test_upgrade_planner_binds_the_configuration_it_read(
    tmp_path, monkeypatch, edit_phase, dry_run
):
    paths = resolve_project_paths(tmp_path / "config.yaml")
    paths.project_dir.mkdir(parents=True, exist_ok=True)
    source = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster-a",
                    "enabled": True,
                    "inputs": {"node_groups": {"worker": {"node_count": 1}}},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster-a",
                    "target_ref": "cluster-a",
                    "enabled": True,
                    "version": "4.1.6",
                    "values": {},
                }
            ]
        },
        "deploy": {"targets": [{"instance_id": "cluster-a"}]},
    }
    paths.config_path.write_text(yaml.safe_dump(source))
    original = paths.config_path.read_bytes()
    concurrent_payload = yaml.safe_load(original)
    concurrent_payload["infra"]["components"][0]["inputs"]["node_groups"]["worker"]["os"] = (
        "ubuntu26.04"
    )
    concurrent = yaml.safe_dump(concurrent_payload).encode()
    calls = []
    target = cli._HelmChartUpgradeTarget("apps:soperator@cluster-a", "soperator", "cluster-a")
    generated = SimpleNamespace(
        client_info=SimpleNamespace(nebius=SimpleNamespace(project_id="project-a"))
    )
    selected = {
        "target_ref": "cluster-a",
        "kind": "managed-mk8s",
        "ownership": "managed",
        "component_id": "mk8s",
    }
    group = FrozenNodeGroupTarget(
        key="worker",
        provider_name="worker",
        provider_id="group-a",
        platform="cpu-e2",
        source_version="1.34",
        source_os="ubuntu22.04",
        source_drivers_preset="",
        target_version="1.35",
        target_os="ubuntu24.04",
        target_drivers_preset="",
        gpu=False,
    )
    compatibility = FrozenCompatibilityRow(
        group_key="worker",
        kubernetes_version="1.35",
        platform="cpu-e2",
        os="ubuntu24.04",
        drivers_preset="",
    )

    def load(path):
        payload = yaml.safe_load(path.read_bytes())
        if edit_phase == "source-load":
            path.write_bytes(concurrent)
        return payload

    class Executor:
        def __init__(self, sdk):
            pass

        def get_cluster_by_name(self, **kw):
            return object()

        def list_node_groups(self, cluster_id):
            if edit_phase == "provider-discovery":
                paths.config_path.write_bytes(concurrent)
            return (SimpleNamespace(metadata=SimpleNamespace(id="group-a")),)

        def control_plane_versions(self):
            return ("1.34", "1.35")

        def compatibility_choices(self, **kw):
            return ()

    monkeypatch.setattr(cli, "_load_source_payload", load)
    monkeypatch.setattr(
        cli, "_resolve_soperator_command_target", lambda *a, **kw: (target, None, False)
    )
    monkeypatch.setattr(cli, "_load_deploy_context_readonly", lambda path: (generated, paths, {}))
    monkeypatch.setattr(cli, "_resolve_selected_deploy_targets", lambda *a, **kw: [selected])
    monkeypatch.setattr(
        cli, "_generated_bundle_mk8s_module_index", lambda _: {"cluster": ("mk8s", "cluster-a")}
    )
    monkeypatch.setattr(
        "nebius_cxcli.deployment_cli.hydrate_upgrade_jail_config", lambda p, value, ref: value
    )
    monkeypatch.setattr(
        cli,
        "_prepare_cluster_handoff_kube_env",
        lambda *a, **kw: {
            cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "mk8scluster-a",
            cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx-a",
        },
    )
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "uid-a")
    monkeypatch.setattr(
        cli,
        "init_nebius_sdk",
        lambda **kw: SimpleNamespace(sync_close=lambda: calls.append("close")),
    )
    monkeypatch.setattr(cli, "Mk8sKubernetesVersionExecutor", Executor)
    monkeypatch.setattr(cli, "_live_mk8s_cluster_id", lambda *a, **kw: "mk8scluster-a")
    monkeypatch.setattr(cli, "_cluster_control_plane_minor_version", lambda *a, **kw: "1.34")
    monkeypatch.setattr(cli, "_live_soperator_release_for_reconcile", lambda **kw: "4.1.6")
    frozen = SimpleNamespace(
        snapshot=SimpleNamespace(
            release="4.1.7",
            jail_cuda_version="12.8",
            snapshot_sha256="sha256:" + "a" * 64,
        )
    )
    monkeypatch.setattr(cli, "resolve_soperator_source", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "freeze_soperator_release", lambda *a, **kw: frozen)
    monkeypatch.setattr(cli, "use_frozen_soperator_release", lambda f: nullcontext())
    monkeypatch.setattr(cli, "live_node_groups_from_sdk", lambda **kw: (group,))
    monkeypatch.setattr(
        cli, "_soperator_full_stack_node_group_targets", lambda **kw: ((group,), (compatibility,))
    )
    monkeypatch.setattr(
        cli,
        "build_soperator_infrastructure_authority",
        lambda **kw: SimpleNamespace(
            ownership="managed",
            backend="terraform",
            digest="sha256:" + "b" * 64,
            provider_api_authorized=False,
        ),
    )
    monkeypatch.setattr(cli, "_configure_soperator_upgrade_persistent_paths", lambda **kw: None)
    monkeypatch.setattr(cli, "_format_soperator_full_stack_campaign_plan", lambda *a, **kw: ())

    def admit(**kw):
        calls.append("admit")
        if edit_phase == "release-admission":
            paths.config_path.write_bytes(concurrent)

    monkeypatch.setattr(cli, "_run_common_soperator_release_upgrade", admit)
    monkeypatch.setattr(cli, "normalize_runtime_config_payload", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "render_command", lambda **kw: calls.append("render"))
    monkeypatch.setattr(cli, "deploy_command", lambda **kw: calls.append("deploy"))

    result = CliRunner().invoke(
        cli.app,
        [
            "soperator",
            "upgrade",
            str(paths.config_path),
            "--target",
            "cluster-a",
            "--to-release",
            "4.1.7",
            "--to-k8s-version",
            "1.35",
            "--to-os",
            "auto",
            "--to-gpu-stack-preset",
            "auto",
            "--no-interactive",
            *(["--dry-run"] if dry_run else []),
        ],
    )

    if edit_phase is not None:
        assert result.exit_code == 1, result.output
        assert "config" in result.output.lower() and "changed" in result.output.lower()
        assert paths.config_path.read_bytes() == concurrent
        assert "render" not in calls and "deploy" not in calls
        assert not paths.generated_dir.exists()
    else:
        assert result.exit_code == 0, result.output
        assert "admit" in calls
        assert ("render" in calls) is not dry_run
        assert ("deploy" in calls) is not dry_run
        if dry_run:
            assert paths.config_path.read_bytes() == original
        else:
            desired = yaml.safe_load(paths.config_path.read_bytes())
            assert desired["apps"]["charts"][0]["version"] == "4.1.7"
