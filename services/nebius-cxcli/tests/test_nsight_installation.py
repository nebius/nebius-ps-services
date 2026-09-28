import copy
import hashlib
import io
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_cxcli import nsight_installation as owner
from nebius_cxcli import nsight_installation_files as files
from nebius_cxcli.deployment_state import digest


def test_composed_repair_job_only_verifies_after_restoring_omissions(tmp_path, monkeypatch):
    from nebius_cxcli import nsight_profiling as profiling

    profile, receipt, missing = (tmp_path / name for name in ("profile", "receipt", "missing"))
    for path in (profile, receipt):
        path.write_text("accepted")

    def preserved_state(path):
        metadata = path.stat()
        return metadata.st_ino, metadata.st_mtime_ns, metadata.st_mode, path.read_bytes()

    before = [preserved_state(path) for path in (profile, receipt)]
    original_source = profiling.installer_source()
    probe = (
        "from pathlib import Path\n"
        "package = {'__name__': 'fixture'}\n"
        f"exec({original_source!r}, package)\n"
        "def reject_install(*args):\n"
        "    raise RuntimeError('ordinary installation must not run during omission repair')\n"
        "def verify(*args):\n"
        f"    assert Path({str(missing)!r}).read_text() == 'restored'\n"
        "    return {'verified': True}\n"
        "package.update(install=reject_install, verify=verify, validate_runtime_privileges=lambda: None)\n"
        "package['main']()\n"
    )
    monkeypatch.setattr(profiling, "installer_source", lambda: probe)
    monkeypatch.setattr(
        profiling, "activation_source", lambda: "import sys\nassert sys.argv[1] == 'verify'\n"
    )
    monkeypatch.setattr(profiling, "runtime_mount_script", lambda **kw: "set -eu\n")
    monkeypatch.setattr(
        owner,
        "repair_command",
        lambda repair: (
            shlex.join(
                [
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(missing)!r}).write_text('restored')",
                ]
            )
            + "\n"
        ),
    )
    job = profiling.customization_job(
        name="omission-repair",
        image=profiling.RUNTIME_IMAGE,
        pvc="jail",
        filesystem_id="filesystem",
        request={"action": "install", "admission": {}},
        installation_repair={"omissions": []},
    )
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["volumeMounts"][0]["readOnly"] is False
    # Execute the composed shell and installer request dispatch. Isolated probes
    # replace Linux privilege/chroot, activation and repair effects.
    script = f'setpriv() {{ shift 7; {shlex.quote(sys.executable)} "$@"; }}\n'
    script += "sleep() { :; }\n" + container["command"][2]
    result = subprocess.run(["sh"], input=script, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert profiling.parse_result(result.stdout) == {"verified": True}
    assert [preserved_state(path) for path in (profile, receipt)] == before


@pytest.fixture
def owned(tmp_path, monkeypatch):
    original = Path.lstat

    def root_owned(path, *args, **kwargs):
        fields = list(original(path, *args, **kwargs))
        fields[4] = 0
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "lstat", root_owned)
    monkeypatch.setattr(files, "safe_parent", lambda path: None)

    def publish(source, destination):
        # Portable model of the Linux atomic no-replace primitive.
        try:
            os.link(source, destination)
        except FileExistsError:
            return
        source.unlink()

    monkeypatch.setattr(files, "publish_missing", publish)
    return tmp_path / "owned"


def test_missing_file_publication_and_replay_preserve_exact_content_and_inode(owned):
    content = b"owned executable"
    checksum = hashlib.sha256(content).hexdigest()
    assert files.regular_state(owned, checksum, 0o755)["path"] == str(owned)
    files.create_file(owned, io.BytesIO(content), 0o755, checksum)
    before = owned.stat()
    assert files.regular_state(owned, checksum, 0o755) is None
    files.create_file(owned, io.BytesIO(content), 0o755, checksum)
    assert owned.stat() == before


