from __future__ import annotations

import pytest
import yaml

from nebius_cxcli.components import component_entries
from nebius_cxcli.config_loader import validate_config
from nebius_cxcli.config_template import starter_config_yaml
from nebius_cxcli.mysterybox_eso import (
    MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND,
    materialize_mysterybox_eso_app_values,
    mysterybox_eso_api_domains,
    mysterybox_eso_app_target_refs,
    mysterybox_eso_dependency_issues,
    mysterybox_eso_extra_objects_for_target,
    mysterybox_eso_manages_kubernetes_secret,
    mysterybox_eso_terraform_output_specs,
    mysterybox_eso_validation_specs,
)


def _starter_payload(
    selected_infra: set[str] | None = None,
    selected_apps: set[str] | None = None,
) -> dict:
    payload = yaml.safe_load(
        starter_config_yaml(
            client_name="client-a",
            tenant_id="tenant-123",
            project_id="project-456",
            region_id="eu-north1",
            email="ops@example.com",
            selected_infra=selected_infra or {"mk8s", "mysterybox"},
            selected_apps=selected_apps or set(),
            infra_entries=component_entries("infra"),
            app_entries=component_entries("apps"),
        )
    )
    assert isinstance(payload, dict)
    return payload


def _mysterybox_component(payload: dict, instance_id: str = "mysterybox") -> dict:
    for item in payload["infra"]["components"]:
        if item["id"] == "mysterybox" and item["instance_id"] == instance_id:
            return item
    raise AssertionError(f"Missing mysterybox component {instance_id}")


def _set_mysterybox_inputs(
    payload: dict,
    *,
    instance_id: str = "mysterybox",
    secrets: list[dict] | None = None,
) -> None:
    _mysterybox_component(payload, instance_id)["inputs"] = {
        "parent_id": "project-456",
        "secrets": secrets
        or [
            {
                "name": "db-uname-pass",
                "version_id": "n/a",
                "payload": {
                    "USERNAME": {"type": "text"},
                    "PASSWORD": {"type": "text"},
                },
            },
            {
                "name": "api-key",
                "version_id": "n/a",
                "payload": {"APIKEY": {"type": "text"}},
            },
        ],
    }


def _enable_mysterybox_eso(
    payload: dict,
    *,
    allow_all_namespaces: bool = True,
    sync_namespaces: list[str] | None = None,
) -> None:
    payload["deploy"]["targets"][0]["secrets"] = {
        "mysterybox": {
            "enabled": True,
            "allow_all_namespaces": allow_all_namespaces,
            "sync_namespaces": ["default"] if sync_namespaces is None else sync_namespaces,
        }
    }


def _external_secrets_app(config: dict) -> dict:
    return next(
        item
        for item in config["apps"]["charts"]
        if item["id"] == "external-secrets" and item["enabled"]
    )


def _extra_objects(config: dict, component_output_values: dict | None = None) -> list[dict]:
    return mysterybox_eso_extra_objects_for_target(
        config,
        target_ref="mk8s",
        component_output_values=component_output_values
        or {
            "mysterybox.secret_ids": {
                "db-uname-pass": "mbsec-e00db",
                "api-key": "mbsec-e00api",
                "app-config": "mbsec-e00app",
            }
        },
    )


def test_mysterybox_backend_with_mk8s_auto_enables_external_secrets_controller() -> None:
    payload = _starter_payload(selected_infra={"mk8s", "mysterybox"})
    _set_mysterybox_inputs(payload)

    config = validate_config(payload)
    external_secret_rows = [
        item for item in config["apps"]["charts"] if item["id"] == "external-secrets"
    ]
    external_secrets = next(item for item in external_secret_rows if item["enabled"])

    assert len(external_secret_rows) == 1
    assert external_secrets["instance_id"] == "mk8s"
    assert external_secrets["target_ref"] == "mk8s"
    assert "target_ref" not in payload["apps"]["charts"][0]
    assert "extraObjects" not in external_secrets["values"]


