#!/usr/bin/env python3
"""Read cluster or node-group metadata with project binding checks."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assets"))
from sdk.inventory import inspector_main

if __name__ == "__main__":
    raise SystemExit(inspector_main("kubernetes"))
