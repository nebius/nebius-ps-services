from __future__ import annotations

import base64
import copy
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
import zipfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nebius_vpngw import cli, ordinary_routes
from nebius_vpngw.agent import ordinary
from nebius_vpngw.agent.local_commands import CURRENT, CommandBudget, run
from nebius_vpngw.deploy import ordinary_apply, ordinary_remote, vm_ha_package
from nebius_vpngw.deploy.ssh_push import SSHPush, VMHAAgentArtifact

pytestmark = pytest.mark.usefixtures("runtime_migration_guest")


@pytest.mark.parametrize("reverse", [False, True])
def test_package_inspection_rejects_conflicting_metadata_in_one_prefix(
    tmp_path, monkeypatch, reverse
):
    import importlib.metadata as metadata

    for version, core in (("2.13.5", "2.46.5"), ("2.14.2", "2.50.2")):
        info = tmp_path / f"pydantic-{version}.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.4\nName: pydantic\nVersion: {version}\n"
            f"Requires-Dist: pydantic-core=={core}\n"
        )
    distributions = sorted(metadata.distributions(path=[str(tmp_path)]), key=lambda d: d.version)
    if reverse:
        distributions.reverse()
    monkeypatch.setattr(ordinary_remote.metadata, "distributions", lambda: iter(distributions))
    boot = tmp_path / "boot"
    boot.write_text("boot-id")
    monkeypatch.setattr(ordinary_remote, "BOOT", boot)
    monkeypatch.setattr(ordinary_remote, "CONFIG", tmp_path / "absent.yaml")
    with pytest.raises(RuntimeError, match="conflicting installed package metadata"):
        ordinary_remote.inspect({"files": {}, "assets": {}}, runtime_admission=False)


def test_package_inspection_preserves_distinct_prefix_shadowing(tmp_path, monkeypatch):
    import importlib.metadata as metadata

    distributions = []
    for prefix, version in (("pip", "2.14.2"), ("apt", "2.4.0")):
        base = tmp_path / prefix
        info = base / f"pydantic-{version}.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(
            f"Metadata-Version: 2.4\nName: pydantic\nVersion: {version}\n"
        )
        distributions.extend(metadata.distributions(path=[str(base)]))
    monkeypatch.setattr(ordinary_remote.metadata, "distributions", lambda: iter(distributions))
    boot = tmp_path / "boot"
    boot.write_text("boot-id")
    monkeypatch.setattr(ordinary_remote, "BOOT", boot)
    monkeypatch.setattr(ordinary_remote, "CONFIG", tmp_path / "absent.yaml")
    result = ordinary_remote.inspect({"files": {}, "assets": {}}, runtime_admission=False)
    assert result["dependencies"] == {"pydantic": "2.14.2"}


def test_package_inspection_accepts_same_version_distro_metadata_forms(tmp_path, monkeypatch):
    import importlib.metadata as metadata

    for suffix, requirements in (("dist-info", ""), ("egg-info", "Requires-Dist: cffi>=1.12\n")):
        info = tmp_path / f"cryptography-41.0.7.{suffix}"
        info.mkdir()
        (info / ("METADATA" if suffix == "dist-info" else "PKG-INFO")).write_text(
            "Metadata-Version: 2.4\nName: cryptography\nVersion: 41.0.7\n" + requirements
        )
    distributions = list(metadata.distributions(path=[str(tmp_path)]))
    assert len(distributions) == 2
    monkeypatch.setattr(ordinary_remote.metadata, "distributions", lambda: iter(distributions))
    boot = tmp_path / "boot"
    boot.write_text("boot-id")
    monkeypatch.setattr(ordinary_remote, "BOOT", boot)
    monkeypatch.setattr(ordinary_remote, "CONFIG", tmp_path / "absent.yaml")
    result = ordinary_remote.inspect({"files": {}, "assets": {}}, runtime_admission=False)
    assert result["dependencies"] == {"cryptography": "41.0.7"}


def empty_routes():
    return {
        "configured": True,
        "status": "missing",
        "state_digest": None,
        "previous": [],
        "current": [],
        "pending_digest": None,
        "pending_sources": [],
        "history_digest": None,
        "obligations": [],
        "kernel": {"boot": "boot", "links": [], "routes": []},
    }


@pytest.fixture
def release_wheel(tmp_path):
    root = Path(ordinary.__file__).resolve().parents[1]
    wheel = tmp_path / "nebius_vpngw-0.6.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in root.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, "nebius_vpngw/" + path.relative_to(root).as_posix())
        archive.writestr(
            "nebius_vpngw-0.6.1.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: nebius-vpngw\nVersion: 0.6.1\n",
        )
    return wheel


def test_previous_release_state_is_read_without_migration(tmp_path):
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/previous_release_0_6_0.json").read_text()
    )
    assert ordinary.CAPABILITY not in fixture["capabilities"]["features"]
    path = tmp_path / "last-applied.json"
    path.write_text(json.dumps(fixture["state"]))
    before = path.read_bytes()
    state = ordinary.StateStore(path)
    assert not state.is_changed(fixture["state"]["resolved_config"])
    assert state.load_last_applied() == fixture["state"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("predecessor", ["missing", "previous", "current", "foreign", "symlink"])
def test_ha_asset_installer_checks_ufw_before_writing_any_asset(
    release_wheel, tmp_path, predecessor
):
    root = tmp_path / "guest"
    root.mkdir()
    override = root / ordinary_apply.UFW_ORDERING_PATH.lstrip("/")
    override.parent.mkdir(parents=True)
    canonical = Path(ordinary.__file__).parents[1] / "systemd/nebius-vpngw-ufw-ordering.conf"
    if predecessor == "symlink":
        override.symlink_to(canonical)
    elif predecessor != "missing":
        override.write_bytes(
            {
                "previous": ordinary_apply.PREVIOUS_UFW_ORDERING,
                "current": canonical.read_bytes(),
                "foreign": b"[Unit]\nAfter=customer.service\n",
            }[predecessor]
        )
    script = shlex.split(SSHPush._install_vm_ha_assets_from_artifact_command(str(release_wheel)))[3]
    # Execute the emitted installer unchanged except for filesystem isolation.
    script = re.sub(r"/(?:etc|usr|var)/", lambda match: str(root) + match.group(), script)
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = subprocess.run([sys.executable, "-c", script, str(release_wheel)], capture_output=True)
    if predecessor in {"foreign", "symlink"}:
        assert result.returncode != 0
        assert b"unknown contents" in result.stderr
        assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    else:
        assert result.returncode == 0, result.stderr
        assert override.read_bytes() == canonical.read_bytes()
        assert override.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("ha", [False, True])
@pytest.mark.parametrize("predecessor", ["missing", "previous", "current", "foreign"])
@pytest.mark.parametrize(
    ("path", "released"),
    [
        (
            ordinary_apply.UFW_ORDERING_PATH,
            b"[Unit]\nAfter=network-online.target cloud-init.service\nWants=network-online.target\n",
        ),
        (
            "/etc/systemd/system/nebius-vpngw-agent.service.d/override.conf",
            b"[Unit]\nAfter=strongswan-starter.service frr.service\n",
        ),
        (
            "/etc/systemd/system/nebius-vpngw-agent.service.d/override.conf",
            b"[Unit]\nAfter=strongswan-starter.service frr.service\nWants=strongswan-starter.service frr.service\n",
        ),
    ],
)
def test_ordering_upgrade_admits_only_product_owned_override(
    release_wheel, monkeypatch, ha, predecessor, path, released
):
    owner = vm_ha_package if ha else ordinary_apply
    config = "gateway: {}\nconnections: []\n"
    manifest = owner.artifact(release_wheel, config)
    asset = manifest["assets"][path]
    prior = {
        "missing": None,
        "previous": ordinary_apply.sha(released),
        "current": asset["sha256"],
        "foreign": ordinary_apply.sha(b"[Unit]\nAfter=customer.service\n"),
    }[predecessor]
    observation = dict.fromkeys(vm_ha_package.PACKAGE_FIELDS)
    observation.update(
        files={},
        assets={path: prior},
        asset_modes={},
        dependencies={},
        requirements={},
        markers={},
        route_ownership=empty_routes(),
        config_sha256=manifest["config_sha256"],
        verified=False,
        agent_state="active",
        runtime={"ready": True},
        runtime_plan={},
        runtime_pending=None,
    )
    monkeypatch.setattr(ordinary_apply, "remote", Mock(return_value={"observation": observation}))
    monkeypatch.setattr(ordinary_apply, "resolve_dependencies", lambda *args: [])
    ssh = SimpleNamespace(_build_wheel=lambda **kw: release_wheel, _find_project_root=lambda: None)
    args = (ssh, "203.0.113.10", SimpleNamespace(hostname="gateway", config_yaml=config), {})
    if predecessor == "foreign":
        with pytest.raises(RuntimeError, match="unknown contents"):
            owner.inspect_plan(*args, target_identity="compute-0")
    else:
        plan = owner.inspect_plan(*args, target_identity="compute-0")
        plan.close()


@pytest.mark.parametrize("wants", [False, True])
def test_ha_asset_installer_upgrades_released_agent_ordering(release_wheel, tmp_path, wants):
    root = tmp_path / "guest"
    override = root / "etc/systemd/system/nebius-vpngw-agent.service.d/override.conf"
    override.parent.mkdir(parents=True)
    # Exact bytes emitted by the authentic 0.6.0-0.6.3 service owners.
    released = b"[Unit]\nAfter=strongswan-starter.service frr.service\n"
    if wants:
        released += b"Wants=strongswan-starter.service frr.service\n"
    override.write_bytes(released)
    script = shlex.split(SSHPush._install_vm_ha_assets_from_artifact_command(str(release_wheel)))[3]
    script = re.sub(r"/(?:etc|usr|var)/", lambda match: str(root) + match.group(), script)
    result = subprocess.run([sys.executable, "-c", script, str(release_wheel)], capture_output=True)
    assert result.returncode == 0, result.stderr
    canonical = Path(ordinary.__file__).parents[1] / "systemd/nebius-vpngw-agent-ordering.conf"
    assert override.read_bytes() == canonical.read_bytes()


def test_previous_capability_absence_plans_approved_upgrade(release_wheel, monkeypatch):
    config = "gateway: {}\nconnections: []\n"
    manifest = ordinary_apply.artifact(release_wheel, config)
    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/previous_release_0_6_0.json").read_text()
    )
    observed = {
        "files": {},
        "assets": {},
        "asset_modes": {},
        "boot_id": "boot",
        "package_version": fixture["state"]["package_version"],
        "config_sha256": manifest["config_sha256"],
        "verified": False,
        "agent_state": "active",
        "runtime": {"ready": True},
        "runtime_plan": {},
        "runtime_pending": None,
        "dependencies": {"cryptography": "46.0.0"},
        "requirements": {},
        "markers": {},
        "route_ownership": empty_routes(),
    }
    remote = Mock(return_value={"observation": observed})
    monkeypatch.setattr(ordinary_apply, "remote", remote)
    ssh = SimpleNamespace(_build_wheel=lambda **kw: release_wheel, _find_project_root=lambda: None)
    plan = ordinary_apply.inspect_plan(
        ssh,
        "203.0.113.10",
        SimpleNamespace(hostname="gateway-0", config_yaml=config),
        {},
        target_identity="compute-0",
    )
    try:
        assert not plan.noop and not plan.dependency_paths
        assert "install agent wheel" in plan.effects
        assert plan.manifest["expected_dependencies"]["cryptography"] == "46.0.0"
        remote.reset_mock()
        with pytest.raises(RuntimeError, match="approval"):
            ordinary_apply.execute_plan(ssh, plan, {}, approved=False)
        remote.assert_not_called()
    finally:
        plan.close()


