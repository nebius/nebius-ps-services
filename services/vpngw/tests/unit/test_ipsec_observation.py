import json
import socket
import struct
import tempfile
import threading
import time
from pathlib import Path

import pytest
from vici.protocol import Message, Packet

from nebius_vpngw.ipsec_observation import Inventory, child_ready, loaded_connections, snapshot


@pytest.mark.parametrize("raw", ["", "null", "{}", '["edge", "edge"]', "[3]", '["bad name"]'])
def test_connection_inventory_rejects_incomplete_or_ambiguous_evidence(raw):
    from nebius_vpngw.ipsec_observation import parse_connections

    with pytest.raises(ValueError):
        parse_connections(raw)


def event(name="edge", state="INSTALLED", ifid="00000064", protocol="ESP", uid="1"):
    return {
        name: {
            "uniqueid": uid,
            "state": "ESTABLISHED",
            "child-sas": {
                "child-1": {
                    "name": name,
                    "state": state,
                    "protocol": protocol,
                    "if-id-in": ifid,
                    "if-id-out": ifid,
                }
            },
        }
    }


def sas(**kwargs):
    return Inventory.from_events([event(**kwargs)]).json()


@pytest.mark.parametrize("state", ["INSTALLING", "REKEYING", "DELETING"])
def test_child_must_be_installed(state):
    assert not child_ready(sas(state=state), "edge", 100)


def test_exact_identity_protocol_and_both_hex_ids():
    assert child_ready(sas(), "edge", 100)
    assert not child_ready(sas(name="edge-backup"), "edge", 100)
    assert not child_ready(sas(protocol="AH"), "edge", 100)
    assert not child_ready(
        sas().replace('"if-id-out":"00000064"', '"if-id-out":"00000065"'), "edge", 100
    )
    assert child_ready(sas(ifid="00000100"), "edge", 256)
    assert not child_ready(sas(ifid="00000100"), "edge", 100)


def test_ike_only_and_complete_empty_are_down():
    assert not Inventory.from_events([{"edge": {"uniqueid": "1", "state": "ESTABLISHED"}}]).ready(
        "edge", 100
    )
    assert not Inventory.from_events([]).ready("edge", 100)


@pytest.mark.parametrize("raw", ["", "list-sas reply {}", "{}", Inventory().json()])
def test_incomplete_or_legacy_observation_is_unknown(raw):
    with pytest.raises(ValueError):
        child_ready(raw, "edge", 100)


def test_rekey_overlap_does_not_hide_installed_child():
    assert Inventory.from_events([event(state="DELETING"), event(uid="2")]).ready("edge", 100)
    with pytest.raises(ValueError):
        Inventory.from_events([event(), event()])


@pytest.mark.parametrize("field", ["state", "protocol", "if-id-in", "if-id-out"])
def test_missing_critical_child_field_is_unknown(field):
    data = event()
    del data["edge"]["child-sas"]["child-1"][field]
    with pytest.raises(ValueError):
        Inventory.from_events([data])


def test_reject_unknown_envelope_fields():
    data = json.loads(sas())
    data["extra"] = 1
    with pytest.raises(ValueError):
        Inventory.from_json(json.dumps(data))


def send(sock, data):
    sock.sendall(struct.pack("!I", len(data)) + data)


def receive(sock):
    header = sock.recv(4)
    (size,) = struct.unpack("!I", header)
    result = b""
    while len(result) < size:
        result += sock.recv(size - len(result))
    return result


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory(prefix="vici-", dir="/tmp") as path:
        yield Path(path)


def native_server(tmp_path, behavior):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    path = str(tmp_path / "vici")
    listener.bind(path)
    listener.listen(1)
    errors = []

    def serve():
        try:
            with listener, listener.accept()[0] as sock:
                sock.settimeout(2)
                behavior(sock)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=serve)
    thread.start()
    return path, thread, errors


