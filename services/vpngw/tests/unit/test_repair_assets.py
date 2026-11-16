"""Full repair must rebuild deployed assets before admitting activation."""

import contextlib
import hashlib
import importlib.util
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_vpngw import repair_state
from nebius_vpngw.deploy import ordinary_apply, repair_remote
from nebius_vpngw.deploy.ssh_push import _VM_HA_SERVICE_ASSET_DESTINATIONS


@pytest.fixture(params=[True, False], ids=["ha", "ordinary"])
def reconstruction(request, monkeypatch, tmp_path):
    ha = request.param
    catalog = _VM_HA_SERVICE_ASSET_DESTINATIONS if ha else ordinary_apply.ASSETS
    entries = [
        (f"nebius_vpngw/systemd/{name}", str(tmp_path / "host" / path.lstrip("/")), mode)
        for name, path, mode in catalog
    ]
    if not ha:
        entries.append(("nebius_vpngw/ingress_policy.py", str(tmp_path / "host/ingress.py"), 0o644))
    source = Path(repair_remote.__file__).parents[2]
    wheel = tmp_path / "product.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("nebius_vpngw-1.dist-info/METADATA", "Name: nebius-vpngw\nVersion: 1\n")
        for member in dict.fromkeys(item[0] for item in entries):
            archive.writestr(member, (source / member).read_bytes())
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    state = {"id": "a" * 32, "phase": "reset", "intent": {"ha": ha}, "effects": {}}
    effects = []

    def effect(name, *_args, **kwargs):
        effects.append((name, kwargs.get("state", "started")))

    def advance(phase):
        # Installed must mean the canonical service and executable bytes exist.
        with zipfile.ZipFile(wheel) as archive:
            for member, path, mode in entries:
                assert Path(path).read_bytes() == archive.read(member)
                assert stat.S_IMODE(Path(path).stat().st_mode) == mode
        state["phase"] = phase

    journal = SimpleNamespace(load=lambda: state, advance=advance, effect=effect)
    payload = {
        "package": {
            "native": {"archives": {}},
            "wheels": {wheel.name: checksum},
            "product": wheel.name,
        }
    }
    monkeypatch.setattr(repair_remote, "admitted", lambda *_a, **_k: (journal, state, tmp_path))
    monkeypatch.setattr(
        repair_remote, "assets", SimpleNamespace(entries=lambda **_kw: entries), raising=False
    )
    monkeypatch.setattr(repair_remote, "native_exclusion", contextlib.nullcontext)
    monkeypatch.setattr(repair_remote.guest, "lock", contextlib.nullcontext)
    monkeypatch.setattr(repair_remote.guest, "verify_guard", lambda _operation: None)
    monkeypatch.setattr(repair_remote.guest, "verify_quiescent", lambda: None)
    monkeypatch.setattr(repair_remote.guest, "POLICY", tmp_path / "policy")
    repair_remote.guest.POLICY.write_bytes(repair_remote.guest.POLICY_BYTES)
    monkeypatch.setattr(repair_remote.guest, "BOOT", tmp_path / "boot")
    repair_remote.guest.BOOT.write_text("boot-a")
    commands = []
    monkeypatch.setattr(repair_remote.guest, "command", lambda argv, **_kw: commands.append(argv))
    inventory = {"archives": repair_state.digest({}), "conffiles": []}
    monkeypatch.setattr(repair_remote.runtime, "archive_inventory", lambda *_a: inventory)
    monkeypatch.setattr(repair_remote.runtime, "install", lambda *_a, **_kw: None)
    monkeypatch.setattr(repair_remote.runtime, "verify_inventory", lambda *_a, **_kw: None)
    monkeypatch.setattr(repair_remote.runtime.migration, "verify_packages", lambda *_a: None)
    monkeypatch.setattr(repair_remote.python, "prepare", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        repair_remote.python, "load", lambda *_a: {"before": {}, "target": {}, "state": "complete"}
    )
    monkeypatch.setattr(repair_remote.python, "install", lambda *_a, **_kw: None)
    monkeypatch.setattr(repair_remote.python, "complete", lambda *_a: True)
    monkeypatch.setattr(repair_remote, "require_shared_dependencies", lambda _desired: None)
    return payload, state, entries, commands, effects


REAL_SHARED_CHECK = repair_remote.require_shared_dependencies


@pytest.fixture
def isolated_dependencies(monkeypatch, tmp_path):
    """Substitute only the distro package directory on this non-Debian test host."""
    spec = importlib.util.find_spec("pip")
    assert spec and spec.origin
    distro = str(Path(spec.origin).parent.parent)
    metadata = tmp_path / "consumer-metadata"
    metadata.mkdir()
    poison = tmp_path / "poison"
    poison.mkdir()
    for name in ("pip", "packaging"):
        (poison / (name + ".py")).write_text("raise RuntimeError('ambient package imported')")
    monkeypatch.setenv("PYTHONPATH", str(poison))
    monkeypatch.setattr(repair_remote.sys, "path", [str(poison), str(metadata)])

    def command(args, **_kwargs):
        assert args[:5] == [sys.executable, "-I", "-S", "-B", "-c"]
        script = args[5].replace("'/usr/lib/python3/dist-packages'", repr(distro))
        result = subprocess.run(
            [*args[:5], script, *args[6:]], capture_output=True, text=True, check=False
        )
        return result.returncode, result.stdout

    monkeypatch.setattr(repair_remote.guest, "command", command)

    def distribution(name, version, requirements=(), suffix=""):
        folder = metadata / f"{name}-{version}{suffix}.dist-info"
        folder.mkdir(exist_ok=True)
        (folder / "METADATA").write_text(
            f"Name: {name}\nVersion: {version}\n"
            + "".join(f"Requires-Dist: {text}\n" for text in requirements)
        )

    distribution("shared", "1")
    return distribution