def test_mysterybox_eso_source_config_strips_managed_extra_objects() -> None:
    payload = _starter_payload(selected_infra={"mk8s", "mysterybox"})
    _set_mysterybox_inputs(payload)
    _enable_mysterybox_eso(payload, sync_namespaces=["ns1"])

    validate_config(payload)
    external_secrets = _external_secrets_app(payload)
    custom_object = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "operator-owned", "namespace": "external-secrets"},
    }
    external_secrets["values"]["extraObjects"] = [
        custom_object,
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": "ns1",
                "labels": {"nebius-cxcli.io/managed": "mysterybox-eso"},
            },
        },
    ]

    config = validate_config(payload)

    assert _external_secrets_app(config)["values"]["extraObjects"] == [custom_object]


def test_native_mysterybox_eso_defaults_to_primary_version_key_mapping() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "n/a",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload)

    config = validate_config(payload)
    mysterybox_sync = payload["deploy"]["targets"][0]["secrets"]["mysterybox"]

    assert mysterybox_sync["sync_namespaces"] == ["default"]
    assert "external_secrets" not in mysterybox_sync
    assert mysterybox_sync["refresh_interval"] == "15m"
    assert mysterybox_eso_terraform_output_specs(config) == (
        {
            "component_id": "mysterybox",
            "instance_id": "mysterybox",
            "output_name": "secret_ids",
            "source_ref": "mysterybox.secret_ids",
        },
    )

    objects = _extra_objects(
        config,
        {"mysterybox.secret_ids": {"db-uname-pass": "mbsec-e00db"}},
    )
    external_secret = next(item for item in objects if item["kind"] == "ExternalSecret")
    assert external_secret["metadata"]["namespace"] == "default"
    assert external_secret["metadata"]["name"] == "db-uname-pass"
    assert external_secret["spec"]["target"]["name"] == "db-uname-pass"
    assert external_secret["spec"]["refreshPolicy"] == "Periodic"
    assert external_secret["spec"]["refreshInterval"] == "15m"
    assert external_secret["spec"]["data"] == [
        {
            "secretKey": "USERNAME",
            "remoteRef": {"key": "mbsec-e00db", "property": "USERNAME"},
        }
    ]
    assert "dataFrom" not in external_secret["spec"]


def test_native_mysterybox_eso_uses_declared_kubernetes_secret_name() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "n/a",
                "kubernetes_secret_name": "app-db-creds",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload)

    config = validate_config(payload)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={
            "mysterybox.secret_ids": {"db-uname-pass": "mbsec-e00db"}
        },
    )

    external_secret = next(item for item in _extra_objects(config) if item["kind"] == "ExternalSecret")
    assert external_secret["metadata"]["name"] == "app-db-creds"
    assert external_secret["spec"]["target"]["name"] == "app-db-creds"
    assert external_secret["spec"]["data"] == [
        {
            "secretKey": "USERNAME",
            "remoteRef": {"key": "mbsec-e00db", "property": "USERNAME"},
        }
    ]
    assert "dataFrom" not in external_secret["spec"]


def test_native_mysterybox_eso_reports_managed_kubernetes_secret_key() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "soperator-slack-webhook",
                "version_id": "n/a",
                "kubernetes_secret_name": "soperator-notifier-slack-webhook",
                "payload": {"url": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload, sync_namespaces=["soperator"])

    config = validate_config(payload)

    assert not mysterybox_eso_manages_kubernetes_secret(
        config,
        target_ref="mk8s",
        namespace="soperator",
        name="soperator-notifier-slack-webhook",
        key="url",
    )
    assert mysterybox_eso_manages_kubernetes_secret(
        config,
        target_ref="mk8s",
        namespace="soperator",
        name="soperator-notifier-slack-webhook",
        key="url",
        component_output_values={
            "mysterybox.secret_ids": {"soperator-slack-webhook": "mbsec-e00slack"}
        },
    )
    assert not mysterybox_eso_manages_kubernetes_secret(
        config,
        target_ref="mk8s",
        namespace="default",
        name="soperator-notifier-slack-webhook",
        key="url",
    )
    assert not mysterybox_eso_manages_kubernetes_secret(
        config,
        target_ref="mk8s",
        namespace="soperator",
        name="soperator-notifier-slack-webhook",
        key="missing",
    )


