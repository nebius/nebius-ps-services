#!/usr/bin/env python3.12
"""Prepare cuda lab dependencies through the shared installation engine."""

import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).absolute().parent))
from course_bootstrap.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(__file__, "cuda"))
