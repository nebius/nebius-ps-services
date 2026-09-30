from __future__ import annotations

import base64
import copy
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

from nebius_cxcli import cli, grafana_cli, inventory_ops
from nebius_cxcli import grafana_access as access
from nebius_cxcli.grafana_database import OWNER_ANNOTATION, app_owner
from nebius_cxcli.grafana_runtime import (
    GRAFANA_TARGET_CLUSTER_ID_ENV,
    GRAFANA_TARGET_KUBE_CONTEXT_ENV,
)


@pytest.fixture
def live(monkeypatch):
    config = {"client_info": {}, "deploy": {"targets": [{"instance_id": "cluster"}]}}
    metadata = {
        "name": "live-grafana",
        "namespace": "monitoring",
        "uid": "resource-uid",
        "generation": 2,
        "annotations": {
            "meta.helm.sh/release-name": "live-grafana",
            "meta.helm.sh/release-namespace": "monitoring",
        },
    }
    release = {
        "metadata": {
            **copy.deepcopy(metadata),
            "annotations": {OWNER_ANNOTATION: app_owner(config, "cluster")},
        },
        "spec": {"releaseName": "live-grafana", "targetNamespace": "monitoring"},
        "status": {
            "history": [{"chartName": "grafana"}],
            "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 2}],
        },
    }
    labels = {"app.kubernetes.io/instance": "live-grafana", "app.kubernetes.io/name": "grafana"}
    container = {
        "name": "grafana",
        "ports": [{"name": "http-web", "containerPort": 3000}],
        "readinessProbe": {"httpGet": {"port": "http-web"}},
        "env": [
            {
                "name": "GF_SECURITY_ADMIN_PASSWORD",
                "valueFrom": {"secretKeyRef": {"name": "live-admin", "key": "custom.password"}},
            },
            {
                "name": "GF_SECURITY_ADMIN_USER",
                "valueFrom": {"secretKeyRef": {"name": "live-admin", "key": "custom.user"}},
            },
        ],
    }
    workload = {
        "metadata": copy.deepcopy(metadata),
        "spec": {
            "replicas": 2,
            "template": {"metadata": {"labels": labels}, "spec": {"containers": [container]}},
        },
        "status": {
            "observedGeneration": 2,
            "updatedReplicas": 2,
            "availableReplicas": 2,
            "replicas": 2,
        },
    }
    service = {
        "metadata": copy.deepcopy(metadata),
        "spec": {"selector": labels, "ports": [{"port": 8080, "targetPort": "http-web"}]},
    }
    headless = copy.deepcopy(service)
    headless["spec"]["clusterIP"] = "None"
    state = SimpleNamespace(
        config=config,
        release=release,
        workload=workload,
        service=service,
        headless=headless,
        calls=[],
    )

    def read(env, resource, **kwargs):
        state.calls.append((resource, kwargs))
        result = {
            "helmreleases.helm.toolkit.fluxcd.io": {"items": [state.release]},
            "helmrelease": state.release,
            "deployment": state.workload,
            "service": state.service,
            "deployments": {"items": [state.workload]},
            "services": {"items": [state.service, state.headless]},
        }
        if resource == "secret":
            output = kwargs["output"]
            if output.startswith("go-template="):
                return "custom.password\ncustom.user\n"
            assert output == "jsonpath={.data.custom\\.user}"
            return base64.b64encode(b"live-operator").decode()
        return copy.deepcopy(result[resource])

    monkeypatch.setattr(access, "_read", read)
    state.read = read
    return state


def access_result(tmp_path, **overrides):
    return access.GrafanaAccess(
        **{
            **dict(
                target_ref="cluster",
                namespace="monitoring",
                release_name="live-grafana",
                service_name="live-grafana",
                service_port=8080,
                admin_secret_name="live-admin",
                admin_password_key="custom.password",
                admin_user="live-operator",
                kube_context="verified-context",
                kubeconfig=tmp_path / "space and 'quote" / "config",
            ),
            **overrides,
        }
    )


def test_live_details_override_undeployed_settings_and_ignore_headless_service(live):
    live.config["apps"] = {
        "charts": [{"id": "grafana", "values": {"admin": {"existingSecret": "not-deployed"}}}]
    }
    result = access.inspect_live_grafana(live.config, "cluster", {})
    assert result["service_port"] == 8080
    assert result["admin_secret_name"] == "live-admin"
    assert result["admin_password_key"] == "custom.password"
    assert result["admin_user"] == "live-operator"
    live.service["spec"]["ports"][0]["port"] = 9090
    assert access.inspect_live_grafana(live.config, "cluster", {})["service_port"] == 9090
    assert all(
        kwargs.get("output") != "json" for resource, kwargs in live.calls if resource == "secret"
    )