def test_soperator_notifier_existing_mysterybox_secret_uses_primary_version() -> None:
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"soperator"})
    soperator = next(item for item in payload["apps"]["charts"] if item["id"] == "soperator")
    soperator["values"] = {
        "soperator-notifier": {
            "enabled": True,
            "slack": {
                "mode": "existing-webhook",
                "webhookSource": "mysterybox",
                "existingSecret": "soperator-notifier-slack-webhook",
                "existingSecretKey": "url",
                "mysterybox": {
                    "secretId": "mbsec-e00slack",
                    "property": "url",
                },
            },
        }
    }

    config = validate_config(payload)

    mysterybox_sync = config["deploy"]["targets"][0]["secrets"]["mysterybox"]
    assert mysterybox_sync["enabled"] is True
    assert "soperator" in mysterybox_sync["sync_namespaces"]
    assert _external_secrets_app(config)["enabled"] is True
    assert mysterybox_eso_manages_kubernetes_secret(
        config,
        target_ref="mk8s",
        namespace="soperator",
        name="soperator-notifier-slack-webhook",
        key="url",
    )
    external_secret = next(
        item for item in _extra_objects(config, {}) if item["kind"] == "ExternalSecret"
    )
    assert external_secret["spec"]["data"] == [
        {
            "secretKey": "url",
            "remoteRef": {"key": "mbsec-e00slack", "property": "url"},
        }
    ]


def test_soperator_notifier_mysterybox_source_requires_external_secrets_target() -> None:
    payload = _starter_payload(selected_infra={"mk8s"}, selected_apps={"soperator"})
    soperator = next(item for item in payload["apps"]["charts"] if item["id"] == "soperator")
    soperator["values"] = {
        "soperator-notifier": {
            "enabled": True,
            "slack": {
                "mode": "existing-webhook",
                "webhookSource": "mysterybox",
                "existingSecret": "soperator-notifier-slack-webhook",
                "existingSecretKey": "url",
                "mysterybox": {
                    "secretId": "mbsec-e00slack",
                    "property": "url",
                },
            },
        }
    }

    assert mysterybox_eso_app_target_refs(payload) == ("mk8s",)
    assert mysterybox_eso_dependency_issues(
        payload,
        app_entries=component_entries("apps"),
    ) == [
        "apps:soperator notifier webhookSource=mysterybox on target "
        "'mk8s' requires apps:external-secrets to be enabled for the same target"
    ]


def test_soperator_notifier_direct_secret_renders_when_native_secret_name_collides() -> None:
    payload = _starter_payload(selected_apps={"soperator"})
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "stale-slack-webhook",
                "version_id": "n/a",
                "kubernetes_secret_name": "soperator-notifier-slack-webhook",
                "payload": {"url": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload, sync_namespaces=["soperator"])
    soperator = next(item for item in payload["apps"]["charts"] if item["id"] == "soperator")
    soperator["values"] = {
        "soperator-notifier": {
            "enabled": True,
            "slack": {
                "mode": "existing-webhook",
                "webhookSource": "mysterybox",
                "existingSecret": "soperator-notifier-slack-webhook",
                "existingSecretKey": "url",
                "mysterybox": {
                    "secretId": "mbsec-e00direct",
                    "property": "url",
                },
            },
        }
    }

    config = validate_config(payload)

    objects = [
        item
        for item in mysterybox_eso_extra_objects_for_target(
            config,
            target_ref="mk8s",
            component_output_values={},
        )
        if item["kind"] == "ExternalSecret"
    ]
    assert len(objects) == 1
    assert objects[0]["metadata"]["name"] == "soperator-notifier-slack-webhook"
    assert objects[0]["spec"]["data"] == [
        {
            "secretKey": "url",
            "remoteRef": {"key": "mbsec-e00direct", "property": "url"},
        }
    ]


