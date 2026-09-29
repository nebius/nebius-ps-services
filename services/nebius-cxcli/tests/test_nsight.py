import shlex
from copy import deepcopy
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired

import pytest
import yaml

from nebius_cxcli.nsight import (
    APP_TOOLS,
    VIEWER_IMAGES,
    configure_viewer,
    configured_viewers,
    report_subpath,
    validate_viewer_values,
    viewer_values,
)
from nebius_cxcli.nsight_runtime import (
    NsightKubernetes,
    access_command,
    password_command,
    persistent_context,
    validate_login_secret,
    validate_reports_pvc,
    validate_viewer_deployment,
)


def viewer(tool="nsys"):
    return {
        "id": "nsight-streamer" if tool == "nsys" else "nsight-streamer-ncu",
        "instance_id": "cluster",
        "release_name": "nsight-streamer" if tool == "nsys" else "nsight-streamer-ncu",
        "namespace": "soperator",
        "enabled": True,
        "values": viewer_values(tool, claim="data", subpath="nsight-reports"),
    }


def test_catalog_has_independent_pinned_viewers():
    catalog = yaml.safe_load((Path(__file__).parents[1] / "component_sources.yaml").read_text())
    for app in APP_TOOLS:
        assert catalog["components"]["apps"][app]["source"]["portable"]["version"] == "2026.4.1"
    rows = configured_viewers({"apps": {"charts": [viewer(), viewer("ncu")]}})
    assert [r["values"]["tool"] for r in rows] == ["nsys", "ncu"]
    assert len({r["values"]["service"]["turnPort"] for r in rows}) == 2


@pytest.mark.parametrize("value", ["/reports", "../reports", "a/../b", "a//b", "a/", "a;pwd", None])
def test_reject_unsafe_pvc_subpaths(value):
    with pytest.raises(ValueError):
        report_subpath(value)


@pytest.mark.parametrize(
    "change",
    [
        {"webPassword": "password"},
        {"tool": "ncu"},
        {"preserveConfig": True},
        {"service": None},
        {"service": {"type": "LoadBalancer"}},
        {"volumeMounts": [{"name": "reports", "mountPath": "/mnt/jail", "readOnly": True}]},
        {"env": [{"name": "WEB_PASSWORD", "value": "password"}]},
    ],
)
def test_reject_unsafe_viewer_values(change):
    values = viewer()["values"]
    values.update(change)
    with pytest.raises(ValueError):
        validate_viewer_values("nsight-streamer", values)


def test_wizard_preserves_custom_ports_and_does_not_prompt_for_credentials():
    row = viewer()
    row["values"]["service"]["httpPort"] = 31080
    prompts = []

    def prompt(label, current):
        prompts.append(label)
        return current

    updated = configure_viewer(row, prompt=prompt)
    assert updated == row
    assert all("Secret" in p for p in prompts if "password" in p or "username" in p)


def test_mac_access_forwards_both_tcp_ports_with_exact_context():
    cmd = access_command(
        kubeconfig=Path("/Users/person/.kube/config"),
        context="my cluster",
        namespace="soperator",
        service_name="nsight-streamer-service",
        http_port=30080,
        turn_port=30478,
    )
    assert "--kubeconfig /Users/person/.kube/config --context 'my cluster'" in cmd
    assert "--namespace soperator port-forward --address 127.0.0.1" in cmd
    assert cmd.endswith("service/nsight-streamer-service 30080:30080 30478:30478")


def test_password_command_quotes_target_and_selects_only_configured_key():
    row = viewer()
    row["namespace"] = "viewer-space"
    row["values"]["webPassword"] = {"secretName": "custom-login", "secretKey": "login.password"}
    context = "cluster ' $(echo unexpected)"
    path = Path("/Users/person/kube configs/selected")
    command = password_command(
        kubeconfig=path,
        context=context,
        namespace=row["namespace"],
        secret_name=row["values"]["webPassword"]["secretName"],
        secret_key=row["values"]["webPassword"]["secretKey"],
    )
    assert shlex.split(command) == [
        "kubectl",
        "--kubeconfig",
        str(path),
        "--context",
        context,
        "--namespace",
        "viewer-space",
        "get",
        "secret",
        "custom-login",
        "-o",
        "jsonpath={.data.login\\.password}",
        "|",
        "base64",
        "--decode",
        "&&",
        "printf",
        "\\n",
    ]


