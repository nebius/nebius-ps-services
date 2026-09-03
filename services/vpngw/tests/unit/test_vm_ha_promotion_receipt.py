from __future__ import annotations

import pytest

from nebius_vpngw.agent.vm_ha.promotion_receipt import promotion_receipt_id_v1


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (
            "planned-failover",
            "4073e7764558a652360368767ff4c09fc54d94e99ccb2d435a673f8ea7b5ce7e",
        ),
        (
            "planned-failback",
            "f20ec94b1eb8ef5f119d197a04a15845f52e3fbc6cfce4bd0d6da9013838b4b3",
        ),
        (
            "automatic-failover",
            "4193f7e67e5ce42b4e9c05523eecec388e88328838aca5093fc715340b72714b",
        ),
        (
            "apply-owner-adoption",
            "d9ba66c7c5fb16c2bd5ce3ff5dec0e90ec613cb57521cfc80defb0fe3a45b530",
        ),
    ],
)
def test_promotion_receipt_v1_digest_is_byte_stable(intent: str, expected: str) -> None:
    assert (
        promotion_receipt_id_v1(
            allocation_id="allocation-a",
            first_operation_id="first-effect",
            generation_id="a" * 64,
            intent=intent,
            owner_node_id="node-a",
            ownership_epoch="revision-10",
            route_operation_id="route-operation",
        )
        == expected
    )
