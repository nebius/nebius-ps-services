#!/usr/bin/env python3
"""Run a complete, deterministic class partition of unittest discovery."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import sys
import time
import unittest


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def partition(tests, count):
    """Balance class sizes, retaining discovery order and fixture boundaries."""
    if count < 1:
        raise ValueError("shard count must be positive")
    groups = defaultdict(list)
    ids = [test.id() for test in tests]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("discovery must contain nonempty, unique test IDs")
    for test in tests:
        groups[test.id().rsplit(".", 1)[0]].append(test)
    if len(groups) < count:
        raise ValueError("shard count exceeds discovered test classes")
    sizes = [0] * count
    assigned = {}
    for name in sorted(groups, key=lambda name: (-len(groups[name]), name)):
        lane = min(range(count), key=lambda index: (sizes[index], index))
        assigned[name] = lane
        sizes[lane] += len(groups[name])
    shards = [[] for _ in range(count)]
    for test in tests:
        shards[assigned[test.id().rsplit(".", 1)[0]]].append(test)
    return shards


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-directory", required=True)
    parser.add_argument("--pattern", default="test_*.py")
    parser.add_argument("--shard-index", required=True, type=int)
    parser.add_argument("--shard-count", required=True, type=int)
    parser.add_argument(
        "--list", action="store_true", help="List selected IDs without running"
    )
    args = parser.parse_args(argv)
    if not 0 <= args.shard_index < args.shard_count:
        parser.error("shard index must be in [0, shard count)")
    loader = unittest.TestLoader()
    suite = loader.discover(args.start_directory, pattern=args.pattern)
    # A broken import must fail every shard, including those that would not
    # otherwise receive unittest's synthetic _FailedTest case.
    if loader.errors:
        for error in loader.errors:
            print(error, file=sys.stderr)
        return 1
    tests = list(cases(suite))
    try:
        selected = partition(tests, args.shard_count)[args.shard_index]
    except ValueError as error:
        parser.error(str(error))
    inventory = {
        "total": len(tests),
        "selected": len(selected),
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "tests": [test.id() for test in selected],
    }
    if args.list:
        print(json.dumps(inventory, sort_keys=True))
        return 0
    print(
        f"Shard {args.shard_index + 1}/{args.shard_count}: "
        f"{len(selected)}/{len(tests)} discovered tests",
        flush=True,
    )
    started = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    print(f"Shard completed in {time.monotonic() - started:.1f}s", flush=True)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
