"""Full reconstruction admits damaged predecessors without weakening upgrades."""

import hashlib
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw import python_package_recovery as packages
from nebius_vpngw import repair_runtime
from nebius_vpngw import runtime_migration as migration


@pytest.fixture
def broken_python(monkeypatch, tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    wheel = tmp_path / "nebius_vpngw-1.0-py3-none-any.whl"
    target = {
        "nebius_vpngw/__init__.py": b"answer = 42\n",
        "nebius_vpngw-1.0.dist-info/METADATA": b"Name: nebius-vpngw\nVersion: 1.0\n",
        "nebius_vpngw-1.0.dist-info/RECORD": b"fixture target inventory\n",
    }
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in target.items():
            archive.writestr(name, raw)
    (site / "nebius_vpngw").mkdir()
    (site / "nebius_vpngw/__init__.py").write_bytes(b"broken import !!!\n")
    (site / "nebius_vpngw/obsolete.py").write_bytes(b"old product module\n")
    (site / "nebius_vpngw-1.0.dist-info").mkdir()
    (site / "nebius_vpngw-1.0.dist-info/METADATA").write_bytes(
        target["nebius_vpngw-1.0.dist-info/METADATA"]
    )
    (site / "unrelated.txt").write_bytes(b"preserve\n")
    installed = SimpleNamespace(
        metadata={"Name": "nebius-vpngw"},
        files=None,
        entry_points=[],
        read_text=lambda _: None,
        locate_file=lambda name: site / name,
    )
    monkeypatch.setattr(packages.metadata, "distribution", lambda _: installed)
    monkeypatch.setattr(packages.metadata, "distributions", lambda: [installed])
    monkeypatch.setattr(packages, "schemes", lambda: (site,))
    monkeypatch.setattr(packages.sysconfig, "get_path", lambda _: str(site))
    calls = []

    def command(args, **_kwargs):
        calls.append(args)
        if args[0] == "dpkg-query":
            return 1, "\n".join(
                "dpkg-query: no path found matching pattern " + path for path in args[2:]
            )
        assert args[:4] == [sys.executable, "-B", "-m", "pip"]
        assert "--no-index" in args and "--no-deps" in args
        destination = site
        if "--root" in args:
            projection = Path(args[args.index("--root") + 1])
            destination = projection / site.relative_to("/")
        for name, raw in target.items():
            packages.atomic(destination / name, raw, 0o644)
        return 0, ""

    return site, wheel, tmp_path / "repair", command, calls, target


def test_repair_reconstructs_missing_record_and_corrupt_same_version(broken_python):
    site, wheel, directory, command, calls, target = broken_python
    packages.prepare(directory, wheel, command, reconstruct=True)
    value = packages.load(directory, wheel)
    assert str(site / "nebius_vpngw/obsolete.py") in value["before"]
    assert str(site / "nebius_vpngw-1.0.dist-info/RECORD") not in value["before"]
    packages.install(directory, wheel, command, lambda: None)
    for name, raw in target.items():
        assert (site / name).read_bytes() == raw
    assert not (site / "nebius_vpngw/obsolete.py").exists()
    assert (site / "unrelated.txt").read_bytes() == b"preserve\n"
    count = len(calls)
    packages.install(directory, wheel, command, lambda: None)
    assert len(calls) == count


def test_normal_upgrade_still_refuses_missing_predecessor_record(broken_python):
    site, wheel, directory, command, calls, _ = broken_python
    with pytest.raises(RuntimeError, match="inventory is missing"):
        packages.prepare(directory, wheel, command)
    assert calls == []
    assert (site / "nebius_vpngw/__init__.py").read_bytes() == b"broken import !!!\n"


def test_repair_reconstructs_malformed_predecessor_metadata(broken_python, monkeypatch):
    site, wheel, directory, command, _calls, _target = broken_python

    class Malformed:
        metadata = {"Name": "nebius-vpngw"}

        def locate_file(self, name):
            return site / name

        @property
        def files(self):
            raise ValueError("malformed RECORD")

        @property
        def entry_points(self):
            raise ValueError("malformed entry_points.txt")

    malformed = Malformed()
    monkeypatch.setattr(packages.metadata, "distribution", lambda _: malformed)
    monkeypatch.setattr(packages.metadata, "distributions", lambda: [malformed])
    packages.prepare(directory, wheel, command, reconstruct=True)
    packages.install(directory, wheel, command, lambda: None)
    assert (site / "nebius_vpngw/__init__.py").read_bytes() == b"answer = 42\n"


def test_repair_preserves_os_package_ownership(broken_python):
    site, wheel, directory, command, _calls, _ = broken_python

    def owned(args, **kwargs):
        if args[0] == "dpkg-query":
            return 0, "foreign-package: " + args[-1]
        return command(args, **kwargs)

    with pytest.raises(RuntimeError, match="owned by the OS"):
        packages.prepare(directory, wheel, owned, reconstruct=True)
    assert (site / "nebius_vpngw/__init__.py").read_bytes() == b"broken import !!!\n"


def test_interrupted_repair_reuses_frozen_before_images(broken_python):
    site, wheel, directory, command, _calls, target = broken_python
    packages.prepare(directory, wheel, command, reconstruct=True)
    original = (directory / "checkpoint.json").read_bytes()

    def interrupted(args, **kwargs):
        command(args, **kwargs)
        raise OSError("lost package reply")

    with pytest.raises(OSError, match="lost package reply"):
        packages.install(directory, wheel, interrupted, lambda: None)
    packages.prepare(directory, wheel, command, reconstruct=True)
    resumed = packages.load(directory, wheel)
    assert resumed["before"] == json.loads(original)["before"]
    packages.install(directory, wheel, command, lambda: None)
    assert packages.load(directory, wheel)["state"] == "complete"
    for name, raw in target.items():
        assert hashlib.sha256((site / name).read_bytes()).digest() == hashlib.sha256(raw).digest()


def test_repair_does_not_claim_shared_namespace(broken_python, monkeypatch):
    site, wheel, directory, command, _calls, _ = broken_python
    foreign = SimpleNamespace(
        metadata={"Name": "foreign-plugin"},
        files=[Path("nebius_vpngw/obsolete.py")],
        locate_file=lambda name: site / name,
    )
    monkeypatch.setattr(packages.metadata, "distributions", lambda: [foreign])
    with pytest.raises(RuntimeError, match="shared ownership"):
        packages.prepare(directory, wheel, command, reconstruct=True)
    assert (site / "nebius_vpngw/obsolete.py").exists()


def test_native_inventory_detects_corruption_despite_unchanged_package_database(tmp_path):
    binary = tmp_path / "usr/sbin/charon-systemd"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"approved binary")
    binary.chmod(0o755)
    inventory = {
        "files": {"/usr/sbin/charon-systemd": repair_runtime.file_identity(binary)},
        "conffiles": [],
    }
    repair_runtime.verify_inventory(inventory, root=tmp_path)
    binary.write_bytes(b"corrupt binary")
    with pytest.raises(RuntimeError, match="differs from its archive"):
        repair_runtime.verify_inventory(inventory, root=tmp_path)


