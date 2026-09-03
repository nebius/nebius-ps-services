#!/usr/bin/env python3
"""Advisory Agentic SDLC adapter for project-spec inspection."""

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


def validate(project_root: Path) -> dict[str, object]:
    """Return strict validation results as non-blocking workflow context."""

    try:
        result = validate_project(project_root)
    except ProjectSpecError as error:
        return {
            "status": "advisory",
            "code": error.code,
            "message": error.message,
        }
    return {**result, "status": "current"}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate canonical project specs.")
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args(argv)
    result = validate(args.project_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
