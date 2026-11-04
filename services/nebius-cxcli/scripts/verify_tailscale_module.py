#!/usr/bin/env python3
"""Validate/test the Tailscale module in an isolated directory without a backend."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[3] / "platform-infra/modules"
    with tempfile.TemporaryDirectory(prefix="cxcli-tailscale-module-") as temporary:
        staging = Path(temporary)
        for name in ("tailscale-gw", "vm"):
            shutil.copytree(
                root / name,
                staging / name,
                ignore=shutil.ignore_patterns(
                    ".terraform", "*.tfstate*", ".terraform.lock.hcl", "__pycache__"
                ),
            )
        for arguments in (
            ["init", "-backend=false", "-input=false", "-no-color"],
            ["validate", "-no-color"],
            ["test", "-no-color"],
        ):
            subprocess.run(
                ["terraform", *arguments], cwd=staging / "tailscale-gw", check=True, timeout=180
            )


if __name__ == "__main__":
    main()
