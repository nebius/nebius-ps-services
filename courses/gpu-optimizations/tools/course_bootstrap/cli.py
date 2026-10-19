"""Public preparation interfaces, sharing one controller and private state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import catalog, selection
from .support import organize_history, prepare


def main(script):
    parser = argparse.ArgumentParser(
        description=(
            "Prepare all available lab variants, profiling paths and optional container exercises with Python 3.12; initialize automatic publication "
            "when private monitoring settings are installed; jobs never install dependencies."
        )
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the selected dependency plan without writes or platform checks.",
    )
    parser.add_argument(
        "--exclude",
        choices=("containers",),
        help="Skip container images and dependent runtimes; preserve existing images and receipts.",
    )
    actions = parser.add_subparsers(dest="action")
    history = actions.add_parser(
        "organize-history",
        help="Copy attributed history, preserving all originals.",
    )
    history.add_argument("--course-root", required=True, type=Path)
    choices = parser.add_mutually_exclusive_group()
    choices.add_argument("--lab", help="Lab ID, optionally qualified by course slug.")
    choices.add_argument(
        "--launcher",
        help="Canonical launcher filename; use selection flags for optional stages.",
    )
    parser.add_argument(
        "--experiment",
        choices=("default", "trials", "two-node", "aiperf", "trtllm", "sanitizer"),
    )
    parser.add_argument("--profiler", choices=("none", "nsys", "ncu", "all"))
    parser.add_argument("--device", choices=("cpu", "cuda"))
    parser.add_argument(
        "--sanitizer", choices=("memcheck", "racecheck", "initcheck", "synccheck")
    )
    args = parser.parse_args()
    try:
        if args.action:
            if args.plan:
                parser.error(
                    "--plan describes dependency preparation, not a separate action"
                )
            if any(
                getattr(args, name) is not None
                for name in (
                    "lab",
                    "launcher",
                    "experiment",
                    "profiler",
                    "device",
                    "sanitizer",
                    "exclude",
                )
            ):
                parser.error("Selection flags apply to dependency preparation only")
            result = organize_history(args.course_root)
            print(
                f"Verified copies: {len(result['copied'])}; unresolved files left in place: {len(result['unresolved'])}. Originals preserved."
            )
            return 0

        def resolve():
            _, courses = catalog.discover(script)
            return selection.resolve(
                courses,
                catalog.load_catalog(),
                lab=getattr(args, "lab", None),
                launcher=getattr(args, "launcher", None),
                experiment=args.experiment,
                profiler=args.profiler,
                device=args.device,
                sanitizer=args.sanitizer,
                exclude=args.exclude,
            )

        if args.plan:
            print(json.dumps(resolve(), indent=2))
            return 0
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError("Run this command with python3.12")
        from .controller import setup
        from .progress import Progress
        from .worker import Cancellation

        with Cancellation() as cancellation, Progress() as progress:
            progress.update("discovering available labs")
            plan = resolve()
            cancellation.check()
            setup(
                script, prepare, plan=plan, progress=progress, cancellation=cancellation
            )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        parser.exit(1, f"ERROR: lab preparation failed: {exc}\n")
    return 0
