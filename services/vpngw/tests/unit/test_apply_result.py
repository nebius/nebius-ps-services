import json
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.deploy.apply_command import CommandJournal
from nebius_vpngw.deploy.apply_plan import CommandPlan, Effect, Evidence
from nebius_vpngw.deploy.apply_result import ApplyReport


@pytest.fixture(autouse=True)
def isolated_command_scope(monkeypatch, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text("# Isolated loader fixture\n")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(cli, "_resolve_local_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(cli, "load_local_config", lambda path: {"project_id": "project-test"})
    monkeypatch.setattr(
        cli,
        "merge_with_peer_configs",
        lambda *args: SimpleNamespace(vm_ha=None, gateway_group=SimpleNamespace(name="gateway")),
    )


def plan():
    return CommandPlan(
        "project",
        "gateway",
        "a" * 64,
        "b" * 64,
        (Effect("guest", "ordinary", "apply", ("vm",), "c" * 64, "d" * 64, True),),
    )


def test_no_success_inferred_from_normal_return(monkeypatch):
    monkeypatch.setattr(cli, "_apply_impl", lambda **kwargs: None)
    result = CliRunner().invoke(cli.app, ["apply", "--output-format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "blocked"
    assert payload["configuration"] == payload["local_runtime"] == "unknown"
    assert payload["connectivity"] == "unknown"
    assert payload["inhibition_retained"] is None


def test_json_separates_diagnostics_and_explicit_evidence(monkeypatch):
    def apply(**kwargs):
        typer.echo("planning progress")
        kwargs["result_report"].verified(changed=False, connectivity=Evidence.DEGRADED)

    monkeypatch.setattr(cli, "_apply_impl", apply)
    result = CliRunner().invoke(cli.app, ["apply", "--output-format", "json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "unchanged"
    assert payload["configuration"] == payload["local_runtime"] == "ready"
    assert payload["connectivity"] == "degraded"
    assert "planning progress" in result.stderr
    assert payload["next_command"] is None


@pytest.mark.parametrize("started,expected", [(False, "blocked"), (True, "failed")])
def test_failure_preserves_exit_contract_and_does_not_assert_remote_inhibition(
    monkeypatch, started, expected
):
    def apply(**kwargs):
        kwargs["result_report"].started = started
        raise typer.Exit(7)

    monkeypatch.setattr(cli, "_apply_impl", apply)
    result = CliRunner().invoke(cli.app, ["apply", "--output-format", "json"])
    assert result.exit_code == 7
    payload = json.loads(result.stdout)
    assert payload["outcome"] == expected
    assert payload["inhibition_retained"] is None
    assert payload["authority"] == "unknown"
    assert payload["next_command"] == "nebius-vpngw status"


def test_preview_keeps_effects_pending_without_execution_or_readiness():
    report = ApplyReport(dry_run=True)
    report.plan(("guest",), approval="a" * 64)
    report.verified(changed=True)
    payload = report.mapping()
    assert payload["outcome"] == "blocked"
    assert payload["pending"] == ("guest",)
    assert payload["completed"] == ()
    assert payload["local_runtime"] == "unknown"


def test_partial_resume_projects_only_exact_receipts(tmp_path):
    journal = CommandJournal("project", "gateway", root=tmp_path / "journal")
    report = ApplyReport()
    report.bind(journal, resumed=True)
    with journal:
        journal.begin(plan())
        journal.started("guest")
        report.started = True
        report.failed()
        assert report.mapping()["pending"] == ("guest",)
        journal.verified("guest", {"request": "exact"})
        journal.complete()
        report.verified(changed=True)
        payload = report.mapping()
        assert payload["outcome"] == "resumed"
        assert payload["completed"] == ("guest",)
        assert payload["pending"] == ()


def test_historical_receipt_does_not_certify_new_invocation(tmp_path):
    journal = CommandJournal("project", "gateway", root=tmp_path / "journal")
    with journal:
        journal.begin(plan())
        journal.verified("guest", {"request": "old"})
        journal.complete()
    report = ApplyReport()
    report.bind(journal, resumed=False)
    report.started = True  # Interrupted before the new envelope was published.
    report.failed()
    assert report.mapping()["completed"] == ()
    assert report.mapping()["configuration"] == "unknown"


def test_writer_lock_refusal_is_one_json_result_without_health_claims(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("competing apply reached its execution owner")

    monkeypatch.setattr(cli, "_apply_impl", forbidden)
    with cli.VMHAApplyLock(project_id="project-test", gateway_name="gateway"):
        result = CliRunner().invoke(cli.app, ["apply", "--output-format", "json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "blocked"
    assert payload["authority"] == "unknown"
    assert "another writer" in result.stderr


def test_configuration_refusal_is_structured_before_any_execution(monkeypatch):
    def invalid(path):
        raise ValueError("invalid fixture")

    monkeypatch.setattr(cli, "load_local_config", invalid)
    result = CliRunner().invoke(cli.app, ["apply", "--output-format", "json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["outcome"] == "blocked"


@pytest.mark.parametrize("execution_exit", [None, 7])
def test_unreadable_receipt_keeps_json_and_preserves_existing_failure(monkeypatch, execution_exit):
    def unavailable():
        raise OSError("receipt unavailable")

    def apply(**kwargs):
        report = kwargs["result_report"]
        report.journal = SimpleNamespace(snapshot=unavailable)
        report.started = True
        if execution_exit is not None:
            raise typer.Exit(execution_exit)
        report.verified(changed=True)

    monkeypatch.setattr(cli, "_apply_impl", apply)
    result = CliRunner().invoke(cli.app, ["apply", "--output-format", "json"])
    assert result.exit_code == (execution_exit or 1)
    payload = json.loads(result.stdout)
    assert payload["outcome"] == "failed"
    assert payload["completed"] == []
    assert payload["configuration"] == payload["authority"] == "unknown"


def test_text_default_keeps_existing_output(monkeypatch):
    monkeypatch.setattr(cli, "_apply_impl", lambda **kwargs: typer.echo("existing output"))
    result = CliRunner().invoke(cli.app, ["apply"])
    assert result.exit_code == 0
    assert result.stdout == "existing output\n"
