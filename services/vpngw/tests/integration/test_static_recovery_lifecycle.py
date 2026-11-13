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

from nebius_vpngw.ipsec_observation import command as ipsec_command

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
    def __init__(self, nodes, directory, record_property, backup_proof_delay_seconds=0):
        self.gateway, self.peer = nodes
        self.directory = directory
        self.record_property = record_property
        self.events = []
        self.contract_failures = []
        self.blocked = set()
        self.traffic = []
        self.controller = None
        self.controller_log = None
        self.last_fault_at = 0.0
        self.backup_proof_delay_seconds = backup_proof_delay_seconds
        self.observations = Path(f"/proc/{self.gateway.process.pid}/root/run/static-recovery.jsonl")
        self.commands = self.observations.with_suffix(".commands.jsonl")

    def mark(self, event, **fields):
        value = {"event": event, "at": time.time(), **fields}
        self.events.append(value)
        return value["at"]

    def contract(self, condition, message):
        if condition:
            return
        self.contract_failures.append(message)
        self.mark("contract-failed", message=message)

    def verify_contract(self):
        assert not self.contract_failures, "\n".join(self.contract_failures)

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
            [
                sys.executable, "-u", DRIVER, str(self.gateway.config),
                "/run/static-recovery.jsonl", "--backup-proof-delay-seconds",
                str(self.backup_proof_delay_seconds),
            ],
            self.controller_log,
        )
        self.start_traffic()
        self.wait(lambda sample: sample["selected"] == "preferred", seconds=15)
        self.clean(5)
        self.assert_cold("backup")
        self.initial_settings = self.latest()["settings"]
        self.mark("baseline-qualified")

    def start_traffic(self):
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
            if (
                operation is not None
                and operation["target"] == name
                and operation["phase"] == "failed"
                and operation["result"] == "suspension-scope-unproven"
            ):
                pytest.fail("Recovery stopped at terminal suspension-scope-unproven")
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
        self.contract(
            self.initial_settings["backup"]["dpd_action"] == "restart",
            "Backup was created without dpd_action=restart required for SA-preserving promotion",
        )
        self.contract(
            self.initial_settings["backup"]["keyingtries"] == "0",
            "Backup was created without keyingtries=0",
        )
        sample = self.select("backup")
        for setting, expected in (
            ("start_action", "trap"), ("dpd_action", "restart"), ("keyingtries", "0")
        ):
            self.contract(
                sample["settings"]["backup"][setting] == expected,
                f"Promoted backup did not have {setting}={expected}",
            )
        self.contract(
            child_signatures(sample, "backup") == before,
            "Promotion recreated or added a CHILD",
        )
        self.clean()
        self.mark("backup-carrying-traffic")
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
                self.contract(
                    not value["preexisting_connecting"],
                    "Initiation duplicated a pending attempt",
                )
        self.contract(
            any("preferred" in attempt["argv"] for attempt in attempts),
            "Controller never tried the primary while selected backup was failing",
        )
        self.contract(
            self.latest()["settings"]["backup"]["start_action"] == "trap",
            "Failing selected backup did not retain start_action=trap",
        )
        self.mark("both-paths-failed-observed")

    def finish_on(self, winner):
        sample = self.select(winner)
        # Trap-only promotion was requested for the backup. The preferred
        # connection retains its configured native trap/start behavior.
        expected_start = "trap" if winner == "backup" else "trap|start"
        self.contract(
            sample["settings"][winner]["start_action"] == expected_start,
            f"Selected {winner} did not have start_action={expected_start}",
        )
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
        self.verify_contract()

    @staticmethod
    def assert_stable(sample, winner, loser):
        assert sample["selected"] == winner
        assert not sample["sas"].get(loser), "Losing path restarted after demotion"

    def close(self):
        # Read-only, key-free observations from both independent sites. These
        # commands never repair routes, SAs or configuration to advance a test.
        diagnostics = {}
        for name, endpoint in (("gateway", self.gateway), ("peer", self.peer)):
            commands = {
                "sas": ipsec_command(),
                "states": ("ip", "xfrm", "state", "list", "nokeys"),
                "policies": ("ip", "-s", "xfrm", "policy"),
                "routes": ("ip", "-j", "-4", "route", "show", "table", "all"),
                "xfrm_stat": ("cat", "/proc/net/xfrm_stat"),
            }
            diagnostics[name] = {}
            for label, argv in commands.items():
                try:
                    result = endpoint.command(*argv)
                    diagnostics[name][label] = result.stdout
                except (AssertionError, OSError, subprocess.SubprocessError) as error:
                    diagnostics[name][label] = {"error": type(error).__name__}
        (self.directory / "native-final.json").write_text(json.dumps(diagnostics, indent=2))
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
        summary = {
            "events": self.events,
            "contract_failures": self.contract_failures,
            "directions": {},
        }
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
def scenario(native_paths, tmp_path, record_property, request):
    directory = tmp_path / "evidence"
    directory.mkdir()
    case = Scenario(native_paths[0], directory, record_property, getattr(request, "param", 0))
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
    scenario.verify_contract()


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