@pytest.mark.parametrize("durable", [False, True])
def test_status_only_builds_password_command_with_verified_persistent_access(monkeypatch, durable):
    from nebius_cxcli import nsight_runtime

    row = viewer()
    row["values"]["webPassword"]["secretName"] = "selected-login"
    path = Path("/Users/person/kube configs/selected")
    observed = []

    class ReadyKube:
        context = "selected cluster"

        def __init__(self, *a, **kw):
            pass

        def get(self, kind, *a):
            if kind == "namespace":
                return {"metadata": {"uid": "cluster-uid"}}
            if kind == "deployment":
                return {
                    "metadata": {"generation": 1},
                    "status": {
                        "observedGeneration": 1,
                        "availableReplicas": 1,
                        "updatedReplicas": 1,
                    },
                }
            assert kind == "service"
            return {
                "spec": {
                    "type": "ClusterIP",
                    "ports": [
                        {
                            "name": name,
                            "port": row["values"]["service"][f"{name}Port"],
                            "protocol": "TCP",
                        }
                        for name in ("http", "turn")
                    ],
                }
            }

        def run(self, args, **kw):
            observed.append(args)
            assert args[2] == "exec"
            return ""

    def persistent(context, *, kubernetes_uid, **kw):
        assert context == ReadyKube.context and kubernetes_uid == "cluster-uid"
        return path if durable else None

    monkeypatch.setattr(nsight_runtime, "NsightKubernetes", ReadyKube)
    monkeypatch.setattr(nsight_runtime, "configured_viewers", lambda *a: [row])
    monkeypatch.setattr(nsight_runtime, "persistent_context", persistent)
    monkeypatch.setattr(nsight_runtime, "validate_viewer_deployment", lambda *a: None)
    monkeypatch.setattr(nsight_runtime, "validate_login_secret", lambda *a: None)
    monkeypatch.setattr(nsight_runtime, "validate_reports_pvc", lambda *a: {})
    messages, commands = [], []
    (status,) = nsight_runtime.collect_nsight_status(
        {}, extra_env={}, target_ref="cluster", emit=messages.append, emit_command=commands.append
    )
    assert observed
    if durable:
        assert commands == [status["port_forward_command"]]
        assert status["port_forward_command"] not in messages
        assert status["password_command"] == password_command(
            kubeconfig=path,
            context=ReadyKube.context,
            namespace=row["namespace"],
            secret_name="selected-login",
            secret_key="password",
        )
        assert status["port_forward_command"] == access_command(
            kubeconfig=path,
            context=ReadyKube.context,
            namespace=row["namespace"],
            service_name="nsight-streamer-service",
            http_port=30080,
            turn_port=30478,
        )
    else:
        assert commands == []
        assert status["access_status"] == "persistent-context-required"
        assert "password_command" not in status and "port_forward_command" not in status


class Kube:
    def __init__(self):
        self.pvc = {
            "metadata": {"name": "data", "namespace": "soperator", "uid": "pvc-1"},
            "spec": {"volumeName": "data", "accessModes": ["ReadWriteMany"]},
            "status": {"phase": "Bound"},
        }
        self.pv = {
            "metadata": {"name": "data", "uid": "pv-1"},
            "spec": {"claimRef": {"name": "data", "namespace": "soperator", "uid": "pvc-1"}},
        }
        self.calls = []

    def get(self, kind, name, namespace=""):
        return deepcopy(self.pvc if kind == "pvc" else self.pv)

    def run(self, args):
        self.calls.append(args)
        return "nsight-streamer-auth\nusername\npassword\n"


def test_pvc_checks_uid_binding_and_exclusive_access():
    kube = Kube()
    assert validate_reports_pvc(kube, viewer())["pvUid"] == "pv-1"
    kube.pv["spec"]["claimRef"]["uid"] = "other"
    with pytest.raises(RuntimeError, match="identity"):
        validate_reports_pvc(kube, viewer())
    kube.pv["spec"]["claimRef"]["uid"] = "pvc-1"
    kube.pvc["spec"]["accessModes"] = ["ReadWriteOncePod"]
    with pytest.raises(RuntimeError, match="ReadWriteOncePod"):
        validate_reports_pvc(kube, viewer())


def test_login_check_only_requests_nonempty_key_names():
    kube = Kube()
    validate_login_secret(kube, viewer())
    assert len(kube.calls) == 2
    assert all("{{if $v}}{{$k}}" in call[-1] for call in kube.calls)
    assert all("base64decode" not in call[-1] for call in kube.calls)


def secret_kube(runner):
    from nebius_cxcli.grafana_runtime import GRAFANA_TARGET_KUBE_CONTEXT_ENV

    return NsightKubernetes(
        {"KUBECONFIG": "test-config", GRAFANA_TARGET_KUBE_CONTEXT_ENV: "test-context"},
        runner=runner,
    )


def test_missing_login_secret_names_prerequisite_without_exposing_stderr():
    def runner(args, **kwargs):
        assert args[args.index("get") + 1 : args.index("get") + 3] == [
            "secret",
            "nsight-streamer-auth",
        ]
        if "--ignore-not-found=true" in args:
            return CompletedProcess(args, 0, "", "")
        return CompletedProcess(args, 1, "", "NotFound: sensitive-server-detail")

    with pytest.raises(RuntimeError) as caught:
        validate_login_secret(secret_kube(runner), viewer())
    assert "soperator/nsight-streamer-auth" in str(caught.value)
    assert "does not exist" in str(caught.value)
    assert "sensitive-server-detail" not in str(caught.value)


