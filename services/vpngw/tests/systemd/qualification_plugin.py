"""Retain collected cases and every outcome even when the native lane fails fast."""

from __future__ import annotations

import json
import os
from pathlib import Path

_state: dict = {}


def _save() -> None:
    path = Path(os.environ["VPNGW_NATIVE_REPORT"])
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(_state, indent=2, sort_keys=True) + "\n")
    pending.replace(path)


def pytest_sessionstart(session) -> None:
    _state.clear()
    _state.update(
        schema="nebius-vpngw.native-qualification.v1",
        topology=os.environ["VPNGW_NATIVE_TOPOLOGY"],
        expected_nodeids=[],
        outcomes={},
        status="collecting",
    )
    _save()


def pytest_collection_finish(session) -> None:
    _state["expected_nodeids"] = [item.nodeid for item in session.items]
    _state["expected_cases"] = len(session.items)
    _state["status"] = "running"
    _save()


def pytest_runtest_logreport(report) -> None:
    _state["outcomes"].setdefault(report.nodeid, {})[report.when] = report.outcome
    if report.when == "call":
        for driver in ("restart_driver", "owner_driver"):
            streams = {
                key.removeprefix(driver + "."): value
                for key, value in report.user_properties
                if key in {driver + ".stdout", driver + ".stderr"}
            }
            if streams:
                _state.setdefault(driver + "_output", {})[report.nodeid] = streams
    _save()


def pytest_sessionfinish(session, exitstatus) -> None:
    complete = all(
        _state["outcomes"].get(node, {}).get("call") == "passed"
        and _state["outcomes"].get(node, {}).get("teardown") == "passed"
        for node in _state["expected_nodeids"]
    )
    _state["exit_code"] = int(exitstatus)
    _state["status"] = (
        "passed" if exitstatus == 0 and complete and _state["expected_nodeids"] else "incomplete"
    )
    _state["unrun_nodeids"] = [
        node for node in _state["expected_nodeids"] if node not in _state["outcomes"]
    ]
    _save()
