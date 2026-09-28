"""A past invocation is recovery evidence, never a shared admission blocker."""

from dataclasses import replace
from pathlib import Path

import pytest

from nebius_cxcli.deployment_local import LocalExecutionOwner, LocalObjectStore
from nebius_cxcli.deployment_state import DeploymentGeneration, DeploymentState, digest
from nebius_cxcli.deployment_workflow import _semantic_controls
from nebius_cxcli.terraform_backend import TerraformBackendSettings


def settings():
    return TerraformBackendSettings(
        "project", "client", "region", "bucket", "state", "https://storage.example.invalid"
    )


def attempt(store, generation, controls):
    identity = digest({"generation": generation.identity, "controls": _semantic_controls(controls)})
    return DeploymentState(store, settings(), assert_held=lambda: None, attempt=identity[7:])


def test_changed_inputs_and_options_leave_prior_attempt_untouched(tmp_path):
    store = LocalObjectStore(tmp_path)
    original = DeploymentGeneration({"runtime_config": {"value": 1}}, {})
    first = attempt(store, original, {"jobPolicy": "fail"})
    started = first.begin(original, plan={"controls": {"jobPolicy": "fail"}})
    first.checkpoint(started, stage="apply", evidence={"status": "executing"})
    before = (tmp_path / first.record_key).read_bytes()
    for generation, options in (
        (DeploymentGeneration({"runtime_config": {"value": 2}}, {}), {"jobPolicy": "fail"}),
        (original, {"jobPolicy": "wait"}),
    ):
        current = attempt(store, generation, options)
        assert current.read() is None
        current.begin(generation, plan={"controls": options})
        assert (tmp_path / first.record_key).read_bytes() == before


def test_matching_attempt_resumes_after_reopening_store(tmp_path):
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    first = attempt(LocalObjectStore(tmp_path), generation, {"jobWaitTimeout": "60s"})
    record = first.begin(generation, plan={"controls": {"jobWaitTimeout": "60s"}})
    record = first.checkpoint(record, stage="apply", evidence={"status": "executing"})
    resumed = attempt(LocalObjectStore(tmp_path), generation, {"jobWaitTimeout": "1m"})
    assert resumed.read() == record


def test_completion_index_never_exposes_another_commands_active_attempt(tmp_path):
    store = LocalObjectStore(tmp_path)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    state = attempt(store, generation, {})
    record = state.begin(generation, plan={})
    assert state.latest_completed() is None
    state.accept(record, evidence={"verified": True})
    command = DeploymentState(store, settings(), command="profiling", assert_held=lambda: None)
    command.begin(generation, plan={"kind": "nsight-profiling"})
    before = (tmp_path / command.record_key).read_bytes()
    new = attempt(store, generation, {"targetRef": "another"})
    assert new.read() is None
    new.accept(new.begin(generation, plan={}), evidence={"verified": True})
    assert (tmp_path / command.record_key).read_bytes() == before


def test_fresh_attempt_does_not_read_corrupt_completion_hint(tmp_path):
    store = LocalObjectStore(tmp_path)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    state = attempt(store, generation, {})
    path = tmp_path / state.prefix / "state.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"interrupted optional report")
    assert state.read() is None
    started = state.begin(generation, plan={})
    assert started.value["accepted"] is None
    completed = state.accept(started, evidence={"verified": True})
    assert state.read() == completed
    assert completed.value["active"] is None
    assert completed.value["accepted"]["generation"] == generation.identity
    assert path.read_bytes() == b"interrupted optional report"


@pytest.mark.parametrize("command", ["profiling-target", "ordinary-apps"])
def test_terminal_command_uses_current_completed_generation_without_rewriting_history(
    tmp_path, command
):
    store = LocalObjectStore(tmp_path)
    old = DeploymentGeneration({"runtime_config": {"revision": 1}}, {})
    current = DeploymentGeneration({"runtime_config": {"revision": 2}}, {})
    state = DeploymentState(store, settings(), command=command, assert_held=lambda: None)
    terminal = state.accept(state.begin(old, plan={}), evidence={})
    prior_bytes = (tmp_path / state.record_key).read_bytes()
    deploy = attempt(store, current, {})
    completed = deploy.accept(deploy.begin(current, plan={}), evidence={})
    state = DeploymentState(
        store,
        settings(),
        command=command,
        baseline_generation=current.identity,
        assert_held=lambda: None,
    )
    selected = state.read()
    assert selected.value["accepted"] == completed.value["accepted"]
    assert selected.etag == terminal.etag
    assert (tmp_path / state.record_key).read_bytes() == prior_bytes
    started = state.begin(current, plan={"kind": "current-command"})
    assert started.value["accepted"] == completed.value["accepted"]
    assert started.value["active"]["generation"] == current.identity


@pytest.mark.parametrize(
    "invalid", [b"unavailable optional completion report", b'{"accepted": ["corrupt"]}']
)
def test_current_command_completion_survives_failed_optional_index_publication(tmp_path, invalid):
    store = LocalObjectStore(tmp_path)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    state = DeploymentState(
        store,
        settings(),
        command="profiling-target",
        baseline_generation=generation.identity,
        assert_held=lambda: None,
    )
    active = state.begin(generation, plan={})
    index = tmp_path / state.prefix / "state.json"
    index.write_bytes(invalid)
    completed = state.accept(active, evidence={"verified": True})
    assert state.read() == completed
    assert index.read_bytes() == invalid