def test_soperator_notifier_direct_secret_replaces_native_secret_key_collision() -> None:
    payload = _starter_payload(selected_apps={"soperator"})
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "stale-slack-webhook",
                "version_id": "n/a",
                "kubernetes_secret_name": "soperator-notifier-slack-webhook",
                "payload": {
                    "url": {"type": "text"},
                    "token": {"type": "text"},
                },
            }
        ],
    )
    _enable_mysterybox_eso(payload, sync_namespaces=["soperator"])
    soperator = next(item for item in payload["apps"]["charts"] if item["id"] == "soperator")
    soperator["values"] = {
        "soperator-notifier": {
            "enabled": True,
            "slack": {
                "mode": "existing-webhook",
                "webhookSource": "mysterybox",
                "existingSecret": "soperator-notifier-slack-webhook",
                "existingSecretKey": "url",
                "mysterybox": {
                    "secretId": "mbsec-e00direct",
                    "property": "url",
                },
            },
        }
    }

    config = validate_config(payload)

    objects = [
        item
        for item in mysterybox_eso_extra_objects_for_target(
            config,
            target_ref="mk8s",
            component_output_values={
                "mysterybox.secret_ids": {"stale-slack-webhook": "mbsec-e00native"}
            },
        )
        if item["kind"] == "ExternalSecret"
    ]
    assert len(objects) == 1
    assert objects[0]["metadata"]["name"] == "soperator-notifier-slack-webhook"
    assert objects[0]["spec"]["data"] == [
        {
            "secretKey": "url",
            "remoteRef": {"key": "mbsec-e00direct", "property": "url"},
        },
        {
            "secretKey": "token",
            "remoteRef": {"key": "mbsec-e00native", "property": "token"},
        },
    ]


def test_native_mysterybox_eso_auto_primary_omits_declared_version() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "mbsecver-e00primary",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload)

    config = validate_config(payload)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={
            "mysterybox.secret_ids": {"db-uname-pass": "mbsec-e00db"}
        },
    )

    external_secret = next(item for item in _extra_objects(config) if item["kind"] == "ExternalSecret")
    assert external_secret["spec"]["data"] == [
        {
            "secretKey": "USERNAME",
            "remoteRef": {"key": "mbsec-e00db", "property": "USERNAME"},
        }
    ]


def test_native_mysterybox_eso_manual_version_pinning_renders_version() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "mbsecver-e00primary",
                "eso_version_policy": "manual-version-pinning",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload)

    config = validate_config(payload)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={
            "mysterybox.secret_ids": {"db-uname-pass": "mbsec-e00db"}
        },
    )

    external_secret = next(item for item in _extra_objects(config) if item["kind"] == "ExternalSecret")
    assert external_secret["spec"]["data"] == [
        {
            "secretKey": "USERNAME",
            "remoteRef": {
                "key": "mbsec-e00db",
                "property": "USERNAME",
                "version": "mbsecver-e00primary",
            },
        }
    ]


def test_native_mysterybox_eso_does_not_manage_builtin_default_namespace() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "n/a",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload, sync_namespaces=["default"])

    config = validate_config(payload)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={
            "mysterybox.secret_ids": {"db-uname-pass": "mbsec-e00db"}
        },
    )

    assert not any(
        item["kind"] == "Namespace" and item["metadata"]["name"] == "default"
        for item in _extra_objects(config)
    )
    external_secret = next(item for item in _extra_objects(config) if item["kind"] == "ExternalSecret")
    assert external_secret["metadata"]["namespace"] == "default"


def test_native_mysterybox_eso_cluster_wide_store_syncs_each_namespace() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "n/a",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload, allow_all_namespaces=True, sync_namespaces=["default", "ns-1"])

    config = validate_config(payload)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={
            "mysterybox.secret_ids": {"db-uname-pass": "mbsec-e00db"}
        },
    )

    extra_objects = _extra_objects(config)
    store = next(item for item in extra_objects if item["kind"] == "ClusterSecretStore")
    namespaces = {item["metadata"]["name"] for item in extra_objects if item["kind"] == "Namespace"}
    external_secret_namespaces = {
        item["metadata"]["namespace"] for item in extra_objects if item["kind"] == "ExternalSecret"
    }

    assert "conditions" not in store["spec"]
    assert namespaces == {"ns-1"}
    assert external_secret_namespaces == {"default", "ns-1"}


