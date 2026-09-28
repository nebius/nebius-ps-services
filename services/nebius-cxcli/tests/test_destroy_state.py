from dataclasses import replace

import pytest

from destroy_fakes import SETTINGS, Store, receipt
from nebius_cxcli.destroy_state import DestroyState, destroy_owner

# Capture production boundaries before the default offline fixtures replace cloud access.
from nebius_cxcli.destroy_state import (
    existing_project_write_lease as real_write_lease,
)


def test_remote_active_record_survives_process_loss_and_gates_nonowners():
    store = Store()
    first = DestroyState(store, SETTINGS)
    approved = replace(receipt(), checkpoints=("approved",), status="running")
    first.save(approved)
    restarted = DestroyState(store, SETTINGS)
    with pytest.raises(RuntimeError, match="unfinished"):
        restarted.require_admission()
    with (
        destroy_owner(restarted, "other-target", approved.approval_fingerprint),
        pytest.raises(RuntimeError),
    ):
        restarted.require_admission()
    with destroy_owner(restarted, approved.target_ref, approved.approval_fingerprint):
        restarted.require_admission()
    assert restarted.read() == approved


def test_remote_receipt_is_cas_bound_and_cannot_be_replaced():
    store = Store()
    first, stale = DestroyState(store, SETTINGS), DestroyState(store, SETTINGS)
    first.read()
    stale.read()
    approved = replace(receipt(), checkpoints=("approved",), status="running")
    first.save(approved)
    with pytest.raises(RuntimeError, match="CAS"):
        stale.save(approved)
    changed = replace(receipt(delete_sfs=True), checkpoints=("approved",), status="running")
    with pytest.raises(RuntimeError, match="cannot be replaced"):
        first.save(changed)


def test_no_remote_approval_for_dry_run_preview():
    state = DestroyState(Store(), SETTINGS)
    with pytest.raises(RuntimeError, match="Only approved"):
        state.save(receipt())


def test_backend_lease_loss_blocks_receipt_writes():
    def lost():
        raise RuntimeError("lease lost")

    state = DestroyState(Store(), SETTINGS, assert_held=lost)
    with pytest.raises(RuntimeError, match="lease lost"):
        state.save(replace(receipt(), checkpoints=("approved",)))


@pytest.mark.parametrize("ordinary", [False, True])
def test_existing_config_writer_checks_old_backend_before_any_publication(
    tmp_path, monkeypatch, ordinary
):
    from contextlib import contextmanager

    from nebius_cxcli import cli, destroy_state

    config = tmp_path / "config.yaml"
    config.write_bytes(b"existing source")
    old = object()
    monkeypatch.setattr(destroy_state, "existing_project_write_lease", real_write_lease)
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: old)
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _: object())

    @contextmanager
    def blocked(**kwargs):
        assert kwargs["config"] is old
        raise RuntimeError("unfinished MK8s destroy")
        yield

    monkeypatch.setattr(cli, "_deployment_execution", blocked)
    with pytest.raises(RuntimeError, match="unfinished"):
        cli._write_runtime_payload_config(
            config, {"different": "backend"}, ordinary_apps_only=ordinary
        )
    assert config.read_bytes() == b"existing source"


def test_completed_remote_authority_cannot_be_reopened():
    from nebius_cxcli.destroy import _CHECKPOINTS

    state = DestroyState(Store(), SETTINGS)
    completed = replace(receipt(), checkpoints=_CHECKPOINTS, status="complete")
    state.save(completed)
    state.save(completed)
    with pytest.raises(RuntimeError, match="cannot be reopened"):
        state.save(replace(completed, status="failed"))
    assert state.read() == completed


@pytest.mark.parametrize("command", ["validate", "quota-check", "render"])
def test_diagnostic_loading_does_not_require_destroy_mutation_authority(
    tmp_path, monkeypatch, command
):
    from nebius_cxcli import cli, destroy_state

    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}\n")
    config = object()
    calls = []

    def load(path, *, persist_normalized):
        assert not persist_normalized
        return config

    def blocked(*a, **kw):
        raise RuntimeError("unfinished destroy: mutation blocked")

    monkeypatch.setattr(cli, "load_config", load)
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", lambda _, **kw: calls.append(kw))
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _: object())
    monkeypatch.setattr(cli, "validate_path_alignment", lambda *a: None)
    monkeypatch.setattr(destroy_state, "recover_project_bundle", blocked)
    monkeypatch.setattr(destroy_state, "existing_project_write_lease", blocked)
    result, _ = cli._load_generic_soperator_lifecycle_context(
        cli._load_context, config_path, command=command
    )
    assert result is config
    assert calls == [{"need_terraform": False}]
    assert config_path.read_text() == "{}\n"


