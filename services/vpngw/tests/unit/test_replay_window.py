from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml
from pydantic import ValidationError

from nebius_vpngw import replay_window
from nebius_vpngw.agent import strongswan_renderer
from nebius_vpngw.agent.state_store import StateStore
from nebius_vpngw.config_loader import load_local_config, merge_with_peer_configs
from nebius_vpngw.config_wizard import _connection_phase, _Prompter, _validate_replay_window
from nebius_vpngw.deploy import ordinary_apply
from nebius_vpngw.deploy.ssh_push import SSHPush, VMHAAgentArtifact
from nebius_vpngw.schema import VPNGatewayConfig
from nebius_vpngw.vm_ha_config_wizard import _derive_member_one_tunnels


def tunnel(config):
    return config["connections"][0]["tunnels"][0]


@pytest.mark.parametrize("value", [0, 31, 1025, -1, True, False, None, "1024", 32.0, 1.5])
def test_replay_window_rejects_non_strict_or_out_of_range(sample_config, value):
    tunnel(sample_config)["replay_window"] = value
    with pytest.raises(ValidationError, match="replay_window"):
        VPNGatewayConfig.model_validate(sample_config)


@pytest.mark.parametrize("value", [32, 33, 64, 511, 1000, 1024])
def test_explicit_window_survives_loader_resolution_render_and_hash(sample_config, tmp_path, value):
    path = tmp_path / "input.yaml"
    path.write_text(yaml.safe_dump(sample_config))
    legacy = load_local_config(path)
    legacy_resolved = next(merge_with_peer_configs(legacy, []).iter_instance_configs()).config_yaml
    assert "replay_window" not in legacy_resolved
    tunnel(sample_config)["replay_window"] = value
    path.write_text(yaml.safe_dump(sample_config))
    current = load_local_config(path)
    assert tunnel(current)["replay_window"] == value
    instance = next(merge_with_peer_configs(current, []).iter_instance_configs())
    resolved = yaml.safe_load(instance.config_yaml)
    renderer = strongswan_renderer.StrongSwanRenderer()
    files = {}
    renderer.render_and_apply(resolved, activate=False, rendered_files=files)
    assert f"        replay_window = {value}\n" in files[strongswan_renderer.SWANCTL_CONF]
    state = StateStore(tmp_path / "state.json")
    assert state._hash_cfg(resolved) != state._hash_cfg(yaml.safe_load(legacy_resolved))
    assert tunnel(VPNGatewayConfig.model_validate(current).model_dump())["replay_window"] == value


def test_omission_round_trips_without_new_keys_or_render_change(sample_config, tmp_path):
    normalized = VPNGatewayConfig.model_validate(sample_config).model_dump(mode="json")
    assert "replay_window" not in tunnel(normalized)
    round_trip = VPNGatewayConfig.model_validate(normalized).model_dump(
        mode="json", exclude_none=False
    )
    assert normalized == round_trip
    before = next(merge_with_peer_configs(normalized, []).iter_instance_configs()).config_yaml
    after = next(merge_with_peer_configs(round_trip, []).iter_instance_configs()).config_yaml
    assert before == after
    files = {}
    strongswan_renderer.StrongSwanRenderer().render_and_apply(
        yaml.safe_load(after), activate=False, rendered_files=files
    )
    assert "replay_window" not in files[strongswan_renderer.SWANCTL_CONF]


@pytest.mark.parametrize("value", [None, 32, 1024])
def test_wizard_enter_preserves_presence_and_explicit_selection(sample_config, value):
    if value is not None:
        tunnel(sample_config)["replay_window"] = value

    class Defaults:
        console = Mock()

        def ask(self, label, *, default=None, validator=None, **kw):
            return validator(default) if validator else default

        def ask_int(self, label, *, default, **kw):
            return default

        def ask_choice(self, label, choices, *, default, **kw):
            return default

        def ask_bool(self, label, *, default, **kw):
            return default

    _connection_phase(sample_config, Defaults())
    assert tunnel(sample_config).get("replay_window") == value
    assert ("replay_window" in tunnel(sample_config)) == (value is not None)


@pytest.mark.parametrize("bad", ["31", "1025", "32.0", "true", "null", "-1", "１024"])
def test_wizard_reprompts_invalid_window(bad):
    with pytest.raises(ValueError):
        _validate_replay_window(bad)


@pytest.mark.parametrize("value", [None, 32, 1024])
def test_ha_conversion_copies_receiver_local_field(sample_config, value):
    if value is not None:
        tunnel(sample_config)["replay_window"] = value
    prompt = Mock(spec=_Prompter)
    prompt.ask.side_effect = lambda label, default, **kw: default
    prompt.ask_bool.return_value = True
    derived = _derive_member_one_tunnels(sample_config, sample_config, prompt)
    assert derived[0][0].get("replay_window") == value
    assert ("replay_window" in derived[0][0]) == (value is not None)


