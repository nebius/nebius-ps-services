from __future__ import annotations

import base64
import copy
import json
import sys
from pathlib import Path

import pytest

from nebius_vpngw import runtime_migration as migration


def test_package_inventory_uses_native_identity_without_adopting_foreign_arch(monkeypatch):
    def run(args, *, timeout):
        assert 0 < timeout <= 60
        if args == ["dpkg", "--print-architecture"]:
            return "arm64\n"
        return "libstrongswan:arm64\t5.9\tinstalled\nlibstrongswan:amd64\t5.8\tinstalled\n"

    monkeypatch.setattr(migration, "run", run)
    assert migration.packages() == {
        "libstrongswan": ["5.9", "installed"],
        "libstrongswan:amd64": ["5.8", "installed"],
    }


def test_unowned_read_command_is_output_bounded(monkeypatch):
    monkeypatch.setattr(migration, "COMMAND", None)
    with pytest.raises(RuntimeError, match="exceeds limit"):
        migration.run(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 5000000)"], timeout=5
        )


def test_operator_boot_policy_is_not_overwritten_by_activation(monkeypatch):
    approved = {
        "before": {"strongswan-starter": ["5.9.13", "installed"]},
        "unit_policy": {
            "strongswan-starter.service": {"UnitFileState": "disabled"},
            "strongswan.service": {"UnitFileState": "not-found"},
            "nebius-vpngw-health-monitor.service": {"UnitFileState": "disabled"},
            "nebius-vpngw-fix-routes.timer": {"UnitFileState": "enabled-runtime"},
        },
    }
    monkeypatch.setattr(migration, "state", lambda: {"plan": approved})
    assert migration.startup_states(approved)["strongswan.service"] == "disabled"
    assert migration.units_to_enable(
        [
            "strongswan.service",
            "nebius-vpngw-health-monitor.service",
            "nebius-vpngw-fix-routes.timer",
            "nebius-vpngw-agent.service",
        ]
    ) == ["nebius-vpngw-agent.service"]


def test_effective_policy_ignores_invocation_statistics_but_preserves_commands():
    before = "ExecStart={ path=/usr/sbin/charon-systemd ; argv[]=/usr/sbin/charon-systemd ; ignore_errors=no ; start_time=[Mon] ; pid=1 ; code=exited ; status=0 }\nExecReload=\n"
    after = before.replace("[Mon]", "[Tue]").replace("pid=1", "pid=2")
    assert migration.command_policy(before) == migration.command_policy(after)
    assert migration.command_policy(before) != migration.command_policy(
        after.replace("argv[]=/usr/sbin/charon-systemd", "argv[]=/usr/sbin/charon-systemd --extra")
    )


def test_completed_runtime_resumes_exact_parent_without_rechecking_old_packages(monkeypatch):
    approved, manifest = {"before": {"strongswan": ["old", "installed"]}}, {"artifact": "fixed"}
    value = {"phase": "complete", "operation": "a" * 32, "plan": approved, "manifest": manifest}
    monkeypatch.setattr(migration, "state", lambda: copy.deepcopy(value))
    monkeypatch.setattr(
        migration,
        "packages",
        lambda: pytest.fail("must not compare installed runtime to its old predecessor"),
    )
    migration.begin(approved, "a" * 32, manifest)
    with pytest.raises(RuntimeError, match="differs"):
        migration.begin(approved, "a" * 32, {"artifact": "other"})


def test_pending_parent_preserves_original_package_plan(monkeypatch):
    approved = {"approved": "exact"}
    monkeypatch.setattr(migration, "state", lambda: {"phase": "complete", "plan": approved})
    monkeypatch.setattr(migration, "pending", lambda parent=None: {"operation": "a" * 32})
    assert migration.plan() == approved


