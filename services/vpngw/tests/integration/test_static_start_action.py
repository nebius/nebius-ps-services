"""Compare native reload and packet recovery, without a product controller.

This isolates start_action from the controller's destructive activation path.
SA changes are observations. Only failure to recover traffic fails the trial.
"""

import json
import os
import time
from pathlib import Path

import pytest
from test_static_recovery_lifecycle import Scenario
from test_static_recovery_lifecycle import native_paths as native_paths
from tests.systemd.static_recovery_evidence import bounded_packet_window
from tests.systemd.static_recovery_driver import loaded_settings

from nebius_vpngw.agent.vm_ha.tunnel_observation import parse_sas
from nebius_vpngw.ipsec_observation import command as ipsec_command

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires the explicit disposable Linux systemd/XFRM fixture",
    ),
    pytest.mark.parametrize("native_paths", ["static-lifecycle"], indirect=True),
]


def set_start_action(endpoint, action):
    path = Path(f"/proc/{endpoint.process.pid}/root/etc/swanctl/swanctl.conf")
    connection = None
    replacements = 0
    lines = []
    for line in path.read_text().splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.endswith(" {"):
            connection = line.strip().split()[0]
        if connection == "preferred" and line.strip().startswith("start_action ="):
            line = "        start_action = " + action
            replacements += 1
        lines.append(line)
    assert replacements == 1, "Fixture could not identify exactly one preferred start_action"
    path.write_text("\n".join(lines) + "\n")
    endpoint.command("swanctl", "--load-all", "--noprompt")


def sa_facts(endpoint):
    rows = []
    for name, parent in parse_sas(endpoint.command(*ipsec_command()).stdout):
        if name != "preferred":
            continue
        for child in parent.get("child-sas", {}).values():
            row = {"ike": parent["uniqueid"]}
            for key in ("uniqueid", "state", "reqid", "spi-in", "spi-out", "if-id-in", "if-id-out"):
                row[key] = child.get(key)
            rows.append(row)
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def observe_packets(case, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        case.reports()
        time.sleep(0.2)


@pytest.mark.parametrize(
    ("trial", "start_action"),
    [(1, "trap"), (1, "trap|start"), (2, "trap"), (2, "trap|start")],
)
def test_start_action_reload_and_active_path_recovery(
    native_paths, tmp_path, record_property, trial, start_action
):
    directory = tmp_path / "evidence"
    directory.mkdir()
    case = Scenario(native_paths[0], directory, record_property)
    result = {"trial": trial, "start_action": start_action, "controller_running": False}
    try:
        original = sa_facts(case.gateway)
        assert original, "Fixture did not establish its initial SA"
        set_start_action(case.gateway, "none")
        config_path = Path(f"/proc/{case.gateway.process.pid}/root/etc/swanctl/swanctl.conf")
        result["prepared_settings"] = loaded_settings(config_path.read_text())["preferred"]
        assert result["prepared_settings"]["dpd_action"] == "restart"
        assert result["prepared_settings"]["keyingtries"] == "0"
        time.sleep(1)
        assert sa_facts(case.gateway) == original, "Preparation changed the established SA"
        case.start_traffic()
        case.clean(5)
        before = sa_facts(case.gateway)
        result["before_reload"] = before
        promotion_started = case.mark("start-action-reload", action=start_action)
        set_start_action(case.gateway, start_action)
        observe_packets(case, 10)
        promotion_ended = case.mark("reload-observation-complete")
        result["after_reload"] = sa_facts(case.gateway)
        result["reloaded_settings"] = loaded_settings(config_path.read_text())["preferred"]
        case.clean(5)
        result["promotion_packets"] = {}
        for direction, report in zip(("outbound", "inbound"), case.reports(), strict=True):
            result["promotion_packets"][direction] = bounded_packet_window(
                report, promotion_started, promotion_ended
            )
            assert result["promotion_packets"][direction]["complete"]
        fault_started = case.fault("preferred", blocked=True)
        observe_packets(case, 20)
        returned = case.fault("preferred", blocked=False)
        result["blocked_seconds"] = returned - fault_started
        case.clean(5)
        result["after_recovery"] = sa_facts(case.gateway)
        result["recovery_packets"] = {}
        for direction, report in zip(("outbound", "inbound"), case.reports(), strict=True):
            after_return = [row for row in report["replies"] if row[0] >= returned]
            result["recovery_packets"][direction] = {
                **bounded_packet_window(report, fault_started, returned),
                "first_reply_after_unblock_seconds": after_return[0][0] - returned,
            }
        result["traffic_recovered"] = True
    finally:
        result["events"] = case.events
        (directory / "start-action-comparison.json").write_text(json.dumps(result, indent=2))
        case.close()
