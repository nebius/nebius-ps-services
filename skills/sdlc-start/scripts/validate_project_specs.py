#!/usr/bin/env python3
"""Agentic SDLC adapter for the shared project-spec validator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


SCRIPT_DIR = Path(__file__).resolve().parent
SHARED_SCRIPTS = SCRIPT_DIR.parents[1] / "maintain-project-specs" / "scripts"
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))

from project_specs_lib.contracts import ProjectSpecError, validate_project  # noqa: E402


SpecValidationError = ProjectSpecError


def validate(project_root: Path) -> dict[str, object]:
    """Delegate Agentic SDLC validation to the shared authoritative owner."""

    return validate_project(project_root)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate canonical project specs.")
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = validate(args.project_root)
    except ProjectSpecError as error:
        print(
            json.dumps(
                {"status": "blocked", "code": error.code, "error": error.message},
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
