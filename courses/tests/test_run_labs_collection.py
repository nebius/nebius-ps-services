"""Collection deadlines must cover large originals without skipping verification."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/run-labs/scripts"
sys.path.insert(0, str(SCRIPTS))
try:
    spec = importlib.util.spec_from_file_location(
        "runlabs_collection_under_test", SCRIPTS / "collect.py"
    )
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
finally:
    sys.path.remove(str(SCRIPTS))


@pytest.mark.parametrize(
    "size,elapsed,expected_budget",
    [(512, 30, 1800), (33 * 2**30, 4200, None), (1000 * 2**30, 60, 14400)],
)
def test_collection_budget_and_checksum_gate(
    tmp_path, monkeypatch, size, elapsed, expected_budget
):
    inventory = {
        "schema": "run-labs-inventory/v1",
        "files": [
            {"path": "results/report.nsys-rep", "sha256": "a" * 64, "size": size}
        ],
    }
    calls, verified = [], []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == "ssh":
            return SimpleNamespace(returncode=0, stdout=json.dumps(inventory))
        assert argv[0] == "rsync"
        assert argv[1:3] == ["-rlt", "--safe-links"]
        if elapsed > kwargs["timeout"]:
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(collector, "ssh_argv", lambda env: ["ssh", "fixture"])
    monkeypatch.setattr(collector.subprocess, "run", execute)
    monkeypatch.setattr(
        collector, "verify_inventory", lambda raw, value: verified.append((raw, value))
    )
    state = {
        "id": "fixture",
        "environment": {"ssh": {"target": "fixture"}},
        "plan": {"source_sha256": "b" * 64},
    }
    raw = tmp_path / "raw"
    assert collector.collect(state, {"remote_root": "/owned workspace"}, raw) == inventory
    assert len(calls) == 2 and calls[0][1]["timeout"] == 60
    budget = calls[1][1]["timeout"]
    assert elapsed <= budget <= 14400
    if expected_budget is not None:
        assert budget == expected_budget
    assert verified == [(raw, inventory)]
    assert json.loads((raw / "inventory.json").read_text()) == inventory


def test_failed_copy_cannot_publish_inventory(tmp_path, monkeypatch):
    inventory = {"schema": "run-labs-inventory/v1", "files": []}
    monkeypatch.setattr(collector, "ssh_argv", lambda env: ["ssh", "fixture"])
    monkeypatch.setattr(
        collector.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(
            returncode=0 if argv[0] == "ssh" else 23,
            stdout=json.dumps(inventory),
        ),
    )
    monkeypatch.setattr(
        collector,
        "verify_inventory",
        lambda *args: pytest.fail("An incomplete copy reached checksum acceptance"),
    )
    state = {
        "id": "fixture",
        "environment": {"ssh": {"target": "fixture"}},
        "plan": {"source_sha256": "b" * 64},
    }
    raw = tmp_path / "raw"
    with pytest.raises(ValueError, match="Raw result copy failed"):
        collector.collect(state, {"remote_root": "/owned workspace"}, raw)
    assert not (raw / "inventory.json").exists()


@pytest.mark.parametrize("size", [-1, 1.5, True])
def test_invalid_inventory_size_cannot_set_copy_budget(size):
    with pytest.raises(ValueError, match="non-negative integer"):
        collector.copy_timeout({"files": [{"size": size}]})
