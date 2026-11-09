"""Qualification diagnostics preserve product results and disclose no values."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "package_diagnostics", Path(__file__).parents[1] / "systemd/package_diagnostics.py"
)
assert SPEC is not None and SPEC.loader is not None
diagnostics = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagnostics)


def invoke(loader, *, traced):
    return subprocess.run(
        [sys.executable, "-B", "-c", diagnostics.loader_code(loader) if traced else loader],
        capture_output=True,
        timeout=10,
    )


def failing_loader(boundary, *, repetitions=1, delivery_failure=False, trace_failure=False):
    source = f"""
from types import SimpleNamespace
def run(args):
    child = SimpleNamespace(returncode=100)
    {'child = type("Broken", (), {"returncode": property(lambda self: 1 / 0)})()' if trace_failure else ""}
    raise RuntimeError('secret exception text')
def apt_indexes():
    run(['apt-get', 'update', 'secret argument'])
def verify_staged():
    raise FileNotFoundError('secret archive path')
def stage():
    if {boundary!r} == 'update': apt_indexes()
    if {boundary!r} == 'download': run(['apt-get', 'download', 'secret package'])
    if {boundary!r} == 'archive': verify_staged()
def stage_payload():
    stage()
    __import__('binascii').a2b_base64('secret invalid wheel content!', strict_mode=True)
"""
    return f"""
import os, sys
exec(compile({source!r}, '<runtime-migration>', 'exec'))
print('original loader invoked once')
{"sys.__stderr__.close()" if delivery_failure else ""}
sys.stderr = open(os.devnull, 'w')
for _ in range({repetitions}):
    try:
        stage_payload()
    except Exception:
        pass
print('{{"status":"failed","stage":"inspect"}}')
raise SystemExit(1)
"""


@pytest.mark.parametrize(
    "boundary,function,phase",
    [
        ("update", "apt_indexes", "apt-update"),
        ("download", "run", "apt-download"),
        ("archive", "verify_staged", None),
        ("wheel", "stage_payload", None),
    ],
)
def test_original_failure_is_preserved_and_staging_boundary_is_observed(boundary, function, phase):
    loader = failing_loader(boundary)
    original, traced = invoke(loader, traced=False), invoke(loader, traced=True)
    assert original.returncode == traced.returncode == 1
    assert original.stdout == traced.stdout
    assert traced.stdout.count(b"original loader invoked once") == 1
    assert b"secret" not in traced.stderr
    events = json.loads(diagnostics.failure_diagnostic(traced.stderr))["events"]
    assert any(event["function"] == function for event in events)
    if phase:
        assert any(event.get("phase") == phase and event.get("exit") == 100 for event in events)


def test_diagnostics_bound_repeated_caught_exceptions_without_changing_success():
    loader = failing_loader("update", repetitions=100).replace("SystemExit(1)", "SystemExit(0)")
    original, traced = invoke(loader, traced=False), invoke(loader, traced=True)
    assert original.returncode == traced.returncode == 0
    assert original.stdout == traced.stdout
    assert len(json.loads(diagnostics.failure_diagnostic(traced.stderr))["events"]) == 16
    assert len(traced.stderr) < 4096


def test_original_source_frame_and_error_envelope_remain_unchanged():
    from nebius_vpngw import ordinary_bootstrap

    source = ordinary_bootstrap.source_frame()
    request = {"action": "stage-package", "manifest": {"runtime_plan": {}}}
    payload = source + json.dumps(request).encode()
    results = [
        subprocess.run(
            [sys.executable, "-B", "-c", loader], input=payload, capture_output=True, timeout=10
        )
        for loader in (
            ordinary_bootstrap.LOADER,
            diagnostics.loader_code(ordinary_bootstrap.LOADER),
        )
    ]
    original, traced = results
    assert original.returncode == traced.returncode == 1
    assert original.stdout == traced.stdout
    assert json.loads(traced.stdout)["error_code"] == "ordinary_transaction_failed"
    events = json.loads(diagnostics.failure_diagnostic(traced.stderr))["events"]
    assert [event["function"] for event in events] == ["stage", "stage_payload"]
    assert ordinary_bootstrap.source_frame() == source


@pytest.mark.parametrize("failure", ["delivery_failure", "trace_failure"])
def test_diagnostic_failure_never_replaces_original_transport_result(failure):
    loader = failing_loader("download", **{failure: True})
    original, traced = invoke(loader, traced=False), invoke(loader, traced=True)
    assert original.returncode == traced.returncode == 1
    assert original.stdout == traced.stdout


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"schema": "other", "events": []},
        {"schema": "package-stage-diagnostic/v1", "events": [{"secret": "canary"}]},
        {"schema": "package-stage-diagnostic/v1", "events": "canary"},
        {"schema": "package-stage-diagnostic/v1", "events": []},
    ],
)
def test_untrusted_stderr_is_never_reflected(value):
    stderr = b"secret raw stderr\nVPNGW_PACKAGE_DIAGNOSTIC=" + json.dumps(value).encode()
    assert diagnostics.failure_diagnostic(stderr) == "unavailable"
