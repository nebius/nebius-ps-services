"""Diagnostic meaning and retained-evidence integrity across bundle revisions."""

import hashlib
import json
import subprocess
import sys
import zipfile

import pytest
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.support_bundle import collector, probe, projection, worker


def journal(message, **fields):
    return json.dumps({"MESSAGE": message, "__REALTIME_TIMESTAMP": "1794866400000000", **fields})


def test_journal_accounting_survives_busy_projection():
    raw = "\n".join(
        [journal("Failed to start example.service")] * 1200
        + [journal("PRIVATE_UNRECOGNIZED")] * 3
        + ["not-json"]
    )
    exported = projection.project(probe.journal_events(raw))
    coverage = exported["coverage"]
    assert coverage["scanned"] == 1204
    assert coverage["recognized"] == 1200
    assert coverage["unrecognized"] == 3
    assert coverage["invalid"] == 1
    assert coverage["exported"] == len(exported["events"])
    assert (
        coverage["recognized"] + coverage["unrecognized"]
        == coverage["exported"] + coverage["discarded"]
    )
    assert coverage["retention"] == "unknown"
    assert "PRIVATE_UNRECOGNIZED" in json.dumps(exported)


def test_malformed_journal_envelope_cannot_recurse_or_break_retention():
    value = {"coverage": {}, "events": [{"reasons": 42}]}
    value["coverage"] = value
    limited = [0]
    result = projection.project(value, limited=limited)
    assert limited[0] > 0
    assert result["coverage"]["counts"] == "lower-bound"
    assert len(json.dumps(result)) < 4096


def test_exception_chronology_is_typed_and_private():
    raw = journal(
        'Traceback (most recent call last):\n  File "/opt/pkg/nebius_vpngw/agent/main.py", line 42, in SECRET_FUNCTION\nPermissionError: [Errno 13] SECRET_ARGUMENT',
        _BOOT_ID="a" * 32,
        _SYSTEMD_INVOCATION_ID="b" * 32,
        __MONOTONIC_TIMESTAMP="123000",
        PRIORITY="3",
        _PID="123",
    )
    data = projection.project(probe.journal_events(raw))
    event = data["events"][0]
    assert event["exception_class"] == "PermissionError"
    assert event["errno"] == 13
    assert event["monotonic_usec"] == 123000
    assert event["invocation_id"] == "b" * 32
    assert event["frames"] == [{"frame": "agent/main.py", "line": 42}]
    assert "SECRET_FUNCTION" in event["message"]
    assert "/opt/" in event["message"]


def test_cloud_init_degraded_status_preserves_only_classified_errors():
    raw = json.dumps(
        {
            "status": "done",
            "extended_status": "degraded done",
            "stage": None,
            "modules-final": {
                "start": 100,
                "finished": 200,
                "errors": ["PermissionError: [Errno 13] SECRET_ERROR"],
                "recoverable_errors": {"WARNING": ["SECRET_WARNING"]},
            },
            "datasource": "SECRET_DATASOURCE",
            "user-data": "SECRET_USERDATA",
        }
    )
    data = projection.project(probe.cloud_init_status(raw))
    assert data["status"] == "done" and data["degraded"] is True
    assert data["stages"][0]["error_count"] == 1
    assert data["stages"][0]["recoverable_error_count"] == 1
    assert data["stages"][0]["errors"][0]["exception_class"] == "PermissionError"
    assert "SECRET" not in json.dumps(data)


