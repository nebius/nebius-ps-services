from __future__ import annotations

import sys
from pathlib import Path

from nebius_vpngw.agent import fix_routes


def test_fix_routes_uses_role_aware_periodic_dispatcher(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("gateway:\n  local_prefixes: []\n", encoding="utf-8")
    observed: list[dict[str, object]] = []
    monkeypatch.setattr(
        fix_routes, "enforce_periodic_routing_invariants", observed.append
    )
    monkeypatch.setattr(
        sys, "argv", ["nebius-vpngw-fix-routes", "--config", str(config)]
    )

    fix_routes.main()

    assert observed == [{"gateway": {"local_prefixes": []}}]
