#!/usr/bin/env python3
"""Internal synchronization implementation. Use sync-labs.sh for its public CLI."""

from pathlib import Path
import subprocess
import sys

from course_bootstrap.sync_workstation import main

if __name__ == "__main__":
    try:
        main(Path(__file__).resolve().parents[1], sys.argv[1:])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        # Do not echo subprocess argv, YAML payloads or private endpoint responses.
        detail = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        sys.exit(f"ERROR: Synchronization handoff failed: {detail}")
