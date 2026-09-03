from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from misc.vm_ha_one_way_probe import (
    ProbeArguments,
    ProbeError,
    _parse_arguments,
    _ssh_command,
    main,
    parse_probe_output,
)


def test_probe_parser_reports_loss_and_five_reply_recovery_without_endpoints() -> None:
    output = """\
[1000.100] 64 bytes from 10.0.0.2: icmp_seq=1 ttl=62 time=8.1 ms
[1000.300] 64 bytes from 10.0.0.2: icmp_seq=2 ttl=62 time=8.2 ms
[1000.900] 64 bytes from 10.0.0.2: icmp_seq=5 ttl=62 time=8.5 ms
[1001.100] 64 bytes from 10.0.0.2: icmp_seq=6 ttl=62 time=8.6 ms
[1001.300] 64 bytes from 10.0.0.2: icmp_seq=7 ttl=62 time=8.7 ms
[1001.500] 64 bytes from 10.0.0.2: icmp_seq=8 ttl=62 time=8.8 ms
[1001.700] 64 bytes from 10.0.0.2: icmp_seq=9 ttl=62 time=8.9 ms
9 packets transmitted, 7 received, 22.2222% packet loss, time 1600ms
"""

    records = parse_probe_output(
        output,
        expected_count=9,
        direction_label="nebius-to-gcp",
    )

    assert [record["sequence"] for record in records[:-1]] == [1, 2, 5, 6, 7, 8, 9]
    summary = records[-1]
    assert summary == {
        "schema": "nebius-vpngw/vm-ha-one-way-probe-v1",
        "event": "summary",
        "direction": "nebius-to-gcp",
        "transmitted": 9,
        "received": 7,
        "lost": 2,
        "missing_sequences": [3, 4],
        "loss_percent": 22.222,
        "first_reply_at": 1000.1,
        "last_reply_at": 1001.7,
        "last_loss_sequence": 4,
        "stable_recovery_sequence": 5,
        "stable_recovery_started_at": 1000.9,
        "stable_recovery_confirmed_at": 1001.7,
        "stable_recovery_replies": 5,
        "complete": True,
    }
    assert "10.0.0.2" not in json.dumps(records)


def test_probe_parser_does_not_invent_recovery_after_trailing_loss() -> None:
    output = """\
[1000.100] 64 bytes from 10.0.0.2: icmp_seq=1 ttl=62 time=8.1 ms
[1000.300] 64 bytes from 10.0.0.2: icmp_seq=2 ttl=62 time=8.2 ms
[1000.500] 64 bytes from 10.0.0.2: icmp_seq=3 ttl=62 time=8.3 ms
[1000.700] 64 bytes from 10.0.0.2: icmp_seq=4 ttl=62 time=8.4 ms
[1000.900] 64 bytes from 10.0.0.2: icmp_seq=5 ttl=62 time=8.5 ms
7 packets transmitted, 5 received, 28.571% packet loss, time 1200ms
"""

    summary = parse_probe_output(
        output,
        expected_count=7,
        direction_label="test-direction",
    )[-1]

    assert summary["missing_sequences"] == [6, 7]
    assert summary["stable_recovery_sequence"] is None
    assert summary["stable_recovery_confirmed_at"] is None


def test_probe_parser_requires_chronological_consecutive_recovery() -> None:
    output = """\
[1000.100] 64 bytes from 10.0.0.2: icmp_seq=1 ttl=62 time=8.1 ms
[1000.300] 64 bytes from 10.0.0.2: icmp_seq=3 ttl=62 time=8.3 ms
[1000.500] 64 bytes from 10.0.0.2: icmp_seq=2 ttl=62 time=8.2 ms
[1000.700] 64 bytes from 10.0.0.2: icmp_seq=4 ttl=62 time=8.4 ms
[1000.900] 64 bytes from 10.0.0.2: icmp_seq=5 ttl=62 time=8.5 ms
[1001.100] 64 bytes from 10.0.0.2: icmp_seq=6 ttl=62 time=8.6 ms
6 packets transmitted, 6 received, 0% packet loss, time 1000ms
"""

    summary = parse_probe_output(
        output,
        expected_count=6,
        direction_label="test-direction",
    )[-1]

    assert summary["missing_sequences"] == []
    assert summary["stable_recovery_sequence"] is None


def test_probe_parser_deduplicates_reply_evidence() -> None:
    output = """\
[1000.100] 64 bytes from 10.0.0.2: icmp_seq=1 ttl=62 time=8.1 ms
[1000.110] 64 bytes from 10.0.0.2: icmp_seq=1 ttl=62 time=8.1 ms
[1000.300] 64 bytes from 10.0.0.2: icmp_seq=2 ttl=62 time=8.2 ms
[1000.500] 64 bytes from 10.0.0.2: icmp_seq=3 ttl=62 time=8.3 ms
[1000.700] 64 bytes from 10.0.0.2: icmp_seq=4 ttl=62 time=8.4 ms
[1000.900] 64 bytes from 10.0.0.2: icmp_seq=5 ttl=62 time=8.5 ms
5 packets transmitted, 5 received, 0% packet loss, time 800ms
"""

    records = parse_probe_output(
        output,
        expected_count=5,
        direction_label="test-direction",
    )

    assert [record["sequence"] for record in records[:-1]] == [1, 2, 3, 4, 5]
    assert records[-1]["stable_recovery_sequence"] == 1