def test_ordinary_parent_resumes_before_guest_without_resolving_new_packages(
    release_wheel, monkeypatch, tmp_path
):
    import io

    from nebius_vpngw import apply_approval
    from nebius_vpngw.deploy import ordinary_command
    from nebius_vpngw.deploy.apply_command import CommandJournal

    config = "gateway: {}\nconnections: []\n"
    instance = SimpleNamespace(hostname="gateway", config_yaml=config)
    ssh = SimpleNamespace(_build_wheel=lambda **kw: release_wheel, _find_project_root=lambda: None)
    raw, filename = b"approved dependency", "example_dep-1.2-py3-none-any.whl"
    manifest = ordinary_apply.artifact(release_wheel, config)
    observed = {
        "files": {},
        "assets": {},
        "asset_modes": {},
        "boot_id": "boot",
        "package_version": "0.6.0",
        "config_sha256": "old",
        "verified": False,
        "agent_state": "active",
        "runtime": {"ready": True},
        "runtime_plan": {"closure": "approved"},
        "runtime_pending": None,
        "dependencies": {},
        "requirements": {},
        "markers": {},
        "route_ownership": empty_routes(),
        "wheel_tags": ["py3-none-any"],
    }

    def remote(_ssh, _target, _instance, _local, request):
        current = copy.deepcopy(observed)
        if request["manifest"].get("command_approval"):
            current["runtime_plan"] = request["manifest"]["runtime_plan"]
        return {"observation": current}

    def resolve(_manifest, _observed, directory):
        path = directory / filename
        path.write_bytes(raw)
        return [path]

    monkeypatch.setattr(ordinary_apply, "remote", remote)
    monkeypatch.setattr(ordinary_apply, "resolve_dependencies", resolve)
    prepared = ordinary_apply.inspect_plan(ssh, "host", instance, {}, target_identity="compute")
    journal = CommandJournal("project", "gateway", root=tmp_path / "command")
    args = {
        "local": {},
        "targets": {"gateway": ("compute", "host")},
        "plans": {"gateway": prepared},
        "attachments": None,
        "attachment_pending": None,
        "artifact_sha256": manifest["wheel_sha256"],
    }
    command = ordinary_command.inspect(journal, **args)
    with journal:
        command.begin()
        journal.started("guest:gateway")
    old_payload = prepared.dependency_paths[0]
    prepared.close()
    assert not old_payload.exists()
    monkeypatch.setattr(
        ordinary_apply,
        "resolve_dependencies",
        lambda *args: pytest.fail("must not resolve newer dependencies"),
    )
    observed["runtime_plan"] = {"closure": "new apt candidates"}
    saved = ordinary_command.package_manifests(
        journal, local={}, targets=args["targets"], artifact=args["artifact_sha256"]
    )["gateway"]

    def download(url, **kwargs):
        if url == "https://pypi.org/pypi/example-dep/1.2/json":
            return io.BytesIO(
                json.dumps(
                    {
                        "urls": [
                            {
                                "filename": filename,
                                "packagetype": "bdist_wheel",
                                "digests": {"sha256": ordinary_apply.sha(raw)},
                                "url": "https://files.pythonhosted.org/packages/approved.whl",
                            }
                        ]
                    }
                ).encode()
            )
        assert url == "https://files.pythonhosted.org/packages/approved.whl"
        return io.BytesIO(raw)

    monkeypatch.setattr(ordinary_apply, "urlopen", download)
    resumed = ordinary_apply.inspect_plan(
        ssh, "host", instance, {}, target_identity="compute", resume_manifest=saved
    )
    try:
        assert resumed.dependency_paths[0].read_bytes() == raw
        assert resumed.manifest == saved
        args["plans"] = {"gateway": resumed}
        continued = ordinary_command.inspect(journal, **args)
        assert continued.resumed and continued.approval == command.approval
    finally:
        resumed.close()
    observed.update(
        files=manifest["files"],
        package_version=manifest["version"],
        verified=True,
        config_sha256=manifest["config_sha256"],
        dependencies=saved["expected_dependencies"],
        assets={path: asset["sha256"] for path, asset in manifest["assets"].items()},
        asset_modes={path: [asset["mode"], 0, 0] for path, asset in manifest["assets"].items()},
        runtime_completion={
            "approval": saved["command_approval"],
            "manifest": apply_approval.manifest_identity(saved),
        },
    )
    monkeypatch.setattr(ordinary_apply, "urlopen", lambda *a, **kw: pytest.fail("no-op download"))
    complete = ordinary_apply.inspect_plan(
        ssh, "host", instance, {}, target_identity="compute", resume_manifest=saved
    )
    try:
        assert complete.noop and not complete.dependency_paths
        args["plans"] = {"gateway": complete}
        with journal:
            ordinary_command.inspect(journal, **args).begin()
            journal.complete()
        assert journal.pending() is None
    finally:
        complete.close()


