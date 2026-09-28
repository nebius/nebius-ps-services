"""Persistent database binding, admission and credential lifecycle contracts."""

from __future__ import annotations

import base64
import copy
import json
from types import SimpleNamespace

import pytest

from grafana_fakes import FakeGrafana, dashboard
from nebius_cxcli import grafana_database as database
from nebius_cxcli import grafana_database_runtime as runtime
from nebius_cxcli.component_sources import load_component_sources
from nebius_cxcli.grafana_import import execute_imports, prepare_imports
from nebius_cxcli.observability import _grafana_managed_values, _set_path_value


def database_config():
    charts = []
    for component in load_component_sources().helm_charts:
        if component.name not in {"postgresql", "grafana"}:
            continue
        row = {
            "id": component.name,
            "enabled": True,
            "instance_id": "cluster",
            "target_ref": "cluster",
            "namespace": "observability",
            "release-name": component.name,
            "values": {},
        }
        for default in component.defaults:
            _set_path_value(row, default.target_path, copy.deepcopy(default.value))
        charts.append(row)
    payload = {"client_info": {}, "apps": {"charts": charts}}
    database.materialize_database_values(payload)
    grafana = next(row for row in charts if row["id"] == "grafana")
    for path, value in _grafana_managed_values(payload, chart_row=grafana).items():
        _set_path_value(grafana, path, value)
    return payload


def test_generated_binding_and_standalone_database_are_idempotent():
    config = database_config()
    original = copy.deepcopy(config)
    assert not database.materialize_database_values(config)
    assert config == original
    pg = next(row for row in config["apps"]["charts"] if row["id"] == "postgresql")
    gf = next(row for row in config["apps"]["charts"] if row["id"] == "grafana")
    assert gf["values"]["replicas"] == 2
    assert gf["values"]["grafana.ini"]["database"]["host"] == "postgresql.observability.svc:5432"
    assert pg["values"]["auth"]["existingSecret"] != pg["values"]["customUser"]["existingSecret"]
    assert pg["values"]["persistence"]["size"] == "10Gi"
    assert "password" not in pg["values"]["auth"]
    config["apps"]["charts"].remove(gf)
    assert database.database_rows(config, "cluster") == [pg]


@pytest.mark.parametrize(
    "values",
    [
        {"persistence": {"existingClaim": "foreign"}},
        {"persistence": {"enabled": False}},
        {"replicaCount": 2},
        {"service": {"type": "LoadBalancer"}},
        {"persistentVolumeClaimRetentionPolicy": {"whenDeleted": "Delete"}},
    ],
)
def test_unsafe_database_storage_and_topology_are_rejected(values):
    with pytest.raises(ValueError, match="PostgreSQL"):
        database.validate_database_values(values)


def test_database_binding_is_target_local_and_rejects_ambiguity():
    config = database_config()
    gf = next(row for row in config["apps"]["charts"] if row["id"] == "grafana")
    other = copy.deepcopy(database.database_rows(config)[0])
    other["target_ref"] = "other"
    other["release-name"] = "other-db"
    config["apps"]["charts"].append(other)
    assert (
        "postgresql.observability"
        in database.grafana_database_values(config, gf)["values.grafana\\.ini.database"]["host"]
    )
    other["target_ref"] = "cluster"
    with pytest.raises(ValueError, match="exactly one"):
        database.grafana_database_values(config, gf)


@pytest.fixture
def cluster(monkeypatch):
    config = database_config()
    objects = {
        ("storageclass", "compute-csi-default-sc"): {"metadata": {"name": "compute-csi-default-sc"}}
    }
    writes = []
    owner = database.app_owner(config, "cluster")

    def read(kind, namespace, name, env, *, selector=""):
        if name:
            return copy.deepcopy(objects.get((kind, name), {}))
        items = objects.get((kind, selector), [])
        return {"items": copy.deepcopy(items)}

    def create(argv, **kwargs):
        doc = json.loads(kwargs["input"])
        name = doc["metadata"]["name"]
        writes.append(name)
        doc["data"] = {
            key: base64.b64encode(value.encode()).decode()
            for key, value in doc.pop("stringData").items()
        }
        objects[("secret", name)] = doc
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runtime, "_read", read)
    monkeypatch.setattr(runtime, "_ensure_namespace", lambda *a, **kw: None)
    monkeypatch.setattr(runtime.kubernetes_process, "run", create)
    return SimpleNamespace(config=config, objects=objects, writes=writes, owner=owner)


