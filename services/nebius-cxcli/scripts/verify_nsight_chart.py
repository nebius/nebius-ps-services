#!/usr/bin/env python3
"""Download the reviewed official archive, verify SHA256, render and apply real patches."""

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from nebius_cxcli.nsight import CHART_VERSION

URL = "https://helm.ngc.nvidia.com/nvidia/devtools/charts/nsight-streamer-2026.4.1.tgz"
# Official repository index.yaml, retrieved 2026-09-18. This pins archive bytes,
# independently of Helm's version label and the pinned runtime image digests.
SHA256 = "e9fdee592f5bfbd443b6bab4295aef8197fa840f85442c17ef29524d512ffe82"


def main():
    if CHART_VERSION != "2026.4.1":
        raise RuntimeError("Nsight chart provenance must be reviewed when its version changes")
    for tool in ("helm", "kubectl"):
        if not shutil.which(tool):
            raise RuntimeError(f"Required native renderer unavailable: {tool}")
    with tempfile.TemporaryDirectory(prefix="cxcli-nsight-chart-") as directory:
        root = Path(directory)
        archive = root / "chart.tgz"
        with urllib.request.urlopen(URL, timeout=60) as response:
            archive.write_bytes(response.read())
        if hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError("Official Nsight chart archive differs from reviewed SHA256")
        with tarfile.open(archive) as stream:
            stream.extractall(root, filter="data")
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
                "tests/test_nsight_chart_integration.py",
            ],
            check=True,
            env={
                **os.environ,
                "NSIGHT_REAL_CHART": str(root / "nsight-streamer"),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        print(f"Verified {URL} sha256:{SHA256}; both tools rendered with production patches.")


if __name__ == "__main__":
    main()
