"""Opt-in official-chart check; run scripts/verify_nsight_chart.py for acquisition."""

import base64
import os
import subprocess

import pytest
import yaml

from nebius_cxcli import cli, nsight_install
from nebius_cxcli.helm_client import render_chart_template_documents
from nebius_cxcli.nsight import VIEWER_IMAGES
from nebius_cxcli.nsight_access import _secret_ref, _service_port
from nebius_cxcli.nsight_profiling import default_settings
from test_nsight_install import installed_project  # noqa: F401


@pytest.mark.skipif(
    not os.environ.get("NSIGHT_REAL_CHART"),
    reason="official chart acquisition is an explicit integration lane",
)
def test_official_chart_accepts_production_values_and_postrenderers(installed_project, tmp_path):  # noqa: F811
    local, initial, *_ = installed_project
    generation = nsight_install.prepare_generation(
        cli, local, initial, target_ref="cluster", settings=default_settings()
    )
    releases = []
    for name, encoded in generation.files.items():
        if "/ordinary/" in name:
            releases += [
                doc
                for doc in yaml.safe_load_all(base64.b64decode(encoded))
                if isinstance(doc, dict) and doc.get("kind") == "HelmRelease"
            ]
    assert len(releases) == 2
    for release in releases:
        spec = release["spec"]
        release_name = spec.get("releaseName", release["metadata"]["name"])
        assert all(
            spec[action]["disableHooks"] is True for action in ("install", "upgrade", "uninstall")
        )
        docs = render_chart_template_documents(
            chart_name=os.environ["NSIGHT_REAL_CHART"],
            chart_repo="",
            chart_version="",
            release_name=release_name,
            namespace="soperator",
            values=spec["values"],
        )
        root = tmp_path / release_name
        root.mkdir()
        (root / "rendered.yaml").write_text(yaml.safe_dump_all(docs))
        (root / "kustomization.yaml").write_text(
            yaml.safe_dump(
                {
                    "apiVersion": "kustomize.config.k8s.io/v1beta1",
                    "kind": "Kustomization",
                    "resources": ["rendered.yaml"],
                    "patches": spec["postRenderers"][0]["kustomize"]["patches"],
                }
            )
        )
        rendered = subprocess.run(
            ["kubectl", "kustomize", str(root)], capture_output=True, text=True, check=True
        ).stdout
        patched = list(yaml.safe_load_all(rendered))
        deployment = next(d for d in patched if d["kind"] == "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        assert pod["automountServiceAccountToken"] is False
        assert container["image"] == VIEWER_IMAGES[spec["values"]["tool"]]
        assert len(pod["initContainers"]) == 1
        assert any(
            m["mountPath"] == "/mnt/reports" and m["readOnly"] and m["subPath"] == "nsight-reports"
            for m in container["volumeMounts"]
        )
        assert all(m["mountPath"] != "/mnt/jail" for m in container["volumeMounts"])
        assert not next(d for d in patched if d["kind"] == "Role")["rules"]
        service = next(d for d in patched if d["kind"] == "Service")
        assert service["spec"]["type"] == "ClusterIP"
        assert service["spec"]["selector"]["release"] == release_name
        assert service["spec"]["selector"]["app"] == f"nsight-streamer-{spec['values']['tool']}"
        assert all(
            deployment["spec"]["template"]["metadata"]["labels"].get(key) == value
            for key, value in service["spec"]["selector"].items()
        )
        # Access discovery must understand the pinned chart's actual port/env
        # shape, including unnamed container ports and omitted targetPort.
        for role in ("http", "turn"):
            assert (
                _service_port(service, container, role) == spec["values"]["service"][f"{role}Port"]
            )
        assert _secret_ref(container, "WEB_PASSWORD") == ("nsight-streamer-auth", "password")
        assert _secret_ref(container, "WEB_USERNAME") == ("nsight-streamer-auth", "username")
        assert {p["port"] for p in service["spec"]["ports"]} == {
            spec["values"]["service"]["httpPort"],
            spec["values"]["service"]["turnPort"],
        }
        for key in ("WEB_USERNAME", "WEB_PASSWORD"):
            assert any(
                e["name"] == key
                and e.get("valueFrom", {}).get("secretKeyRef", {}).get("name")
                == "nsight-streamer-auth"
                for e in container["env"]
            )
