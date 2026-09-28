import ast
import copy
import json
import shlex
import stat
import subprocess

import pytest

from nebius_cxcli import nsight_jail
from nebius_cxcli.deployment_state import digest
from nebius_cxcli.nsight import soperator_report_binding
from nebius_cxcli.nsight_install import candidate_config
from nebius_cxcli.nsight_profiling import (
    customize_generation,
    default_settings,
    installer_source,
    runtime_mount_script,
    sealed_customization,
)
from test_deployment_jail_state import initial, switch
from test_deployment_plan import config


def profiling_config():
    payload = config()
    payload["deploy"] = {"targets": [{"instance_id": "cluster"}]}
    payload["apps"]["charts"][0]["values"] = initial()
    return candidate_config(payload, target_ref="cluster", settings=default_settings())


def package_receipts():
    admission = {"schema": "nebius-cxcli.nsight-packages.v2", "artifacts": [], "transaction": {}}
    verification = {
        "schema": "nebius-cxcli.nsight-customization.v1",
        "admissionSha256": digest(admission),
        "binaries": {"nsys": "/opt/nsys/bin/nsys", "ncu": "/opt/ncu/ncu"},
        "profileSha256": "a" * 64,
        "binarySha256": {"nsys": "b" * 64, "ncu": "c" * 64},
    }
    return admission, verification


@pytest.mark.parametrize(
    "output",
    [
        "Remv existing-package [1.0]",
        "Inst cuda-drivers (600 repo [amd64])",
        "Inst libglib2.0-0 [1.0] (2.0 repo [amd64])",
        "Inst nsight-compute-2026.2.1 (2026.3.0 repo [amd64])",
        "Inst invalid output",
    ],
)
def test_package_admission_rejects_unrelated_changes(output):
    with pytest.raises(RuntimeError):
        nsight_jail.parse_transaction(output)


def test_package_admission_only_allows_pinned_tools_and_new_bounded_dependencies():
    assert nsight_jail.parse_transaction(
        "Inst nsight-compute-2026.2.1 (2026.2.1.5-1 local-deb [amd64])\n"
        "Inst libglib2.0-0t64 (2.80 distro [amd64])"
    ) == {"nsight-compute-2026.2.1": "2026.2.1.5-1", "libglib2.0-0t64": "2.80"}


def test_configuration_only_recovery_is_admitted_but_unrelated_configuration_is_rejected():
    assert nsight_jail.parse_transaction(
        "Conf nsight-compute-2026.2.1 (2026.2.1.5-1 local [amd64])"
    ) == {"nsight-compute-2026.2.1": "2026.2.1.5-1"}
    with pytest.raises(RuntimeError):
        nsight_jail.parse_transaction("Conf cuda-drivers (600 repo [amd64])")


def test_dependency_checksum_uses_exact_signed_metadata_not_apt_display_hash(monkeypatch):
    metadata = (
        "Package: libglib2.0-0t64\nVersion: 2.80\nArchitecture: arm64\nSHA256: "
        + "a" * 64
        + "\nSHA512: "
        + "b" * 128
    )
    calls = []
    monkeypatch.setattr(nsight_jail, "run", lambda args: calls.append(args) or metadata)
    assert nsight_jail.dependency_checksum("libglib2.0-0t64", "2.80", "arm64") == "a" * 64
    assert "Dir::Etc::sourceparts=-" in calls[0]
    with pytest.raises(RuntimeError, match="exact signed"):
        nsight_jail.dependency_checksum("libglib2.0-0t64", "2.81", "arm64")


def test_installer_works_with_python310_hashing(tmp_path, monkeypatch):
    ast.parse(installer_source(), feature_version=(3, 10))
    monkeypatch.delattr(nsight_jail.hashlib, "file_digest", raising=False)
    path = tmp_path / "artifact"
    path.write_bytes(b"artifact")
    assert nsight_jail.sha(path) == nsight_jail.hashlib.sha256(b"artifact").hexdigest()


def test_active_jail_verification_uses_the_same_bounded_request_transport():
    from nebius_cxcli.nsight_profiling import active_verify_arguments

    admission, _ = package_receipts()
    args = active_verify_arguments(admission)
    assert args[:4] == ["chroot", "/mnt/jail", "/bin/sh", "-c"]
    commands = shlex.split(args[4])
    assert commands[3:5] == ["verify", "&&"]
    assert commands[7] == installer_source()
    assert nsight_jail.decode_payload(commands[8]) == {"action": "verify", "admission": admission}


