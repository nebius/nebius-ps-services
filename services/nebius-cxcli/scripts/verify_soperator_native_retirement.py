#!/usr/bin/env python3
"""Run native retirement qualification in an owned disposable kind cluster."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import urllib.request
import uuid
from pathlib import Path

import yaml

CONTROLLERS = {"helm-controller": "v1.5.0", "source-controller": "v1.8.0"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", default="kind", help="Path to the kind executable")
    args = parser.parse_args()
    name = "cxcli-retirement-" + uuid.uuid4().hex[:12]
    project = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="cxcli-retirement-fixture-") as directory:
        root = Path(directory)
        kubeconfig = root / "kubeconfig"
        env = {**os.environ, "KUBECONFIG": str(kubeconfig)}
        try:
            subprocess.run(
                [
                    args.kind,
                    "create",
                    "cluster",
                    "--name",
                    name,
                    "--image",
                    "kindest/node:v1.37.0",
                    "--kubeconfig",
                    str(kubeconfig),
                    "--wait",
                    "120s",
                ],
                check=True,
                timeout=300,
            )
            documents = [
                {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "flux-system"}}
            ]
            for controller, version in CONTROLLERS.items():
                for suffix in ("crds", "deployment"):
                    url = f"https://github.com/fluxcd/{controller}/releases/download/{version}/{controller}.{suffix}.yaml"
                    with urllib.request.urlopen(url, timeout=60) as response:
                        selected = list(yaml.safe_load_all(response.read()))
                    for document in selected:
                        if document["kind"] in {"Deployment", "Service"}:
                            document["metadata"]["namespace"] = "flux-system"
                        if document["kind"] == "Deployment":
                            document["spec"]["template"]["spec"]["serviceAccountName"] = controller
                        documents.append(document)
                documents.append(
                    {
                        "apiVersion": "v1",
                        "kind": "ServiceAccount",
                        "metadata": {"name": controller, "namespace": "flux-system"},
                    }
                )
            for kind in ("controller", "reconciler"):
                url = f"https://raw.githubusercontent.com/fluxcd/flux2/v2.6.4/manifests/rbac/{kind}.yaml"
                with urllib.request.urlopen(url, timeout=60) as response:
                    documents.extend(yaml.safe_load_all(response.read()))
            subprocess.run(
                ["kubectl", "apply", "-f", "-"],
                env=env,
                input=yaml.safe_dump_all(documents),
                text=True,
                check=True,
                timeout=120,
                stdout=subprocess.DEVNULL,
            )
            subprocess.run(
                [
                    "kubectl",
                    "-n",
                    "flux-system",
                    "wait",
                    "deployment/helm-controller",
                    "deployment/source-controller",
                    "--for=condition=Available",
                    "--timeout=120s",
                ],
                env=env,
                check=True,
                timeout=150,
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_soperator_native_retirement_integration.py",
                ],
                cwd=project,
                env={**env, "CXCLI_RETIREMENT_KUBECONFIG": str(kubeconfig)},
                check=True,
                timeout=600,
            )
        finally:
            subprocess.run(
                [args.kind, "delete", "cluster", "--name", name], check=True, timeout=120
            )


if __name__ == "__main__":
    main()
