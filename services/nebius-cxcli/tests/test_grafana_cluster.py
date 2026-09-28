from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest
import yaml
from typer.testing import CliRunner

from grafana_fakes import FakeGrafana, dashboard
from nebius_cxcli import cli, grafana_cli, ordinary_apps
from nebius_cxcli import grafana_cluster as cluster
from nebius_cxcli.app_mutation import app_mutation_scope
from nebius_cxcli.grafana_api import GrafanaError
from nebius_cxcli.grafana_dashboards import content_digest, json_bytes
from nebius_cxcli.grafana_import import execute_imports, prepare_imports
from nebius_cxcli.paths import resolve_project_paths


@pytest.fixture
def bound(tmp_path, monkeypatch):
    from nebius_cxcli import (
        deployment_observation,
        deployment_state,
        generated_manifest,
        soperator_operation_lock,
    )

    paths = resolve_project_paths(tmp_path / "config.yaml")
    payload = {
        "client_info": {},
        "apps": {"charts": [{"id": "grafana", "instance_id": "cluster", "enabled": True}]},
        "deploy": {"targets": [{"instance_id": "cluster"}]},
    }
    paths.config_path.write_text(yaml.safe_dump(payload))
    identity = {"cluster_id": "fixture-cluster", "kubernetes_uid": "fixture-uid"}
    spec = SimpleNamespace(
        namespace="observability",
        release_name="grafana",
        service_name="grafana",
        admin_secret_name="admin",
        admin_user_key="admin-user",
        admin_password_key="admin-password",
    )
    owner = hashlib.sha256(json.dumps([{}, "cluster"], sort_keys=True).encode()).hexdigest()
    release = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": "grafana",
            "namespace": "observability",
            "annotations": {"cxcli.nebius.com/app-owner": owner},
            "generation": 1,
        },
        "spec": {"values": {"admin": {"existingSecret": "admin"}}},
        "status": {"conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}]},
    }
    live = copy.deepcopy(release)
    client = FakeGrafana()
    opened = []

    @contextmanager
    def release_client(*args, fence, **kwargs):
        fence()
        opened.append(True)
        yield client

    def kube(args, **kwargs):
        if "helmrelease" in args:
            return live
        if "crd" in args:
            return {"metadata": {"name": "crd"}}
        return {
            "metadata": {
                "annotations": {
                    "meta.helm.sh/release-name": "grafana",
                    "meta.helm.sh/release-namespace": "observability",
                }
            }
        }

    env = {
        cluster.GRAFANA_TARGET_CLUSTER_ID_ENV: identity["cluster_id"],
        cluster.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "bound-context",
    }

    def handoff(*args, **kwargs):
        assert kwargs["allow_terraform_output"] is False
        assert kwargs["persist_local_kubeconfig"] is False
        assert kwargs["set_current_context"] is False
        return env

    monkeypatch.setattr(cli, "_load_context_readonly", lambda _: (payload, paths))
    monkeypatch.setattr(generated_manifest, "load_generated_manifest", lambda _: {})
    monkeypatch.setattr(
        ordinary_apps,
        "validate_ordinary_app_scope",
        lambda *a, **kw: {"identities": {"cluster": identity}},
    )
    state = SimpleNamespace(read=lambda: SimpleNamespace(value={"active": None, "accepted": {}}))
    monkeypatch.setattr(ordinary_apps, "assert_accepted_deployment", lambda *a, **kw: state)
    monkeypatch.setattr(ordinary_apps, "_validate_live_lifecycle_complete", lambda *a, **kw: None)
    monkeypatch.setattr(deployment_state.DeploymentGeneration, "capture", lambda *a: object())
    monkeypatch.setattr(deployment_observation, "target_documents", lambda *a: {"grafana": release})
    lease = SimpleNamespace(assert_held=lambda: None)
    monkeypatch.setattr(cli, "_deployment_execution", lambda **_: nullcontext(lease))
    monkeypatch.setattr(
        soperator_operation_lock, "SoperatorOperationLease", lambda **_: nullcontext(lease)
    )
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", handoff)
    monkeypatch.setattr(
        cli, "_read_kube_system_namespace_uid", lambda **_: identity["kubernetes_uid"]
    )
    monkeypatch.setattr(cluster, "grafana_release_specs", lambda *a, **kw: [spec])
    monkeypatch.setattr(cluster, "_kubectl_json", kube)
    monkeypatch.setattr(cluster, "release_client", release_client)
    return SimpleNamespace(
        paths=paths,
        payload=payload,
        spec=spec,
        release=release,
        live=live,
        env=env,
        client=client,
        opened=opened,
    )


