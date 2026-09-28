import copy
import json
from types import SimpleNamespace

import pytest

from nebius_cxcli.operation_completion import load_completion, prepare_completion, verify_completion
from nebius_cxcli.soperator_operation import SoperatorOperationAnchor
from test_soperator_operation import _lease_authority, _spec


def test_completion_freezes_full_identity_before_seal_and_requires_live_terminal_state(
    tmp_path, monkeypatch
):
    spec = _spec(tmp_path)
    anchor = SoperatorOperationAnchor(
        kube_context="context",
        cluster_id=spec.nebius_cluster_id,
        operation_spec=spec,
        lease_authority=_lease_authority(),
    )
    paths = SimpleNamespace(reports_dir=tmp_path / "reports")
    data = {
        **anchor._expected_data(),
        "mainWorkloadAuthority": "frozen",
        "mainWorkloadAuthoritySha256": "bound",
    }
    payload = {"metadata": {"uid": "anchor-uid"}, "data": data}
    monkeypatch.setattr(
        anchor, "_kubectl", lambda *a: SimpleNamespace(returncode=0, stdout=json.dumps(payload))
    )
    monkeypatch.setattr(
        "nebius_cxcli.deployment_applications.target_bundle_digest", lambda *a: "sha256:" + "b" * 64
    )
    proof = prepare_completion(anchor, paths, spec.target_ref, generation=object())
    assert load_completion(paths, spec.target_ref) == proof
    assert proof["data"]["mainWorkloadAuthority"] == "frozen"
    monkeypatch.setattr(
        "nebius_cxcli.installation_reconciliation._read", lambda *a: copy.deepcopy(payload)
    )
    kwargs = dict(
        identity={"cluster_id": spec.nebius_cluster_id, "kubernetes_uid": spec.kubernetes_uid},
        target_ref=spec.target_ref,
        env={},
        desired_bundle="sha256:" + "b" * 64,
    )
    assert verify_completion(proof, **kwargs) is None
    data["fencingEpoch"] = str(int(data["fencingEpoch"]) + 1)
    data["holderIdentitySha256"] = "sha256:" + "c" * 64
    assert verify_completion(proof, **kwargs) is None
    payload["data"] = data = copy.deepcopy(proof["data"])
    data["status"] = "complete"
    assert verify_completion(proof, **kwargs) == proof
    assert verify_completion(proof, **{**kwargs, "desired_bundle": "sha256:" + "d" * 64}) is None
    payload["metadata"]["uid"] = "replacement"
    with pytest.raises(RuntimeError, match="changed in the cluster"):
        verify_completion(proof, **kwargs)
