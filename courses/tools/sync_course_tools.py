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
    "fabric_guard",
    "install_fabric_tools",
    "publish_results",
    "inspect_results",
    "managed_profilers",
    "regular-lab-setup",
    "cuda-lab-setup",
    "communication-lab-setup",
    "serving-lab-setup",
    "transformer-engine-lab-setup",
    "course_runtime",
    "verify_monitoring",
    "readiness",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    pairs = []
    for course in COURSES:
        shared = [
            ROOT / "tools/course_env.sh",
            ROOT / "tools/course_job_cache.sh",
            ROOT / "tools/ensure_python312.sh",
        ]
        shared += sorted(
            path
            for path in (ROOT / "tools/course_bootstrap").iterdir()
            if path.is_file()
        )
        pairs += [(path, ROOT / course / path.relative_to(ROOT)) for path in shared]
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
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
            destination.chmod(source.stat().st_mode & 0o777)

    from course_bootstrap.retirement import retire

    if not args.check:
        # Every replacement was written above; verify bytes before retirement.
        if any(
            destination.read_bytes() != source.read_bytes()
            for source, destination in pairs
        ):
            raise SystemExit("Standalone replacement verification failed")
        outcome = retire(ROOT, [".", *COURSES])
        for name in outcome["preserved_modified"]:
            print(f"Preserved modified old entrypoint: {name}")


if __name__ == "__main__":
    main()
