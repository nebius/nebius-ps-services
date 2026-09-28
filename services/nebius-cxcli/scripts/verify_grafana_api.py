#!/usr/bin/env python3
"""Qualify dashboard APIs against a disposable, loopback-only pinned Grafana."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

IMAGE = (
    "grafana/grafana:13.2.2@sha256:ac461fb352abc50da10a51c7d02462e9c05488f11f53f14b3ad79a8145f638a0"
)


def main() -> None:
    name = "cxcli-grafana-test-" + uuid.uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    with tempfile.TemporaryDirectory(prefix="cxcli-grafana-api-") as directory:
        root = Path(directory)
        provisioning = root / "provisioning" / "dashboards"
        dashboards = root / "dashboards"
        locked = root / "locked-dashboards"
        provisioning.mkdir(parents=True)
        dashboards.mkdir()
        locked.mkdir()
        (dashboards / "fixture.json").write_text(
            json.dumps(
                {
                    "uid": "cxcli-provisioned-fixture",
                    "title": "Provisioned fixture",
                    "schemaVersion": 42,
                    "panels": [],
                }
            )
        )
        (locked / "fixture.json").write_text(
            json.dumps(
                {
                    "uid": "cxcli-provisioned-locked",
                    "title": "Locked provisioned fixture",
                    "schemaVersion": 42,
                    "panels": [],
                }
            )
        )
        (provisioning / "fixture.yaml").write_text(
            "apiVersion: 1\nproviders:\n  - name: fixture\n    type: file\n"
            "    allowUiUpdates: true\n    updateIntervalSeconds: 11\n"
            "    options:\n      path: /fixture-dashboards\n"
            "  - name: locked\n    type: file\n    allowUiUpdates: false\n"
            "    options:\n      path: /locked-dashboards\n"
        )
        created = False
        try:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--rm",
                    "--name",
                    name,
                    "--label",
                    "nebius-cxcli.fixture=grafana-api",
                    "--publish",
                    "127.0.0.1::3000",
                    "--env-file",
                    "/dev/stdin",
                    "--volume",
                    f"{root / 'provisioning'}:/etc/grafana/provisioning:ro",
                    "--volume",
                    f"{dashboards}:/fixture-dashboards:ro",
                    "--volume",
                    f"{locked}:/locked-dashboards:ro",
                    IMAGE,
                ],
                input=f"GF_SECURITY_ADMIN_PASSWORD={password}\nGF_ANALYTICS_REPORTING_ENABLED=false\nGF_ANALYTICS_CHECK_FOR_UPDATES=false\n",
                text=True,
                stdout=subprocess.DEVNULL,
                check=True,
            )
            created = True
            endpoint = subprocess.check_output(
                ["docker", "port", name, "3000/tcp"], text=True
            ).strip()
            if not endpoint.startswith("127.0.0.1:"):
                raise RuntimeError("Disposable Grafana is not bound to loopback")
            url = f"http://{endpoint}/"
            deadline = time.monotonic() + 60
            while True:
                try:
                    with urllib.request.urlopen(url + "api/health", timeout=2) as response:
                        if json.load(response).get("database") == "ok":
                            break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("Disposable Grafana did not become ready")
                time.sleep(0.25)
            subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "tests/test_grafana_api_integration.py"],
                check=True,
                env={
                    **os.environ,
                    "CXCLI_GRAFANA_TEST_URL": url,
                    "CXCLI_GRAFANA_TEST_PASSWORD": password,
                    "CXCLI_GRAFANA_TEST_SOURCE": str(dashboards / "fixture.json"),
                },
            )
        finally:
            if created:
                subprocess.run(
                    ["docker", "stop", "--time", "5", name], stdout=subprocess.DEVNULL, check=True
                )


if __name__ == "__main__":
    main()
