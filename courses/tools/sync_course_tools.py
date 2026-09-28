#!/usr/bin/env python3
"""Maintain identical standalone copies of the canonical shared course helpers."""

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COURSES = (
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
    "advanced-gpu-communication",
)
TOOLS = (
    "submit_lab",
    "fabric_guard",
    "install_fabric_tools",
    "publish_results",
    "inspect_results",
    "profile_lab",
    "managed_profilers",
    "course_setup",
    "verify_monitoring",
    "readiness",
    "server_capture",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    pairs = []
    for course in COURSES:
        pairs += [
            (ROOT / "tools" / f"{tool}.py", ROOT / course / "tools" / f"{tool}.py")
            for tool in TOOLS
        ]
        pairs += [
            (
                ROOT / "tools/course_evidence.py",
                ROOT / course / "labs/course_evidence.py",
            ),
            (
                ROOT / "tools/validate_course_template.py",
                ROOT / course / "tools/validate_course.py",
            ),
        ]
    pairs.append(
        (
            ROOT / "tools/cuda_result.py",
            ROOT / "custom-cuda-kernels/tools/cuda_result.py",
        )
    )
    for source, destination in pairs:
        content = source.read_bytes()
        if args.check:
            if not destination.is_file() or destination.read_bytes() != content:
                raise SystemExit(
                    f"Stale standalone helper: {destination.relative_to(ROOT)}"
                )
        else:
            destination.write_bytes(content)
            destination.chmod(source.stat().st_mode & 0o777)


if __name__ == "__main__":
    main()