@pytest.mark.parametrize("refresh_interval", ["30s", "1m", "15m", "1h"])
def test_native_mysterybox_eso_accepts_refresh_interval_units(
    refresh_interval: str,
) -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "n/a",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload)
    payload["deploy"]["targets"][0]["secrets"]["mysterybox"][
        "refresh_interval"
    ] = refresh_interval

    config = validate_config(payload)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={
            "mysterybox.secret_ids": {"db-uname-pass": "mbsec-e00db"}
        },
    )

    external_secret = next(item for item in _extra_objects(config) if item["kind"] == "ExternalSecret")
    assert external_secret["spec"]["refreshInterval"] == refresh_interval


def test_native_mysterybox_eso_restricted_store_uses_sync_namespaces() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "n/a",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload, allow_all_namespaces=False, sync_namespaces=["ns-1", "ns-2"])

    config = validate_config(payload)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={
            "mysterybox.secret_ids": {"db-uname-pass": "mbsec-e00db"}
        },
    )

    extra_objects = _extra_objects(config)
    store = next(item for item in extra_objects if item["kind"] == "ClusterSecretStore")
    namespaces = {item["metadata"]["name"] for item in extra_objects if item["kind"] == "Namespace"}
    external_secret_namespaces = {
        item["metadata"]["namespace"] for item in extra_objects if item["kind"] == "ExternalSecret"
    }

    assert store["spec"]["conditions"] == [{"namespaces": ["ns-1", "ns-2"]}]
    assert namespaces == {"ns-1", "ns-2"}
    assert external_secret_namespaces == {"ns-1", "ns-2"}


def test_native_mysterybox_eso_api_domains_follow_target_config() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(payload)
    _enable_mysterybox_eso(payload)
    mysterybox = payload["deploy"]["targets"][0]["secrets"]["mysterybox"]
    mysterybox["api_domain"] = "https://api.eu-north1.nebius.cloud:443/"

    config = validate_config(payload)

    assert mysterybox_eso_api_domains(config) == ("api.eu-north1.nebius.cloud:443",)
    assert mysterybox_eso_api_domains(config, target_ref="mk8s") == (
        "api.eu-north1.nebius.cloud:443",
    )
    assert mysterybox_eso_api_domains(config, target_ref="missing") == ()


@pytest.mark.parametrize("stale_field", ["allowed_namespaces", "external_secrets"])
def test_native_mysterybox_eso_rejects_old_namespace_and_raw_sync_fields(
    stale_field: str,
) -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(payload)
    _enable_mysterybox_eso(payload)
    payload["deploy"]["targets"][0]["secrets"]["mysterybox"][stale_field] = []

    with pytest.raises(ValueError, match=f"unsupported field\\(s\\): {stale_field}"):
        validate_config(payload)


def test_native_mysterybox_eso_requires_sync_namespaces() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(payload)
    _enable_mysterybox_eso(payload)
    payload["deploy"]["targets"][0]["secrets"]["mysterybox"].pop("sync_namespaces")

    with pytest.raises(ValueError, match="sync_namespaces must be a non-empty list"):
        validate_config(payload)


def test_native_mysterybox_eso_rejects_empty_sync_namespaces() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(payload)
    _enable_mysterybox_eso(payload, sync_namespaces=[])

    with pytest.raises(ValueError, match="sync_namespaces must be a non-empty list"):
        validate_config(payload)


def test_native_mysterybox_eso_rejects_invalid_sync_namespace() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(payload)
    _enable_mysterybox_eso(payload, sync_namespaces=["Bad_Namespace"])

    with pytest.raises(ValueError, match="sync_namespaces\\[0\\] must be a Kubernetes namespace name"):
        validate_config(payload)