def bootstrap(cluster):
    runtime.ensure_database_runtime_secrets(cluster.config, target_ref="cluster", extra_env={})


def retained(cluster):
    cluster.objects[("persistentvolumeclaims", "")] = [
        {
            "metadata": {
                "name": "data-postgresql-0",
                "labels": {"app.kubernetes.io/instance": "postgresql"},
                "annotations": {database.OWNER_ANNOTATION: cluster.owner},
            },
        }
    ]


def test_fresh_bootstrap_then_reapply_reuses_all_credentials(cluster):
    bootstrap(cluster)
    assert set(cluster.writes) == {
        "postgresql-admin",
        "postgresql-grafana",
        "grafana-encryption",
        "nebius-cxcli-grafana-admin",
    }
    original = copy.deepcopy(cluster.objects)
    retained(cluster)
    bootstrap(cluster)
    assert len(cluster.writes) == 4
    for key, value in original.items():
        assert cluster.objects[key] == value


def test_standalone_database_bootstraps_without_grafana(cluster):
    cluster.config["apps"]["charts"] = database.database_rows(cluster.config)
    bootstrap(cluster)
    assert set(cluster.writes) == {"postgresql-admin", "postgresql-grafana"}


@pytest.mark.parametrize(
    "name",
    ["postgresql-admin", "postgresql-grafana", "grafana-encryption", "nebius-cxcli-grafana-admin"],
)
def test_retained_database_never_regenerates_missing_credentials(cluster, name):
    bootstrap(cluster)
    retained(cluster)
    del cluster.objects[("secret", name)]
    cluster.writes.clear()
    with pytest.raises(RuntimeError, match="refusing regeneration"):
        bootstrap(cluster)
    assert cluster.writes == []


@pytest.mark.parametrize("failure", ["partial", "foreign", "deleting"])
def test_whole_inventory_admission_precedes_first_write(cluster, failure):
    metadata = {"annotations": {database.OWNER_ANNOTATION: cluster.owner}}
    data = {"secret-key": base64.b64encode(b"fixture").decode()}
    if failure == "partial":
        data = {}
    if failure == "foreign":
        metadata["annotations"][database.OWNER_ANNOTATION] = "another-owner"
    if failure == "deleting":
        metadata["deletionTimestamp"] = "fixture"
    cluster.objects[("secret", "grafana-encryption")] = {"metadata": metadata, "data": data}
    with pytest.raises(RuntimeError, match="credential|ownership"):
        bootstrap(cluster)
    assert cluster.writes == []


def test_sqlite_backend_rejected_without_dashboard_declarations(cluster):
    cluster.objects[("deployments", "")] = [
        {
            "metadata": {"name": "grafana"},
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{"name": "grafana"}],
                        "volumes": [{"configMap": {"name": "grafana"}}],
                    }
                }
            },
        }
    ]
    cluster.objects[("configmap", "grafana")] = {
        "data": {"grafana.ini": "[database]\ntype=sqlite3\n"}
    }
    with pytest.raises(RuntimeError, match="no automatic migration"):
        bootstrap(cluster)
    assert not cluster.writes


def test_helm_history_without_workload_is_not_a_fresh_database(cluster):
    cluster.objects[("secrets", "owner=helm,name=postgresql")] = [{"metadata": {"name": "history"}}]
    with pytest.raises(RuntimeError, match="refusing regeneration"):
        bootstrap(cluster)
    assert not cluster.writes


def test_import_forces_editable_without_changing_input_or_export_normalization():
    client = FakeGrafana()
    source = {**dashboard(), "editable": False}
    execute_imports(client, prepare_imports(client, [source]))
    assert source["editable"] is False
    assert client.portable(client.get(source["uid"]))["editable"] is True
    resource = client.get(source["uid"])
    resource["spec"]["editable"] = False
    assert client.portable(resource)["editable"] is False


