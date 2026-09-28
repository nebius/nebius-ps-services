#!/usr/bin/env python3
"""Submit a known course lab with private, per-lab Slurm logs."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


def private_directory(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Result directories must not be symlinks")
        current.mkdir(mode=0o700, exist_ok=True)
        if current.stat().st_uid != os.getuid() or current.stat().st_mode & 0o077:
            raise ValueError(
                f"Use a learner-owned private directory (mode 700): {current}"
            )
    return current


def submission(root: Path, lab: str, arguments: list[str]) -> list[str]:
    root = root.resolve()
    metadata = json.loads((root / "reference/course.json").read_text())
    sources = {Path(row["path"]).stem: row["path"] for row in metadata["labs"]}
    if lab not in sources:
        raise ValueError("--lab must name an executable lab in this course")
    options = []
    while arguments and arguments[0].startswith("--"):
        option = arguments.pop(0)
        if option not in ("--wait", "--parsable") and not re.fullmatch(
            r"--(?:job-name|comment|export|partition|account|reservation|time|nodes|gpus-per-node|cpus-per-task)=[^\n\r]+",
            option,
        ):
            raise ValueError(
                "Use supported --name=value Slurm options; log paths are course-owned"
            )
        options.append(option)
    if not arguments:
        raise ValueError("Provide a slurm/*.sbatch launcher and its workload arguments")
    launcher = root / arguments.pop(0)
    if (
        launcher.is_symlink()
        or launcher.resolve().parent != root / "slurm"
        or launcher.suffix != ".sbatch"
        or not launcher.is_file()
    ):
        raise ValueError(
            "Launcher must be a regular slurm/*.sbatch file in this course"
        )
    for argument in arguments:
        if (
            re.fullmatch(r"labs/\d+_[\w]+\.(?:py|cu)", argument)
            and argument != sources[lab]
        ):
            raise ValueError("The workload source and --lab identity differ")
    logs = private_directory(root, Path("results") / lab / "logs")
    return [
        "sbatch",
        f"--chdir={root}",
        f"--output={logs}/%j.out",
        f"--error={logs}/%j.err",
        *options,
        str(launcher),
        *arguments,
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lab", required=True, help="Lab source stem, for example 01_cpu_gpu_crossover"
    )
    args, rest = parser.parse_known_args()
    root = Path(__file__).resolve().parents[1]
    os.umask(0o077)
    try:
        command = submission(root, args.lab, rest)
        if "--parsable" not in command:
            print(
                f"Private logs: {root / 'results' / args.lab / 'logs'} (<job>.out and <job>.err)",
                flush=True,
            )
        subprocess.run(command, check=True)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Submission failed: {exc}") from exc


if __name__ == "__main__":
    main()