def test_effective_policy_keeps_indirect_loader_as_extra_command():
    canonical = "{ path=/usr/bin/chmod ; argv[]=/usr/bin/chmod 0600 /run/charon.vici ; ignore_errors=no ; pid=1 }"
    loader = "{ path=/usr/local/bin/load-vpn ; argv[]=/usr/local/bin/load-vpn ; ignore_errors=no ; pid=2 }"
    policy = migration.command_policy("ExecStartPost=" + canonical + " " + loader)
    assert len(policy["ExecStartPost"]) == 2


@pytest.mark.parametrize("property_name", ["ExecCondition", "ExecStartPost", "ExecReload"])
def test_effective_policy_preserves_repeated_command_properties(property_name):
    runtime = "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -B /var/lib/nebius-vpngw/runtime-migration/startup.py ; ignore_errors=no ; pid=0 }"
    ordinary = "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -B /var/lib/nebius-vpngw/ordinary-startup.py ; ignore_errors=no ; pid=0 }"
    repeated = f"{property_name}={runtime}\n{property_name}={ordinary}\nLoadState=loaded\n"
    joined = f"{property_name}={runtime} {ordinary}\nLoadState=loaded\n"
    assert migration.command_policy(repeated) == migration.command_policy(joined)
    assert len(migration.command_policy(repeated)[property_name]) == 2


def test_fresh_apt_workspace_is_bounded_and_removed_on_error(monkeypatch):
    from pathlib import Path

    calls = []

    def run(args, *, timeout, cwd):
        calls.append((args, timeout, cwd))
        config = Path(args[2]).read_text()
        assert args == ["apt-get", "-c", str(cwd / "apt.conf"), "update", "-qq"]
        assert timeout == 120
        assert f'Dir::State::lists "{cwd / "lists"}";' in config
        assert 'Dir::Cache::pkgcache "";' in config
        assert 'APT::Update::Error-Mode "any";' in config
        assert "#clear APT::Update::Post-Invoke-Success;" in config
        assert 'Acquire::AllowInsecureRepositories "false";' in config
        raise RuntimeError("repository unavailable")

    monkeypatch.setattr(migration, "run", run)
    with (
        pytest.raises(RuntimeError, match="Signed Ubuntu repository metadata is unavailable"),
        migration.apt_indexes(),
    ):
        pytest.fail("failed index refresh must not provide a resolver")
    assert len(calls) == 1
    assert not calls[0][2].exists()


def test_inhibited_missing_archive_never_refreshes_or_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(migration, "ROOT", tmp_path)
    monkeypatch.setattr(migration, "validate_plan", lambda _: None)
    monkeypatch.setattr(migration, "apt_indexes", lambda: pytest.fail("must stay offline"))
    (tmp_path / "inhibited").touch()
    with pytest.raises(RuntimeError, match="lost its staged archive"):
        migration.stage({"archives": {"missing.deb": {"package": "charon-systemd"}}})


@pytest.fixture
def checkpoint_files(monkeypatch, tmp_path):
    monkeypatch.setattr(migration, "ROOT", tmp_path)
    monkeypatch.setattr(migration, "STATE", tmp_path / "operation.json")
    monkeypatch.setattr(migration, "POLICY", tmp_path / "policy-rc.d")
    monkeypatch.setattr(migration, "UNITS", ())
    monkeypatch.setattr(migration, "validate_plan", lambda _: None)
    monkeypatch.setattr(migration, "TOKEN", tmp_path / "activation.json")

    def write(path, data, mode=0o600):
        # These tests exercise real checkpoint decoding, independently of the
        # root-owned ancestor checks covered by native package qualification.
        assert path.is_relative_to(tmp_path)
        path.write_bytes(data)
        path.chmod(mode)

    monkeypatch.setattr(migration, "write", write)
    return tmp_path