def test_unlabelled_retained_claim_is_still_detected(cluster):
    retained(cluster)
    cluster.objects[("persistentvolumeclaims", "")][0]["metadata"].pop("labels")
    with pytest.raises(RuntimeError, match="refusing regeneration"):
        bootstrap(cluster)
    assert not cluster.writes


def test_declarative_sqlite_release_without_pods_is_rejected(cluster):
    cluster.objects[("customresourcedefinition", "helmreleases.helm.toolkit.fluxcd.io")] = {
        "metadata": {"name": "fixture"}
    }
    cluster.objects[("helmreleases", "")] = [
        {
            "metadata": {
                "name": "grafana",
                "annotations": {database.OWNER_ANNOTATION: cluster.owner},
            },
            "spec": {"values": {}},
        }
    ]
    with pytest.raises(RuntimeError, match="no automatic migration"):
        bootstrap(cluster)
    assert not cluster.writes


def test_backend_admission_precedes_all_application_preparation(monkeypatch, tmp_path):
    from nebius_cxcli.app_mutation import app_mutation_scope
    from nebius_cxcli.deployment_target import prepare_application_runtime
    from nebius_cxcli.paths import resolve_project_paths

    calls = []

    def reject(*args, **kwargs):
        calls.append("admission")
        raise RuntimeError("fixture SQLite refusal")

    monkeypatch.setattr(runtime, "preflight_grafana_database", reject)
    # Any attempt to touch later CLI runtime hooks would fail with AttributeError.
    cli = SimpleNamespace(app_mutation_scope=app_mutation_scope)
    with pytest.raises(RuntimeError, match="SQLite refusal"):
        prepare_application_runtime(
            cli,
            database_config(),
            resolve_project_paths(tmp_path / "config.yaml"),
            target_ref="cluster",
            kube_env={},
            assert_authority=lambda: None,
            soperator_owned=False,
        )
    assert calls == ["admission"]


def test_acceptance_checks_every_grafana_replica(cluster):
    cluster.objects[("statefulset", "postgresql")] = {
        "metadata": {"generation": 2},
        "status": {"observedGeneration": 2, "readyReplicas": 1},
    }
    cluster.objects[("deployment", "grafana")] = {
        "metadata": {"generation": 2},
        "spec": {"replicas": 2},
        "status": {"observedGeneration": 2, "updatedReplicas": 2},
    }
    selector = "app.kubernetes.io/instance=grafana"
    ready = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
    cluster.objects[("pods", selector)] = [ready]
    with pytest.raises(RuntimeError, match="Every Grafana replica"):
        runtime.verify_database_runtime(cluster.config, target_ref="cluster", extra_env={})
    cluster.objects[("pods", selector)].append(copy.deepcopy(ready))
    runtime.verify_database_runtime(cluster.config, target_ref="cluster", extra_env={})


def test_missing_storage_class_precedes_secret_creation(cluster):
    cluster.objects.pop(("storageclass", "compute-csi-default-sc"))
    with pytest.raises(RuntimeError, match="StorageClass"):
        bootstrap(cluster)
    assert not cluster.writes


def live_grafana(cluster):
    bootstrap(cluster)
    retained(cluster)
    cluster.writes.clear()
    cluster.objects[("deployments", "")] = [
        {
            "metadata": {
                "name": "grafana",
                "annotations": {database.OWNER_ANNOTATION: cluster.owner},
            },
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "grafana",
                                "env": [
                                    {
                                        "name": "GF_DATABASE_PASSWORD",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "postgresql-grafana",
                                                "key": "password",
                                            }
                                        },
                                    },
                                    {
                                        "name": "GF_SECURITY_SECRET_KEY",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "grafana-encryption",
                                                "key": "secret-key",
                                            }
                                        },
                                    },
                                ],
                                "volumeMounts": [
                                    {
                                        "name": "config",
                                        "mountPath": "/etc/grafana/grafana.ini",
                                        "subPath": "grafana.ini",
                                    }
                                ],
                            }
                        ],
                        "volumes": [{"name": "config", "configMap": {"name": "grafana"}}],
                    }
                }
            },
        }
    ]
    cluster.objects[("configmap", "grafana")] = {
        "data": {
            "grafana.ini": "[database]\ntype=postgres\nhost=postgresql.observability.svc:5432\nname=grafana\nuser=grafana\nssl_mode=disable\n"
        }
    }
    return cluster.objects[("deployments", "")][0]["spec"]["template"]["spec"]


