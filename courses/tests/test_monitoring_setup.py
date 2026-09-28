"""Discovery consumes cxcli artifacts without installing or mutating monitoring."""

import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml
from test_observability_integration import ROOT, load


def digest(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def owned_monitoring(tmp_path, monkeypatch, request):
    m = load("course_setup")
    custom = getattr(request, "param", False)
    project = tmp_path / "deployment space"
    generated = project / "generated"
    ordinary = generated / "flux/targets/cluster/ordinary"
    ordinary.mkdir(parents=True)
    (generated / "reports").mkdir()
    config = project / "config.yaml"
    config.write_text("deployment: course\n")
    kubeconfig = project / "kubeconfig"
    kubeconfig.write_text("fixture only")
    args = SimpleNamespace(
        config=config,
        kubeconfig=kubeconfig,
        context="course-context",
        target="cluster",
        workspace="learner-01",
        output_dir=project / "connections",
    )
    ns, name, port = (
        ("results", "custom-gateway", 9191)
        if custom
        else ("observability", "prometheus-pushgateway", 9091)
    )
    grafana_name = "custom-grafana" if custom else "grafana"
    rows = [
        {
            "id": "grafana",
            "enabled": True,
            "instance_id": "cluster",
            "namespace": "observability",
            "release-name": "grafana",
        },
        {
            "id": "prometheus-pushgateway",
            "enabled": True,
            "instance_id": "cluster",
            "namespace": "observability",
            "release-name": "batch" if custom else name,
        },
    ]
    datasource = {
        "name": "metrics-local",
        "type": "prometheus",
        "uid": "cxcli-fixture-metrics",
        "url": "http://vmsingle-native.monitoring.svc:8429",
    }
    ready = {
        "conditions": [{"type": "Ready", "status": "True", "observedGeneration": 1}]
    }

    def hr(row, values):
        return {
            "apiVersion": "helm.toolkit.fluxcd.io/v2",
            "kind": "HelmRelease",
            "metadata": {
                "name": row["release-name"],
                "namespace": row["namespace"],
                "annotations": {m.OWNER: "accepted-owner"},
                "generation": 1,
            },
            "spec": {
                "targetNamespace": row["namespace"],
                "releaseName": row["release-name"],
                "values": values,
            },
            "status": copy.deepcopy(ready),
        }

    grafana = hr(
        rows[0],
        {
            "datasources": {"datasources.yaml": {"datasources": [datasource]}},
            "fullnameOverride": grafana_name,
        },
    )
    gateway = hr(
        rows[1],
        {"namespaceOverride": ns, "fullnameOverride": name, "service": {"port": port}},
    )
    graph = {
        "schema": "nebius-cxcli.soperator-release-graph.v2",
        "release": "4.1.8",
        "releases": [
            {
                "upstreamReleaseName": "soperator-fluxcd-vm-stack",
                "owner": "third-party",
                "sourceKind": "HelmChart",
                "sourceName": "metrics-chart",
                "namespace": "flux-system",
                "releaseName": "metrics-release",
                "revision": "sha256:pinned",
            }
        ],
    }
    cm = {
        "kind": "ConfigMap",
        "metadata": {
            "name": "nebius-cxcli-soperator-release-graph",
            "namespace": "flux-system",
            "labels": {"soperator.nebius.ai/release-graph": "graph-owner"},
        },
        "data": {"graph.json": json.dumps(graph)},
    }
    chart = {
        "kind": "HelmChart",
        "metadata": {
            "name": "metrics-chart",
            "namespace": "flux-system",
            "annotations": {"soperator.nebius.ai/package-sha256": "sha256:pinned"},
        },
        "spec": {"chart": "victoria-metrics-k8s-stack"},
    }
    native_hr = {
        "metadata": {
            "name": "metrics-release",
            "namespace": "flux-system",
            "generation": 1,
            "labels": cm["metadata"]["labels"],
        },
        "spec": {
            "targetNamespace": "monitoring",
            "releaseName": "native",
            "chartRef": {
                "kind": "HelmChart",
                "name": "metrics-chart",
                "namespace": "flux-system",
            },
        },
        "status": ready,
    }

    def service(name, namespace, port, uid, row=None, owner=None):
        metadata = {"name": name, "namespace": namespace, "uid": uid}
        if row:
            metadata.update(
                annotations={
                    "meta.helm.sh/release-name": row["release-name"],
                    "meta.helm.sh/release-namespace": row["namespace"],
                },
                labels={"app.kubernetes.io/managed-by": "Helm"},
            )
        if owner:
            metadata["ownerReferences"] = [{"uid": owner}]
        return {
            "metadata": metadata,
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": name},
                "ports": [{"name": "http", "port": port}],
                "clusterIPs": ["10.3.0.4"],
            },
        }

    push_service = service(name, ns, port, "push-service", row=rows[1])
    grafana_service = service(
        grafana_name, "observability", 80, "graf-service", row=rows[0]
    )
    metrics = service(
        "vmsingle-native", "monitoring", 8429, "metrics-service", owner="native-db"
    )
    single = {
        "metadata": {
            "name": "native-db",
            "namespace": "monitoring",
            "uid": "native-db",
            "labels": {"app.kubernetes.io/instance": "native"},
        }
    }
    agent = {
        "metadata": {
            "name": "native-agent",
            "namespace": "monitoring",
            "uid": "native-agent",
            "labels": {"app.kubernetes.io/instance": "native"},
        },
        "spec": {
            "remoteWrite": [{"url": datasource["url"] + "/api/v1/write"}],
            "inlineScrapeConfig": yaml.safe_dump(
                [
                    {
                        "job_name": "cxcli-pushgateway",
                        "scrape_interval": "5s",
                        "honor_labels": True,
                        "static_configs": [{"targets": [f"{name}.{ns}.svc:{port}"]}],
                    }
                ]
            ),
        },
    }
    manifest = {
        "schema": "nebius-cxcli-generated/v2",
        "render": {
            "source_config_sha256": digest(
                json.dumps(
                    yaml.safe_load(config.read_text()),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
        },
        "deploy": {
            "targets": [{"target_ref": "cluster", "flux_dir": str(ordinary.parent)}]
        },
        "runtime_config": {"apps": {"charts": rows}},
    }
    baseline = {
        "schema": "nebius-cxcli-ordinary-apps/v1",
        "deployment_generation": "accepted-generation",
        "identities": {
            "cluster": {
                "kubernetes_uid": "cluster-uid",
                "cluster_id": "fixture-cluster",
            }
        },
    }

    def save():
        files = {
            "flux/targets/cluster/protected.yaml": [cm, chart],
            "flux/targets/cluster/ordinary/apps.yaml": [grafana, gateway],
        }
        for relative, documents in files.items():
            (generated / relative).write_text(yaml.safe_dump_all(documents))
        baseline["ordinary_files"] = {
            name: digest((generated / name).read_bytes())
            for name in files
            if "/ordinary/" in name
        }
        baseline["protected_files"] = {
            name: digest((generated / name).read_bytes())
            for name in files
            if "/ordinary/" not in name
        }
        (generated / "nebius-cxcli-manifest.json").write_text(json.dumps(manifest))
        (generated / "reports/ordinary-apps-baseline.json").write_text(
            json.dumps(baseline)
        )

    save()
    live_grafana, live_gateway = copy.deepcopy(grafana), copy.deepcopy(gateway)
    state = SimpleNamespace(
        manifest=manifest,
        baseline=baseline,
        generated=generated,
        grafana=grafana,
        gateway=gateway,
        live_grafana=live_grafana,
        live_gateway=live_gateway,
        push=push_service,
        grafana_service=grafana_service,
        metrics=metrics,
        agent=agent,
        single=single,
        save=save,
        calls=[],
        cluster_uid="cluster-uid",
        ready=True,
    )

    def kube(connection, *argv):
        assert connection == (kubeconfig.resolve(), "course-context")
        assert argv[0] == "get"
        state.calls.append(argv)
        kind = argv[1]
        namespace = argv[argv.index("-n") + 1] if "-n" in argv else None
        if kind == "namespace":
            return {"metadata": {"uid": state.cluster_uid}}
        if kind == "helmrelease":
            return {
                "grafana": live_grafana,
                rows[1]["release-name"]: live_gateway,
                "metrics-release": native_hr,
            }[argv[2]]
        if kind == "configmap":
            return cm
        if kind == "services":
            return {
                "items": [
                    s
                    for s in (push_service, grafana_service, metrics)
                    if s["metadata"]["namespace"] == namespace
                ]
            }
        if kind == "endpointslices":
            name = argv[-1].split("=", 1)[1]
            svc = next(
                s
                for s in (push_service, grafana_service, metrics)
                if s["metadata"]["name"] == name
            )
            return {
                "items": [
                    {
                        "metadata": {
                            "ownerReferences": [{"uid": svc["metadata"]["uid"]}]
                        },
                        "ports": [
                            {
                                "name": "http",
                                "port": 9091
                                if svc is push_service
                                else svc["spec"]["ports"][0]["port"],
                            }
                        ],
                        "endpoints": [
                            {
                                "conditions": {"ready": state.ready},
                                "addresses": ["10.2.0.7"],
                            }
                        ],
                    }
                ]
            }
        if kind == "vmagents.operator.victoriametrics.com":
            return {"items": [agent]}
        if kind == "vmsingles.operator.victoriametrics.com":
            return {"items": [single]}
        raise AssertionError(argv)

    monkeypatch.setattr(m, "kube", kube)
    return m, args, state


@pytest.mark.parametrize("owned_monitoring", [False, True], indirect=True)
def test_discovery_writes_only_private_connections(owned_monitoring):
    m, args, state = owned_monitoring
    config_before = args.config.read_bytes()
    discovered = m.discover(args)
    m.write_connections(args, discovered)
    assert sorted(p.name for p in args.output_dir.iterdir()) == [
        "environment.sh",
        "laptop-environment.sh",
        "monitoring.json",
    ]
    assert args.output_dir.stat().st_mode & 0o777 == 0o700
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in args.output_dir.iterdir())
    assert discovered["schema"] == "gpu-course-monitoring/v2"
    assert discovered["datasource_uid"] == "cxcli-fixture-metrics"
    assert discovered["pushgateway_service"] == state.push["metadata"]["name"]
    assert discovered["pushgateway_port"] == state.push["spec"]["ports"][0]["port"]
    assert args.config.read_bytes() == config_before
    assert all(c[0] == "get" for c in state.calls)
    worker = (args.output_dir / "environment.sh").read_text()
    assert "KUBECONFIG" not in worker and "CLUSTER_CONFIG" not in worker
    laptop = args.output_dir / "laptop-environment.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s\\n%s" "$KUBECONFIG" "$COURSE_PUSHGATEWAY"; type -t nebius-cxcli || true',
            "bash",
            str(laptop),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.startswith(
        str(args.kubeconfig.resolve()) + "\n" + discovered["pushgateway_url"]
    )
    assert "function" not in result.stdout
    assert "COURSE_CATALOG" not in laptop.read_text()
    with pytest.raises(ValueError, match="already exists"):
        m.write_connections(args, discovered)


@pytest.mark.parametrize(
    "fault",
    [
        "cluster",
        "config",
        "schema",
        "baseline",
        "path",
        "artifact",
        "missing",
        "ambiguous",
        "owner",
        "drift",
        "unready",
        "public",
        "endpoint",
        "datasource",
        "scrape",
        "replicas",
        "write",
        "service-owner",
    ],
)
def test_invalid_installation_fails_before_outputs(owned_monitoring, fault):
    m, args, s = owned_monitoring
    if fault == "cluster":
        s.cluster_uid = "other"
    elif fault == "config":
        args.config.write_text("deployment: changed")
    elif fault == "schema":
        s.manifest["schema"] = "unknown"
        s.save()
    elif fault == "baseline":
        s.baseline["deployment_generation"] = ""
        s.save()
    elif fault == "path":
        s.manifest["deploy"]["targets"][0]["flux_dir"] = "/tmp/foreign"
        s.save()
    elif fault == "artifact":
        (s.generated / "flux/targets/cluster/ordinary/apps.yaml").write_text("changed")
    elif fault == "missing":
        s.manifest["runtime_config"]["apps"]["charts"].pop()
        s.save()
    elif fault == "ambiguous":
        s.manifest["runtime_config"]["apps"]["charts"].append(
            s.manifest["runtime_config"]["apps"]["charts"][0]
        )
        s.save()
    elif fault == "owner":
        s.live_gateway["metadata"]["annotations"][m.OWNER] = "other"
    elif fault == "drift":
        s.live_grafana["spec"]["values"]["unexpected"] = True
    elif fault == "unready":
        s.live_gateway["status"]["conditions"][0]["observedGeneration"] = 0
    elif fault == "public":
        s.push["spec"]["type"] = "LoadBalancer"
    elif fault == "endpoint":
        s.ready = False
    elif fault == "datasource":
        s.grafana["spec"]["values"]["datasources"]["datasources.yaml"]["datasources"][
            0
        ]["name"] = "metrics-remote"
        s.live_grafana["spec"] = copy.deepcopy(s.grafana["spec"])
        s.save()
    elif fault == "scrape":
        s.agent["spec"]["inlineScrapeConfig"] = "[]"
    elif fault == "replicas":
        s.agent["spec"]["replicaCount"] = 2
    elif fault == "write":
        s.agent["spec"]["remoteWrite"] = []
    else:
        s.push["metadata"]["annotations"]["meta.helm.sh/release-name"] = "foreign"
    with pytest.raises(ValueError):
        m.write_connections(args, m.discover(args))
    assert not args.output_dir.exists()


def test_config_digest_is_semantic_and_manifest_paths_are_repo_relative(
    owned_monitoring,
):
    m, args, s = owned_monitoring
    args.config.write_text("# formatting only\ndeployment: 'course'\n")
    s.manifest["deploy"]["targets"][0]["flux_dir"] = (
        "deployment space/generated/flux/targets/cluster"
    )
    s.save()
    assert m.discover(args)["target"] == "cluster"


@pytest.mark.parametrize(
    "fault", ["down", "stale", "duplicate", "pool", "destination", "owner"]
)
def test_actual_native_scrape_rejects_misleading_states(
    monkeypatch, owned_monitoring, fault
):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    m = load("verify_monitoring")
    setup, args, s = owned_monitoring
    receipt = setup.discover(args)
    target = {
        "scrapeUrl": receipt["pushgateway_url"] + "/metrics",
        "_course_agent_uid": receipt["vmagent_uid"],
        "scrapePool": "cxcli-pushgateway",
        "health": "up",
        "lastError": "",
        "lastScrape": datetime.fromtimestamp(1000, timezone.utc).isoformat(),
    }
    m.verify_targets([target], {"10.2.0.7:9091"}, 1005, receipt=receipt, service=s.push)
    rows = [target]
    if fault == "down":
        target["health"] = "down"
    elif fault == "stale":
        target["lastScrape"] = datetime.fromtimestamp(900, timezone.utc).isoformat()
    elif fault == "duplicate":
        rows.append(copy.deepcopy(target))
        rows[-1]["scrapePool"] = "another-route"
    elif fault == "destination":
        target["scrapeUrl"] = "http://foreign.observability.svc:9091/metrics"
    elif fault == "owner":
        target["_course_agent_uid"] = "other-agent"
    else:
        target["scrapePool"] = "another-pool"
    with pytest.raises(ValueError):
        m.verify_targets(rows, {"10.2.0.7:9091"}, 1005, receipt=receipt, service=s.push)


def test_kubectl_always_uses_explicit_connection(monkeypatch):
    m = load("course_setup")
    calls = []
    monkeypatch.setattr(
        m.subprocess,
        "check_output",
        lambda argv, **kw: calls.append(argv) or '{"items": []}',
    )
    m.kube(
        ("/tmp/selected config", "selected"), "get", "services", "-n", "observability"
    )
    assert calls == [
        [
            "kubectl",
            "--kubeconfig",
            "/tmp/selected config",
            "--context",
            "selected",
            "get",
            "services",
            "-n",
            "observability",
            "-o",
            "json",
        ]
    ]


@pytest.mark.parametrize("flag", ["--source-catalog", "--pushgateway-image"])
def test_removed_installer_flags_are_rejected(flag):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/course_setup.py"),
            "--config",
            "config.yaml",
            "--target",
            "cluster",
            "--kubeconfig",
            "kubeconfig",
            "--context",
            "selected",
            "--workspace",
            "student",
            "--output-dir",
            "out",
            flag,
            "old",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and "unrecognized arguments" in result.stderr


@pytest.mark.parametrize("fault", ["service", "database", "owner", "port", "exposure"])
def test_backend_replacement_is_rejected(monkeypatch, fault):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    m = load("verify_monitoring")
    receipt = {
        "vmsingle_uid": "db-one",
        "service_uid": "svc-one",
        "metrics_service": "metrics",
        "metrics_port": 8429,
    }
    singles = [{"metadata": {"uid": "db-one"}}]
    service = {
        "metadata": {
            "name": "metrics",
            "uid": "svc-one",
            "ownerReferences": [{"uid": "db-one"}],
        },
        "spec": {"ports": [{"name": "http", "port": 8429}]},
    }
    m.verify_backend(receipt, [service], singles)
    if fault == "service":
        service["metadata"]["uid"] = "new-service"
    elif fault == "database":
        singles[0]["metadata"]["uid"] = "new-db"
    elif fault == "owner":
        service["metadata"]["ownerReferences"] = []
    elif fault == "port":
        service["spec"]["ports"][0]["port"] = 1234
    else:
        service["spec"]["type"] = "LoadBalancer"
    with pytest.raises(ValueError):
        m.verify_backend(receipt, [service], singles)


def test_legacy_receipt_is_rejected_before_kubernetes(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    m = load("verify_monitoring")
    receipt = tmp_path / "monitoring.json"
    receipt.write_text(json.dumps({"schema": "gpu-course-monitoring/v1"}))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_monitoring.py",
            "--receipt",
            str(receipt),
            "--kubeconfig",
            "selected",
            "--context",
            "selected",
        ],
    )
    monkeypatch.setattr(
        m, "kube", lambda *args: pytest.fail("Old receipt triggered cluster access")
    )
    with pytest.raises(SystemExit) as error:
        m.main()
    assert error.value.code == 2


