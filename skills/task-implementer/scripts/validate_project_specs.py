#!/usr/bin/env python3
"""Validate Task Implementer project specs and emit the owner receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

SHARED_SCRIPTS = SCRIPT_DIR.parents[1] / "maintain-project-specs" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))

from project_specs_lib.contracts import ProjectSpecError, validate_project  # noqa: E402


def validate(project_root: Path) -> dict[str, object]:
    """Delegate Task Implementer validation to the shared authoritative owner."""

    return validate_project(project_root)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Task Implementer specs and emit a v2 private receipt."
    )
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = validate(args.project_root)
    except ProjectSpecError as error:
        print(
            json.dumps(
                {"status": "blocked", "code": error.code, "error": error.message},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
