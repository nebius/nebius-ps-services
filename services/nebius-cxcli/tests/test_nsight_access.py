from __future__ import annotations

import copy
import io
import json
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from rich.text import Text
from typer.testing import CliRunner

from nebius_cxcli import cli
from nebius_cxcli import nsight_access as access
from nebius_cxcli.grafana_database import OWNER_ANNOTATION, app_owner
from nebius_cxcli.grafana_runtime import (
    GRAFANA_TARGET_CLUSTER_ID_ENV,
    GRAFANA_TARGET_KUBE_CONTEXT_ENV,
)


@pytest.fixture
def live(monkeypatch):
    config = {
        "client_info": {},
        "deploy": {"targets": [{"instance_id": "cluster"}]},
        "apps": {"charts": [{"id": "nsight-streamer", "values": {"tool": "not-deployed"}}]},
    }
    state = SimpleNamespace(config=config, releases=[], workloads=[], services=[], calls=[])
    for tool, http, turn in (("nsys", 31080, 31478), ("ncu", 31081, 31479)):
        release_name = f"live-{tool}"
        metadata = {
            "name": release_name,
            "namespace": "viewers",
            "uid": release_name,
            "generation": 2,
            "annotations": {
                "meta.helm.sh/release-name": release_name,
                "meta.helm.sh/release-namespace": "viewers",
            },
        }
        state.releases.append(
            {
                "metadata": {
                    **copy.deepcopy(metadata),
                    "annotations": {OWNER_ANNOTATION: app_owner(config, "cluster")},
                },
                "spec": {
                    "releaseName": release_name,
                    "targetNamespace": "viewers",
                    "chart": {"spec": {"chart": "nsight-streamer"}},
                    "values": {"tool": tool},
                },
                "status": {
                    "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 2}]
                },
            }
        )
        labels = {"app": f"nsight-streamer-{tool}", "release": release_name}
        container = {
            "name": f"nsight-streamer-{tool}",
            "ports": [{"containerPort": http}, {"containerPort": turn}],
            "env": [
                {"name": "HTTP_PORT", "value": str(http)},
                {"name": "TURN_PORT", "value": str(turn)},
                {
                    "name": "WEB_USERNAME",
                    "valueFrom": {"secretKeyRef": {"name": "live-login", "key": "custom.user"}},
                },
                {
                    "name": "WEB_PASSWORD",
                    "valueFrom": {"secretKeyRef": {"name": "live-login", "key": "custom.password"}},
                },
            ],
        }
        state.workloads.append(
            {
                "metadata": copy.deepcopy(metadata),
                "spec": {
                    "replicas": 1,
                    "template": {
                        "metadata": {"labels": labels},
                        "spec": {"containers": [container]},
                    },
                },
                "status": {
                    "observedGeneration": 2,
                    "replicas": 1,
                    "updatedReplicas": 1,
                    "availableReplicas": 1,
                },
            }
        )
        state.services.append(
            {
                "metadata": {**copy.deepcopy(metadata), "name": f"renamed-{tool}"},
                "spec": {
                    "type": "ClusterIP",
                    "selector": labels,
                    "ports": [{"name": "http", "port": http}, {"name": "turn", "port": turn}],
                },
            }
        )
    state.secret = [
        "secret-uid",
        "live-login",
        "viewers",
        "12",
        "active",
        "custom.password",
        "custom.user",
    ]

    def read(env, kind, *, namespace="", name=""):
        state.calls.append((kind, namespace, name))
        resources = {
            "helmreleases.helm.toolkit.fluxcd.io": state.releases,
            "helmrelease": state.releases,
            "deployments": state.workloads,
            "deployment": state.workloads,
            "services": state.services,
            "service": state.services,
        }
        if kind == "secret":
            return copy.deepcopy(state.secret)
        items = resources[kind]
        return copy.deepcopy(
            next(r for r in items if r["metadata"]["name"] == name) if name else {"items": items}
        )

    state.read = read
    state.container = lambda index=0: state.workloads[index]["spec"]["template"]["spec"][
        "containers"
    ][0]
    monkeypatch.setattr(access, "_read", read)
    return state


def result_for(live, tmp_path):
    return access.NsightAccess(
        "cluster",
        access.inspect_live_nsight(live.config, "cluster", {}),
        tmp_path / "kube configs" / "a 'quoted' config",
        "cluster ' $(unexpected)",
    )


