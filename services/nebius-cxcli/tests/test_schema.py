from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from nebius_cxcli.components import component_entries
from nebius_cxcli.config_loader import load_config
from nebius_cxcli.config_template import starter_config_yaml

_VALID_ED25519_PUBLIC_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f demo@example"
)


def _dynamic_payload() -> dict:
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            email="ops@example.com",
            infra_entries=component_entries("infra"),
            app_entries=component_entries("apps"),
        )
    )
    assert isinstance(payload, dict)
    return payload


def test_schema_valid_dynamic_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    payload = _dynamic_payload()
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    loaded = load_config(config_path)
    assert loaded.version == "v1"
    assert loaded.client_info.client_name == "client-a"
    assert isinstance(loaded.infra.components, list)
    assert isinstance(loaded.apps.charts, list)


def test_schema_accepts_ssh_public_key_local_file_path(tmp_path: Path) -> None:
    key_path = tmp_path / "id_ed25519.pub"
    key_path.write_text(_VALID_ED25519_PUBLIC_KEY + "\n", encoding="utf-8")

    payload = _dynamic_payload()
    payload["infra"]["components"] = [
        {
            "id": "wireguard-jumphost",
            "instance_id": "wireguard-jumphost",
            "enabled": True,
            "inputs": {
                "ssh_user_name": "ubuntu",
                "ssh_public_key": "./id_ed25519.pub",
            },
        }
    ]
    payload.pop("deploy", None)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    loaded = load_config(config_path)
    component = loaded.infra.components[0]
    assert component["inputs"]["ssh_public_key"] == _VALID_ED25519_PUBLIC_KEY


def test_schema_rejects_static_shape(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "version": "v1",
                "client_info": {"client_name": "x"},
                "infra": {"mk8s": {"enabled": True}},
                "apps": {"workloads": {}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "dynamic model" in str(exc_info.value)


def test_schema_rejects_duplicate_infra_component_ids(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    components = payload["infra"]["components"]
    assert isinstance(components, list)
    components.append({"id": "mk8s", "instance_id": "mk8s", "enabled": True, "inputs": {}})

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "is duplicated" in str(exc_info.value)


def test_schema_allows_duplicate_component_types_with_unique_instance_ids(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    components = payload["infra"]["components"]
    assert isinstance(components, list)
    components.append(
        {
            "id": "mk8s",
            "instance_id": "mk8s-secondary",
            "enabled": True,
            "inputs": {},
        }
    )

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    loaded = load_config(config_path)
    assert len(loaded.infra.components) == len(components)
    assert loaded.infra.mk8s.enabled is True
    assert loaded.infra.mk8s_secondary.enabled is True


def test_schema_rejects_invalid_chart_group_token(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    charts = payload["apps"]["charts"]
    assert isinstance(charts, list)
    charts[0]["group"] = "bad group"

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "group must use lowercase letters, digits, and hyphens" in str(exc_info.value)


def test_schema_rejects_release_name_alias_in_app_chart(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    charts = payload["apps"]["charts"]
    assert isinstance(charts, list)
    release_name = charts[0].pop("release-name")
    charts[0]["release_name"] = release_name

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "apps.charts[0] has unsupported field(s): release_name" in str(exc_info.value)


def test_schema_rejects_unknown_root_key(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["unknown"] = True

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "unknown field(s) at root" in str(exc_info.value)


def test_schema_rejects_top_level_observability_contract(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["observability"] = {"enabled": True}

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "unknown field(s) at root: observability" in str(exc_info.value)


def test_schema_rejects_shared_root_key(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["shared"] = {
        "admin_ssh": {"user_name": "ubuntu", "public_key": _VALID_ED25519_PUBLIC_KEY}
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "unknown field(s) at root: shared" in str(exc_info.value)


def test_schema_rejects_legacy_client_info_fields(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    client_info = payload.get("client_info")
    assert isinstance(client_info, dict)
    client_info["env"] = "dev"
    client_info["cluster_name"] = "cluster-a"

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "client_info has unsupported field(s)" in str(exc_info.value)


def test_schema_rejects_legacy_mk8s_gpu_validation_overrides_input(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "cluster-a",
            "enabled": True,
            "inputs": {
                "cluster_name": "cluster-a",
                "parent_id": "project-456",
                "subnet_id": "subnet-123",
                "gpu_enabled": True,
                "gpu_node_groups": 1,
                "gpu_nodes_count_per_group": 1,
                "gpu_nodes_platform": "gpu-h100-sxm",
                "gpu_nodes_preset": "8gpu-128vcpu-1600gb",
                "gpu_validation_overrides": {
                    "operator_readiness": {"enabled": False},
                },
            },
        }
    ]
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "cluster-a",
            "enabled": True,
            "values": {},
        }
    ]

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "gpu_validation_overrides is no longer supported" in str(exc_info.value)


def test_schema_rejects_root_mk8s_gpu_deploy_validations(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["deploy"] = {
        "validations": {
            "mk8s_gpu": {
                "operator_readiness": {
                    "enabled": True,
                },
            },
        },
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "deploy has unsupported field(s): validations" in str(exc_info.value)


def test_schema_rejects_missing_infra_instance_id(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    components = payload["infra"]["components"]
    assert isinstance(components, list)
    components[0].pop("instance_id", None)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "infra.components[0].instance_id is required" in str(exc_info.value)


def test_schema_rejects_missing_app_instance_id(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "enabled": True,
            "values": {},
        }
    ]

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "apps.charts[0].instance_id is required" in str(exc_info.value)


def test_schema_rejects_app_chart_target_ref(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "cluster-a",
            "enabled": True,
            "inputs": {},
        }
    ]
    payload["deploy"] = {"targets": [{"instance_id": "cluster-a"}]}
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "cluster-a",
            "enabled": True,
            "target_ref": "cluster-a",
            "values": {},
        }
    ]

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "apps.charts[0] has unsupported field(s): target_ref" in str(exc_info.value)


def test_schema_rejects_target_bound_app_instance_id_mismatch(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["infra"]["components"] = [
        {
            "id": "mk8s",
            "instance_id": "cluster-a",
            "enabled": True,
            "inputs": {},
        }
    ]
    payload["deploy"] = {"targets": [{"instance_id": "cluster-a"}]}
    payload["apps"]["charts"] = [
        {
            "id": "nvidia-gpu-operator",
            "instance_id": "nvidia-gpu-operator",
            "enabled": True,
            "values": {},
        }
    ]

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "apps.charts[0].instance_id must reference one of the enabled cluster targets" in str(
        exc_info.value
    )


def test_schema_rejects_root_kubernetes_observability(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["deploy"] = {
        "observability": {
            "enabled": True,
            "kubernetes": {"metrics": {"enabled": True}},
        }
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "deploy.observability is only supported for enabled infra:vm components" in str(
        exc_info.value
    )


def test_schema_rejects_root_kubernetes_observability_with_vm(tmp_path: Path) -> None:
    payload = _dynamic_payload()
    payload["infra"]["components"] = [
        {
            "id": "vm",
            "instance_id": "vm",
            "enabled": True,
            "inputs": {},
        }
    ]
    payload["deploy"] = {
        "observability": {
            "enabled": True,
            "kubernetes": {"metrics": {"enabled": True}},
        }
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_config(config_path)
    assert "deploy.observability has unsupported field(s): kubernetes" in str(exc_info.value)