def test_output_symlink_is_rejected(owned_monitoring, tmp_path):
    m, args, _state = owned_monitoring
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    args.output_dir.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="unsafe"):
        m.write_connections(args, m.discover(args))
    assert list(elsewhere.iterdir()) == []


def test_native_write_is_rechecked_after_discovery(owned_monitoring):
    m, args, s = owned_monitoring
    receipt = m.discover(args)
    s.agent["spec"]["remoteWrite"] = [
        {"url": "http://foreign.monitoring.svc:8429/api/v1/write"}
    ]
    with pytest.raises(ValueError, match="local VMSingle"):
        m.verify_metrics_write(s.agent, s.metrics, receipt["metrics_port"])


@pytest.mark.parametrize("fault", ["suspended", "terminating", "remote-kubeconfig"])
def test_inactive_or_remotely_bound_releases_are_rejected(owned_monitoring, fault):
    m, args, s = owned_monitoring
    if fault == "suspended":
        s.gateway["spec"]["suspend"] = True
        s.live_gateway["spec"] = copy.deepcopy(s.gateway["spec"])
        s.save()
    elif fault == "terminating":
        s.live_gateway["metadata"]["deletionTimestamp"] = "2026-01-01T00:00:00Z"
    else:
        s.gateway["spec"]["kubeConfig"] = {"secretRef": {"name": "another-cluster"}}
        s.live_gateway["spec"] = copy.deepcopy(s.gateway["spec"])
        s.save()
    with pytest.raises(ValueError):
        m.discover(args)
