"""Grafana setup runs normal current-input rendering and deployment."""

import copy
from contextlib import nullcontext

import pytest
import yaml

from nebius_cxcli import cli, config_loader, deployment_cli
from nebius_cxcli import observability_installation as owner
from nebius_cxcli.config_model import to_dynamic_payload, to_runtime_payload
from nebius_cxcli.grafana_install import configure
from test_observability_routing import payload


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    data = configure(to_dynamic_payload(to_runtime_payload(payload())), "cluster")
    path = tmp_path / "config.yaml"
    config_loader.normalize_runtime_config_payload(data, base_dir=path.parent)
    path.write_text(yaml.safe_dump(to_dynamic_payload(data)))

    def load(p, **kwargs):
        assert kwargs.get("persist_normalized") is False
        value = yaml.safe_load(p.read_text())
        config_loader.normalize_runtime_config_payload(value, base_dir=p.parent)
        return to_runtime_payload(value)

    monkeypatch.setattr(config_loader, "load_config", load)
    monkeypatch.setattr(deployment_cli, "deployment_execution", lambda **kw: nullcontext())
    calls = []

    def save(p, candidate, **kwargs):
        assert p.read_bytes() == kwargs["expected_bytes"]
        p.write_text(yaml.safe_dump(candidate))
        calls.append("save")

    monkeypatch.setattr(cli, "_write_runtime_payload_config", save)
    monkeypatch.setattr(cli, "render_command", lambda **kw: calls.append(("render", kw)))
    monkeypatch.setattr(cli, "deploy_command", lambda **kw: calls.append(("deploy", kw)))
    return path, data, calls


def invoke(path, data, *, expected=None):
    owner.install_observability(
        path,
        "cluster",
        data,
        expected_bytes=path.read_bytes() if expected is None else expected,
        emit=lambda _: None,
    )


def test_unchanged_install_always_renders_and_runs_normal_deploy(lifecycle):
    path, data, calls = lifecycle
    before = path.read_bytes()
    invoke(path, data)
    assert path.read_bytes() == before
    assert calls == [
        ("render", {"config_path": path, "force": True}),
        ("deploy", {"config_path": path}),
    ]


def test_changed_settings_are_saved_before_render_and_deploy(lifecycle):
    path, data, calls = lifecycle
    candidate = configure(data, "cluster", overrides={"pushgateway": True})
    invoke(path, candidate)
    assert calls[0] == "save"
    assert [call[0] for call in calls[1:]] == ["render", "deploy"]
    calls.clear()
    invoke(path, candidate)
    assert [call[0] for call in calls] == ["render", "deploy"]


def test_pending_infrastructure_is_left_for_normal_deployment_plan(lifecycle):
    path, data, calls = lifecycle
    data = copy.deepcopy(data)
    data["infra"]["components"][0].setdefault("inputs", {})["pending"] = "current"
    path.write_text(yaml.safe_dump(to_dynamic_payload(data)))
    invoke(path, data)
    assert [call[0] for call in calls] == ["render", "deploy"]


def test_configuration_race_saves_nothing(lifecycle):
    path, data, calls = lifecycle
    before = path.read_bytes()
    path.write_bytes(before + b"\n# newer edit\n")
    with pytest.raises(RuntimeError, match="Config changed"):
        invoke(path, data, expected=before)
    assert not calls and path.read_bytes().endswith(b"# newer edit\n")


def test_wizard_cannot_change_unrelated_settings(lifecycle):
    path, data, calls = lifecycle
    candidate = copy.deepcopy(data)
    candidate["apps"]["charts"].append({"id": "unrelated", "enabled": True})
    with pytest.raises(RuntimeError, match="outside"):
        invoke(path, candidate)
    assert not calls


def test_failed_deploy_keeps_saved_desired_settings(lifecycle, monkeypatch):
    path, data, calls = lifecycle
    candidate = configure(data, "cluster", overrides={"pushgateway": True})

    def fail(**kwargs):
        raise RuntimeError("apply failed")

    monkeypatch.setattr(cli, "deploy_command", fail)
    with pytest.raises(RuntimeError, match="apply failed"):
        invoke(path, candidate)
    assert calls[0] == "save" and calls[1][0] == "render"
    assert "prometheus-pushgateway" in path.read_text()