def test_session_accepts_ready_owned_ordinary_app_without_accepted_app_generation(bound):
    with cluster.cluster_session(bound.paths.config_path, "cluster", mutating=True) as session:
        assert session.client is bound.client
        assert session.identity["cluster_id"] == "fixture-cluster"
    assert bound.opened == [True]


def test_import_reuses_auth_handoff_and_selection_inventory_with_fresh_locked_admission(
    bound, monkeypatch, tmp_path
):
    handoffs = []
    admissions = []
    handoff = cli._prepare_cluster_handoff_kube_env
    admission = ordinary_apps.assert_accepted_deployment

    def connect(*args, **kwargs):
        handoffs.append(True)
        return handoff(*args, **kwargs)

    def admit(*args, **kwargs):
        admissions.append(True)
        return admission(*args, **kwargs)

    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", connect)
    monkeypatch.setattr(ordinary_apps, "assert_accepted_deployment", admit)
    monkeypatch.setattr(grafana_cli, "selected_target", lambda *_: "cluster")
    monkeypatch.setattr(grafana_cli, "interactive", lambda: True)
    selections = []

    def select(exc):
        assert len(admissions) == 1
        selections.append(exc.source)
        return "metrics"

    monkeypatch.setattr(grafana_cli, "_select_datasource", select)
    source = tmp_path / "input.json"
    source.write_bytes(
        json_bytes(
            dashboard(
                panels=[
                    {"datasource": {"type": "prometheus", "uid": "missing-a"}},
                    {"datasource": {"type": "prometheus", "uid": "missing-b"}},
                ]
            )
        )
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(source),
            "--config",
            str(bound.paths.config_path),
            "--target",
            "cluster",
        ],
    )
    assert result.exit_code == 0, result.output
    assert selections == ["missing-a", "missing-b"]
    assert len(admissions) == 2  # Discovery never substitutes for locked admission.
    assert len(handoffs) == 1
    assert bound.opened == [True, True]  # Fresh Secret/tunnel/client after human input.
    assert sum(path == "api/datasources" for _, path, _ in bound.client.calls) == 2
    assert bound.client.writes == 1