def test_live_environment_cannot_hide_a_different_database(cluster):
    pod = live_grafana(cluster)
    env = pod["containers"][0]["env"]
    env.insert(0, {"name": "GF_DATABASE_HOST", "value": "another-database"})
    with pytest.raises(RuntimeError, match="no automatic migration"):
        bootstrap(cluster)
    assert not cluster.writes
    env.pop(0)
    bootstrap(cluster)
    assert not cluster.writes


@pytest.mark.parametrize(
    "override",
    [
        {"env": {"GF_PATHS_CONFIG": "/usr/share/grafana/conf/defaults.ini"}},
        {"env": {"GF_PATHS_CONFIG__FILE": "/tmp/config-path"}},
        {
            "envValueFrom": {
                "GF_PATHS_CONFIG": {"configMapKeyRef": {"name": "alternate", "key": "path"}}
            }
        },
        {"command": ["grafana", "server"]},
        {"args": ["--config=/usr/share/grafana/conf/defaults.ini"]},
    ],
)
def test_configuration_startup_overrides_are_rejected(override):
    config = database_config()
    gf = next(row for row in config["apps"]["charts"] if row["id"] == "grafana")
    gf["values"].update(override)
    with pytest.raises(ValueError, match="configuration|startup"):
        database.grafana_database_values(config, gf)


@pytest.mark.parametrize(
    "override",
    [
        {"env": [{"name": "GF_PATHS_CONFIG", "value": "/usr/share/grafana/conf/defaults.ini"}]},
        {"env": [{"name": "GF_PATHS_CONFIG__FILE", "value": "/tmp/config-path"}]},
        {"command": ["grafana", "server"]},
        {"args": ["cfg:database.type=sqlite3"]},
    ],
)
def test_live_startup_overrides_fail_before_mutation(cluster, override):
    pod = live_grafana(cluster)
    container = pod["containers"][0]
    if "env" in override:
        container["env"].extend(override["env"])
    else:
        container.update(override)
    with pytest.raises(RuntimeError, match="no automatic migration"):
        bootstrap(cluster)
    assert not cluster.writes


@pytest.mark.parametrize("drift", ["unmounted", "different-volume", "different-key", "shadowed"])
def test_backend_inspects_only_the_consumed_configuration(cluster, drift):
    pod = live_grafana(cluster)
    mounts = pod["containers"][0]["volumeMounts"]
    if drift == "unmounted":
        mounts.clear()
    elif drift == "different-volume":
        mounts[0]["name"] = "other"
    elif drift == "different-key":
        pod["volumes"][0]["configMap"]["items"] = [{"key": "unused.ini", "path": "grafana.ini"}]
    else:
        mounts.append({"name": "other", "mountPath": "/etc/grafana"})
    with pytest.raises(RuntimeError, match="no automatic migration"):
        bootstrap(cluster)
    assert not cluster.writes


