"""Live acquisition, readable evidence and credential-redaction contract."""

import hashlib
import json
import sys
import zipfile

import pytest

from nebius_vpngw.deploy import ssh_client_auth as auth
from nebius_vpngw.support_bundle import collector, diagnostics, probe, worker
from nebius_vpngw.support_bundle.redaction import LINE_LIMIT, TextRedactor, redact_text, unsafe_text


@pytest.mark.parametrize("opener", ['password="', "-----BEGIN PRIVATE KEY-----"])
def test_overlong_line_cannot_release_multiline_secret_continuation(opener):
    source = "ordinary before\n" + "x" * LINE_LIMIT + opener + "\nsynthetic-continuation\n"
    for split in (0, LINE_LIMIT // 2, LINE_LIMIT + 16, len(source)):
        redactor = TextRedactor()
        result = redactor.feed(source[:split]) + redactor.feed(source[split:], final=True)
        assert "ordinary before" in result
        assert "synthetic-continuation" not in result
        assert redactor.limited and redactor.discarded


def test_journal_overlong_line_retains_safe_facts_and_marks_source_limit():
    messages = ["ordinary before", "x" * LINE_LIMIT + 'password="', "synthetic-continuation"]
    raw = "\n".join(
        json.dumps({"MESSAGE": message, "__REALTIME_TIMESTAMP": str(index), "_PID": "12"})
        for index, message in reversed(list(enumerate(messages)))
    )
    result = probe.journal_events(raw)
    assert "synthetic-continuation" not in json.dumps(result)
    assert "ordinary before" in json.dumps(result)
    assert result["coverage"]["source_limited"]
    assert result["coverage"]["exported"] == len(messages)


@pytest.mark.parametrize(
    "secret",
    [
        "password=synthetic value with spaces",
        'psk="synthetic quoted value"',
        "authorization: Bearer synthetic-token",
        "-----BEGIN PRIVATE KEY-----\nsynthetic key body\n-----END PRIVATE KEY-----",
        "private_key: |\n  synthetic line one\n  synthetic line two",
        'token="synthetic\nvalue spanning lines"',
        'tool --password "synthetic-first\nsynthetic-continuation"',
        "tool --token 'synthetic-first\nsynthetic-continuation'",
        'tool --password="synthetic-first\nsynthetic-continuation"',
        'password="synthetic-one" --token "synthetic-two\nsynthetic-continuation"',
        "encryption key => 8 bytes\n  01 02 03 04 05 06 07 08 synthetic",
    ],
)
def test_credentials_are_redacted_at_every_chunk_boundary(secret):
    source = "ordinary diagnostic retained\n" + secret + "\n"
    expected = redact_text(source)["content"]
    assert "synthetic" not in expected
    assert "ordinary diagnostic retained" in expected
    assert not unsafe_text(expected)
    for offset in range(len(source)):
        redactor = TextRedactor()
        actual = redactor.feed(source[:offset]) + redactor.feed(source[offset:], final=True)
        assert actual == expected


def test_unknown_logs_retained_and_multiline_journal_secrets_redacted_newest_first():
    raw = "\n".join(
        json.dumps({"MESSAGE": message, "__REALTIME_TIMESTAMP": str(index)})
        for index, message in reversed(
            list(
                enumerate(
                    [
                        "ordinary diagnostic",
                        "-----BEGIN PRIVATE KEY-----",
                        "short synthetic body",
                        "-----END PRIVATE KEY-----",
                        "resumed normally",
                    ]
                )
            )
        )
    )
    result = probe.journal_events(raw)
    exported = json.dumps(result)
    assert "synthetic" not in exported
    assert "ordinary diagnostic" in exported and "resumed normally" in exported
    assert result["coverage"]["unrecognized"] == 5


def test_incomplete_secret_tail_is_never_flushed():
    assert redact_text("ordinary\npassword=partial", complete=False)["content"] == "ordinary\n"
    assert (
        redact_text("unlabelled synthetic literal", literals=("synthetic literal",))["content"]
        == "unlabelled [REDACTED]"
    )


@pytest.mark.parametrize("timestamps", [[1, 1, 1, 1, 1], [5, 4, 3, 2, 1]])
@pytest.mark.parametrize("opener", ['password="', 'tool --password "'])
def test_multiline_journal_redaction_survives_clock_ordering(
    tmp_path, monkeypatch, timestamps, opener
):
    monkeypatch.setenv("HOME", str(tmp_path))
    chronological = [
        "ordinary before",
        opener,
        "synthetic-continuation",
        '"',
        "ordinary after",
    ]
    raw = "\n".join(
        json.dumps(
            {
                "MESSAGE": message,
                "__REALTIME_TIMESTAMP": str(timestamp),
                "_BOOT_ID": "a" * 32,
                "_SYSTEMD_UNIT": "nebius-vpngw-agent.service",
                "_PID": "12",
            }
        )
        for timestamp, message in reversed(list(zip(timestamps, chronological, strict=True)))
    )
    data = probe.journal_events(raw)
    assert "synthetic-continuation" not in json.dumps(data)
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

    def run(request, timeout):
        if request["action"] != "remote":
            return [], "ok"
        return [
            {
                "probe": "logs/nebius-vpngw-agent",
                "data": data,
                "outcome": "ok",
                "text": {"stdout": {"content": collector.journal_text(data)}},
            }
        ], "ok"

    path, _ = collector.collect(config, output=tmp_path / "bundle.zip", runner=run)
    with zipfile.ZipFile(path) as archive:
        assert b"synthetic-continuation" not in b"".join(
            archive.read(name) for name in archive.namelist()
        )
        assert b"ordinary before" in archive.read("nodes/0/logs/nebius-vpngw-agent.stdout.txt")


def test_artifact_lookup_uses_bounded_worker_and_preserves_partial_bundle(tmp_path, monkeypatch):
    from nebius_vpngw.deploy.ssh_push import SSHPush

    monkeypatch.setenv("HOME", str(tmp_path))
    unbounded = []
    monkeypatch.setattr(SSHPush, "select_vm_ha_agent_wheel", lambda self: unbounded.append(True))
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
    calls = []

    def run(request, timeout):
        calls.append((request["action"], timeout))
        return [], "timeout" if request["action"] == "artifact" else "ok"

    path, code = collector.collect(config, output=tmp_path / "bundle.zip", runner=run)
    assert not unbounded
    assert any(action == "artifact" and 0 < budget <= 30 for action, budget in calls)
    assert any(action == "remote" for action, _ in calls)
    assert code == 1
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        row = next(
            row for row in manifest["probes"] if row["file"] == "operator/selected-artifact.json"
        )
        assert row["outcome"] == "timeout"


def test_artifact_worker_retains_exact_digest_without_ssh_configuration(tmp_path, monkeypatch):
    wheel = tmp_path / "nebius_vpngw-0.0.0-py3-none-any.whl"
    wheel.write_bytes(b"synthetic artifact bytes")
    monkeypatch.setenv("VPNGW_AGENT_WHEEL", str(wheel))
    records, outcome = collector.run_worker({"action": "artifact"}, 10)
    assert outcome == "ok"
    record = next(row for row in records if row.get("probe") == "selected-artifact")
    assert record["outcome"] == "ok"
    assert record["data"]["artifact_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert record["data"]["source_kind"] == "VPNGW_AGENT_WHEEL"
    assert str(wheel) not in json.dumps(records)


def test_shared_public_path_and_operator_override(tmp_path, monkeypatch):
    key = tmp_path / "public.pub"
    key.write_text("ssh-ed25519 cHVibGlj comment\n")
    monkeypatch.setenv("BUNDLE_PUBLIC_PATH", str(key))
    values = auth.normalize_ssh_access({"ssh_public_key_path": "${BUNDLE_PUBLIC_PATH}"})
    assert values["ssh_public_key"] == key.read_text().strip()
    selected = object()
    token = auth.OPERATOR_IDENTITY.set(selected)
    try:
        assert (
            auth.normalize_ssh_access({"ssh_public_key_path": "/nonexistent"})[
                "ssh_public_key_path"
            ]
            == "/nonexistent"
        )
        assert auth.configured_ssh_client_auth({}) is selected
    finally:
        auth.OPERATOR_IDENTITY.reset(token)


def test_shared_auth_does_not_fall_back_from_mismatched_configured_key(monkeypatch):
    def mismatch(*args, **kwargs):
        raise ValueError("mismatch")

    monkeypatch.setattr(auth, "resolve_ssh_client_auth", mismatch)
    with pytest.raises(ValueError, match="mismatch"):
        auth.configured_ssh_client_auth(
            {"ssh_public_key": "configured", "ssh_private_key_path": "/selected"}
        )


def test_streamed_worker_progress_and_partial_timeout(monkeypatch):
    seen = []

    def command(argv, **kwargs):
        kwargs["on_stdout"](b'{"kind":"progress","stage":"connect","state":"started"}\n')
        kwargs["on_stdout"](b'{"probe":"host/os","data":{"os_id":"ubuntu"},"outcome":"ok"}\n')
        return b"", "timeout", -9

    monkeypatch.setattr(collector, "bounded_command", command)
    rows, outcome = collector.run_worker({"action": "remote"}, 1, on_progress=seen.append)
    assert outcome == "timeout" and rows[0]["probe"] == "host/os"
    assert rows[-1]["failure"] == {
        "stage": "connect",
        "code": "timeout",
        "connection_attempted": True,
    }
    assert seen[0]["stage"] == "connect" and seen[-1]["state"] == "failed"


def test_failure_does_not_reflect_exception_text():
    result = diagnostics.failure(ValueError("schema synthetic confidential"), "trust", False)
    assert result == {"stage": "trust", "code": "trust-format", "connection_attempted": False}


def test_command_collects_stdout_and_stderr_without_deadlock():
    errors = []
    out, outcome, code = probe.bounded_command(
        [sys.executable, "-c", "import sys;print('stdout');sys.stderr.write('stderr')"],
        stderr_out=errors,
    )
    assert (out, outcome, code) == (b"stdout\n", "ok", 0)
    assert errors == [b"stderr"]


def test_status_invokes_public_cli_with_same_selectors(monkeypatch):
    seen = []

    def command(argv, **kwargs):
        seen.extend(argv)
        kwargs["stderr_out"].append(b"password=synthetic-secret\n")
        return b"actual status output\n", "ok", 0

    monkeypatch.setattr(collector, "bounded_command", command)
    rows, outcome = collector.run_worker(
        {
            "config": "/config",
            "project": "project-example",
            "region": "example-region",
            "identity": "/identity",
            "action": "status",
        },
        30,
    )
    assert outcome == "ok"
    assert seen[seen.index("status") + 1 :] == [
        "--local-config-file",
        "/config",
        "--packet-details",
        "--project-id",
        "project-example",
        "--region",
        "example-region",
    ]
    assert seen.index("--ssh-identity") < seen.index("status")
    assert "synthetic-secret" not in json.dumps(rows)
    assert rows[0]["text"]["stdout"]["content"] == "actual status output\n"


def test_text_archive_and_report_integrity(tmp_path, monkeypatch):
    from nebius_vpngw.deploy.ssh_push import SSHPush

    monkeypatch.setenv("HOME", str(tmp_path))
    wheel = tmp_path / "nebius_vpngw-0.0.0-py3-none-any.whl"
    wheel.write_bytes(b"fixture")
    monkeypatch.setattr(
        SSHPush, "select_vm_ha_agent_wheel", lambda self: (wheel, "local-prebuilt-wheel")
    )
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
                "connections": [{"psk": "unlabelled-canary"}],
            }
        )
    )

    def run(request, timeout):
        if request["action"] == "artifact":
            return [
                {
                    "probe": "selected-artifact",
                    "data": {"artifact_sha256": "a" * 64},
                    "outcome": "ok",
                }
            ], "ok"
        if request["action"] == "cloud":
            return [], "ok"
        name = "status" if request["action"] == "status" else "host/uptime"
        return [
            {
                "probe": name,
                "data": {},
                "outcome": "ok",
                "text": {
                    "stdout": redact_text(
                        'useful output\npsk="synthetic secret"\nunlabelled-canary\n'
                    ),
                    "stderr": redact_text("ordinary diagnostic\n"),
                },
            }
        ], "ok"

    events = []
    path, code = collector.collect(
        config, output=tmp_path / "bundle.zip", runner=run, progress=events.append
    )
    assert code == 0
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
        assert b"synthetic secret" not in b"".join(files.values())
        assert b"unlabelled-canary" not in b"".join(files.values())
        assert b"useful output" in files["operator/status.txt"]
        manifest = json.loads(files["manifest.json"])
        assert manifest["guest_nodes"] == [0]
        for row in manifest["probes"]:
            assert row["sha256"] == hashlib.sha256(files[row["file"]]).hexdigest()
        index = json.loads(files["evidence-index.json"])
        assert "operator/status.txt" in json.dumps(index)
    assert events[-1]["guest_nodes"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "psk:\n  synthetic-secret\nordinary output\n",
        "--password synthetic-secret\n",
        "--token=synthetic-secret\n",
        'password="first\\" escaped\nsynthetic-secret\nlast"\n',
    ],
)
def test_additional_credential_forms(text):
    assert "synthetic-secret" not in redact_text(text)["content"]


