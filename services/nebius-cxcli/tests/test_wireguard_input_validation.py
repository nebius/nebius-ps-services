"""WireGuard validation agrees with its Terraform module before render/deploy."""

import re

import pytest

from nebius_cxcli.runtime_component_validation import _validate_wireguard


def validate(**overrides):
    inputs = {
        "enabled": True,
        "name": "vpn",
        "ssh_user_name": "ubuntu",
        "local_subnets": [],
        **overrides,
    }
    _validate_wireguard(
        {"vpn": inputs},
        lambda payload, path, default=None: payload["vpn"].get(path.removeprefix("vpn."), default),
        lambda value: str(value or "").strip(),
        "vpn",
        re.compile(r"^[a-z0-9][a-z0-9-]*$"),
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("ssh_user_name", "root"),
        ("ssh_user_name", "admin"),
        ("wireguard_listen_port", True),
        ("wireguard_listen_port", 51820.5),
        ("client_default_persistent_keepalive", -1),
        ("client_default_persistent_keepalive", 65536),
        ("client_default_persistent_keepalive", 1.5),
        ("client_default_persistent_keepalive", True),
    ],
)
def test_invalid_wizard_values_rejected_before_terraform(field, value):
    with pytest.raises(ValueError, match=field):
        validate(**{field: value})


@pytest.mark.parametrize("port,keepalive", [(1, 0), (65535, 65535), (51820.0, 25.0)])
def test_valid_integer_boundaries(port, keepalive):
    validate(wireguard_listen_port=port, client_default_persistent_keepalive=keepalive)


def test_full_wireguard_module_wizard_matches_catalog_and_persists_defaults(monkeypatch):
    from dataclasses import replace
    from pathlib import Path

    import yaml

    from nebius_cxcli import cli, wireguard_subnets
    from nebius_cxcli.components import component_entries
    from nebius_cxcli.provider_options import OptionChoice, ProviderOptionLookup

    source = str(Path(__file__).resolve().parents[3] / "platform-infra/modules/wireguard-gw")
    entry = replace(
        next(item for item in component_entries("infra") if item.id == "wireguard-gw"),
        source=source,
        metadata_source=source,
    )
    specs = cli.module_variables(source)
    assert len(specs) > 20  # Exercise the whole real module, not a selected fixture subset.
    fields = []
    providers = []

    def resolve(self, *, provider, **kwargs):
        providers.append(provider)
        self._last_vpc_complete = True
        values = {
            "project_networks": "network",
            "project_subnets": "subnet",
            "compute_platforms": "cpu-d3",
            "compute_platform_presets": "2vcpu-8gb",
            "compute_public_image_families": "ubuntu24.04-driverless",
            "compute_boot_disk_types": "NETWORK_SSD",
            "wireguard_admin_groups": "admins",
        }
        value = values[provider]
        return [
            OptionChoice(value, value, metadata={"parent_id": "project", "network_id": "network"})
        ]

    def scalar(label, current, **kwargs):
        fields.append(label)
        if label.endswith(".name"):
            return "vpn", False
        if label.endswith(".access.admin_group_id"):
            return "admins", False
        choices = kwargs.get("choices") or []
        return (choices[0].value if choices else current), False

    monkeypatch.setattr(ProviderOptionLookup, "resolve", resolve)
    monkeypatch.setattr(cli, "_prompt_scalar_override", scalar)
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *a, **k: True)
    monkeypatch.setattr(cli, "_is_tty_session", lambda: False)
    monkeypatch.setattr(cli.typer, "prompt", lambda *a, **k: k.get("default", ""))
    monkeypatch.setattr(
        wireguard_subnets,
        "live_subnet_choices",
        lambda *a: [OptionChoice("10.20.0.0/24", "workers")],
    )
    config = {
        "version": "v2",
        "client_info": {"nebius": {"project_id": "project", "tenant_id": "tenant"}},
        "infra": {
            "components": [
                {
                    "id": "wireguard-gw",
                    "instance_id": "vpn",
                    "enabled": True,
                    "inputs": {"parent_id": "project", "name": "vpn"},
                }
            ]
        },
        "apps": {"charts": []},
    }
    updated, complete = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(config),
        selected_infra={"vpn"},
        selected_apps=set(),
        infra_entries=(entry,),
        app_entries=(),
        provider_lookup=ProviderOptionLookup(),
    )
    assert complete
    row = yaml.safe_load(updated)["infra"]["components"][0]
    assert row["access"]["admin_group_id"] == "admins"
    assert row["inputs"]["wireguard_tunnel_cidr"] == "10.8.0.1/22"
    assert row["inputs"]["local_subnets"] == []
    assert row["inputs"]["create_public_ip_allocation"] is True
    assert row["inputs"]["client_default_persistent_keepalive"] == 25
    assert row["inputs"]["nat_mode"] is True
    for hidden in (
        "access_bootstrap",
        "clients",
        "endpoint_host",
        "labels",
        "boot_disk_block_size_bytes",
    ):
        assert not any(label.endswith("." + hidden) for label in fields)
    assert set(providers) >= {
        "project_networks",
        "project_subnets",
        "wireguard_admin_groups",
        "compute_platforms",
        "compute_platform_presets",
    }


@pytest.mark.parametrize("metadata_only", [False, True])
def test_secret_container_validation_respects_version_owner(metadata_only):
    from nebius_cxcli.runtime_component_validation import _validate_mysterybox_secrets

    def check(secrets):
        return _validate_mysterybox_secrets(
            secrets,
            enabled=True,
            metadata_only=metadata_only,
            secrets_path="secrets",
            enabled_label="enabled",
            as_text=lambda x: str(x or ""),
            id_pattern=re.compile(r"^[a-z0-9-]+$"),
        )

    if metadata_only:
        check([{"name": "vpn-login"}])
        with pytest.raises(ValueError, match="unsupported"):
            check([{"name": "vpn-login", "payload": {"key": {"type": "text"}}}])
    else:
        with pytest.raises(ValueError, match="payload"):
            check([{"name": "app"}])
        check([{"name": "app", "payload": {"key": {"type": "text"}}}])
    with pytest.raises(ValueError, match="unique"):
        check(
            [{"name": "same", **({} if metadata_only else {"payload": {"key": {"type": "text"}}})}]
            * 2
        )