@pytest.fixture
def bundle_config(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / "gateway.json"
    path.write_text(
        json.dumps(
            {
                "project_id": "project-example",
                "gateway_group": {
                    "name": "gateway",
                    "instance_count": 2,
                    "external_ips": [["192.0.2.1"], ["192.0.2.2"]],
                },
                "psk": "SYNTHETIC_PRIVATE_MATERIAL",
            }
        )
    )
    from nebius_vpngw.deploy.ssh_push import SSHPush

    wheel = tmp_path / "nebius_vpngw-0.6.5-py3-none-any.whl"
    wheel.write_bytes(b"fixture-artifact")
    monkeypatch.setattr(
        SSHPush, "select_vm_ha_agent_wheel", lambda self: (wheel, "local-prebuilt-wheel")
    )
    return path


@pytest.mark.parametrize(
    "message,field,value",
    [
        ("[Errno 98] PRIVATE_BIND_ADDRESS", "errno", 98),
        (
            '  File "/opt/pkg/nebius_vpngw/agent/main.py", line 3, in PRIVATE_FUNCTION',
            "frames",
            [{"frame": "agent/main.py", "line": 3}],
        ),
        ("ModuleNotFoundError: PRIVATE_MODULE", "exception_class", "ModuleNotFoundError"),
        ("Killed process 123 (PRIVATE_PROCESS) anon-rss:42kB", "victim_pid", 123),
    ],
)
def test_distinct_failure_causes_survive_without_arguments(message, field, value):
    data = projection.project(probe.journal_events(journal(message)))
    assert data["events"][0][field] == value
    assert "PRIVATE" in data["events"][0]["message"]


def test_near_cap_rich_events_keep_coverage_and_fit_serialized_limit():
    frames = "\n".join(
        f'File "/opt/pkg/nebius_vpngw/{name}", line 42, in PRIVATE' for name in projection.FRAMES
    )
    raw = "\n".join([journal("PermissionError: [Errno 13] PRIVATE\n" + frames)] * 5000)
    limited = [0]
    data = projection.project(probe.journal_events(raw), limited=limited)
    assert len(json.dumps(data, indent=2).encode()) <= projection.MAX_BYTES
    assert 0 < len(data["events"]) < 5000
    assert (
        data["coverage"]["recognized"]
        == data["coverage"]["exported"] + data["coverage"]["discarded"]
    )
    assert limited[0] > 0
    assert projection.project(data) == data


def test_projection_retains_old_failure_before_new_routine_events(monkeypatch):
    monkeypatch.setattr(projection, "MAX_BYTES", 24000)
    raw = "\n".join(
        journal("Started service", __REALTIME_TIMESTAMP=str(10000 - index)) for index in range(1000)
    )
    raw += "\n" + journal("PermissionError: PRIVATE", __REALTIME_TIMESTAMP="1")
    data = projection.project(probe.journal_events(raw))
    assert any(event.get("exception_class") == "PermissionError" for event in data["events"])
    assert data["coverage"]["discarded"] > 0


def test_timeline_reserves_both_nodes_and_failure_landmarks():
    from nebius_vpngw.support_bundle.report import build_reports

    entries = {}
    manifest = []
    for node, count in ((0, 1), (1, 1200)):
        name = f"nodes/{node}/logs/nebius-vpngw-agent.json"
        events = [
            {
                "event_id": f"{node * 2000 + index:064x}",
                "timestamp": str(index),
                "boot_id": "a" * 32,
                "reasons": ["python-error"] if node == 0 else ["service-transition"],
            }
            for index in range(count)
        ]
        entries[name] = json.dumps({"coverage": {}, "events": events}).encode()
        manifest.append({"file": name, "record_id": str(node), "outcome": "ok"})
    timeline = json.loads(build_reports(entries, manifest, True)["timeline.json"])
    assert any(item["file"].startswith("nodes/0/") for item in timeline["events"])
    assert timeline["indexed_event_count"] == 1000
    assert timeline["omitted_from_timeline"] == 201


def test_empty_journal_is_unknown_retention_not_invalid(monkeypatch):
    monkeypatch.setattr(probe, "bounded_command", lambda *a, **k: (b"", "ok", 0))
    monkeypatch.setattr(probe, "read_regular", lambda path: b"{}")
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    records = probe.collect_probe({"since": "2026-01-01", "until": "2026-01-02"})
    logs = [row for row in records if row["probe"].startswith("logs/")]
    assert logs and all(row["outcome"] == "ok" for row in logs)
    assert all(row["data"]["coverage"]["scanned"] == 0 for row in logs)
    assert all(row["data"]["coverage"]["retention"] == "unknown" for row in logs)


@pytest.mark.parametrize("status,code", [("done", 0), ("error", 1), ("done", 2), ("running", 0)])
def test_cloud_init_observation_retains_failure_exit_codes(monkeypatch, status, code):
    def command(argv, **kwargs):
        if argv[0] == "cloud-init":
            assert argv in (
                ["cloud-init", "status", "--format", "json"],
                ["cloud-init", "status", "--long"],
            )
            return (
                json.dumps(
                    {"status": status, "extended_status": "degraded done" if code == 2 else status}
                ).encode(),
                "ok" if code == 0 else "unavailable",
                code,
            )
        return b"", "ok", 0

    monkeypatch.setattr(probe, "bounded_command", command)
    monkeypatch.setattr(probe, "read_regular", lambda path: b"{}")
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    records = probe.collect_probe({"since": "2026-01-01", "until": "2026-01-02"})
    row = next(row for row in records if row["probe"] == "host/cloud-init")
    assert row["outcome"] == "ok" and row["exit_code"] == code
    assert row["data"]["status"] == status
    assert {"cloud-init-local", "cloud-init", "cloud-config", "cloud-final"} <= set(probe.UNITS)


def sample(index, *, boot="a" * 32):
    return {
        "boot_id": boot,
        "clock_realtime_usec": 10000000 + index * 1000000,
        "clock_monotonic_usec": index * 1000000,
        "_cpu": [index * n for n in (10, 0, 5, 60, 10, 0, 0, 15)],
        "memory_available": 1024,
        "pressure": [{"resource": "memory", "available": True, "some": {"avg10": 5}}],
        "swap_in": index * 2,
        "swap_out": index * 3,
        "page_in": index * 4,
        "page_out": index * 5,
    }


def test_resource_samples_are_interval_deltas_not_since_boot(monkeypatch):
    observations = iter(sample(index) for index in range(1, 7))
    monkeypatch.setattr(probe, "resource_snapshot", lambda: next(observations))
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    data, outcome = probe.resource_samples(probe.time.monotonic() + 10)
    data = projection.project(data)
    assert outcome == "ok" and data["sample_count"] == 5
    assert all(item["interval_seconds"] == 1 for item in data["samples"])
    assert all(item["cpu_steal"] == 15 and item["cpu_iowait"] == 10 for item in data["samples"])
    assert all(item["swap_in"] == 2 and item["page_out"] == 5 for item in data["samples"])
    assert "_cpu" not in json.dumps(data)


def test_resource_reboot_and_deadline_stay_partial(monkeypatch):
    observations = iter([sample(10)] + [sample(index, boot="b" * 32) for index in range(1, 6)])
    monkeypatch.setattr(probe, "resource_snapshot", lambda: next(observations))
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    data, outcome = probe.resource_samples(probe.time.monotonic() + 10)
    assert outcome == "partial"
    assert data["samples"][0]["counter_reset"] is True
    assert "cpu_steal" not in data["samples"][0]
    monkeypatch.setattr(probe, "resource_snapshot", lambda: sample(1))
    data, outcome = probe.resource_samples(probe.time.monotonic())
    assert outcome == "timeout" and data["sample_count"] == 0


def test_managed_listeners_do_not_export_other_processes_or_arguments():
    raw = 'tcp LISTEN 0 100 0.0.0.0:443 0.0.0.0:* users:(("PRIVATE_PROCESS",pid=123,fd=3))\n'
    raw += 'tcp LISTEN 0 100 *:444 *:* users:(("PRIVATE_PROCESS",pid=124,fd=3))\n'
    raw += 'tcp LISTEN 0 100 127.0.0.1:22 *:* users:(("PRIVATE_UNRELATED",pid=999,fd=3))'
    data = projection.project(probe.managed_listeners(raw, {123: "nebius-vpngw-agent", 124: "frr"}))
    assert len(data) == 2 and data[1]["address"] == "wildcard"
    assert data[0]["local_port"] == 443
    assert "PRIVATE" not in json.dumps(data) and "999" not in json.dumps(data)


def test_final_reports_only_cite_retained_evidence(bundle_config, tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "ZIP_LIMIT", 12000)

    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [], "ok"
        raw = "\n".join(
            journal("PermissionError: PRIVATE", __MONOTONIC_TIMESTAMP=str(index), _PID=str(index))
            for index in range(200)
        )
        return [{"probe": "logs/kernel", "outcome": "ok", "data": probe.journal_events(raw)}], "ok"

    path, code = collector.collect(bundle_config, output=tmp_path / "trimmed.zip", runner=run)
    assert code == 1 and path.stat().st_size <= 12000
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        timeline = json.loads(archive.read("timeline.json"))
        index = json.loads(archive.read("evidence-index.json"))
        assert (
            manifest["schema"].endswith("v3") and manifest["diagnostic_sufficiency"] == "unassessed"
        )
        for row in manifest["probes"]:
            if row["sha256"]:
                assert hashlib.sha256(archive.read(row["file"])).hexdigest() == row["sha256"]
        for event in timeline["events"]:
            source = json.loads(archive.read(event["file"]))
            assert any(item["event_id"] == event["event_id"] for item in source["events"])
        for source in index["sources"]:
            assert source["retained"] == (source["file"] in archive.namelist())
        assert b"PRIVATE" in b"".join(archive.read(name) for name in archive.namelist())
        assert any(row.get("coverage", {}).get("discarded", 0) > 0 for row in manifest["probes"])


def test_public_cli_creates_bundle_and_old_name_is_rejected(bundle_config, tmp_path, monkeypatch):
    real_collect = collector.collect
    monkeypatch.setattr(
        collector,
        "collect",
        lambda *args, **kwargs: real_collect(
            *args, **kwargs, runner=lambda request, timeout: ([], "unavailable")
        ),
    )
    destination = tmp_path / "cli.zip"
    result = CliRunner().invoke(
        cli.app, ["support-bundle", "-c", str(bundle_config), "--output", str(destination)]
    )
    assert result.exit_code == 1, result.output
    with zipfile.ZipFile(destination) as archive:
        assert {"summary.md", "analysis-guide.md", "timeline.json", "evidence-index.json"} <= set(
            archive.namelist()
        )
    assert CliRunner().invoke(cli.app, ["collect-logs", "--help"]).exit_code == 2


def test_assembled_source_runs_diagnostics_without_package_imports():
    script = (
        worker.standalone_source()
        + """
print(json.dumps(project(cloud_init_status('{"status":"done","extended_status":"degraded done"}'))))
print(json.dumps(project(journal_events('{"MESSAGE":"[Errno 98] PRIVATE"}'))))
"""
    )
    result = subprocess.run(
        [sys.executable, "-I", "-"],
        input=script,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert json.loads(lines[0])["degraded"] is True
    assert json.loads(lines[1])["events"][0]["errno"] == 98
    assert "PRIVATE" in result.stdout


def test_linux_proc_projection_reads_realistic_fields_and_pressure(monkeypatch):
    files = {
        "/proc/stat": "cpu 100 20 30 400 10 2 3 40 7 2\nprocs_running 2\nprocs_blocked 1\n",
        "/proc/meminfo": "MemTotal: 2048 kB\nMemAvailable: 512 kB\nSwapTotal: 256 kB\nSwapFree: 128 kB\n",
        "/proc/vmstat": "pswpin 5\npswpout 6\npgpgin 7\npgpgout 8\n",
        "/proc/loadavg": "1.0 2.0 3.0 2/200 1234\n",
        "/proc/sys/kernel/random/boot_id": "a" * 32,
    }

    def read(path):
        if str(path).startswith("/proc/pressure/"):
            return b"some avg10=1.00 avg60=2.00 avg300=3.00 total=1234\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=5\n"
        return files[str(path)].encode()

    monkeypatch.setattr(probe, "read_regular", read)
    raw = probe.resource_snapshot()
    assert raw["_cpu"] == [100, 20, 30, 400, 10, 2, 3, 40]
    data = projection.project(raw)
    assert data["memory_available"] == 512 * 1024
    assert data["swap_free"] == 128 * 1024
    assert data["pressure"][0]["some"]["total_usec"] == 1234
    assert data["blocked"] == 1


def test_cgroup_oom_is_scoped_to_fixed_service_identity(monkeypatch):
    def read(path):
        assert str(path).startswith("/sys/fs/cgroup/system.slice/nebius-vpngw-agent.service/")
        return (
            b"low 0\nhigh 2\nmax 3\noom 4\noom_kill 1\nPRIVATE 99\n"
            if path.name == "memory.events"
            else b"4096"
        )

    monkeypatch.setattr(probe, "read_regular", read)
    data = projection.project(probe.service_cgroups(["nebius-vpngw-agent"]))
    assert data[0]["memory_events"]["oom_kill"] == 1
    assert data[0]["memory_current"] == 4096
    assert "PRIVATE" not in json.dumps(data)


def test_managed_child_listener_and_failed_service_oom_survive(monkeypatch):
    def read(path):
        if "/sys/fs/cgroup/system.slice/" in str(path):
            if path.name == "memory.events":
                return b"oom 1\noom_kill 1\n"
            if path.name == "memory.current":
                return b"4096"
            if path.name == "cgroup.procs":
                return b"100\n101\n" if path.parent.name == "nebius-vpngw-agent.service" else b""
        return b"{}"

    def command(argv, **kwargs):
        if argv[0] == "systemctl":
            return b"LoadState=loaded\nMainPID=0\nActiveState=failed\n", "ok", 0
        if argv[0] == "ss":
            return b'tcp LISTEN 0 100 0.0.0.0:443 *:* users:(("PRIVATE",pid=101,fd=3))\n', "ok", 0
        return b"", "ok", 0

    monkeypatch.setattr(probe, "read_regular", read)
    monkeypatch.setattr(probe, "bounded_command", command)
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    rows = probe.collect_probe({"since": "2026-01-01", "until": "2026-01-02"})
    listeners = next(row for row in rows if row["probe"] == "network/listeners")
    assert listeners["data"]["listeners"][0]["pid"] == 101
    assert listeners["data"]["listeners"][0]["unit"] == "nebius-vpngw-agent"
    cgroups = next(row for row in rows if row["probe"] == "host/service-cgroups")
    assert cgroups["data"][0]["memory_events"]["oom_kill"] == 1
    assert "PRIVATE" not in json.dumps([row["data"] for row in rows])


def test_formatted_systemd_time_does_not_erase_clock_observation(monkeypatch):
    def command(argv, **kwargs):
        if argv[0] == "timedatectl":
            return (
                b"NTPSynchronized=yes\nTimeUSec=Mon 2026-11-16 16:00:00 UTC\nRTCTimeUSec=Mon 2026-11-16 16:00:00 UTC\n",
                "ok",
                0,
            )
        return b"", "ok", 0

    monkeypatch.setattr(probe, "bounded_command", command)
    monkeypatch.setattr(probe, "read_regular", lambda path: b"{}")
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    rows = probe.collect_probe({"since": "2026-01-01", "until": "2026-01-02"})
    row = next(row for row in rows if row["probe"] == "host/time")
    assert row["data"]["NTPSynchronized"] == "yes"
    assert row["data"]["clock_realtime_usec"] > 0 and row["data"]["clock_monotonic_usec"] > 0
    assert row["collection_started_at"] <= row["collection_finished_at"]


def test_cloud_init_top_level_errors_and_limits_are_visible():
    data = projection.project(
        probe.cloud_init_status(
            json.dumps({"status": "error", "errors": ["PermissionError: PRIVATE"] * 70})
        )
    )
    assert data["error_count"] == 70 and data["discarded"] == 6
    assert data["source_limited"] is True and len(data["errors"]) == 64
    assert "PRIVATE" not in json.dumps(data)


def test_bundle_does_not_turn_ambiguous_health_into_root_cause(bundle_config, tmp_path):
    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [], "ok"
        return [
            {
                "probe": "ha-0/native",
                "data": {"apply_locked": True, "reasons": ["apply-lock-held"]},
                "outcome": "ok",
            },
            {
                "probe": "logs/nebius-vpngw-agent",
                "data": probe.journal_events(journal("PRIVATE_UNKNOWN_FAILURE")),
                "outcome": "ok",
            },
        ], "ok"

    path, code = collector.collect(bundle_config, output=tmp_path / "ambiguous.zip", runner=run)
    assert code == 0
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["complete"] is True and manifest["diagnostic_sufficiency"] == "unassessed"
        assert any(row.get("coverage", {}).get("unrecognized") == 1 for row in manifest["probes"])
        assert b"PRIVATE_UNKNOWN_FAILURE" in b"".join(
            archive.read(name) for name in archive.namelist()
        )


def test_field_and_event_caps_never_remove_coverage_from_manifest(
    bundle_config, tmp_path, monkeypatch
):
    monkeypatch.setattr(collector, "TOTAL_LIMIT", 18000)

    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "status":
            return [{"probe": "status", "data": {}, "outcome": "ok"}], "ok"
        if request["action"] == "cloud":
            return [], "ok"
        return [
            {
                "probe": "logs/kernel",
                "data": probe.journal_events("\n".join([journal("[Errno 98] PRIVATE")] * 2000)),
                "outcome": "ok",
            }
        ], "ok"

    path, code = collector.collect(bundle_config, output=tmp_path / "capped.zip", runner=run)
    assert code == 1
    with zipfile.ZipFile(path) as archive:
        assert sum(item.file_size for item in archive.infolist()) <= 18000
        manifest = json.loads(archive.read("manifest.json"))
        rows = [row for row in manifest["probes"] if "/logs/" in row["file"]]
        assert len(rows) == 2
        for row in rows:
            assert row["coverage"]["recognized"] == 2000
            assert row["coverage"]["discarded"] + row["coverage"]["exported"] == 2000