@pytest.mark.parametrize("drift", ["startup", "valuesFrom", "host", "password", "url"])
def test_declarative_startup_override_without_pods_is_rejected(cluster, drift):
    bootstrap(cluster)
    retained(cluster)
    cluster.writes.clear()
    cluster.objects[("customresourcedefinition", "helmreleases.helm.toolkit.fluxcd.io")] = {
        "metadata": {"name": "fixture"}
    }
    gf = next(row for row in cluster.config["apps"]["charts"] if row["id"] == "grafana")
    values = copy.deepcopy(gf["values"])
    if drift == "startup":
        values["args"] = ["cfg:database.type=sqlite3"]
    elif drift == "host":
        values["grafana.ini"]["database"]["host"] = "different-database:5432"
    elif drift == "password":
        values["envValueFrom"]["GF_DATABASE_PASSWORD"]["secretKeyRef"]["name"] = (
            "different-password"
        )
    elif drift == "url":
        values["grafana.ini"]["database"]["url"] = "sqlite3:///tmp/alternate.db"
    cluster.objects[("helmreleases", "")] = [
        {
            "metadata": {
                "name": "grafana",
                "annotations": {database.OWNER_ANNOTATION: cluster.owner},
            },
            "spec": {"values": values},
        }
    ]
    if drift == "valuesFrom":
        cluster.objects[("helmreleases", "")][0]["spec"]["valuesFrom"] = [
            {"kind": "ConfigMap", "name": "alternate"}
        ]
    with pytest.raises(RuntimeError, match="no automatic migration"):
        bootstrap(cluster)
    assert not cluster.writes
    cluster.objects[("helmreleases", "")][0]["spec"] = {"values": copy.deepcopy(gf["values"])}
    bootstrap(cluster)
    assert not cluster.writes


def test_runtime_uses_catalog_storage_default_when_row_omits_values(cluster):
    cluster.config["apps"]["charts"] = [
        {
            "id": "postgresql",
            "enabled": True,
            "instance_id": "cluster",
            "target_ref": "cluster",
            "namespace": "observability",
            "release-name": "postgresql",
            "values": {},
        }
    ]
    bootstrap(cluster)
    assert set(cluster.writes) == {"postgresql-admin", "postgresql-grafana"}