@pytest.mark.parametrize("keys", ["", "username\n", "password\n"])
def test_login_secret_requires_each_configured_nonempty_key(keys):
    kube = secret_kube(
        lambda args, **kwargs: CompletedProcess(args, 0, "nsight-streamer-auth\n" + keys, "")
    )
    missing = "password" if "username" in keys else "username"
    with pytest.raises(RuntimeError) as caught:
        validate_login_secret(kube, viewer())
    assert "soperator/nsight-streamer-auth" in str(caught.value)
    assert f"non-empty '{missing}' key" in str(caught.value)


@pytest.mark.parametrize("failure", ["forbidden", "timeout"])
def test_unreadable_login_secret_is_not_misreported_as_missing(failure):
    def runner(args, **kwargs):
        if failure == "timeout":
            raise TimeoutExpired(args, 60, output="sensitive-output", stderr="sensitive-error")
        return CompletedProcess(args, 1, "sensitive-output", "Forbidden: sensitive-error")

    with pytest.raises(RuntimeError) as caught:
        validate_login_secret(secret_kube(runner), viewer())
    message = str(caught.value)
    assert "Cannot read Nsight login Secret soperator/nsight-streamer-auth" in message
    assert "access" in message and "permissions" in message
    assert "does not exist" not in message and "sensitive" not in message


def test_login_secret_validates_custom_key_names():
    row = viewer()
    row["values"]["webUsername"]["secretKey"] = "login"
    row["values"]["webPassword"]["secretKey"] = "passphrase"
    kube = secret_kube(
        lambda args, **kwargs: CompletedProcess(args, 0, "nsight-streamer-auth\nlogin\n", "")
    )
    with pytest.raises(RuntimeError, match="non-empty 'passphrase' key"):
        validate_login_secret(kube, row)


def test_two_viewers_cannot_publish_conflicting_laptop_ports():
    first, second = viewer(), viewer("ncu")
    second["values"]["service"]["turnPort"] = first["values"]["service"]["httpPort"]
    with pytest.raises(ValueError, match="distinct laptop"):
        configured_viewers({"apps": {"charts": [first, second]}})


@pytest.mark.parametrize("drift", ["image", "mount", "credentials", "token"])
def test_live_viewer_drift_cannot_produce_success(drift):
    row = viewer()
    values = row["values"]
    container = {
        "name": "nsight-streamer-nsys",
        "image": VIEWER_IMAGES["nsys"],
        "volumeMounts": deepcopy(values["volumeMounts"]),
        "env": [
            {
                "name": name,
                "valueFrom": {
                    "secretKeyRef": {
                        "name": values[key]["secretName"],
                        "key": values[key]["secretKey"],
                    }
                },
            }
            for name, key in (("WEB_USERNAME", "webUsername"), ("WEB_PASSWORD", "webPassword"))
        ],
    }
    pod = {
        "automountServiceAccountToken": False,
        "containers": [container],
        "volumes": deepcopy(values["volumes"]),
    }
    deployment = {"spec": {"replicas": 1, "template": {"spec": pod}}}
    validate_viewer_deployment(deployment, row)
    if drift == "image":
        container["image"] = "unreviewed:latest"
    elif drift == "mount":
        container["volumeMounts"][0]["readOnly"] = False
    elif drift == "credentials":
        container["env"][0] = {"name": "WEB_USERNAME", "value": "default"}
    else:
        pod["automountServiceAccountToken"] = True
    with pytest.raises(RuntimeError, match="differs"):
        validate_viewer_deployment(deployment, row)


def test_invalid_persistent_kubeconfig_does_not_emit_temporary_access(tmp_path):
    path = tmp_path / "config"
    path.write_text("contexts: [broken")
    assert persistent_context("selected", kubernetes_uid="uid", candidates=(path,)) is None


@pytest.mark.parametrize("name", ["a..b", ".invalid", "invalid.", "Invalid", "a" * 64 + ".b"])
def test_kubernetes_name_rejects_invalid_dns_components(name):
    from nebius_cxcli.nsight import kubernetes_name

    with pytest.raises(ValueError):
        kubernetes_name(name, "Secret")


@pytest.mark.parametrize("name", ["a.b", "1numeric", "a" * 54, "invalid-"])
def test_release_names_follow_service_label_and_helm_length_constraints(name):
    from nebius_cxcli.nsight import release_name

    with pytest.raises(ValueError):
        release_name(name)


@pytest.mark.parametrize(
    "field,value",
    [
        ("resources", []),
        ("resources", {"limits": []}),
        ("resources", {"limits": {"cpu": -1}}),
        ("nodeSelector", {"gpu": True}),
        ("affinity", []),
        ("tolerations", ["invalid"]),
        ("imagePullSecrets", "secret"),
        ("enableResize", "true"),
        ("maxResolution", "0x1080"),
    ],
)
def test_viewer_values_reject_wrong_yaml_shapes(field, value):
    from nebius_cxcli.nsight import validate_viewer_values, viewer_values

    values = viewer_values("nsys", claim="reports", subpath="reports", secret="auth")
    values[field] = value
    with pytest.raises(ValueError):
        validate_viewer_values("nsight-streamer", values)
