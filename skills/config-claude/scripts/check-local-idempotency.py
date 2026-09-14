#!/usr/bin/env python3
"""Read-only, redacted convergence checks for the requested Claude surfaces."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from claude_config import MCP_NAMES, SKILL, inspect, native_home


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--claude-home", type=Path, help="Absolute native home; defaults to CLAUDE_CONFIG_DIR or ~/.claude.")
    parser.add_argument("--source-root", type=Path, default=SKILL.parent, help="Reviewed complete skills catalog; read as data only.")
    parser.add_argument("--hook-route", choices=("local", "plugin"), default="local")
    parser.add_argument("--native-only", action="store_true", help="Inspect native files without claiming hook setup or dependencies.")
    parser.add_argument("--require-trusted-local", action="store_true")
    parser.add_argument("--require-delegation", action="store_true")
    parser.add_argument("--require-task-implementer-workspace", action="store_true")
    parser.add_argument("--require-mcp", action="append", choices=MCP_NAMES, default=[])
    parser.add_argument("--mcp-config", type=Path, help="Exact native application JSON for requested user-scope MCP checks; never printed.")
    args = parser.parse_args(argv)
    try:
        result = inspect(native_home(args.claude_home), args.source_root.absolute(),
                         route=args.hook_route, native_only=args.native_only,
                         trusted=args.require_trusted_local, delegation=args.require_delegation,
                         workspace=args.require_task_implementer_workspace,
                         mcp_names=tuple(args.require_mcp), mcp_config=args.mcp_config)
    except (OSError, ValueError):
        print('{"status":"NOT_ALIGNED","reason":"unsafe or invalid target"}')
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "STATIC_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
