from __future__ import annotations

import subprocess

import pytest

from nebius_cxcli import kubernetes_process as process


@pytest.mark.parametrize("tool", ["kubectl", "helm", "flux"])
@pytest.mark.parametrize("stream", [False, True])
def test_ambient_kubeconfig_never_supplies_a_target(monkeypatch, tool, stream):
    monkeypatch.setenv("KUBECONFIG", "/unrelated/config")
    monkeypatch.setenv("KUBECTL_CONTEXT", "wrong-cluster")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: calls.append(a))
    with pytest.raises(ValueError, match="explicit target context"):
        (process.popen if stream else process.run)([tool, "get", "all"])
    assert not calls


@pytest.mark.parametrize(
    "tool,flag", [("kubectl", "--context"), ("helm", "--kube-context"), ("flux", "--context")]
)
def test_handoff_overrides_ambient_selection(monkeypatch, tool, flag):
    monkeypatch.setenv("KUBECTL_CONTEXT", "wrong-cluster")
    env = {"KUBECONFIG": "/selected/config", process.TARGET_CONTEXT_ENV: "selected-cluster"}
    assert process.target_command([tool, "get", "all"], env=env) == [
        tool,
        flag,
        "selected-cluster",
        "get",
        "all",
    ]


@pytest.mark.parametrize(
    "args",
    [
        ["kubectl", "--context", "wrong", "get", "pods"],
        ["kubectl", "--context=wrong", "get", "pods"],
        ["kubectl", "--context=", "get", "pods"],
        ["kubectl", "--context", "--namespace=example", "get", "pods"],
        ["kubectl", "--kubeconfig", "/other/config", "get", "pods"],
    ],
)
def test_conflicting_or_empty_selectors_fail_before_execution(monkeypatch, args):
    def forbidden(*a, **kw):
        pytest.fail("subprocess must not run")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError):
        process.run(
            args, env={"KUBECONFIG": "/selected/config", process.TARGET_CONTEXT_ENV: "selected"}
        )


def test_exec_arguments_cannot_select_the_connection():
    with pytest.raises(ValueError, match="explicit target context"):
        process.target_command(["kubectl", "exec", "pod", "--", "tool", "--context", "wrong"])


@pytest.mark.parametrize(
    "args",
    [
        ["kubectl", "kustomize", "rendered"],
        ["kubectl", "version", "--client=true"],
        ["kubectl", "config", "view"],
        ["helm", "template", "release", "chart"],
        ["helm", "template", "server", "chart"],
        ["helm", "pull", "chart"],
        ["flux", "install", "--export"],
        ["flux", "version", "--client"],
        ["terraform", "plan"],
    ],
)
def test_offline_commands_do_not_require_a_cluster(args):
    assert process.target_command(args) == args


@pytest.mark.parametrize(
    "args",
    [
        ["kubectl", "version"],
        ["helm", "template", "release", "chart", "--validate"],
        ["helm", "template", "release", "chart", "--dry-run=server"],
        ["flux", "version"],
        ["flux", "check", "--pre"],
    ],
)
def test_server_operations_remain_guarded(args):
    with pytest.raises(ValueError, match="explicit target context"):
        process.target_command(args)


def test_explicit_context_is_preserved():
    args = ["kubectl", "--context=selected", "get", "pods"]
    assert process.target_command(args) == args


@pytest.mark.parametrize(
    "args",
    [
        ["helm", "template", "release", "chart", "--validate=true"],
        ["helm", "template", "release", "chart", "--dry-run", "server"],
        ["kubectl", "version", "--client", "--client=false"],
        ["flux", "install", "--export", "--export=false"],
        ["kubectl", "get", "pods", "--help", "--help=false"],
        ["helm", "list", "-h", "--help=false"],
        ["flux", "get", "all", "--help", "-h=false"],
    ],
)
def test_ambiguous_client_only_flags_never_authorize_server_access(args):
    with pytest.raises(ValueError, match="explicit target context"):
        process.target_command(args)


@pytest.mark.parametrize(
    "args,env",
    [
        (["kubectl", "--context", "selected", "--cluster", "other", "get", "pods"], {}),
        (["kubectl", "--context", "selected", "--server=https://other.invalid", "get", "pods"], {}),
        (["kubectl", "--context", "selected", "-shttps://other.invalid", "get", "pods"], {}),
        (
            ["helm", "--kube-context", "selected", "list"],
            {"HELM_KUBEAPISERVER": "https://other.invalid"},
        ),
    ],
)
def test_server_override_cannot_bypass_target(args, env):
    with pytest.raises(ValueError, match="overrides cannot replace"):
        process.target_command(args, env=env)


