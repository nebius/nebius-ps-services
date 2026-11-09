"""Optional observational pytest plugin: load with -p tsc_support.pytest_receipt.

TSC_PYTEST_CONFIG points to a private JSON configuration (see suite-evidence.md).
Collection and execution are separate processes. No test selection or retries.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

from tsc_support.contracts import (
    digest,
    fingerprint,
    read_json,
    require,
    seal,
    string,
    write_new,
)
from tsc_support.suite_contracts import INVENTORY, RECEIPT, validate_inventory


def pytest_configure(config):
    path = os.environ.get("TSC_PYTEST_CONFIG")
    if path is None:
        raise pytest.UsageError("pytest_receipt requires TSC_PYTEST_CONFIG")
    try:
        settings = read_json(path)
        require(settings.get("mode") in {"collect", "run"}, "invalid reporter mode")
        require(
            (settings["mode"] == "collect") == bool(config.getoption("collectonly")),
            "reporter mode must match --collect-only",
        )
        require(
            settings.get("target") in {"source", "installed"}, "invalid origin target"
        )
        string(settings.get("origin_reference"), "origin reference")
        for key in ("suite_sha256", "selection_sha256", "input_sha256"):
            digest(settings.get(key), key)
        # Refuse known retry/parallel plugins, including disabled-but-loaded ones.
        unsupported = (
            "xdist",
            "pytest_rerunfailures",
            "pytest_repeat",
            "flaky",
            "pytest_parallel",
        )
        for _, plugin in config.pluginmanager.list_name_plugin():
            module = getattr(plugin, "__name__", type(plugin).__module__)
            require(
                not any(module == p or module.startswith(p + ".") for p in unsupported),
                "retry/distributed plugins are unsupported; disable them for this receipt",
            )
        state = Reporter(settings)
        config._tsc_reporter = state
        config.pluginmanager.register(state, "tsc_observer")
    except (ValueError, OSError, TypeError, KeyError) as exc:
        raise pytest.UsageError(
            str(exc)
            if isinstance(exc, ValueError)
            else "Reporter configuration/output unavailable"
        ) from exc


class Reporter:
    def __init__(self, settings):
        self.settings = settings
        self.identity = {
            k: settings[k] for k in ("suite_sha256", "selection_sha256", "input_sha256")
        }
        self.identity["runner"] = {"name": "pytest", "version": pytest.__version__}
        self.inventory = None
        if settings["mode"] == "run":
            string(settings.get("case_id"), "case id")
            digest(settings.get("plan_sha256"), "plan identity")
            self.inventory = validate_inventory(read_json(settings["inventory"]))
            require(
                all(self.inventory[k] == v for k, v in self.identity.items()),
                "reporter differs from frozen collection",
            )
        self.output = Path(settings["output"])
        self.output.mkdir(mode=0o700, exist_ok=False)
        self.selected, self.deselected, self.observed, self.errors = [], [], {}, []
        self.collection_skips = []
        self.interrupted = False
        self.sequence = 0
        self.emit("initial.json", self.receipt(False, None))

    def emit(self, name, value):
        write_new(
            self.output / name, json.dumps(value, indent=2, allow_nan=False) + "\n"
        )

    def event(self, value):
        self.sequence += 1
        self.emit(f"event-{self.sequence:06d}.json", value)

    def pytest_deselected(self, items):
        self.deselected.extend(
            {"id": item.nodeid, "reason": "runner selection"} for item in items
        )

    def pytest_collection_finish(self, session):
        self.selected = [item.nodeid for item in session.items]
        self.event({"collection": self.selected, "deselected": self.deselected})

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors.append(
                {
                    "phase": "collection",
                    "reason": "collection failed; inspect private runner log",
                }
            )
            self.event({"collection_error": report.nodeid})
        elif report.skipped:
            # Module-level skips have no test inventory and must not disappear.
            self.collection_skips.append(
                {
                    "id": report.nodeid or ".",
                    "reason": "module skipped; inspect private runner log",
                }
            )
            self.event({"collection_skipped": report.nodeid})

    def pytest_runtest_logreport(self, report):
        outcome = report.outcome
        reason = None
        if hasattr(report, "wasxfail"):
            outcome = "xfail" if report.skipped else "xpass"
            reason = str(report.wasxfail)[:256] or "expected failure"
        elif outcome != "passed":
            if report.skipped and isinstance(report.longrepr, tuple):
                reason = str(report.longrepr[2])[:256]
            else:
                reason = "test phase failed; inspect private runner log"
        item = {"outcome": outcome, "seconds": report.duration}
        if reason:
            item["reason"] = reason.replace("\n", " ").replace("\r", " ")
        if outcome == "xfail":
            item["failure_sha256"] = hashlib.sha256(
                str(report.longrepr).encode()
            ).hexdigest()
        test = self.observed.setdefault(
            report.nodeid,
            {
                "id": report.nodeid,
                "phases": {},
                "origin": {
                    "kind": self.settings["target"],
                    "reference": self.settings["origin_reference"],
                },
            },
        )
        if report.when in test["phases"]:
            self.errors.append(
                {"phase": "runner", "reason": "duplicate phase or retry observed"}
            )
        else:
            test["phases"][report.when] = item
        self.event({"id": report.nodeid, "phase": report.when, "result": item})

    def pytest_keyboard_interrupt(self, excinfo):
        self.interrupted = True
        self.event({"interrupted": True})

    def pytest_internalerror(self, excrepr, excinfo):
        self.errors.append({"phase": "runner", "reason": "pytest internal error"})
        self.event({"runner_error": True})

    def receipt(self, completed, code):
        return {
            "schema": RECEIPT,
            **self.identity,
            "case_id": self.settings.get("case_id"),
            "plan_sha256": self.settings.get("plan_sha256"),
            "inventory_sha256": self.inventory["sha256"] if self.inventory else None,
            "completed": completed,
            "exit_code": code,
            "interrupted": self.interrupted,
            "process_completed": False,
            "process_exit_code": None,
            "collection_skips": self.collection_skips,
            "errors": self.errors,
            "collected": self.selected,
            "deselected": self.deselected,
            "observed": list(self.observed.values()),
            "phase_seconds": {
                phase: sum(
                    t["phases"].get(phase, {}).get("seconds", 0)
                    for t in self.observed.values()
                )
                for phase in ("setup", "call", "teardown")
            },
        }

    @pytest.hookimpl(wrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session, exitstatus):
        try:
            result = yield
        except BaseException:
            self.errors.append({"phase": "runner", "reason": "session finish failed"})
            self.emit("terminal.json", self.receipt(False, None))
            raise
        code = int(session.exitstatus)
        if (
            self.settings["mode"] == "collect"
            and code in {0, 5}
            and not self.errors
            and not self.interrupted
            and not self.collection_skips
        ):
            inventory = seal(
                {
                    "schema": INVENTORY,
                    **self.identity,
                    "complete": True,
                    "selected": self.selected,
                    "deselected": self.deselected,
                }
            )
            validate_inventory(inventory)
            self.emit("inventory.json", inventory)
        self.emit("terminal.json", self.receipt(True, code))
        self.emit(
            "terminal-digest.json", {"sha256": fingerprint(self.receipt(True, code))}
        )
        return result
