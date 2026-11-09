import hashlib
import importlib.metadata as metadata
import importlib.util
import marshal
import os
import py_compile
import struct
from pathlib import Path

import pytest

from nebius_vpngw import python_package_recovery as packages


@pytest.mark.parametrize(
    ("original", "successor"),
    [
        ({}, {"ha_parent": "parent-a"}),
        ({"ha_parent": "parent-a"}, {"ha_parent": "parent-c"}),
        (
            {"command_approval": {"digest": "approval-a"}},
            {"command_approval": {"digest": "approval-c"}},
        ),
    ],
)
def test_payload_checkpoint_retains_admission_at_both_entrypoints(
    monkeypatch, tmp_path, original, successor
):
    manifest = {
        "ordinary_assets": {},
        "dependency_wheels": {"dependency.whl": "d" * 64},
        "wheel_name": "product.whl",
        "wheel_sha256": "a" * 64,
    }
    prepared, installed = [], []
    monkeypatch.setattr(packages, "prepare", lambda directory, *_: prepared.append(directory))
    monkeypatch.setattr(packages, "load", lambda *_: {"before": {}, "target": {}})
    monkeypatch.setattr(packages, "install", lambda directory, *_: installed.append(directory))

    for authority in (original, original, successor):
        admitted = {**manifest, **authority}
        packages.prepare_payload(admitted, tmp_path, lambda: None)
        packages.install_payload(admitted, tmp_path, lambda: None, lambda: None)

    assert prepared == installed
    assert prepared[:2] == prepared[2:4]
    assert set(prepared[:2]).isdisjoint(prepared[4:])


@pytest.fixture
def checkpoint(monkeypatch, tmp_path):
    scheme = tmp_path.resolve() / "site"
    scheme.mkdir()
    monkeypatch.setattr(packages, "schemes", lambda: (scheme,))
    directory = tmp_path / "checkpoint"
    packages.storage.directory(directory / "blobs")
    before, target = {}, {}
    old = {
        "module.py": b"old code\n",
        "vpngw_recovery_fixture-1.0.dist-info/RECORD": b"old inventory\n",
        "vpngw_recovery_fixture-1.0.dist-info/METADATA": b"Name: vpngw-recovery-fixture\nVersion: 1.0\n",
    }
    new = {
        "module.py": b"new code\n",
        "vpngw_recovery_fixture-2.0.dist-info/RECORD": b"new inventory\n",
        "vpngw_recovery_fixture-2.0.dist-info/METADATA": b"Name: vpngw-recovery-fixture\nVersion: 2.0\n",
    }
    for name, raw in old.items():
        path = scheme / name
        packages.atomic(path, raw, 0o644)
        before[str(path)] = packages.content(path)
        packages.atomic(directory / "blobs" / before[str(path)]["sha256"], raw, 0o600)
    for name, raw in new.items():
        target[str(scheme / name)] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "mode": 0o644,
            "uid": os.geteuid(),
            "gid": os.getegid(),
        }
    value = {"before": before, "target": target, "state": "installing"}
    return directory, scheme, old, new, value


def test_missing_record_and_partial_owned_install_restore_exact_predecessor(checkpoint):
    directory, scheme, old, new, value = checkpoint
    (scheme / "vpngw_recovery_fixture-1.0.dist-info/RECORD").unlink()
    packages.atomic(scheme / "module.py", new["module.py"], 0o644)
    packages.atomic(
        scheme / "vpngw_recovery_fixture-2.0.dist-info/RECORD",
        new["vpngw_recovery_fixture-2.0.dist-info/RECORD"],
        0o644,
    )
    packages.restore(directory, value)
    assert value["state"] == "restored"
    for path, raw in old.items():
        assert (scheme / path).read_bytes() == raw
    assert not (scheme / "vpngw_recovery_fixture-2.0.dist-info/RECORD").exists()


def test_foreign_content_blocks_before_any_restoration(checkpoint):
    directory, scheme, _old, new, value = checkpoint
    (scheme / "module.py").write_bytes(b"operator change")
    packages.atomic(
        scheme / "vpngw_recovery_fixture-2.0.dist-info/RECORD",
        new["vpngw_recovery_fixture-2.0.dist-info/RECORD"],
        0o644,
    )
    with pytest.raises(RuntimeError, match="foreign or unproven"):
        packages.restore(directory, value)
    assert (scheme / "vpngw_recovery_fixture-2.0.dist-info/RECORD").exists()
    assert (scheme / "module.py").read_bytes() == b"operator change"