def test_ordinary_readonly_inspection_honors_pinned_native_plan(
    monkeypatch, tmp_path, runtime_migration_guest
):
    from nebius_vpngw import runtime_migration

    monkeypatch.setattr(ordinary_remote, "CONFIG", tmp_path / "missing-config")
    boot = tmp_path / "boot"
    boot.write_text("12345678-1234-1234-1234-123456789012")
    monkeypatch.setattr(ordinary_remote, "BOOT", boot)
    monkeypatch.setattr(
        runtime_migration, "plan", lambda *args: pytest.fail("must not resolve another apt closure")
    )
    observed = ordinary_remote.inspect(
        {
            "files": {},
            "assets": {},
            "runtime_plan": runtime_migration_guest,
            "command_approval": {
                "schema": "nebius-vpngw.apply-approval.v1",
                "approval": "a" * 64,
                "scope": "b" * 64,
            },
        },
        runtime_admission=False,
    )
    assert observed["runtime_plan"] == runtime_migration_guest


@pytest.mark.parametrize(
    ("existing", "enrollment", "retained"),
    [
        ({"gateway-0", "gateway-1"}, {"gateway-0", "gateway-1"}, set()),
        ({"gateway-0", "gateway-1"}, {"gateway-1"}, {"gateway-0"}),
        ({"gateway-0"}, {"gateway-1"}, {"gateway-0"}),
        ({"gateway-0", "gateway-1"}, set(), {"gateway-0", "gateway-1"}),
    ],
)
def test_ha_package_plans_bind_only_retained_compute(
    release_wheel, monkeypatch, existing, enrollment, retained
):
    artifact = VMHAAgentArtifact(
        path=release_wheel,
        sha256=hashlib.sha256(release_wheel.read_bytes()).hexdigest(),
        source="test",
        capabilities=(),
        device=release_wheel.stat().st_dev,
        inode=release_wheel.stat().st_ino,
    )
    instances = tuple(SimpleNamespace(hostname=f"gateway-{index}") for index in range(2))
    inspected = []
    closed = []

    def inspect_plan(
        _ssh,
        _target,
        instance,
        _local,
        *,
        target_identity,
        resume_parent=None,
        resume_manifest=None,
        reservation=None,
    ):
        assert resume_parent is None and resume_manifest is None and reservation is None
        assert instance.hostname not in enrollment, "retired guest must not be inspected"
        assert target_identity == instance.hostname
        inspected.append(instance.hostname)
        return SimpleNamespace(dependency_paths=[], close=lambda: closed.append(instance.hostname))

    monkeypatch.setattr(vm_ha_package, "inspect_plan", inspect_plan)
    with ExitStack() as stack:
        token = cli._VM_MANAGER_LIFETIMES.set(stack)
        try:
            prepared = cli._plan_vm_ha_package_dependencies(
                artifact,
                instances=instances,
                targets={name: "203.0.113.10" for name in existing},
                enrollment_hosts=enrollment,
                local={},
                ssh_policy=None,
            )
        finally:
            cli._VM_MANAGER_LIFETIMES.reset(token)
        assert {name for name, _plan in prepared.dependency_plans} == retained
        assert set(inspected) == retained
    assert set(closed) == retained


@pytest.fixture
def transaction(
    release_wheel, tmp_path, monkeypatch, ordinary_operation_guest, runtime_migration_guest
):
    root = tmp_path / "guest"
    root.mkdir()
    for name, basename in (
        ("CONFIG", "config-resolved.yaml"),
        ("BOOT", "boot"),
        ("LOCK", "deploy.lock"),
        ("ROUTING_LOCK", "routing.lock"),
    ):
        monkeypatch.setattr(ordinary_remote, name, root / basename)
    ordinary_remote.BOOT.write_text("12345678-1234-1234-1234-123456789012")
    ordinary_remote.CONFIG.write_text("gateway: {}\nconnections: []\n# previous release\n")
    config = "gateway: {}\nconnections: []\n"
    manifest = ordinary_apply.artifact(release_wheel, config)
    manifest["assets"] = {
        str(root / Path(path).name): item for path, item in manifest["assets"].items()
    }
    manifest["runtime_plan"] = runtime_migration_guest
    manifest["expected_dependencies"] = {
        "nebius-vpngw": manifest["version"],
        "cryptography": "46.0.0",
    }
    state = {
        "files": {},
        "package_version": "0.6.0",
        "agent_state": "active",
        "runtime": {"ready": True},
        "runtime_plan": {},
        "runtime_pending": None,
        "verified": False,
        "dependencies": {"nebius-vpngw": "0.6.0", "cryptography": "46.0.0"},
    }
    effects = []

    def inspect(manifest, *, verify_runtime=True, route_locked=False):
        return {
            **copy.deepcopy(state),
            "route_ownership": empty_routes(),
            "boot_id": ordinary_remote.BOOT.read_text(),
            "config_sha256": ordinary_remote.sha(ordinary_remote.CONFIG.read_bytes()),
            "environment": ordinary_operation_guest(time.monotonic() + 600).environment(),
            "assets": {
                path: ordinary_remote.sha(Path(path).read_bytes()) if Path(path).exists() else None
                for path in manifest["assets"]
            },
            "asset_modes": {
                path: [Path(path).stat().st_mode & 0o777, 0, 0] if Path(path).exists() else None
                for path in manifest["assets"]
            },
        }

    def command(args, **kwargs):
        effects.append(args)
        if args[:2] == ["systemctl", "stop"]:
            state["agent_state"] = "inactive"
        elif args[:2] == ["systemctl", "start"]:
            state["agent_state"] = "active"
        elif args[:2] == ["systemctl", "show"]:
            return 0, "0"
        elif args[:2] == ["systemctl", "is-active"]:
            return 0, state["agent_state"]
        elif "pip" in args:
            assert "--no-deps" in args and "--no-index" in args
            assert "--upgrade" not in args and "--ignore-installed" not in args
            with open(ordinary_remote.ROUTING_LOCK) as lock, pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state.update(
                files=copy.deepcopy(manifest["files"]),
                package_version=manifest["version"],
                dependencies=dict(manifest["expected_dependencies"]),
            )
        return 0, ""

    def receipt(config_sha, boot, *, verify_only, request_id=None):
        effects.append(["verify" if verify_only else "reconcile"])
        assert ordinary_remote.CONFIG.read_bytes() == config.encode()
        for path, item in manifest["assets"].items():
            assert ordinary_remote.sha(Path(path).read_bytes()) == item["sha256"]
        if not verify_only:
            from nebius_vpngw import ordinary_operations as ops

            ledger_digest = ordinary_routes.RouteStore().commit(ops.Journal().read(), [])
            (root / "ordinary-verification.json").write_text(
                json.dumps({ordinary_routes.PROOF_KEY: ledger_digest})
            )
            state["verified"] = True
        return {
            "schema": ordinary.SCHEMA,
            "status": "unchanged" if verify_only else "applied",
            "request_id": request_id or "0" * 32,
            "config_sha256": config_sha,
            "boot_id": boot,
            "package_identity": manifest["package_identity"],
            "render_version": manifest["render_version"],
            "proof_sha256": ordinary_remote.sha((root / "ordinary-verification.json").read_bytes()),
        }

    monkeypatch.setattr(ordinary_remote, "inspect", inspect)
    monkeypatch.setattr(ordinary_remote, "command", command)
    monkeypatch.setattr(ordinary_remote, "agent_receipt", receipt)
    monkeypatch.setattr(ordinary_remote, "DEADLINE", time.monotonic() + 600)
    ordinary_operation_guest.on_service = staticmethod(
        lambda action, name: ordinary_remote.command(["systemctl", action, name])
    )
    manifest["route_plan"] = ordinary_routes.RouteRetirementPlan.build(empty_routes(), []).payload
    monkeypatch.setattr(ordinary_routes.RouteStore, "snapshot", lambda *a, **kw: empty_routes())
    request = {
        "manifest": manifest,
        "noop": False,
        "approval": "approved",
        "plan_digest": "approved",
        "request_id": "a" * 32,
        "predecessor": ordinary_remote.canonical(inspect(manifest)),
        "wheel": base64.b64encode(release_wheel.read_bytes()).decode(),
        "config": base64.b64encode(config.encode()).decode(),
        "dependency_wheels": [],
    }
    return SimpleNamespace(
        request=request,
        effects=effects,
        state=state,
        inspect=inspect,
        command=command,
        receipt=receipt,
    )