@pytest.mark.parametrize("kind", ["changed", "symlink", "hardlink", "mode"])
def test_conflicting_file_is_never_overwritten(owned, kind):
    owned.write_bytes(b"admin bytes")
    owned.chmod(0o644)
    if kind == "symlink":
        target = owned.with_suffix(".target")
        owned.rename(target)
        owned.symlink_to(target)
    elif kind == "hardlink":
        os.link(owned, owned.with_suffix(".link"))
    elif kind == "mode":
        owned.chmod(0o666)
    before = owned.lstat(), owned.read_bytes()
    with pytest.raises(RuntimeError, match="changed or foreign"):
        files.create_file(
            owned, io.BytesIO(b"expected"), 0o644, hashlib.sha256(b"expected").hexdigest()
        )
    assert (owned.lstat(), owned.read_bytes()) == before


def test_interrupted_file_publication_retries_without_partial_target(owned, monkeypatch):
    publish = files.publish_missing
    monkeypatch.setattr(
        files, "publish_missing", lambda *a: (_ for _ in ()).throw(OSError("interrupted"))
    )
    checksum = hashlib.sha256(b"expected").hexdigest()
    with pytest.raises(OSError, match="interrupted"):
        files.create_file(owned, io.BytesIO(b"expected"), 0o644, checksum)
    assert list(owned.parent.iterdir()) == []
    monkeypatch.setattr(files, "publish_missing", publish)
    files.create_file(owned, io.BytesIO(b"expected"), 0o644, checksum)
    assert owned.read_bytes() == b"expected"


def test_restore_frozen_profile_omissions_allows_partial_replay_and_rejects_new_omissions(owned):
    package = {"PACKAGES": {}, "PROFILE": owned, "profile_content": lambda _: "profile\n"}
    hook = owned.with_suffix(".hook")
    activation = {"HOOK": hook, "CONTENT": "hook\n"}
    rows = [
        {"path": str(path), "sha256": hashlib.sha256(content.encode()).hexdigest(), "mode": 0o644}
        for path, content in ((owned, "profile\n"), (hook, "hook\n"))
    ]
    request = {"omissions": rows, "admission": {"artifacts": []}, "verification": {"binaries": {}}}
    files.restore(package, activation, request, rows[:1])
    before = owned.stat()
    files.restore(package, activation, request, rows[1:])
    assert owned.stat() == before and hook.read_text() == "hook\n"
    with pytest.raises(RuntimeError, match="preimage changed"):
        files.restore(package, activation, request, [{**rows[0], "sha256": "different"}])


def test_healthy_installation_never_uses_repair_observation(monkeypatch):
    body = {"verification": {}, "admission": {}}
    receipt = {**body, "receiptSha256": digest(body)}
    monkeypatch.setattr("nebius_cxcli.nsight_profiling.validate_customization", lambda *a: None)
    monkeypatch.setattr(
        "nebius_cxcli.nsight_runtime.NsightKubernetes", lambda *a: pytest.fail("unexpected repair")
    )
    assert owner.observe_installation({}, receipt, lambda *a: None).state == "healthy"


@pytest.mark.parametrize("result", [{"state": "healthy", "omissions": []}, {"state": "unknown"}])
def test_failed_verify_is_not_silently_classified_as_repairable(monkeypatch, result):
    body = {"verification": {}, "admission": {}}
    receipt = {**body, "receiptSha256": digest(body)}
    monkeypatch.setattr("nebius_cxcli.nsight_profiling.validate_customization", lambda *a: None)
    monkeypatch.setattr(
        "nebius_cxcli.nsight_profiling.parse_result", lambda *a: copy.deepcopy(result)
    )
    monkeypatch.setattr(
        "nebius_cxcli.nsight_runtime.NsightKubernetes",
        lambda *a: SimpleNamespace(run=lambda *a, **kw: ""),
    )
    with pytest.raises(RuntimeError, match="transport failed"):
        owner.observe_installation(
            {}, receipt, lambda *a: (_ for _ in ()).throw(RuntimeError("transport failed"))
        )


def test_repair_program_is_standalone_python_and_does_not_change_pinned_installer():
    program = owner.file_program()
    compile(program, "nsight-repair", "exec")
    assert "renameat2" in program
