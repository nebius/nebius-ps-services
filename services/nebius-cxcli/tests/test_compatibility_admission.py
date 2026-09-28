from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nebius_cxcli.compatibility_adapters import provider_evidence, terraform_evidence
from nebius_cxcli.compatibility_execution import frozen_manifest_inputs, verify_constraint_replay
from nebius_cxcli.compatibility_matrix import assess
from nebius_cxcli.frozen_catalog import freeze_catalog


def test_generated_validation_rejects_missing_or_changed_inputs_before_work():
    config = {"infra": {"components": []}, "apps": {"charts": []}}
    frozen = {"schema": "invalid"}
    calls = []

    @frozen_manifest_inputs
    def validate(config, paths, *, manifest):
        calls.append(True)

    with pytest.raises(ValueError, match="missing or malformed"):
        validate(config, None, manifest={})
    with pytest.raises(ValueError, match="registry/evaluator changed"):
        validate(
            config,
            None,
            manifest={
                "render": {
                    "inputs": freeze_catalog(config),
                    "compatibility": frozen,
                }
            },
        )
    assert not calls


def test_module_and_provider_lock_bytes_are_bound_to_recovery(tmp_path, monkeypatch):
    lock = tmp_path / ".terraform.lock.hcl"
    lock.write_text('provider "example/test" {}\n')
    (tmp_path / "main.tf").write_text('module "sample" { source = "./module" }\n')
    module = tmp_path / "module"
    module.mkdir()
    (module / "main.tf").write_text('output "value" { value = 1 }\n')
    manifest = tmp_path / ".terraform/modules/modules.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "Modules": [
                    {"Key": "sample", "Dir": "module", "Source": "./module"},
                ]
            }
        )
    )
    monkeypatch.setattr(
        "nebius_cxcli.compatibility_adapters.kubernetes_process.run",
        lambda *a, **kw: SimpleNamespace(
            stdout=json.dumps(
                {
                    "terraform_version": "1.11.4",
                    "platform": "darwin_arm64",
                    "provider_selections": {"example/test": "1.0"},
                }
            )
        ),
    )
    subject = {
        "component_id": "example",
        "instance_id": "cluster",
        "distribution": "unknown",
        "required_adapters": ["resolved_terraform_constraints"],
    }
    before = assess([subject], receipts=[terraform_evidence(subject, tmp_path)], admission=True)
    verify_constraint_replay(before, before)
    (module / "main.tf").write_text('output "value" { value = 2 }\n')
    after = assess([subject], receipts=[terraform_evidence(subject, tmp_path)], admission=True)
    with pytest.raises(ValueError, match="cannot reinterpret"):
        verify_constraint_replay(before, after)


@pytest.mark.parametrize("incompatible", [False, True])
def test_provider_admission_checks_each_group_version_and_driver_owner(monkeypatch, incompatible):
    import nebius.api.nebius.mk8s.v1 as api

    from nebius_cxcli.mk8s_upgrade import CompatibilityChoice

    requests = []
    closed = []
    sdk = SimpleNamespace(sync_close=lambda: closed.append(True))

    def wait(result):
        return SimpleNamespace(wait=lambda: result)

    monkeypatch.setattr("nebius_cxcli.sdk_auth.init_nebius_sdk", lambda **kw: sdk)
    monkeypatch.setattr(
        api,
        "ClusterServiceClient",
        lambda sdk: SimpleNamespace(
            list_control_plane_versions=lambda *a, **kw: wait(
                SimpleNamespace(items=[SimpleNamespace(version="1.35")])
            )
        ),
    )

    def lookup(request, **kwargs):
        requests.append((request.cluster_kubernetes_version, request.platform))
        return wait(request.platform)

    monkeypatch.setattr(
        api, "NodeGroupServiceClient", lambda sdk: SimpleNamespace(get_compatibility_matrix=lookup)
    )
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_upgrade.compatibility_choices_from_response",
        lambda response, **kw: (
            CompatibilityChoice(response, "ubuntu24.04", "cuda13" if response == "gpu" else ""),
        ),
    )
    groups = [
        SimpleNamespace(
            key="cpu",
            os="ubuntu24.04",
            platform="cpu",
            gpu_stack_preset="",
            gpu_stack_source="operator",
        ),
        SimpleNamespace(
            key="gpu",
            os="ubuntu24.04",
            platform="gpu",
            gpu_stack_preset="wrong" if incompatible else "cuda13",
            gpu_stack_source="nebius_image",
        ),
    ]
    monkeypatch.setattr(
        "nebius_cxcli.mk8s_node_groups.iter_node_groups", lambda inputs: iter(groups)
    )
    config = {
        "client_info": {"nebius": {"project_id": "project-example"}},
        "infra": {
            "components": [
                {
                    "id": "mk8s",
                    "instance_id": "cluster",
                    "inputs": {
                        "node_groups": {"cpu": {"version": "1.34"}, "gpu": {"version": "1.35"}},
                    },
                }
            ]
        },
    }
    subject = {"component_id": "mk8s", "instance_id": "cluster", "kubernetes_minor": "1.35"}
    if incompatible:
        with pytest.raises(ValueError, match="rejected gpu"):
            provider_evidence(subject, config)
    else:
        proof = provider_evidence(subject, config)
        assert [row["version"] for row in proof[1]["node_groups"]] == ["1.34", "1.35"]
    assert requests == [("1.34", "cpu"), ("1.35", "gpu")]
    assert closed == [True]