def test_route_timer_starts_only_after_native_release(
    transaction, monkeypatch, ordinary_operation_guest
):
    from nebius_vpngw import runtime_migration

    settled = ordinary_operation_guest.unit_settled
    timer_phases = []

    def guarded_timer(self, effect):
        if effect.get("unit") == "nebius-vpngw-fix-routes.timer" and effect["action"] == "start":
            phase = runtime_migration.state()["phase"]
            timer_phases.append(phase)
            # PID 1 skips the timer while its negative ConditionPathExists is false.
            return phase == "complete"
        return settled(self, effect)

    monkeypatch.setattr(ordinary_operation_guest, "unit_settled", guarded_timer)
    monkeypatch.setattr(ordinary_operation_guest, "unit_finished", lambda self, effect: True)
    result = ordinary_remote.execute(transaction.request)
    assert result["status"] == "applied", result
    assert timer_phases == ["complete"]


def test_approved_transaction_publishes_exact_assets_then_rerun_has_no_effects(transaction):
    tx = transaction
    result = ordinary_remote.execute(tx.request)
    assert result["status"] == "applied", result
    assert result["receipt"]["request_id"] == tx.request["request_id"]
    assert tx.effects.index(["reconcile"]) < next(
        i for i, row in enumerate(tx.effects) if row[:2] == ["systemctl", "start"]
    )
    paths = [ordinary_remote.CONFIG, *map(Path, tx.request["manifest"]["assets"])]
    before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    tx.request.update(
        noop=True,
        approval=None,
        predecessor=ordinary_remote.canonical(tx.inspect(tx.request["manifest"])),
    )
    tx.effects.clear()
    assert ordinary_remote.execute(tx.request)["status"] == "unchanged"
    assert tx.effects == [["verify"]]
    assert before == {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}


def test_frozen_dependency_wheels_use_shared_distro_aware_installer(transaction):
    tx = transaction
    name = "fixture_dependency-2.0-py3-none-any.whl"
    raw = b"selected dependency bytes"
    tx.request["manifest"]["dependency_wheels"] = {name: ordinary_remote.sha(raw)}
    tx.request["dependency_wheels"] = [{"name": name, "content": base64.b64encode(raw).decode()}]
    assert ordinary_remote.execute(tx.request)["status"] == "applied"
    installs = [row for row in tx.effects if ordinary_remote.DEPENDENCY_INSTALL_SCRIPT in row]
    assert len(installs) == 1 and installs[0][-1].endswith("/" + name)
    assert tx.effects.index(installs[0]) < next(
        index for index, row in enumerate(tx.effects) if "--force-reinstall" in row
    )


@pytest.mark.parametrize("fault", ["missing-ledger", "wrong-proof"])
def test_final_confirmation_rejects_missing_retirement_commit(transaction, monkeypatch, fault):
    tx = transaction

    def receipt(*args, **kwargs):
        value = tx.receipt(*args, **kwargs)
        if kwargs["verify_only"]:
            if fault == "missing-ledger":
                ordinary_routes.RouteStore().history.unlink()
            else:
                proof = ordinary_remote.CONFIG.parent / "ordinary-verification.json"
                proof.write_text(json.dumps({ordinary_routes.PROOF_KEY: "0" * 64}))
                value["proof_sha256"] = ordinary_remote.sha(proof.read_bytes())
        return value

    monkeypatch.setattr(ordinary_remote, "agent_receipt", receipt)
    result = ordinary_remote.execute(tx.request)
    assert result["status"] == "failed" and result["stage"] == "verify"


def test_final_confirmation_waits_for_guarded_timer_job_and_rechecks(
    transaction, monkeypatch, ordinary_operation_guest
):
    from nebius_vpngw import ordinary_operations as ops

    tx = transaction
    observations = []

    def quiet(self, names):
        value = ops.Journal().read()
        if value and value["phase"] == "verify":
            observations.append(value["state"])
            if len(observations) == 2:
                with open(ordinary_remote.ROUTING_LOCK) as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with pytest.raises(ops.OperationBlocked):
                    ops.require_idle()
            # An already-enabled timer dispatches its guarded oneshot after
            # activation. The job settles without touching the pending apply.
            return len(observations) > 1
        return True

    monkeypatch.setattr(ordinary_operation_guest, "quiet", quiet)
    result = ordinary_remote.execute(tx.request)

    assert result["status"] == "applied", result
    assert len(observations) >= 3
    assert set(observations) == {"active"}
    assert tx.effects.count(["reconcile"]) == 1
    assert tx.effects.count(["verify"]) == 2
    assert ops.Journal().read()["state"] == "complete"


def test_final_confirmation_rejects_artifact_drift_while_timer_job_settles(
    transaction, monkeypatch, ordinary_operation_guest
):
    from nebius_vpngw import ordinary_operations as ops

    tx = transaction
    observations = []

    def quiet(self, names):
        value = ops.Journal().read()
        if value and value["phase"] == "verify":
            observations.append(value["state"])
            if len(observations) == 1:
                return False
            tx.state["dependencies"]["cryptography"] = "changed-during-settlement"
        return True

    monkeypatch.setattr(ordinary_operation_guest, "quiet", quiet)
    result = ordinary_remote.execute(tx.request)

    assert len(observations) >= 2
    assert tx.effects.count(["verify"]) == 2
    assert result["status"] == "failed" and result["stage"] == "verify"
    assert ops.Journal().read()["state"] == "unresolved"


@pytest.mark.parametrize("fault", ["busy", "late", "owner", "observation"])
def test_final_settlement_preserves_deadline_ownership_and_read_failures(
    transaction, monkeypatch, ordinary_operation_guest, fault
):
    from nebius_vpngw import ordinary_operations as ops

    tx = transaction
    observations = []
    clock = SimpleNamespace(now=time.monotonic())
    monkeypatch.setattr(
        ordinary_remote,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock.now,
            sleep=lambda seconds: setattr(clock, "now", clock.now + seconds),
        ),
    )

    def quiet(self, names):
        value = ops.Journal().read()
        if not value or value["phase"] != "verify":
            return True
        observations.append(value["state"])
        if len(observations) == 1:
            self.deadline = clock.now + 0.2
            return False
        if fault == "late":
            clock.now = self.deadline + 1
            return True
        if fault == "owner":
            value["id"] = "b" * 32
            ops.Journal().write(value)
        if fault == "observation":
            raise ops.OperationBlocked("ordinary_manager_observation_failed")
        return False

    monkeypatch.setattr(ordinary_operation_guest, "quiet", quiet)
    result = ordinary_remote.execute(tx.request)

    assert result["status"] == "failed" and result["stage"] == "verify"
    assert len(observations) >= 2
    assert tx.effects.count(["reconcile"]) == 1
    assert ops.Journal().read()["state"] != "complete"
    if fault == "owner":
        assert ops.Journal().read()["id"] == "b" * 32


@pytest.mark.parametrize("releases", [True, False])
def test_final_confirmation_bounds_guarded_routing_lock_contention(
    transaction, monkeypatch, releases
):
    tx = transaction
    clock = SimpleNamespace(now=time.monotonic(), sleeps=0)
    held = []

    def sleep(seconds):
        clock.now += seconds
        clock.sleeps += 1
        if releases and held:
            os.close(held.pop())

    monkeypatch.setattr(
        ordinary_remote, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
    )

    def receipt(*args, **kwargs):
        value = tx.receipt(*args, **kwargs)
        if kwargs["verify_only"] and clock.sleeps == 0:
            # The periodic helper takes the routing lock before checking the
            # pending-operation guard. It may race the parent's final lock.
            lock = os.open(ordinary_remote.ROUTING_LOCK, os.O_RDONLY)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held.append(lock)
            ordinary_remote.OPERATION.manager.deadline = clock.now + 0.2
        return value

    monkeypatch.setattr(ordinary_remote, "agent_receipt", receipt)
    try:
        result = ordinary_remote.execute(tx.request)
        assert clock.sleeps > 0
        assert result["status"] == ("applied" if releases else "failed")
        assert tx.effects.count(["reconcile"]) == 1
        assert tx.effects.count(["verify"]) == (2 if releases else 1)
    finally:
        for lock in held:
            os.close(lock)


