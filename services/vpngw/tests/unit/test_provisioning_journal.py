import base64
from copy import deepcopy
from types import SimpleNamespace

import pytest
from nebius.api.nebius.compute import v1 as compute_api
from test_provisioning_plan import compute, disk

from nebius_vpngw.deploy.provisioning_journal import ProvisioningJournal
from nebius_vpngw.deploy.provisioning_plan import Request
from nebius_vpngw.deploy.replacement_cloud import Cloud, message_digest


def plan():
    return {
        "project_id": "project-a",
        "gateway_name": "gateway",
        "configuration_sha256": "a" * 64,
        "artifact_sha256": "b" * 64,
        "requests": [r.mapping() for r in (disk(), compute())],
        "observed": {},
        "inventories": [],
        "bootstraps": {"gateway-0": {"template_sha256": "c" * 64, "trust_sha256": "d" * 64}},
    }


def operation(identifier):
    return SimpleNamespace(
        id="operation-" + identifier,
        resource_id=identifier,
        sync_wait=lambda **kwargs: None,
        successful=lambda: True,
    )


@pytest.mark.parametrize("boundary", ["submission-reply", "settlement-reply"])
def test_interrupted_creation_retains_exact_request_and_key_without_private_data(
    tmp_path, monkeypatch, boundary
):
    desired = plan()
    private = "private-bootstrap-fixture-never-journaled"
    captured = []
    accepted = operation("new-compute")

    def submit(request, **options):
        captured.append((message_digest(request), options["metadata"]))
        assert options["retries"] == 0
        if boundary == "submission-reply":
            raise TimeoutError("response lost")
        accepted.sync_wait = lambda **kwargs: (_ for _ in ()).throw(TimeoutError("wait lost"))
        return SimpleNamespace(wait=lambda: accepted)

    def restore(journal, record):
        recipe = next(Request.parse(r) for r in desired["requests"] if r["key"] == record["label"])
        return recipe.resolve(
            journal.state["bindings"], bootstrap=private if recipe.guest else None
        )

    with ProvisioningJournal("project-a", "gateway", root=tmp_path / "state") as journal:
        journal.begin(desired, approval="e" * 64, execution_id="f" * 32)
        cloud = Cloud(object(), journal, operation_scope="f" * 32)
        request = disk().resolve({})
        journal.stage(disk(), request)
        cloud.mutate(
            "disk", request, lambda *a, **kw: SimpleNamespace(wait=lambda: operation("new-disk"))
        )
        journal.verified(disk(), request)
        request = compute().resolve(journal.state["bindings"], bootstrap=private)
        journal.stage(compute(), request)
        with pytest.raises(TimeoutError):
            cloud.mutate("compute", request, submit)
        state = journal.path.read_text()
        assert private not in state and base64.b64encode(private.encode()).decode() not in state
        assert "cloud_init_user_data" not in state

    accepted.sync_wait = lambda **kwargs: None

    def replay(request, **options):
        captured.append((message_digest(request), options["metadata"]))
        return SimpleNamespace(wait=lambda: accepted)

    monkeypatch.setattr(
        compute_api, "InstanceServiceClient", lambda _: SimpleNamespace(create=replay)
    )
    monkeypatch.setattr(
        "nebius.api.nebius.common.v1.OperationServiceClient",
        lambda _: SimpleNamespace(get=lambda *a, **kw: SimpleNamespace(wait=lambda: accepted)),
    )
    with ProvisioningJournal("project-a", "gateway", root=tmp_path / "state") as journal:
        journal.begin(desired, approval="e" * 64, execution_id="f" * 32)
        cloud = Cloud(
            object(),
            journal,
            operation_scope="f" * 32,
            restore_request=lambda r: restore(journal, r),
        )
        cloud.settle()
        resolved = compute().resolve(journal.state["bindings"], bootstrap=private)
        journal.verified(compute(), resolved)
        receipt = journal.complete()
        completed = journal.path.read_bytes()
        journal.begin(desired, approval="e" * 64, execution_id="f" * 32)
        assert journal.complete() == receipt
        assert journal.path.read_bytes() == completed
        assert journal.state["bindings"]["compute"]["resource_id"] == "new-compute"
    assert len(captured) == (2 if boundary == "submission-reply" else 1)
    assert all(item == captured[0] for item in captured)


def test_unproven_creation_binding_and_late_scope_change_are_rejected(tmp_path):
    desired = plan()
    with ProvisioningJournal("project-a", "gateway", root=tmp_path / "state") as journal:
        journal.begin(desired, approval="e" * 64, execution_id="f" * 32)
        original = journal.path.read_bytes()
        changed = deepcopy(desired)
        changed["artifact_sha256"] = "1" * 64
        with pytest.raises(RuntimeError, match="original command and intent"):
            journal.begin(changed, approval="e" * 64, execution_id="f" * 32)
        forged = deepcopy(journal.state)
        forged["bindings"]["disk"] = {"resource_id": "same-name-resource"}
        with pytest.raises(ValueError, match="journal is invalid"):
            journal.validate(forged)
        with pytest.raises(RuntimeError, match="prerequisites"):
            journal.stage(compute(), compute().template())
        assert journal.path.read_bytes() == original


def test_repeated_premature_completion_never_returns_a_receipt(tmp_path):
    with ProvisioningJournal("project-a", "gateway", root=tmp_path / "state") as journal:
        journal.begin(plan(), approval="e" * 64, execution_id="f" * 32)
        original = journal.path.read_bytes()
        for _ in range(2):
            with pytest.raises((ValueError, RuntimeError)):
                journal.complete()
            assert journal.state["transaction"]["phase"] == "pending"
            assert journal.path.read_bytes() == original