def test_discovery_rejects_aliased_credentials_before_reading_any_secret(live):
    container = live.workload["spec"]["template"]["spec"]["containers"][0]
    container["env"][1]["valueFrom"] = copy.deepcopy(container["env"][0]["valueFrom"])

    with pytest.raises(access.GrafanaAccessError, match="distinct Secret keys"):
        access.inspect_live_grafana(live.config, "cluster", {})

    assert all(resource != "secret" for resource, _kwargs in live.calls)


@pytest.mark.parametrize(
    "fault",
    [
        "owner",
        "unready",
        "suspended",
        "remote",
        "rollout",
        "service-owner",
        "literal-password",
        "ambiguous-port",
        "no-history",
    ],
)
def test_discovery_refuses_unverifiable_live_access(live, fault):
    if fault == "owner":
        live.release["metadata"]["annotations"][OWNER_ANNOTATION] = "someone-else"
    elif fault == "unready":
        live.release["status"]["conditions"][0]["observedGeneration"] = 1
    elif fault == "suspended":
        live.release["spec"]["suspend"] = True
    elif fault == "remote":
        live.release["spec"]["kubeConfig"] = {"secretRef": {"name": "other"}}
    elif fault == "rollout":
        live.workload["status"]["updatedReplicas"] = 1
    elif fault == "service-owner":
        live.service["metadata"]["annotations"]["meta.helm.sh/release-name"] = "other"
    elif fault == "literal-password":
        live.workload["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = (
            "must-not-appear"
        )
    elif fault == "ambiguous-port":
        live.service["spec"]["ports"].append({"port": 9999, "targetPort": "http-web"})
    else:
        live.release["status"]["history"] = []
    with pytest.raises(access.GrafanaAccessError) as exc:
        access.inspect_live_grafana(live.config, "cluster", {})
    assert "must-not-appear" not in str(exc.value)


def test_commands_quote_paths_use_explicit_identity_and_match_both_presentations(tmp_path):
    result = access_result(tmp_path)
    commands = result.commands(tmp_path / "source config.yaml")
    port = shlex.split(commands[0][1])
    assert port[:3] == ["kubectl", "--kubeconfig", str(result.kubeconfig)]
    assert port[-2:] == ["service/live-grafana", "3000:8080"]
    assert "127.0.0.1" in port and "verified-context" in port
    assert "jsonpath={.data.custom\\.password}" in shlex.split(commands[1][1])
    assert commands[1][1].endswith(" | base64 --decode && printf '\\n'")
    for _, command in commands:
        assert command in result.terminal_lines(tmp_path / "source config.yaml")
        assert command in result.markdown_lines(tmp_path / "source config.yaml")


@pytest.mark.parametrize("resource", ["helmrelease", "deployment", "service"])
def test_changed_resource_snapshot_is_not_presented_as_current(live, monkeypatch, resource):
    def read(env, kind, **kwargs):
        result = live.read(env, kind, **kwargs)
        if kind == resource:
            result["metadata"]["uid"] = "replacement"
        return result

    monkeypatch.setattr(access, "_read", read)
    with pytest.raises(access.GrafanaAccessError, match="changed during"):
        access.inspect_live_grafana(live.config, "cluster", {})


