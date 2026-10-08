from __future__ import annotations

import io
import json
import sys
import time
from types import SimpleNamespace

import pytest
from rich.console import Console

from nebius_vpngw import cli
from nebius_vpngw import packet_health as health

BOOT = "12345678-1234-1234-1234-123456789012"
EXPECTED = {"site-a": 1024}
RAW = (
    "list-sa event {site-a {uniqueid=5 version=2 local-host=192.0.2.1 "
    "remote-host=198.51.100.1 child-sas {site-a-7 {name=site-a uniqueid=7 "
    "reqid=12 state=INSTALLED protocol=ESP spi-in=c0123456 if-id-in=64 "
    "local-ts=[0.0.0.0/0] remote-ts=[0.0.0.0/0] install-time=23}}}}\n"
    "list-sas reply {}\n"
)


def xfrm(window=1024, late=10, duplicate=0, integrity=0, packets=100):
    return f"""src 198.51.100.1 dst 192.0.2.1
    proto esp spi 0xc0123456(3222418518) reqid 12(0x0000000c) mode tunnel
    replay-window {window if window <= 32 else 0} seq 0x00000000 flag af-unspec
    auth-trunc hmac(sha256) 256 128
    enc cbc(aes) 256
    {"anti-replay esn context: replay_window " + str(window) if window > 32 else ""}
    if_id 0x64
    lifetime current:
      9000(bytes), {packets}(packets)
      add 2026-01-01 10:00:00 use 2026-01-01 10:05:00
    stats:
      replay-window {late} replay {duplicate} failed {integrity}
"""


def links(errors=10, dropped=10):
    return json.dumps(
        [
            {
                "ifindex": 5,
                "ifname": "xfrm0",
                "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": 100}},
                "stats64": {"rx": {"errors": errors, "dropped": dropped, "packets": 100}},
            }
        ]
    )


def sample(monkeypatch, *, raw=RAW, state=None, link=None, seq=10):
    monkeypatch.setattr(health, "_boot_id", lambda: BOOT)
    monkeypatch.setattr(health.Path, "read_text", lambda self: f"XfrmInStateSeqError\t{seq}\n")
    outputs = {
        "swanctl": raw,
        "xfrm": state if state is not None else xfrm(),
        "link": link if link is not None else links(),
    }

    def command(args, deadline):
        if args[0] == "swanctl":
            assert args == ["swanctl", "--list-sas", "--raw"]
            value = outputs["swanctl"]
        elif "xfrm" in args:
            assert args == ["ip", "-s", "xfrm", "state", "list", "nokeys"]
            value = outputs["xfrm"]
        else:
            assert args == ["ip", "-j", "-d", "-s", "link", "show"]
            value = outputs["link"]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(health, "_command", command)
    return health.snapshot(EXPECTED, time.monotonic() + 20)


def observation(monkeypatch, **kwargs):
    first = sample(monkeypatch)
    second = sample(monkeypatch, **kwargs)
    first.update(started=10.0, finished=10.1)
    second.update(started=13.1, finished=13.2)
    result = {"schema": health.SCHEMA, "samples": [first, second]}
    return health.validate_observation(result, EXPECTED)


@pytest.mark.parametrize("window", [0, 32, 33, 1000, 1024])
def test_effective_window_is_distinct_from_window_error_counter(window):
    state = health.parse_xfrm(xfrm(window=window, late=987654))[0]
    assert state["window"] == window
    assert state["late"] == 987654
    assert state["packets"] == 100


def test_balanced_vici_uses_hex_spi_interface_and_decimal_reqid():
    result = health.parse_sas(RAW)[0]
    assert result["spi"] == 0xC0123456
    assert result["if_id"] == 100
    assert result["reqid"] == 12
    assert result["unique"] == 7
    assert "install-time" not in result
    assert health.parse_sas("list-sas reply {}") == []


