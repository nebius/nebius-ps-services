#!/usr/bin/env python3
"""Qualify immutable PostgreSQL/Grafana charts with generated product values."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

CHARTS = (
    (
        "oci://registry-1.docker.io/cloudpirates/postgres",
        "0.20.6",
        "sha256:e7f6474f0892d1c24191fc57e21289af0a0c338a9ada5c971bf1350adec0d797",
    ),
    (
        "oci://ghcr.io/grafana-community/helm-charts/grafana",
        "13.2.5",
        "sha256:0fcb82fdbf9409a24fb0f9b4c20875a2d0fa668d4f3ad71f1717845ba9bb99ca",
    ),
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="cxcli-grafana-postgres-charts-") as directory:
        root = Path(directory)
        for source, version, digest in CHARTS:
            result = subprocess.run(
                ["helm", "pull", source, "--version", version, "--untar", "--untardir", str(root)],
                capture_output=True,
                text=True,
                check=True,
            )
            if f"Digest: {digest}" not in result.stderr + result.stdout:
                raise RuntimeError("Pinned chart digest differs from reviewed upstream artifact")
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "no:cacheprovider",
                "-o",
                "addopts=",
                "-q",
                "tests/test_grafana_postgres_chart_integration.py",
            ],
            env={**os.environ, "CXCLI_DATABASE_CHARTS": str(root)},
            check=True,
        )


if __name__ == "__main__":
    main()
