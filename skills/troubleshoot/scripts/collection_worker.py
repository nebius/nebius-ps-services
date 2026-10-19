#!/usr/bin/env python3
"""Private collector worker; parent owns the hard wall-clock deadline."""

import json
import sys
from pathlib import Path

from collect_evidence import build_evidence

if __name__ == "__main__":
    try:
        print(
            json.dumps(
                build_evidence(
                    Path(sys.argv[1]), json.loads(sys.argv[2]), json.loads(sys.argv[3])
                )
            )
        )
    except (OSError, ValueError):
        raise SystemExit(2) from None