@pytest.mark.parametrize("original", [None, b"#!/bin/sh\n# private-policy-fixture\nexit 0\n"])
def test_policy_plan_has_only_identity_and_resume_restores_exact_local_payload(
    checkpoint_files, monkeypatch, original
):
    policy = migration.POLICY
    if original is not None:
        migration.write(policy, original, 0o750)

    def payload():
        return (
            None
            if not policy.exists()
            else {
                "content": base64.b64encode(policy.read_bytes()).decode(),
                "mode": policy.stat().st_mode & 0o777,
            }
        )

    monkeypatch.setattr(migration, "policy_payload", payload)
    approved = {
        "before": {},
        "desired": {},
        "remove": [],
        "unit_policy": {},
        "startup_policy": migration.policy_snapshot(),
    }
    monkeypatch.setattr(migration, "packages", lambda: {})
    monkeypatch.setattr(migration, "unit_policy", lambda: {})
    monkeypatch.setattr(migration, "verify_staged", lambda _: checkpoint_files)
    monkeypatch.setattr(migration, "native", lambda: {"ready": True})
    monkeypatch.setattr(migration, "CHECK", lambda: None)
    monkeypatch.setattr(migration, "run", lambda _: "")
    expected_payload = payload()
    migration.begin(approved, "a" * 32, {"runtime_plan": approved})
    assert migration.state()["policy"] == expected_payload
    assert "content" not in json.dumps(migration.state()["plan"])
    migration.inhibit()
    migration.begin(approved, "a" * 32, {"runtime_plan": approved})
    assert migration.state()["policy"] == expected_payload
    migration.release()
    assert payload() == expected_payload


def test_changed_policy_blocks_before_local_checkpoint(checkpoint_files, monkeypatch):
    approved = {"before": {}, "unit_policy": {}, "startup_policy": None}
    monkeypatch.setattr(migration, "packages", lambda: {})
    monkeypatch.setattr(migration, "unit_policy", lambda: {})
    monkeypatch.setattr(migration, "policy_payload", lambda: {"content": "bmV3", "mode": 0o750})
    with pytest.raises(RuntimeError, match="startup policy predecessor changed"):
        migration.begin(approved, "b" * 32, {})
    assert not migration.STATE.exists()


def test_completed_corrective_policy_cannot_adopt_an_unapproved_second_read(monkeypatch):
    monkeypatch.setattr(migration, "policy_payload", lambda: {"content": "bmV3", "mode": 0o750})
    with pytest.raises(RuntimeError, match="corrective package startup policy changed"):
        migration.corrective_policy({"phase": "complete"}, {"startup_policy": None})


@pytest.mark.parametrize(
    "policy",
    [
        {"content": "bmV3", "mode": 0o755},
        {"sha256": "bad", "mode": 0o755},
        {"sha256": "a" * 64, "mode": 0o777},
        {"sha256": "a" * 64, "mode": True},
    ],
)
def test_plan_rejects_payload_or_malformed_policy_identity(policy):
    approved = {
        "schema": migration.SCHEMA,
        "before": {},
        "desired": {},
        "install": {},
        "remove": [],
        "archives": {},
        "unit_policy": {},
        "startup_policy": policy,
    }
    with pytest.raises(RuntimeError, match="startup policy identity"):
        migration.validate_plan(approved)