@pytest.mark.parametrize(
    "raw", [RAW[:-2], RAW.replace("child-sas {", "child-sas {{"), "secret diagnostic"]
)
def test_partial_or_malformed_vici_never_becomes_empty_healthy_response(raw):
    with pytest.raises((ValueError, KeyError)):
        health.parse_sas(raw)


def test_key_bearing_xfrm_rejected():
    with pytest.raises(ValueError, match="key-bearing"):
        health.parse_xfrm(xfrm().replace("enc cbc(aes) 256", "enc cbc(aes) 0x01234567"))


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({}, "Idle"),
        ({"state": xfrm(packets=120)}, "No new drops"),
        ({"state": xfrm(late=28)}, "18 late drops"),
        ({"state": xfrm(duplicate=2)}, "2 duplicate drops"),
        ({"state": xfrm(integrity=3)}, "3 integrity failures"),
        (
            {"state": xfrm(late=28, duplicate=2, integrity=3)},
            "18 late drops; 2 duplicate drops; 3 integrity failures",
        ),
    ],
)
def test_health_uses_fresh_deltas_including_zero_accepted_packets(monkeypatch, kwargs, expected):
    result = health.summarize(observation(monkeypatch, **kwargs), "site-a", 1024)
    assert result["window"] == "1024"
    assert result["health"] == expected
    assert "Gateway-wide" in result["details"][-1]


def test_global_counter_does_not_need_to_equal_tunnel_counter(monkeypatch):
    result = health.summarize(observation(monkeypatch, state=xfrm(late=28), seq=99), "site-a", 1024)
    assert result["health"] == "18 late drops"
    assert "(+89)" in result["details"][-1]


@pytest.mark.parametrize(
    "explicit,expected", [(None, "Idle"), (32, "Expected 32; not active; Idle")]
)
def test_inherited_window_is_not_reported_as_mismatch(monkeypatch, explicit, expected):
    assert health.summarize(observation(monkeypatch), "site-a", explicit)["health"] == expected


def test_disabled_window_is_reported(monkeypatch):
    obs = observation(monkeypatch, state=xfrm(window=0))
    assert "Replay protection disabled" in health.summarize(obs, "site-a", None)["health"]


def test_partial_sample_retains_confirmed_drops(monkeypatch):
    obs = observation(monkeypatch, state=xfrm(late=28), link=ValueError("private detail"))
    # Lost interface mapping does not invalidate the stable SA's own counters.
    result = health.summarize(obs, "site-a", 1024)
    assert "18 late drops" in result["health"]
    assert "sample incomplete" in result["health"]
    assert "private detail" not in json.dumps(obs)


def test_counter_reset_and_rekey_do_not_subtract_unrelated_totals(monkeypatch):
    obs = observation(monkeypatch, state=xfrm(late=1, packets=0))
    assert "sample incomplete" in health.summarize(obs, "site-a", 1024)["health"]
    obs = observation(
        monkeypatch, raw=RAW.replace("uniqueid=7", "uniqueid=8"), state=xfrm(late=999)
    )
    result = health.summarize(obs, "site-a", 1024)
    assert result["health"] == "sample incomplete (SAs changed)"
    assert not any("(+989)" in line for line in result["details"])


def test_boot_change_never_looks_healthy(monkeypatch):
    obs = observation(monkeypatch)
    obs["samples"][1]["boot"] = "22345678-1234-1234-1234-123456789012"
    assert health.summarize(obs, "site-a", 1024)["health"] == "Gateway restarted"


def test_same_peer_multiple_marked_states_are_ambiguous(monkeypatch):
    obs = observation(
        monkeypatch, state=xfrm() + xfrm().replace("if_id", "mark 0x1/0xff\n    if_id")
    )
    assert obs["samples"][1]["tunnels"]["site-a"]["reason"] == "ambiguous"
    assert "sample incomplete" in health.summarize(obs, "site-a", 1024)["health"]


