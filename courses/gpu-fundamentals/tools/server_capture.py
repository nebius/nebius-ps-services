#!/usr/bin/env python3
"""Start/stop a bounded vLLM CUDA capture after the private server is ready."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "stop"))
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    tool = os.environ.get("COURSE_PROFILE_TOOL", "none")
    if tool == "none":
        return
    if tool != "nsys" or not 1 <= args.port <= 65535:
        parser.error("Serving captures require Systems and a loopback TCP port")
    url = f"http://127.0.0.1:{args.port}/{args.action}_profile"
    with urllib.request.urlopen(
        urllib.request.Request(url, data=b"", method="POST"), timeout=90
    ) as response:
        if response.status != 200:
            raise SystemExit("The server did not acknowledge capture control")
        response.read(65536)
    print(
        json.dumps(
            {
                "capture_action": args.action,
                "unix_seconds": time.time(),
                "acceptance_timing": False,
            }
        )
    )


if __name__ == "__main__":
    main()
