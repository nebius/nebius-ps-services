from types import SimpleNamespace

import pytest

from nebius_cxcli.deployment_retirement import freeze_capacity


@pytest.mark.parametrize("autoscaling", [False, True])
def test_freeze_uses_exact_provider_identity_and_resource_version(monkeypatch, autoscaling):
    import nebius.api.nebius.mk8s.v1 as api
    from nebius.api.nebius.common.v1 import ResourceMetadata

    group = api.NodeGroup(
        metadata=ResourceMetadata(id="group", resource_version=7),
        spec=api.NodeGroupSpec(
            **(
                {"autoscaling": api.NodeGroupAutoscalingSpec(min_node_count=1, max_node_count=4)}
                if autoscaling
                else {"fixed_node_count": 2}
            )
        ),
    )
    writes = []

    def update(request):
        writes.append(request)
        group.spec = request.spec
        return SimpleNamespace(wait=lambda: None)

    monkeypatch.setattr(
        api,
        "NodeGroupServiceClient",
        lambda _: SimpleNamespace(
            get=lambda request: SimpleNamespace(wait=lambda: group), update=update
        ),
    )
    freeze_capacity(object(), provider_id="group", count=2)
    if autoscaling:
        assert len(writes) == 1
        assert writes[0].metadata.resource_version == 7
        assert writes[0].metadata.id == "group"
        assert group.spec.autoscaling.min_node_count == group.spec.autoscaling.max_node_count == 2
        freeze_capacity(object(), provider_id="group", count=2)
        assert len(writes) == 1
    else:
        assert writes == []
        with pytest.raises(RuntimeError, match="fixed capacity changed"):
            freeze_capacity(object(), provider_id="group", count=1)