def test_metadata_only_restoration_does_not_rewrite_matching_payload(checkpoint):
    directory, scheme, _old, _new, value = checkpoint
    before = (scheme / "module.py").stat().st_ino
    (scheme / "vpngw_recovery_fixture-1.0.dist-info/RECORD").unlink()
    packages.restore(directory, value)
    assert (scheme / "module.py").stat().st_ino == before


def test_interrupted_restoration_resumes_same_before_images(checkpoint, monkeypatch):
    directory, scheme, _old, new, value = checkpoint
    (scheme / "vpngw_recovery_fixture-1.0.dist-info/RECORD").unlink()
    packages.atomic(scheme / "module.py", new["module.py"], 0o644)
    original = packages.atomic
    calls = []

    def interrupted(path, raw, mode):
        original(path, raw, mode)
        calls.append(path)
        if len(calls) == 1:
            raise OSError("injected interruption")

    monkeypatch.setattr(packages, "atomic", interrupted)
    with pytest.raises(OSError):
        packages.restore(directory, value)
    assert value["state"] == "restoring"
    monkeypatch.setattr(packages, "atomic", original)
    packages.restore(directory, value)
    assert packages.matches(value["before"])


def test_symlink_and_outside_scheme_are_never_repaired(checkpoint):
    _directory, scheme, *_ = checkpoint
    with pytest.raises(RuntimeError, match="escapes"):
        packages.safe(scheme / ".." / "foreign", (scheme,))
    (scheme / "link").symlink_to(scheme / "module.py")
    with pytest.raises(RuntimeError, match="symlink"):
        packages.safe(scheme / "link", (scheme,))


def test_corrupt_checkpoint_blob_blocks_before_file_mutation(checkpoint):
    directory, scheme, _old, new, value = checkpoint
    packages.atomic(scheme / "module.py", new["module.py"], 0o644)
    next((directory / "blobs").iterdir()).write_bytes(b"foreign")
    with pytest.raises(RuntimeError, match="before-image changed"):
        packages.restore(directory, value)
    assert (scheme / "module.py").read_bytes() == new["module.py"]


def install_fixture(checkpoint, monkeypatch, *, resume=False):
    directory, scheme, _old, new, value = checkpoint
    if not resume:
        value["state"] = "prepared"
    monkeypatch.setattr(packages, "load", lambda *_: value)
    monkeypatch.syspath_prepend(str(scheme))

    def install(*_args, **_kwargs):
        for name, raw in new.items():
            packages.atomic(scheme / name, raw, 0o644)

    packages.install(directory, scheme / "fixture.whl", install, lambda: None)
    return value


def test_completed_admission_refuses_changed_metadata_without_replay(checkpoint, monkeypatch):
    directory, scheme, *_ = checkpoint
    install_fixture(checkpoint, monkeypatch)
    saved = (directory / "checkpoint.json").read_bytes()
    record = scheme / "vpngw_recovery_fixture-2.0.dist-info/RECORD"
    record.write_bytes(b"another admitted operation's provenance\n")
    calls = []
    with pytest.raises(RuntimeError, match="Completed Python package changed"):
        packages.install(
            directory,
            scheme / "fixture.whl",
            lambda *args, **_kwargs: calls.append(args),
            lambda: None,
        )
    assert not calls
    assert (directory / "checkpoint.json").read_bytes() == saved
    assert record.read_bytes() == b"another admitted operation's provenance\n"


def test_upgrade_retires_obsolete_metadata_from_distribution_discovery(checkpoint, monkeypatch):
    _directory, scheme, *_ = checkpoint
    install_fixture(checkpoint, monkeypatch)
    assert metadata.distribution("vpngw-recovery-fixture").version == "2.0"
    assert not (scheme / "vpngw_recovery_fixture-1.0.dist-info").exists()


def test_restore_retires_target_metadata_and_discovers_predecessor(checkpoint, monkeypatch):
    directory, scheme, _old, _new, value = checkpoint
    install_fixture(checkpoint, monkeypatch)
    packages.restore(directory, value)
    assert metadata.distribution("vpngw-recovery-fixture").version == "1.0"
    assert not (scheme / "vpngw_recovery_fixture-2.0.dist-info").exists()