def test_reports_directory_refuses_symlinks_and_preserves_existing_permissions(tmp_path):
    mount = tmp_path / "data"
    mount.mkdir()
    nsight_jail.ensure_reports(str(mount), "team/reports")
    assert stat.S_IMODE((mount / "team/reports").stat().st_mode) == 0o1777
    (mount / "team/reports").chmod(0o750)
    nsight_jail.ensure_reports(str(mount), "team/reports")
    assert stat.S_IMODE((mount / "team/reports").stat().st_mode) == 0o750
    (mount / "redirect").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        nsight_jail.ensure_reports(str(mount), "redirect/escape")
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("target_exists", [False, True])
def test_install_rejects_profile_symlink_before_package_work(tmp_path, monkeypatch, target_exists):
    profile = tmp_path / "99-nsight.sh"
    target = tmp_path / "administrator-profile"
    if target_exists:
        target.write_text("administrator content")
    profile.symlink_to(target)
    monkeypatch.setattr(nsight_jail, "PROFILE", profile)
    monkeypatch.setattr(nsight_jail, "RECEIPT", tmp_path / "receipt.json")
    monkeypatch.setattr(nsight_jail, "platform", lambda: ("ubuntu2404", "amd64"))
    monkeypatch.setattr(nsight_jail, "transaction", lambda _: pytest.fail("reached package work"))
    admission = {
        "schema": "nebius-cxcli.nsight-packages.v2",
        "baseline": {},
        "preimages": {"profile": {"missing": True}, "receipt": {"missing": True}},
        "distro": "ubuntu2404",
        "arch": "amd64",
        "artifacts": [],
        "transaction": {},
    }

    with pytest.raises(RuntimeError, match="not owned by this installer"):
        nsight_jail.install(admission)

    assert profile.is_symlink() and profile.readlink() == target
    assert target.exists() is target_exists
    if target_exists:
        assert target.read_text() == "administrator content"