@pytest.fixture
def handoff(live, tmp_path, monkeypatch):
    from nebius_cxcli import kubeconfig_target

    identity = {"cluster_id": "cluster-id", "kubernetes_uid": "namespace-uid"}
    env = {
        "KUBECONFIG": str(tmp_path / "temporary"),
        GRAFANA_TARGET_CLUSTER_ID_ENV: "cluster-id",
        GRAFANA_TARGET_KUBE_CONTEXT_ENV: "verified-context",
    }
    state = SimpleNamespace(
        identity=identity,
        env=env,
        persisted=tmp_path / "durable",
        persist_calls=[],
        verified=[],
        identities=[],
    )
    state.persisted.write_text("fixture")

    def prepare(*args, **kwargs):
        assert kwargs["persist_local_kubeconfig"] is False
        assert kwargs["allow_terraform_output"] is False
        assert kwargs["target"]["cluster_id"] == "cluster-id"
        return env

    def uid(**kwargs):
        state.identities.append(kwargs["extra_env"]["KUBECONFIG"])
        return state.identity["kubernetes_uid"]

    def persist(**kwargs):
        state.persist_calls.append(kwargs)
        return state.persisted

    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", prepare)
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", uid)
    monkeypatch.setattr(
        cli,
        "_mk8s_cluster_handoff_spec",
        lambda *a, **k: SimpleNamespace(
            context_name="verified-context", server="fixture", ca_pem="fixture"
        ),
    )
    monkeypatch.setattr(cli, "_persist_cluster_handoff_kubeconfig", persist)
    monkeypatch.setattr(
        kubeconfig_target,
        "verify_context_cluster",
        lambda path, **kwargs: state.verified.append(path),
    )
    return state


def test_handoff_verifies_durable_context_after_refresh(live, handoff):
    result = access.discover_grafana_access(live.config, None, "cluster", handoff.identity)
    assert result.kubeconfig == handoff.persisted
    assert handoff.persist_calls[0]["set_current_context"] is False
    assert handoff.verified == [handoff.persisted]
    assert handoff.identities == [handoff.env["KUBECONFIG"], str(handoff.persisted)]


def test_wrong_cluster_fails_before_inspecting_resources_or_persisting(live, handoff):
    with pytest.raises(access.GrafanaAccessError, match="identity differs"):
        access.discover_grafana_access(
            live.config, None, "cluster", {**handoff.identity, "kubernetes_uid": "wrong"}
        )
    assert not handoff.persist_calls and not live.calls