def regenerated_cache(checkpoint, optimization=0):
    directory, scheme, old, new, value = checkpoint
    source = scheme / "cache_source.py"
    raw = b"answer = 42\n"
    packages.atomic(source, raw, 0o644)
    os.utime(source, (100, 100))
    old[source.name] = new[source.name] = raw
    value["before"][str(source)] = value["target"][str(source)] = packages.content(source)
    packages.atomic(directory / "blobs" / packages.content(source)["sha256"], raw, 0o600)
    cache = Path(
        importlib.util.cache_from_source(str(source), optimization=str(optimization or ""))
    )
    code = marshal.dumps(
        compile(raw, str(source), "exec", dont_inherit=True, optimize=optimization)
    )
    original = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, 100, len(raw)) + code
    packages.atomic(cache, original, 0o644)
    value["before"][str(cache)] = packages.content(cache)
    packages.atomic(directory / "blobs" / packages.content(cache)["sha256"], original, 0o600)
    os.utime(source, (200, 200))
    packages.atomic(cache, original[:8] + struct.pack("<I", 200) + original[12:], 0o644)
    return source, cache, original


@pytest.mark.parametrize("optimization", [0, 1, 2])
def test_install_accepts_only_timestamp_regeneration_of_owned_cache(
    checkpoint, monkeypatch, optimization
):
    _source, cache, _original = regenerated_cache(checkpoint, optimization)
    assert not packages.matches(checkpoint[-1]["before"])
    install_fixture(checkpoint, monkeypatch)
    assert checkpoint[-1]["state"] == "complete"
    assert not cache.exists()


@pytest.mark.parametrize("optimization", [0, 1, 2])
def test_completed_install_keeps_verified_regenerated_cache_without_reinstall(
    checkpoint, monkeypatch, optimization
):
    directory, scheme, _old, _new, value = checkpoint
    source, cache, _original = regenerated_cache(checkpoint, optimization)
    # Use the interpreter's real writer for both cache generations: marshal's
    # byte encoding also reflects object interning, not just code equality.
    py_compile.compile(str(source), doraise=True, optimize=optimization)
    value["before"][str(cache)] = packages.content(cache)
    packages.atomic(
        directory / "blobs" / packages.content(cache)["sha256"], cache.read_bytes(), 0o600
    )
    install_fixture(checkpoint, monkeypatch)
    py_compile.compile(str(source), doraise=True, optimize=optimization)
    before = cache.read_bytes(), (directory / "checkpoint.json").read_bytes()
    assert packages.matches_before(directory, value, str(cache), packages.content(cache))
    calls = []
    packages.install(
        directory, scheme / "fixture.whl", lambda *a, **k: calls.append(a), lambda: None
    )
    assert calls == []
    assert (cache.read_bytes(), (directory / "checkpoint.json").read_bytes()) == before


@pytest.mark.parametrize("fault", ["code", "mode", "unowned-source", "changed-target", "blob"])
def test_completed_install_cache_exception_rejects_foreign_changes(checkpoint, monkeypatch, fault):
    directory, scheme, _old, _new, value = checkpoint
    source, cache, _original = regenerated_cache(checkpoint)
    py_compile.compile(str(source), doraise=True)
    value["before"][str(cache)] = packages.content(cache)
    blob = directory / "blobs" / packages.content(cache)["sha256"]
    packages.atomic(blob, cache.read_bytes(), 0o600)
    install_fixture(checkpoint, monkeypatch)
    py_compile.compile(str(source), doraise=True)
    if fault == "code":
        raw = cache.read_bytes()
        cache.write_bytes(raw[:20] + bytes([raw[20] ^ 1]) + raw[21:])
    elif fault == "mode":
        cache.chmod(0o644 if packages.content(cache)["mode"] == 0o600 else 0o600)
    elif fault == "unowned-source":
        del value["before"][str(source)]
    elif fault == "changed-target":
        source.write_bytes(b"answer = 43\n")
        value["target"][str(source)] = packages.content(source)
        py_compile.compile(str(source), doraise=True)
    else:
        blob.write_bytes(b"changed before image")
    saved = cache.read_bytes(), (directory / "checkpoint.json").read_bytes()
    calls = []
    with pytest.raises(RuntimeError, match="Completed Python package changed"):
        packages.install(
            directory, scheme / "fixture.whl", lambda *a, **k: calls.append(a), lambda: None
        )
    assert calls == []
    assert (cache.read_bytes(), (directory / "checkpoint.json").read_bytes()) == saved


