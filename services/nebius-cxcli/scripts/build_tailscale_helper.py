#!/usr/bin/env python3
"""Build portable wheel assets from the pinned, checksum-locked HuJSON module."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def build(destination: Path, go: str = "go") -> None:
    source = Path(__file__).resolve().parents[1] / "src/nebius_cxcli/hujson_helper"
    destination.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(
        b"".join((source / name).read_bytes() for name in ("go.mod", "go.sum", "main.go"))
    ).hexdigest()
    environment = {**os.environ, "GOTOOLCHAIN": "local", "CGO_ENABLED": "0", "GOWORK": "off"}
    subprocess.run([go, "mod", "verify"], cwd=source, env=environment, check=True, timeout=60)
    subprocess.run(
        [go, "test", "-mod=readonly", "./..."], cwd=source, env=environment, check=True, timeout=180
    )
    hashes = {}
    for system in ("darwin", "linux"):
        for architecture in ("amd64", "arm64"):
            name = system + "-" + architecture
            path = destination / (name + ".bin")
            subprocess.run(
                [
                    go,
                    "build",
                    "-mod=readonly",
                    "-trimpath",
                    "-ldflags=-buildid=",
                    "-o",
                    str(path.resolve()),
                    ".",
                ],
                cwd=source,
                env={**environment, "GOOS": system, "GOARCH": architecture},
                check=True,
                timeout=180,
            )
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (destination / "manifest.json").write_text(
        json.dumps({"source_sha256": identity, "binaries": hashes}, sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--go", default="go")
    args = parser.parse_args()
    build(args.output, args.go)