def failed_grafana(cluster):
    import gzip

    import yaml

    live_grafana(cluster)
    workload = cluster.objects[("deployments", "")].pop()
    workload.update(apiVersion="apps/v1", kind="Deployment")
    cm = copy.deepcopy(cluster.objects[("configmap", "grafana")])
    cm.update(apiVersion="v1", kind="ConfigMap")
    identity = {
        "name": "grafana",
        "namespace": "observability",
        "annotations": {database.OWNER_ANNOTATION: cluster.owner},
        "labels": {"app.kubernetes.io/instance": "grafana"},
    }
    workload["metadata"] = copy.deepcopy(identity)
    cm["metadata"] = copy.deepcopy(identity)
    values = copy.deepcopy(
        next(row for row in cluster.config["apps"]["charts"] if row["id"] == "grafana")["values"]
    )
    pod_ip = {"valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}}, "name": "POD_IP"}
    workload["spec"]["template"]["spec"]["containers"][0]["env"] += [
        copy.deepcopy(pod_ip),
        copy.deepcopy(pod_ip),
    ]
    values["envValueFrom"]["POD_IP"] = pod_ip["valueFrom"]
    hr = {
        "metadata": {**identity, "uid": "hr-uid", "generation": 1},
        "spec": {"values": values},
        "status": {
            "observedGeneration": 1,
            "lastAttemptedReleaseAction": "install",
            "lastAttemptedRevision": "13.2.5+fixture",
            "lastAttemptedConfigDigest": "sha256:fixture",
            "conditions": [
                {"type": kind, "status": truth, "reason": reason, "observedGeneration": 1}
                for kind, truth, reason in [
                    ("Stalled", "True", "RetriesExceeded"),
                    ("Ready", "False", "InstallFailed"),
                    ("Released", "False", "InstallFailed"),
                ]
            ],
            "history": [
                {
                    "name": "grafana",
                    "namespace": "observability",
                    "version": 1,
                    "status": "failed",
                    "action": "install",
                    "chartName": "grafana",
                    "chartVersion": "13.2.5+fixture",
                    "configDigest": "sha256:fixture",
                }
            ],
        },
    }
    stored = {
        "name": "grafana",
        "namespace": "observability",
        "version": 1,
        "info": {"status": "failed"},
        "chart": {"metadata": {"name": "grafana", "version": "13.2.5+fixture"}},
        "config": copy.deepcopy(values),
    }
    secret = {
        "type": "helm.sh/release.v1",
        "metadata": {
            "name": "sh.helm.release.v1.grafana.v1",
            "namespace": "observability",
            "uid": "storage-uid",
            "labels": {"owner": "helm", "name": "grafana", "version": "1", "status": "failed"},
        },
        "data": {},
    }
    cluster.objects[("customresourcedefinition", "helmreleases.helm.toolkit.fluxcd.io")] = {
        "metadata": {"name": "fixture"}
    }
    cluster.objects[("helmreleases", "")] = [hr]
    cluster.objects[("secrets", "owner=helm,name=grafana")] = [secret]

    def encode():
        stored["manifest"] = yaml.safe_dump_all([cm, workload])
        secret["data"]["release"] = base64.b64encode(
            base64.b64encode(gzip.compress(json.dumps(stored).encode()))
        ).decode()

    encode()
    return SimpleNamespace(
        hr=hr, stored=stored, secret=secret, workload=workload, cm=cm, encode=encode
    )


def test_proven_failed_first_postgres_install_reuses_all_credentials(cluster):
    failed_grafana(cluster)
    before = copy.deepcopy(cluster.objects)
    bootstrap(cluster)
    bootstrap(cluster)
    assert cluster.objects == before
    assert not cluster.writes


@pytest.mark.parametrize(
    "drift",
    [
        "stale",
        "stale-condition",
        "revision",
        "previous-success",
        "reconciling",
        "config-digest",
        "stored-config",
        "storage-owner",
        "workload-owner",
        "config-owner",
        "namespace",
        "sqlite",
        "consumed-config",
        "duplicate-secret",
        "missing-credential",
        "remaining-pod",
        "encoded-limit",
        "expanded-limit",
        "malformed-storage",
    ],
)
def test_failed_first_install_requires_complete_unambiguous_postgres_proof(cluster, drift):
    fixture = failed_grafana(cluster)
    if drift == "stale":
        fixture.hr["metadata"]["generation"] = 2
    elif drift == "stale-condition":
        fixture.hr["status"]["conditions"][0]["observedGeneration"] = 0
    elif drift == "revision":
        fixture.stored["version"] = 2
    elif drift == "previous-success":
        fixture.hr["status"]["history"].append({"version": 0, "status": "deployed"})
    elif drift == "reconciling":
        fixture.hr["status"]["conditions"].append({"type": "Reconciling", "status": "True"})
    elif drift == "config-digest":
        fixture.hr["status"]["lastAttemptedConfigDigest"] = "sha256:other"
    elif drift == "stored-config":
        fixture.stored["config"]["unexpected"] = True
    elif drift == "storage-owner":
        fixture.secret["metadata"]["labels"]["name"] = "foreign"
    elif drift in {"workload-owner", "config-owner"}:
        item = fixture.workload if drift == "workload-owner" else fixture.cm
        item["metadata"]["annotations"][database.OWNER_ANNOTATION] = "foreign"
    elif drift == "namespace":
        fixture.workload["metadata"]["namespace"] = "foreign"
    elif drift == "sqlite":
        fixture.cm["data"]["grafana.ini"] = "[database]\ntype=sqlite3\n"
    elif drift == "consumed-config":
        fixture.workload["spec"]["template"]["spec"]["volumes"][0]["configMap"]["name"] = (
            "unverified"
        )
    elif drift == "duplicate-secret":
        fixture.workload["spec"]["template"]["spec"]["containers"][0]["env"].insert(
            0, {"name": "GF_DATABASE_PASSWORD", "value": "secret-sentinel"}
        )
    elif drift == "missing-credential":
        del cluster.objects[("secret", "grafana-encryption")]
    elif drift == "remaining-pod":
        cluster.objects[("pods,replicasets", "app.kubernetes.io/instance=grafana")] = [
            {"metadata": {"name": "grafana-old"}}
        ]
    elif drift == "expanded-limit":
        fixture.stored["oversized"] = "x" * (8 * 1024 * 1024)
    fixture.encode()
    if drift == "encoded-limit":
        fixture.secret["data"]["release"] = "x" * (2 * 1024 * 1024 + 1)
    elif drift == "malformed-storage":
        fixture.secret["data"]["release"] = "secret-sentinel"
    with pytest.raises(RuntimeError, match="migration|regeneration") as error:
        bootstrap(cluster)
    assert "secret-sentinel" not in str(error.value)
    assert not cluster.writes
