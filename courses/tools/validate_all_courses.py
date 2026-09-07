#!/usr/bin/env python3
"""Run every standalone course validator with the active Python."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COURSES = (
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
)


def main() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "build_course_html.py"), "--check"],
        cwd=ROOT,
        check=True,
    )
    for course in COURSES:
        validator = ROOT / course / "tools" / "validate_course.py"
        subprocess.run([sys.executable, str(validator)], cwd=ROOT / course, check=True)
    print("PASS: all five course validators")


if __name__ == "__main__":
    main()
