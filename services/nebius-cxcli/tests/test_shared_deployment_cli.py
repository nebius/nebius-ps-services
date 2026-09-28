from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer.main
from typer.testing import CliRunner

from nebius_cxcli import cli
from nebius_cxcli.frozen_catalog import freeze_catalog

runner = CliRunner()


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("explicit_paths", [None, []])
@pytest.mark.usefixtures("offline_shared_lease")
def test_deploy_rejects_obsolete_rendered_soperator_values_before_auth(
    monkeypatch, tmp_path, dry_run, explicit_paths
):
    """Saved defaults must pass admission before auth, backend or preflight effects."""
    row = {
        "id": "soperator",
        "enabled": True,
        "values": {"soperator-dcgm-exporter": {"enabled": False}},
    }
    if explicit_paths is not None:
        row["values-explicit-paths"] = explicit_paths
    manifest = {"runtime_config": {"apps": {"charts": [row]}}}
    manifest["render"] = {"inputs": freeze_catalog(manifest["runtime_config"])}
    paths = SimpleNamespace(
        config_path=tmp_path / "config.yaml",
        generated_dir=tmp_path / "generated",
        reports_dir=tmp_path / "generated" / "reports",
    )
    # Deploy consumes the saved render even when the source was already corrected.
    paths.config_path.write_text("apps:\n  charts: []\n")
    monkeypatch.setattr(cli, "resolve_deploy_config_paths", lambda _: paths)
    monkeypatch.setattr(cli, "load_generated_manifest", lambda _: manifest)
    monkeypatch.setattr(
        cli, "_ensure_runtime_auth_material", lambda *a, **kw: pytest.fail("auth reached")
    )
    monkeypatch.setattr(
        cli, "deploy_rendered_bundle", lambda *a, **kw: pytest.fail("deployment reached")
    )

    result = runner.invoke(
        cli.app, ["deploy", str(paths.config_path), *(["--dry-run"] if dry_run else [])]
    )

    assert result.exit_code == 1, result.output
    assert "Unsupported Soperator values.soperator-dcgm-exporter" in result.output
    assert "values.observability.dcgmExporter" in result.output
    assert not paths.generated_dir.exists()


def test_create_writes_config_and_never_renders_or_executes(monkeypatch, tmp_path):
    config = tmp_path / "tenant" / "project" / "config.yaml"
    calls = []

    @contextmanager
    def frozen(_snapshot):
        yield

    monkeypatch.setattr(
        cli,
        "resolve_soperator_source",
        lambda *a, **kw: SimpleNamespace(release="1.22.3"),
    )
    monkeypatch.setattr(cli, "use_frozen_soperator_release", frozen)
    monkeypatch.setattr(cli, "_create_project", lambda **kw: calls.append(kw) or config)
    for name in (
        "render_command",
        "_load_deploy_context",
        "_deployment_execution",
        "_deploy_generated_artifacts",
    ):
        monkeypatch.setattr(
            cli, name, lambda *a, **kw: pytest.fail("create must end after config authoring")
        )
    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "create",
            str(tmp_path),
            "--client-name",
            "example",
            "--tenant-id",
            "tenant",
            "--project-id",
            "project",
            "--profile",
            "mixed",
            "--release",
            "1.22.3",
            "--no-interactive",
        ],
    )
    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    assert calls[0]["soperator_profile"] == "nebius-mixed-v1"
    assert calls[0]["soperator_release"].release == "1.22.3"
    assert calls[0]["validate_config"] is True
    assert "nebius-cxcli deploy" in result.output


def test_install_is_removed_and_create_has_only_authoring_options():
    group = typer.main.get_command(cli.soperator_app)
    assert list(group.commands) == [
        "create",
        "discover",
        "onboard",
        "upgrade",
        "status",
        "profiling",
    ]
    assert runner.invoke(cli.app, ["soperator", "install", "--help"]).exit_code == 2
    names = {option for param in group.commands["create"].params for option in param.opts}
    assert not names & {
        "--execute",
        "--approve",
        "--dry-run",
        "--resume",
        "--replan",
        "--approval-fingerprint",
    }