def test_public_key_path_is_retained_for_fresh_bootstrap_even_with_override(tmp_path):
    public = tmp_path / "public.pub"
    public.write_text("ssh-ed25519 cHVibGlj")
    token = auth.OPERATOR_IDENTITY.set(object())
    try:
        assert (
            auth.normalize_ssh_access({"ssh_public_key_path": str(public)})["ssh_public_key"]
            == public.read_text()
        )
    finally:
        auth.OPERATOR_IDENTITY.reset(token)


@pytest.mark.parametrize(
    "completion",
    ["ok", "timeout", "invalid-output", "send-sudo", "send-python", "exit-python", "send-timeout"],
)
def test_remote_drains_stdout_after_early_exit_status(monkeypatch, capsys, completion):
    from types import SimpleNamespace

    import paramiko

    from nebius_vpngw.deploy import ssh_policy

    monkeypatch.delenv(ssh_policy.KNOWN_HOSTS_ENV, raising=False)
    monkeypatch.setattr(
        ssh_policy, "configure_paramiko_host_verification", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(auth, "configured_ssh_client_auth", lambda vm: None)

    class Channel:
        chunks = [
            b'{"kind":"progress","stage":"startup","state":"completed"}\n',
            b'{"probe":"host/uptime","data":{},"outcome":"ok"}\n',
            b"",
        ]
        errors = [b'ordinary startup diagnostic\npassword="synthetic-secret']

        def settimeout(self, seconds):
            pass

        def exec_command(self, command):
            pass

        def sendall(self, script):
            if completion == "send-timeout":
                raise TimeoutError("upload timeout")
            if completion.startswith("send-"):
                raise OSError("Socket is closed")

        def shutdown_write(self):
            pass

        def exit_status_ready(self):
            return True

        def recv_ready(self):
            return False

        def recv_stderr_ready(self):
            return bool(self.errors)

        def recv_stderr(self, size):
            return self.errors.pop(0)

        def recv(self, size):
            return self.chunks.pop(0)

        def recv_exit_status(self):
            return 1 if completion in {"send-sudo", "send-python", "exit-python"} else 0

    channel = Channel()
    if completion == "timeout":
        moments = iter([0, 0, 200])
        monkeypatch.setattr(worker.time, "monotonic", lambda: next(moments))
    elif completion == "invalid-output":
        channel.chunks = [b"invalid json\n"]
    elif completion in {"send-sudo", "send-python", "exit-python"}:
        channel.chunks = [b""]
        diagnostic = (
            b"sudo: a password is required\n"
            if completion == "send-sudo"
            else b"sudo: /usr/bin/python3: command not found\n"
        )
        channel.errors = [diagnostic + b'ordinary startup diagnostic\npassword="synthetic-secret']
    client = SimpleNamespace(
        connect=lambda **kwargs: None,
        close=lambda: None,
        get_transport=lambda: SimpleNamespace(open_session=lambda **kwargs: channel),
    )
    monkeypatch.setattr(paramiko, "SSHClient", lambda: client)
    config = {
        "project_id": "project-fixture",
        "gateway_group": {"name": "gateway", "instance_count": 1},
    }
    request = {
        "hostname": "gateway-0",
        "targets": {"gateway-0": "192.0.2.1"},
        "since": "2026-01-01",
        "until": "2026-01-02",
    }
    if completion == "ok":
        worker.remote(config, request)
    elif completion in {"send-sudo", "send-python", "exit-python"}:
        expected = "sudo-denied" if completion == "send-sudo" else "python-unavailable"
        with pytest.raises(diagnostics.CollectionFailure) as caught:
            worker.remote(config, request)
        assert caught.value.code == expected
    else:
        with pytest.raises(
            TimeoutError if completion in {"timeout", "send-timeout"} else ValueError
        ):
            worker.remote(config, request)
    rows = list(map(json.loads, capsys.readouterr().out.splitlines()))
    if completion == "ok":
        assert any(row.get("probe") == "host/uptime" for row in rows)
    startup = next(row for row in rows if row.get("probe") == "startup")
    assert "ordinary startup diagnostic" in startup["text"]["stderr"]["content"]
    assert "synthetic-secret" not in json.dumps(rows)
    if completion in {"timeout", "send-timeout", "invalid-output"}:
        assert startup["outcome"] == "partial"
        assert startup["text"]["stderr"]["truncated"]


def test_status_runs_in_the_direct_bounded_process_group(monkeypatch):
    commands = []

    def command(argv, **kwargs):
        commands.append(argv)
        return b"public status output\n", "ok", 0

    monkeypatch.setattr(collector, "bounded_command", command)
    records, outcome = collector.run_worker({"action": "status", "config": "/config"}, 30)
    assert outcome == "ok" and records[0]["probe"] == "status"
    assert commands[0][-4:] == ["status", "--local-config-file", "/config", "--packet-details"]


@pytest.mark.parametrize("remaining,outcome", [(120, "ok"), (20, "timeout")])
def test_aggregate_status_can_finish_after_thirty_seconds(monkeypatch, remaining, outcome):
    def command(argv, **kwargs):
        assert kwargs["timeout"] <= min(90, remaining)
        completed = kwargs["timeout"] >= 32
        return b"status evidence\n", "ok" if completed else "timeout", 0 if completed else -9

    monkeypatch.setattr(collector, "bounded_command", command)
    records, observed = collector.run_worker({"action": "status", "config": "/config"}, remaining)
    assert observed == outcome
    assert records[0]["outcome"] == outcome


@pytest.mark.parametrize("spent,status_budget", [(100, 90), (250, 30)])
def test_aggregate_status_preserves_total_and_archive_budget(
    tmp_path, monkeypatch, spent, status_budget
):
    monkeypatch.setenv("HOME", str(tmp_path))
    elapsed = [0.0]
    monkeypatch.setattr(collector.time, "monotonic", lambda: elapsed[0])
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
    budgets = {}

    def run(request, timeout):
        action = request["action"]
        budgets[action] = timeout
        if action == "remote":
            elapsed[0] = spent
        return [], "ok"

    collector.collect(config, output=tmp_path / "bundle.zip", runner=run)
    assert budgets["status"] == status_budget
    assert elapsed[0] + budgets["status"] <= 300 - 20
    assert budgets["artifact"] <= 30


def test_ipsec_reader_accepts_child_name_already_present(monkeypatch, capsys):
    import ast
    import inspect
    from types import SimpleNamespace

    tree = ast.parse(inspect.getsource(probe.collect_probe))
    script = next(
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "vici_code" for t in node.targets)
    )
    session = SimpleNamespace(
        list_sas=lambda: iter(
            [
                {
                    b"tunnel-one": {
                        b"state": b"ESTABLISHED",
                        b"child-sas": {
                            b"child-one": {
                                b"name": b"child-one",
                                b"bytes-in": b"1234",
                                b"bytes-out": b"5678",
                            }
                        },
                    }
                }
            ]
        )
    )
    sock = SimpleNamespace(
        settimeout=lambda seconds: None, connect=lambda path: None, close=lambda: None
    )
    monkeypatch.setitem(sys.modules, "vici", SimpleNamespace(Session=lambda sock: session))
    monkeypatch.setitem(
        sys.modules, "socket", SimpleNamespace(AF_UNIX=1, SOCK_STREAM=1, socket=lambda *args: sock)
    )
    exec(script, {})
    result = json.loads(capsys.readouterr().out)
    assert result[0]["name"] == "tunnel-one"
    assert result[0]["child-sas"][0]["name"] == "child-one"
    assert result[0]["child-sas"][0]["bytes-in"] == "1234"


