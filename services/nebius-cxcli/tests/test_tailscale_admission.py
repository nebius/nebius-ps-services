from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace as NS

import pytest

from nebius_cxcli import cli, nat_gateway_deploy
from test_tailscale import row


@pytest.mark.parametrize("configured", ["removed", "disabled", "enabled"])
def test_direct_apply_rejects_tailscale_in_state(tmp_path, monkeypatch, configured):
    value = row()
    value["enabled"] = configured == "enabled"
    config = {"infra": {"components": [] if configured == "removed" else [value]}}
    applied = _direct_apply_fixture(tmp_path, monkeypatch, tailscale=True)
    with pytest.raises(RuntimeError, match="Tailscale.*ordinary"):
        cli._run_terraform_apply_with_status(
            config, NS(infra_dir=tmp_path), initialize=False, run_mk8s_preflight=False
        )
    assert not applied


def test_direct_apply_allows_state_without_tailscale(tmp_path, monkeypatch):
    applied = _direct_apply_fixture(tmp_path, monkeypatch, tailscale=False)
    cli._run_terraform_apply_with_status(
        {"infra": {"components": []}},
        NS(infra_dir=tmp_path),
        initialize=False,
        run_mk8s_preflight=False,
    )
    assert applied == [tmp_path]


def _direct_apply_fixture(tmp_path, monkeypatch, *, tailscale):
    monkeypatch.setattr(cli, "_terraform_runtime_env", lambda _: {})
    monkeypatch.setattr(nat_gateway_deploy, "guard_direct_mutation", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_validate_mysterybox_runtime_payload_values", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_raise_on_terraform_gpu_fabric_drift", lambda *a, **k: None)
    monkeypatch.setattr(
        cli,
        "terraform_show_json",
        lambda *a, **k: {
            "values": {
                "root_module": {
                    "resources": [
                        {
                            "type": "nebius_compute_v1_instance",
                            "values": {
                                "id": "vm-test",
                                "labels": {"cxcli-component": "tailscale-gw"} if tailscale else {},
                            },
                        }
                    ]
                }
            }
        },
    )
    monkeypatch.setattr(
        cli,
        "deployment_status_reporting",
        lambda *a, **k: nullcontext(NS(handle_terraform_event=lambda _: None)),
    )
    applied = []
    monkeypatch.setattr(cli, "terraform_apply", lambda path, **k: applied.append(path))
    return applied