@pytest.mark.parametrize("dry_run", [False, True])
def test_deploy_dispatches_rendered_context_directly_with_optional_preview(
    monkeypatch, tmp_path, dry_run
):
    context = (
        {"runtime": "frozen"},
        object(),
        {"rendered": True, "render": {"inputs": freeze_catalog({})}},
    )
    calls = []
    monkeypatch.setattr(cli, "_load_generic_soperator_lifecycle_context", lambda *a, **kw: context)
    monkeypatch.setattr(cli, "_require_soperator_lifecycle_scope", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "deploy_rendered_bundle", lambda *a, **kw: calls.append((a, kw)))
    result = runner.invoke(
        cli.app, ["deploy", str(tmp_path / "config.yaml"), *(["--dry-run"] if dry_run else [])]
    )
    assert result.exit_code == 0, result.output
    assert calls[0][0] == context
    assert calls[0][1]["options"].dry_run is dry_run


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.usefixtures("offline_shared_lease")
def test_fresh_soperator_deploy_loads_without_an_ordinary_app_baseline(
    monkeypatch, tmp_path, dry_run
):
    """Exercise the public loader before the shared planner on a fresh install."""
    import yaml

    config = {
        "apps": {
            "charts": [
                {"id": name, "enabled": True, "target_ref": "cluster"}
                for name in ("soperator", "nvidia-gpu-operator", "nvidia-network-operator")
            ]
        }
    }
    paths = SimpleNamespace(
        config_path=tmp_path / "config.yaml",
        generated_dir=tmp_path / "generated",
        reports_dir=tmp_path / "generated" / "reports",
    )
    paths.config_path.write_text(yaml.safe_dump(config))
    manifest = {"runtime_config": config, "render": {"inputs": freeze_catalog(config)}}
    calls = []
    monkeypatch.setattr(cli, "resolve_deploy_config_paths", lambda _path: paths)
    monkeypatch.setattr(cli, "load_generated_manifest", lambda _path: manifest)
    monkeypatch.setattr(cli, "runtime_config_from_manifest", lambda value: value["runtime_config"])
    monkeypatch.setattr(cli, "_apply_generated_tool_version_overrides", lambda _value: None)
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", lambda *a, **kw: None)
    monkeypatch.setattr(
        cli,
        "_materialize_generated_terraform_tfvars",
        lambda *a, **kw: pytest.fail("deploy must retain the frozen generated inputs"),
    )
    monkeypatch.setattr(cli, "deploy_rendered_bundle", lambda *a, **kw: calls.append((a, kw)))

    result = runner.invoke(
        cli.app,
        ["deploy", str(paths.config_path), *(["--dry-run"] if dry_run else [])],
    )

    assert result.exit_code == 0, result.output
    assert calls[0][0] == (config, paths, manifest)
    assert calls[0][1]["options"].dry_run is dry_run
    assert not paths.reports_dir.exists()


@pytest.mark.parametrize(
    "option", ["--execute", "--approve", "--approval-fingerprint", "--resume", "--replan"]
)
def test_deploy_rejects_removed_execution_protocol(option):
    assert runner.invoke(cli.app, ["deploy", "config.yaml", option]).exit_code == 2


