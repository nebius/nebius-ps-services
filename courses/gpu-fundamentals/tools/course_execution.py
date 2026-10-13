"""Pure selection contract shared by native jobs and preparation.

The batch file owns native commands. This module only selects declared stages;
it never installs dependencies, starts a workload or changes device choices.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

PROFILERS = ("none", "nsys", "ncu", "all")
EXPERIMENTS = ("default", "trials", "two-node", "aiperf", "trtllm", "sanitizer")
SANITIZERS = ("memcheck", "racecheck", "initcheck", "synccheck")


def capabilities(course: Path) -> dict:
    path = course / "reference/execution.json"
    if path.is_symlink():
        raise ValueError("Execution metadata must not be symlinked")
    value = json.loads(path.read_text())
    if (
        value.get("schema") != "course-execution/v1"
        or value.get("course") != course.name
    ):
        raise ValueError("Invalid course execution metadata")
    return value["labs"]


class SelectionParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse(arguments: list[str]) -> tuple[dict, list[str]]:
    parser = SelectionParser(
        description="Run one experiment or supported diagnostics in a fresh Slurm job.",
        epilog="Put program options after -- when they overlap launcher options.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--workload",
        choices=("small", "large"),
        default=os.environ.get("COURSE_WORKLOAD", "small"),
    )
    parser.add_argument("--profiler", choices=PROFILERS, default="none")
    parser.add_argument("--experiment", choices=EXPERIMENTS, default="default")
    parser.add_argument("--device", choices=("cpu", "cuda"))
    parser.add_argument("--sanitizer", choices=SANITIZERS)
    before, after = arguments, []
    if "--" in arguments:
        boundary = arguments.index("--")
        before, after = arguments[:boundary], arguments[boundary + 1 :]
    args, forwarded = parser.parse_known_args(before)
    if args.workload not in {"small", "large"}:
        parser.error("COURSE_WORKLOAD must be small or large")
    if any(item == "--profile" or item.startswith("--profile=") for item in forwarded):
        parser.error("use --workload small|large")
    # Recognized launcher selectors must be unique: argparse's last-value-wins
    # behavior would otherwise conceal contradictory device or experiment input.
    for name in ("workload", "profiler", "experiment", "device", "sanitizer"):
        if (
            sum(item == f"--{name}" or item.startswith(f"--{name}=") for item in before)
            > 1
        ):
            parser.error(f"--{name} may be supplied only once")
    return vars(args), [*forwarded, *after]


def option_values(arguments: list[str], name: str) -> list[str]:
    values = []
    for index, item in enumerate(arguments):
        if item == name:
            if index + 1 == len(arguments):
                raise ValueError(f"{name} requires a value")
            values.append(arguments[index + 1])
        elif item.startswith(name + "="):
            values.append(item.split("=", 1)[1])
    return values


def select(
    course: Path, lab: str, requested: dict, arguments: list[str] | None = None
) -> list[dict]:
    arguments = arguments or []
    labs = capabilities(course)
    if lab not in labs:
        raise ValueError(f"No executable lab named {lab}")
    spec = labs[lab]
    experiment = requested.get("experiment", "default")
    profiler = requested.get("profiler", "none")
    device = requested.get("device")
    if (
        experiment not in EXPERIMENTS
        or profiler not in PROFILERS
        or device not in (None, "cpu", "cuda")
    ):
        raise ValueError("Invalid experiment, profiler or device selection")
    program_devices = option_values(arguments, "--device")
    if len(set(program_devices)) > 1 or (
        device and any(item != device for item in program_devices)
    ):
        raise ValueError("Conflicting explicit device selections")
    if program_devices:
        device = program_devices[0]
    if device and not spec["device_option"]:
        raise ValueError("This lab does not accept an explicit --device")
    if device not in (None, "cpu", "cuda"):
        raise ValueError("--device must be cpu or cuda")
    variants = [row for row in spec["variants"] if row["experiment"] == experiment]
    if not variants:
        raise ValueError(f"Experiment {experiment} is unsupported for {lab}")
    sanitizer = requested.get("sanitizer")
    if experiment == "sanitizer":
        if sanitizer not in SANITIZERS or profiler != "none":
            raise ValueError(
                "Sanitizer requires --sanitizer and cannot be combined with Nsight"
            )
    elif sanitizer is not None:
        raise ValueError("--sanitizer requires --experiment sanitizer")
    stages = []
    for tool in ("nsys", "ncu") if profiler == "all" else (profiler,):
        candidates = [
            row
            for row in variants
            if row["profiler"] == tool and (not device or row["device"] == device)
        ]
        if len(candidates) > 1:
            candidates = [row for row in candidates if row.get("default_device")]
        if not candidates:
            reason = f"{tool} is unsupported for {lab}, experiment {experiment}" + (
                f", device {device}" if device else ""
            )
            if profiler != "all":
                raise ValueError(reason)
            stages.append({"id": tool, "status": "skipped", "reason": reason})
            continue
        if len(candidates) != 1:
            raise ValueError("Ambiguous execution capability")
        row = candidates[0]
        for flag, expected in row.get("fixed_options", {}).items():
            if any(value != str(expected) for value in option_values(arguments, flag)):
                raise ValueError(
                    f"{flag} conflicts with the selected native scope ({expected})"
                )
        stages.append(
            {**row, "id": "normal" if tool == "none" else tool, "status": "unstarted"}
        )
    return stages


def runtime_selections(course: Path, lab: str, **selection) -> list[dict]:
    return [row for row in select(course, lab, selection) if row["status"] != "skipped"]
