"""Compose product ordering with the Ubuntu networkd/FRR startup graph."""

from configparser import ConfigParser
from graphlib import TopologicalSorter
from importlib.resources import files


def test_underlay_waits_for_physical_network_without_frr_ordering_cycle():
    # FRR participates in bringing networking up, so it precedes network.target.
    # Waiting for the aggregate online target before FRR creates a cycle during
    # both startup and the reversed shutdown ordering.
    graph = {
        "network.target": {"frr.service", "systemd-networkd.service"},
        "network-online.target": {
            "network.target",
            "systemd-networkd-wait-online.service",
        },
        "systemd-networkd-wait-online.service": {"systemd-networkd.service"},
    }
    unit_name = "nebius-vpngw-underlay.service"
    unit = ConfigParser(interpolation=None)
    unit.read_string(files("nebius_vpngw").joinpath("systemd", unit_name).read_text())
    graph[unit_name] = set(unit["Unit"].get("After", "").split())
    for successor in unit["Unit"].get("Before", "").split():
        graph.setdefault(successor, set()).add(unit_name)

    tuple(TopologicalSorter(graph).static_order())

    def predecessors(name):
        result = set()
        pending = list(graph.get(name, ()))
        while pending:
            item = pending.pop()
            if item not in result:
                result.add(item)
                pending.extend(graph.get(item, ()))
        return result

    assert {
        "systemd-networkd-wait-online.service",
        "nebius-vpngw-vm-ha-guard.service",
    } <= predecessors(unit_name)
    assert unit_name in predecessors("frr.service")
    assert unit_name in predecessors("nebius-vpngw-vm-ha.service")
    assert "systemd-networkd-wait-online.service" in {
        *unit["Unit"].get("Wants", "").split(),
        *unit["Unit"].get("Requires", "").split(),
    }
