#!/usr/bin/env python3
"""Emit the authoritative shared project-spec receipt."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from project_specs_lib.contracts import ProjectSpecError, validate_project


def validate(project_root: Path) -> dict[str, object]:
    return validate_project(project_root)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate canonical project specs.")
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
