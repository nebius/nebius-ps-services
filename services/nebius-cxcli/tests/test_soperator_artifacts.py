from __future__ import annotations

import pytest

from nebius_cxcli.soperator_artifacts import (
    SoperatorClusterArtifactIdentity,
    soperator_cluster_artifact_identity,
    soperator_cluster_artifact_identity_from_payload,
)


def test_soperator_cluster_key_prefers_cluster_id() -> None:
    identity = soperator_cluster_artifact_identity(
        cluster_id="mk8scluster-e00sy1jv52q5vrqrxy",
        cluster_name="prod cluster",
        kube_context="ctx",
        target_ref="mk8s",
    )

    assert identity.cluster_key == "mk8scluster-e00sy1jv52q5vrqrxy"
    assert identity.cluster_id == "mk8scluster-e00sy1jv52q5vrqrxy"
    assert identity.cluster_name == "prod cluster"
    assert identity.target_ref == "mk8s"
    assert identity.kube_context == "ctx"


def test_soperator_cluster_key_uses_name_when_id_missing() -> None:
    identity = soperator_cluster_artifact_identity(
        cluster_name="Onboarded Soperator Prod",
        kube_context="ctx",
        target_ref="external",
    )

    assert identity.cluster_key == "onboarded-soperator-prod"


def test_soperator_cluster_key_uses_kube_context_when_identity_missing() -> None:
    identity = soperator_cluster_artifact_identity(
        kube_context="kind/soperator:prod",
        target_ref="external",
    )

    assert identity.cluster_key == "kind-soperator-prod"


def test_soperator_cluster_key_falls_back_to_target() -> None:
    identity = soperator_cluster_artifact_identity(target_ref="Onboarded Soperator")

    assert identity.cluster_key == "onboarded-soperator"


@pytest.mark.parametrize("unsafe_value", (".", ".."))
def test_soperator_cluster_key_rejects_dot_path_components(unsafe_value: str) -> None:
    identity = soperator_cluster_artifact_identity(
        cluster_id=unsafe_value,
        cluster_name=unsafe_value,
        kube_context=unsafe_value,
        target_ref="safe-target",
    )

    assert identity.cluster_key == "safe-target"
    with pytest.raises(ValueError, match="safe path token"):
        SoperatorClusterArtifactIdentity(cluster_key=unsafe_value)


def test_soperator_cluster_identity_from_payload_uses_target_cluster_id() -> None:
    payload = {
        "deploy": {
            "targets": [
                {
                    "instance_id": "external-soperator",
                    "cluster_id": "mk8scluster-e00sy1jv52q5vrqrxy",
                    "cluster_name": "External Prod",
                    "kube_context": "ctx",
                }
            ]
        }
    }

    identity = soperator_cluster_artifact_identity_from_payload(
        payload,
        target_ref="external-soperator",
    )

    assert identity.cluster_key == "mk8scluster-e00sy1jv52q5vrqrxy"
    assert identity.cluster_id == "mk8scluster-e00sy1jv52q5vrqrxy"
    assert identity.cluster_name == "External Prod"
    assert identity.kube_context == "ctx"
