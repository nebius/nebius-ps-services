"""Evidence regressions: a running packet source must not hide the outage tail."""

from types import SimpleNamespace

import pytest
from tests.systemd import static_recovery_driver
from tests.systemd.static_recovery_evidence import child_signatures, packet_report
from tests.systemd.static_recovery_peer import peer_config, peer_interfaces


def test_final_packet_counters_include_leading_internal_and_trailing_loss():
    report = packet_report(
        "\n".join(
            [
                "[100.200000] 64 bytes from 10.0.0.2: icmp_seq=2 ttl=64 time=0.1 ms",
                "[100.600000] 64 bytes from 10.0.0.2: icmp_seq=4 ttl=64 time=0.1 ms",
                "[100.700000] 64 bytes from 10.0.0.2: icmp_seq=4 ttl=64 time=0.1 ms (DUP!)",
                "6 packets transmitted, 2 received, +1 duplicates, 66% packet loss",
            ]
        )
    )
    assert report["transmitted"] == 6
    assert report["unanswered"] == 4
    assert report["unique_replies"] == 2
    assert report["duplicates"] == 1
    assert report["maximum_reply_gap_seconds"] == pytest.approx(0.4)
    assert report["reply_gaps"][0]["unanswered_between_replies"] == 1


def test_partial_packet_log_does_not_claim_zero_trailing_loss():
    report = packet_report("[100.200000] 64 bytes from 10.0.0.2: icmp_seq=1 ttl=64 time=0.1 ms\n")
    assert report["transmitted"] is None
    assert report["unanswered"] is None
    assert report["final_counters_available"] is False


def test_all_lost_packets_still_have_reportable_final_counters():
    report = packet_report("5 packets transmitted, 0 received, 100% packet loss")
    assert report["unanswered"] == 5
    assert report["last_reply_at"] is None


def test_icmp_error_is_not_mistaken_for_success():
    report = packet_report(
        "[100.2] From 10.0.0.1 icmp_seq=1 Destination Host Unreachable\n"
        "1 packets transmitted, 0 received, 100% packet loss"
    )
    assert report["unique_replies"] == 0
    assert report["unanswered"] == 1


def test_extra_child_changes_signature_even_when_ike_and_reqid_match():
    child = {
        "uniqueid": "1",
        "state": "INSTALLED",
        "reqid": "1",
        "spi-in": "aa",
        "spi-out": "bb",
        "if-id-in": "64",
        "if-id-out": "64",
    }
    snapshot = {"sas": {"backup": [{"id": "1", "children": [child]}]}}
    original = child_signatures(snapshot, "backup")
    snapshot["sas"]["backup"][0]["children"].append(
        {**child, "uniqueid": "2", "spi-in": "cc", "spi-out": "dd"}
    )
    assert len(original) == 1
    assert len(child_signatures(snapshot, "backup")) == 2
    assert child_signatures(snapshot, "backup") != original


def test_plain_peer_uses_standard_responder_config_for_both_paths():
    tunnels = []
    for index, name in enumerate(("preferred", "backup"), 1):
        tunnels.append(
            {
                "name": name,
                "psk": "ephemeral-test-only",
                "local_public_ip": f"192.0.2.{index + 1}",
                "remote_public_ip": "192.0.2.1",
                "inner_cidr": f"169.254.{index}.0/30",
                "inner_local_ip": f"169.254.{index}.2",
            }
        )
    config = {
        "gateway": {"local_prefixes": ["10.200.2.1/32"]},
        "connections": [{"tunnels": tunnels}],
        "_fixture_remote_ts": ["10.200.1.1/32", "169.254.1.0/30", "169.254.2.0/30"],
    }
    rendered = peer_config(config)
    assert rendered.count("start_action = none") == 2
    assert "trap" not in rendered
    assert "keyingtries = 0" not in rendered
    assert rendered.count("remote_ts = 10.200.1.1/32, 169.254.1.0/30, 169.254.2.0/30") == 2
    interfaces = peer_interfaces(config)
    assert [interface["if_id"] for interface in interfaces] == [100, 101]
    assert [interface["name"] for interface in interfaces] == ["xfrm0", "xfrm1"]


def test_snapshot_accepts_text_xfrm_policy_from_json_flag_and_redacts_settings(
    tmp_path, monkeypatch
):
    policy = (
        "src 10.0.0.0/24 dst 10.1.0.0/24\n"
        "\tdir out priority 1\n"
        "\ttmpl src 192.0.2.1 dst 192.0.2.2 proto esp reqid 1 mode tunnel\n"
        "\tif_id 0x64\n"
    )
    config = tmp_path / "swanctl.conf"
    config.write_text(
        "connections {\n  preferred {\n    keyingtries = 0\n  }\n}\n"
        "secrets {\n  test {\n    secret = must-not-be-exported\n  }\n}\n"
    )
    monkeypatch.setattr(static_recovery_driver, "SWANCTL_CONF", config)
    monkeypatch.setattr(static_recovery_driver, "parse_sas", lambda _raw: [])

    def run(argv):
        if argv == ("ip", "-j", "xfrm", "policy"):
            return policy
        return "[]"

    selector = SimpleNamespace(
        state={"operation": None, "selected": {}, "health": {"site": "healthy"}},
        preferred_observations={},
    )
    evidence = static_recovery_driver.snapshot(selector, SimpleNamespace(run=run), 0)
    assert int(evidence["policies"][0]["reqid"]) == 1
    assert int(evidence["policies"][0]["if_id"], 0) == 100
    assert evidence["settings"] == {"preferred": {"keyingtries": "0"}}