def test_managed_cluster_import_stops_before_selection_or_mutation_lease(
    bound, monkeypatch, tmp_path
):
    execute_imports(bound.client, prepare_imports(bound.client, [dashboard()]), emit=lambda _: None)
    bound.client.resources["board"]["metadata"]["annotations"]["grafana.app/managedBy"] = "file"
    bound.client.calls.clear()
    monkeypatch.setattr(grafana_cli, "selected_target", lambda *_: "cluster")
    monkeypatch.setattr(grafana_cli, "interactive", lambda: True)
    monkeypatch.setattr(
        grafana_cli, "_select_datasource", lambda _: pytest.fail("Unexpected prompt")
    )
    monkeypatch.setattr(cli, "_deployment_execution", lambda **_: pytest.fail("Unexpected lease"))
    source = tmp_path / "input.json"
    source.write_bytes(
        json_bytes(dashboard(panels=[{"datasource": {"type": "prometheus", "uid": "missing"}}]))
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "grafana",
            "import",
            str(source),
            "--config",
            str(bound.paths.config_path),
            "--target",
            "cluster",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "provision" in result.output and "new UID" in result.output
    assert not any(
        method == "POST" or path == "api/datasources" for method, path, _ in bound.client.calls
    )
    assert bound.opened == [True]


@pytest.mark.parametrize("drift", ["config", "identity", "closed", "release", "kube_uid"])
def test_reused_handoff_rejects_changed_admission_before_reopening_grafana(
    bound, monkeypatch, drift
):
    with cluster.cluster_session(bound.paths.config_path, "cluster", mutating=False) as discovery:
        if drift == "config":
            bound.paths.config_path.write_text(bound.paths.config_path.read_text() + "# edited\n")
        elif drift == "identity":
            discovery.identity["cluster_id"] = "different"
        elif drift == "closed":
            discovery.active = False
        elif drift == "release":
            bound.live["spec"]["suspend"] = True
        else:
            monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **_: "changed")
        with (
            pytest.raises(GrafanaError),
            cluster.cluster_session(
                bound.paths.config_path, "cluster", mutating=True, discovery=discovery
            ),
        ):
            pytest.fail("Reused changed discovery admission")
    assert bound.opened == [True]
    assert not discovery.active


def test_discovery_auth_outlives_locked_connection_and_closes_on_failure(bound, monkeypatch):
    events = []

    @contextmanager
    def auth():
        events.append("auth-open")
        try:
            yield bound.env
        finally:
            events.append("auth-close")

    def handoff(*args, stack, **kwargs):
        return stack.enter_context(auth())

    @contextmanager
    def release_client(*args, **kwargs):
        assert "auth-close" not in events
        events.append("client-open")
        try:
            yield bound.client
        finally:
            assert "auth-close" not in events
            events.append("client-close")

    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", handoff)
    monkeypatch.setattr(cluster, "release_client", release_client)
    with (
        pytest.raises(GrafanaError, match="fixture"),
        cluster.cluster_session(bound.paths.config_path, "cluster", mutating=False) as discovery,
        cluster.cluster_session(
            bound.paths.config_path, "cluster", mutating=True, discovery=discovery
        ),
    ):
        raise GrafanaError("fixture failure")
    assert events == [
        "auth-open",
        "client-open",
        "client-open",
        "client-close",
        "client-close",
        "auth-close",
    ]
    assert not discovery.active


def test_session_reports_lease_stage_before_acquiring_or_contacting_grafana(bound, monkeypatch):
    stages = []

    @contextmanager
    def busy(**kwargs):
        assert stages[-1] == "Acquiring project operation lease"
        raise RuntimeError("Another workstation owns the Deployment lease")
        yield  # pragma: no cover

    monkeypatch.setattr(cli, "_deployment_execution", busy)
    with (
        pytest.raises(RuntimeError, match="Another workstation owns"),
        cluster.cluster_session(
            bound.paths.config_path, "cluster", mutating=True, progress=stages.append
        ),
    ):
        pytest.fail("Lease conflict admitted the import")
    assert stages == ["Loading project configuration", "Acquiring project operation lease"]
    assert not bound.opened


def test_config_edit_during_handoff_is_not_adopted(bound, monkeypatch):
    def handoff(*a, **kw):
        bound.paths.config_path.write_text("concurrent: editor\n")
        return bound.env

    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", handoff)
    with (
        pytest.raises(GrafanaError, match="Configuration changed"),
        cluster.cluster_session(bound.paths.config_path, "cluster", mutating=True),
    ):
        pytest.fail("admitted edited config")
    assert not bound.opened


def test_cluster_identity_mismatch_prevents_secret_access(bound, monkeypatch):
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **_: "wrong")
    with (
        pytest.raises(GrafanaError, match="identity"),
        cluster.cluster_session(bound.paths.config_path, "cluster", mutating=True),
    ):
        pass
    assert not bound.opened


