"""Frozen fixture assertions for early task trials; independent of product TDD."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def load_product():
    path = Path(__file__).resolve().parents[1] / "tasks/domain.py"
    spec = importlib.util.spec_from_file_location("fixture_product_domain", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cases(group):
    if group == "title":
        return ["title.normal", "title.empty", "title.whitespace"]
    if group == "filter":
        return ["filter.active", "filter.completed"]
    raise ValueError("unknown frozen test group")


def assertion(product, name):
    if name == "title.normal":
        assert product.normalize_title("  Example task  ") == "Example task"
    elif name.startswith("title."):
        value = "" if name == "title.empty" else " \t\n "
        try:
            product.normalize_title(value)
        except ValueError:
            return
        raise AssertionError("blank title accepted")
    else:
        records = [{"id": 2, "title": "Active", "completed": False},
                   {"id": 7, "title": "Done", "completed": True}]
        expected = records[:1] if name == "filter.active" else records[1:]
        assert product.filter_tasks(records, name.split(".")[1]) == expected


def main(arguments):
    phase, group = arguments
    selected = cases(group)
    if phase == "check":
        product = load_product()
        for name in selected:
            assertion(product, name)
        return 0
    common = {"suite_sha256": os.environ["SDLC_ACCEPTANCE_SUITE_SHA256"],
              "selection_sha256": os.environ["SDLC_ACCEPTANCE_SELECTION_SHA256"],
              "input_sha256": os.environ["SDLC_ACCEPTANCE_INPUT_SHA256"],
              "runner": {"name": "stdlib-acceptance", "version": "1"}}
    output = Path(os.environ["SDLC_ACCEPTANCE_OUTPUT"])
    if phase == "collect":
        inventory = {"schema": "test-software-in-container.inventory.v2", **common,
                     "selected": selected, "deselected": [], "complete": True}
        output.write_text(json.dumps({**inventory, "sha256": fingerprint(inventory)}))
        return 0
    if phase != "run":
        raise ValueError("unknown acceptance phase")
    inventory = json.loads(Path(os.environ["SDLC_ACCEPTANCE_INVENTORY"]).read_text())
    observed, errors = [], []
    seconds = 0.0
    code = 0
    for name in selected:
        start = time.monotonic()
        outcome = "passed"
        try:
            assertion(load_product(), name)
        except Exception:
            outcome = "failed"
            code = 1
        elapsed = time.monotonic() - start
        seconds += elapsed
        observed.append({"id": name, "origin": {"kind": "container", "reference": "tasks/domain.py"},
                         "phases": {"setup": {"outcome": "passed", "seconds": 0},
                                    "call": {"outcome": outcome, "seconds": elapsed, **({"reason": "product assertion failed"} if outcome == "failed" else {})},
                                    "teardown": {"outcome": "passed", "seconds": 0}}})
    result = {"schema": "test-software-in-container.suite-result.v2", **common,
              "case_id": os.environ["SDLC_ACCEPTANCE_CASE_ID"], "plan_sha256": os.environ["SDLC_ACCEPTANCE_PLAN_SHA256"],
              "inventory_sha256": inventory["sha256"], "completed": True, "exit_code": code,
              "collected": selected, "deselected": [], "phase_seconds": {"setup": 0, "call": seconds, "teardown": 0},
              "errors": errors, "collection_skips": [], "observed": observed}
    output.write_text(json.dumps(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