def test_inherited_helm_server_override_is_rejected(monkeypatch):
    monkeypatch.setenv("HELM_KUBEAPISERVER", "https://other.invalid")
    with pytest.raises(ValueError, match="overrides cannot replace"):
        process.target_command(["helm", "--kube-context", "selected", "list"])


@pytest.mark.parametrize("mismatch", [None, "server", "ca", "tls", "duplicate"])
def test_local_context_identity_is_proven_from_provider_endpoint_and_ca(tmp_path, mismatch):
    import base64

    import yaml

    from nebius_cxcli.kubeconfig_target import verify_context_cluster

    cluster = {
        "server": "https://selected.invalid",
        "certificate-authority-data": base64.b64encode(b"test-ca").decode(),
    }
    payload = {
        "current-context": "unrelated",
        "contexts": [{"name": "custom-alias", "context": {"cluster": "local-entry"}}],
        "clusters": [{"name": "local-entry", "cluster": cluster}],
    }
    if mismatch == "server":
        cluster["server"] = "https://unrelated.invalid"
    elif mismatch == "ca":
        cluster["certificate-authority-data"] = base64.b64encode(b"different-ca").decode()
    elif mismatch == "tls":
        cluster["insecure-skip-tls-verify"] = True
    elif mismatch == "duplicate":
        payload["contexts"].append(payload["contexts"][0])
    path = tmp_path / "config"
    path.write_text(yaml.safe_dump(payload))
    if mismatch:
        with pytest.raises(RuntimeError):
            verify_context_cluster(
                path, context="custom-alias", server="https://selected.invalid", ca_pem="test-ca"
            )
    else:
        verify_context_cluster(
            path, context="custom-alias", server="https://selected.invalid", ca_pem="test-ca"
        )


def test_flux_runner_rejects_ambient_connection_before_subprocess(monkeypatch):
    from nebius_cxcli import flux_ops

    monkeypatch.delenv(process.TARGET_CONTEXT_ENV, raising=False)
    monkeypatch.setenv("KUBECONFIG", "/unrelated/config")
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(a))
    with pytest.raises(ValueError, match="explicit target context"):
        flux_ops._run_captured(["kubectl", "get", "pods"])
    assert not calls


def test_flux_runner_passes_selected_context_and_config_to_real_boundary(monkeypatch):
    from nebius_cxcli import flux_ops

    captured = {}

    def run(args, **kwargs):
        captured.update(args=args, env=kwargs["env"])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("KUBECONFIG", "/unrelated/config")
    env = {process.TARGET_CONTEXT_ENV: "selected", "KUBECONFIG": "/selected/config"}
    flux_ops._run_captured(["kubectl", "get", "pods"], extra_env=env)
    assert captured["args"] == ["kubectl", "--context", "selected", "get", "pods"]
    assert captured["env"]["KUBECONFIG"] == "/selected/config"


def test_saved_cluster_id_selects_exact_context_despite_unrelated_current(monkeypatch):
    from contextlib import ExitStack

    from nebius_cxcli import cli

    monkeypatch.setattr(
        cli,
        "_known_kube_context_names",
        lambda: (
            "nebius-target-mk8scluster-old-external",
            "nebius-target-mk8scluster-selected-external",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_kubeconfig_env_for_context",
        lambda name, **kw: {process.TARGET_CONTEXT_ENV: name, "KUBECONFIG": "/selected/config"},
    )
    with ExitStack() as stack:
        env = cli._kubeconfig_target_env(
            "target", stack=stack, preferred_cluster_id="mk8scluster-selected"
        )
        assert env[process.TARGET_CONTEXT_ENV] == "nebius-target-mk8scluster-selected-external"
        with pytest.raises(RuntimeError, match="different cluster ID"):
            cli._kubeconfig_target_env(
                "target",
                stack=stack,
                preferred_cluster_id="mk8scluster-selected",
                preferred_context="nebius-target-mk8scluster-old-external",
            )


def test_flux_rollout_failure_keeps_controller_and_both_streams(monkeypatch):
    from nebius_cxcli import flux_ops

    monkeypatch.setattr(flux_ops, "_require_binary", lambda *a: None)
    monkeypatch.setattr(flux_ops, "wait_for_flux_namespace_ready", lambda **kw: None)
    monkeypatch.setattr(flux_ops, "wait_for_flux_crds_clear", lambda **kw: None)
    monkeypatch.setattr(
        flux_ops,
        "_run_filtered_kubectl_apply",
        lambda *a, **kw: flux_ops.FluxApplySummary(0, 0, 0, 0),
    )

    def run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            1,
            "Waiting for deployment source-controller: 0 of 1 updated replicas are available",
            "error: timed out waiting for the condition\ntoken=secret-value",
        )

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError) as error:
        flux_ops._install_flux_controller_manifest(
            "unused", extra_env={process.TARGET_CONTEXT_ENV: "selected"}
        )
    detail = str(error.value)
    assert "flux-system/source-controller" in detail
    assert "0 of 1 updated replicas" in detail
    assert "timed out waiting" in detail
    assert "secret-value" not in detail


