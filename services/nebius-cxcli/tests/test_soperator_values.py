from __future__ import annotations

import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest
import yaml

from nebius_cxcli import cli
from nebius_cxcli.soperator_config_materialization import (
    _materialize_soperator_component_defaults,
    _materialize_soperator_guided_sssd_values,
)
from nebius_cxcli.soperator_values import (
    EXPLICIT_VALUES_FIELD,
    apply_source_feature_defaults,
    explicit_values,
    mark_explicit_value,
    read_soperator_values_file,
    seed_soperator_values,
    validate_feature_values,
    validate_schedule,
    validate_source_input,
)
from soperator_fixtures import sample_snapshot


def test_file_is_values_only_and_lists_are_atomic(tmp_path):
    path = tmp_path / "values.yaml"
    path.write_text(
        "slurmConfig: {debugLevel: info}\npartitionConfiguration: {partitions: []}\nsssd: {enabled: false}\n"
    )
    values = read_soperator_values_file(path)
    row = {"id": "soperator", "enabled": True, "values": {}}
    seed_soperator_values({"apps": {"charts": [row]}}, values)
    assert row[EXPLICIT_VALUES_FIELD] == [
        "/partitionConfiguration/partitions",
        "/slurmConfig/debugLevel",
        "/sssd/enabled",
    ]
    assert explicit_values(row) == values
    mark_explicit_value(row, ("partitionConfiguration", "partitions", 0, "name"))
    assert explicit_values(row) == values


@pytest.mark.parametrize(
    "content",
    [
        "values: {}",
        "slurmNodes: {}",
        "slurmNodes: null",
        "slurmNodes: {controller: {}}",
        "slurmNodes: {controller: null}",
        "slurmConfig: {}",
        "slurmConfig: null",
        "slurmNodes: {sssd: {enabled: true}}",
        "[]",
        "---\n{}\n---\n{}",
        "sssd: {enabled: true, enabled: false}",
        "sssd: {enabled: yes, configFile: /private/file}",
        "images: {}",
        "controllerManager: {manager: {env: {isApparmorCrdInstalled: false}}}",
        "controllerManager: {manager: {env: {isMariadbCrdInstalled: false}}}",
        "controllerManager: {manager: {env: {isPrometheusCrdInstalled: false}}}",
        "controllerManager: {manager: {env: {}}}",
        "nodesets: []",
        "slurmNodes: {controller: {size: 99}}",
        "x: &x {again: *x}",
        "sssd: {enabled: true}\nslurmNodes: {sssd: {enabled: false}}",
        "soperator-backup-config: {secret: {stringData: {password: SENSITIVE}}}",
    ],
)
def test_file_rejects_invalid_or_owned_inputs_without_echoing_values(tmp_path, content):
    path = tmp_path / "input.yaml"
    path.write_text(content)
    with pytest.raises(ValueError) as error:
        read_soperator_values_file(path)
    assert "SENSITIVE" not in str(error.value)


@pytest.mark.parametrize("value", ["0", "null", '"false"'])
@pytest.mark.parametrize(
    "feature", ["soperator-checks", "soperator-activechecks", "soperator-notifier"]
)
def test_feature_enablement_must_be_boolean_before_frozen_resolution(tmp_path, feature, value):
    path = tmp_path / "input.yaml"
    path.write_text(f"{feature}: {{enabled: {value}}}\n")
    with pytest.raises(ValueError, match="enabled must be a boolean"):
        read_soperator_values_file(path)


@pytest.mark.parametrize("feature", ["soperator-checks", "soperator-activechecks"])
def test_required_check_components_cannot_be_disabled_at_input(tmp_path, feature):
    path = tmp_path / "input.yaml"
    path.write_text(f"{feature}: {{enabled: false}}\n")
    with pytest.raises(ValueError, match="cannot be disabled"):
        read_soperator_values_file(path)


def test_explicit_values_survive_default_materialization_and_serialization(monkeypatch):
    row = {"id": "soperator", "enabled": True, "values": {}}
    payload = {"apps": {"charts": [row]}}
    expected = {
        "sssd": {"enabled": False},
        "partitionConfiguration": {"partitions": [{"name": "main"}]},
    }
    seed_soperator_values(payload, expected)

    # Deliberately reproduce the generated-default collision that lost user intent.
    def materialize(current):
        current["apps"]["charts"][0]["values"].update(
            {
                "sssd": {"enabled": True},
                "partitionConfiguration": {"partitions": [{"name": "replacement"}]},
            }
        )
        return True

    monkeypatch.setattr(
        "nebius_cxcli.soperator_config_materialization._materialize_soperator_generated_defaults",
        materialize,
    )
    for _ in range(3):
        _materialize_soperator_component_defaults(payload)
        assert explicit_values(payload["apps"]["charts"][0]) == expected
        payload = yaml.safe_load(yaml.safe_dump(payload))


