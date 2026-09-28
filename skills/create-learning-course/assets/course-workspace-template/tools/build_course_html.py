#!/usr/bin/env python3
"""Build configured course publications, or check freshness without writing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from publication import check_budget, contained_path, write_atomic


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compare all planned outputs without writing",
    )
    args = parser.parse_args()
    try:
        import course_adapter as adapter

        project = Path(__file__).resolve().parents[1]
        root = adapter.PUBLICATION_ROOT
        outputs = adapter.plan_outputs(project)
        if not isinstance(outputs, dict) or not outputs:
            raise ValueError("The renderer must return a nonempty output mapping")
        report = check_budget(
            root,
            outputs,
            max_file_bytes=adapter.MAX_FILE_BYTES,
            max_site_bytes=adapter.MAX_SITE_BYTES,
            inventory=adapter.INVENTORY,
        )
        print("Publication estimate: " + json.dumps(report, sort_keys=True), flush=True)
        # Complete preflight precedes any replacement; atomicity is per file.
        for name, content in outputs.items():
            destination = contained_path(root, name)
            if destination.exists() and not destination.is_file():
                raise ValueError(f"Output destination is not a regular file: {name}")
        for name, content in outputs.items():
            destination = contained_path(root, name)
            if args.check:
                if not destination.is_file() or destination.read_bytes() != content:
                    raise ValueError(f"Stale or missing generated output: {name}")
                print(f"current {name}", flush=True)
            else:
                write_atomic(destination, content)
                print(f"built {name}", flush=True)
    except (OSError, ValueError, ImportError, AttributeError) as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