@pytest.mark.parametrize(
    "field,value",
    [
        ("kubeConfig", {"secretRef": {"name": "elsewhere"}}),
        ("valuesFrom", [{"kind": "Secret", "name": "elsewhere"}]),
        ("postRenderers", [{"kustomize": {}}]),
        ("suspend", True),
    ],
)
def test_live_release_indirection_or_drift_is_rejected_before_credentials(bound, field, value):
    bound.live["spec"][field] = value
    with (
        pytest.raises(GrafanaError, match="configuration"),
        cluster.cluster_session(bound.paths.config_path, "cluster", mutating=True),
    ):
        pass
    assert not bound.opened


def declaration(payload, value):
    entry = {
        "uid": value["uid"],
        "json_file": "dashboards/board.json",
        "sha256": content_digest(value),
    }
    payload["apps"]["charts"][0]["dashboard_imports"] = [entry]
    return entry


def test_pre_apply_guard_blocks_linked_file_provisioned_takeover(bound):
    plans = prepare_imports(bound.client, [dashboard()])
    execute_imports(bound.client, plans, emit=lambda _: None)
    bound.client.resources["board"]["metadata"]["annotations"]["grafana.app/managedBy"] = (
        "classic-file-provisioning"
    )
    entry = declaration(bound.payload, plans[0].dashboard)
    entry["catalog_key"] = "folder/board"
    with app_mutation_scope(lambda: None), pytest.raises(GrafanaError, match="provisioned"):
        cluster.preflight_dashboard_ownership(bound.payload, target="cluster", env=bound.env)
    assert bound.client.writes == 1