def test_frozen_defaults_and_routing_use_selected_release(tmp_path):
    snapshot = sample_snapshot()
    defaults = {
        "slurmCluster": {"slurmConfig": {}},
        "operator": {"controllerManager": {}},
        "backupConfig": {
            "bucket": {},
            "backup": {"schedule": "0 3 * * *"},
            "prune": {"schedule": "0 4 * * *", "retention": {"keepDaily": 11}},
        },
    }
    charts = {}
    for role, values in defaults.items():
        directory = tmp_path / role
        directory.mkdir()
        (directory / "values.yaml").write_text(yaml.safe_dump(values))
        charts[role] = replace(snapshot.umbrella, source_path=role)
    frozen = SimpleNamespace(
        charts=charts, umbrella=snapshot.umbrella, source=SimpleNamespace(source_dir=str(tmp_path))
    )
    validate_source_input({"slurmConfig": {"customNestedSetting": 3}}, frozen)
    validate_source_input(
        {"controllerManager": {"manager": {"resources": {"limits": {"cpu": "2"}}}}}, frozen
    )
    for value in (
        {"certManager": {"resources": {"requests": {"cpu": "100m"}}}},
        {"mariadb-operator": {"installOperator": True}},
        {"kruise": {"manager": {"replicas": 2}}},
        {"observability": {"vmStack": {"overrideValues": {"grafana": {"enabled": False}}}}},
        {"serviceMonitor": {}},
    ):
        with pytest.raises(ValueError):
            validate_source_input(value, frozen)
    with pytest.raises(ValueError, match="Unsupported"):
        validate_source_input({"typo": True}, frozen)
    row = {"id": "soperator", "enabled": True}
    apply_source_feature_defaults({"apps": {"charts": [row]}}, frozen)
    assert row["values"]["soperator-backup-config"]["prune"]["retention"]["keepDaily"] == 11


@pytest.mark.parametrize("value", ["@daily-random", "@weekly", "0 2 * * *", "*/5 0-12 1,3 * 1-5"])
def test_supported_schedules(value):
    validate_schedule(value, "schedule")


@pytest.mark.parametrize("value", ["", "SENSITIVE", "99 * * * *", "* * * *", "*/0 * * * *", 5])
def test_invalid_schedules(value):
    with pytest.raises(ValueError):
        validate_schedule(value, "schedule")


def test_backup_requires_destination_and_positive_retention():
    backup = {
        "enabled": True,
        "bucket": {"name": "backup", "endpoint": "https://storage.example.invalid"},
        "prune": {"retention": {"keepDaily": 7}},
    }
    validate_feature_values({"soperator-backup-config": backup})
    for fragment in (
        {"bucket": {}},
        {"bucket": {"name": "b", "endpoint": "https://user:SENSITIVE@example.invalid"}},
        {"prune": {"retention": {"keepDaily": 0}}},
    ):
        with pytest.raises(ValueError) as error:
            validate_feature_values({"soperator-backup-config": {**backup, **fragment}})
        assert "SENSITIVE" not in str(error.value)


def test_sssd_shared_references_lower_to_each_consumer():
    shared = {
        "enabled": True,
        "sssdConfSecretRefName": "directory-config",
        "sssdLdapCAConfigMapRefName": "directory-ca",
    }
    values = {"sssd": copy.deepcopy(shared), "nodesets": [{"name": "cpu"}, {"name": "gpu"}]}
    _materialize_soperator_guided_sssd_values(values)
    assert values["slurmNodes"]["sssd"] == shared
    for nodeset in values["nodesets"]:
        assert nodeset["sssd"]["enabled"] is True
        assert nodeset["sssdConfSecretRefName"] == "directory-config"
        assert nodeset["sssdLdapCAConfigMapRefName"] == "directory-ca"
    values["sssd"]["enabled"] = False
    _materialize_soperator_guided_sssd_values(values)
    assert all(item["sssd"]["enabled"] is False for item in values["nodesets"])


def test_deploy_rejects_values_file_before_reading_it(tmp_path):
    from typer.testing import CliRunner

    result = CliRunner().invoke(
        cli.app,
        [
            "deploy",
            str(tmp_path),
            "--values-file",
            str(tmp_path / "missing"),
        ],
    )
    assert result.exit_code != 0
    assert "No such option" in result.output
    assert "--values-file" in result.output


