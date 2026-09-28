"""Managed profiler discovery and container wiring without host or cluster changes."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module():
    spec = importlib.util.spec_from_file_location(
        "managed_profilers", ROOT / "tools/managed_profilers.py"
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def package_fixture(tmp_path, monkeypatch):
    m = module()
    prefix = tmp_path / "opt"
    profile = tmp_path / "activation.sh"
    profile.write_text("# fixture activation\n")
    binaries, listings, owners = [], {}, {}
    for tool in ("nsys", "ncu"):
        directory = prefix / f"vendor/{tool}-version"
        binary = directory / f"bin/{tool}"
        library = directory / "lib/support.so"
        for path in (binary, library):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        binary.chmod(0o755)
        binaries.append(str(binary))
        owners[str(binary)] = tool
        listings[tool] = f"{prefix}\n{directory}\n{binary}\n{library}"

    def output(command):
        if command[0] == "bash":
            return "\n".join(binaries)
        if command[1] == "-S":
            return owners[command[2]] + ": " + command[2]
        return listings[command[2]]

    monkeypatch.setattr(m, "output", output)
    return m, profile, prefix, binaries, listings


def test_roots_include_package_support_files_not_just_binaries(tmp_path, monkeypatch):
    m, profile, prefix, _binaries, _listings = package_fixture(tmp_path, monkeypatch)
    assert m.profiler_roots(profile, prefix) == [
        prefix / "vendor/nsys-version",
        prefix / "vendor/ncu-version",
    ]


@pytest.mark.parametrize(
    "defect",
    ["missing-profile", "linked-profile", "outside", "broad-root", "missing-inventory"],
)
def test_incomplete_or_overbroad_mounts_fail(tmp_path, monkeypatch, defect):
    m, profile, prefix, binaries, listings = package_fixture(tmp_path, monkeypatch)
    if defect == "missing-profile":
        profile.unlink()
    elif defect == "linked-profile":
        original = profile.with_suffix(".original")
        profile.rename(original)
        profile.symlink_to(original)
    elif defect == "outside":
        binaries[0] = sys.executable
    elif defect == "broad-root":
        extra = prefix / "unrelated.so"
        extra.write_text("fixture")
        listings["nsys"] += "\n" + str(extra)
    else:
        listings["nsys"] = str(prefix)
    with pytest.raises(ValueError):
        m.profiler_roots(profile, prefix)


@pytest.mark.parametrize("course", ["llm-inference", "custom-cuda-kernels"])
@pytest.mark.parametrize("discovery_ok", [True, False])
def test_runner_mounts_managed_tools_and_preserves_arguments(
    tmp_path, course, discovery_ok
):
    root = tmp_path / "kit with spaces"
    (root / "slurm").mkdir(parents=True)
    (root / "tools").mkdir()
    runner = root / "slurm/runner.sh"
    shutil.copyfile(ROOT / course / "slurm/container_runner.example.sh", runner)
    (root / "tools/managed_profilers.py").write_text(
        "import sys\n"
        + (
            'sys.stdout.buffer.write(b"/opt/tool resources:/opt/tool resources:ro\\0/etc/profile.d/99-nsight.sh:/etc/profile.d/99-nsight.sh:ro\\0")\n'
            if discovery_ok
            else 'raise SystemExit("missing managed tools")\n'
        )
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "apptainer"
    fake.write_text(
        f'#!{sys.executable}\nimport json,os,sys\nopen(os.environ["ARGV_OUTPUT"],"w").write(json.dumps(sys.argv[1:]))\n'
    )
    fake.chmod(0o755)
    tools = tmp_path / "course tools"
    tools.mkdir()
    output = tmp_path / "argv.json"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "COURSE_TOOLS": str(tools),
        "ARGV_OUTPUT": str(output),
    }
    image = "docker://fixture/image@sha256:" + "a" * 64
    result = subprocess.run(
        ["bash", str(runner), image, "python3", "two words", "literal;$()"],
        env=env,
        text=True,
        capture_output=True,
    )
    if not discovery_ok:
        assert result.returncode != 0
        assert not output.exists()
        return
    assert result.returncode == 0, result.stderr
    args = json.loads(output.read_text())
    assert args[:2] == ["exec", "--nv"]
    assert "/opt/tool resources:/opt/tool resources:ro" in args
    assert "/etc/profile.d/99-nsight.sh:/etc/profile.d/99-nsight.sh:ro" in args
    assert f"{tools}:{tools}:ro" in args
    assert args[-3:] == ["python3", "two words", "literal;$()"]
    assert any("source /etc/profile.d/99-nsight.sh" in arg for arg in args)


@pytest.mark.parametrize("symlink", [False, True])
def test_documented_report_export_preserves_private_original(tmp_path, symlink):
    source = (ROOT / "README.md").read_text()
    command = next(
        block
        for block in re.findall(r"```bash\n(.*?)\n```", source, re.S)
        if "viewer_dir=" in block
    )
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    report = private / "selected report.nsys-rep"
    report.write_bytes(b"native report fixture")
    report.chmod(0o600)
    selected = private / "link.nsys-rep" if symlink else report
    if symlink:
        selected.symlink_to(report)
    destination = tmp_path / "viewer"
    destination.mkdir()
    command = command.replace(
        "'<absolute path to the selected completed report>'", '"$SELECTED_REPORT"'
    ).replace("/data/nsight-reports", str(destination))
    result = subprocess.run(
        ["bash", "-c", command],
        env={**os.environ, "SELECTED_REPORT": str(selected)},
        text=True,
        capture_output=True,
    )
    assert report.read_bytes() == b"native report fixture"
    assert report.stat().st_mode & 0o777 == 0o600
    assert private.stat().st_mode & 0o777 == 0o700
    if symlink:
        assert result.returncode != 0
        assert not list(destination.iterdir())
    else:
        assert result.returncode == 0, result.stderr
        copies = list(destination.glob("*/*.nsys-rep"))
        assert len(copies) == 1 and copies[0].read_bytes() == report.read_bytes()
        assert copies[0].stat().st_mode & 0o777 == 0o644
        assert copies[0].parent.stat().st_mode & 0o777 == 0o755


def test_activation_cannot_fall_back_to_an_ambient_profiler(tmp_path, monkeypatch):
    m = module()
    profile = tmp_path / "activation.sh"
    profile.write_text('export PATH="/missing-managed-install:$PATH"\n')
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    for name in ("nsys", "ncu"):
        binary = ambient / name
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
    monkeypatch.setenv("PATH", f"{ambient}:{os.environ['PATH']}")
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        m.profiler_roots(profile, tmp_path)