def test_live_names_ports_and_references_override_local_values(live, tmp_path):
    result = result_for(live, tmp_path)
    commands = result.commands()
    assert len(commands) == 3
    for index, tool in enumerate(("nsys", "ncu")):
        tokens = shlex.split(commands[index])
        assert tokens[:7] == [
            "kubectl",
            "--kubeconfig",
            str(result.kubeconfig),
            "--context",
            result.kube_context,
            "--namespace",
            "viewers",
        ]
        assert tokens[7:11] == ["port-forward", "--address", "127.0.0.1", f"service/renamed-{tool}"]
        assert tokens[-2:] == (
            ["31080:31080", "31478:31478"] if index == 0 else ["31081:31081", "31479:31479"]
        )
    assert "jsonpath={.data.custom\\.password}" in shlex.split(commands[2])
    assert commands[2].endswith(" | base64 --decode && printf '\\n'")
    assert result.viewers[0].browser_url == "http://127.0.0.1:31080"
    assert all("not-deployed" not in command for command in commands)


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "duplicate",
        "owner",
        "suspended",
        "remote",
        "generation",
        "rollout",
        "workload-owner",
        "service-owner",
        "selector",
        "broad-selector",
        "missing-app-selector",
        "workload-labels",
        "headless",
        "public-service",
        "duplicate-service",
        "literal-password",
        "optional-password",
        "duplicate-password",
        "aliased-password",
        "separate-password",
        "missing-key",
        "empty-key",
        "deleted-secret",
        "udp",
        "missing-port",
        "duplicate-port",
        "wrong-target-port",
        "turn-advertisement",
        "bad-port",
        "collision",
    ],
)
def test_unverifiable_live_access_fails_without_secret_values(live, fault):
    release, workload, service = live.releases[0], live.workloads[0], live.services[0]
    container = live.container()
    if fault == "missing":
        live.releases.pop()
    elif fault == "duplicate":
        live.releases.append(copy.deepcopy(release))
    elif fault == "owner":
        release["metadata"]["annotations"][OWNER_ANNOTATION] = "foreign"
    elif fault == "suspended":
        release["spec"]["suspend"] = True
    elif fault == "remote":
        release["spec"]["kubeConfig"] = {"secretRef": {"name": "other"}}
    elif fault == "generation":
        release["status"]["conditions"][0]["observedGeneration"] = 1
    elif fault == "rollout":
        workload["status"]["availableReplicas"] = 0
    elif fault == "workload-owner":
        workload["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
    elif fault == "service-owner":
        service["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
    elif fault == "selector":
        service["spec"]["selector"] = {"app": "foreign"}
    elif fault == "broad-selector":
        service["spec"]["selector"].pop("release")
    elif fault == "missing-app-selector":
        service["spec"]["selector"].pop("app")
    elif fault == "workload-labels":
        workload["spec"]["template"]["metadata"]["labels"]["release"] = "foreign"
    elif fault == "headless":
        service["spec"]["clusterIP"] = "None"
    elif fault == "public-service":
        service["spec"]["type"] = "NodePort"
    elif fault == "duplicate-service":
        live.services.append(copy.deepcopy(service))
    elif fault == "literal-password":
        container["env"][3]["value"] = "must-not-appear"
    elif fault == "optional-password":
        container["env"][3]["valueFrom"]["secretKeyRef"]["optional"] = True
    elif fault == "duplicate-password":
        container["env"].append(copy.deepcopy(container["env"][3]))
    elif fault == "aliased-password":
        container["env"][2]["valueFrom"] = copy.deepcopy(container["env"][3]["valueFrom"])
    elif fault == "separate-password":
        live.container(1)["env"][3]["valueFrom"]["secretKeyRef"]["name"] = "other-login"
    elif fault in {"missing-key", "empty-key"}:
        live.secret.remove("custom.password")
    elif fault == "deleted-secret":
        live.secret[4] = "deleting"
    elif fault == "udp":
        service["spec"]["ports"][1]["protocol"] = "UDP"
    elif fault == "missing-port":
        service["spec"]["ports"].pop()
    elif fault == "duplicate-port":
        service["spec"]["ports"].append(copy.deepcopy(service["spec"]["ports"][1]))
    elif fault == "wrong-target-port":
        service["spec"]["ports"][0]["targetPort"] = 1234
    elif fault == "turn-advertisement":
        service["spec"]["ports"][1].update(port=1234, targetPort=31478)
    elif fault == "bad-port":
        service["spec"]["ports"][0]["port"] = True
    elif fault == "collision":
        other = live.container(1)
        other["ports"][0]["containerPort"] = 31080
        other["env"][0]["value"] = "31080"
        live.services[1]["spec"]["ports"][0]["port"] = 31080
    with pytest.raises((ValueError, access.NsightAccessError)) as exc:
        access.inspect_live_nsight(live.config, "cluster", {})
    assert "must-not-appear" not in str(exc.value)
    if fault in {"aliased-password", "literal-password", "separate-password"}:
        assert all(kind != "secret" for kind, _, _ in live.calls)


def test_named_http_target_port_is_resolved_without_changing_turn_port(live):
    live.container()["ports"][0]["name"] = "web"
    live.services[0]["spec"]["ports"][0].update(port=8080, targetPort="web")
    viewers = access.inspect_live_nsight(live.config, "cluster", {})
    assert viewers[0].http_port == 8080
    assert viewers[0].turn_port == 31478


@pytest.mark.parametrize("kind", ["helmrelease", "deployment", "service", "secret"])
def test_mid_discovery_changes_are_rejected(live, monkeypatch, kind):
    reads = 0

    def read(env, resource, **kwargs):
        nonlocal reads
        result = live.read(env, resource, **kwargs)
        if resource == kind:
            reads += 1
            if kind == "secret":
                if reads > 1:
                    result[3] = "new-version"
            else:
                result["metadata"]["uid"] = "replacement"
        return result

    monkeypatch.setattr(access, "_read", read)
    with pytest.raises(access.NsightAccessError, match="changed during"):
        access.inspect_live_nsight(live.config, "cluster", {})


@pytest.fixture
def handoff(live, monkeypatch, tmp_path):
    from nebius_cxcli import deployment_cli, kubeconfig_target

    identity = {"cluster_id": "recorded-cluster", "kubernetes_uid": "recorded-uid"}
    state = SimpleNamespace(
        identity=identity,
        persisted=tmp_path / "durable",
        persist_calls=[],
        verified=[],
        uid_calls=[],
        env={
            "KUBECONFIG": str(tmp_path / "scratch"),
            GRAFANA_TARGET_CLUSTER_ID_ENV: identity["cluster_id"],
            GRAFANA_TARGET_KUBE_CONTEXT_ENV: "verified-context",
        },
    )
    state.record = {
        "accepted": {
            "evidence": {
                "identities": {"cluster": identity},
                "targets": {"cluster": {"jailState": {"owned": True}}},
            }
        }
    }

    def prepare(*args, **kwargs):
        assert kwargs["target"]["cluster_id"] == "recorded-cluster"
        assert "kube_context" not in kwargs["target"]
        assert kwargs["set_current_context"] is kwargs["persist_local_kubeconfig"] is False
        assert kwargs["allow_terraform_output"] is False
        assert kwargs["require_renewable_auth"] is True
        return state.env

    def uid(**kwargs):
        state.uid_calls.append(kwargs["extra_env"]["KUBECONFIG"])
        return state.identity["kubernetes_uid"]

    def persist(**kwargs):
        state.persist_calls.append(kwargs)
        return state.persisted

    monkeypatch.setattr(cli, "_load_context_readonly", lambda path: (live.config, None))
    monkeypatch.setattr(
        deployment_cli,
        "read_local_deployment_record",
        lambda path: SimpleNamespace(value=state.record),
    )
    monkeypatch.setattr(cli, "_prepare_cluster_handoff_kube_env", prepare)
    monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", uid)
    monkeypatch.setattr(
        cli,
        "_mk8s_cluster_handoff_spec",
        lambda *a, **kw: SimpleNamespace(
            context_name="verified-context", server="fixture", ca_pem="fixture"
        ),
    )
    monkeypatch.setattr(cli, "_persist_cluster_handoff_kubeconfig", persist)
    monkeypatch.setattr(
        kubeconfig_target, "verify_context_cluster", lambda path, **kw: state.verified.append(path)
    )
    return state


def test_show_uses_recorded_identity_and_proves_durable_access(live, handoff, tmp_path):
    result = access.show_nsight_access(tmp_path / "config.yaml", "cluster")
    assert result.kubeconfig == handoff.persisted
    assert handoff.persist_calls[0]["set_current_context"] is False
    assert handoff.verified == [handoff.persisted]
    assert handoff.uid_calls == [handoff.env["KUBECONFIG"], str(handoff.persisted)]


@pytest.mark.parametrize(
    "fault",
    [
        "unaccepted",
        "non-soperator",
        "no-identity",
        "wrong-cluster",
        "wrong-durable",
        "context",
        "persistence",
    ],
)
def test_handoff_failure_prints_no_partial_commands(live, handoff, monkeypatch, tmp_path, fault):
    from nebius_cxcli import kubeconfig_target

    if fault == "unaccepted":
        handoff.record.clear()
    elif fault == "non-soperator":
        handoff.record["accepted"]["evidence"]["targets"]["cluster"].clear()
    elif fault == "no-identity":
        handoff.record["accepted"]["evidence"]["identities"].clear()
    elif fault == "wrong-cluster":
        handoff.env[GRAFANA_TARGET_CLUSTER_ID_ENV] = "other"
    elif fault == "wrong-durable":
        uids = iter(["recorded-uid", "other"])
        monkeypatch.setattr(cli, "_read_kube_system_namespace_uid", lambda **kw: next(uids))
    elif fault == "context":
        monkeypatch.setattr(
            cli,
            "_mk8s_cluster_handoff_spec",
            lambda *a, **kw: SimpleNamespace(context_name="other"),
        )
    elif fault == "persistence":

        def invalid(*a, **kw):
            raise RuntimeError("Persistent context is unavailable")

        monkeypatch.setattr(kubeconfig_target, "verify_context_cluster", invalid)
    invocation = CliRunner().invoke(
        cli.app,
        ["soperator", "profiling", "show", str(tmp_path / "config.yaml"), "--target", "cluster"],
    )
    assert invocation.exit_code == 1, invocation.output
    assert "Error:" in invocation.output
    assert "kubectl " not in invocation.output
    assert "# Soperator" not in invocation.output


def test_persistence_opt_out_requires_existing_verified_context(
    live, handoff, monkeypatch, tmp_path
):
    handoff.persisted = None
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = access.show_nsight_access(tmp_path / "config.yaml", "cluster")
    assert result.kubeconfig == tmp_path / ".kube/config"
    assert handoff.verified == [result.kubeconfig]


@pytest.mark.parametrize("terminal,no_color", [(True, False), (True, True), (False, False)])
def test_cli_prints_exactly_three_complete_commands_and_unstyled_labels(
    live, handoff, monkeypatch, tmp_path, terminal, no_color
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    stream = io.StringIO()
    console = Console(
        file=stream,
        force_terminal=terminal,
        color_system="truecolor" if terminal else "auto",
        no_color=no_color,
        width=40,
    )
    monkeypatch.setattr(cli, "console", console)
    invocation = CliRunner().invoke(
        cli.app,
        ["soperator", "profiling", "show", str(tmp_path / "config.yaml"), "--target", "cluster"],
    )
    assert invocation.exit_code == 0, invocation.exception
    raw = stream.getvalue()
    text = Text.from_ansi(raw)
    commands = [line for line in text.plain.splitlines() if line.startswith("kubectl ")]
    assert len(commands) == 3
    assert sum("port-forward" in line for line in commands) == 2
    assert sum("get secret" in line for line in commands) == 1
    for command in commands:
        start = text.plain.index(command)
        for i in range(start, start + len(command)):
            style = text.get_style_at_offset(console, i)
            assert (style.bgcolor is not None) is (terminal and not no_color)
    for i, char in enumerate(text.plain):
        if char == "#":
            assert text.get_style_at_offset(console, i).bgcolor is None
    if not terminal:
        assert "\x1b" not in raw


def test_repeated_show_reconstructs_live_commands(live, handoff, tmp_path):
    first = access.show_nsight_access(tmp_path / "config.yaml", "cluster").commands()
    live.services[0]["metadata"]["name"] = "new-live-service"
    second = access.show_nsight_access(tmp_path / "config.yaml", "cluster").commands()
    assert "service/new-live-service" not in first[0]
    assert "service/new-live-service" in second[0]


@pytest.mark.parametrize("kind", ["secret", "services"])
def test_process_boundary_is_bounded_get_and_projects_no_secret_values(monkeypatch, kind):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout="uid\nlogin\nviewers\n1\nactive\npassword\n"
            if kind == "secret"
            else json.dumps({"items": []}),
        )

    monkeypatch.setattr(access.kubernetes_process, "run", run)
    env = {"KUBECONFIG": "verified", GRAFANA_TARGET_KUBE_CONTEXT_ENV: "verified-context"}
    access._read(env, kind, namespace="viewers", name="login" if kind == "secret" else "")
    argv, kwargs = calls[0]
    assert argv[:4] == ["kubectl", "--context", "verified-context", "get"]
    assert "--request-timeout=20s" in argv and kwargs["timeout"] == 30
    assert kwargs["env"]["KUBECONFIG"] == "verified"
    if kind == "secret":
        assert argv[-1] == access._SECRET_PROJECTION
        assert "{{$value}}" not in argv[-1]
        assert "{{$key}}" in argv[-1]


@pytest.mark.parametrize("fault", ["permissions", "timeout", "invalid", "missing-tool"])
def test_process_failures_do_not_echo_remote_output(monkeypatch, fault):
    def run(argv, **kwargs):
        if fault == "timeout":
            raise subprocess.TimeoutExpired(argv, 30, output="must-not-appear")
        if fault == "missing-tool":
            raise OSError("must-not-appear")
        return SimpleNamespace(
            returncode=1 if fault == "permissions" else 0,
            stdout="must-not-appear",
            stderr="must-not-appear",
        )

    monkeypatch.setattr(access.kubernetes_process, "run", run)
    with pytest.raises(access.NsightAccessError) as exc:
        access._read(
            {"KUBECONFIG": "verified", GRAFANA_TARGET_KUBE_CONTEXT_ENV: "verified-context"},
            "services",
            namespace="viewers",
        )
    assert "must-not-appear" not in str(exc.value)