@pytest.mark.parametrize("mode", ["execute", "preview", "stale", "concurrent"])
@pytest.mark.parametrize(
    ("strategy", "surge"), [("safe-surge", 2), ("zero-surge", 0), ("force-delete", 0)]
)
def test_guided_upgrade_preserves_resolved_controls_and_source_cas(
    monkeypatch, tmp_path, mode, strategy, surge
):
    import hashlib
    import json

    import yaml

    from nebius_cxcli.deployment_recovery import is_deployment_preview

    config_path = tmp_path / "config.yaml"
    source = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster",
                    "enabled": True,
                    "inputs": {"node_groups": {"worker": {"node_count": 2}}},
                }
            ]
        },
        "apps": {
            "charts": [
                {
                    "id": "soperator",
                    "instance_id": "cluster",
                    "target_ref": "cluster",
                    "enabled": True,
                    "version": "4.1.6",
                    "values": {},
                }
            ]
        },
        "deploy": {"targets": [{"instance_id": "cluster"}]},
    }
    config_path.write_text(yaml.safe_dump(source))
    before = config_path.read_bytes()
    calls = []
    intent = SimpleNamespace(
        target_ref="cluster",
        source_config_sha256="sha256:" + hashlib.sha256(before).hexdigest(),
        target_release="4.1.7",
        target_kubernetes_version="1.33",
        checks_policy_proposal={},
        jail_protection="",
        checks_release_snapshot_sha256="sha256:" + "a" * 64,
        node_groups=[
            SimpleNamespace(
                key="worker",
                target_version="1.33",
                target_os="ubuntu24.04",
                gpu=True,
                target_drivers_preset="cuda12",
            )
        ],
        node_group_strategy=strategy,
        strategy_max_surge_count=surge,
        drain_timeout="45m",
        job_policy="requeue-selected",
        cancel_job_ids=(),
        requeue_job_ids=("42",),
        job_wait_timeout="2h",
        job_refresh_interval="15s",
    )
    if mode == "stale":
        intent.source_config_sha256 = "sha256:" + "0" * 64

    from nebius_cxcli.soperator_jail_mounts import apply_jail_persistent_mount_values
    from nebius_cxcli.soperator_jail_protection import (
        freeze_jail_protection,
        protect_jail_directories,
    )

    protected_values = protect_jail_directories(
        apply_jail_persistent_mount_values({}, layout="managed", target_ref="cluster"),
        paths=["/workspace"],
        layout="managed",
        target_ref="cluster",
    )
    intent.jail_protection = freeze_jail_protection(protected_values)

    def select(**kwargs):
        assert kwargs["dry_run"] is True and is_deployment_preview()
        calls.append("select")
        return intent

    def checks(values, proposal):
        if mode == "concurrent":
            config_path.write_text("competing: writer\n")
        return values, None

    monkeypatch.setattr(cli, "_run_soperator_upgrade_campaign", select)
    monkeypatch.setattr(
        cli,
        "_resolve_soperator_command_target",
        lambda *a, **kw: (
            cli._HelmChartUpgradeTarget("apps:soperator@cluster", "soperator", "cluster"),
            None,
            False,
        ),
    )
    monkeypatch.setattr(cli, "apply_checks_proposal", checks)
    monkeypatch.setattr(cli, "normalize_runtime_config_payload", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "freeze_soperator_release", lambda *a, **kw: object())

    @contextmanager
    def frozen(_release):
        yield

    monkeypatch.setattr(cli, "use_frozen_soperator_release", frozen)
    monkeypatch.setattr(cli, "render_command", lambda **kw: calls.append("render"))
    monkeypatch.setattr(cli, "deploy_command", lambda **kw: calls.append(kw))
    result = runner.invoke(
        cli.app,
        [
            "soperator",
            "upgrade",
            str(config_path),
            "--to-release",
            "4.1.7",
            "--no-interactive",
            *(["--dry-run"] if mode == "preview" else []),
        ],
    )
    if mode in {"stale", "concurrent"}:
        assert result.exit_code == 1, result.output
        assert "changed" in result.output
        assert calls == ["select"]
        assert config_path.read_bytes() == (before if mode == "stale" else b"competing: writer\n")
    elif mode == "preview":
        assert result.exit_code == 0, result.output
        assert calls == ["select"] and config_path.read_bytes() == before
    else:
        assert result.exit_code == 0, result.output
        authored = yaml.safe_load(config_path.read_text())
        assert authored["apps"]["charts"][0]["version"] == "4.1.7"
        assert (
            freeze_jail_protection(authored["apps"]["charts"][0]["values"])
            == intent.jail_protection
        )
        assert authored["infra"]["components"][0]["inputs"]["node_groups"]["worker"] == {
            "node_count": 2,
            "version": "1.33",
            "os": "ubuntu24.04",
            "gpu_stack_preset": "cuda12",
        }
        assert authored["deploy"]["targets"][0]["soperator_rollout"] == {
            "strategy": strategy,
            "max_surge_count": surge,
            "drain_timeout": "45m",
        }
        from nebius_cxcli import deployment_cli, mk8s_upgrade

        executor = deployment_cli._CliDeploymentExecutor(
            SimpleNamespace(),
            SimpleNamespace(config_path=config_path),
            {},
            options=deployment_cli.DeployOptions(),
            lease=None,
        )
        executor.generation = SimpleNamespace(manifest={"runtime_config": authored})
        executor.plan = SimpleNamespace(target_ref="cluster", target_release="4.1.7", stages=())
        executor.hooks = SimpleNamespace(source_groups=("worker",))
        kwargs = executor._campaign_kwargs()
        assert kwargs["desired_jail_protection"] == intent.jail_protection
        assert json.loads(kwargs["desired_jail_protection"])["jailRootfs"]["retainedGenerations"]
        assert (
            mk8s_upgrade.resolve_strategy_max_surge_count(
                kwargs["node_group_strategy"], kwargs["strategy_max_surge_count"]
            )
            == surge
        )
        assert calls[:2] == ["select", "render"]
        assert calls[2] == {
            "config_path": config_path,
            "job_policy": "requeue-selected",
            "cancel_job": [],
            "requeue_job": ["42"],
            "job_wait_timeout": "2h",
            "job_refresh_interval": "15s",
        }

        # Shared deploy also accepts sparse legacy desired configuration. Its
        # frozen override must match admission normalization exactly on replay.
        authored["apps"]["charts"][0]["values"] = {}
        sparse_kwargs = executor._campaign_kwargs()
        cli._configure_soperator_upgrade_persistent_paths(
            source_payload=authored,
            target=cli._HelmChartUpgradeTarget("apps:soperator@cluster", "soperator", "cluster"),
            ownership="managed",
            interactive=False,
        )
        assert sparse_kwargs["desired_jail_protection"] == freeze_jail_protection(
            authored["apps"]["charts"][0]["values"]
        )


