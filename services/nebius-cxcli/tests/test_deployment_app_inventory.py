import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli.deployment_app_inventory import assert_live_release_inventory
from nebius_cxcli.grafana_database import app_owner


@pytest.mark.parametrize(
    "state", ["unchanged", "removed", "renamed", "foreign", "no-flux", "forbidden", "invalid"]
)
def test_live_inventory_detects_omitted_owned_apps_without_local_history(tmp_path, state):
    config = {"client_info": {"client_name": "example"}}
    owner = app_owner(config, "cluster")
    live = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": "old",
            "namespace": "flux-system",
            "uid": "uid",
            "annotations": {"cxcli.nebius.com/app-owner": owner},
        },
        "spec": {},
    }
    desired = copy.deepcopy(live)
    if state == "renamed":
        desired["metadata"]["name"] = "new"
    if state == "foreign":
        live["metadata"]["annotations"]["cxcli.nebius.com/app-owner"] = "someone-else"
    (tmp_path / "kustomization.yaml").write_text(
        "resources: []\n" if state in {"removed", "foreign"} else "resources: [release.yaml]\n"
    )
    if state not in {"removed", "foreign"}:
        (tmp_path / "release.yaml").write_text(yaml.safe_dump(desired))
    calls = []

    def read(namespace, args, **kwargs):
        calls.append(args)
        assert kwargs["check"] and kwargs["kube_context"] == "explicit-context"
        if state == "forbidden":
            raise RuntimeError("Forbidden")
        if args[1] == "crd":
            return SimpleNamespace(stdout="" if state == "no-flux" else "crd/helmreleases")
        return SimpleNamespace(stdout=json.dumps({} if state == "invalid" else {"items": [live]}))

    cli = SimpleNamespace(
        GRAFANA_TARGET_KUBE_CONTEXT_ENV="context", _run_soperator_upgrade_kubectl=read
    )
    kwargs = dict(target_ref="cluster", flux_dir=tmp_path, kube_env={"context": "explicit-context"})
    if state in {"removed", "renamed", "forbidden", "invalid"}:
        with pytest.raises(RuntimeError, match="omit|Forbidden|unavailable"):
            assert_live_release_inventory(cli, config, **kwargs)
    else:
        assert_live_release_inventory(cli, config, **kwargs)
    assert calls


def test_selected_target_with_no_apps_still_admits_live_inventory(tmp_path, monkeypatch):
    from nebius_cxcli import cli
    from nebius_cxcli.deployment_target import deploy_application_target
    from test_deployment_campaign import paths

    config = {"infra": {"components": []}, "apps": {"charts": []}}
    target = {"target_ref": "cluster", "ownership": "external"}
    env = {
        cli.GRAFANA_TARGET_CLUSTER_ID_ENV: "cluster-id",
        cli.GRAFANA_TARGET_KUBE_CONTEXT_ENV: "ctx",
    }
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", lambda *a, **kw: env)
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: "uid")
    owner = app_owner(config, "cluster")
    live = {
        "apiVersion": "helm.toolkit.fluxcd.io/v2",
        "kind": "HelmRelease",
        "metadata": {
            "name": "removed-grafana",
            "namespace": "flux-system",
            "uid": "app-uid",
            "annotations": {"cxcli.nebius.com/app-owner": owner},
        },
    }

    def read(namespace, args, **kwargs):
        assert kwargs["kube_context"] == "ctx"
        return SimpleNamespace(
            stdout="crd/helmreleases" if args[1] == "crd" else json.dumps({"items": [live]})
        )

    monkeypatch.setattr(cli, "_run_soperator_upgrade_kubectl", read)
    with pytest.raises(RuntimeError, match="omit.*installed application"):
        deploy_application_target(
            cli,
            config,
            paths(tmp_path),
            target,
            deploy_validations=[],
            deployment_lease=SimpleNamespace(assert_held=lambda: None),
        )