def test_persistence_opt_out_reuses_only_a_verified_existing_context(
    live, handoff, monkeypatch, tmp_path
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    expected = tmp_path / ".kube" / "config"
    expected.parent.mkdir()
    expected.write_text("fixture")
    monkeypatch.setattr(cli, "_persist_cluster_handoff_kubeconfig", lambda **k: None)
    result = access.discover_grafana_access(live.config, None, "cluster", handoff.identity)
    assert result.kubeconfig == expected
    assert handoff.verified == [expected]


def test_unverifiable_persistent_context_never_yields_commands(live, handoff, monkeypatch):
    from nebius_cxcli import kubeconfig_target

    def mismatch(*args, **kwargs):
        raise RuntimeError(
            "Selected kubeconfig context does not match the requested cluster endpoint and CA"
        )

    monkeypatch.setattr(kubeconfig_target, "verify_context_cluster", mismatch)
    with pytest.raises(RuntimeError, match="does not match"):
        access.discover_grafana_access(live.config, None, "cluster", handoff.identity)


def test_show_reads_again_each_time_and_prints_commands_without_markup(tmp_path, monkeypatch):
    calls = []

    def discover(path, target):
        calls.append((path, target))
        return access_result(tmp_path, service_port=8080 + len(calls))

    monkeypatch.setattr(access, "show_grafana_access", discover)
    monkeypatch.setattr(grafana_cli, "console", Console(width=200, force_terminal=False))
    args = ["grafana", "show", "--config", str(tmp_path / "config.yaml"), "--target", "cluster"]
    first, second = CliRunner().invoke(cli.app, args), CliRunner().invoke(cli.app, args)
    assert first.exit_code == second.exit_code == 0
    assert "3000:8081" in first.output and "3000:8082" in second.output
    assert len(calls) == 2
    assert "get secret live-admin" in first.output


def test_show_highlights_complete_commands_without_highlighting_labels(tmp_path, monkeypatch):
    import io

    monkeypatch.delenv("NO_COLOR", raising=False)
    result = access_result(tmp_path)
    monkeypatch.setattr(access, "show_grafana_access", lambda *a: result)
    stream = io.StringIO()
    console = Console(file=stream, force_terminal=True, color_system="truecolor", width=40)
    monkeypatch.setattr(grafana_cli, "console", console)
    config = tmp_path / "project path" / "config.yaml"
    invocation = CliRunner().invoke(
        cli.app, ["grafana", "show", "--config", str(config), "--target", "cluster"]
    )
    assert invocation.exit_code == 0, invocation.output
    text = Text.from_ansi(stream.getvalue())
    assert text.plain.splitlines() == result.terminal_lines(config)
    for _, command in result.commands(config):
        start = text.plain.index(command)
        assert all(
            text.get_style_at_offset(console, i).bgcolor is not None
            for i in range(start, start + len(command))
        )
    assert text.get_style_at_offset(console, 0).bgcolor is None


def test_show_failure_never_prints_cached_commands(tmp_path, monkeypatch):
    def fail(*a):
        raise access.GrafanaAccessError("Cluster unavailable")

    monkeypatch.setattr(access, "show_grafana_access", fail)
    result = CliRunner().invoke(
        cli.app, ["grafana", "show", "--config", "config.yaml", "--target", "cluster"]
    )
    assert result.exit_code == 1 and "Cluster unavailable" in result.output
    assert "port-forward" not in result.output


def test_show_requires_both_explicit_selectors():
    for args in (["--target", "cluster"], ["--config", "config.yaml"]):
        result = CliRunner().invoke(cli.app, ["grafana", "show", *args])
        assert result.exit_code == 2


def test_deployment_footer_keeps_two_targets_and_commands_copyable(tmp_path, monkeypatch):
    import io

    from test_deployment_reports import paths

    stream = io.StringIO()
    monkeypatch.setattr(cli, "console", Console(file=stream, width=400, force_terminal=False))
    monkeypatch.setattr(cli, "wireguard_access_command_hints", lambda *a: ())
    monkeypatch.setattr(cli, "ssh_jump_access_hints", lambda *a: ())
    outputs = (
        access_result(tmp_path),
        access_result(tmp_path, target_ref="second", kube_context="second-context"),
    )
    location = paths(tmp_path)
    cli._print_deploy_command_footer(
        {}, location, cli.DeployRunSummary(grafana_access=outputs), succeeded=True
    )
    text = stream.getvalue()
    for item in outputs:
        assert item.target_ref in text
        for _, command in item.commands(location.config_path):
            assert command in text
    assert "Deploy completed" in text


def test_show_uses_recorded_identity_without_rendered_settings_admission(
    live, tmp_path, monkeypatch
):
    from nebius_cxcli import deployment_cli
    from test_deployment_reports import paths

    location = paths(tmp_path)
    identity = {"cluster_id": "cluster-id", "kubernetes_uid": "namespace-uid"}
    monkeypatch.setattr(cli, "_load_context_readonly", lambda p: (live.config, location))
    monkeypatch.setattr(
        deployment_cli,
        "read_local_deployment_record",
        lambda p: SimpleNamespace(
            value={"accepted": {"evidence": {"identities": {"cluster": identity}}}}
        ),
    )
    seen = []

    def discover(config, path, target, selected):
        seen.append((config, path, target, selected))
        return access_result(tmp_path)

    monkeypatch.setattr(access, "discover_grafana_access", discover)
    access.show_grafana_access(location.config_path, "cluster")
    assert seen == [(live.config, location, "cluster", identity)]


def test_secret_reader_projects_only_keys_and_username(monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="custom.user\ncustom.password\n")

    monkeypatch.setattr(access.kubernetes_process, "run", run)
    env = {GRAFANA_TARGET_KUBE_CONTEXT_ENV: "selected", "KUBECONFIG": "fixture"}
    output = 'go-template={{range $key, $value := .data}}{{printf "%s\\n" $key}}{{end}}'
    assert "custom.password" in access._read(
        env, "secret", namespace="monitoring", name="admin", output=output
    )
    assert commands[0][:4] == ["kubectl", "--context", "selected", "get"]
    assert commands[0][-1] == output
    with pytest.raises(access.GrafanaAccessError, match="Secret bodies"):
        access._read(env, "secret", namespace="monitoring", name="admin")
    assert len(commands) == 1


def test_report_includes_private_access_without_cloud_collection(tmp_path, monkeypatch):
    import yaml

    from nebius_cxcli.config_loader import load_config
    from nebius_cxcli.paths import resolve_project_paths
    from test_inventory_ops import (
        _enable_mk8s_observability,
        _project_config_path,
        _starter_payload,
    )

    path = _project_config_path(tmp_path)
    path.parent.mkdir(parents=True)
    data = _starter_payload(selected_infra={"mk8s"}, selected_apps=set())
    _enable_mk8s_observability(data)
    path.write_text(yaml.safe_dump(data))
    config, paths = load_config(path), resolve_project_paths(path)
    result = access_result(tmp_path, target_ref="mk8s")
    real = inventory_ops._build_payload

    def payload(*args):
        value = real(*args)
        value["apps"]["observability"]["enabled"] = False
        value["apps"]["observability"]["vm_monitoring_agent"] = False
        value["apps"]["observability_endpoints"]["configured"] = False
        return value

    monkeypatch.setattr(inventory_ops, "_build_payload", payload)
    markdown = inventory_ops.write_inventory(
        config, paths, grafana_access=[result]
    ).markdown.read_text()
    assert "## Grafana" in markdown
    for _, command in result.commands(paths.config_path):
        assert command in markdown
    assert "- Grafana: `pending`" not in markdown


def test_successful_completion_publishes_handoff_even_without_application_summary(
    tmp_path, monkeypatch
):
    from nebius_cxcli import grafana_runtime
    from test_deployment_reports import paths

    source, destination = paths(tmp_path / "scratch"), paths(tmp_path / "workspace with 'quote")
    result = access_result(tmp_path)
    monkeypatch.setattr(grafana_runtime, "grafana_enabled_for_target", lambda *a, **k: True)
    monkeypatch.setattr(access, "discover_grafana_access", lambda *a: result)

    def report(config, location, **kwargs):
        location.reports_dir.mkdir(parents=True)
        (location.reports_dir / "deploy-report.md").write_text(
            "\n".join(
                kwargs["grafana_access"][0].markdown_lines(kwargs["grafana_command_config_path"])
            )
        )

    monkeypatch.setattr(inventory_ops, "write_inventory", report)
    summary = access.complete_grafana_handoff(
        {}, source, destination, cli.DeployRunSummary(), ["cluster"], []
    )
    assert summary.grafana_access == (result,)
    text = (destination.reports_dir / "deploy-report.md").read_text()
    assert str(source.config_path) not in text
    show_command = result.commands(destination.config_path)[2][1]
    assert show_command in text
    assert shlex.split(show_command)[4] == str(destination.config_path)
    assert "3000:8080" in text


def test_completion_access_failure_preserves_deployment_and_reports_reason(tmp_path, monkeypatch):
    from nebius_cxcli import grafana_runtime

    monkeypatch.setattr(grafana_runtime, "grafana_enabled_for_target", lambda *a, **k: True)

    def fail(*args):
        raise access.GrafanaAccessError("Cluster unavailable")

    monkeypatch.setattr(access, "discover_grafana_access", fail)
    seen = []
    monkeypatch.setattr(inventory_ops, "write_inventory", lambda *a, **k: seen.append(k))
    from test_deployment_reports import paths

    location = paths(tmp_path)
    summary = access.complete_grafana_handoff(
        {}, location, location, cli.DeployRunSummary(), ["cluster"], []
    )
    assert summary.grafana_access_errors == ("cluster: Cluster unavailable",)
    assert seen[0]["grafana_access_errors"] == {"cluster": "Cluster unavailable"}


@pytest.mark.parametrize("verification", [1, 2])
def test_uid_timeout_is_a_safe_access_error(live, handoff, monkeypatch, verification):
    import subprocess

    count = 0

    def uid(**kwargs):
        nonlocal count
        count += 1
        if count == verification:
            raise subprocess.TimeoutExpired(["kubectl", "private-fixture-command"], 30)
        return handoff.identity["kubernetes_uid"]

    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", uid)
    with pytest.raises(access.GrafanaAccessError, match="cluster identity") as exc:
        access.discover_grafana_access(live.config, None, "cluster", handoff.identity)
    assert "private-fixture-command" not in str(exc.value)


def test_unexpected_handoff_error_does_not_reverse_deployment(tmp_path, monkeypatch):
    import subprocess

    from nebius_cxcli import grafana_runtime
    from test_deployment_reports import paths

    monkeypatch.setattr(grafana_runtime, "grafana_enabled_for_target", lambda *a, **k: True)

    def timeout(*a):
        raise subprocess.TimeoutExpired(["fixture-sensitive-command"], 30)

    monkeypatch.setattr(access, "discover_grafana_access", timeout)
    monkeypatch.setattr(inventory_ops, "write_inventory", lambda *a, **k: None)
    location = paths(tmp_path)
    summary = access.complete_grafana_handoff(
        {}, location, location, cli.DeployRunSummary(), ["cluster"], []
    )
    assert "Unable to verify" in summary.grafana_access_errors[0]
    assert "fixture-sensitive-command" not in repr(summary)
