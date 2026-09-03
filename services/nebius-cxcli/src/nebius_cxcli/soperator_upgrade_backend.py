"""Fail-closed infrastructure ownership for full-stack Soperator upgrades."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Protocol


def _text(value: object) -> str:
    return str(value or "").strip()


def _sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class SoperatorInfrastructureAuthority:
    """Immutable ownership and backend evidence for one campaign."""

    target_ref: str
    ownership: str
    backend: str
    cluster_id: str
    kubernetes_uid: str
    node_group_ids: tuple[str, ...]
    target_kind: str
    target_ownership: str
    managed_component_instance: str
    terraform_module: str
    registration_sha256: str
    provider_api_authorized: bool

    @property
    def digest(self) -> str:
        return _sha256(asdict(self))


def build_soperator_infrastructure_authority(
    *,
    target_ref: str,
    source_target: Mapping[str, object] | None,
    generated_target: Mapping[str, object],
    managed_component_instance: str,
    terraform_modules: Sequence[str],
    cluster_id: str,
    kubernetes_uid: str,
    node_group_ids: Sequence[str],
    registration_sha256: str,
    provider_api_authorized: bool,
    require_mutation_authorization: bool,
) -> SoperatorInfrastructureAuthority:
    """Resolve exactly one supported ownership/backend pair without fallback."""

    normalized_target = _text(target_ref)
    generated_kind = _text(generated_target.get("kind")).lower()
    generated_ownership = _text(generated_target.get("ownership")).lower()
    generated_component = _text(generated_target.get("component_id")).lower()
    source_kind = _text((source_target or {}).get("kind")).lower()
    source_ownership = _text((source_target or {}).get("ownership")).lower()
    source_instance = _text((source_target or {}).get("instance_id"))
    modules = tuple(sorted({_text(value) for value in terraform_modules if _text(value)}))
    managed_instance = _text(managed_component_instance)
    external_markers = {
        source_kind == "external-mk8s",
        source_ownership == "external",
        generated_kind == "external-mk8s",
        generated_ownership == "external",
    }
    has_external = any(external_markers)
    external_consistent = all(external_markers)
    managed_consistent = (
        not has_external
        and generated_ownership == "managed"
        and generated_component == "mk8s"
        and managed_instance == normalized_target
        and len(modules) == 1
    )
    if external_consistent:
        if source_instance != normalized_target:
            raise RuntimeError("onboarded Soperator ownership does not match the selected target")
        if managed_instance or modules:
            raise RuntimeError(
                "Soperator target has both onboarded and Terraform-managed authority"
            )
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", registration_sha256):
            raise RuntimeError("onboarded Soperator upgrade requires exact registration authority")
        if require_mutation_authorization and not provider_api_authorized:
            raise RuntimeError(
                "Onboarded full-stack upgrades require provider API authority from the "
                "newly approved campaign."
            )
        authority = SoperatorInfrastructureAuthority(
            target_ref=normalized_target,
            ownership="onboarded",
            backend="provider-api",
            cluster_id=_text(cluster_id),
            kubernetes_uid=_text(kubernetes_uid),
            node_group_ids=tuple(sorted({_text(value) for value in node_group_ids})),
            target_kind="external-mk8s",
            target_ownership="external",
            managed_component_instance="",
            terraform_module="",
            registration_sha256=_text(registration_sha256),
            provider_api_authorized=bool(provider_api_authorized),
        )
    elif managed_consistent:
        if provider_api_authorized:
            raise RuntimeError(
                "provider API authority is valid only for an onboarded Soperator target"
            )
        authority = SoperatorInfrastructureAuthority(
            target_ref=normalized_target,
            ownership="managed",
            backend="terraform",
            cluster_id=_text(cluster_id),
            kubernetes_uid=_text(kubernetes_uid),
            node_group_ids=tuple(sorted({_text(value) for value in node_group_ids})),
            target_kind="managed-mk8s",
            target_ownership="managed",
            managed_component_instance=managed_instance,
            terraform_module=modules[0],
            registration_sha256="",
            provider_api_authorized=False,
        )
    else:
        raise RuntimeError(
            "Soperator infrastructure ownership is missing, contradictory, or ambiguous; "
            "expected one onboarded/provider-api or managed/terraform authority"
        )
    if (
        not authority.target_ref
        or not authority.cluster_id
        or not authority.kubernetes_uid
        or not authority.node_group_ids
        or any(not value for value in authority.node_group_ids)
    ):
        raise RuntimeError("Soperator infrastructure authority is incomplete")
    return authority


class SoperatorInfrastructureUpgradeBackend(Protocol):
    authority: SoperatorInfrastructureAuthority

    def apply_version(self, version: str) -> Mapping[str, object]: ...


@dataclass
class TerraformManagedUpgradeBackend:
    authority: SoperatorInfrastructureAuthority
    apply_stage: Callable[[str], Mapping[str, object]]

    def __post_init__(self) -> None:
        if (self.authority.ownership, self.authority.backend) != ("managed", "terraform"):
            raise ValueError("Terraform upgrade backend requires managed authority")

    def apply_version(self, version: str) -> Mapping[str, object]:
        return self.apply_stage(version)


@dataclass
class OnboardedProviderApiUpgradeBackend:
    authority: SoperatorInfrastructureAuthority
    apply_stage: Callable[[str], Mapping[str, object]]

    def __post_init__(self) -> None:
        if (self.authority.ownership, self.authority.backend) != (
            "onboarded",
            "provider-api",
        ):
            raise ValueError("provider API upgrade backend requires onboarded authority")
        if not self.authority.provider_api_authorized:
            raise ValueError("provider API upgrade backend requires campaign authorization")

    def apply_version(self, version: str) -> Mapping[str, object]:
        return self.apply_stage(version)


__all__ = [
    "OnboardedProviderApiUpgradeBackend",
    "SoperatorInfrastructureAuthority",
    "SoperatorInfrastructureUpgradeBackend",
    "TerraformManagedUpgradeBackend",
    "build_soperator_infrastructure_authority",
]
