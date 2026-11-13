"""Test-first seven-case Static lifecycle contract with real strongSwan and packets.

These tests deliberately assert behavior not yet implemented. They must not be
xfail'ed or satisfied by test-side route changes, SA promotion, or trap install.
Site A runs one gateway controller. Site B is a plain strongSwan IPsec peer with
no product controller or renderer. Cloud authority is frozen, not qualified here.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from test_ha_tunnel_native import native_paths as native_paths
from tests.systemd.static_recovery_evidence import child_signatures, packet_report

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires the explicit disposable Linux systemd/XFRM fixture",
    ),
    pytest.mark.parametrize("native_paths", ["static-lifecycle"], indirect=True),
]

DRIVER = "/workspace/tests/systemd/static_recovery_driver.py"
RECOVERY_SECONDS = 120
FAILBACK_SECONDS = 160


class Scenario:
    def __init__(self, nodes, directory, record_property):
        self.gateway, self.peer = nodes
        self.directory = directory
        self.record_property = record_property
        self.events = []
        self.blocked = set()
        self.traffic = []
        self.controller = None
        self.controller_log = None
        self.last_fault_at = 0.0
        self.observations = Path(f"/proc/{self.gateway.process.pid}/root/run/static-recovery.jsonl")
        self.commands = self.observations.with_suffix(".commands.jsonl")

    def mark(self, event, **fields):
        value = {"event": event, "at": time.time(), **fields}
        self.events.append(value)
        return value["at"]

    def spawn(self, endpoint, argv, output):
        return subprocess.Popen(
            ["nsenter", "-t", str(endpoint.process.pid), "-m", "-n", "--", *argv],
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "LC_ALL": "C"},
        )

    def start(self):
        self.controller_log = (self.directory / "controller.log").open("w")
        self.controller = self.spawn(
            self.gateway,
            [sys.executable, "-u", DRIVER, str(self.gateway.config), "/run/static-recovery.jsonl"],
            self.controller_log,
        )
        for direction, source, target in (
            ("outbound", self.gateway, self.peer),
            ("inbound", self.peer, self.gateway),
        ):
            path = self.directory / f"{direction}.log"
            output = path.open("w")
            process = self.spawn(
                source,
                [
                    "ping",
                    "-D",
                    "-n",
                    "-O",
                    "-i",
                    "0.2",
                    "-c",
                    "10000",
                    "-W",
                    "1",
                    "-I",
                    source.address,
                    target.address,
                ],
                output,
            )
            self.traffic.append((process, output, path))
        self.wait(lambda sample: sample["selected"] == "preferred", seconds=15)
        self.clean(5)
        self.assert_cold("backup")
        settings = self.latest()["settings"]
        assert settings["backup"]["dpd_action"] == settings["preferred"]["dpd_action"] == "restart"
        assert settings["backup"]["keyingtries"] == settings["preferred"]["keyingtries"] == "0"
        self.mark("baseline-qualified")

    def reports(self):
        reports = []
        for process, _output, path in self.traffic:
            assert process.poll() is None, "Packet generator exited during the scenario"
            reports.append(packet_report(path.read_text()))
        return reports

    def latest(self):
        assert self.controller.poll() is None, "Controller failed: " + self.controller_log.name
        if not self.observations.exists():
            return None
        for line in reversed(self.observations.read_text().splitlines()):
            try:
                return json.loads(line)
            except ValueError:
                continue
        return None

    def wait(self, predicate, *, seconds=RECOVERY_SECONDS):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.reports()
            sample = self.latest()
            if sample is not None and sample["at"] >= self.last_fault_at and predicate(sample):
                return sample
            time.sleep(0.2)
        pytest.fail(f"Lifecycle condition did not converge within {seconds}s; see {self.directory}")

    def hold(self, seconds, check=lambda _sample: None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.reports()
            sample = self.latest()
            assert sample is not None
            check(sample)
            time.sleep(0.2)

    def clean(self, seconds=5):
        started = time.time()
        deadline = time.monotonic() + RECOVERY_SECONDS
        clean_since = None
        while time.monotonic() < deadline:
            now = time.time()
            healthy = True
            for report in self.reports():
                recent = [row for row in report["replies"] if row[0] >= started]
                if not recent or now - recent[-1][0] > 1:
                    healthy = False
                for previous, current in zip(recent, recent[1:], strict=False):
                    if current[1] != previous[1] + 1:
                        started = current[0]
                        healthy = False
            if not healthy:
                clean_since = None
            elif clean_since is None:
                clean_since = time.monotonic()
            elif time.monotonic() - clean_since >= seconds:
                self.mark("traffic-clean", seconds=seconds)
                return
            time.sleep(0.2)
        pytest.fail("Bidirectional traffic did not regain a clean window")

    def fault(self, name, *, blocked):
        address = {"preferred": "192.0.2.2", "backup": "192.0.2.3"}[name]
        assert (name in self.blocked) != blocked
        method = "-I" if blocked else "-D"
        for chain, flag in (("OUTPUT", "-d"), ("INPUT", "-s")):
            self.gateway.command("iptables", method, chain, flag, address, "-j", "DROP")
        if blocked:
            self.blocked.add(name)
        else:
            self.blocked.remove(name)
        self.last_fault_at = self.mark("path-blocked" if blocked else "path-returned", path=name)
        return self.last_fault_at

    def select(self, name, *, seconds=RECOVERY_SECONDS):
        def settled(sample):
            operation = sample["operation"]
            return (
                sample["selected"] == name
                and sample["health"].get("site") == "healthy"
                and (operation is None or operation["phase"] == "succeeded")
            )

        sample = self.wait(settled, seconds=seconds)
        self.mark("path-selected", path=name)
        return sample

    def assert_cold(self, name):
        def cold(sample):
            return (
                not sample["sas"].get(name)
                and sample["settings"].get(name, {}).get("start_action") == "none"
            )

        self.wait(cold, seconds=30)

    def backup_carries_traffic(self):
        self.fault("preferred", blocked=True)
        observed = self.wait(lambda sample: bool(child_signatures(sample, "backup")))
        before = child_signatures(observed, "backup")
        self.mark("backup-established", signatures=sorted(before))
        sample = self.select("backup")
        assert sample["settings"]["backup"]["start_action"] == "trap"
        assert sample["settings"]["backup"]["dpd_action"] == "restart"
        assert sample["settings"]["backup"]["keyingtries"] == "0"
        assert child_signatures(sample, "backup") == before, "Promotion recreated or added a CHILD"
        self.clean()
        self.assert_cold("preferred")

    def both_fail(self):
        self.backup_carries_traffic()
        failed_at = self.fault("backup", blocked=True)
        self.wait(lambda sample: sample["health"].get("site") == "down")
        self.hold(35)
        attempts = []
        for line in self.commands.read_text().splitlines():
            value = json.loads(line)
            if (
                value["at"] >= failed_at
                and "--initiate" in value["argv"]
                and value["phase"] == "intent"
            ):
                attempts.append(value)
                assert not value["preexisting_connecting"], (
                    "Initiation duplicated a pending attempt"
                )
        assert any("preferred" in attempt["argv"] for attempt in attempts), (
            "Controller never tried the primary while selected backup was failing"
        )
        assert self.latest()["settings"]["backup"]["start_action"] == "trap"

    def finish_on(self, winner):
        sample = self.select(winner)
        assert sample["settings"][winner]["start_action"] == "trap"
        self.clean()
        loser = "backup" if winner == "preferred" else "preferred"
        self.assert_cold(loser)
        if winner == "preferred":
            self.hold(10, lambda current: self.assert_stable(current, winner, loser))
        else:
            # Later bounded preferred-path qualification remains allowed. It
            # must not move routes before the failback stability gate passes.
            def backup_remains_selected(current):
                assert current["selected"] == "backup"

            self.hold(10, backup_remains_selected)

    @staticmethod
    def assert_stable(sample, winner, loser):
        assert sample["selected"] == winner
        assert not sample["sas"].get(loser), "Losing path restarted after demotion"

    def close(self):
        # Preserve all packets, including leading/trailing losses, even on a red test.
        for process, output, _path in self.traffic:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            output.close()
        if self.controller is not None and self.controller.poll() is None:
            os.killpg(self.controller.pid, signal.SIGTERM)
            try:
                self.controller.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(self.controller.pid, signal.SIGKILL)
                self.controller.wait(timeout=5)
        if self.controller_log is not None:
            self.controller_log.close()
        if self.observations.exists():
            (self.directory / "controller.jsonl").write_bytes(self.observations.read_bytes())
        if self.commands.exists():
            (self.directory / "commands.jsonl").write_bytes(self.commands.read_bytes())
        summary = {"events": self.events, "directions": {}}
        for _process, _output, path in self.traffic:
            report = packet_report(path.read_text())
            report.pop("replies")
            summary["directions"][path.stem] = report
        (self.directory / "traffic-summary.json").write_text(json.dumps(summary, indent=2))
        self.record_property("static_lifecycle_packets", json.dumps(summary))
        archive = Path("/tmp/static-recovery-evidence") / self.directory.parent.name
        archive.parent.mkdir(exist_ok=True)
        shutil.copytree(self.directory, archive)


@pytest.fixture
def scenario(native_paths, tmp_path, record_property):
    directory = tmp_path / "evidence"
    directory.mkdir()
    case = Scenario(native_paths[0], directory, record_property)
    try:
        case.start()
        yield case
    finally:
        case.close()


def test_case_1_primary_returns_before_backup_qualifies(scenario):
    scenario.fault("backup", blocked=True)
    scenario.fault("preferred", blocked=True)
    scenario.hold(40)
    scenario.fault("preferred", blocked=False)
    scenario.select("preferred")
    scenario.clean()
    scenario.assert_cold("backup")
    scenario.hold(10, lambda sample: scenario.assert_stable(sample, "preferred", "backup"))


def test_case_2_primary_stays_down_backup_promoted_without_sa_recreation(scenario):
    scenario.backup_carries_traffic()
    scenario.finish_on("backup")


def test_case_3_both_fail_backup_returns_first(scenario):
    scenario.both_fail()
    scenario.fault("backup", blocked=False)
    scenario.finish_on("backup")


def test_case_4_both_fail_primary_returns_first(scenario):
    scenario.both_fail()
    scenario.fault("preferred", blocked=False)
    scenario.finish_on("preferred")


def test_case_5_both_return_primary_eventually_wins_without_oscillation(scenario):
    scenario.both_fail()
    scenario.fault("preferred", blocked=False)
    scenario.fault("backup", blocked=False)
    scenario.select("preferred", seconds=FAILBACK_SECONDS)
    scenario.finish_on("preferred")


def test_case_6_neither_returns_no_false_recovery_then_primary_recovers(scenario):
    scenario.both_fail()

    def still_down(sample):
        assert sample["health"].get("site") != "healthy"

    scenario.hold(60, still_down)
    scenario.fault("preferred", blocked=False)
    scenario.finish_on("preferred")


def test_case_7_remote_primary_return_qualifies_failback_then_stops_backup(scenario):
    scenario.backup_carries_traffic()
    scenario.fault("preferred", blocked=False)
    # Only the remote responder initiates. The test does not select or promote.
    scenario.peer.command("swanctl", "--initiate", "--child", "preferred", "--timeout", "5")
    sample = scenario.wait(lambda value: bool(child_signatures(value, "preferred")))
    primary = child_signatures(sample, "preferred")
    sample = scenario.wait(
        lambda value: value["preferred_qualification"].get("site", {}).get("since") is not None
    )
    qualification_started = sample["preferred_qualification"]["site"]["since"]
    sample = scenario.select("preferred", seconds=FAILBACK_SECONDS)
    assert sample["at"] - qualification_started >= 60, "Preferred qualification was bypassed"
    assert child_signatures(sample, "preferred") == primary, "Failback recreated the proven SA"
    scenario.finish_on("preferred")
