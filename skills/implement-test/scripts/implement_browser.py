"""Lifecycle-owned Playwright evidence for the exact delivered local application."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.request import HTTPRedirectHandler, build_opener
import uuid

import implement_catalog as catalog
from implement_errors import LifecycleError, CapabilityUnavailableError

ASSETS = Path(__file__).resolve().parents[1] / "assets/headless-browser"
FILES = (
    "package.json",
    "package-lock.json",
    "playwright.config.mjs",
    "acceptance.spec.mjs",
)
SCHEMA = "implement-test/browser-evidence-v1"


def support():
    scripts = catalog.anchor() / "sdlc-workflow-test/scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    module = importlib.import_module("three_tier_browser")
    if Path(module.__file__).resolve() != scripts / "three_tier_browser.py":
        raise LifecycleError("browser process owner comes from a different catalog")
    return module


def sha(path):
    return hashlib.sha256(catalog.safe_bytes(path)).hexdigest()


def close_owned(state, root, *, reap=None):
    support().require_dependency_quiescence(root)
    value = state.get("browser_instance")
    if value is not None:
        state["browser_instance"] = support().close(
            root, state["generation_id"], value, reap=reap
        )


def assertions(value, expected):
    keys = {
        "schema",
        "generation_id",
        "head",
        "url",
        "phase",
        "attempt_id",
        "record",
        "checks",
        "cleanup",
        "status",
        "browser_version",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise LifecycleError("browser assertion shape is invalid")
    for key in ("schema", "generation_id", "head", "url", "phase", "attempt_id"):
        if value[key] != expected[key]:
            raise LifecycleError("browser assertion identity differs")
    checks = (
        ["frontend", "create", "update", "reload"]
        if value["phase"] == "before-restart"
        else ["frontend", "reload"]
    )
    record = value["record"]
    if (
        value["status"] != "PASS"
        or value["cleanup"] is not True
        or value["checks"] != checks
        or not isinstance(value["browser_version"], str)
        or not value["browser_version"]
        or not isinstance(record, dict)
        or set(record) != {"id", "title", "completed"}
        or type(record["id"]) is not int
        or record["id"] < 1
        or not isinstance(record["title"], str)
        or not 1 <= len(record["title"]) <= 200
        or record["completed"] is not True
    ):
        raise LifecycleError(
            "browser assertions did not establish completed UI behavior"
        )
    if expected.get("record") is not None and record != expected["record"]:
        raise LifecycleError("browser restart changed the task")
    return record


def run_phase(state, root, phase, record, persist):
    owner = support()
    attempt = uuid.uuid4().hex
    private = root / "private/browser" / attempt
    output = root / "evidence/browser" / attempt
    private.mkdir(parents=True, mode=0o700)
    output.mkdir(parents=True, mode=0o700)
    (private / "tmp").mkdir(mode=0o700)
    bundle = root / "private/browser/bundle"
    input_value = {
        "schema": SCHEMA,
        "generation_id": state["generation_id"],
        "head": state["promoted_head"],
        "url": f"http://127.0.0.1:{state['web_port']}",
        "phase": phase,
        "attempt_id": attempt,
        "record": record,
    }
    owner.private_json(private / "input.json", input_value)
    (private / "playwright.config.mjs").write_text(
        "import config from "
        + json.dumps(str(bundle / "playwright.config.mjs"))
        + ";\nexport default config;\n"
    )
    env = {
        **os.environ,
        "IMPLEMENT_BROWSER_INPUT": str(private / "input.json"),
        "IMPLEMENT_BROWSER_OUTPUT": str(output),
        "TMPDIR": str(private / "tmp"),
        "CI": "1",
    }
    process = None
    capture = None
    started = time.monotonic_ns()
    try:
        process = subprocess.Popen(
            [
                "node",
                str(bundle / "node_modules/@playwright/test/cli.js"),
                "test",
                "--config",
                str(private / "playwright.config.mjs"),
            ],
            cwd=bundle,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            umask=0o077,
        )
        state["browser_instance"] = {
            **owner.initial_state(state["generation_id"]),
            "status": "RUNNING",
            "active": {"attempt_id": attempt, "pid": process.pid},
        }
        persist()
        capture = owner.process_owner().PipeCapture(process)
        next_observation = time.monotonic() + 30
        while process.poll() is None:
            capture.drain(.05)
            if time.monotonic() >= next_observation:
                print("Owned browser stage continues; observation checkpoint.", flush=True)
                next_observation = time.monotonic() + 30
        if process.returncode:
            receipt = output / "assertions.json"
            if receipt.is_file():
                unavailable = json.loads(catalog.safe_bytes(receipt))
                expected = {
                    **input_value,
                    "checks": [],
                    "cleanup": True,
                    "browser_version": "",
                    "status": "PARTIAL",
                }
                if unavailable == expected:
                    raise CapabilityUnavailableError(
                        "isolated Chrome launch capability is unavailable"
                    )
            raise LifecycleError(
                "Playwright assertions failed; preserve this trial"
            )
    finally:
        try:
            if process is not None:
                close_owned(state, root, reap=process.poll)
                process.wait(timeout=5)
                persist()
        finally:
            if capture is not None:
                diagnostics = capture.finish()
                owner.private_json(output / "runner-output.json", diagnostics)
                if not all(row["complete"] for row in diagnostics.values()):
                    raise LifecycleError("browser output completeness is unproven")
    observed = json.loads(catalog.safe_bytes(output / "assertions.json"))
    record = assertions(observed, input_value)
    artifacts = {
        name: {
            "path": str((output / name).relative_to(root)),
            "sha256": sha(output / name),
        }
        for name in ("assertions.json", "trace.zip", "page.png")
    }
    if (
        not (output / "trace.zip").stat().st_size
        or not (output / "page.png").stat().st_size
    ):
        raise LifecycleError("browser trace or screenshot is empty")
    return {
        "input": input_value,
        "record": record,
        "artifacts": artifacts,
        "exit_code": 0,
        "cleanup": "PASS",
        "started_monotonic_ns": started,
        "finished_monotonic_ns": time.monotonic_ns(),
    }


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise LifecycleError("browser API correlation must stay on the owned origin")


def correlate(state, record, database):
    with build_opener(NoRedirect).open(
        f"http://127.0.0.1:{state['web_port']}/api/tasks", timeout=15
    ) as response:
        body = response.read(1024 * 1024 + 1)
    if len(body) > 1024 * 1024:
        raise LifecycleError("browser API correlation exceeded size bound")
    tasks = json.loads(body)
    matches = (
        [t for t in tasks if isinstance(t, dict) and t.get("id") == record["id"]]
        if isinstance(tasks, list)
        else []
    )
    if (
        len(matches) != 1
        or type(matches[0].get("id")) is not int
        or type(matches[0].get("completed")) is not bool
        or not isinstance(matches[0].get("title"), str)
        or {key: matches[0].get(key) for key in ("id", "title", "completed")} != record
    ):
        raise LifecycleError("browser task differs from API state")
    row = database(record["id"])
    if {
        "id": row.get("task_id"),
        "title": row.get("title"),
        "completed": row.get("completed"),
    } != record:
        raise LifecycleError("browser task differs from database state")
    return {"api": True, "database": True, "record": record}


def collect(state, root, *, persist, database, restart):
    from implement_lifecycle import _atomic_json

    if state.get("browser_attempted"):
        raise LifecycleError("browser acceptance is single-attempt; create a new trial")
    if not shutil.which("node") or not shutil.which("npm"):
        raise CapabilityUnavailableError(
            "Node/npm are required for pinned Playwright acceptance"
        )
    state["browser_attempted"] = True
    persist()
    bundle = root / "private/browser/bundle"
    bundle.mkdir(parents=True, mode=0o700)
    for name in FILES:
        (bundle / name).write_bytes(catalog.safe_bytes(ASSETS / name))
    result = support().install_dependencies(bundle, os.environ.copy())
    if result.returncode:
        raise CapabilityUnavailableError(
            "pinned Playwright dependency installation failed"
        )
    phases = []
    before = run_phase(state, root, "before-restart", None, persist)
    before["correlation"] = correlate(state, before["record"], database)
    phases.append(before)
    restart_started = time.monotonic_ns()
    restart()
    restart_finished = time.monotonic_ns()
    after = run_phase(state, root, "after-restart", before["record"], persist)
    after["correlation"] = correlate(state, after["record"], database)
    phases.append(after)
    receipt = {
        "schema": SCHEMA,
        "generation_id": state["generation_id"],
        "head": state["promoted_head"],
        "url": f"http://127.0.0.1:{state['web_port']}",
        "catalog_sha256": state["runtime_catalog"]["sha256"],
        "phases": phases,
        "restart": {
            "started_monotonic_ns": restart_started,
            "finished_monotonic_ns": restart_finished,
        },
        "status": "PASS",
        "cleanup": "PASS",
    }
    output = root / "evidence/browser.json"
    _atomic_json(output, receipt)
    state["browser_evidence_sha256"] = sha(output)
    persist()
    validate(state, root)
    return {
        "status": "BROWSER_VERIFIED",
        "artifact": str(output),
        "sha256": state["browser_evidence_sha256"],
    }


def validate(state, root):
    try:
        return _validate(state, root)
    except (KeyError, TypeError, ValueError) as error:
        raise LifecycleError("browser evidence shape is invalid") from error


def _validate(state, root):
    path = root / "evidence/browser.json"
    if sha(path) != state.get("browser_evidence_sha256"):
        raise LifecycleError("browser evidence is missing or changed")
    value = json.loads(catalog.safe_bytes(path))
    expected_url = f"http://127.0.0.1:{state['web_port']}"
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "generation_id",
            "head",
            "url",
            "catalog_sha256",
            "phases",
            "restart",
            "status",
            "cleanup",
        }
        or value["schema"] != SCHEMA
        or value["generation_id"] != state["generation_id"]
        or value["head"] != state.get("promoted_head")
        or value["url"] != expected_url
        or value["catalog_sha256"] != state["runtime_catalog"]["sha256"]
        or value["status"] != "PASS"
        or value["cleanup"] != "PASS"
        or not isinstance(value["phases"], list)
        or len(value["phases"]) != 2
    ):
        raise LifecycleError("browser evidence identity or completion is invalid")
    record = None
    intervals = []
    attempts = set()
    for phase, row in zip(("before-restart", "after-restart"), value["phases"]):
        if not isinstance(row, dict) or set(row) != {
            "input",
            "record",
            "artifacts",
            "exit_code",
            "cleanup",
            "correlation",
            "started_monotonic_ns",
            "finished_monotonic_ns",
        }:
            raise LifecycleError("browser phase shape is invalid")
        started, finished = row["started_monotonic_ns"], row["finished_monotonic_ns"]
        if (
            type(started) is not int
            or type(finished) is not int
            or not 0 < started <= finished
        ):
            raise LifecycleError("browser phase timing is invalid")
        intervals.append((started, finished))
        expected = {
            "schema": SCHEMA,
            "generation_id": state["generation_id"],
            "head": state["promoted_head"],
            "url": expected_url,
            "phase": phase,
            "attempt_id": row["input"]["attempt_id"],
            "record": record,
        }
        attempt = expected["attempt_id"]
        if (
            not isinstance(attempt, str)
            or len(attempt) != 32
            or any(c not in "0123456789abcdef" for c in attempt)
        ):
            raise LifecycleError("browser attempt identity is invalid")
        if attempt in attempts:
            raise LifecycleError("browser phases reused an attempt")
        attempts.add(attempt)
        if (
            row["input"] != expected
            or type(row.get("exit_code")) is not int
            or row["exit_code"] != 0
            or row.get("cleanup") != "PASS"
            or set(row["artifacts"]) != {"assertions.json", "trace.zip", "page.png"}
        ):
            raise LifecycleError("browser phase identity or cleanup is invalid")
        for name, artifact in row["artifacts"].items():
            relative = f"evidence/browser/{attempt}/{name}"
            if (
                artifact.get("path") != relative
                or sha(root / relative) != artifact.get("sha256")
                or not (root / relative).stat().st_size
            ):
                raise LifecycleError("browser artifact is missing, changed or unsafe")
        observed = json.loads(
            catalog.safe_bytes(root / row["artifacts"]["assertions.json"]["path"])
        )
        record = assertions(observed, expected)
        if row.get("record") != record or row.get("correlation") != {
            "api": True,
            "database": True,
            "record": record,
        }:
            raise LifecycleError("browser correlation is incomplete")
    restart = value["restart"]
    if (
        not isinstance(restart, dict)
        or set(restart) != {"started_monotonic_ns", "finished_monotonic_ns"}
        or any(type(v) is not int or v < 1 for v in restart.values())
        or not intervals[0][1]
        <= restart["started_monotonic_ns"]
        <= restart["finished_monotonic_ns"]
        <= intervals[1][0]
    ):
        raise LifecycleError("browser restart boundary is invalid")
    support().validate_state(state.get("browser_instance"), state["generation_id"])
    if state["browser_instance"]["status"] != "CLOSED":
        raise LifecycleError("browser process cleanup is incomplete")
    return value
