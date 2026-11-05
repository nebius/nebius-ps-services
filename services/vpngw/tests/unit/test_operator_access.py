import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy import operator_guest
from nebius_vpngw.deploy.operator_access import OperatorEnrollment, OperatorTarget
from nebius_vpngw.deploy.operator_keys import OperatorKey


class Harness(OperatorEnrollment):
    def __init__(self):
        super().__init__(
            tuple(
                OperatorTarget(name, name, name, "ubuntu", lambda: None) for name in ("one", "two")
            ),
            None,
            None,
            paramiko_module=object(),
        )
        self.projections = {
            name: {"compute_id": name, "operators": {}, "files": {}} for name in ("one", "two")
        }
        self.events = []
        self.failed_preflight = None
        self.failed_install = None
        self.failed_login = None
        self.inspect_counts = {}

    def inspect(self, target):
        self.events.append(("inspect", target.hostname))
        if self.failed_preflight == target.hostname:
            raise RuntimeError("unreachable")
        projection = copy.deepcopy(self.projections[target.hostname])
        return {
            "projection": projection,
            "digest": operator_guest.digest(projection),
            "context": {"boot": "boot-" + target.hostname, "connection": "source port target 22"},
        }

    def connect(self, target, *, new_key=None):
        self.events.append(("new-login" if new_key else "actor-login", target.hostname))
        if new_key and self.failed_login == target.hostname:
            raise RuntimeError("new login denied")
        return SimpleNamespace(close=lambda: None, name=target.hostname)

    def request(self, client, target, action, connection, **values):
        self.events.append((action, target.hostname))
        if self.failed_install == target.hostname:
            raise RuntimeError("installation failed")
        projection = self.projections[target.hostname]
        projection["operators"][values["name"]] = {
            "public_key": "ssh-ed25519 example",
            "fingerprint": "SHA256:example",
        }
        return {
            "projection": copy.deepcopy(projection),
            "digest": operator_guest.digest(projection),
        }

    @staticmethod
    def _read_json(client, command, request=None):
        return {"boot": "boot-" + client.name}


def key(*, generated=True):
    return OperatorKey(
        "ssh-ed25519 example",
        "SHA256:example",
        Path("/keys/private") if generated else None,
        Path("/keys/private.pub"),
        object() if generated else None,
    )


def test_every_member_preflight_precedes_credential_creation_or_grant():
    harness = Harness()
    harness.failed_preflight = "two"
    generated = []
    with pytest.raises(RuntimeError, match="unreachable"):
        harness.run("alice", lambda: generated.append(True))
    assert not generated
    assert harness.events == [("inspect", "one"), ("inspect", "two")]


def test_generated_key_is_reported_before_grants_and_fresh_logins_verify_each_member():
    harness = Harness()
    _, results = harness.run(
        "alice",
        key,
        on_key=lambda item: harness.events.append(("key-path", str(item.private_path))),
    )
    assert harness.events[:3] == [
        ("inspect", "one"),
        ("inspect", "two"),
        ("key-path", "/keys/private"),
    ]
    assert all(item.complete(generated=True) for item in results)
    for name in ("one", "two"):
        assert ("new-login", name) in harness.events
        assert harness.events.count(("inspect", name)) == 2


def test_partial_grant_is_retained_and_retry_reuses_public_identity():
    harness = Harness()
    harness.failed_install = "two"
    original = key()
    _, results = harness.run("alice", lambda: original)
    assert results[0].complete(generated=True)
    assert not results[1].complete(generated=True)
    assert "alice" in harness.projections["one"]["operators"]
    harness.failed_install = None
    same, results = harness.run("alice", lambda: original)
    assert same is original and all(item.complete(generated=True) for item in results)
    assert harness.projections["one"]["operators"] == harness.projections["two"]["operators"]


def test_public_only_does_not_claim_new_private_key_login():
    harness = Harness()
    _, results = harness.run("alice", lambda: key(generated=False))
    assert all(item.complete(generated=False) and not item.new_login_verified for item in results)
    assert not any(event[0] == "new-login" for event in harness.events)


def test_new_key_login_failure_does_not_undo_successful_grant():
    harness = Harness()
    harness.failed_login = "one"
    _, results = harness.run("alice", key)
    assert results[0].installed and not results[0].complete(generated=True)
    assert results[1].complete(generated=True)
    assert "alice" in harness.projections["one"]["operators"]


def test_unrelated_roster_drift_blocks_before_key_creation():
    harness = Harness()
    harness.projections["one"]["operators"]["bob"] = {"public_key": "other", "fingerprint": "other"}
    generated = []
    with pytest.raises(RuntimeError, match="rosters differ"):
        harness.run("alice", lambda: generated.append(True))
    assert not generated