@pytest.mark.parametrize("member", ["ordinary_routes.py", "tunnel_state.py", "agent/ordinary.py"])
def test_selected_wheel_must_implement_the_approved_route_contract(release_wheel, tmp_path, member):
    selected = tmp_path / "selected.whl"
    with zipfile.ZipFile(release_wheel) as source, zipfile.ZipFile(selected, "w") as target:
        for name in source.namelist():
            raw = source.read(name)
            if name == "nebius_vpngw/" + member:
                raw = b"# previous route behavior\n"
            target.writestr(name, raw)
    with pytest.raises(RuntimeError, match="route ownership|mandatory managed ingress"):
        ordinary_apply.artifact(selected, "gateway: {}\nconnections: []\n")


def test_quiescence_drift_prevents_install_and_publication(transaction, monkeypatch):
    tx = transaction
    previous = ordinary_remote.CONFIG.read_bytes()

    def command(args, **kwargs):
        result = tx.command(args, **kwargs)
        if args[:2] == ["systemctl", "stop"]:
            tx.state["dependencies"]["cryptography"] = "changed"
        return result

    monkeypatch.setattr(ordinary_remote, "command", command)
    result = ordinary_remote.execute(tx.request)
    assert result["status"] == "failed" and result["stage"] == "install"
    assert not any("pip" in row for row in tx.effects)
    assert ordinary_remote.CONFIG.read_bytes() == previous


@pytest.mark.parametrize("fault", ["config", "dependencies", "proof", "service", "permissions"])
def test_final_race_cannot_produce_success(transaction, monkeypatch, fault):
    tx = transaction

    def receipt(*args, **kwargs):
        result = tx.receipt(*args, **kwargs)
        if kwargs["verify_only"]:
            if fault == "config":
                ordinary_remote.CONFIG.write_text("changed")
            elif fault == "dependencies":
                tx.state["dependencies"]["cryptography"] = "changed"
            elif fault == "proof":
                (ordinary_remote.CONFIG.parent / "ordinary-verification.json").write_text("changed")
            elif fault == "service":
                tx.state["agent_state"] = "failed"
            else:
                Path(next(iter(tx.request["manifest"]["assets"]))).chmod(0o777)
        return result

    monkeypatch.setattr(ordinary_remote, "agent_receipt", receipt)
    assert ordinary_remote.execute(tx.request)["status"] == "failed"


