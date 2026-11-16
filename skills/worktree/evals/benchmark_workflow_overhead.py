#!/usr/bin/env python3
"""Compare frozen local workflow helpers; never assert native/workflow speedup.

The parent randomizes seven paired trials. Each child imports one catalog,
prepares identical synthetic inputs outside timing, warms up once, then times
unprofiled calls. Output equality is mandatory. Output contains no local paths.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import tempfile
import time

CASES = {"projection-small": 40, "projection-history": 3,
         "inventory-single": 40, "inventory-repeated": 10,
         "status-planned": 3, "status-blocked-worker": 3}
SOURCES = ("worktree/scripts/workflow_statistics_report.py",
           "worktree/scripts/workflow_statistics_inventory.py",
           "worktree/scripts/workflow_statistics.py")
IGNORED = {".git", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".ruff_cache"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def manifest(root):
    files = {}
    for parent, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED)
        for name in dirs + names:
            path = Path(parent) / name
            if path.is_symlink():
                raise ValueError("benchmark catalog must not contain symlinks")
            if path.is_file():
                files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def fixture(root, case, temporary, cleanup):
    sys.path.insert(0, str(root / "worktree/scripts"))
    sys.path.insert(0, str(root / "maintain-project-specs/scripts"))
    if case.startswith("status"):
        from test_workflow_statistics import OwnerTests
        from workflow_statistics import record, status
        owner = OwnerTests()
        owner.setUp()
        cleanup.callback(owner.tearDown)
        record(owner.run_dir, "plan_locked", initial=True)
        if case == "status-blocked-worker":
            first, _ = owner.result_fixture()
            owner.result_ingest(first, "blocked")
        def observe():
            report = status(owner.run_dir)
            if report.get("status") == "unavailable":
                raise ValueError("status fixture unavailable")
            return report
        return observe
    if case.startswith("projection"):
        from test_workflow_statistics import event, stamp, task
        from workflow_statistics_report import project
        count, size = (8, 40) if case == "projection-small" else (64, 1000)
        rows = [task(f"TASK-{index:03d}") for index in range(count)]
        events = []
        for index in range(size):
            slot = (index // 2) % count
            start = index % 2 == 0
            old = rows[slot]
            rows[slot] = {**old, "status": "running" if start else "done",
                          "attempt": old["attempt"] + int(start),
                          "worker_started_at": str(index) if start else old["worker_started_at"]}
            action = "recover_task" if start and old["attempt"] else "start_task" if start else "finish_task"
            events.append(event(action, index // 4, [dict(row) for row in rows]))
            events[-1]["state"]["objective"]["sha256"] = str(index // 100)
        return lambda: project(events, events[-1]["state"], stamp(size))
    from workflow_statistics_inventory import validate
    files, references = (1, 1) if case == "inventory-single" else (8, 200)
    sources = []
    for index in range(files):
        data = (f"Component {index}\n" * 128).encode()
        path = temporary / f"source-{index}.md"
        path.write_bytes(data)
        sources.append({"kind": "project", "path": path.name, "anchor": "Component",
                        "sha256": hashlib.sha256(data).hexdigest()})
    # Each fact has at most 16 sources, preserving the production schema bound.
    layers = [{"id": f"LAYER-{index}", "name": f"Layer {index}", "origin": "observed",
               "sources": [dict(sources[position % files]) for position in range(index, min(index + 16, references))]}
              for index in range(0, references, 16)]
    value = {"schema": "workflow/architecture-inventory-v1", "layers": layers, "components": [], "gaps": []}
    return lambda: validate(value, temporary, temporary / "run")


def worker(root, case):
    def comparable(value):
        if case.startswith("status"):
            # Live clocks and disposable Git/run identities necessarily differ.
            # Compare declared semantic summary fields; pure projection cases
            # above compare their complete output with fixed clocks/identities.
            return {key: value[key] for key in ("owner", "task_counts", "logical_task_count",
                    "retired_task_counts", "delivery", "measurement_gaps")}
        return value
    with ExitStack() as cleanup, tempfile.TemporaryDirectory() as temporary:
        call = fixture(root, case, Path(temporary).resolve(), cleanup)
        expected = digest(comparable(call()))  # untimed warmup/equivalence output
        start = time.perf_counter_ns()
        for _ in range(CASES[case]):
            result = call()
        elapsed = time.perf_counter_ns() - start
        if digest(comparable(result)) != expected:
            raise ValueError("helper output changed during measurement")
        return {"ns_per_call": elapsed / CASES[case], "output_sha256": expected}


def compare(baseline, candidate):
    roots = {"baseline": baseline, "candidate": candidate}
    fingerprints = {name: {relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
                           for relative in SOURCES} for name, root in roots.items()}
    catalogs = {name: manifest(root) for name, root in roots.items()}
    harness = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    randomizer = random.Random(821)
    result = {"scope": "local deterministic helper performance; not native or whole-workflow qualification",
              "python": sys.version.split()[0], "seed": 821, "pairs": 7,
              "warmup_calls_per_process": 1, "source_sha256": fingerprints, "cases": {}}
    result.update(catalogs=catalogs, harness_sha256=harness,
                  status_comparison="semantic summary fields; clocks and disposable identities excluded")
    for case, iterations in CASES.items():
        pairs = []
        output = None
        for _ in range(7):
            order = list(roots)
            randomizer.shuffle(order)
            pair = {"order": order}
            for version in order:
                completed = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()),
                                            "--worker", str(roots[version]), "--case", case],
                                           check=True, capture_output=True, text=True, timeout=120)
                measurement = json.loads(completed.stdout)
                if output is not None and output != measurement["output_sha256"]:
                    raise ValueError("baseline/candidate outputs differ")
                output = measurement["output_sha256"]
                pair[version] = measurement["ns_per_call"]
            pairs.append(pair)
        medians = {version: statistics.median(row[version] for row in pairs) for version in roots}
        result["cases"][case] = {
            "iterations": iterations, "output_sha256": output, "trials": pairs,
            "median_ns": medians,
            "range_ns": {version: [min(row[version] for row in pairs), max(row[version] for row in pairs)] for version in roots},
            "paired_median_difference_ns": statistics.median(row["candidate"] - row["baseline"] for row in pairs),
            "candidate_faster_pairs": sum(row["candidate"] < row["baseline"] for row in pairs),
        }
    for version, root in roots.items():
        if catalogs[version] != manifest(root):
            raise ValueError("source changed during measurement")
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != harness:
        raise ValueError("harness changed during measurement")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--case", choices=CASES, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(args.worker.resolve(), args.case)))
        return
    if not all((args.baseline, args.candidate, args.output)):
        parser.error("--baseline, --candidate and --output are required")
    result = compare(args.baseline.resolve(), args.candidate.resolve())
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"cases": {name: value["median_ns"] for name, value in result["cases"].items()}}))


if __name__ == "__main__":
    main()