@pytest.mark.parametrize("with_manual_copy", [False, True])
def test_replay_restores_frozen_asset_but_verification_is_read_only(
    bound, monkeypatch, with_manual_copy
):
    from nebius_cxcli import grafana_project

    monkeypatch.setattr(grafana_project, "assert_catalog_ownership", lambda *_: None)
    value = prepare_imports(bound.client, [dashboard()])[0].dashboard
    declaration(bound.payload, value)
    if with_manual_copy:
        bound.payload["apps"]["charts"][0]["dashboard_imports"].append(
            {
                "uid": "manual-copy",
                "json_file": "absent-manual.json",
                "sha256": "a" * 64,
                "replay": False,
                "management_sha256": "b" * 64,
            }
        )
    path = (
        bound.paths.generated_dir
        / "grafana_dashboards"
        / "cluster"
        / "cxcli-api-imports"
        / "board.json"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(json_bytes(value))
    assert not cluster.replay_dashboards(
        bound.payload, bound.paths, target="cluster", env=bound.env, verify_only=True
    )
    assert bound.client.writes == 0
    with pytest.raises(GrafanaError, match="authority"):
        cluster.replay_dashboards(bound.payload, bound.paths, target="cluster", env=bound.env)
    with app_mutation_scope(lambda: None):
        assert cluster.replay_dashboards(
            bound.payload, bound.paths, target="cluster", env=bound.env
        )
    assert bound.client.writes == 1
    assert cluster.replay_dashboards(
        bound.payload, bound.paths, target="cluster", env=bound.env, verify_only=True
    )
    bound.client.resources["board"]["spec"]["title"] = "Browser edit"
    with app_mutation_scope(lambda: None):
        assert cluster.replay_dashboards(
            bound.payload, bound.paths, target="cluster", env=bound.env
        )
    assert bound.client.writes == 1
    assert bound.client.resources["board"]["spec"]["title"] == "Browser edit"
    assert cluster.replay_dashboards(
        bound.payload, bound.paths, target="cluster", env=bound.env, verify_only=True
    )
    assert not any("/manual-copy" in path for _, path, _ in bound.client.calls)


@pytest.mark.parametrize("local_grafana", [False, True])
def test_soperator_replay_does_not_import_dashboards_without_target_intent(
    tmp_path, monkeypatch, local_grafana
):
    paths = resolve_project_paths(tmp_path / "config.yaml")
    payload = {
        "apps": {
            "charts": [
                {"id": "soperator", "instance_id": "cluster", "enabled": True},
                {"id": "grafana", "instance_id": "cluster", "enabled": local_grafana},
                {
                    "id": "grafana",
                    "instance_id": "other",
                    "enabled": True,
                    "dashboard_imports": [{"uid": "other-dashboard"}],
                },
            ]
        }
    }

    def unexpected_client(*args, **kwargs):
        pytest.fail("No target import intent: must not open a Grafana API session")

    monkeypatch.setattr(cluster, "release_client", unexpected_client)
    assert cluster.replay_dashboards(payload, paths, target="cluster", env={})
    assert not paths.generated_dir.exists()


def test_manual_only_imports_skip_deploy_preflight_replay_and_acceptance(bound, monkeypatch):
    from nebius_cxcli.grafana_project import declarations

    declaration(bound.payload, dashboard())
    entry = next(row for row in bound.payload["apps"]["charts"] if row["id"] == "grafana")[
        "dashboard_imports"
    ][0]
    entry.update(replay=False, management_sha256="a" * 64)
    assert declarations(bound.payload, "cluster", replay_only=True) == []

    def unexpected(*args, **kwargs):
        pytest.fail("Manual copies must not require live deployment access")

    monkeypatch.setattr(cluster, "release_client", unexpected)
    monkeypatch.setattr(cluster, "_kubectl_json", unexpected)
    cluster.preflight_dashboard_ownership(bound.payload, target="cluster", env={})
    for verify_only in [False, True]:
        assert cluster.replay_dashboards(
            bound.payload, bound.paths, target="cluster", env={}, verify_only=verify_only
        )
    assert not bound.client.calls


def test_import_assets_are_in_frozen_and_ordinary_inventories(tmp_path):
    from nebius_cxcli.application_compatibility import application_files
    from nebius_cxcli.deployment_state import DeploymentGeneration

    paths = resolve_project_paths(tmp_path / "config.yaml")
    path = (
        paths.generated_dir / "grafana_dashboards" / "cluster" / "cxcli-api-imports" / "board.json"
    )
    path.parent.mkdir(parents=True)
    path.write_bytes(json_bytes(dashboard()))
    key = path.relative_to(paths.generated_dir).as_posix()
    generation = DeploymentGeneration.capture(paths, {})
    assert key in generation.files
    assert path in ordinary_apps._ordinary_files(paths, {"deploy": {"targets": []}})
    assert key in application_files(paths)["files"]


def test_http_helm_release_admission_accepts_exact_crd_defaults(bound):
    chart = {
        "spec": {
            "chart": "grafana",
            "version": "12.1.3",
            "sourceRef": {"kind": "HelmRepository", "name": "grafana"},
        }
    }
    bound.release["spec"]["chart"] = copy.deepcopy(chart)
    bound.live["spec"]["chart"] = copy.deepcopy(chart)
    bound.live["spec"]["chart"]["spec"]["reconcileStrategy"] = "ChartVersion"
    bound.release["spec"]["uninstall"] = {"timeout": "5m"}
    bound.live["spec"]["uninstall"] = {"timeout": "5m", "deletionPropagation": "background"}
    cluster._verify_release(bound.spec, bound.release, bound.env)
    bound.live["spec"]["chart"]["spec"]["reconcileStrategy"] = "Revision"
    with pytest.raises(GrafanaError, match="configuration"):
        cluster._verify_release(bound.spec, bound.release, bound.env)


def test_standalone_import_uses_canonical_backend_policy(bound, monkeypatch):
    from nebius_cxcli import cli

    calls = []

    def lease(**kwargs):
        calls.append(kwargs)
        return nullcontext(SimpleNamespace(assert_held=lambda: None))

    monkeypatch.setattr(cli, "_deployment_execution", lease)
    with cluster.cluster_session(bound.paths.config_path, "cluster", mutating=True):
        pass
    assert "lease_policy" not in calls[0]
    assert calls[0]["target_ref"] == "cluster"
