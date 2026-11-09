import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy

import pytest
import yaml
from tests.unit.test_pair_config import seed
from typer.testing import CliRunner

from nebius_vpngw import pair_apply, pair_cli, pair_config, pair_worker
from nebius_vpngw.apply_approval import digest
from nebius_vpngw.cli import app


@pytest.fixture(autouse=True)
def selected_artifact(tmp_path, monkeypatch):
    path = tmp_path / "candidate.whl"
    path.write_bytes(b"test candidate bytes")
    monkeypatch.setattr(pair_apply, "artifact", lambda: path)
    monkeypatch.setenv("VPNGW_AGENT_WHEEL", str(path))
    return path


def prepared_pair(tmp_path, a=True, b=True, *, profiles=None):
    path = pair_config.create(
        tmp_path / "private", "regional", seed("a", a), seed("b", b), mode="bgp", **(profiles or {})
    )
    _, configs = pair_config.load(path)
    for side in pair_config.SIDES:
        config = configs[side]
        config["gateway_group"]["external_ips"] = [
            [f"192.0.{2 if side == 'a' else 3}.{10 + index}"]
            for index in range(config["gateway_group"]["instance_count"])
        ]
        (path.parent / f"{side}.config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    pair_config.bind_endpoints(path)
    return path


def evidence(side, phase="prepared"):
    return {
        "run": "run",
        "side": side,
        "phase": phase,
        "receipts": {"prepared": {"proof": side}},
        "result": None,
    }


def test_no_region_converges_before_both_preparation_and_unlock_receipts():
    a, b = evidence("a"), evidence("b", "starting")
    assert pair_apply.release_for("run", "prepared", {"a": a, "b": b}) is None
    b["phase"] = "prepared"
    assert pair_apply.release_for("run", "prepared", {"a": a, "b": b}) == {
        "run": "run",
        "receipts": {side: digest({"proof": side}) for side in pair_config.SIDES},
    }
    a["phase"] = "originating"
    a["receipts"]["originating"] = {"unlock": "a"}
    assert pair_apply.release_for("run", "originating", {"a": a, "b": b}) is None
    b["phase"] = "originating"
    b["receipts"]["originating"] = {"unlock": "b"}
    assert pair_apply.release_for("run", "originating", {"a": a, "b": b})["receipts"][
        "a"
    ] == digest({"unlock": "a"})


def test_stale_or_unverified_complete_child_cannot_release_other_region():
    a, b = evidence("a"), evidence("b", "complete")
    with pytest.raises(RuntimeError, match="verified owner"):
        pair_apply.release_for("run", "prepared", {"a": a, "b": b})
    b["result"] = {"outcome": "unchanged"}
    assert pair_apply.release_for("run", "prepared", {"a": a, "b": b})
    b["run"] = "older"
    with pytest.raises(RuntimeError, match="different invocation"):
        pair_apply.release_for("run", "prepared", {"a": a, "b": b})


@pytest.mark.parametrize("converges", (False, True))
def test_worker_keeps_first_readiness_failure_across_unknown_observations(
    tmp_path, monkeypatch, converges
):
    from types import SimpleNamespace as NS
    from unittest.mock import Mock

    from nebius_vpngw import pair_readiness

    descriptor = os.open(tmp_path, os.O_RDONLY)
    child = NS(
        request={"approval": "a" * 64, "lock_fd": descriptor, "parent": os.getppid()},
        record={"result": {"outcome": "unchanged"}},
        config=tmp_path / "a.yaml",
        deadline=100,
        save=Mock(),
    )
    monkeypatch.setattr(pair_worker, "Child", lambda _: child)
    monkeypatch.setattr("typer.main.get_command", lambda _: NS(main=lambda *a, **kw: None))
    clock = iter([0, 1, 2 if converges else 100])
    monkeypatch.setattr(pair_worker.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(pair_worker.time, "sleep", lambda _: None)
    first = pair_readiness.ReadinessFailure("pair-owner", "regional-health-not-verified")
    later = pair_readiness.ReadinessFailure("pair-cloud", "cloud-observation-unavailable")
    results = [first, later]
    if converges:
        results.append({"connection": "ready", "connectivity": "unknown"})
    monkeypatch.setattr(pair_readiness, "observe", Mock(side_effect=results))
    with pytest.raises(SystemExit if converges else RuntimeError) as error:
        pair_worker.main(tmp_path / "request.json")
    assert child.record["first_readiness_failure"] == first.failure.mapping()
    assert child.record["phase"] == ("complete" if converges else "failed")
    if converges:
        assert error.value.code == 0
        assert child.record["readiness"]["connection"] == "ready"
    else:
        assert "pair-owner/regional-health-not-verified" in str(error.value)
        assert child.record["readiness"]["latest_failure"] == later.failure.mapping()


CHILD = """
import json,os,time
from pathlib import Path
from nebius_vpngw.pair_worker import Child,ENV
child=Child(Path(os.environ[ENV]))
child.save()
side=child.request['side']
if side=='b':time.sleep(.1)
receipt={'execution':side,'artifact':child.request['artifact'],'approval':'b'*64,'command':'c'*64,'evidence':'d'*64}
try:
 child.reached('prepared',receipt)
 assert (child.path.parent/'prepared.release.json').exists()
 child.reached('originating',receipt)
 assert (child.path.parent/'originating.release.json').exists()
 child.report({'outcome':'applied','connectivity':'unknown'})
 child.record['readiness']={'connection':'ready','connectivity':'unknown'}
 child.record['phase']='complete'
finally:
 child.save()
 os.close(child.request['lock_fd'])
"""


def test_atomic_child_progress_race_is_unknown_then_reobserved(tmp_path, monkeypatch):
    from nebius_vpngw.deploy.operator_keys import PrivateFileChanged

    path = prepared_pair(tmp_path)
    monkeypatch.setattr(pair_apply, "command", lambda: [sys.executable, "-c", CHILD])
    original = pair_apply.read_private_file
    raced = []

    def read(path, **kwargs):
        if path.name == "a.status.json" and path.exists() and not raced:
            raced.append(path)
            raise PrivateFileChanged("writer atomically replaced status")
        return original(path, **kwargs)

    monkeypatch.setattr(pair_apply, "read_private_file", read)
    result = pair_apply.apply(path, approvals={"a": None, "b": None})
    assert raced and result["phase"] == "complete"
    assert all(c["readiness"]["connection"] == "ready" for c in result["children"].values())


@pytest.mark.parametrize("a,b", [(False, False), (False, True), (True, False), (True, True)])
def test_real_child_process_barriers_and_independent_project_context(tmp_path, monkeypatch, a, b):
    path = prepared_pair(tmp_path, a, b)
    monkeypatch.setattr(pair_apply, "command", lambda: [sys.executable, "-c", CHILD])
    state = pair_apply.apply(path, approvals={"a": None, "b": None})
    assert state["phase"] == "complete"
    assert set(state["children"]) == {"a", "b"}
    for side in pair_config.SIDES:
        assert set(state["children"][side]["receipts"]) == {"prepared", "originating"}
    before = deepcopy(state)
    resumed = pair_apply.apply(path, approvals={"a": None, "b": None})
    assert resumed["run"] != before["run"]
    assert resumed["previous"] == before["run"]
    assert (path.parent / ".pair-runs" / before["run"] / "a.status.json").exists()


def test_inherited_writer_lock_survives_parent_fd_close(tmp_path):
    path = prepared_pair(tmp_path)
    ready = path.parent / "child-ready"
    code = "import os,sys,time;from pathlib import Path;Path(sys.argv[1]).touch();time.sleep(10)"
    with pair_apply.writer(path) as lock:
        child = subprocess.Popen(
            [sys.executable, "-c", code, str(ready)], pass_fds=(lock,), start_new_session=True
        )
    try:
        with pytest.raises(RuntimeError, match="still running"), pair_apply.writer(path):
            pass
    finally:
        pair_apply._stop({"a": child})
    with pair_apply.writer(path):
        pass


def test_child_parent_loss_leaves_barrier_without_fabricating_completion(
    tmp_path, monkeypatch, selected_artifact
):
    path = prepared_pair(tmp_path)
    manifest, configs = pair_config.load(path)
    with pair_apply.writer(path) as lock:
        request = {
            "schema": pair_worker.SCHEMA,
            "run": "run",
            "side": "a",
            "manifest": str(path),
            "configuration": digest(configs["a"]),
            "parent": os.getppid() + 1,
            "lock_fd": lock,
            "approval": None,
            "identity": manifest["identity"],
            "profile": manifest["operator_profiles"]["a"],
            "timeout": 300,
            "artifact": hashlib.sha256(selected_artifact.read_bytes()).hexdigest(),
        }
        target = path.parent / "a.request.json"
        from nebius_vpngw.deploy.operator_keys import publish_new

        publish_new(target, json.dumps(request).encode())
        child = pair_worker.Child(target)
        with pytest.raises(RuntimeError, match="coordinator stopped"):
            child.reached("prepared", {"execution": "test", "artifact": request["artifact"]})
        assert json.loads(child.status.read_bytes())["phase"] == "prepared"
        assert child.record["result"] is None


def test_preview_and_status_do_not_create_pair_journals_or_networks(tmp_path, monkeypatch):
    path = pair_config.create(
        tmp_path / "private", "regional", seed("a", False), seed("b", True), mode="static"
    )
    monkeypatch.setattr(pair_cli, "_run", lambda *a: pytest.fail("Unresolved pair contacted cloud"))
    before = {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in path.parent.iterdir()}
    preview = CliRunner().invoke(app, ["pair", "apply", str(path), "--dry-run"])
    status = CliRunner().invoke(app, ["pair", "status", str(path)])
    assert preview.exit_code == status.exit_code == 0
    assert "network-preparation-required" in preview.output
    assert "readiness unknown" in status.output
    assert {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in path.parent.iterdir()} == before


def test_prepared_preview_and_status_delegate_each_exact_region(tmp_path, monkeypatch):
    path = prepared_pair(tmp_path)
    events = []
    monkeypatch.setattr(
        pair_cli,
        "_run",
        lambda manifest, path, side, action, *options: events.append((side, action, options)),
    )
    assert CliRunner().invoke(app, ["pair", "apply", str(path), "--dry-run"]).exit_code == 0
    assert events == [("a", "apply", ("--dry-run",)), ("b", "apply", ("--dry-run",))]
    events.clear()
    assert CliRunner().invoke(app, ["pair", "status", str(path)]).exit_code == 0
    assert events == [("a", "status", ()), ("b", "status", ())]


@pytest.mark.parametrize("arguments", [["apply", "--dry-run"], ["status"]])
def test_failed_regional_observation_still_reports_other_region(tmp_path, monkeypatch, arguments):
    path = prepared_pair(tmp_path)
    seen = []

    def observe(manifest, path, side, action, *options):
        seen.append(side)
        if side == "a":
            raise RuntimeError("cloud unavailable")

    monkeypatch.setattr(pair_cli, "_run", observe)
    result = CliRunner().invoke(app, ["pair", arguments[0], str(path), *arguments[1:]])
    assert result.exit_code == 1
    assert seen == ["a", "b"]
    assert "readiness unknown" in result.output


def test_interrupted_pair_retains_artifact_and_resumes_both_children(
    tmp_path, monkeypatch, selected_artifact
):
    path = prepared_pair(tmp_path)
    code = CHILD.replace(
        "child.save()\nside=", "child.save()\nraise RuntimeError('interruption')\nside="
    )
    monkeypatch.setattr(pair_apply, "command", lambda: [sys.executable, "-c", code])
    with pytest.raises(RuntimeError, match="did not complete"):
        pair_apply.apply(path, approvals={"a": None, "b": None})
    journal = path.parent / ".pair-apply.json"
    previous = journal.read_bytes()
    original = selected_artifact.read_bytes()
    selected_artifact.write_bytes(b"different candidate")
    with pytest.raises(RuntimeError, match="exact configurations and artifact"):
        pair_apply.apply(path, approvals={"a": None, "b": None})
    assert journal.read_bytes() == previous
    selected_artifact.write_bytes(original)
    monkeypatch.setattr(pair_apply, "command", lambda: [sys.executable, "-c", CHILD])
    result = pair_apply.apply(path, approvals={"a": None, "b": None})
    assert result["phase"] == "complete"
    assert result["previous"] == json.loads(previous)["run"]


def test_successful_child_exit_without_readiness_does_not_complete_pair(tmp_path, monkeypatch):
    path = prepared_pair(tmp_path)
    code = CHILD.replace("'connection':'ready'", "'connection':'unknown'")
    monkeypatch.setattr(pair_apply, "command", lambda: [sys.executable, "-c", code])
    with pytest.raises(RuntimeError, match="independent connection readiness"):
        pair_apply.apply(path, approvals={"a": None, "b": None})
    assert json.loads((path.parent / ".pair-apply.json").read_bytes())["phase"] == "pending"


def test_stop_tolerates_child_exit_between_poll_and_signal(monkeypatch):
    from unittest.mock import Mock

    child = Mock(pid=123, poll=lambda: None)
    monkeypatch.setattr(pair_apply.os, "killpg", Mock(side_effect=ProcessLookupError))
    pair_apply._stop({"a": child})
    assert child.wait.called


def test_malformed_seed_error_never_prints_secret_source_line(tmp_path):
    seed_path = tmp_path / "seed.yaml"
    seed_path.write_text("psk: [never-print-this-secret\n")
    result = CliRunner().invoke(
        app,
        [
            "pair",
            "create-config",
            str(tmp_path / "private"),
            "--name",
            "regional",
            "--left",
            str(seed_path),
            "--right",
            str(seed_path),
        ],
    )
    assert result.exit_code != 0
    assert "valid configuration" in result.output
    assert "never-print-this-secret" not in result.output