def test_ha_members_may_select_independent_windows_and_change_generation(sample_config):
    group = sample_config["gateway_group"]
    group["instance_count"] = 2
    group["external_ips"] = [["203.0.113.10"], ["203.0.113.11"]]
    group["vm_ha"] = {
        "enabled": True,
        "cluster_id": "sample-cluster",
        "members": [
            {"node_id": "gateway-a", "instance_index": 0, "role": "active"},
            {"node_id": "gateway-b", "instance_index": 1, "role": "passive"},
        ],
    }
    other = dict(tunnel(sample_config), name="tunnel-2", gateway_instance_index=1)
    sample_config["connections"][0]["tunnels"].append(other)
    before = merge_with_peer_configs(
        VPNGatewayConfig.model_validate(sample_config).model_dump(mode="json"), []
    )
    tunnel(sample_config)["replay_window"] = 1024
    other["replay_window"] = 32
    after = merge_with_peer_configs(
        VPNGatewayConfig.model_validate(sample_config).model_dump(mode="json"), []
    )
    assert before.vm_ha.generation.generation_id != after.vm_ha.generation.generation_id
    assert [
        tunnel(yaml.safe_load(item.config_yaml))["replay_window"]
        for item in after.iter_instance_configs()
    ] == [1024, 32]


def wheel(tmp_path, *, stale_source=None):
    root = Path(replay_window.__file__).parent
    members = {
        "nebius_vpngw/" + p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    if stale_source:
        members["nebius_vpngw/" + stale_source] += b"\n# older implementation\n"
    dist = "nebius_vpngw-1.2.3.dist-info/"
    members[dist + "METADATA"] = b"Metadata-Version: 2.4\nName: nebius-vpngw\nVersion: 1.2.3\n"
    members[dist + "WHEEL"] = b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    records = []
    for name, content in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        records.append(f"{name},sha256={digest},{len(content)}")
    records.append(dist + "RECORD,,")
    members[dist + "RECORD"] = ("\n".join(records) + "\n").encode()
    path = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return path


def test_matching_artifact_proves_capability_and_ordinary_support(tmp_path, sample_config):
    path = wheel(tmp_path)
    artifact = VMHAAgentArtifact.from_wheel(path, source="test")
    assert replay_window.REPLAY_WINDOW_CAPABILITY in artifact.capabilities
    tunnel(sample_config)["replay_window"] = 1024
    ordinary_apply.artifact(path, yaml.safe_dump(sample_config))
    artifact.verify_current()


@pytest.mark.parametrize("value", [32, 1024])
@pytest.mark.parametrize(
    "stale", ["schema.py", "config_loader.py", "agent/strongswan_renderer.py", "agent/main.py"]
)
def test_stale_artifact_fails_explicit_window_before_package_effects(
    tmp_path, sample_config, value, stale, monkeypatch
):
    path = wheel(tmp_path, stale_source=stale)
    artifact = VMHAAgentArtifact.from_wheel(path, source="test")
    assert replay_window.REPLAY_WINDOW_CAPABILITY not in artifact.capabilities
    # Omitted configurations retain the existing HA capability contract.
    replay_window.require_replay_window_capability(sample_config, artifact.capabilities)
    tunnel(sample_config)["replay_window"] = value
    with pytest.raises(RuntimeError, match="replay_window"):
        ordinary_apply.artifact(path, yaml.safe_dump(sample_config))
    push = SSHPush(ssh_policy=object())
    effects = Mock(side_effect=AssertionError("No SSH effects permitted"))
    monkeypatch.setattr(push, "_ensure_paramiko", effects)
    instance = SimpleNamespace(config_yaml=yaml.safe_dump(sample_config))
    with pytest.raises(RuntimeError, match="replay_window"):
        push.ensure_vm_ha_agent_package("192.0.2.1", instance, sample_config, artifact=artifact)
    effects.assert_not_called()


def test_omitted_ordinary_config_does_not_add_replay_capability_gate(tmp_path, sample_config):
    ordinary_apply.artifact(
        wheel(tmp_path, stale_source="schema.py"), yaml.safe_dump(sample_config)
    )


@pytest.mark.parametrize("local_window", [None, 32, 1024])
def test_peer_import_cannot_override_receive_window(sample_config, monkeypatch, local_window):
    from nebius_vpngw import config_loader

    if local_window is not None:
        tunnel(sample_config)["replay_window"] = local_window
    peer = {"vendor": "generic", "tunnels": [dict(tunnel(sample_config), replay_window=128)]}
    monkeypatch.setattr(config_loader, "_parse_peer_file", lambda path: peer)
    merged = config_loader.merge_peer_configs_into_local_config(
        sample_config, [Path("peer.txt")], prefer_peer=True
    )
    assert tunnel(merged).get("replay_window") == local_window
    resolved = next(
        config_loader.merge_with_peer_configs(
            sample_config, [Path("peer.txt")]
        ).iter_instance_configs()
    )
    assert tunnel(yaml.safe_load(resolved.config_yaml)).get("replay_window") == local_window


def test_early_ordinary_admission_pins_exact_private_wheel_until_deployment(
    tmp_path, sample_config
):
    from contextlib import ExitStack

    selected = wheel(tmp_path)
    tunnel(sample_config)["replay_window"] = 32
    instances = tuple(merge_with_peer_configs(sample_config, []).iter_instance_configs())
    ssh = SimpleNamespace(_build_wheel=lambda: selected)
    with ExitStack() as lifetime:
        ordinary_apply.pin_replay_window_artifact(ssh, instances, sample_config, lifetime)
        pinned = ssh._wheel_path
        assert pinned != selected
        original = selected.read_bytes()
        selected.write_bytes(b"later replacement")
        assert pinned.read_bytes() == original
        assert pinned.stat().st_mode & 0o777 == 0o600
        ordinary_apply.artifact(pinned, instances[0].config_yaml)
    assert not pinned.exists()