def test_concurrent_transaction_rejected_before_guest_effects(transaction):
    tx = transaction
    with open(ordinary_remote.LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert ordinary_remote.execute(tx.request)["status"] == "failed"
    assert not tx.effects


def test_local_timeout_also_joins_descendants(tmp_path):
    marker = tmp_path / "late"
    child = "import time,pathlib;time.sleep(.4);pathlib.Path(" + repr(str(marker)) + ").touch()"
    parent = (
        "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"
        + repr(child)
        + "]);time.sleep(10)"
    )
    token = CURRENT.set(CommandBudget(time.monotonic() + 0.1))
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run([sys.executable, "-c", parent], capture_output=True, text=True)
    finally:
        CURRENT.reset(token)
    time.sleep(0.5)
    assert not marker.exists()


@pytest.mark.parametrize("drift", [False, True])
@pytest.mark.parametrize(
    "failure", [None, "timeout", "authentication", "host identity", "reboot", "policy drift"]
)
def test_ha_noop_requires_both_member_artifacts_and_fresh_health(
    release_wheel, monkeypatch, drift, failure
):
    instances = tuple(
        SimpleNamespace(
            hostname=f"gateway-{i}",
            config_yaml="gateway: {}\nconnections: []\nvm_ha: {}\n",
        )
        for i in range(2)
    )
    monkeypatch.setattr(
        ordinary_apply.ingress_ssh,
        "verify_fresh",
        Mock(
            side_effect=(
                ordinary_apply.ingress_ssh.IngressVerificationDrift("confirmed drift")
                if failure == "policy drift"
                else RuntimeError(f"fresh SSH verification: {failure}")
            )
            if failure
            else None
        ),
    )
    artifact = SimpleNamespace(path=release_wheel, verify_current=Mock())
    calls = []

    def remote(ssh, target, instance, local, request):
        calls.append(instance.hostname)
        manifest = request["manifest"]
        return {
            "observation": {
                "runtime": {"ready": True},
                "runtime_pending": None,
                "files": manifest["files"],
                "assets": {path: item["sha256"] for path, item in manifest["assets"].items()},
                "asset_modes": {
                    path: [item["mode"], 0, 0] for path, item in manifest["assets"].items()
                },
                "ha_config_digest": ordinary_apply.digest(
                    {
                        "gateway": {},
                        "connections": [],
                        "vm_ha": {},
                    }
                ),
                "config_sha256": manifest["config_sha256"],
                "boot_id": "changed" if drift and len(calls) == 3 else "boot",
                "requirements": {},
                "dependencies": {},
                "markers": {},
            }
        }

    monkeypatch.setattr(ordinary_apply, "remote", remote)
    from test_apply_health import snapshot

    inspection = SimpleNamespace(snapshot=snapshot())
    monkeypatch.setattr(cli, "_inspect_vm_ha_status_with_region", Mock(return_value=inspection))
    ssh = Mock()
    monkeypatch.setattr(cli, "SSHPush", lambda **kwargs: ssh)

    def check():
        return cli._unchanged_vm_ha_apply(
            Path("generic.yaml"),
            instances=instances,
            local={},
            targets={i.hostname: "203.0.113.10" for i in instances},
            ssh_policy=None,
            artifact=artifact,
            region=None,
        )

    if failure and failure != "policy drift" and not drift:
        with pytest.raises(RuntimeError, match=f"fresh SSH verification: {failure}"):
            check()
    else:
        assert check() is (not drift and failure != "policy drift")
    assert cli._inspect_vm_ha_status_with_region.call_count == 2
    assert calls[:2] == [i.hostname for i in instances]
    assert not ssh.mock_calls


def test_extras_are_part_of_transitive_dependency_compatibility():
    observed = {
        "markers": {},
        "dependencies": {"example": "1.2"},
        "requirements": {"example": ['child>=2; extra == "secure"']},
    }
    assert ordinary_apply.dependencies_satisfied(["example>=1"], observed)
    assert not ordinary_apply.dependencies_satisfied(["example[secure]>=1"], observed)


@pytest.mark.parametrize(
    ("requirement", "markers", "enabled"),
    [
        ('child @ https://example.invalid/child.whl ; extra == "docs"', {}, False),
        (
            'child @ https://example.invalid/child.whl ; sys_platform == "win32"',
            {"sys_platform": "linux"},
            False,
        ),
        (
            'child @ https://example.invalid/child.whl ; sys_platform == "linux"',
            {"sys_platform": "linux"},
            True,
        ),
        ("child @ https://example.invalid/child.whl", {}, True),
    ],
)
def test_direct_url_policy_applies_only_to_active_dependencies(requirement, markers, enabled):
    observed = {
        "markers": markers,
        "dependencies": {"example": "1.2"},
        "requirements": {"example": [requirement]},
    }
    if enabled:
        with pytest.raises(RuntimeError, match="Direct URL dependencies"):
            ordinary_apply.dependencies_satisfied(["example>=1"], observed)
    else:
        assert ordinary_apply.dependencies_satisfied(["example>=1"], observed)


def test_selected_extra_keeps_direct_url_dependency_blocking():
    observed = {
        "markers": {},
        "dependencies": {"example": "1.2"},
        "requirements": {
            "example": ['child @ https://example.invalid/child.whl ; extra == "docs"']
        },
    }
    with pytest.raises(RuntimeError, match="Direct URL dependencies"):
        ordinary_apply.dependencies_satisfied(["example[docs]>=1"], observed)


@pytest.mark.parametrize("slow_upload", [False, True])
def test_client_deadline_includes_upload_and_always_closes(monkeypatch, slow_upload):
    monkeypatch.setattr(ordinary_apply.ingress_ssh, "preflight", lambda *args: "boot")
    now = [0.0]
    sent = bytearray()
    channel = Mock()

    def send(data):
        sent.extend(data)
        if slow_upload:
            now[0] = 631.0
        return len(data)

    channel.send.side_effect = send
    channel.recv_ready.side_effect = [True, False, False]
    channel.recv.return_value = b'{"status":"inspected","observation":{}}'
    channel.recv_stderr_ready.return_value = False
    channel.exit_status_ready.return_value = True
    channel.recv_exit_status.return_value = 0
    client = Mock()
    client.exec_command.return_value = (
        SimpleNamespace(channel=channel),
        SimpleNamespace(channel=channel),
        Mock(),
    )
    ssh = SimpleNamespace(
        _ensure_paramiko=lambda: SimpleNamespace(SSHClient=lambda: client),
        _ssh_policy=None,
        _connect_client=Mock(),
    )
    monkeypatch.setattr(ordinary_apply, "configure_paramiko_host_verification", Mock())
    monkeypatch.setattr(ordinary_apply.time, "monotonic", lambda: now[0])
    request = {"action": "inspect", "manifest": {}, "padding": "x" * 70000}
    if slow_upload:
        with pytest.raises(RuntimeError, match="during upload"):
            ordinary_apply.remote(
                ssh,
                "203.0.113.10",
                SimpleNamespace(
                    hostname="gateway-0",
                    config_yaml="gateway: {}",
                ),
                {},
                request,
            )
        channel.shutdown_write.assert_not_called()
    else:
        assert (
            ordinary_apply.remote(
                ssh,
                "203.0.113.10",
                SimpleNamespace(
                    hostname="gateway-0",
                    config_yaml="gateway: {}",
                ),
                {},
                request,
            )["status"]
            == "inspected"
        )
        source, payload = bytes(sent).split(b"\n", 1)
        assert b"<ordinary-transaction>" in base64.b64decode(source, validate=True)
        assert json.loads(payload) == request
        assert all(0 < call.args[0] <= 630 for call in channel.settimeout.call_args_list)
    client.close.assert_called_once()


def test_frr_observation_checks_policy_without_requiring_peer_sessions():
    desired = "router bgp 65001\n neighbor 169.254.1.2 remote-as 65002\n address-family ipv4 unicast\n  neighbor 169.254.1.2 route-map ADVERTISE-ACTIVE out\n exit-address-family\nroute-map ADVERTISE-ACTIVE permit 10\n set metric 0\n"
    running = "Building configuration...\n!\n" + desired + "!\nend\n"
    assert ordinary.frr_policy_rows(desired) <= ordinary.frr_policy_rows(running)
    assert not ordinary.frr_policy_rows(desired) <= ordinary.frr_policy_rows(
        running.replace(" set metric 0", " set metric 100")
    )


def test_preview_cannot_invoke_route_or_service_effects(monkeypatch):
    from nebius_vpngw.agent import frr_renderer, strongswan_renderer

    effect = Mock(side_effect=AssertionError("preview mutated runtime"))
    monkeypatch.setattr(frr_renderer.FRRRenderer, "ensure_local_prefix_routes", effect)
    monkeypatch.setattr(frr_renderer, "run", effect)
    monkeypatch.setattr(strongswan_renderer, "run", effect)
    files = ordinary.managed_files(
        {
            "gateway": {"local_prefixes": ["10.0.0.0/24"]},
            "connections": [],
        }
    )
    assert files[ordinary.SWANCTL_CONF] and files[ordinary.FRR_CONF]
    effect.assert_not_called()


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "sysctl",
        "table220",
        "neighbor",
        "policy",
        "parent",
        "connection",
        "firewall",
        "files",
        "static-valid",
        "static-stale",
        "static-multipath",
        "static-foreign",
        "static-other-table",
        "static-default",
        "static-host",
        "bgp-learned",
    ],
)
def test_local_observation_checks_invariants_without_peer_liveness(
    tmp_path, monkeypatch, fault, ordinary_protection_guest
):
    cfg = {
        "gateway": {"local_prefixes": ["10.0.0.0/24"]},
        "connections": [
            {
                "name": "site",
                "tunnels": [
                    {
                        "name": "tunnel-a",
                        "remote_public_ip": "203.0.113.20",
                        "inner_local_ip": "169.254.1.1",
                        "inner_remote_ip": "169.254.1.2",
                        "inner_cidr": "169.254.1.0/30",
                    }
                ],
            }
        ],
    }
    static = isinstance(fault, str) and fault.startswith("static-")
    prefix = (
        "0.0.0.0/0"
        if fault == "static-default"
        else "192.0.2.1/32"
        if fault == "static-host"
        else "10.10.0.0/16"
    )
    if static:
        cfg["connections"][0].update(routing_mode="static", remote_prefixes=[prefix])
    swan, frr = tmp_path / "swanctl.conf", tmp_path / "frr.conf"
    swan.write_text("expected config")
    swan.chmod(0o600)
    frr.write_text("router bgp 65001\n neighbor 169.254.1.2 remote-as 65002\n")
    expected = {swan: swan.read_text(), frr: frr.read_text()}
    monkeypatch.setattr(ordinary, "SWANCTL_CONF", swan)
    monkeypatch.setattr(ordinary, "FRR_CONF", frr)
    monkeypatch.setattr(ordinary, "managed_files", lambda cfg: expected)
    if fault == "files":
        swan.write_text("changed")
    observed_commands = []

    def output(args):
        observed_commands.append(args)
        if args[0] == "nft":
            from nebius_vpngw import ingress_policy

            objects = ingress_policy.objects(ingress_policy.from_config(cfg))
            return json.dumps({"nftables": objects[:1] if "tables" in args else objects})
        if args[0] == "systemctl":
            return "active"
        if args[0] == "ufw":
            return "Status: active"
        if args[0] == "sysctl":
            return "bad" if fault == "sysctl" else ordinary.REQUIRED_SYSCTLS.get(args[-1], "0")
        if "rule" in args:
            return json.dumps([{"table": 220}] if fault == "table220" else [])
        if "route" in args:
            rows = [
                {"dst": "default", "gateway": "169.254.169.1", "dev": "eth0"},
                {"dst": "169.254.1.0/30", "dev": "xfrm0", "protocol": "kernel", "scope": "link"},
                {
                    "dst": ("default" if fault == "static-default" else prefix.removesuffix("/32"))
                    if static
                    else "169.254.1.2",
                    "dev": "xfrm0",
                },
            ]
            if fault in {"static-stale", "static-foreign", "static-other-table", "bgp-learned"}:
                rows.append(
                    {
                        "dst": "10.20.0.0/16",
                        "dev": "eth0" if fault == "static-foreign" else "xfrm0",
                        "table": 123 if fault == "static-other-table" else 254,
                    }
                )
            if fault == "static-multipath":
                rows.append(
                    {"dst": "10.20.0.0/16", "nexthops": [{"dev": "xfrm0"}, {"dev": "eth0"}]}
                )
            return json.dumps(rows)
        if "link" in args:
            if args[-1] == "eth0":
                return json.dumps([{"mtu": 1500, "ifindex": 2}])
            return json.dumps(
                [
                    {
                        "mtu": 1436,
                        "link_index": 999 if fault == "parent" else 2,
                        "flags": ["UP", "NOARP"],
                        "linkinfo": {"info_kind": "xfrm", "info_data": {"if_id": "0x64"}},
                    }
                ]
            )
        if "addr" in args:
            return json.dumps([{"addr_info": [{"local": "169.254.1.1", "prefixlen": 30}]}])
        if "neigh" in args:
            return json.dumps(
                [
                    {
                        "dst": "169.254.1.2",
                        "state": ["FAILED" if fault == "neighbor" else "PERMANENT"],
                    }
                ]
            )
        if args[0] == "swanctl":
            assert "--list-conns" in args
            return (
                "list-conn event {}" if fault == "connection" else "list-conn event {tunnel-a {}}"
            )
        if args[0] == "vtysh":
            return frr.read_text().replace("65002", "999") if fault == "policy" else frr.read_text()
        if args[0] == "iptables-save":
            return (
                "ufw-before-input\n"
                if fault == "firewall"
                else "ufw-before-input\n-A ufw-user-input -i xfrm0 -j ACCEPT\n-A ufw-user-output -o xfrm0 -j ACCEPT\n"
            )
        raise AssertionError(args)

    monkeypatch.setattr(ordinary, "_output", output)
    if fault and fault not in {
        "static-valid",
        "static-foreign",
        "static-other-table",
        "static-default",
        "static-host",
        "bgp-learned",
    }:
        with pytest.raises(RuntimeError):
            ordinary.observe_local(cfg)
    else:
        assert len(ordinary.observe_local(cfg)) == 64
    assert not any("--list-sas" in args or "ping" in args for args in observed_commands)