def test_completed_install_rejects_exact_recorded_cache_with_different_code(
    checkpoint, monkeypatch
):
    directory, scheme, _old, _new, value = checkpoint
    source, cache, _original = regenerated_cache(checkpoint)
    wrong_code = marshal.dumps(compile(b"answer = 43\n", str(source), "exec", dont_inherit=True))
    recorded = importlib.util.MAGIC_NUMBER + struct.pack("<III", 0, 200, 12) + wrong_code
    packages.atomic(cache, recorded, 0o644)
    value["before"][str(cache)] = packages.content(cache)
    packages.atomic(directory / "blobs" / packages.content(cache)["sha256"], recorded, 0o600)
    install_fixture(checkpoint, monkeypatch)
    os.utime(source, (200, 200))
    packages.atomic(cache, recorded, 0o644)
    assert packages.content(cache) == value["before"][str(cache)]
    assert packages.matches(value["target"])
    calls = []
    with pytest.raises(RuntimeError, match="Completed Python package changed"):
        packages.install(
            directory, scheme / "fixture.whl", lambda *a, **k: calls.append(a), lambda: None
        )
    assert calls == []


def test_interrupted_restore_accepts_timestamp_regeneration_without_refreshing_checkpoint(
    checkpoint,
):
    directory, scheme, _old, new, value = checkpoint
    _source, cache, original = regenerated_cache(checkpoint)
    saved = dict(value["before"])
    (scheme / "vpngw_recovery_fixture-1.0.dist-info/RECORD").unlink()
    packages.atomic(scheme / "module.py", new["module.py"], 0o644)
    packages.restore(directory, value)
    assert value["before"] == saved
    assert cache.read_bytes() == original
    assert packages.matches(value["before"])


@pytest.mark.parametrize(
    "fault",
    ["code", "source", "flags", "magic", "size", "mode", "timestamp", "unowned-source", "blob"],
)
def test_cache_exception_rejects_foreign_changes_before_restoration(checkpoint, monkeypatch, fault):
    directory, _scheme, _old, _new, value = checkpoint
    source, cache, original = regenerated_cache(checkpoint)
    raw = cache.read_bytes()
    if fault == "source":
        source.write_bytes(b"answer = 43\n")
    elif fault == "unowned-source":
        del value["before"][str(source)]
    elif fault == "mode":
        cache.chmod(0o644 if packages.content(cache)["mode"] == 0o600 else 0o600)
    elif fault == "blob":
        (directory / "blobs" / hashlib.sha256(original).hexdigest()).write_bytes(b"corrupt")
    else:
        offset = {"code": 20, "flags": 4, "magic": 0, "size": 12, "timestamp": 8}[fault]
        cache.write_bytes(raw[:offset] + bytes([raw[offset] ^ 1]) + raw[offset + 1 :])
    writes = []
    monkeypatch.setattr(packages, "atomic", lambda *args: writes.append(args))
    with pytest.raises(RuntimeError, match="foreign or unproven"):
        packages.restore(directory, value)
    assert writes == []


def test_complete_rejects_empty_obsolete_metadata_directory(checkpoint, monkeypatch):
    directory, scheme, _old, new, value = checkpoint
    for path in value["before"].keys() - value["target"].keys():
        packages.safe(Path(path), (scheme,)).unlink()
    for name, raw in new.items():
        packages.atomic(scheme / name, raw, 0o644)
    assert not packages.complete(directory, value)


@pytest.mark.parametrize("foreign_kind", ["file", "directory", "symlink"])
def test_foreign_metadata_entries_block_before_obsolete_file_removal(
    checkpoint, monkeypatch, foreign_kind
):
    _directory, scheme, old, _new, _value = checkpoint
    foreign = scheme / "vpngw_recovery_fixture-1.0.dist-info" / "foreign"
    if foreign_kind == "file":
        foreign.write_bytes(b"operator data")
    elif foreign_kind == "directory":
        foreign.mkdir()
    else:
        foreign.symlink_to(scheme / "module.py")
    with pytest.raises(RuntimeError, match="metadata|symlink"):
        install_fixture(checkpoint, monkeypatch)
    for name, raw in old.items():
        assert (scheme / name).read_bytes() == raw
    assert foreign.exists()