@pytest.mark.parametrize(
    "loader", ["_read_config_payload", "_load_source_payload", "_load_context_readonly"]
)
def test_readers_do_not_publish_pending_project_generations(tmp_path, monkeypatch, loader):
    from nebius_cxcli import cli
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    path = tmp_path / "config.yaml"
    path.write_bytes(b"version: old\n")

    def crash(name):
        if name == "after-commit":
            raise OSError("publication interrupted")

    with pytest.raises(OSError):
        ProjectBundleTransaction(tmp_path, failpoint=crash).commit({path: b"version: new\n"})
    monkeypatch.setattr(
        cli,
        "_identity_values_from_payload",
        lambda _: ("client", "tenant", "project", "region", ""),
    )
    monkeypatch.setattr(cli, "_ensure_project_auth_identity", lambda **kw: None)
    monkeypatch.setattr(cli, "_ensure_runtime_auth_material", lambda *a, **kw: None)
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _: object())
    monkeypatch.setattr(cli, "validate_path_alignment", lambda *a: None)
    getattr(cli, loader)(path)
    assert path.read_bytes() == b"version: old\n"


def test_existing_writer_recovers_only_under_lease_and_refuses_stale_source(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from nebius_cxcli import cli, destroy_state
    from nebius_cxcli.project_bundle_transaction import ProjectBundleTransaction

    path = tmp_path / "config.yaml"
    path.write_bytes(b"version: old\n")

    def crash(name):
        if name == "after-commit":
            raise OSError("publication interrupted")

    with pytest.raises(OSError):
        ProjectBundleTransaction(tmp_path, failpoint=crash).commit({path: b"version: new\n"})
    held = []
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: object())
    monkeypatch.setattr(
        cli, "resolve_project_paths", lambda _: SimpleNamespace(project_dir=tmp_path)
    )
    original = destroy_state.recover_project_bundle

    def recover(directory):
        assert held == [True]
        return original(directory)

    @contextmanager
    def lease(**kwargs):
        held.append(True)
        yield SimpleNamespace(assert_held=lambda: None)
        held.pop()

    monkeypatch.setattr(destroy_state, "recover_project_bundle", recover)
    monkeypatch.setattr(cli, "_deployment_execution", lease)
    with pytest.raises(RuntimeError, match="recovered.*rerun"), real_write_lease(path):
        pytest.fail("caller used stale source after recovery")
    assert path.read_bytes() == b"version: new\n"


def test_existing_writer_rejects_source_changes_during_lease_acquisition(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from nebius_cxcli import cli, destroy_state

    path = tmp_path / "config.yaml"
    path.write_bytes(b"backend: first\n")
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: object())
    monkeypatch.setattr(cli, "resolve_project_paths", lambda _: object())
    monkeypatch.setattr(
        destroy_state,
        "recover_project_bundle",
        lambda _: pytest.fail("recovery reached under obsolete backend"),
    )

    @contextmanager
    def lease(**kwargs):
        path.write_bytes(b"backend: changed\n")
        yield SimpleNamespace(assert_held=lambda: None)

    monkeypatch.setattr(cli, "_deployment_execution", lease)
    with pytest.raises(RuntimeError, match="changed.*ownership"), real_write_lease(path):
        pytest.fail("publication reached under obsolete backend")
    assert path.read_bytes() == b"backend: changed\n"


@pytest.mark.parametrize("command", ["validate", "quota-check"])
def test_public_diagnostics_select_nonpublishing_load_context(tmp_path, monkeypatch, command):
    from typer.testing import CliRunner

    from nebius_cxcli import cli

    seen = []

    def load(_):
        seen.append(cli._GENERIC_SOPERATOR_LIFECYCLE_COMMAND.get())
        raise RuntimeError("diagnostic-loader-reached")

    monkeypatch.setattr(cli, "_load_context", load)
    result = CliRunner().invoke(cli.app, [command, str(tmp_path / "config.yaml")])
    assert result.exit_code == 1 and "diagnostic-loader-reached" in result.output
    assert seen == [command]