@pytest.fixture
def guest(monkeypatch, tmp_path, ordinary_operation_guest):
    for name in ("CONFIG", "PROOF", "STATE", "BOOT"):
        monkeypatch.setattr(ordinary, name, tmp_path / name)
    ordinary.CONFIG.write_text("gateway: {}\nconnections: []\n")
    ordinary.BOOT.write_text("12345678-1234-1234-1234-123456789012")
    monkeypatch.setattr(ordinary, "package_identity", lambda: "a" * 64)
    monkeypatch.setattr(
        ordinary,
        "acquire_routing_lock",
        lambda **kwargs: os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR, 0o600),
    )
    return {
        "request_id": "f" * 32,
        "boot_id": ordinary.BOOT.read_text(),
        "config_sha256": hashlib.sha256(ordinary.CONFIG.read_bytes()).hexdigest(),
        "verify_only": False,
    }


def invoke(request):
    return ordinary.action(base64.b64encode(json.dumps(request).encode()).decode())


@pytest.mark.parametrize("change", ["boot", "hash", "ha", "malformed"])
def test_identity_or_topology_failure_precedes_any_reconciliation(
    guest, monkeypatch, capsys, change
):
    reconcile = Mock()
    monkeypatch.setattr(ordinary, "reconcile_locked", reconcile)
    if change == "boot":
        guest["boot_id"] = "00000000-0000-0000-0000-000000000000"
    elif change == "hash":
        guest["config_sha256"] = "b" * 64
    elif change == "ha":
        ordinary.CONFIG.write_text("gateway: {}\nconnections: []\nvm_ha: {}\n")
        guest["config_sha256"] = hashlib.sha256(ordinary.CONFIG.read_bytes()).hexdigest()
    else:
        guest["request_id"] = "SECRET-MALFORMED"
    assert invoke(guest) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "failed"
    assert "SECRET" not in output
    reconcile.assert_not_called()


def test_receipt_is_exact_and_contains_no_renderer_output(guest, monkeypatch, capsys):
    def reconcile(*args, **kwargs):
        print("SECRET CONFIG")
        ordinary.PROOF.write_text("verified")
        return "applied"

    monkeypatch.setattr(ordinary, "reconcile_locked", reconcile)
    assert invoke(guest) == 0
    output = capsys.readouterr().out
    receipt = json.loads(output)
    assert "SECRET" not in output
    assert receipt["status"] == "applied"
    assert {key: receipt[key] for key in ("request_id", "boot_id", "config_sha256")} == {
        key: guest[key] for key in ("request_id", "boot_id", "config_sha256")
    }
    assert receipt["package_identity"] == "a" * 64


def test_healthy_noop_observes_runtime_and_never_saves_or_mutates(guest, monkeypatch):
    raw = ordinary.CONFIG.read_bytes()
    ordinary.PROOF.write_text(json.dumps({"binding": ordinary.binding(raw), "runtime": "runtime"}))
    observe = Mock(return_value="runtime")
    monkeypatch.setattr(ordinary, "observe_local", observe)
    effect = Mock(side_effect=AssertionError("unexpected mutation"))
    monkeypatch.setattr(ordinary.firewall, "update_firewall_from_config", effect)
    monkeypatch.setattr(ordinary, "StateStore", effect)
    before = ordinary.PROOF.read_bytes()
    assert ordinary.reconcile_locked({}, raw, verify_only=False) == "unchanged"
    observe.assert_called_once()
    effect.assert_not_called()
    assert ordinary.PROOF.read_bytes() == before


def test_saved_hash_cannot_hide_runtime_drift(guest, monkeypatch):
    raw = ordinary.CONFIG.read_bytes()
    ordinary.PROOF.write_text(json.dumps({"binding": ordinary.binding(raw), "runtime": "old"}))
    monkeypatch.setattr(ordinary, "observe_local", lambda cfg: "drifted")
    with pytest.raises(RuntimeError, match="reconciliation required"):
        ordinary.reconcile_locked({}, raw, verify_only=True)
    assert not ordinary.STATE.exists()


@pytest.mark.parametrize("stage", ["firewall", "strongswan", "xfrm", "frr", "routing", "verify"])
def test_required_local_failure_never_persists_success(
    guest, monkeypatch, stage, ordinary_protection_guest
):
    calls = []

    def effect(name, result=None):
        def invoke(*args, **kwargs):
            calls.append(name)
            if name == stage:
                raise RuntimeError("private failure")
            return result

        return invoke

    monkeypatch.setattr(ordinary, "verify_unchanged", lambda *args: False)
    monkeypatch.setattr(ordinary.firewall, "update_firewall_from_config", effect("firewall"))
    monkeypatch.setattr(
        ordinary,
        "StrongSwanRenderer",
        lambda: SimpleNamespace(render_and_apply=effect("strongswan", [{"name": "xfrm0"}])),
    )
    monkeypatch.setattr(
        ordinary, "XFRMManager", lambda: SimpleNamespace(setup_interfaces=effect("xfrm"))
    )
    monkeypatch.setattr(
        ordinary, "FRRRenderer", lambda: SimpleNamespace(render_and_apply=effect("frr"))
    )
    monkeypatch.setattr(ordinary, "enforce_routing_invariants_locked", effect("routing"))
    monkeypatch.setattr(ordinary, "observe_local", effect("verify"))
    token = CURRENT.set(CommandBudget(time.monotonic() + 300))
    try:
        with pytest.raises(RuntimeError):
            ordinary.reconcile_locked({}, ordinary.CONFIG.read_bytes(), verify_only=False)
    finally:
        CURRENT.reset(token)
    assert calls[-1] == stage
    assert not ordinary.STATE.exists() and not ordinary.PROOF.exists()


def test_command_failure_survives_a_renderer_swallowing_it(monkeypatch):
    budget = CommandBudget(time.monotonic() + 10)
    token = CURRENT.set(budget)
    try:
        run([sys.executable, "-c", "raise SystemExit(1)"])
        with pytest.raises(RuntimeError, match="local reconciliation command failed"):
            budget.require_success()
    finally:
        CURRENT.reset(token)


@pytest.mark.parametrize(
    "supplied,interactive,answer,ok",
    [
        (None, False, False, False),
        ("stale", False, False, False),
        (None, True, False, False),
        (None, True, True, True),
        ("a" * 64, False, False, True),
    ],
)
def test_disruption_is_default_no_and_exact(monkeypatch, supplied, interactive, answer, ok):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: interactive)
    confirm = Mock(return_value=answer)
    monkeypatch.setattr(cli.typer, "confirm", confirm)
    if ok:
        assert cli._approve_disruption("a" * 64, supplied=supplied, dry_run=False)
    else:
        with pytest.raises(RuntimeError):
            cli._approve_disruption("a" * 64, supplied=supplied, dry_run=False)
    if interactive and supplied is None:
        assert confirm.call_args.kwargs["default"] is False


def test_dry_run_never_prompts(monkeypatch):
    monkeypatch.setattr(cli.typer, "confirm", Mock(side_effect=AssertionError("prompt")))
    assert not cli._approve_disruption("a" * 64, supplied=None, dry_run=True)


def test_compatible_transitive_dependencies_are_retained():
    observed = {
        "markers": {},
        "dependencies": {"example": "1.2", "child": "2.5"},
        "requirements": {"example": ["child>=2,<3"]},
    }
    assert ordinary_apply.dependencies_satisfied(["example>=1,<2"], observed)
    observed["dependencies"]["child"] = "1.0"
    assert not ordinary_apply.dependencies_satisfied(["example>=1,<2"], observed)