def test_real_wizard_prefills_file_and_records_confirmed_default(monkeypatch):
    monkeypatch.setattr(cli.typer, "confirm", lambda *a, **kw: True)
    from nebius_cxcli.components import ComponentEntry

    row = {"id": "soperator", "instance_id": "mk8s", "enabled": True, "values": {}}
    payload = {"version": "v1", "infra": {"components": []}, "apps": {"charts": [row]}}
    seed_soperator_values(payload, {"sssd": {"enabled": True}})
    entry = ComponentEntry(
        id="soperator",
        scope="apps",
        config_path="apps.slurm.soperator",
        description="Soperator",
        wizard_fields={
            "values": {"prompt": False},
            "values.sssd.enabled": {"default": False, "write_default_to_config": True},
        },
    )
    seen = []

    def answer(path_label, current, **kwargs):
        seen.append((path_label, current))
        return False, False

    monkeypatch.setattr(cli, "module_variables", lambda source: ())
    monkeypatch.setattr(cli, "module_required_variables", lambda source: ())
    monkeypatch.setattr(
        cli, "helm_chart_default_values", lambda **kwargs: {"sssd": {"enabled": False}}
    )
    monkeypatch.setattr(cli, "_wizard_continue_phase", lambda *args, **kwargs: True)
    monkeypatch.setattr(cli, "_prompt_scalar_override", answer)
    updated, completed = cli._run_component_field_wizard(
        config_yaml=yaml.safe_dump(payload),
        selected_infra=set(),
        selected_apps={"soperator"},
        infra_entries=(),
        app_entries=(entry,),
        provider_lookup=None,
    )
    assert completed
    assert ("apps.charts[0].values.sssd.enabled", True) in seen
    saved = yaml.safe_load(updated)
    cli._prune_redundant_app_chart_default_values(payload=saved, app_entries=(entry,))
    assert explicit_values(saved["apps"]["charts"][0])["sssd"]["enabled"] is False


def test_real_normalization_and_upgrade_keep_explicit_values(tmp_path):
    from nebius_cxcli.config_loader import normalize_runtime_config_payload
    from nebius_cxcli.config_model import to_runtime_payload

    row = {
        "id": "soperator",
        "instance_id": "cluster1",
        "enabled": True,
        "profile": "nebius-cpu-v1",
        "version": "4.1.7",
        "values": {},
    }
    payload = {
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster1",
                    "enabled": True,
                    "inputs": {
                        "node_group_defaults": {
                            "cpu": {"platform": "cpu-d3", "preset": "32vcpu-128gb"}
                        }
                    },
                },
                {"id": "sfs", "instance_id": "sfs", "enabled": True, "inputs": {}},
            ]
        },
        "apps": {"charts": [row]},
    }
    expected = {
        "sssd": {"enabled": False},
        "partitionConfiguration": {
            "configType": "structured",
            "partitions": [{"name": "explicit", "isAll": True, "config": "Default=YES"}],
        },
    }
    seed_soperator_values(payload, expected)
    for version in ("4.1.7", "4.1.8", "4.1.8"):
        payload["apps"]["charts"][0]["version"] = version
        normalize_runtime_config_payload(payload)
        assert explicit_values(payload["apps"]["charts"][0]) == expected
        assert explicit_values(to_runtime_payload(payload)["apps"]["charts"][0]) == expected
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(payload))
        payload = yaml.safe_load(path.read_text())


def test_feature_prompts_are_conditional_and_required():
    from nebius_cxcli.components import soperator_install_entry

    entry = soperator_install_entry(
        version="4.1.8",
        chart_repo="oci://cr.eu-north1.nebius.cloud/soperator/helm-soperator-fluxcd",
    )
    row = {
        "id": "soperator",
        "enabled": True,
        "values": {"sssd": {"enabled": False}, "soperator-backup-config": {"enabled": False}},
    }
    payload = {"apps": {"charts": [row]}}
    for feature, field in (
        ("sssd", "sssdConfSecretRefName"),
        ("soperator-backup-config", "bucket.name"),
        ("soperator-backup-config", "bucket.endpoint"),
    ):
        label = f"apps.charts[0].values.{feature}.{field}"
        row["values"][feature]["enabled"] = False
        assert cli._skip_soperator_child_chart_prompt(
            payload=payload, entry=entry, full_path_label=label
        )
        row["values"][feature]["enabled"] = True
        assert not cli._skip_soperator_child_chart_prompt(
            payload=payload, entry=entry, full_path_label=label
        )
        assert cli._dynamic_required_prompt(payload=payload, entry=entry, full_path_label=label)


