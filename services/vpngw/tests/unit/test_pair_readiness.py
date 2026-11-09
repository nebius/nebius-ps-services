import hashlib
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
import yaml

from nebius_vpngw import pair_readiness as readiness


def test_readiness_diagnostics_never_copy_untrusted_exception_text():
    def unavailable():
        raise RuntimeError("TOP_SECRET remote exception")

    with pytest.raises(readiness.ReadinessFailure) as error:
        readiness.checked("pair-cloud", "cloud-observation-unavailable", unavailable)
    assert "TOP_SECRET" not in str(error.value)
    assert error.value.failure.phase == "pair-cloud"
    with pytest.raises(readiness.ReadinessFailure) as outer:
        readiness.checked(
            "pair-configuration",
            "configuration-unavailable",
            lambda: readiness.checked("pair-cloud", "cloud-observation-unavailable", unavailable),
        )
    assert outer.value.failure == error.value.failure


def route(prefix="10.20.0.0/16", allocation="allocation"):
    return NS(spec=NS(destination=NS(cidr=prefix), next_hop=NS(allocation=NS(id=allocation))))


@pytest.mark.parametrize(
    "rows,reason",
    [
        ([], "missing"),
        ([route(allocation="foreign")], "next hop"),
        ([route(), route()], "missing"),
        ([route(), route("10.20.1.0/24", "foreign")], "overriding"),
    ],
)
def test_ordinary_cloud_readiness_requires_complete_unambiguous_coverage(rows, reason):
    observed = {
        "allocations": {0: "allocation"},
        "active_tables": ["table"],
        "routes": {"table": rows},
    }
    with pytest.raises(RuntimeError, match=reason):
        readiness.cloud_coverage(observed, ["10.20.0.0/16"])


def test_cloud_readiness_checks_every_workload_target():
    observed = {
        "allocations": {0: "allocation"},
        "active_tables": ["a", "b"],
        "routes": {"a": [route()], "b": []},
    }
    with pytest.raises(RuntimeError, match="missing"):
        readiness.cloud_coverage(observed, ["10.20.0.0/16"])
    observed["routes"]["b"] = [route()]
    readiness.cloud_coverage(observed, ["10.20.0.0/16"])


def test_ha_readiness_keeps_required_prefix_gate_beyond_existing_route_health(monkeypatch):
    from nebius_vpngw import cli

    monkeypatch.setattr(cli, "_vm_ha_cloud_authority", lambda *a: NS(condition="exact"))
    observed = {
        "route_targets": [{"route_table_id": "table"}],
        "routes": [
            {"route_table_id": "table", "prefix": "10.30.0.0/16", "allocation_id": "shared"}
        ],
    }
    with pytest.raises(RuntimeError, match="lacks its exact"):
        readiness.ha_cloud_coverage(NS(allocation_id="shared"), observed, ["10.20.0.0/16"])
    observed["routes"][0]["prefix"] = "10.20.0.0/16"
    readiness.ha_cloud_coverage(NS(allocation_id="shared"), observed, ["10.20.0.0/16"])
    monkeypatch.setattr(cli, "_vm_ha_cloud_authority", lambda *a: NS(condition="unknown"))
    with pytest.raises(RuntimeError, match="not exact"):
        readiness.ha_cloud_coverage(NS(allocation_id="shared"), observed, ["10.20.0.0/16"])


def test_native_readiness_reuses_encryption_and_protection_without_repair(tmp_path, monkeypatch):
    from pathlib import Path

    from nebius_vpngw.agent import ordinary, ordinary_cloud_routes

    path = tmp_path / "config"
    cfg = {"connections": []}
    raw = yaml.safe_dump(cfg).encode()
    path.write_bytes(raw)
    monkeypatch.setattr(ordinary, "CONFIG", path)
    read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, *a, **kw: (
            "1" if str(self) == "/proc/sys/net/ipv4/ip_forward" else read(self, *a, **kw)
        ),
    )
    protection = NS(verify=Mock())
    monkeypatch.setattr(ordinary, "destination_protection", lambda _: protection)
    encrypted = Mock()
    monkeypatch.setattr(ordinary_cloud_routes, "encrypted_routes", encrypted)
    result = readiness.native(hashlib.sha256(raw).hexdigest(), ["10.20.0.0/16"])
    assert result["encrypted_routes"] == "verified"
    encrypted.assert_called_once_with(cfg, ["10.20.0.0/16"])
    protection.verify.assert_called_once()
    encrypted.side_effect = RuntimeError("exact CHILD mismatch")
    with pytest.raises(RuntimeError, match="CHILD mismatch"):
        readiness.native(hashlib.sha256(raw).hexdigest(), ["10.20.0.0/16"])
    with pytest.raises(RuntimeError, match="generation changed"):
        readiness.native("0" * 64, ["10.20.0.0/16"])
