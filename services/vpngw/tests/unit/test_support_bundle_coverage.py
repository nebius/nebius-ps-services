"""Real journal encodings and bounded collection must have accurate coverage."""

import json
import zipfile

import pytest
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.support_bundle import collector, probe, projection, report


def test_byte_array_journal_messages_are_decoded_before_stateful_redaction():
    messages = [
        "ordinary diagnostic\x1b[0m",
        'password="',
        "synthetic-secret",
        '"',
        "resumed normally",
    ]
    raw = "\n".join(
        json.dumps({"MESSAGE": list(message.encode()), "_PID": "12"})
        for message in reversed(messages)
    )
    result = probe.journal_events(raw)
    assert result["coverage"]["invalid"] == 0
    assert result["coverage"]["exported"] == 5
    rendered = json.dumps(projection.project(result))
    assert "ordinary diagnostic" in rendered and "resumed normally" in rendered
    assert "synthetic-secret" not in rendered


@pytest.mark.parametrize("message", [None, [256], [-1], [True], ["invalid"], {}])
def test_malformed_journal_messages_remain_invalid(message):
    result = probe.journal_events(json.dumps({"MESSAGE": message}))
    assert result["coverage"]["invalid"] == 1
    assert result["coverage"]["exported"] == 0


def firewall_rule(index):
    return {
        "rule": {
            "family": "ip",
            "table": "filter",
            "chain": "forward",
            "handle": index,
            "expr": [{"counter": {"packets": index, "bytes": index * 64}} for _ in range(8)],
        }
    }


def test_firewall_rules_do_not_share_one_small_traversal_budget():
    source = {"nftables": [firewall_rule(i) for i in range(300)]}
    limits = [0]
    result = projection.project(source, limited=limits)
    assert limits == [0]
    assert len(result["nftables"]) == 300
    assert result["nftables"][-1]["rule"]["handle"] == 299
    assert result["nftables"][-1]["rule"]["expr"][-1]["counter"]["packets"] == 299
    assert len(json.dumps(result, indent=2).encode()) < projection.MAX_BYTES


def test_firewall_projection_preserves_privacy_and_real_limits():
    source = {"nftables": [firewall_rule(i) for i in range(4097)]}
    source["nftables"][0]["rule"]["password"] = "synthetic-secret"
    limits = [0]
    result = projection.project(source, limited=limits)
    assert limits[0] > 0
    assert 0 < len(result["nftables"]) < 4097
    encoded = json.dumps(result, indent=2).encode()
    assert len(encoded) <= projection.MAX_BYTES
    assert b"synthetic-secret" not in encoded


def test_firewall_projection_rejects_cycles():
    source = {"nftables": []}
    source["nftables"].append({"rule": source})
    limits = [0]
    result = projection.project(source, limited=limits)
    assert limits[0] > 0
    assert len(json.dumps(result)) < 256


@pytest.mark.parametrize(
    "source,outcome,gaps",
    [
        ("nodes/0/logs/kernel.json", "source-limit", 0),
        ("nodes/0/file-logs/cloud-init.json", "source-limit", 0),
        ("nodes/0/logs/cloud-final.json", "partial", 1),
        ("nodes/0/logs/kernel.json", "timeout", 1),
        ("nodes/0/logs/kernel.json", "invalid-data", 1),
        ("nodes/0/logs/kernel.json", "unavailable", 1),
        ("nodes/0/network/firewall.json", "source-limit", 1),
        ("operator/status.json", "source-limit", 1),
    ],
)
def test_only_routine_history_limits_are_silent(source, outcome, gaps):
    assert report.collection_counts([{"file": source, "outcome": outcome}]) == {
        "limited_probes": int(outcome == "source-limit"),
        "collection_gaps": gaps,
    }


