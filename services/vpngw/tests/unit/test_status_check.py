from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nebius_vpngw.agent.status_check import check_routing_health


@pytest.mark.parametrize(
    ("rules", "table_220", "broad_apipa", "expected_table", "expected_broad"),
    (
        ("", "", "", False, False),
        ("220: from all lookup 220\n", "", "", True, False),
        (
            "220: from all lookup main\n100: from all lookup 2200\n",
            "",
            "",
            False,
            False,
        ),
        ("", "10.10.0.0/24 dev xfrm0\n", "", True, False),
        ("", "", "169.254.0.0/16 dev eth0\n", False, True),
    ),
)
def test_check_routing_health_detects_exact_passive_hygiene_drift(
    monkeypatch: pytest.MonkeyPatch,
    rules: str,
    table_220: str,
    broad_apipa: str,
    expected_table: bool,
    expected_broad: bool,
) -> None:
    def run(argv, **_kwargs):
        outputs = {
            ("ip", "rule", "show"): rules,
            ("ip", "-j", "-4", "route", "show", "table", "all"): json.dumps(
                []
                if not table_220
                else [{"table": 220, "dst": table_220.split()[0]}]
            ),
            ("ip", "route", "show", "169.254.0.0/16"): broad_apipa,
            ("ip", "route", "show"): "",
        }
        return SimpleNamespace(returncode=0, stdout=outputs[tuple(argv)])

    monkeypatch.setattr("nebius_vpngw.agent.status_check.subprocess.run", run)

    health = check_routing_health()

    assert health["table_220_exists"] is expected_table
    assert health["broad_apipa_exists"] is expected_broad
    assert health["overall_status"] == (
        "error" if expected_table or expected_broad else "healthy"
    )


def test_check_routing_health_fails_closed_when_hygiene_is_unobservable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run(argv, **_kwargs):
        return SimpleNamespace(
            returncode=(
                1
                if tuple(argv)
                == ("ip", "-j", "-4", "route", "show", "table", "all")
                else 0
            ),
            stdout="",
        )

    monkeypatch.setattr("nebius_vpngw.agent.status_check.subprocess.run", run)

    health = check_routing_health()

    assert health["table_220_exists"] is False
    assert health["broad_apipa_exists"] is False
    assert health["overall_status"] == "error"