@pytest.mark.parametrize("mode", ["same", "different", "local-busy", "held-execution"])
def test_render_publication_is_local_and_preserves_backend_generation(monkeypatch, tmp_path, mode):
    import copy
    import json

    import yaml

    from nebius_cxcli import deployment_state
    from nebius_cxcli.generated_manifest import build_generated_manifest, load_generated_manifest
    from nebius_cxcli.paths import resolve_project_paths
    from nebius_cxcli.terraform_backend import backend_settings_from_config

    path = tmp_path / "deployments" / "tenant" / "project" / "config.yaml"
    path.parent.mkdir(parents=True)
    paths = resolve_project_paths(path)
    payload = {
        "client_info": {
            "client_name": "example",
            "nebius": {
                "tenant_id": "tenant",
                "project_id": "project-example",
                "region_id": "eu-north2",
            },
        },
        "apps": {
            "charts": [
                {"id": "soperator", "instance_id": "cluster", "enabled": True, "version": "4.1.7"}
            ]
        },
        "infra": {"components": []},
        "value": 1,
    }
    path.write_text(yaml.safe_dump(payload))
    monkeypatch.setattr(
        cli,
        "_load_generic_soperator_lifecycle_context",
        lambda *a, **kw: (copy.deepcopy(payload), paths),
    )
    for name in (
        "_require_soperator_lifecycle_scope",
        "_materialize_soperator_component_defaults",
        "_materialize_soperator_render_only_values",
        "materialize_compute_boot_disk_defaults",
        "prune_inactive_mk8s_gpu_app_rows",
        "materialize_mk8s_gpu_app_values",
        "materialize_soperator_child_chart_values",
        "materialize_observability_infra_values",
        "materialize_observability_app_values",
        "materialize_mysterybox_eso_app_values",
        "_raise_on_render_gpu_fabric_drift",
        "_assert_not_nested_deployments_root",
        "_print_mk8s_gpu_validation_warnings",
        "_try_generate_terraform_lock_file",
    ):
        monkeypatch.setattr(cli, name, lambda *a, **kw: None)
    monkeypatch.setattr(
        cli, "resolve_component_sources_profile", lambda: cli.SourceProfile.PORTABLE
    )
    monkeypatch.setattr(
        cli, "_ensure_deployments_gitignore", lambda **kw: SimpleNamespace(path=None)
    )
    monkeypatch.setattr(cli, "_runtime_component_output_values", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "_required_runtime_component_output_specs", lambda *a, **kw: ())
    monkeypatch.setattr(
        cli,
        "_warn_on_config_live_quota_issues",
        lambda *a, **kw: SimpleNamespace(has_confirmed_insufficiency=False),
    )

    def render_flux(config, staged, **kwargs):
        target_dir = cli.flux_target_dir(staged, "cluster")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "configmap-terraform-fluxcd-values.yaml"
        target.write_text(yaml.safe_dump({"data": {"values.yaml": "{}"}}))
        return [target]

    monkeypatch.setattr(cli, "render_flux", render_flux)

    def render_infra(config, staged, **kwargs):
        target = staged.infra_dir / "main.tf"
        target.write_text(f"locals {{ value = {config['value']} }}\n")
        return [target]

    def manifest(config, staged, **kwargs):
        value = build_generated_manifest(
            config=config, paths=paths, targets=[], required_component_outputs=[]
        )
        kwargs["output_path"].write_text(json.dumps(value))

    monkeypatch.setattr(cli, "render_terraform_artifacts", render_infra)
    monkeypatch.setattr(cli, "_write_generated_runtime_manifest", manifest)
    objects = {}

    def write(key, value, *, etag):
        previous = objects.get(key)
        assert (previous.etag if previous else None) == etag
        version = str(len(objects) + 1)
        objects[key] = deployment_state.ObjectVersion(copy.deepcopy(value), version)
        return version

    store = SimpleNamespace(read=lambda key: objects.get(key), write=write)
    monkeypatch.setattr(
        "nebius_cxcli.deployment_local.LocalObjectStore.for_project", lambda _: store
    )

    def forbidden_backend(*args, **kwargs):
        pytest.fail("render consulted backend lifecycle authority")

    monkeypatch.setattr(cli, "_deployment_execution", forbidden_backend)
    first = runner.invoke(cli.app, ["render", str(path), "--force"])
    assert first.exit_code == 0, first.output
    assert "Published render: standard" in first.output
    assert "Generated values:" in first.output
    generation = deployment_state.DeploymentGeneration.capture(
        paths, load_generated_manifest(paths.generated_dir)
    )
    state = deployment_state.DeploymentState(
        store, backend_settings_from_config(payload), assert_held=lambda: None
    )
    state.begin(generation, plan={})
    monkeypatch.setattr(deployment_state.DeploymentState, "assert_publishable", forbidden_backend)
    before = {
        str(p.relative_to(paths.generated_dir)): p.read_bytes()
        for p in paths.generated_dir.rglob("*")
        if p.is_file()
    }
    if mode == "different":
        payload["value"] = 2
    from contextlib import nullcontext

    from nebius_cxcli.deployment_local import DeploymentLocalLock

    backend_before = copy.deepcopy(objects)
    local_lock = paths.project_dir / ".nebius-cxcli" / "render.lock"
    context = DeploymentLocalLock(local_lock) if mode == "local-busy" else nullcontext()
    if mode == "held-execution":
        from nebius_cxcli import deployment_cli

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        context = deployment_cli.deployment_execution(
            config=payload,
            paths=paths,
            target_ref="project",
            operation_id="grafana install",
            bootstrap_backend=False,
        )
    with context:
        result = runner.invoke(cli.app, ["render", str(path), "--force"])
    assert result.exit_code == (1 if mode == "local-busy" else 0), result.output
    assert ("Published render:" in result.output) is (mode != "local-busy")
    assert objects == backend_before
    if mode == "different":
        assert "value = 2" in (paths.infra_dir / "main.tf").read_text()
        return
    assert {
        str(p.relative_to(paths.generated_dir)): p.read_bytes()
        for p in paths.generated_dir.rglob("*")
        if p.is_file()
    } == before
