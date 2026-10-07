"""Public preparation interfaces, sharing one controller and private state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from . import catalog, selection
from .support import monitoring, organize_history, prepare


def main(script, group):
    parser = argparse.ArgumentParser(
        description=f"Prepare {group} labs with Python 3.12; jobs never install dependencies."
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the selected dependency plan without writes or platform checks.",
    )
    if group == "regular":
        actions = parser.add_subparsers(dest="action")
        remote = actions.add_parser(
            "monitoring", help="Discover existing cxcli-managed monitoring."
        )
        for name in (
            "config",
            "target",
            "kubeconfig",
            "context",
            "output-dir",
            "workspace",
        ):
            remote.add_argument(
                f"--{name}",
                required=True,
                type=Path if name in {"config", "kubeconfig", "output-dir"} else str,
            )
        history = actions.add_parser(
            "organize-history",
            help="Copy attributed history, preserving all originals.",
        )
        history.add_argument("--course-root", required=True, type=Path)
    else:
        choices = parser.add_mutually_exclusive_group()
        choices.add_argument(
            "--lab", help="Lab ID, optionally qualified by course slug."
        )
        choices.add_argument(
            "--launcher", help="Launcher filename, including an optional variant."
        )
        choices.add_argument(
            "--all",
            action="store_true",
            help="Prepare every default lab in this group; exclude optional containers.",
        )
    args = parser.parse_args()
    try:
        if group == "regular" and args.action:
            if args.plan:
                parser.error(
                    "--plan describes dependency preparation, not a separate action"
                )
            if args.action == "monitoring":
                monitoring(args)
            else:
                result = organize_history(args.course_root)
                print(
                    f"Verified copies: {len(result['copied'])}; unresolved files left in place: {len(result['unresolved'])}. Originals preserved."
                )
            return 0
        root, courses = catalog.discover(script)
        definitions = catalog.load_catalog()
        plan = selection.resolve(
            group,
            courses,
            definitions,
            lab=getattr(args, "lab", None),
            launcher=getattr(args, "launcher", None),
        )
        if args.plan:
            print(json.dumps(plan, indent=2))
            return 0
        if group != "regular" and not (args.lab or args.launcher or args.all):
            print("Supported labs (no installation performed):")
            print("\n".join(plan["labs"]) or "None in this standalone course.")
            print(
                "Select --lab LAB, --launcher FILE.sbatch, or --all; inspect with --plan."
            )
            return 0
        if sys.version_info[:2] != (3, 12):
            raise RuntimeError("Run this command with python3.12")
        from .controller import setup

        setup(script, prepare, plan=plan)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        parser.exit(1, f"ERROR: {group} preparation failed: {exc}\n")
    return 0
