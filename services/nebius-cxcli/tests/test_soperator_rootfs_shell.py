"""Execute rootfs safety scripts against disposable storage and failing tools."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys

import pytest

from nebius_cxcli.soperator_jail_protection import directory_probe_script
from nebius_cxcli.soperator_protected_data_plane import (
    parse_rootfs_inventory_log,
    rootfs_cleanup_job_manifest,
    rootfs_inventory_evidence_sha256,
    rootfs_inventory_job_manifest,
)

IMAGE = "registry.example/rootfs@sha256:" + "a" * 64


@pytest.fixture
def shell_rootfs(tmp_path):
    root = tmp_path / "jail"
    root.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    # Run the actual shell/find/hash pipeline. GNU stat/readlink need portable
    # adapters for macOS; failures occur at executable boundaries, not in Python
    # replicas of the inventory or protection algorithms.
    for name in ("find", "stat", "readlink", "sha256sum", "base64", "tr", "sort"):
        executable = shutil.which(name)
        assert executable, f"test requires {name}"
        wrapper = tools / name
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import os, sys\n"
            f"name = {name!r}\n"
            "args = sys.argv[1:]\n"
            "if os.environ.get('CXCLI_TEST_FAIL_TOOL') == name and '-delete' not in args:\n"
            "    sys.exit(23)\n"
            "if name == 'stat':\n"
            "    item = os.lstat(args[-1])\n"
            "    values = {'%d': str(item.st_dev), '%i': str(item.st_ino),\n"
            "              '%f:%u:%g': f'{item.st_mode:x}:{item.st_uid}:{item.st_gid}'}\n"
            "    print(values[args[1]])\n"
            "elif name == 'readlink':\n"
            "    os.write(1, os.fsencode(os.readlink(args[-1])) + b'\\n')\n"
            "else:\n"
            f"    os.execv({executable!r}, [{executable!r}, *args])\n"
        )
        wrapper.chmod(0o700)

    def run(script, fail_tool=""):
        script = script.replace("/mnt/jail", shlex.quote(str(root)))
        script = script.replace(" /mnt\n", " " + shlex.quote(str(root.parent)) + "\n")
        return subprocess.run(
            ["/bin/sh", "-c", script],
            env={
                **os.environ,
                "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                "CXCLI_TEST_FAIL_TOOL": fail_tool,
            },
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )

    return root, run


def _inventory_script():
    job = rootfs_inventory_job_manifest(
        namespace="soperator", name="inventory", image=IMAGE, pvc_name="passive"
    )
    return job["spec"]["template"]["spec"]["containers"][0]["command"][2]


@pytest.mark.parametrize(
    "failed_tool", ["find", "stat", "readlink", "sha256sum", "base64", "tr", "sort"]
)
def test_rootfs_inventory_propagates_tool_failure(shell_rootfs, failed_tool):
    root, run = shell_rootfs
    (root / "file").write_text("fixture")
    (root / "link").symlink_to("file")

    result = run(_inventory_script(), failed_tool)

    assert result.returncode != 0, f"{failed_tool} failure produced successful inventory"


@pytest.mark.parametrize("failed_tool", ["find", "stat", "sha256sum", "sort"])
def test_rootfs_cleanup_never_deletes_after_failed_inventory(shell_rootfs, failed_tool):
    root, run = shell_rootfs
    marker = root / "keep"
    marker.write_text("retained fixture")
    empty = parse_rootfs_inventory_log(image=IMAGE, output="")
    job = rootfs_cleanup_job_manifest(
        namespace="soperator",
        name="cleanup",
        image=IMAGE,
        pvc_name="passive",
        expected_inventory_sha256=rootfs_inventory_evidence_sha256(empty),
    )

    result = run(job["spec"]["template"]["spec"]["containers"][0]["command"][2], failed_tool)

    assert result.returncode != 0
    assert marker.read_text() == "retained fixture"


@pytest.mark.parametrize("populated", [False, True])
def test_rootfs_inventory_and_guarded_cleanup_preserve_canonical_evidence(shell_rootfs, populated):
    import hashlib

    root, run = shell_rootfs
    if populated:
        (root / "directory").mkdir()
        (root / "file").write_text("fixture")
        (root / "link").symlink_to("file\n")
    result = run(_inventory_script())
    assert result.returncode == 0, result.stderr
    manifest = parse_rootfs_inventory_log(image=IMAGE, output=result.stdout)
    assert len(manifest.entries) == (3 if populated else 0)
    if populated:
        link = next(item for item in manifest.entries if item.path == "/link")
        assert link.digest == "sha256:" + hashlib.sha256(b"file\n\n").hexdigest()
    expected = rootfs_inventory_evidence_sha256(manifest)
    assert expected == "sha256:" + hashlib.sha256(result.stdout.encode()).hexdigest()
    job = rootfs_cleanup_job_manifest(
        namespace="soperator",
        name="cleanup",
        image=IMAGE,
        pvc_name="passive",
        expected_inventory_sha256=expected,
    )
    result = run(job["spec"]["template"]["spec"]["containers"][0]["command"][2])
    assert result.returncode == 0, result.stderr
    assert list(root.iterdir()) == []


@pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
def test_protected_directory_probe_accepts_only_real_directories(shell_rootfs, kind):
    root, run = shell_rootfs
    selected = root / "selected"
    if kind == "directory":
        selected.mkdir()
    elif kind == "file":
        selected.write_text("fixture")
    else:
        selected.symlink_to(".")

    result = run(directory_probe_script(["/selected"]))

    assert (result.returncode == 0) is (kind == "directory")
    if kind == "directory":
        assert result.stdout.strip() == str(selected.stat().st_ino)