def test_collection_issues_group_views_and_keep_text_only_gaps():
    rows = [
        {"file": "nodes/0/logs/kernel.json", "outcome": "source-limit"},
        {
            "file": "nodes/0/logs/kernel.stdout.txt",
            "source_file": "nodes/0/logs/kernel.json",
            "outcome": "source-limit",
        },
        {"file": "nodes/0/network/firewall.json", "outcome": "ok"},
        {
            "file": "nodes/0/network/firewall.stdout.txt",
            "source_file": "nodes/0/network/firewall.json",
            "outcome": "source-limit",
        },
        {"file": "nodes/1/host/uptime.json", "outcome": "timeout"},
        {"file": "nodes/1/ha-0/native.json", "outcome": "not-applicable"},
    ]
    for index, row in enumerate(rows):
        row["record_id"] = str(index)
    assert report.collection_counts(rows) == {"limited_probes": 2, "collection_gaps": 2}
    summary = report.build_reports({}, rows, False)["summary.md"].decode()
    assert "Collection limits and gaps" in summary
    assert "nodes/0/network/firewall.json" in summary
    assert "source-limit" in summary and "timeout" in summary


@pytest.mark.parametrize("gaps", [0, 1])
def test_cli_confirms_archive_and_only_warns_for_nonroutine_gaps(tmp_path, monkeypatch, gaps):
    config = tmp_path / "config.yaml"
    config.write_text("{}")

    def collect(*args, **kwargs):
        kwargs["progress"](
            {
                "node": 0,
                "successful_probes": 104,
                "text_files": 88,
                "incomplete_probes": 4,
                "limited_probes": 4,
                "collection_gaps": gaps,
            }
        )
        kwargs["progress"](
            {
                "stage": "archive",
                "guest_nodes": 1,
                "expected_nodes": 1,
                "collection_gaps": gaps,
            }
        )
        return tmp_path / "bundle.zip", 1

    monkeypatch.setattr(collector, "collect", collect)
    result = CliRunner().invoke(cli.app, ["support-bundle", "-c", str(config)])
    assert result.exit_code == 1
    assert "Support bundle created." in result.output
    assert "limited probes" not in result.output
    assert "incomplete" not in result.output
    assert ("summary.md" in result.output) == bool(gaps)
    assert ("collection gaps" in result.output) == bool(gaps)


def test_collector_counts_text_only_limits_and_retains_partial_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "project_id": "project-example",
                "gateway_group": {
                    "name": "example",
                    "instance_count": 1,
                    "external_ips": [["192.0.2.1"]],
                },
            }
        )
    )

    def run(request, _timeout):
        if request["action"] == "cloud":
            return [], "ok"
        name = {"artifact": "selected-artifact", "status": "status"}.get(
            request["action"], "host/uptime"
        )
        return [
            {
                "probe": name,
                "data": {},
                "outcome": "ok",
                "text": {"stdout": {"content": "retained\n", "truncated": True}},
            }
        ], "ok"

    events = []
    path, code = collector.collect(
        config, output=tmp_path / "bundle.zip", runner=run, progress=events.append
    )
    assert code == 1
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["complete"] is False
    coverage = manifest["node_coverage"][0]
    assert coverage["incomplete_probes"] == 0  # Existing field retains its JSON-only meaning.
    assert coverage["limited_probes"] == 1
    assert coverage["collection_gaps"] == 1
    assert events[-1]["limited_probes"] >= 1


def test_unreachable_member_has_no_successful_guest_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "project_id": "project-example",
        "gateway_group": {"name": "example", "instance_count": 1,
                          "external_ips": [["192.0.2.1"]]},
    }))
    events = []
    path, code = collector.collect(
        config, output=tmp_path / "bundle.zip",
        runner=lambda *_: ([], "unavailable"), progress=events.append,
    )
    assert code == 1
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        peer = json.loads(archive.read("nodes/0/network/peer-channel.json"))
    assert peer["assessment"] == "unknown"
    assert manifest["node_coverage"][0]["successful_probes"] == 0
    assert events[-1]["guest_nodes"] == 0