def test_native_inventory_does_not_exempt_arbitrary_modified_files(tmp_path):
    inventory = {"files": {}, "conffiles": ["/etc/frr/frr.conf"]}
    with pytest.raises(RuntimeError, match="Only declared"):
        repair_runtime.verify_inventory(inventory, root=tmp_path, rendered={"/usr/sbin/bgpd"})


def test_frr_postinstall_permission_hardening_preserves_exact_content_proof(tmp_path):
    path = tmp_path / "etc/frr/frr.conf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"native defaults")
    path.chmod(0o644)
    inventory = {
        "files": {"/etc/frr/frr.conf": repair_runtime.file_identity(path)},
        "conffiles": ["/etc/frr/frr.conf"],
        "postinstall_modes": {"/etc/frr/frr.conf": [0o640, 0o644]},
    }
    path.chmod(0o640)
    repair_runtime.verify_inventory(inventory, root=tmp_path)
    path.chmod(0o666)
    with pytest.raises(RuntimeError, match="differs"):
        repair_runtime.verify_inventory(inventory, root=tmp_path)
    path.chmod(0o640)
    path.write_bytes(b"broken configuration")
    with pytest.raises(RuntimeError, match="differs"):
        repair_runtime.verify_inventory(inventory, root=tmp_path)


def test_native_repair_unpacks_same_version_and_restores_conffiles(monkeypatch, tmp_path):
    selected = migration.REPAIR_REQUIRED
    approved = {
        "install": dict.fromkeys(selected, "1.0"),
        "desired": {name: ["1.0", "installed"] for name in selected},
        "remove": [],
        "archives": {},
    }
    for name in selected:
        raw = ("fixture archive " + name).encode()
        (tmp_path / (name + ".deb")).write_bytes(raw)
        approved["archives"][name + ".deb"] = {
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
    monkeypatch.setattr(migration, "validate_plan", lambda _: None)
    monkeypatch.setattr(migration, "packages", lambda: approved["desired"])
    monkeypatch.setattr(repair_runtime, "verify_inventory", lambda _: None)
    calls, checks = [], []

    def command(args, **_kwargs):
        calls.append(args)
        return 0, "\n".join(f"Inst {name} (1.0 fixture)" for name in selected)

    inventory = {"archives": repair_runtime.storage.digest(approved["archives"])}
    repair_runtime.install(
        approved,
        tmp_path,
        inventory,
        command=command,
        check=lambda: checks.append(True),
    )
    assert [call[1] for call in calls] == ["--simulate", "--assume-yes"]
    assert all(
        {"Dpkg::Options::=" + value for value in repair_runtime.CONF_OPTIONS}.issubset(call)
        for call in calls
    )
    assert all(
        "--no-download" in call and "--no-remove" in call and "--reinstall" in call
        for call in calls
    )
    assert str(tmp_path / "frr.deb") in calls[0]
    assert len(checks) == 3
    (tmp_path / "frr.deb").write_bytes(b"changed archive")
    calls.clear()
    with pytest.raises(RuntimeError, match="archive identity changed"):
        repair_runtime.install(
            approved,
            tmp_path,
            inventory,
            command=lambda args, **_: calls.append(args),
            check=lambda: checks.append(True),
        )
    assert calls == []