@pytest.mark.parametrize("after_removal", [False, True])
def test_interrupted_metadata_retirement_resumes_exact_install(
    checkpoint, monkeypatch, after_removal
):
    _directory, scheme, _old, _new, value = checkpoint
    original = packages.prune_metadata

    def interrupted(directories):
        if after_removal:
            original(directories)
        raise OSError("injected metadata interruption")

    monkeypatch.setattr(packages, "prune_metadata", interrupted)
    with pytest.raises(OSError, match="injected"):
        install_fixture(checkpoint, monkeypatch)
    assert value["state"] == "installing"
    monkeypatch.setattr(packages, "prune_metadata", original)
    install_fixture(checkpoint, monkeypatch, resume=True)
    assert value["state"] == "complete"
    assert metadata.distribution("vpngw-recovery-fixture").version == "2.0"
    assert not (scheme / "vpngw_recovery_fixture-1.0.dist-info").exists()


def test_nested_metadata_pruned_without_removing_shared_namespace(checkpoint, monkeypatch):
    directory, scheme, old, new, value = checkpoint
    old["vpngw_recovery_fixture-1.0.dist-info/licenses/LICENSE"] = b"old license"
    old["namespace/obsolete.py"] = b"old namespace module"
    new["vpngw_recovery_fixture-2.0.dist-info/licenses/LICENSE"] = b"new license"
    new["namespace/current.py"] = b"new namespace module"
    for name, raw in old.items():
        path = scheme / name
        packages.atomic(path, raw, 0o644)
        item = packages.content(path)
        value["before"][str(path)] = item
        packages.atomic(directory / "blobs" / item["sha256"], raw, 0o600)
    for name, raw in new.items():
        value["target"][str(scheme / name)] = dict(
            sha256=hashlib.sha256(raw).hexdigest(), mode=0o644, uid=os.geteuid(), gid=os.getegid()
        )
    foreign = scheme / "namespace/foreign.py"
    foreign.write_bytes(b"another distribution")
    install_fixture(checkpoint, monkeypatch)
    assert foreign.read_bytes() == b"another distribution"
    assert not (scheme / "vpngw_recovery_fixture-1.0.dist-info").exists()
    packages.restore(directory, value)
    assert foreign.read_bytes() == b"another distribution"
    assert not (scheme / "vpngw_recovery_fixture-2.0.dist-info").exists()
    assert metadata.distribution("vpngw-recovery-fixture").version == "1.0"


def test_same_version_metadata_is_preserved(checkpoint, monkeypatch):
    _directory, scheme, _old, new, value = checkpoint
    for name in list(new):
        if "2.0.dist-info" in name:
            raw = new.pop(name).replace(b"Version: 2.0", b"Version: 1.0")
            renamed = name.replace("2.0.dist-info", "1.0.dist-info")
            new[renamed] = raw
            item = value["target"].pop(str(scheme / name))
            value["target"][str(scheme / renamed)] = dict(
                item, sha256=hashlib.sha256(raw).hexdigest()
            )
    root = scheme / "vpngw_recovery_fixture-1.0.dist-info"
    inode = root.stat().st_ino
    install_fixture(checkpoint, monkeypatch)
    assert root.stat().st_ino == inode
    assert metadata.distribution("vpngw-recovery-fixture").version == "1.0"


def test_restore_retry_syncs_previously_created_metadata_before_completion(checkpoint, monkeypatch):
    directory, scheme, _old, _new, value = checkpoint
    install_fixture(checkpoint, monkeypatch)
    atomic = packages.atomic

    def interrupted(path, raw, mode):
        if path.parent.name.endswith("1.0.dist-info"):
            path.parent.mkdir(parents=True, exist_ok=True)
            raise OSError("interrupted after directory creation")
        atomic(path, raw, mode)

    monkeypatch.setattr(packages, "atomic", interrupted)
    with pytest.raises(OSError, match="directory creation"):
        packages.restore(directory, value)
    monkeypatch.setattr(packages, "atomic", atomic)
    synced = []
    sync = packages.sync_directory
    publish = packages.storage.publish

    def observe_sync(path):
        synced.append(path)
        sync(path)

    def observe_publish(path, state):
        if state.get("state") == "restored":
            assert scheme in synced
        publish(path, state)

    monkeypatch.setattr(packages, "sync_directory", observe_sync)
    monkeypatch.setattr(packages.storage, "publish", observe_publish)
    packages.restore(directory, value)
    assert value["state"] == "restored"
    assert metadata.distribution("vpngw-recovery-fixture").version == "1.0"