def test_active_command_keeps_its_exact_record_despite_new_baseline(tmp_path):
    store = LocalObjectStore(tmp_path)
    old = DeploymentGeneration({"runtime_config": {"revision": 1}}, {})
    current = DeploymentGeneration({"runtime_config": {"revision": 2}}, {})
    state = DeploymentState(
        store,
        settings(),
        command="profiling-target",
        baseline_generation=current.identity,
        assert_held=lambda: None,
    )
    active = state.begin(old, plan={"kind": "frozen-owner"})
    fresh = attempt(store, current, {})
    fresh.accept(fresh.begin(current, plan={}), evidence={})
    assert state.read() == active


def test_target_only_completion_preserves_other_targets_without_seeding_attempt(tmp_path):
    from nebius_cxcli.ordinary_apps import validate_accepted_deployment_record

    store = LocalObjectStore(tmp_path)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    identities = {ref: {"cluster_id": ref, "kubernetes_uid": ref + "-uid"} for ref in ("a", "b")}
    first = attempt(store, generation, {"allTargets": True})
    first.accept(
        first.begin(generation, plan={}),
        evidence={
            "identities": identities,
            "targets": {
                ref: {"identity": value, "generation": generation.identity}
                for ref, value in identities.items()
            },
        },
    )
    selected = attempt(store, generation, {"targetRef": "b"})
    started = selected.begin(generation, plan={"semanticPlan": {"selectedTargets": ["b"]}})
    assert started.value["accepted"] is None
    completed = selected.accept(
        started,
        evidence={
            "identities": identities,
            "targets": {"b": {"identity": identities["b"], "generation": generation.identity}},
        },
    )
    assert set(completed.value["accepted"]["evidence"]["targets"]) == {"b"}
    latest = DeploymentState(store, settings(), assert_held=lambda: None).read()
    assert set(latest.value["accepted"]["evidence"]["targets"]) == {"a", "b"}
    validate_accepted_deployment_record(
        latest.value,
        {"deployment_generation": generation.identity, "identities": identities},
        ["a"],
    )


def test_same_generation_command_can_add_completed_targets_without_losing_own_receipts(tmp_path):
    store = LocalObjectStore(tmp_path)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    command = DeploymentState(store, settings(), command="ordinary-apps", assert_held=lambda: None)
    a = {"identity": {"cluster_id": "a", "kubernetes_uid": "a-uid"}, "receipt": "command-owned"}
    terminal = command.accept(command.begin(generation, plan={}), evidence={"targets": {"a": a}})
    selected = attempt(store, generation, {"targetRef": "b"})
    selected.accept(
        selected.begin(generation, plan={}),
        evidence={"targets": {"b": {"identity": {"cluster_id": "b", "kubernetes_uid": "b-uid"}}}},
    )
    current = DeploymentState(
        store,
        settings(),
        command="ordinary-apps",
        baseline_generation=generation.identity,
        baseline_targets=("b",),
        assert_held=lambda: None,
    )
    record = current.read()
    assert set(record.value["accepted"]["evidence"]["targets"]) == {"a", "b"}
    assert record.value["accepted"]["evidence"]["targets"]["a"] == a
    assert record.etag == terminal.etag
    assert store.read(command.record_key) == terminal


def test_invalid_optional_index_cannot_fail_completed_generic_attempt(tmp_path):
    store = LocalObjectStore(tmp_path)
    generation = DeploymentGeneration({"runtime_config": {}}, {})
    state = attempt(store, generation, {})
    active = state.begin(generation, plan={})
    path = tmp_path / state.prefix / "state.json"
    path.write_bytes(
        b'{"schema":"nebius-cxcli.deployment.v2","active":null,"accepted":["corrupt"]}'
    )
    completed = state.accept(active, evidence={"verified": True})
    assert state.read() == completed and completed.value["active"] is None


def test_process_supervisor_retains_lock_after_parent_stops_waiting(tmp_path, monkeypatch):
    import os
    import signal
    import sys
    import time

    from nebius_cxcli.lease_clock import elapsed

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    owner = LocalExecutionOwner(settings=settings(), operation_id="first")
    owner.__enter__()
    ready = tmp_path / "ready"
    process = owner.scope.launch(
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)",
        ],
        {},
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        os.kill(process.pid, signal.SIGSTOP)
        close = owner.scope.close
        monkeypatch.setattr(owner.scope, "close", lambda _: close(elapsed() + 0.05))
        with pytest.raises(RuntimeError, match="shutdown was not confirmed"):
            owner.__exit__(None, None, None)
        with (
            pytest.raises(RuntimeError, match="Another deployment"),
            LocalExecutionOwner(settings=settings(), operation_id="second"),
        ):
            pass
    finally:
        os.kill(process.pid, signal.SIGCONT)
        process.terminate()
        process.wait(timeout=12)
        if owner._held:
            owner.__exit__(None, None, None)
    assert process.quiescent
    with LocalExecutionOwner(settings=settings(), operation_id="third"):
        pass


def test_local_store_rejects_changed_preimage_and_symlink(tmp_path):
    store = LocalObjectStore(tmp_path)
    etag = store.write("attempt.json", {"stage": 1}, etag=None)
    store.write("attempt.json", {"stage": 2}, etag=etag)
    with pytest.raises(RuntimeError, match="changed"):
        store.write("attempt.json", {"stage": 3}, etag=etag)
    (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symbolic"):
        store.read("link/attempt.json")


def test_native_local_ownership_is_released_and_shared_across_checkouts(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    first = LocalExecutionOwner(settings=settings(), operation_id="one")
    second = LocalExecutionOwner(settings=settings(), operation_id="two")
    with first:
        first.assert_held()
        with pytest.raises(RuntimeError, match="Another deployment"), second:
            pass
    with second:
        second.assert_held()
    with pytest.raises(RuntimeError, match="ended"):
        second.assert_held()
    other = LocalExecutionOwner(settings=replace(settings(), key="other"), operation_id="three")
    assert other.lock.path != first.lock.path