def test_overlap_preserves_positive_stable_subset(monkeypatch):
    obs = observation(monkeypatch, state=xfrm(late=28))
    sa = next(iter(obs["samples"][1]["tunnels"]["site-a"]["sas"].values()))
    obs["samples"][1]["tunnels"]["site-a"]["sas"]["b" * 64] = dict(sa, spi=123, late=1000)
    assert (
        health.summarize(obs, "site-a", 1024)["health"]
        == "18 late drops; sample incomplete (SAs changed)"
    )


@pytest.mark.parametrize("cold", [False, True])
def test_no_sa_only_expected_for_authoritative_cold_standby(monkeypatch, cold):
    obs = observation(monkeypatch, raw="list-sas reply {}", state="")
    obs["samples"][0]["tunnels"]["site-a"]["sas"] = {}
    expected = "Cold standby (no inbound SA)" if cold else "No inbound SA"
    assert health.summarize(obs, "site-a", 1024, cold_standby=cold)["health"] == expected


def test_malformed_remote_data_does_not_reach_presentation(monkeypatch):
    obs = observation(monkeypatch)
    obs["samples"][1]["tunnels"]["site-a"]["reason"] = "secret diagnostic"
    with pytest.raises(ValueError):
        health.validate_observation(obs, EXPECTED)


def test_ssh_probe_bounded_and_stdin_only(monkeypatch):
    calls = []

    def run(args, deadline, **kwargs):
        assert 0 < deadline - time.monotonic() <= health.GATEWAY_TIMEOUT
        calls.append((args, kwargs))
        raise ValueError("private remote diagnostic")

    monkeypatch.setattr(health, "_command", run)
    result = health.collect(["ssh", "-o", "StrictHostKeyChecking=yes", "host"], EXPECTED)
    assert result["samples"] == [None, None]
    args, kwargs = calls[0]
    assert args[-1] == "sudo -n /usr/bin/python3 -B -"
    assert kwargs["timeout"] == 30
    assert "probe({'site-a': 1024})" in kwargs["input_text"]
    assert "from nebius_vpngw" not in kwargs["input_text"]
    assert "config-resolved.yaml" not in kwargs["input_text"]


def test_remote_command_has_output_and_time_bounds():
    assert health._command([sys.executable, "-c", "print('ok')"], time.monotonic() + 1) == "ok\n"
    with pytest.raises(TimeoutError):
        health._command(
            [sys.executable, "-c", "import time; time.sleep(5)"], time.monotonic() + 0.05
        )
    with pytest.raises(ValueError, match="too large"):
        health._command([sys.executable, "-c", "print('x' * 5000000)"], time.monotonic() + 3)


def test_ssh_stdout_limit_stops_producer_before_buffering_all_output(monkeypatch, tmp_path):
    marker = tmp_path / "finished-output"
    monkeypatch.setattr(health, "MAX_OUTPUT", 4096)
    script = (
        "import os,sys,pathlib;sys.stdin.buffer.read();"
        "os.write(1,b'x'*5000000);"
        f"pathlib.Path({str(marker)!r}).touch()"
    )
    result = health.collect([sys.executable, "-c", script], EXPECTED)
    assert result["samples"] == [None, None]
    assert not marker.exists(), "collector consumed the entire oversized output"


def test_ssh_stderr_is_discarded_while_valid_stdout_is_collected(monkeypatch):
    original_popen = health.subprocess.Popen
    calls = []

    def popen(*args, **kwargs):
        calls.append(kwargs)
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(health.subprocess, "Popen", popen)
    observation = {
        "schema": health.SCHEMA,
        "samples": [
            None,
            {
                "boot": BOOT,
                "started": 1,
                "finished": 2,
                "tunnels": {},
                "links": {},
                "global_seq": 0,
            },
        ],
    }
    script = (
        "import os,sys;sys.stdin.buffer.read();os.write(2,b'x'*5000000);"
        f"print({json.dumps(observation)!r})"
    )
    assert health.collect([sys.executable, "-c", script], {}) == observation
    assert calls[0]["stderr"] == health.subprocess.DEVNULL


