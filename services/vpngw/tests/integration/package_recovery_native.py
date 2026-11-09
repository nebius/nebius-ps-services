"""Run explicitly in disposable Ubuntu userspace, never on the operator host.

Synthetic wheels exercise the real checkpoint owner with the system pip/dpkg.
This is package-boundary evidence, not a product release or HA runtime trial.
"""

import base64
import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from nebius_vpngw import python_package_recovery as owner


def wheel(root, version):
    name = "vpngw_recovery_fixture"
    info = f"{name}-{version}.dist-info"
    files = {
        f"{name}/__init__.py": f"VERSION = {version!r}\n".encode(),
        f"{name}/{'obsolete' if version == '1.0' else 'added'}.py": b"VALUE = 1\n",
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = io.StringIO()
    writer = csv.writer(record)
    for path, raw in files.items():
        writer.writerow(
            [
                path,
                "sha256="
                + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).decode().rstrip("="),
                len(raw),
            ]
        )
    writer.writerow([f"{info}/RECORD", "", ""])
    files[f"{info}/RECORD"] = record.getvalue().encode()
    target = root / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(target, "w") as archive:
        for path, raw in files.items():
            archive.writestr(path, raw)
    return target


def command(args, *, limit=120, required=True, capture_stderr=False):
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=limit,
        env={**os.environ, "LC_ALL": "C", "PIP_CONFIG_FILE": "/dev/null", "PYTHONNOUSERSITE": "1"},
    )
    if required and result.returncode:
        raise RuntimeError(result.stderr[-2000:])
    return result.returncode, result.stdout + (result.stderr if capture_stderr else "")


def native_configure(root):
    """Actual dpkg fault with synthetic closure; service admission is a fixture."""
    from nebius_vpngw import runtime_migration as migration

    name = "vpngw-recovery-native-fixture"
    package = root / "deb"
    (package / "DEBIAN").mkdir(parents=True)
    (package / "DEBIAN/control").write_text(
        f"Package: {name}\nVersion: 1.0\nArchitecture: all\n"
        "Maintainer: Package test <test@example.invalid>\nDescription: Disposable recovery fixture\n"
    )
    marker = root / "configure-admitted"
    script = package / "DEBIAN/postinst"
    script.write_text(f"#!/bin/sh\ntest -f '{marker}'\n")
    script.chmod(0o755)
    archive = root / (name + ".deb")
    command(["dpkg-deb", "--build", str(package), str(archive)])
    approved = {
        "before": {},
        "desired": {name: ["1.0", "installed"]},
        "remove": [],
        "install": {name: "1.0"},
        "archives": {archive.name: {"package": name, "version": "1.0"}},
    }
    migration.ROOT = root / "runtime"
    migration.STATE = migration.ROOT / "operation.json"
    migration.ROOT.mkdir()
    (migration.ROOT / "inhibited").touch()
    migration.save(
        dict(
            schema=migration.SCHEMA,
            operation="a" * 32,
            phase="inhibited",
            plan=approved,
            plan_digest=migration.digest(approved),
            manifest={},
        )
    )
    # Scope is dpkg phase selection/continuation, not systemd or artifact staging.
    migration.validate_plan = lambda _p: None
    migration.verify_staged = lambda _p: root
    migration.verify_inhibited = lambda: None
    migration.preserve_unit_policy = lambda _p: None
    migration.CHECK = lambda: None
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args == ["systemctl", "daemon-reload"]:
            return 0, ""
        return command(args, **kwargs)

    migration.COMMAND = run
    try:
        migration.install()
    except RuntimeError:
        pass
    else:
        raise AssertionError("native configure fault did not occur")
    assert migration.packages()[name] == ["1.0", "half-configured"]
    assert migration.state()["package_step"] == "configure"
    marker.touch()
    calls.clear()
    migration.install()
    assert migration.packages()[name] == ["1.0", "installed"]
    assert migration.state()["phase"] == "installed"
    assert not any(args[:2] == ["dpkg", "--unpack"] for args in calls)
    assert ["dpkg", "--configure", "--force-confold", name] in calls
    print("PASS: native dpkg half-configured continuation without repeated unpack")