@pytest.mark.parametrize("refresh_interval", ["15minutes", "1d", "m15"])
def test_native_mysterybox_eso_rejects_invalid_refresh_interval(
    refresh_interval: str,
) -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(payload)
    _enable_mysterybox_eso(payload)
    payload["deploy"]["targets"][0]["secrets"]["mysterybox"][
        "refresh_interval"
    ] = refresh_interval

    with pytest.raises(ValueError, match="refresh_interval must use s, m, or h units"):
        validate_config(payload)


def test_native_mysterybox_rejects_invalid_kubernetes_secret_name() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "n/a",
                "kubernetes_secret_name": "Bad_Secret",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload)

    with pytest.raises(ValueError, match="kubernetes_secret_name must be a Kubernetes Secret name"):
        validate_config(payload)


def test_native_mysterybox_eso_multi_instance_secret_names_are_unique() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "shared",
                "version_id": "n/a",
                "payload": {"VALUE": {"type": "text"}},
            }
        ],
    )
    payload["infra"]["components"].append(
        {
            "id": "mysterybox",
            "instance_id": "secondary",
            "enabled": True,
            "inputs": {
                "parent_id": "project-456",
                "secrets": [
                    {
                        "name": "shared",
                        "version_id": "n/a",
                        "payload": {"VALUE": {"type": "text"}},
                    }
                ],
            },
        }
    )
    _enable_mysterybox_eso(payload, sync_namespaces=["default"])

    config = validate_config(payload)
    materialize_mysterybox_eso_app_values(
        config,
        component_output_values={
            "mysterybox.secret_ids": {"shared": "mbsec-e00primary"},
            "secondary.secret_ids": {"shared": "mbsec-e00secondary"},
        },
    )

    objects = _extra_objects(
        config,
        {
            "mysterybox.secret_ids": {"shared": "mbsec-e00primary"},
            "secondary.secret_ids": {"shared": "mbsec-e00secondary"},
        },
    )
    external_secret_names = {
        item["metadata"]["name"] for item in objects if item["kind"] == "ExternalSecret"
    }
    assert external_secret_names == {"mysterybox-shared", "secondary-shared"}
    assert mysterybox_eso_terraform_output_specs(config) == (
        {
            "component_id": "mysterybox",
            "instance_id": "mysterybox",
            "output_name": "secret_ids",
            "source_ref": "mysterybox.secret_ids",
        },
        {
            "component_id": "mysterybox",
            "instance_id": "secondary",
            "output_name": "secret_ids",
            "source_ref": "secondary.secret_ids",
        },
    )


def test_native_mysterybox_eso_validation_specs_are_required_and_target_scoped() -> None:
    payload = _starter_payload()
    _set_mysterybox_inputs(
        payload,
        secrets=[
            {
                "name": "db-uname-pass",
                "version_id": "n/a",
                "payload": {"USERNAME": {"type": "text"}},
            }
        ],
    )
    _enable_mysterybox_eso(payload, sync_namespaces=["default", "ns-1"])

    config = validate_config(payload)
    specs = mysterybox_eso_validation_specs(config)

    assert specs == [
        {
            "kind": MYSTERYBOX_ESO_CONNECTIVITY_VALIDATION_KIND,
            "name": "ESO MysteryBox connectivity (mk8s)",
            "target_ref": "mk8s",
            "required": True,
            "store_name": "nebius-mysterybox-shared",
            "api_domain": "api.nebius.cloud:443",
            "credentials_secret": {
                "name": "nebius-mysterybox-shared-creds",
                "namespace": "external-secrets",
                "key": "credentials.json",
            },
            "eso_namespace": "external-secrets",
            "external_secrets": [
                {"namespace": "default", "name": "db-uname-pass"},
                {"namespace": "ns-1", "name": "db-uname-pass"},
            ],
            "report_file": "mysterybox-eso-connectivity-report-mk8s.json",
        }
    ]