def test_ssh_bidirectional_pipe_pressure_does_not_deadlock(monkeypatch):
    monkeypatch.setattr(health, "remote_script", lambda _: "x" * 1000000)
    observation = {
        "schema": health.SCHEMA,
        "samples": [
            None,
            {
                "boot": BOOT,
                "started": 1,
                "finished": 2,
                "tunnels": {},
                "links": {},
                "global_seq": 0,
            },
        ],
    }
    script = (
        "import os,sys;os.write(1,b' '*1000000);"
        "assert len(sys.stdin.buffer.read())==1000000;"
        f"print({json.dumps(observation)!r})"
    )
    assert health.collect([sys.executable, "-c", script], {}) == observation


def test_ssh_deadline_covers_blocked_stdin_and_reaps_child(monkeypatch):
    monkeypatch.setattr(health, "remote_script", lambda _: "x" * 1000000)
    monkeypatch.setattr(health, "GATEWAY_TIMEOUT", 0.05)
    original_popen = health.subprocess.Popen
    processes = []

    def popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(health.subprocess, "Popen", popen)
    result = health.collect([sys.executable, "-c", "import time;time.sleep(10)"], EXPECTED)
    assert result["samples"] == [None, None]
    assert processes[0].returncode is not None


def test_packet_table_narrow_no_color_and_expected_only_payload(monkeypatch):
    obs = observation(monkeypatch, state=xfrm(late=28))
    monkeypatch.setattr(cli, "_status_ssh_target_command", lambda *a, **k: ["ssh", "host"])
    jobs = []
    monkeypatch.setattr(
        health, "collect_gateways", lambda value: jobs.append(value) or {"gw-0": obs}
    )
    config = {
        "connections": [
            {"tunnels": [{"name": "site-a", "replay_window": 1024, "psk": "do-not-send"}]}
        ]
    }
    plan = SimpleNamespace(
        iter_instance_configs=lambda: [
            SimpleNamespace(hostname="gw-0", config_yaml=json.dumps(config))
        ]
    )
    stream = io.StringIO()
    cli._render_packet_health(
        Console(file=stream, width=60, color_system=None),
        plan=plan,
        vm_ips={"gw-0": "192.0.2.1"},
        ssh_context=None,
        ha_snapshot=None,
        details=True,
    )
    text = stream.getvalue()
    assert "Packet health" in text and "18 late drops" in text
    assert "do-not-send" not in repr(jobs)
    assert "Gateway-wide XfrmInStateSeqError" in text
    assert "not additive" in text


@pytest.mark.parametrize("packets", [100, 110])
def test_interface_drops_never_report_idle_or_no_new_drops(monkeypatch, packets):
    obs = observation(monkeypatch, state=xfrm(packets=packets), link=links(errors=15, dropped=15))
    assert health.summarize(obs, "site-a", 1024)["health"] == "Interface RX errors/drops increased"


def test_unaccounted_retiring_kernel_sa_marks_sample_incomplete(monkeypatch):
    old = xfrm().replace("0xc0123456(3222418518)", "0xc0123455(3222418517)")
    obs = observation(monkeypatch, state=xfrm(late=28) + old)
    assert health.summarize(obs, "site-a", 1024)["health"] == "18 late drops; sample incomplete"


def test_huge_timestamp_is_unavailable_without_changing_status_exit(monkeypatch):
    obs = observation(monkeypatch)
    obs["samples"][1]["started"] = 10**400
    monkeypatch.setattr(health, "remote_script", lambda expected: "probe")
    monkeypatch.setattr(
        health,
        "_command",
        lambda *a, **kw: json.dumps(obs),
    )
    assert health.collect(["ssh", "host"], EXPECTED)["samples"] == [None, None]