def test_probe_uses_fixed_fail_closed_ssh_and_ping_arguments(tmp_path: Path) -> None:
    known_hosts = tmp_path / "known_hosts"
    identity = tmp_path / "id_ed25519"
    known_hosts.write_text("pin\n", encoding="utf-8")
    identity.write_text("key\n", encoding="utf-8")
    identity.chmod(0o600)
    arguments = ProbeArguments(
        ssh_target="observer@192.0.2.10",
        known_hosts_file=known_hosts,
        identity_file=identity,
        destination="198.51.100.20",
        count=50,
        direction_label="test-direction",
    )

    command = _ssh_command(arguments)

    assert command[:3] == ["ssh", "-F", "/dev/null"]
    for option in (
        "BatchMode=yes",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "IdentitiesOnly=yes",
        "StrictHostKeyChecking=yes",
        f"UserKnownHostsFile={known_hosts}",
        "GlobalKnownHostsFile=/dev/null",
        "ProxyCommand=none",
        "ProxyJump=none",
        "ConnectTimeout=10",
        "ConnectionAttempts=1",
        "LogLevel=ERROR",
    ):
        assert option in command
    assert command[command.index("-i") + 1] == str(identity)
    assert command[-2] == "observer@192.0.2.10"
    assert command[-1] == ("env LC_ALL=C ping -n -D -O -i 0.2 -W 1 -c 50 198.51.100.20")


def test_probe_rejects_hostnames_and_requires_explicit_bounded_count(tmp_path: Path) -> None:
    known_hosts = tmp_path / "known_hosts"
    identity = tmp_path / "id_ed25519"
    known_hosts.write_text("pin\n", encoding="utf-8")
    identity.write_text("key\n", encoding="utf-8")
    identity.chmod(0o600)

    with pytest.raises(SystemExit):
        _parse_arguments(
            [
                "--ssh-target",
                "observer@example.invalid",
                "--known-hosts-file",
                str(known_hosts),
                "--identity-file",
                str(identity),
                "--destination",
                "198.51.100.20",
                "--count",
                "0",
                "--direction-label",
                "test-direction",
            ]
        )


def test_probe_requires_one_private_explicit_identity(tmp_path: Path) -> None:
    known_hosts = tmp_path / "known_hosts"
    identity = tmp_path / "id_ed25519"
    known_hosts.write_text("pin\n", encoding="utf-8")
    identity.write_text("key\n", encoding="utf-8")
    identity.chmod(0o644)

    with pytest.raises(SystemExit):
        _parse_arguments(
            [
                "--ssh-target",
                "observer@192.0.2.10",
                "--known-hosts-file",
                str(known_hosts),
                "--identity-file",
                str(identity),
                "--destination",
                "198.51.100.20",
                "--count",
                "10",
                "--direction-label",
                "test-direction",
            ]
        )


@pytest.mark.parametrize(
    "output",
    (
        "ping: sendmsg: Network is unreachable\n1 packets transmitted, 0 received, "
        "100% packet loss, time 0ms\n",
        "1 packets transmitted, 0 received, +1 errors, 100% packet loss, time 0ms\n",
        "1 packets transmitted, 0 received\n",
    ),
)
def test_probe_rejects_runtime_errors_and_incomplete_or_malformed_summaries(
    output: str,
) -> None:
    with pytest.raises(ProbeError, match="ping"):
        parse_probe_output(output, expected_count=1, direction_label="test-direction")


def test_probe_emits_no_partial_json_when_remote_execution_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    known_hosts = tmp_path / "known_hosts"
    identity = tmp_path / "id_ed25519"
    known_hosts.write_text("pin\n", encoding="utf-8")
    identity.write_text("key\n", encoding="utf-8")
    identity.chmod(0o600)
    monkeypatch.setattr(
        "misc.vm_ha_one_way_probe.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=255, stdout="", stderr="private"),
    )

    result = main(
        [
            "--ssh-target",
            "observer@192.0.2.10",
            "--known-hosts-file",
            str(known_hosts),
            "--identity-file",
            str(identity),
            "--destination",
            "198.51.100.20",
            "--count",
            "10",
            "--direction-label",
            "test-direction",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "Probe failed: SSH or remote ping execution failed\n"
    assert "private" not in captured.err


def test_probe_rejects_remote_stderr_without_emitting_partial_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    known_hosts = tmp_path / "known_hosts"
    identity = tmp_path / "id_ed25519"
    known_hosts.write_text("pin\n", encoding="utf-8")
    identity.write_text("key\n", encoding="utf-8")
    identity.chmod(0o600)
    monkeypatch.setattr(
        "misc.vm_ha_one_way_probe.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="1 packets transmitted, 0 received, 100% packet loss, time 0ms\n",
            stderr="private endpoint",
        ),
    )

    result = main(
        [
            "--ssh-target",
            "observer@192.0.2.10",
            "--known-hosts-file",
            str(known_hosts),
            "--identity-file",
            str(identity),
            "--destination",
            "198.51.100.20",
            "--count",
            "1",
            "--direction-label",
            "test-direction",
        ]
    )

    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "Probe failed: SSH or remote ping reported an execution error\n"
    assert "endpoint" not in captured.err
