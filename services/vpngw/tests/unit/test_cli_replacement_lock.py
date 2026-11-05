from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.deploy import (
    replacement,
    replacement_ha,
    replacement_ordinary,
    replacement_trust,
)
from nebius_vpngw.deploy.vm_ha_lifecycle import VMHAApplyLock


@pytest.mark.parametrize("ha", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_public_replacement_retains_coordinator_lock_without_reacquiring(
    monkeypatch, tmp_path, ha, resume
):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    config_path = tmp_path / "config.yaml"
    config_path.write_text("# Configuration is supplied by the isolated loader.\n")
    bundle = tmp_path / "recovery.json"
    bundle.write_text("{}\n")
    local = {"project_id": "project-test"}
    plan = SimpleNamespace(
        vm_ha=object() if ha else None,
        gateway_group=SimpleNamespace(name="gateway-test"),
    )
    manager = SimpleNamespace(project_id=local["project_id"])
    monkeypatch.setattr(cli, "load_local_config", lambda path: local)
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *args: plan)
    monkeypatch.setattr(cli, "_gateway_ssh_trust_scope", lambda *args, **kwargs: "scope")
    monkeypatch.setattr(cli, "_approve_disruption", lambda *args, **kwargs: True)
    monkeypatch.setattr(replacement_trust, "preview", Mock())
    monkeypatch.setattr(replacement_trust, "resolve", Mock())

    journal = Mock(bundle=bundle)
    journal_context = Mock()
    journal_context.__enter__ = Mock(return_value=journal)
    journal_context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(replacement, "Journal", Mock(return_value=journal_context))
    ports = Mock()
    ports.inspect.return_value = SimpleNamespace(members=(), routes=(), mapping=lambda: {})
    monkeypatch.setattr(replacement_ordinary, "OrdinaryReplacement", Mock(return_value=ports))
    monkeypatch.setattr(replacement_ha, "HAReplacement", Mock(return_value=ports))

    def lock():
        return VMHAApplyLock(project_id="project-test", gateway_name="gateway-test")

    def run(current_journal, current_ports, *, checkpoint):
        assert current_journal is journal
        assert current_ports is ports
        # A competing writer must remain excluded throughout replacement.
        with pytest.raises(RuntimeError, match="another gateway apply owns"), lock():
            pytest.fail("replacement lost its coordinator lock")

    runner = Mock(side_effect=run)
    monkeypatch.setattr(replacement, "run", runner)

    def apply_impl(**kwargs):
        cli._execute_gateway_replacement(
            manager=manager,
            ssh=SimpleNamespace(),
            plan=plan,
            local=local,
            instances=(),
            dry_run=kwargs["dry_run"],
            recreate=kwargs["recreate_gw"],
            approval=kwargs["approve_disruption"],
            config_path=kwargs["local_config_file"],
            resume=kwargs["resume_replacement"],
        )

    monkeypatch.setattr(cli, "_apply_impl", apply_impl)
    arguments = ["apply", "--local-config-file", str(config_path)]
    arguments += ["--resume-replacement", str(bundle)] if resume else ["--recreate-gw"]
    result = CliRunner().invoke(cli.app, arguments)

    assert result.exit_code == 0, (result.output, result.exception)
    runner.assert_called_once()
    if resume:
        journal.import_bundle.assert_called_once_with(bundle)
        journal.start.assert_not_called()
    else:
        journal.start.assert_called_once()
        journal.import_bundle.assert_not_called()
    # The public coordinator releases ownership once the operation returns.
    with lock():
        pass
