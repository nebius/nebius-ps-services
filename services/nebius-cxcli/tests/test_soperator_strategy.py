from __future__ import annotations

import pytest

from nebius_cxcli.soperator_strategy import SoperatorStrategy, plan_soperator_strategy


@pytest.mark.parametrize(
    ("source_release", "source_contract", "expected"),
    [
        (None, None, SoperatorStrategy.INSTALL),
        ("4.1.6", "upstream-flux-v1", SoperatorStrategy.IN_PLACE),
        (
            "1.22.0",
            "protected-data-plane-v1",
            SoperatorStrategy.PROTECTED_DATA_PLANE,
        ),
        ("4.1.7", "upstream-flux-v1", SoperatorStrategy.NOOP),
    ],
)
def test_capability_graph_selects_strategy_without_release_pair_dispatch(
    source_release: str | None,
    source_contract: str | None,
    expected: SoperatorStrategy,
) -> None:
    plan = plan_soperator_strategy(
        source_release=source_release,
        target_release="4.1.7",
        source_contract=source_contract,
        target_contract="upstream-flux-v1",
    )
    assert plan.strategy is expected


def test_strategy_rejects_downgrade() -> None:
    with pytest.raises(ValueError, match="downgrade"):
        plan_soperator_strategy(
            source_release="4.2.0",
            target_release="4.1.7",
            source_contract="upstream-flux-v1",
            target_contract="upstream-flux-v1",
        )


def test_strategy_rejects_unknown_capability_edge() -> None:
    with pytest.raises(ValueError, match="no reviewed"):
        plan_soperator_strategy(
            source_release="3.0.0",
            target_release="4.1.7",
            source_contract="unknown-v1",
            target_contract="upstream-flux-v1",
        )
