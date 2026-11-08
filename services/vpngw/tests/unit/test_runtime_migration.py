from __future__ import annotations

import copy
import sys

import pytest

from nebius_vpngw import runtime_migration as migration


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
    with pytest.raises(RuntimeError, match="repository unavailable"), migration.apt_indexes():
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
    monkeypatch.setattr(migration, "verify_staged", lambda value: verified.append(value))
    assert migration.stage(approved) == directory
    assert verified == [approved]
