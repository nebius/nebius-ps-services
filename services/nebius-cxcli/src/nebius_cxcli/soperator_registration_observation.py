"""Read-only live observation service for Soperator registration."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SoperatorRegistrationObservation:
    snapshot: Mapping[str, Any]
    collection_context: str
    kubernetes_uid: str
    live_release: Mapping[str, Any]
    live_namespace: str
    live_release_name: str
    adoption_values: Mapping[str, Any]
    adoption_profile: str
    metadata: Any
    source: Any
    source_contract: str
    capability_sha256: str
    provenance: Any
    protected_storage_sha256: str
    protected_storage_bindings: Sequence[Mapping[str, str]]


def _text(value: object) -> str:
    return str(value or "").strip()


def observe_existing_soperator_for_registration(
    *,
    payload: Mapping[str, Any],
    cluster_id: str,
    access: str,
    explicit_context: str,
    onboarded_cluster_context: Any,
    status_context: Any,
    collect_snapshot: Any,
    validate_explicit_context: Any,
    snapshot_kubernetes_uid: Any,
    adoption_values_from_snapshot: Any,
    inspect_release_contract: Any,
    normalize_release_selector: Any,
    verify_release_provenance: Any,
    protected_storage_evidence: Any,
) -> SoperatorRegistrationObservation:
    """Keep one generated Kubernetes authority live through provenance proof."""

    with ExitStack() as stack:
        collection_context = explicit_context
        if not collection_context:
            collection_context = stack.enter_context(
                onboarded_cluster_context(
                    payload,
                    cluster_id=cluster_id,
                    access=access,
                )
            )
        context_label = f" ({collection_context})" if explicit_context else " via Nebius API"
        with status_context(f"Observing installed Soperator on {cluster_id}{context_label}..."):
            snapshot = collect_snapshot(kube_context=collection_context)
        if explicit_context:
            validate_explicit_context(
                payload=payload,
                cluster_id=cluster_id,
                kube_context=explicit_context,
                access=access,
                explicit_snapshot=snapshot,
            )

        collection_errors = snapshot.get("collection_errors")
        if not isinstance(collection_errors, list):
            raise RuntimeError("Soperator discovery returned an invalid collection_errors record.")
        if collection_errors:
            raise RuntimeError(
                "Soperator onboarding requires complete live discovery; no target was written: "
                + "; ".join(str(item) for item in collection_errors)
            )
        kubernetes_uid = snapshot_kubernetes_uid(
            snapshot,
            source_label=f"MK8s cluster '{cluster_id}'",
        )
        raw_releases = snapshot.get("helm_releases")
        live_releases = (
            [item for item in raw_releases if isinstance(item, Mapping)]
            if isinstance(raw_releases, Sequence)
            and not isinstance(raw_releases, (str, bytes, bytearray))
            else []
        )
        if len(live_releases) != 1:
            raise RuntimeError(
                "Soperator onboarding requires exactly one unambiguous installed Soperator Helm "
                f"release; discovered {len(live_releases)} and wrote no target."
            )
        live_release = live_releases[0]
        if _text(live_release.get("status")).lower() != "deployed":
            raise RuntimeError(
                "Soperator onboarding requires the discovered Soperator Helm release to be "
                "deployed; no target was written."
            )
        live_version = next(
            (
                match.group("version")
                for key in ("chart_version", "chart", "app_version", "appVersion", "version")
                if (
                    match := re.search(
                        r"(?:^|[-v])(?P<version>[0-9]+\.[0-9]+\.[0-9]+)$",
                        _text(live_release.get(key)),
                    )
                )
            ),
            "",
        )
        if not live_version:
            raise RuntimeError(
                "Soperator onboarding requires one installed Soperator Helm release with an "
                "exact stable version; no target was written."
            )
        live_version = normalize_release_selector(live_version)
        if live_version == "latest":
            raise RuntimeError("Installed Soperator release identity must be an exact version.")
        adoption_values, adoption_profile = adoption_values_from_snapshot(snapshot)

        with status_context(
            f"Verifying official upstream Soperator {live_version} source identity..."
        ):
            metadata, source, source_contract, capability_sha256 = inspect_release_contract(
                live_version
            )
        live_namespace = _text(live_release.get("namespace")) or "soperator"
        live_storage_namespace = _text(live_release.get("storage_namespace"))
        if not live_storage_namespace:
            raise RuntimeError("Soperator onboarding requires the live Helm storage namespace.")
        live_release_name = _text(live_release.get("name"))
        if not live_release_name:
            raise RuntimeError("Soperator onboarding requires the live Helm release name.")
        live_oci_digest = _text(live_release.get("digest") or live_release.get("chart_digest"))
        if live_oci_digest and not re.fullmatch(r"sha256:[0-9a-f]{64}", live_oci_digest):
            raise RuntimeError("Soperator onboarding found a malformed live OCI digest.")
        with status_context(
            "Comparing the live Helm object graph with the verified official render..."
        ):
            provenance = verify_release_provenance(
                kube_context=collection_context,
                release_name=live_release_name,
                namespace=live_namespace,
                storage_namespace=live_storage_namespace,
                source_dir=Path(source.source_dir),
                oci_digest=live_oci_digest,
            )
        protected_storage_sha256, protected_storage_bindings = protected_storage_evidence(snapshot)
        return SoperatorRegistrationObservation(
            snapshot=snapshot,
            collection_context=collection_context,
            kubernetes_uid=kubernetes_uid,
            live_release=live_release,
            live_namespace=live_namespace,
            live_release_name=live_release_name,
            adoption_values=adoption_values,
            adoption_profile=adoption_profile,
            metadata=metadata,
            source=source,
            source_contract=source_contract,
            capability_sha256=capability_sha256,
            provenance=provenance,
            protected_storage_sha256=protected_storage_sha256,
            protected_storage_bindings=protected_storage_bindings,
        )


__all__ = ["SoperatorRegistrationObservation", "observe_existing_soperator_for_registration"]