@pytest.mark.parametrize("explicit_context", [False, True])
def test_handoff_rejects_misleading_context_before_any_kubernetes_process(
    tmp_path, monkeypatch, explicit_context
):
    import base64
    from contextlib import ExitStack
    from types import SimpleNamespace

    import yaml

    from nebius_cxcli import cli

    context = "nebius-target-mk8scluster-selected-external"
    config_path = tmp_path / "config"
    config_path.write_text(
        yaml.safe_dump(
            {
                "current-context": "unrelated",
                "contexts": [{"name": context, "context": {"cluster": "misleading"}}],
                "clusters": [
                    {
                        "name": "misleading",
                        "cluster": {
                            "server": "https://unrelated.invalid",
                            "certificate-authority-data": base64.b64encode(b"test-ca").decode(),
                        },
                    }
                ],
            }
        )
    )
    monkeypatch.setattr(cli, "_candidate_kubeconfig_paths", lambda: (str(config_path),))
    monkeypatch.setattr(cli, "_runtime_auth_env_available", lambda: True)
    requested = []

    def provider_spec(config, *, cluster_id, access):
        requested.append(cluster_id)
        return SimpleNamespace(server="https://selected.invalid", ca_pem="test-ca")

    monkeypatch.setattr(cli, "_mk8s_cluster_handoff_spec", provider_spec)
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("unexpected process"))
    target = {"target_ref": "target", "access": "external"}
    if explicit_context:
        target.update(cluster_id="mk8scluster-selected", kube_context=context)
    with ExitStack() as stack, pytest.raises(RuntimeError, match="does not match"):
        cli._prepare_cluster_handoff_kube_env(
            SimpleNamespace(),
            SimpleNamespace(),
            stack=stack,
            target=target,
            allow_terraform_output=False,
            persist_local_kubeconfig=False,
        )
    assert requested == ["mk8scluster-selected"]


@pytest.mark.parametrize("command", ["./bin/auth", "path-auth", "/usr/bin/auth"])
def test_isolated_kubeconfig_preserves_source_relative_paths(tmp_path, monkeypatch, command):
    from contextlib import ExitStack
    from pathlib import Path

    import yaml

    from nebius_cxcli import cli

    source = tmp_path / "config"
    original = yaml.safe_dump(
        {
            "current-context": "unrelated",
            "contexts": [{"name": "selected", "context": {"cluster": "cluster", "user": "user"}}],
            "clusters": [{"name": "cluster", "cluster": {"certificate-authority": "ca.pem"}}],
            "users": [
                {"name": "user", "user": {"client-key": "key.pem", "exec": {"command": command}}}
            ],
        }
    )
    source.write_text(original)
    monkeypatch.setattr(cli, "_candidate_kubeconfig_paths", lambda: (str(source),))
    with ExitStack() as stack:
        env = cli._kubeconfig_env_for_context("selected", stack=stack)
        copied_path = Path(env["KUBECONFIG"])
        copied = yaml.safe_load(copied_path.read_text())
        assert copied["current-context"] == "selected"
        assert copied["clusters"][0]["cluster"]["certificate-authority"] == str(tmp_path / "ca.pem")
        user = copied["users"][0]["user"]
        assert user["client-key"] == str(tmp_path / "key.pem")
        assert user["exec"]["command"] == (
            str(tmp_path / "bin/auth") if command.startswith("./") else command
        )
        assert copied_path.stat().st_mode & 0o777 == 0o600
    assert source.read_text() == original