@pytest.mark.parametrize(
    ("step", "interrupted"),
    [
        ("remove", False),
        ("unpack", False),
        ("configure", False),
        ("verify", False),
        ("unpack", True),
    ],
)
def test_reinhibition_preserves_readable_package_checkpoint(
    monkeypatch, checkpoint_files, step, interrupted
):
    tmp_path = checkpoint_files
    approved = {"fixture": "unchanged approved package closure"}
    value = {
        "schema": migration.SCHEMA,
        "operation": "a" * 32,
        "plan": approved,
        "plan_digest": migration.digest(approved),
        "manifest": {"artifact": "same saved request"},
        "phase": "packages",
        "package_step": step,
        "policy": None,
    }
    migration.save(value)
    monkeypatch.setattr(migration, "policy_snapshot", lambda: value["policy"])
    checked = []
    monkeypatch.setattr(migration, "CHECK", lambda: checked.append(migration.state()))
    reloaded = []
    monkeypatch.setattr(migration, "run", lambda args: reloaded.append(args))

    if interrupted:
        writer = migration.write

        def fail_after_intent(path, data, mode=0o600):
            if path == tmp_path / "startup.py":
                raise OSError("fixture interruption after durable intent")
            writer(path, data, mode)

        monkeypatch.setattr(migration, "write", fail_after_intent)
        with pytest.raises(OSError, match="fixture interruption"):
            migration.inhibit()
        assert migration.state() == value
        monkeypatch.setattr(migration, "write", writer)

    for _ in range(2):
        migration.inhibit()
        assert migration.state() == value
        assert (tmp_path / "inhibited").read_bytes() == b"inhibited\n"
        assert migration.POLICY.read_bytes() == migration.POLICY_BYTES
        assert not migration.startup_allowed()

    assert checked == [value] * (3 if interrupted else 2)
    assert reloaded == [["systemctl", "daemon-reload"]] * 2


@pytest.mark.parametrize("phase", ["prepared", "activating"])
def test_inhibition_outside_package_resume_reestablishes_inhibited_phase(
    monkeypatch, checkpoint_files, phase
):
    tmp_path = checkpoint_files
    approved = {"fixture": "unchanged approved package closure"}
    value = {
        "schema": migration.SCHEMA,
        "operation": "b" * 32,
        "plan": approved,
        "plan_digest": migration.digest(approved),
        "manifest": {},
        "phase": phase,
        "policy": None,
    }
    migration.save(value)
    monkeypatch.setattr(migration, "policy_snapshot", lambda: value["policy"])
    monkeypatch.setattr(migration, "CHECK", lambda: None)
    monkeypatch.setattr(migration, "run", lambda _: None)
    (tmp_path / "inhibited").touch()
    boot = tmp_path / "boot-id"
    boot.write_text("fixture-boot\n")
    process = tmp_path / "process-stat"
    process.write_text("123 (fixture) " + " ".join(["0"] * 19 + ["321"]))
    migration.TOKEN.write_text(
        json.dumps(
            {
                "operation": value["operation"],
                "boot": "fixture-boot",
                "pid": 123,
                "start": "321",
            }
        )
    )
    paths = {"/proc/sys/kernel/random/boot_id": boot, "/proc/123/stat": process}
    monkeypatch.setattr(migration, "Path", lambda name: paths.get(str(name), Path(name)))
    assert migration.startup_allowed() is (phase == "activating")

    migration.inhibit()

    assert migration.state() == {**value, "phase": "inhibited"}
    assert not migration.startup_allowed()


def test_package_step_outside_package_phase_remains_invalid(monkeypatch, checkpoint_files):
    approved = {"fixture": "unchanged approved package closure"}
    migration.save(
        {
            "schema": migration.SCHEMA,
            "operation": "c" * 32,
            "plan": approved,
            "plan_digest": migration.digest(approved),
            "phase": "inhibited",
            "package_step": "unpack",
        }
    )
    with pytest.raises(RuntimeError, match="runtime transaction identity is invalid"):
        migration.state()


def test_staged_archive_resume_never_refreshes_repository_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(migration, "ROOT", tmp_path)
    monkeypatch.setattr(migration, "validate_plan", lambda _: None)
    monkeypatch.setattr(migration, "apt_indexes", lambda: pytest.fail("must stay offline"))
    monkeypatch.setattr(migration, "write", lambda *_: None)
    approved = {"archives": {"present.deb": {"package": "charon-systemd"}}}
    directory = tmp_path / migration.digest(approved)
    directory.mkdir()
    (directory / "present.deb").touch()
    verified = []
    monkeypatch.setattr(
        migration,
        "verify_staged",
        lambda value, *, root: (
            verified.append(value) if root == tmp_path else pytest.fail("wrong stage root")
        ),
    )
    assert migration.stage(approved) == directory
    assert verified == [approved]
