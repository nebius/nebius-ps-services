from __future__ import annotations

import pytest

from nebius_cxcli.soperator_upgrade_backend import (
    OnboardedProviderApiUpgradeBackend,
    TerraformManagedUpgradeBackend,
    build_soperator_infrastructure_authority,
)


def _managed_authority():
    return build_soperator_infrastructure_authority(
        target_ref="cluster-a",
        source_target={"kind": "mk8s", "ownership": "managed", "instance_id": "cluster-a"},
        generated_target={"kind": "mk8s", "ownership": "managed", "component_id": "mk8s"},
        managed_component_instance="cluster-a",
        terraform_modules=("module.mk8s_cluster_a",),
        cluster_id="mk8scluster-a",
        kubernetes_uid="uid-a",
        node_group_ids=("nodegroup-a",),
        registration_sha256="",
        provider_api_authorized=False,
        require_mutation_authorization=True,
    )


def _onboarded_authority(*, authorized: bool, require_mutation: bool = True):
    return build_soperator_infrastructure_authority(
        target_ref="cluster-a",
        source_target={
            "kind": "external-mk8s",
            "ownership": "external",
            "instance_id": "cluster-a",
        },
        generated_target={"kind": "external-mk8s", "ownership": "external"},
        managed_component_instance="",
        terraform_modules=(),
        cluster_id="mk8scluster-a",
        kubernetes_uid="uid-a",
        node_group_ids=("nodegroup-a",),
        registration_sha256="sha256:" + "c" * 64,
        provider_api_authorized=authorized,
        require_mutation_authorization=require_mutation,
    )


def test_managed_authority_selects_only_terraform() -> None:
    authority = _managed_authority()

    assert (authority.ownership, authority.backend) == ("managed", "terraform")
    assert authority.terraform_module == "module.mk8s_cluster_a"
    assert authority.provider_api_authorized is False
    with pytest.raises(ValueError, match="onboarded authority"):
        OnboardedProviderApiUpgradeBackend(authority=authority, apply_stage=lambda _version: {})


def test_onboarded_authority_requires_approved_campaign_authorization() -> None:
    with pytest.raises(RuntimeError, match="newly approved campaign"):
        _onboarded_authority(authorized=False)

    authority = _onboarded_authority(authorized=True)
    assert (authority.ownership, authority.backend) == ("onboarded", "provider-api")
    assert authority.terraform_module == ""
    with pytest.raises(ValueError, match="managed authority"):
        TerraformManagedUpgradeBackend(authority=authority, apply_stage=lambda _version: {})


def test_ambiguous_ownership_is_rejected_before_backend_selection() -> None:
    with pytest.raises(RuntimeError, match="both onboarded and Terraform-managed"):
        build_soperator_infrastructure_authority(
            target_ref="cluster-a",
            source_target={
                "kind": "external-mk8s",
                "ownership": "external",
                "instance_id": "cluster-a",
            },
            generated_target={"kind": "external-mk8s", "ownership": "external"},
            managed_component_instance="cluster-a",
            terraform_modules=("module.mk8s_cluster_a",),
            cluster_id="mk8scluster-a",
            kubernetes_uid="uid-a",
            node_group_ids=("nodegroup-a",),
            registration_sha256="sha256:" + "c" * 64,
            provider_api_authorized=True,
            require_mutation_authorization=True,
        )


def test_backend_adapters_invoke_only_the_frozen_mutation_path() -> None:
    calls: list[tuple[str, str]] = []
    terraform = TerraformManagedUpgradeBackend(
        authority=_managed_authority(),
        apply_stage=lambda version: (
            calls.append(("terraform", version)) or {"backend": "terraform"}
        ),
    )
    provider = OnboardedProviderApiUpgradeBackend(
        authority=_onboarded_authority(authorized=True),
        apply_stage=lambda version: (
            calls.append(("provider-api", version)) or {"backend": "provider-api"}
        ),
    )

    assert terraform.apply_version("1.34") == {"backend": "terraform"}
    assert provider.apply_version("1.35") == {"backend": "provider-api"}
    assert calls == [("terraform", "1.34"), ("provider-api", "1.35")]


@pytest.mark.parametrize(
    ("changes", "message"),
    (
        ({"source_instance": "another"}, "does not match"),
        ({"registration_sha256": "bad"}, "registration authority"),
        ({"managed": True}, "both onboarded"),
    ),
)
def test_onboarded_authority_rejects_identity_and_ownership_drift(
    changes: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        build_soperator_infrastructure_authority(
            target_ref="cluster-a",
            source_target={
                "kind": "external-mk8s",
                "ownership": "external",
                "instance_id": changes.get("source_instance", "cluster-a"),
            },
            generated_target={"kind": "external-mk8s", "ownership": "external"},
            managed_component_instance="cluster-a" if changes.get("managed") else "",
            terraform_modules=("module.mk8s",) if changes.get("managed") else (),
            cluster_id="mk8scluster-a",
            kubernetes_uid="uid-a",
            node_group_ids=("nodegroup-a",),
            registration_sha256=str(changes.get("registration_sha256", "sha256:" + "c" * 64)),
            provider_api_authorized=True,
            require_mutation_authorization=True,
        )


def test_managed_authority_rejects_provider_authority_and_incomplete_identity() -> None:
    with pytest.raises(RuntimeError, match="valid only for an onboarded"):
        build_soperator_infrastructure_authority(
            target_ref="cluster-a",
            source_target={"kind": "mk8s", "ownership": "managed"},
            generated_target={"kind": "mk8s", "ownership": "managed", "component_id": "mk8s"},
            managed_component_instance="cluster-a",
            terraform_modules=("module.mk8s",),
            cluster_id="cluster-id",
            kubernetes_uid="uid-a",
            node_group_ids=("nodegroup-a",),
            registration_sha256="",
            provider_api_authorized=True,
            require_mutation_authorization=True,
        )
    with pytest.raises(RuntimeError, match="authority is incomplete"):
        build_soperator_infrastructure_authority(
            target_ref="cluster-a",
            source_target={"kind": "mk8s", "ownership": "managed"},
            generated_target={"kind": "mk8s", "ownership": "managed", "component_id": "mk8s"},
            managed_component_instance="cluster-a",
            terraform_modules=("module.mk8s",),
            cluster_id="",
            kubernetes_uid="uid-a",
            node_group_ids=("nodegroup-a",),
            registration_sha256="",
            provider_api_authorized=False,
            require_mutation_authorization=True,
        )


def test_provider_backend_requires_campaign_authorization() -> None:
    authority = _onboarded_authority(authorized=False, require_mutation=False)

    with pytest.raises(ValueError, match="campaign authorization"):
        OnboardedProviderApiUpgradeBackend(authority=authority, apply_stage=lambda _version: {})