def test_complete_wire_exchange_requires_terminal_reply_and_unsubscribe(socket_dir):
    def respond(sock):
        assert receive(sock)[0] == Packet.EVENT_REGISTER
        send(sock, bytes([Packet.EVENT_CONFIRM]))
        assert receive(sock)[0] == Packet.CMD_REQUEST
        send(sock, bytes([Packet.EVENT, 7]) + b"list-sa" + Message.serialize(event()))
        send(sock, bytes([Packet.CMD_RESPONSE]) + Message.serialize({}))
        assert receive(sock)[0] == Packet.EVENT_UNREGISTER
        send(sock, bytes([Packet.EVENT_CONFIRM]))

    path, thread, errors = native_server(socket_dir, respond)
    value = snapshot(0.5, socket_path=path)
    thread.join(2)
    assert not errors
    assert value.ready("edge", 100)


@pytest.mark.parametrize("partial", [b"\x00", struct.pack("!I", 100) + b"\x07"])
def test_partial_frame_deadline_survives_upstream_timeout_reset(socket_dir, partial):
    def respond(sock):
        receive(sock)
        sock.sendall(partial)
        time.sleep(0.15)

    path, thread, errors = native_server(socket_dir, respond)
    start = time.monotonic()
    value = snapshot(0.04, socket_path=path)
    elapsed = time.monotonic() - start
    thread.join(2)
    assert not errors
    assert not value.complete
    assert elapsed < 0.13


def test_disconnect_after_event_never_publishes_partial_inventory(socket_dir):
    def respond(sock):
        receive(sock)
        send(sock, bytes([Packet.EVENT_CONFIRM]))
        receive(sock)
        send(sock, bytes([Packet.EVENT, 7]) + b"list-sa" + Message.serialize(event()))

    path, thread, errors = native_server(socket_dir, respond)
    assert not snapshot(0.5, socket_path=path).complete
    thread.join(2)
    assert not errors


@pytest.mark.parametrize("complete", [False, True])
def test_loaded_authority_requires_complete_terminal_exchange(socket_dir, complete):
    def respond(sock):
        receive(sock)
        send(sock, bytes([Packet.EVENT_CONFIRM]))
        receive(sock)
        send(
            sock,
            bytes([Packet.EVENT, 9]) + b"list-conn" + Message.serialize({"edge": {"children": {}}}),
        )
        if complete:
            send(sock, bytes([Packet.CMD_RESPONSE]) + Message.serialize({}))
            receive(sock)
            send(sock, bytes([Packet.EVENT_CONFIRM]))

    path, thread, errors = native_server(socket_dir, respond)
    if complete:
        assert loaded_connections(0.5, socket_path=path) == frozenset({"edge"})
    else:
        with pytest.raises(OSError):
            loaded_connections(0.5, socket_path=path)
    thread.join(2)
    assert not errors


@pytest.mark.parametrize(
    "reply", [{"keys": []}, {"keys": ["ike-psk-1"]}, {}, {"keys": ["x", "x"]}, {"keys": "x"}, None]
)
def test_configuration_inventory_requires_complete_credential_identifiers(socket_dir, reply):
    from nebius_vpngw.ipsec_observation import ConfigurationInventory, loaded_configuration

    def respond(sock):
        receive(sock)
        send(sock, bytes([Packet.EVENT_CONFIRM]))
        receive(sock)
        send(sock, bytes([Packet.EVENT, 9]) + b"list-conn" + Message.serialize({"edge": {}}))
        send(sock, bytes([Packet.CMD_RESPONSE]) + Message.serialize({}))
        receive(sock)
        send(sock, bytes([Packet.EVENT_CONFIRM]))
        request = receive(sock)
        assert b"get-shared" in request
        if reply is not None:
            send(sock, bytes([Packet.CMD_RESPONSE]) + Message.serialize(reply))

    path, thread, errors = native_server(socket_dir, respond)
    expected = ConfigurationInventory(frozenset({"edge"}), frozenset({"ike-psk-1"}))
    if reply in ({"keys": []}, {"keys": ["ike-psk-1"]}):
        actual = loaded_configuration(0.5, socket_path=path)
        assert actual.connections == expected.connections
        assert actual.includes(expected) is bool(reply["keys"])
        assert ConfigurationInventory.from_json(actual.json()) == actual
    else:
        with pytest.raises((ValueError, OSError)):
            loaded_configuration(0.5, socket_path=path)
    thread.join(2)
    assert not errors