def operation_provenance(root, *, authority_field="ha_parent", version="3.0"):
    """A new admission owns fresh before-images; the original remains immutable."""
    a, b = root / "A", root / "B"
    a.mkdir()
    b.mkdir()
    wa = wheel(a, version)
    wb = b / wa.name
    wb.write_bytes(wa.read_bytes())
    digest = hashlib.sha256(wa.read_bytes()).hexdigest()
    assert digest == hashlib.sha256(wb.read_bytes()).hexdigest()

    def manifest(admission):
        authority = admission if authority_field == "ha_parent" else {"digest": admission}
        return {
            "ordinary_assets": {},
            "dependency_wheels": {},
            "wheel_name": wa.name,
            "wheel_sha256": digest,
            authority_field: authority,
        }

    ma, mb, mc = (manifest(name) for name in ("admission-a", "admission-b", "admission-c"))
    owner.prepare_payload(ma, a, command)
    owner.install_payload(ma, a, command, lambda: None)
    ca = owner.checkpoint_root(ma, a) / digest
    va = owner.load(ca, wa)
    saved_a = (ca / "checkpoint.json").read_bytes()
    assert va["state"] == "complete" and owner.complete(ca, va)

    owner.prepare_payload(mb, b, command)
    owner.install_payload(mb, b, command, lambda: None)
    cb = owner.checkpoint_root(mb, b) / digest
    saved_b = (cb / "checkpoint.json").read_bytes()
    differences = [path for path, item in va["target"].items() if owner.content(Path(path)) != item]
    assert len(differences) == 2
    assert {Path(path).name for path in differences} == {"direct_url.json", "RECORD"}
    direct = Path(next(path for path in differences if path.endswith("/direct_url.json")))
    current = json.loads(direct.read_text())
    assert current["url"] == wb.as_uri()
    assert current["archive_info"]["hashes"]["sha256"] == digest
    predecessor = owner.content(direct)
    calls = []

    def reject_replay(*args, **_kwargs):
        calls.append(args)
        raise AssertionError("completed admission repeated pip")

    try:
        owner.install_payload(ma, a, reject_replay, lambda: None)
    except RuntimeError as error:
        assert str(error) == "Completed Python package changed"
    else:
        raise AssertionError("changed prior admission was accepted")
    assert not calls and (ca / "checkpoint.json").read_bytes() == saved_a

    # Reuse the exact A staging path, but with a separately admitted operation.
    owner.prepare_payload(mc, a, command)
    cc = owner.checkpoint_root(mc, a) / digest
    assert cc != ca, "distinct admissions reused a Python checkpoint"
    prepared = owner.load(cc, wa)
    assert prepared["before"][str(direct)] == predecessor
    owner.install_payload(mc, a, command, lambda: None)
    complete = owner.load(cc, wa)
    assert complete["state"] == "complete" and owner.complete(cc, complete)
    assert json.loads(direct.read_text())["url"] == wa.as_uri()
    saved_c = (cc / "checkpoint.json").read_bytes()
    owner.prepare_payload(mc, a, reject_replay)
    owner.install_payload(mc, a, reject_replay, lambda: None)
    assert not calls and (cc / "checkpoint.json").read_bytes() == saved_c
    assert (ca / "checkpoint.json").read_bytes() == saved_a
    assert (cb / "checkpoint.json").read_bytes() == saved_b
    return {"authority_field": authority_field, "artifact": digest, "pip_replay_calls": 0}


def main():
    assert os.environ.get("VPNGW_DISPOSABLE_PACKAGE_TEST") == "1"
    assert Path("/etc/os-release").read_text().find('VERSION_ID="24.04"') >= 0
    with tempfile.TemporaryDirectory(prefix="package-native-", dir="/var/lib") as temporary:
        root = Path(temporary)
        native_configure(root)
        old, new = wheel(root, "1.0"), wheel(root, "2.0")
        command(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-compile",
                "--no-deps",
                "--no-index",
                "--break-system-packages",
                str(old),
            ]
        )
        # A normal service import creates bytecode absent from --no-compile RECORD.
        subprocess.check_call([sys.executable, "-c", "import vpngw_recovery_fixture"])
        checkpoint = root / "checkpoint"
        owner.prepare(checkpoint, new, command)
        admitted = owner.load(checkpoint, new)
        assert admitted["before"] and admitted["target"]
        assert any("__pycache__" in path for path in admitted["before"])
        # Real service imports may refresh only a timestamp cache header after
        # staging, while source bytes and the compiled code remain identical.
        source = Path(
            importlib.metadata.distribution("vpngw-recovery-fixture").locate_file(
                "vpngw_recovery_fixture/__init__.py"
            )
        )
        cache = Path(importlib.util.cache_from_source(str(source)))
        original_cache = cache.read_bytes()
        stamp = int(source.stat().st_mtime) + 2
        os.utime(source, (stamp, stamp))
        environment = dict(os.environ)
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        subprocess.check_call(
            [sys.executable, "-c", "import vpngw_recovery_fixture"], env=environment
        )
        refreshed_cache = cache.read_bytes()
        assert original_cache[8:12] != refreshed_cache[8:12]
        assert original_cache[:8] == refreshed_cache[:8]
        assert original_cache[12:] == refreshed_cache[12:]
        assert not owner.matches(admitted["before"])
        invoked = []

        def interrupted(args, **kwargs):
            # An exact fault boundary after the owner's obsolete-file removal
            # and before pip starts. Recovery itself must restore and continue.
            invoked.append(args)
            raise OSError("injected transport/owner loss before pip")

        try:
            owner.install(checkpoint, new, interrupted, lambda: None)
        except OSError:
            pass
        else:
            raise AssertionError("fault boundary not reached")
        assert len(invoked) == 1
        assert owner.load(checkpoint, new)["state"] == "installing"
        owner.install(checkpoint, new, command, lambda: None)
        completed = owner.load(checkpoint, new)
        assert completed["state"] == "complete" and owner.complete(checkpoint, completed)
        print("PASS: native regenerated timestamp cache preserves exact package recovery")
        probe = subprocess.check_output(
            [sys.executable, "-B", "-c", "import vpngw_recovery_fixture as x; print(x.VERSION)"],
            text=True,
        ).strip()
        assert probe == "2.0"
        assert not any(
            Path(path).exists() for path in admitted["before"].keys() - admitted["target"].keys()
        )
        owner.install(
            checkpoint,
            new,
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("completed phase replayed")),
            lambda: None,
        )
        print(
            "PASS: native pip checkpoint, exact interrupted continuation, obsolete-file removal, and no-op readback"
        )
        for index, authority in enumerate(("ha_parent", "command_approval"), start=3):
            trial = root / authority
            trial.mkdir()
            operation_provenance(trial, authority_field=authority, version=f"{index}.0")
            print(f"PASS: native pip operation provenance and immutable resume ({authority})")


if __name__ == "__main__":
    main()
