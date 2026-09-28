"""Pinned upstream charts, generated values and the actual SQL readiness patch."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from nebius_cxcli.grafana_database import database_readiness_patch
from nebius_cxcli.helm_client import render_chart_template_documents
from test_grafana_database import database_config


@pytest.mark.skipif(
    not os.environ.get("CXCLI_DATABASE_CHARTS"), reason="Run scripts/verify_grafana_postgres.py"
)
@pytest.mark.parametrize("grafana_name", ["grafana", "monitoring"])
def test_pinned_charts_render_generated_binding_and_authenticated_readiness(
    tmp_path, grafana_name, monkeypatch
):
    root = Path(os.environ["CXCLI_DATABASE_CHARTS"])
    from nebius_cxcli.grafana_database import grafana_database_values
    from nebius_cxcli.observability import _set_path_value

    payload = database_config()
    grafana = next(row for row in payload["apps"]["charts"] if row["id"] == "grafana")
    grafana["release-name"] = grafana_name
    grafana["values"].pop("fullnameOverride", None)
    for path, value in grafana_database_values(payload, grafana).items():
        _set_path_value(grafana, path, value)
    for row in payload["apps"]["charts"]:
        chart = "postgres" if row["id"] == "postgresql" else "grafana"
        docs = render_chart_template_documents(
            chart_name=str(root / chart),
            chart_repo="",
            chart_version="",
            release_name=row["release-name"],
            namespace=row["namespace"],
            values=row["values"],
        )
        assert not any(doc["kind"] == "Secret" for doc in docs)
        if chart == "postgres":
            (tmp_path / "rendered.yaml").write_text(yaml.safe_dump_all(docs))
            (tmp_path / "kustomization.yaml").write_text(
                yaml.safe_dump(
                    {
                        "apiVersion": "kustomize.config.k8s.io/v1beta1",
                        "kind": "Kustomization",
                        "resources": ["rendered.yaml"],
                        "patches": [database_readiness_patch("postgresql")],
                    }
                )
            )
            patched = subprocess.run(
                ["kubectl", "kustomize", str(tmp_path)], capture_output=True, text=True, check=True
            )
            docs = list(yaml.safe_load_all(patched.stdout))
            workload = next(doc for doc in docs if doc["kind"] == "StatefulSet")
            assert workload["spec"]["persistentVolumeClaimRetentionPolicy"] == {
                "whenDeleted": "Retain",
                "whenScaled": "Retain",
            }
            claim = workload["spec"]["volumeClaimTemplates"][0]
            assert claim["spec"]["storageClassName"] == "compute-csi-default-sc"
            assert claim["spec"]["resources"]["requests"]["storage"] == "10Gi"
            probe = workload["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["exec"][
                "command"
            ]
            assert "SELECT 1" in probe[-1] and "127.0.0.1" in probe[-1]
            assert any(doc["kind"] == "NetworkPolicy" for doc in docs)
        else:
            workload = next(doc for doc in docs if doc["kind"] == "Deployment")
            assert workload["spec"]["replicas"] == 2
            assert workload["metadata"]["name"] == grafana_name
            assert any(doc["kind"] == "PodDisruptionBudget" for doc in docs)
            assert any(
                doc["kind"] == "Service" and doc["spec"].get("clusterIP") == "None" for doc in docs
            )
            containers = workload["spec"]["template"]["spec"]["containers"]
            container = next(item for item in containers if item["name"] == "grafana")
            assert container["image"] == (
                "docker.io/grafana/grafana:13.2.2@sha256:"
                "ac461fb352abc50da10a51c7d02462e9c05488f11f53f14b3ad79a8145f638a0"
            )
            variables = {item["name"]: item for item in container["env"]}
            assert len(variables) == len(container["env"]), "duplicate container environment names"
            assert variables["POD_IP"]["valueFrom"] == {"fieldRef": {"fieldPath": "status.podIP"}}
            assert (
                variables["GF_DATABASE_PASSWORD"]["valueFrom"]["secretKeyRef"]["name"]
                == "postgresql-grafana"
            )
            assert (
                variables["GF_SECURITY_SECRET_KEY"]["valueFrom"]["secretKeyRef"]["name"]
                == f"{grafana_name}-encryption"
            )
            assert container["readinessProbe"]["httpGet"]["path"] == "/api/health"
            config = next(
                doc
                for doc in docs
                if doc["kind"] == "ConfigMap" and "grafana.ini" in doc.get("data", {})
            )
            assert "type = postgres" in config["data"]["grafana.ini"]
            assert "postgresql.observability.svc:5432" in config["data"]["grafana.ini"]
            from nebius_cxcli import grafana_database_runtime as runtime

            monkeypatch.setattr(runtime, "_read", lambda *_args, result=config, **_kwargs: result)
            assert (
                runtime._database_backend(
                    workload,
                    row["namespace"],
                    {},
                    config_map_name=grafana_name,
                    password_secret="postgresql-grafana",
                    encryption_secret=f"{grafana_name}-encryption",
                )
                == row["values"]["grafana.ini"]["database"]
            )