def test_remote_stale_predecessor_has_no_command_or_config_effects(monkeypatch, tmp_path):
    monkeypatch.setattr(ordinary_remote, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(ordinary_remote, "inspect", lambda manifest: {"changed": True})
    command = Mock(side_effect=AssertionError("effect"))
    monkeypatch.setattr(ordinary_remote, "command", command)
    result = ordinary_remote.execute(
        {
            "manifest": {"ingress_policy": {"peer_ips": []}},
            "noop": False,
            "approval": "approved",
            "plan_digest": "approved",
            "predecessor": "stale",
        }
    )
    assert result["status"] == "failed" and result["stage"] == "predecessor"
    assert result["error_code"] == "stale_plan"
    command.assert_not_called()


@pytest.mark.parametrize("failure", ["runtime_drift", "final_artifact", "private-fixture-output"])
def test_remote_failure_diagnostics_are_closed_and_metadata_only(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(ordinary_remote, "LOCK", tmp_path / "lock")
    monkeypatch.setattr(ordinary_remote, "inspect", Mock(side_effect=RuntimeError(failure)))
    result = ordinary_remote.execute({"manifest": {}, "noop": True})
    assert result == {
        "status": "failed",
        "stage": "predecessor",
        "error_code": failure
        if failure != "private-fixture-output"
        else "ordinary_transaction_failed",
    }
    assert "private-fixture-output" not in json.dumps(result)


@pytest.mark.parametrize("status,output", [(1, "{}"), (0, "{}\n{}"), (0, "{"), (0, "[]")])
def test_agent_receipt_rejects_bad_exit_and_ambiguous_json(monkeypatch, status, output):
    monkeypatch.setattr(ordinary_remote, "command", lambda *args, **kwargs: (status, output))
    with pytest.raises((RuntimeError, ValueError)):
        ordinary_remote.agent_receipt("a" * 64, "boot", verify_only=False)


def test_remote_timeout_kills_grandchild_before_return(tmp_path, monkeypatch):
    marker = tmp_path / "late"
    child = "import time,pathlib;time.sleep(0.5);pathlib.Path(" + repr(str(marker)) + ").touch()"
    parent = (
        "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"
        + repr(child)
        + "]);time.sleep(10)"
    )
    monkeypatch.setattr(ordinary_remote, "DEADLINE", time.monotonic() + 0.1)
    with pytest.raises(subprocess.TimeoutExpired):
        ordinary_remote.command([sys.executable, "-c", parent])
    time.sleep(0.6)
    assert not marker.exists()


@pytest.mark.parametrize("mode", ["bgp", "static"])
def test_real_060_nonempty_state_and_tunnel_identity_remain_compatible(tmp_path, mode):
    import yaml

    from nebius_vpngw.config_loader import load_local_config, merge_with_peer_configs
    from nebius_vpngw.tunnel_state import collect_tunnel_state

    fixture = json.loads(
        (Path(__file__).parents[1] / "fixtures/previous_release_0_6_0_ordinary.json").read_text()
    )["cases"][mode]
    old_state = fixture["state"]
    assert old_state["package_version"] == "0.6.0"
    assert old_state["resolved_config"]["connections"]
    path = tmp_path / "last-applied.json"
    path.write_text(json.dumps(old_state))
    before = path.read_bytes()
    store = ordinary.StateStore(path)
    assert store.load_last_applied() == old_state
    assert not store.is_changed(old_state["resolved_config"])
    assert path.read_bytes() == before
    config = tmp_path / "ordinary.yaml"
    migrated = copy.deepcopy(fixture["local_config"])
    config.write_text(yaml.safe_dump(migrated))
    plan = merge_with_peer_configs(load_local_config(config), [])
    plan.validate()
    assert len(plan.per_instance) == 1
    resolved = yaml.safe_load(plan.per_instance[0].config_yaml)
    assert resolved.get("vm_ha") is None
    current = collect_tunnel_state(resolved, log=lambda _: None)[2]
    assert [
        {k: v for k, v in endpoint.items() if k != "remote_prefixes"} for endpoint in current
    ] == [
        {k: v for k, v in endpoint.items() if k != "remote_prefixes"}
        for endpoint in fixture["interfaces"]
    ]
    # Historical identity is stable; the accepted Static selection change puts
    # workload routes on the preferred path and leaves the alternative responding.
    if mode == "static":
        assert current[0]["remote_prefixes"] == fixture["interfaces"][0]["remote_prefixes"]
        assert current[1]["remote_prefixes"] == []
    else:
        assert current == fixture["interfaces"]


def test_replacement_preparation_never_starts_dataplane_or_commits_routes(
    transaction, tmp_path, monkeypatch
):
    tx = transaction
    guard = tmp_path / "replacement-guard.json"
    operation = "b" * 64
    guard.write_text(json.dumps({"operation": operation}))
    forwarding = tmp_path / "forwarding"
    forwarding.write_text("0\n")
    actual_path = Path

    def isolated_path(value):
        if str(value) == "/etc/nebius-vpngw/replacement-guard.json":
            return guard
        if str(value) in {
            "/proc/sys/net/ipv4/ip_forward",
            "/proc/sys/net/ipv6/conf/all/forwarding",
        }:
            return forwarding
        return actual_path(value)

    monkeypatch.setattr(ordinary_remote, "Path", isolated_path)
    monkeypatch.setattr(
        ordinary_remote,
        "inspect",
        lambda manifest, **kw: tx.inspect(
            manifest, **{key: value for key, value in kw.items() if key != "runtime_admission"}
        ),
    )
    route_prepare = Mock(side_effect=AssertionError("passive preparation must not prepare routes"))
    monkeypatch.setattr(ordinary_routes.RouteStore, "prepare", route_prepare)
    tx.request["prepare_replacement"] = operation
    result = ordinary_remote.execute(tx.request)
    assert result["status"] == "prepared", result
    assert result["receipt"]["operation"] == operation
    assert ["reconcile"] not in tx.effects
    assert not any(effect[:2] == ["systemctl", "start"] for effect in tx.effects)
    route_prepare.assert_not_called()
    assert json.loads(guard.read_text()) == {"operation": operation}


@pytest.mark.parametrize("fault", [None, "unapproved", "wheel", "receipt", "unchanged"])
def test_ordinary_durable_staging_requires_exact_approval_and_receipt(tmp_path, monkeypatch, fault):
    wheel = tmp_path / "nebius_vpngw-1-py3-none-any.whl"
    wheel.write_bytes(b"approved wheel")
    dependency = tmp_path / "vici-6.0.3-py3-none-any.whl"
    dependency.write_bytes(b"approved dependency")
    manifest = {
        "wheel_sha256": ordinary_apply.sha(wheel.read_bytes()),
        "runtime_plan": {"archives": {"exact.deb": {"sha256": "a" * 64}}},
        "dependency_wheels": {dependency.name: ordinary_apply.sha(dependency.read_bytes())},
    }
    plan = SimpleNamespace(
        noop=fault == "unchanged",
        wheel=wheel,
        manifest=manifest,
        dependency_paths=[dependency],
        target="192.0.2.1",
        instance=object(),
    )
    expected = {
        "manifest": ordinary_apply.digest(manifest),
        "runtime_plan": ordinary_apply.digest(manifest["runtime_plan"]),
    }
    remote = Mock(return_value={"status": "staged", "receipt": expected})
    monkeypatch.setattr(ordinary_apply, "remote", remote)
    if fault == "wheel":
        wheel.write_bytes(b"changed artifact")
    if fault == "receipt":
        remote.return_value = {"status": "staged", "receipt": {**expected, "manifest": "b" * 64}}
    if fault in {"unapproved", "wheel", "receipt"}:
        with pytest.raises(RuntimeError):
            ordinary_apply.stage_plan(object(), plan, {}, approved=fault != "unapproved")
    else:
        assert ordinary_apply.stage_plan(object(), plan, {}, approved=True) == (
            None if fault == "unchanged" else expected
        )
    if fault in {"unapproved", "wheel", "unchanged"}:
        remote.assert_not_called()
    else:
        payload = remote.call_args.args[4]
        assert payload["action"] == "stage-package"
        assert base64.b64decode(payload["wheel"]) == b"approved wheel"
        assert (
            base64.b64decode(payload["dependency_wheels"][dependency.name])
            == b"approved dependency"
        )
        assert "config" not in payload  # This phase cannot publish guest configuration.