@pytest.mark.parametrize("enabled", [True, False])
def test_observability_defaults_are_taken_from_frozen_umbrella_without_persistence(
    tmp_path, enabled
):
    from nebius_cxcli.soperator_values import with_source_observability

    snapshot = sample_snapshot()
    directory = tmp_path / snapshot.umbrella.source_path
    directory.mkdir(parents=True)
    native = {
        "enabled": enabled,
        "dcgmExporter": {
            "enabled": True,
            "values": {"validateToolkit": True, "hpcJobMapDir": "/upstream/jobs"},
        },
        "vmStack": {"enabled": False},
        "opentelemetry": {"enabled": True},
    }
    (directory / "values.yaml").write_text(yaml.safe_dump({"observability": native}))
    frozen = SimpleNamespace(
        charts=snapshot.charts,
        umbrella=snapshot.umbrella,
        source=SimpleNamespace(source_dir=str(tmp_path)),
    )
    authored = {"observability": {"dcgmExporter": {"values": {"validateToolkit": False}}}}
    before = copy.deepcopy(authored)
    validate_source_input(authored, frozen)
    effective = with_source_observability(authored, frozen)
    assert effective["observability"]["enabled"] is enabled
    assert effective["observability"]["dcgmExporter"]["values"] == {
        "validateToolkit": False,
        "hpcJobMapDir": "/upstream/jobs",
    }
    assert effective["observability"]["vmStack"] == {"enabled": False}
    assert authored == before
    # A different frozen release default must not inherit the previous one.
    native["dcgmExporter"]["enabled"] = False
    (directory / "values.yaml").write_text(yaml.safe_dump({"observability": native}))
    assert (
        with_source_observability(authored, frozen)["observability"]["dcgmExporter"]["enabled"]
        is False
    )


@pytest.mark.parametrize("path", ["values", "overrideValues"])
def test_bundled_grafana_cannot_bypass_optional_app_selection(tmp_path, path):
    config = tmp_path / "input.yaml"
    config.write_text(
        yaml.safe_dump({"observability": {"vmStack": {path: {"grafana": {"enabled": True}}}}})
    )
    with pytest.raises(ValueError, match="bundled Grafana is disabled"):
        read_soperator_values_file(config)


def test_obsolete_dcgm_input_rejected_even_without_explicit_metadata():
    with pytest.raises(ValueError, match="reauthor"):
        validate_feature_values({"soperator-dcgm-exporter": {"enabled": False}})


@pytest.mark.parametrize(
    "change", ["dcgm", "grafana", "unauthenticated", "fingerprint", "same", "other_patch"]
)
def test_frozen_observability_replay_rejects_policy_changes(change):
    from nebius_cxcli.soperator_operation import soperator_sha256
    from nebius_cxcli.soperator_values import assert_frozen_observability_replay

    previous = {"observability": {"enabled": True, "dcgmExporter": {"enabled": True}}}
    desired = copy.deepcopy(previous)
    material = {"renderedFluxSha256": "frozen"}
    receipt = {**material, "fingerprint": soperator_sha256(material), "createdAt": "ignored"}
    if change == "dcgm":
        desired["observability"]["dcgmExporter"]["enabled"] = False
    elif change == "grafana":
        desired["observability"]["vmStack"] = {"values": {"grafana": {"enabled": True}}}
    elif change == "fingerprint":
        receipt["fingerprint"] = "tampered"
    args = dict(
        receipt=receipt,
        previous_bundle_sha256="changed" if change == "unauthenticated" else "frozen",
        desired_bundle_sha256="frozen" if change == "same" else "candidate",
        previous_values=previous,
        desired_values=desired,
    )
    before = copy.deepcopy(args)
    if change in {"same", "other_patch"}:
        assert_frozen_observability_replay(**args)
    else:
        with pytest.raises(ValueError, match="[Ff]rozen"):
            assert_frozen_observability_replay(**args)
    assert args == before


@pytest.mark.parametrize(
    "native",
    [
        {"clusterId": "replacement"},
        {"region": "replacement"},
        {"vmStack": {"overrideValues": {"vmagent": {"enabled": False}}}},
        {"opentelemetry": {"logs": {"overrideValues": {"config": {}}}}},
        {"opentelemetry": {"events": {"overrideValues": {"config": {}}}}},
    ],
)
def test_native_inputs_cannot_replace_protected_bindings(tmp_path, native):
    path = tmp_path / "input.yaml"
    path.write_text(yaml.safe_dump({"observability": native}))
    with pytest.raises(ValueError, match="owned|protected"):
        read_soperator_values_file(path)