def test_trimmed_journal_text_tracks_retained_events_and_hashes():
    name = "nodes/0/logs/kernel.json"
    events = [
        {"timestamp": str(i), "message": f"ordinary-event-{i}", "event_id": f"{i:064x}"}
        for i in range(12)
    ]
    value = {"events": events, "coverage": {"exported": 12, "discarded": 0, "truncation": "none"}}
    text_name = "nodes/0/logs/kernel.stdout.txt"
    entries = {name: json.dumps(value).encode(), text_name: collector.journal_text(value).encode()}
    manifest = [
        {
            "file": name,
            "record_id": "fixture",
            "outcome": "ok",
            "coverage": value["coverage"].copy(),
        },
        {
            "file": text_name,
            "source_file": name,
            "stream": "stdout",
            "text_cap": 5000,
            "outcome": "ok",
        },
    ]
    assert collector.trim_oldest_logs(entries, manifest)
    collector.synchronize_text(entries, manifest)
    retained = json.loads(entries[name])
    assert entries[text_name].decode() == collector.journal_text(retained)
    assert manifest[1]["coverage"]["exported"] == len(retained["events"])
    assert manifest[1]["sha256"] == hashlib.sha256(entries[text_name]).hexdigest()
    del entries[name]
    collector.synchronize_text(entries, manifest)
    assert text_name not in entries and manifest[1]["sha256"] is None


def test_oversized_text_line_has_explicit_limit():
    result = redact_text("ordinary " * 10000)
    assert result["truncated"] and result["discarded"] > 0 and not result["content"]


def test_access_provenance_is_closed_and_contains_no_key_material():
    from nebius_vpngw.support_bundle.projection import project

    assert project(
        {"ssh_identity_source": "explicit-private-key", "ssh_trust_source": "managed-pins"}
    ) == {"ssh_identity_source": "explicit-private-key", "ssh_trust_source": "managed-pins"}
    assert (
        project({"ssh_identity_source": "/private/key/path", "ssh_trust_source": "arbitrary"}) == {}
    )
