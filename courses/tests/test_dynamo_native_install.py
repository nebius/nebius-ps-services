"""Reject incomplete native package closures before replacing a runtime wheel."""

import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest


@pytest.fixture
def installer():
    path = (
        Path(__file__).resolve().parents[1]
        / "advanced-gpu-communication/tools/install_dynamo_native.py"
    )
    spec = importlib.util.spec_from_file_location("dynamo_native_install", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wheel(tmp_path, *, omit=None, extra=None, version="1.3.2"):
    members = {
        "nixl_cu13/_bindings.cpython-312-x86_64-linux-gnu.so": b"\x7fELFbinding",
        "nixl_ep_cu13/nixl_ep_cpp_torch211.cpython-312-x86_64-linux-gnu.so": b"\x7fELFep",
        ".nixl_cu13.mesonpy.libs/libnixl.so": b"\x7fELFcore",
        ".nixl_cu13.mesonpy.libs/plugins/libplugin_UCX.so": b"\x7fELFplugin",
        "nixl_cu13-1.3.2.dist-info/METADATA": (
            f"Metadata-Version: 2.4\nName: nixl-cu13\nVersion: {version}\n"
        ).encode(),
    }
    if omit:
        members = {name: data for name, data in members.items() if omit not in name}
    members.update(extra or {})
    path = tmp_path / "nixl_cu13-1.3.2-cp312-cp312-linux_x86_64.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def test_joint_wheel_has_both_native_consumers_and_ucx_plugin(installer, tmp_path):
    result = installer.inspect_wheel(wheel(tmp_path))
    assert result["version"] == "1.3.2"
    assert len(result["native_files"]) == 4


@pytest.mark.parametrize("missing", ["nixl_ep_cu13/", "_bindings.", "libplugin_UCX"])
def test_missing_native_component_rejected(installer, tmp_path, missing):
    with pytest.raises(ValueError, match="missing"):
        installer.inspect_wheel(wheel(tmp_path, omit=missing))


@pytest.mark.parametrize(
    "name,payload",
    [
        ("nixl_cu13.libs/libucp-deadbeef.so.0", b"\x7fELFucx"),
        (
            "nixl_ep_cu13/nixl_ep_cpp_torch211.cpython-312-x86_64-linux-gnu.so",
            b"\x7fELF\x00libucp-deadbeef.so.0\x00",
        ),
        ("../escaped", b"data"),
    ],
)
def test_bundled_native_dependency_or_escaping_member_rejected(
    installer, tmp_path, name, payload
):
    with pytest.raises(ValueError):
        installer.inspect_wheel(wheel(tmp_path, extra={name: payload}))


def test_wrong_wheel_version_rejected(installer, tmp_path):
    with pytest.raises(ValueError, match="version"):
        installer.inspect_wheel(wheel(tmp_path, version="9.9.9"))


def test_existing_build_prefix_is_preserved_before_commands(installer, tmp_path):
    prefix = tmp_path / "existing"
    prefix.mkdir()
    marker = prefix / "owned"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="exists"):
        installer.install(tmp_path / "python", tmp_path / "ucx", prefix)
    assert marker.read_text() == "keep"


def test_missing_ucx_fails_before_prefix_creation(installer, tmp_path):
    prefix = tmp_path / "new"
    with pytest.raises(ValueError, match="UCX"):
        installer.install(Path(sys.executable), tmp_path / "ucx", prefix)
    assert not prefix.exists()


@pytest.fixture
def native_build(installer, tmp_path, monkeypatch):
    ucx = tmp_path / "ucx"
    (ucx / "include/ucp/api").mkdir(parents=True)
    (ucx / "include/ucp/api/ucp.h").write_text("headers")
    (ucx / "lib").mkdir()
    (ucx / "lib/libucp.so").write_bytes(b"native UCX")
    site = tmp_path / "site"
    site.mkdir()
    # A symlink models the venv executable whose path must remain selected.
    python = tmp_path / "venv-python"
    python.symlink_to(sys.executable)
    state = {
        "python": [3, 12],
        "torch": "2.11.0+cu130",
        "cuda": "13.0",
        "packages": {"ai-dynamo": "1.4.2", "vllm": "0.26.0", "nixl-cu13": "1.3.2"},
        "site": str(site),
    }
    build = {
        "calls": [],
        "omit": None,
        "revision": installer.SOURCE_COMMIT,
        "drift": False,
        "corrupt": False,
        "cuda": "13.0",
        "installed": False,
    }
    monkeypatch.setattr(installer.shutil, "which", lambda name: "/tools/" + name)

    def command(argv, *, env=None, log=None):
        build["calls"].append(argv)
        if argv[1:2] == ["-c"]:
            assert argv[0] == str(python)
            assert "PYTHONPATH" not in env
            if build["installed"] and build["drift"]:
                state["packages"]["unrelated"] = "9.0"
            return json.dumps(state)
        if argv[:2] == ["nvcc", "--version"]:
            return "Cuda compilation tools, release " + build["cuda"]
        if argv[0] == "git" and "rev-parse" in argv:
            return build["revision"]
        if argv[1:3] == ["-m", "build"]:
            output = Path(argv[argv.index("--outdir") + 1])
            output.mkdir(parents=True)
            wheel(output, omit=build["omit"])
        if "--reinstall-package" in argv:
            assert "--no-deps" in argv
            assert argv[argv.index("--python") + 1] == str(python)
            with zipfile.ZipFile(argv[-1]) as archive:
                archive.extractall(site)
            if build["corrupt"]:
                (site / ".nixl_cu13.mesonpy.libs/libnixl.so").write_bytes(b"corrupt")
            build["installed"] = True
        return ""

    monkeypatch.setattr(installer, "command", command)
    build.update(python=python, ucx=ucx, prefix=tmp_path / "build")
    return build


def run_install(installer, build):
    return installer.install(build["python"], build["ucx"], build["prefix"])


def test_joint_build_preserves_versions_and_checks_installed_bytes(
    installer, native_build
):
    receipt = run_install(installer, native_build)
    assert native_build["installed"]
    assert receipt["native_runtime_qualified"] is False
    assert (native_build["prefix"] / "installation.json").is_file()
    compile_command = next(
        c for c in native_build["calls"] if c[1:3] == ["-m", "build"]
    )
    assert "-Csetup-args=-Dbuild_nixl_ep=true" in compile_command
    assert "-Ccompile-args=-j8" in compile_command


@pytest.mark.parametrize(
    "fault,message", [("revision", "revision"), ("omit", "NIXL-EP")]
)
def test_bad_source_or_incomplete_build_never_replaces_runtime(
    installer, native_build, fault, message
):
    native_build[fault] = "0" * 40 if fault == "revision" else "nixl_ep_cu13/"
    with pytest.raises(ValueError, match=message):
        run_install(installer, native_build)
    assert not native_build["installed"]


@pytest.mark.parametrize(
    "fault,message", [("drift", "versions"), ("corrupt", "differs")]
)
def test_post_install_drift_has_no_success_receipt(
    installer, native_build, fault, message
):
    native_build[fault] = True
    with pytest.raises(ValueError, match=message):
        run_install(installer, native_build)
    assert not (native_build["prefix"] / "installation.json").exists()


def test_wrong_cuda_compiler_fails_before_build(installer, native_build):
    native_build["cuda"] = "12.9"
    with pytest.raises(ValueError, match="CUDA 13"):
        run_install(installer, native_build)
    assert not native_build["prefix"].exists()