def test_isolated_shared_check_preserves_all_consumers(isolated_dependencies, monkeypatch):
    distribution = isolated_dependencies
    distribution("consumer", "1", ["shared>=2", "not a valid unrelated requirement ???"])
    REAL_SHARED_CHECK({"shared": "2"})
    distribution("consumer", "1", ["shared<2"], suffix="-other-prefix")
    # The streamed bootstrap has no source files; its retained trusted bytes work too.
    scope = repair_remote.python.dependency_scope
    monkeypatch.setattr(scope, "_SOURCE", Path(scope.__file__).read_bytes(), raising=False)
    monkeypatch.setattr(scope, "__file__", "/nonexistent/dependency_scope.py")
    with pytest.raises(scope.DependencyFailure) as failure:
        REAL_SHARED_CHECK({"shared": "2"})
    assert failure.value.code == "dependency-shared-conflict"


@pytest.mark.parametrize("output", ["not-json", '{}', '{"error_code": []}', '{"status":"unknown"}'])
def test_shared_check_rejects_unverified_result(monkeypatch, output):
    monkeypatch.setattr(repair_remote.guest, "command", lambda *_a, **_k: (0, output))
    with pytest.raises(repair_remote.python.dependency_scope.DependencyFailure) as failure:
        REAL_SHARED_CHECK({"shared": "2"})
    assert failure.value.code == "dependency-resolver-unavailable"


@pytest.mark.parametrize("phase", ["reset", "installed"])
def test_repair_rechecks_shared_consumers_before_install(
    reconstruction, isolated_dependencies, monkeypatch, tmp_path, phase
):
    payload, state, _entries, _commands, _effects = reconstruction
    state["phase"] = phase
    wheel = tmp_path / "shared.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("shared-2.dist-info/METADATA", "Name: shared\nVersion: 2\n")
    payload["package"]["wheels"][wheel.name] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    isolated_dependencies("new-consumer", "1", ["shared<2"])
    monkeypatch.setattr(repair_remote, "require_shared_dependencies", REAL_SHARED_CHECK)
    installed = []
    monkeypatch.setattr(repair_remote.python, "install", lambda *args: installed.append(args[1]))
    with pytest.raises(repair_remote.python.dependency_scope.DependencyFailure):
        repair_remote.execute(payload)
    assert not installed


def test_repair_rechecks_between_wheel_mutations(reconstruction, monkeypatch):
    payload, _state, _entries, _commands, _effects = reconstruction
    installed = []

    def check(_desired):
        if installed:
            raise repair_remote.python.dependency_scope.DependencyFailure(
                "dependency-shared-conflict", "new consumer"
            )

    def install(_checkpoint, wheel, _command, guard):
        guard()
        installed.append(wheel.name)

    monkeypatch.setattr(repair_remote, "require_shared_dependencies", check)
    monkeypatch.setattr(repair_remote.python, "install", install)
    # A second admitted wheel forces a second mutation boundary check.
    payload["package"]["wheels"]["other.whl"] = "b" * 64
    monkeypatch.setattr(
        repair_remote.python, "wheel_identity", lambda path: (path.stem, "1")
    )
    with pytest.raises(repair_remote.python.dependency_scope.DependencyFailure):
        repair_remote.execute(payload)
    assert installed == ["other.whl"]


def test_worker_rebuilds_canonical_assets_before_installed(reconstruction):
    payload, state, entries, commands, effects = reconstruction
    repair_remote.execute(payload)
    assert state["phase"] == "installed"
    assert commands == [["systemctl", "daemon-reload"]]
    assert ("service-assets", "complete") in effects
    repair_remote.verify_installation(payload)
    # An installed wheel alone cannot satisfy verification after asset damage.
    Path(entries[0][1]).unlink()
    with pytest.raises(RuntimeError, match="service asset"):
        repair_remote.verify_installation(payload)


def test_interrupted_asset_publication_resumes_under_guard(reconstruction, monkeypatch):
    payload, state, entries, commands, _effects = reconstruction
    original = repair_remote.guest.publish_file
    count = 0

    def interrupted(*args, **kwargs):
        nonlocal count
        count += 1
        original(*args, **kwargs)
        if count == 2:
            raise OSError("lost reply")

    monkeypatch.setattr(repair_remote.guest, "publish_file", interrupted)
    with pytest.raises(OSError, match="lost reply"):
        repair_remote.execute(payload)
    assert state["phase"] == "reset"
    assert not commands
    monkeypatch.setattr(repair_remote.guest, "publish_file", original)
    repair_remote.execute(payload)
    assert state["phase"] == "installed"
    repair_remote.verify_installation(payload)


def test_asset_preflight_rejects_symlink_without_writing_other_assets(reconstruction):
    payload, _state, entries, commands, _effects = reconstruction
    target = Path(entries[-1][1])
    target.parent.mkdir(parents=True)
    target.symlink_to("/unrelated")
    with pytest.raises(RuntimeError, match="service asset"):
        repair_remote.execute(payload)
    assert not Path(entries[0][1]).exists()
    assert not commands
