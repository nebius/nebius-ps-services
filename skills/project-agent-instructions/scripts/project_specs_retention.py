#!/usr/bin/env python3
"""Internal ownership-retention classifier for project-spec maintenance."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from project_agent_instructions_lib.workflow import retention_disposition


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--session-root", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    args = parser.parse_args(argv)
    disposition, generation, registry_sha256 = retention_disposition(
        args.session_root, args.workspace_root
    )
    print(
        json.dumps(
            {
                "disposition": disposition,
                "registry_generation": generation,
                "registry_sha256": registry_sha256,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