@pytest.mark.parametrize("promoted", [False, True])
def test_cold_label_requires_endpoint_role_and_multihost_rows_are_identified(monkeypatch, promoted):
    obs = observation(monkeypatch, raw="list-sas reply {}", state="")
    obs["samples"][0]["tunnels"]["site-a"]["sas"] = {}
    monkeypatch.setattr(cli, "_status_ssh_target_command", lambda *a, **k: ["ssh", "host"])
    monkeypatch.setattr(health, "collect_gateways", lambda value: {host: obs for host in value})
    config = {"connections": [{"tunnels": [{"name": "site-a"}]}]}
    plan = SimpleNamespace(
        iter_instance_configs=lambda: [
            SimpleNamespace(hostname=host, config_yaml=json.dumps(config))
            for host in ("gw-0", "gw-1")
        ]
    )

    def snapshot(owner):
        return SimpleNamespace(
            authority=SimpleNamespace(condition="exact", owner_node_id=owner),
            members=[
                SimpleNamespace(
                    name="gw-0",
                    node_id="node-0",
                    condition="exact",
                    record={
                        "observed_owner_node_id": owner,
                        "data_plane_mode": "passive",
                        "standby_ready": True,
                        "standby_tunnel_state": "cold",
                        "pending_operation_id": None,
                        "apply_locked": False,
                        "guard_boot_id": BOOT,
                    },
                )
            ],
        )

    refreshes = []

    def refresh():
        refreshes.append(True)
        return snapshot("node-0" if promoted else "node-1")

    stream = io.StringIO()
    cli._render_packet_health(
        Console(file=stream, width=100, color_system=None),
        plan=plan,
        vm_ips={"gw-0": "192.0.2.1", "gw-1": "192.0.2.2"},
        ssh_context=None,
        ha_snapshot=snapshot("node-1"),
        details=False,
        refresh_ha=refresh,
    )
    text = stream.getvalue()
    assert refreshes == [True]
    assert ("Cold standby" in text) is not promoted
    assert "gw-0" in text and "gw-1" in text


def test_stable_overlapping_sas_sum_only_their_individual_deltas(monkeypatch):
    obs = observation(monkeypatch, state=xfrm(late=28))
    for index, count in enumerate((1000, 1002)):
        sa = next(iter(obs["samples"][index]["tunnels"]["site-a"]["sas"].values()))
        obs["samples"][index]["tunnels"]["site-a"]["sas"]["b" * 64] = dict(sa, spi=123, late=count)
    assert health.summarize(obs, "site-a", 1024)["health"] == "20 late drops"


def test_one_kernel_sa_cannot_be_attributed_to_two_child_names(monkeypatch):
    extra = RAW.replace("site-a", "site-b").replace("uniqueid=7", "uniqueid=8")
    raw = RAW.removesuffix("list-sas reply {}\n") + extra
    obs = observation(monkeypatch, raw=raw)
    assert "ambiguous mapping" in health.summarize(obs, "site-a", 1024)["health"]


def test_probe_source_executes_without_installed_product_or_third_party_packages():
    import subprocess

    source = health.Path(health.__file__).read_text()
    commands = {
        ("swanctl", "--list-sas", "--raw"): RAW,
        ("ip", "-s", "xfrm", "state", "list", "nokeys"): xfrm(),
        ("ip", "-j", "-d", "-s", "link", "show"): links(),
    }
    source += (
        f"\ncommands = {commands!r}\n"
        "_command = lambda args, deadline: commands[tuple(args)]\n"
        f"_boot_id = lambda: {BOOT!r}\n"
        "Path.read_text = lambda self: 'XfrmInStateSeqError 10\\n'\n"
        "time.sleep = lambda seconds: None\n"
        f"print(json.dumps(probe({EXPECTED!r})))\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-"],
        input=source,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    obs = health.validate_observation(json.loads(result.stdout), EXPECTED)
    sa = next(iter(obs["samples"][1]["tunnels"]["site-a"]["sas"].values()))
    assert sa["window"] == 1024 and sa["late"] == 10
