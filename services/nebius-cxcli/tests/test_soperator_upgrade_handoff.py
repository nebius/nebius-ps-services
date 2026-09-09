"""Managed renewable handoff resolves only its declared Terraform output."""

from contextlib import ExitStack
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli


@pytest.mark.parametrize("case", ["managed", "external", "disabled", "missing-output"])
def test_renewable_handoff_reads_only_authorized_managed_output(tmp_path, monkeypatch, case):
    config = SimpleNamespace(
        client_info=SimpleNamespace(
            client_name="client", nebius=SimpleNamespace(project_id="project-test")
        )
    )
    paths = SimpleNamespace(infra_dir=tmp_path / "infra")
    target = {
        "component_id": "mk8s",
        "target_ref": "lab",
        "ownership": "managed",
        "access": "external",
        "cluster_id_output_name": "lab_cluster_id",
    }
    if case == "external":
        target.update(kind="external-mk8s", ownership="external")
    if case == "missing-output":
        target.pop("cluster_id_output_name")
    reads = []
    specs = []
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _: {"RUNTIME": "scoped"})
    monkeypatch.setattr(cli, "_renewable_runtime_auth_env_available", lambda: True)

    def output(directory, name, *, extra_env, initialize):
        assert initialize is False
        reads.append((directory, name, extra_env))
        return "mk8scluster-test"

    def spec(_config, **kwargs):
        specs.append(kwargs)
        return cli._Mk8sKubeconfigSpec(
            cluster_entry_name="cluster",
            user_entry_name="user",
            context_name="lab",
            server="https://cluster.example.invalid",
            ca_pem="TEST-CA",
            exec_command="python",
            exec_args=("-m", "nebius_cxcli", "mk8s-token"),
        )

    monkeypatch.setattr(cli, "terraform_output_raw", output)
    monkeypatch.setattr(cli, "_mk8s_cluster_handoff_spec", spec)
    monkeypatch.setattr(
        cli,
        "_kubeconfig_target_env",
        lambda *a, **kw: pytest.fail("renewable auth must not reuse an ambient context"),
    )
    with ExitStack() as stack:
        kwargs = dict(
            stack=stack,
            target=target,
            persist_local_kubeconfig=False,
            set_current_context=False,
            allow_terraform_output=case != "disabled",
            require_renewable_auth=True,
        )
        if case == "managed":
            env = cli._prepare_cluster_handoff_kube_env(config, paths, **kwargs)
            assert env[cli.GRAFANA_TARGET_CLUSTER_ID_ENV] == "mk8scluster-test"
        else:
            with pytest.raises(RuntimeError, match="immutable cluster_id"):
                cli._prepare_cluster_handoff_kube_env(config, paths, **kwargs)
    if case == "managed":
        assert reads == [(paths.infra_dir, "lab_cluster_id", {"RUNTIME": "scoped"})]
        assert specs == [
            {"cluster_id": "mk8scluster-test", "access": "external", "require_renewable_auth": True}
        ]
    else:
        assert not reads and not specs