def test_interruption_after_profile_publication_is_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr(nsight_jail, "PROFILE", tmp_path / "etc/profile.d/99-nsight.sh")
    monkeypatch.setattr(nsight_jail, "RECEIPT", tmp_path / "var/lib/nsight.json")
    monkeypatch.setattr(nsight_jail, "INTENT", tmp_path / "intent.json")
    monkeypatch.setattr(nsight_jail, "package_state", lambda: {})
    monkeypatch.setattr(nsight_jail, "platform", lambda: ("ubuntu2404", "amd64"))
    monkeypatch.setattr(nsight_jail, "transaction", lambda paths: {})
    monkeypatch.setattr(
        nsight_jail, "binaries", lambda: {"nsys": "/opt/nsys/bin/nsys", "ncu": "/opt/ncu/ncu"}
    )
    admission = {
        "schema": "nebius-cxcli.nsight-packages.v2",
        "baseline": {},
        "preimages": {"profile": {"missing": True}, "receipt": {"missing": True}},
        "distro": "ubuntu2404",
        "arch": "amd64",
        "artifacts": [],
        "transaction": {},
    }

    def interrupted(_):
        raise RuntimeError("interrupted verification")

    monkeypatch.setattr(nsight_jail, "verify", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        nsight_jail.install(admission)
    assert nsight_jail.PROFILE.exists() and nsight_jail.RECEIPT.exists()
    monkeypatch.setattr(
        nsight_jail, "verify", lambda _: {"profileSha256": nsight_jail.sha(nsight_jail.PROFILE)}
    )
    nsight_jail.install(admission)
    nsight_jail.PROFILE.write_text("administrator edit")
    with pytest.raises(RuntimeError, match="modified outside"):
        nsight_jail.install(admission)


def test_install_consumes_only_remaining_frozen_local_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(nsight_jail, "PROFILE", tmp_path / "profile")
    monkeypatch.setattr(nsight_jail, "RECEIPT", tmp_path / "receipt")
    monkeypatch.setattr(nsight_jail, "INTENT", tmp_path / "intent")
    monkeypatch.setattr(nsight_jail, "package_state", lambda: {})
    monkeypatch.setattr(nsight_jail, "platform", lambda: ("ubuntu2404", "arm64"))
    monkeypatch.setattr(
        nsight_jail, "binaries", lambda: {"nsys": "/opt/nsys/bin/nsys", "ncu": "/opt/ncu/ncu"}
    )
    monkeypatch.setattr(nsight_jail, "verify", lambda _: {"verified": True})
    monkeypatch.setattr(
        nsight_jail,
        "download",
        lambda artifact, *, cached_only: "/cache/" + artifact["package"] + ".deb",
    )
    package = "nsight-compute-2026.2.1"
    monkeypatch.setattr(nsight_jail, "transaction", lambda _: {package: "2026.2.1.5-1"})
    calls = []
    monkeypatch.setattr(nsight_jail, "run", lambda args: calls.append(args) or "")
    nsight_jail.install(
        {
            "schema": "nebius-cxcli.nsight-packages.v2",
            "baseline": {},
            "preimages": {"profile": {"missing": True}, "receipt": {"missing": True}},
            "distro": "ubuntu2404",
            "arch": "arm64",
            "artifacts": [
                {"package": "already-installed", "architecture": "arm64"},
                {"package": package, "architecture": "arm64"},
            ],
            "transaction": {package: "2026.2.1.5-1"},
        }
    )
    assert calls == [
        ["dpkg", "--unpack", "/cache/" + package + ".deb"],
        ["dpkg", "--configure", package],
    ]


def test_receipt_write_failure_preserves_previous_valid_intent(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    nsight_jail.atomic_json(path, {"intent": "frozen"})
    original = path.read_bytes()

    def power_loss(value, stream, **kwargs):
        stream.write('{"partial":')
        raise OSError("power loss during write")

    monkeypatch.setattr(nsight_jail.json, "dump", power_loss)
    with pytest.raises(OSError, match="power loss"):
        nsight_jail.atomic_json(path, {"complete": True})
    assert path.read_bytes() == original


def test_previous_owned_profile_survives_interrupted_new_receipt_publication(tmp_path, monkeypatch):
    monkeypatch.setattr(nsight_jail, "PROFILE", tmp_path / "profile")
    monkeypatch.setattr(nsight_jail, "RECEIPT", tmp_path / "receipt")
    monkeypatch.setattr(nsight_jail, "INTENT", tmp_path / "intent")
    nsight_jail.PROFILE.write_text("previous accepted profile")
    nsight_jail.atomic_json(
        nsight_jail.RECEIPT, {"profileSha256": nsight_jail.sha(nsight_jail.PROFILE)}
    )
    admission = {
        "schema": "nebius-cxcli.nsight-packages.v2",
        "baseline": {},
        "preimages": {
            "profile": nsight_jail.file_state(nsight_jail.PROFILE),
            "receipt": nsight_jail.file_state(nsight_jail.RECEIPT),
        },
        "distro": "ubuntu2404",
        "arch": "arm64",
        "artifacts": [],
        "transaction": {},
    }
    monkeypatch.setattr(nsight_jail, "platform", lambda: ("ubuntu2404", "arm64"))
    monkeypatch.setattr(nsight_jail, "package_state", lambda: {})
    monkeypatch.setattr(nsight_jail, "transaction", lambda _: {})
    monkeypatch.setattr(
        nsight_jail, "binaries", lambda: {"nsys": "/opt/new/nsys", "ncu": "/opt/new/ncu"}
    )
    monkeypatch.setattr(
        nsight_jail, "verify", lambda _: {"profileSha256": nsight_jail.sha(nsight_jail.PROFILE)}
    )
    atomic = nsight_jail.atomic_write

    def power_loss(path, writer):
        if path == nsight_jail.PROFILE:
            raise RuntimeError("power loss before profile publication")
        return atomic(path, writer)

    monkeypatch.setattr(nsight_jail, "atomic_write", power_loss)
    with pytest.raises(RuntimeError, match="power loss"):
        nsight_jail.install(admission)
    assert nsight_jail.PROFILE.read_text() == "previous accepted profile"
    monkeypatch.setattr(nsight_jail, "atomic_write", atomic)
    nsight_jail.install(admission)
    assert "/opt/new" in nsight_jail.PROFILE.read_text()


def test_runtime_mount_setup_is_syntax_valid():
    for read_only in (False, True):
        script = runtime_mount_script(read_only=read_only)
        subprocess.run(["sh", "-n"], input=script, text=True, check=True)
        assert "mount --make-rprivate /" in script
        assert "remount,bind,ro,nosuid,nodev,noexec" in script
        assert "mount --bind /dev/$device" in script
        assert "readlink -m" in script
        failed = subprocess.run(
            ["sh"],
            input="mount() { return 1; }\nsetpriv() { :; }\n" + script + "printf installer-ran",
            text=True,
            capture_output=True,
        )
        assert failed.returncode != 0 and "installer-ran" not in failed.stdout


def test_signed_ubuntu_index_refresh_uses_only_isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(nsight_jail, "CACHE", tmp_path / "nsight-cache")
    original = nsight_jail.Path.is_file
    monkeypatch.setattr(
        nsight_jail.Path,
        "is_file",
        lambda path: (
            True
            if str(path) == "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
            else original(path)
        ),
    )
    calls = []
    monkeypatch.setattr(nsight_jail, "run", lambda args: calls.append(args) or "")
    nsight_jail.refresh_indexes("ubuntu2404", "arm64")
    assert (nsight_jail.CACHE / "apt-lists/partial").is_dir()
    sources = (nsight_jail.CACHE / "ubuntu.list").read_text()
    assert "signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg" in sources
    assert "https://ports.ubuntu.com/ubuntu-ports noble-security main" in sources
    assert all("trusted=yes" not in line for line in sources.splitlines())
    assert calls[0][-1] == "update"
    assert "Dir::Etc::sourceparts=-" in calls[0]
    assert "Acquire::AllowInsecureRepositories=false" in calls[0]


def test_soperator_uses_persistent_data_binding_across_jail_switches():
    values = initial()
    before = soperator_report_binding(values, namespace="soperator")
    after = soperator_report_binding(switch(values), namespace="soperator")
    assert before == after
    assert before.subpath == "nsight-reports"
    assert before.claim != "jail-pvc"
    with pytest.raises(ValueError, match="entire shared"):
        soperator_report_binding(values, namespace="soperator", reports_path="/data")
    with pytest.raises(ValueError, match="persistent"):
        soperator_report_binding(values, namespace="soperator", reports_path="/tmp/reports")


def test_customization_has_separate_admission_install_and_verification_and_seals_exact_generation():
    payload = profiling_config()
    admission, verified = package_receipts()
    stages = {}
    jobs = []

    def run_job(name, job):
        jobs.append(job)
        result = admission if name == "nsight-admit" else verified
        from nebius_cxcli.nsight_recovery import initial_chain
        from nebius_cxcli.soperator_protected_data_plane import bind_protected_job_authority

        chain = initial_chain(
            bind_protected_job_authority(
                job, operation_id=digest("generation"), fence_epoch=1, pvc_uid="pvc-id"
            )
        )
        chain["attempts"][0].update(
            jobUid=name, workloadSha256=digest(job), complete=True, result=result
        )
        stages["rootfs-" + name] = {
            "status": "complete",
            "evidence": {"result": result},
            "nsight": chain,
        }
        return result

    receipt = customize_generation(
        config=payload,
        target_ref="cluster",
        image="example/jail@sha256:" + "d" * 64,
        pvc="new-jail",
        pvc_uid="pvc-id",
        pv_uid="pv-id",
        filesystem_id="fs-id",
        generation=digest("generation"),
        run_job=run_job,
    )
    assert len(jobs) == 3
    assert all(job["spec"]["backoffLimit"] == 0 for job in jobs)
    assert "until setpriv" in jobs[1]["spec"]["template"]["spec"]["containers"][0]["command"][2]
    assert jobs[2]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0]["readOnly"]
    from types import SimpleNamespace

    from nebius_cxcli.nsight_recovery import bind_attempt_receipt

    receipt = bind_attempt_receipt(
        receipt, SimpleNamespace(stage=lambda name: stages["rootfs-" + name]["nsight"])
    )
    stages["rootfs-nsight-customization"] = {"status": "complete", "evidence": receipt}
    kwargs = {
        "settings_sha256": digest(default_settings()),
        "generation": digest("generation"),
        "pvc_uid": "pvc-id",
        "pv_uid": "pv-id",
    }
    assert sealed_customization(stages, **kwargs) == receipt
    bad = copy.deepcopy(stages)
    del bad["rootfs-nsight-verify"]
    with pytest.raises(RuntimeError, match="promotion"):
        sealed_customization(bad, **kwargs)
    with pytest.raises(RuntimeError, match="another rootfs"):
        sealed_customization(stages, **{**kwargs, "pvc_uid": "another"})
    assert json.dumps(payload).count('"profiling"') == 1
