"""Installation and activation contracts without package managers or GPUs."""

import errno
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

import pytest

from course_bootstrap import catalog, controller, runtime, system
from course_bootstrap.installer import Installer
from course_bootstrap.state import State, atomic_json, file_digest, setup_lock

ROOT = Path(__file__).resolve().parents[1]


def archive_fixture(tmp_path, member_name="bin/compiler"):
    state = State(tmp_path)
    source = tmp_path / "fixture.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        member = tarfile.TarInfo(member_name)
        member.mode = 0o755
        member.uid, member.gid = 123, 456
        payload = b"#!/bin/sh\nexit 0\n"
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    checksum = file_digest(source)
    source.rename(state.cache / (checksum + ".tar.gz"))
    prefix = tmp_path / "extracted"
    prefix.mkdir()
    return Installer(tmp_path, {}, {}, state), {"sha256": checksum}, prefix


def test_archive_keeps_executable_modes_without_changing_ownership(tmp_path, monkeypatch):
    installer, spec, prefix = archive_fixture(tmp_path)

    def rejected_chown(*args, **kwargs):
        raise OSError(errno.EINVAL, "Shared filesystem rejects ownership changes")

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", rejected_chown)
    previous = os.umask(0o077)
    try:
        installer.archive(spec, prefix, "compiler")
    finally:
        os.umask(previous)
    compiler = prefix / "bin/compiler"
    assert compiler.stat().st_mode & 0o777 == 0o755
    assert compiler.stat().st_uid == os.getuid()
    assert subprocess.run([str(compiler)], check=False).returncode == 0


def test_archive_refuses_paths_outside_installation(tmp_path):
    installer, spec, prefix = archive_fixture(tmp_path, "../escape")
    with pytest.raises(tarfile.OutsideDestinationError):
        installer.archive(spec, prefix, "compiler")
    assert not (tmp_path / "escape").exists()


def test_archive_attribute_failure_is_fatal(tmp_path, monkeypatch):
    installer, spec, prefix = archive_fixture(tmp_path)

    def rejected_chmod(*args, **kwargs):
        raise OSError(errno.EPERM, "Cannot apply permissions")

    monkeypatch.setattr(os, "chmod", rejected_chmod)
    with pytest.raises(tarfile.ExtractError, match="mode"):
        installer.archive(spec, prefix, "compiler")


def test_every_catalog_lab_and_launcher_has_one_runtime():
    root, courses = catalog.discover(ROOT / "tools/regular-lab-setup.py")
    definitions = catalog.load_catalog()
    assert len(courses) == 6
    rows = [catalog.bindings(course, definitions) for course in courses.values()]
    assert sum(len(row["labs"]) for row in rows) == 110
    assert sum(len(row["launchers"]) for row in rows) == 288
    assert root == ROOT
    for course in courses.values():
        for launcher in (course / "slurm").glob("*.sbatch"):
            assert f"source tools/course_env.sh {launcher.name}" in launcher.read_text()


def test_installed_generation_is_reused_and_missing_artifact_is_repaired(tmp_path):
    definitions = {
        "components": {
            "sample": {
                "kind": "commands",
                "artifacts": ["ready"],
                "commands": [
                    {
                        "argv": [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('ready').write_text('complete')",
                        ]
                    }
                ],
            }
        }
    }
    state = State(tmp_path)
    first = Installer(tmp_path, {}, definitions, state)
    first.install("sample")
    receipt = first.records["sample"]
    second = Installer(tmp_path, {}, definitions, state)
    second.install("sample")
    assert second.records["sample"] == receipt
    (Path(receipt["prefix"]) / "ready").unlink()
    third = Installer(tmp_path, {}, definitions, state)
    third.install("sample")
    assert third.records["sample"]["prefix"] != receipt["prefix"]
    assert Path(receipt["prefix"]).is_dir()


def test_failed_install_does_not_publish_completion(tmp_path):
    definitions = {
        "components": {
            "broken": {
                "kind": "commands",
                "artifacts": ["ready"],
                "commands": [{"argv": [sys.executable, "-c", "raise SystemExit(7)"]}],
            }
        }
    }
    state = State(tmp_path)
    with pytest.raises(RuntimeError, match="exit 7"):
        Installer(tmp_path, {}, definitions, state).install("broken")
    assert not (state.records / "broken.json").exists()
    assert len(list((state.installs / "broken").iterdir())) == 1


def test_setup_lock_rejects_concurrent_writer(tmp_path):
    root = tmp_path / ".runtime"
    with setup_lock(root):
        with pytest.raises(RuntimeError, match="Another"):
            with setup_lock(root):
                pytest.fail("second installer acquired lock")


@pytest.mark.parametrize(
    "transaction",
    [
        "Remv harmless [1]",
        "Inst nvidia-driver-580 [1] (2)",
        "Inst cuda-toolkit-13-3 (13)",
        "Inst slurm-client (1)",
    ],
)
def test_system_install_rejects_platform_changes(transaction):
    with pytest.raises(RuntimeError, match="protected"):
        system.check_transaction(transaction)


def test_system_install_does_nothing_when_complete(monkeypatch):
    monkeypatch.setattr(system, "installed", lambda _: True)
    monkeypatch.setattr(system.shutil, "which", lambda _: "/usr/bin/apptainer")
    system.install_packages(
        lambda *a, **k: pytest.fail("unexpected package mutation"),
        packages=system.BASE_PACKAGES,
    )


@pytest.mark.parametrize("phase", ["system-packages", "apptainer"])
def test_failed_package_plan_keeps_private_diagnostics(tmp_path, monkeypatch, phase):
    commands = tmp_path / "bin"
    commands.mkdir()
    timeout = commands / "timeout"
    timeout.write_text(
        f"#!{sys.executable}\nimport os, sys\nos.execvp(sys.argv[3], sys.argv[3:])\n"
    )
    timeout.chmod(0o700)
    apt = commands / "apt-get"
    apt.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if '--simulate' in sys.argv:\n"
        " print('The following packages have unmet dependencies:')\n"
        " print('E: fixture dependency version mismatch', file=sys.stderr)\n"
        " sys.exit(100)\n"
        "if 'install' in sys.argv: sys.exit('unexpected installation')\n"
    )
    apt.chmod(0o700)
    repository = commands / "add-apt-repository"
    repository.write_text(f"#!{sys.executable}\n")
    repository.chmod(0o700)
    monkeypatch.setenv("PATH", str(commands))
    monkeypatch.setattr(system, "os", SimpleNamespace(geteuid=lambda: 0))
    monkeypatch.setattr(
        system,
        "installed",
        lambda name: not (phase == "system-packages" and name == "cmake"),
    )
    monkeypatch.setattr(
        system.shutil,
        "which",
        lambda _: "/usr/bin/apptainer" if phase == "system-packages" else None,
    )
    installer = Installer(tmp_path, {}, {}, State(tmp_path))
    with pytest.raises(RuntimeError) as error:
        system.install_packages(
            installer.run, packages=(*system.BASE_PACKAGES, "cmake"), apptainer=True
        )
    log = installer.logs / f"{phase}-plan.log"
    assert log.is_file(), "APT plan failure discarded its stdout and stderr"
    assert "unmet dependencies" in log.read_text()
    assert "fixture dependency version mismatch" in log.read_text()
    assert log.stat().st_mode & 0o777 == 0o600
    assert f"{phase}-plan" in str(error.value)
    assert "exit 100" in str(error.value)
    assert "fixture dependency version mismatch" not in str(error.value)
    assert not (installer.logs / f"{phase}.log").exists()


def test_all_package_mutations_defer_interruption(monkeypatch):
    monkeypatch.setattr(system, "installed", lambda _: False)
    monkeypatch.setattr(system.shutil, "which", lambda _: None)
    monkeypatch.setattr(system.os, "geteuid", lambda: 1000)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return "Inst python3.12 (3.12)" if kwargs.get("capture_output") else None

    system.install_packages(
        run, packages=(*system.BASE_PACKAGES, "cmake"), apptainer=True
    )
    assert len(calls) == 7
    assert all(
        argv[:4] == ["sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive"]
        for argv, _ in calls
    )
    assert all(kwargs["transaction"] is True for _, kwargs in calls)
    plans = [(argv, kwargs) for argv, kwargs in calls if "--simulate" in argv]
    assert len(plans) == 2
    assert all(
        argv[4:8] == ["timeout", "--kill-after=5s", "30s", "apt-get"]
        and kwargs["capture_output"] is True
        for argv, kwargs in plans
    )


def test_captured_plan_excludes_earlier_log_and_uses_installer_environment(
    tmp_path, monkeypatch
):
    installer = Installer(tmp_path, {}, {}, State(tmp_path))
    log = installer.logs / "system-packages-plan.log"
    log.write_text("Remv protected-old-plan [1]\n")
    log.chmod(0o600)
    monkeypatch.setenv("PYTHONHOME", "/unrelated/python")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/unrelated/libraries")
    output = installer.run(
        [
            sys.executable,
            "-c",
            "import os; assert 'PYTHONHOME' not in os.environ; "
            "assert 'LD_LIBRARY_PATH' not in os.environ; print('Inst cmake (1)')",
        ],
        label="system-packages-plan",
        capture_output=True,
    )
    assert output.strip() == "Inst cmake (1)"
    system.check_transaction(output)
    assert "protected-old-plan" in log.read_text()


@pytest.mark.parametrize("definition", [None, "cuda.def"])
def test_image_build_bounds_compression_without_forcing_privileges(
    tmp_path, monkeypatch, definition
):
    installer = Installer(tmp_path, {}, {}, State(tmp_path))
    prefix = tmp_path / "image"
    prefix.mkdir(mode=0o700)
    calls = []

    def run(argv, **kwargs):
        scratch = Path(kwargs["env"]["APPTAINER_TMPDIR"])
        assert scratch.is_relative_to(installer.state.cache)
        assert scratch.is_dir() and scratch.stat().st_mode & 0o777 == 0o700
        calls.append(argv)
        (prefix / "image.sif").write_bytes(b"fixture image")

    monkeypatch.setattr(installer, "run", run)
    spec = {"uri": "docker://example/image@sha256:" + "a" * 64}
    if definition:
        spec["definition"] = definition
    installer.image(spec, prefix, "cuda-image")
    assert "--fakeroot" not in calls[0]
    assert calls[0][:2] == ["apptainer", "build"]
    assert "--notest" in calls[0]
    index = calls[0].index("--mksquashfs-args")
    assert calls[0][index + 1].split() == ["-mem", "1G", "-processors", "2"]
    assert calls[0][-2] == str(prefix / "image.sif")
    expected_source = str(prefix / "container.def") if definition else spec["uri"]
    assert calls[0][-1] == expected_source


@pytest.mark.parametrize("plan", ["Remv cmake [1]", "Inst nvidia-driver-580 (1)"])
def test_successful_but_unsafe_package_plan_never_installs(monkeypatch, plan):
    monkeypatch.setattr(system, "installed", lambda name: name != "cmake")
    monkeypatch.setattr(system.shutil, "which", lambda _: "/usr/bin/apptainer")
    calls = []

    def run(argv, **kwargs):
        calls.append(kwargs["label"])
        return plan

    with pytest.raises(RuntimeError, match="protected"):
        system.install_packages(
            run, packages=(*system.BASE_PACKAGES, "cmake"), apptainer=True
        )
    assert calls == ["package-index", "system-packages-plan"]


@pytest.mark.parametrize(
    "gres,expected",
    [
        ("gpu:h100:8(S:0-1)", 8),
        ("gpu:4", 4),
        ("gpu:a100:4,gpu:h100:4", 8),
        ("gpu:h100:8(S:0-1),mps:800", 8),
        ("", 0),
    ],
)
def test_capacity_uses_configured_gres(monkeypatch, gres, expected):
    monkeypatch.setattr(
        system, "capture", lambda _: json.dumps({"nodes": [{"gres": gres}]})
    )
    assert system.capacity()[0]["gpus"] == expected


def test_hardware_skip_is_distinct_from_unknown():
    assert (
        system.applicability({"gpus": 8, "nodes": 2}, [{"gpus": 1, "hopper": True}])[0]
        is False
    )


def test_generic_gpu_type_is_unknown_and_mixed_types_count_separately(monkeypatch):
    monkeypatch.setattr(
        system,
        "capture",
        lambda _: json.dumps(
            {"nodes": [{"gres": "gpu:generic:8"}, {"gres": "gpu:h100:4,gpu:a100:4"}]}
        ),
    )
    generic, mixed = system.capacity()
    assert system.applicability({"gpus": 8, "hopper": True}, [generic])[0] is None
    assert system.applicability({"gpus": 8, "hopper": True}, [mixed])[0] is False
    assert system.applicability({"gpus": 1, "hopper": True}, [mixed])[0] is True
    assert (
        system.applicability({"gpus": 1}, [{"gpus": None, "hopper": None}])[0] is None
    )
    assert (
        system.applicability(
            {"gpus": 1, "hopper": True}, [{"gpus": 8, "hopper": False}]
        )[0]
        is False
    )


def test_runtime_shell_restores_selectors_and_ignores_stale_managed_libraries(
    tmp_path, monkeypatch, capsys
):
    record = {
        "id": "lab",
        "environment": {
            "COURSE_PYTHON": str(
                tmp_path / ".runtime/installations/new/venv/bin/python"
            ),
            "COURSE_LIBRARY_PATH": str(tmp_path / ".runtime/installations/new/lib"),
        },
    }
    monkeypatch.setattr(runtime, "load", lambda *a, **k: (tmp_path, record))
    monkeypatch.setenv(
        "LD_LIBRARY_PATH",
        str(tmp_path / ".runtime/installations/old/lib") + ":/usr/local/site/lib",
    )
    runtime.shell(tmp_path, "lab")
    code = capsys.readouterr().out
    assert "old/lib" not in code
    result = subprocess.run(
        ["bash", "-c", code + '\nprintf "%s\\n" "$COURSE_PYTHON" "$LD_LIBRARY_PATH"'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert str(tmp_path / ".runtime/installations/new/venv/bin/python") in result.stdout
    assert "/usr/local/site/lib" in result.stdout


def test_monitoring_file_is_parsed_as_data_not_executed(tmp_path):
    marker = tmp_path / "should-not-exist"
    file = tmp_path / ".course-environment.sh"
    file.write_text(
        f'export COURSE_WORKSPACE="$(touch {marker})"\nexport COURSE_METRICS_URL=x\nexport COURSE_PUSHGATEWAY=y\n'
    )
    file.chmod(0o600)
    assert runtime.monitoring(tmp_path)["COURSE_WORKSPACE"].startswith("$(touch")
    assert not marker.exists()
    file.write_text("export AWS_SECRET_ACCESS_KEY=x\n")
    with pytest.raises(ValueError, match="unexpected"):
        runtime.monitoring(tmp_path)


def test_runtime_switch_clears_previous_checkout_and_python_overrides(
    tmp_path, monkeypatch, capsys
):
    previous = tmp_path / "previous catalog"
    selected = tmp_path / "selected catalog"
    record = {
        "id": "lab",
        "environment": {
            "COURSE_PYTHON": str(selected / ".runtime/installations/new/bin/python"),
            "COURSE_LIBRARY_PATH": str(selected / ".runtime/installations/new/lib"),
        },
    }
    monkeypatch.setattr(runtime, "load", lambda *a, **k: (selected, record))
    monkeypatch.setenv("COURSE_RUNTIME_ROOT", str(previous))
    monkeypatch.setenv(
        "PATH", f"{previous}/.runtime/installations/old/bin:/usr/bin:/bin"
    )
    monkeypatch.setenv(
        "LD_LIBRARY_PATH",
        f"{previous}/.runtime/installations/old/lib:/site/lib",
    )
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        monkeypatch.setenv(name, str(previous / "python"))
    runtime.shell(selected, "lab")
    code = capsys.readouterr().out
    result = subprocess.run(
        ["bash", "-c", code + '\nprintf "%s\\n" "$PATH" "$LD_LIBRARY_PATH"; env'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert str(previous) not in result.stdout
    assert "/site/lib" in result.stdout
    assert record["environment"]["COURSE_PYTHON"] in result.stdout
    assert all(
        not line.startswith(("PYTHONPATH=", "PYTHONHOME=", "VIRTUAL_ENV="))
        for line in result.stdout.splitlines()
    )


@pytest.mark.parametrize("override", ["PYTHONPATH", "PYTHONHOME"])
def test_shell_loader_starts_without_inherited_python_overrides(tmp_path, override):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "course_runtime.py").write_text(
        "import sys\nassert sys.flags.ignore_environment\n"
        "print('export COURSE_PYTHON=/prepared/bin/python')\n"
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "python3.12").symlink_to(sys.executable)
    result = subprocess.run(
        ["bash", "-c", f'source "{ROOT}/tools/course_env.sh" example'],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            override: str(tmp_path / "invalid-python"),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_no_argument_entrypoint_dispatches_setup_without_importing_dependencies(
    tmp_path,
):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "regular-lab-setup.py").write_bytes(
        (ROOT / "tools/regular-lab-setup.py").read_bytes()
    )
    package = tools / "course_bootstrap"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "cli.py").write_text(
        "def main(script, group):\n print('automatic setup dispatched'); return 0\n"
    )
    result = subprocess.run(
        [sys.executable, "-S", str(tools / "regular-lab-setup.py")],
        cwd=tmp_path.parent,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "automatic setup dispatched"


def test_setup_never_executes_runtime_qualification(tmp_path):
    installer = Installer(tmp_path, {}, {}, State(tmp_path))
    for command in ("sbatch", "srun", "nvidia-smi", "ctest", "nsys", "ncu"):
        with pytest.raises(ValueError, match="qualification"):
            installer.run([command])


def test_installation_ignores_inherited_course_python_and_libraries(
    tmp_path, monkeypatch
):
    for name in (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
    ):
        monkeypatch.setenv(name, "/unrelated/course")
    environment = Installer(tmp_path, {}, {}, State(tmp_path)).environment()
    assert not set(environment) & {
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
    }
    assert environment["CUDA_VISIBLE_DEVICES"] == ""


def test_activation_fails_when_loader_fails(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "course_runtime.py").write_text("raise SystemExit(9)\n")
    result = subprocess.run(
        ["bash", "-c", f'source "{ROOT}/tools/course_env.sh" missing'], cwd=tmp_path
    )
    assert result.returncode != 0


@pytest.mark.parametrize(
    "available,broken", [(True, False), (False, False), (True, True)]
)
def test_controller_cold_and_warm_setup_and_hardware_skips(
    tmp_path, monkeypatch, available, broken
):
    definitions = {
        "components": {
            "shared": {
                "kind": "commands",
                "artifacts": ["ready"],
                "commands": [
                    {
                        "argv": [
                            sys.executable,
                            "-c",
                            "from pathlib import Path; Path('ready').write_text('installed')",
                        ]
                    }
                ],
            },
            "gpu": {"kind": "commands", "depends": ["shared"], "artifacts": []},
        },
        "runtimes": {
            "shared-tools": {"components": ["shared"]},
            "lab": {
                "components": ["gpu"],
                "needs": {"gpus": 8, "nodes": 2},
                "environment": {"COURSE_PYTHON": "{shared}/ready"},
            },
        },
    }
    monkeypatch.setattr(
        controller.definitions, "discover", lambda _: (tmp_path, {"example": tmp_path})
    )
    monkeypatch.setattr(controller.definitions, "load_catalog", lambda: definitions)
    monkeypatch.setattr(
        controller.definitions,
        "bindings",
        lambda *a: {"launchers": {"lab.sbatch": "lab"}},
    )
    monkeypatch.setattr(controller.definitions, "fingerprint", lambda *a: "source")
    monkeypatch.setattr(controller, "ubuntu", lambda: {"os": "test"})
    monkeypatch.setattr(
        controller,
        "capacity",
        lambda: [{"gpus": 8 if available else 1, "hopper": True}] * 2,
    )
    monkeypatch.setattr(controller, "install_packages", lambda *a, **k: None)
    if broken:
        definitions["components"]["gpu"]["commands"] = [
            {"argv": [sys.executable, "-c", "raise SystemExit(7)"]}
        ]

    def prepare(**kwargs):
        (tmp_path / ".runtime").mkdir(mode=0o700, exist_ok=True)

    def reconcile():
        if broken:
            with pytest.raises(RuntimeError, match="could not be installed"):
                controller.setup(
                    tmp_path / "tools/regular-lab-setup.py",
                    prepare,
                    plan={"group": "regular", "runtimes": ["shared-tools", "lab"]},
                )
        else:
            controller.setup(
                tmp_path / "tools/regular-lab-setup.py",
                prepare,
                plan={"group": "regular", "runtimes": ["shared-tools", "lab"]},
            )

    reconcile()
    state = State(tmp_path)
    before = (state.records / "shared.json").read_bytes()
    reconcile()
    assert before == (state.records / "shared.json").read_bytes()
    record = json.loads((state.runtimes / "lab.json").read_text())
    assert record["status"] == (
        "failed" if broken else "installed" if available else "skipped"
    )
    assert (state.records / "gpu.json").exists() == (available and not broken)


def test_synchronized_container_definition_invalidates_completed_image(
    tmp_path, monkeypatch
):
    from course_bootstrap import installer as module

    implementation = tmp_path / "bootstrap"
    import shutil

    shutil.copytree(
        ROOT / "tools/course_bootstrap",
        implementation,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    monkeypatch.setattr(catalog, "__file__", str(implementation / "catalog.py"))
    definition = implementation / "cuda.def"
    definition.write_text("first definition")
    monkeypatch.setattr(module, "__file__", str(implementation / "installer.py"))

    def build(self, spec, prefix, name):
        (prefix / "image.sif").write_text(definition.read_text())

    monkeypatch.setattr(Installer, "image", build)
    spec = {
        "components": {
            "image": {
                "kind": "image",
                "definition": "cuda.def",
                "artifacts": ["image.sif"],
            }
        }
    }
    state = State(tmp_path)
    first = Installer(tmp_path, {}, spec, state)
    first.install("image")
    definition.write_text("changed definition")
    second = Installer(tmp_path, {}, spec, state)
    second.install("image")
    assert first.records["image"]["prefix"] != second.records["image"]["prefix"]


def test_timed_out_install_terminates_its_process_group(tmp_path, monkeypatch):
    from course_bootstrap import installer as module

    calls = []

    class Process:
        pid = 123456

        def __init__(self, *args, **kwargs):
            assert kwargs["start_new_session"] is True

        def wait(self, timeout=None):
            if timeout:
                raise subprocess.TimeoutExpired("fixture", timeout)
            calls.append("waited")

    monkeypatch.setattr(module.subprocess, "Popen", Process)
    monkeypatch.setattr(module.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    with pytest.raises(RuntimeError, match=r"timed out; inspect .*install\.log"):
        Installer(tmp_path, {}, {}, State(tmp_path)).run(["fixture"])
    assert calls == [(123456, module.signal.SIGKILL), "waited"]


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
@pytest.mark.parametrize("transaction", [False, True])
def test_interrupted_setup_stops_installers_before_releasing_lock(
    tmp_path, signum, transaction
):
    child = """
import os, subprocess, sys, time
from pathlib import Path
grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path('children').write_text(f'{os.getpid()} {grandchild.pid}')
if sys.argv[1] == 'True':
    while not Path('finish-transaction').exists():
        time.sleep(0.02)
    grandchild.terminate()
    grandchild.wait()
    Path('transaction-complete').touch()
else:
    time.sleep(60)
"""
    driver = """
import sys
from pathlib import Path
from course_bootstrap import controller
from course_bootstrap.installer import Installer
from course_bootstrap.state import State, setup_lock
root = Path(sys.argv[1])
def fixture_reconcile(*_args, **_kwargs):
    state = State(root)
    with setup_lock(state.root):
        Installer(root, {}, {}, state).run(
            [sys.executable, '-c', sys.argv[2], sys.argv[3]],
            cwd=root, transaction=sys.argv[3] == 'True')
controller.reconcile = fixture_reconcile
controller.setup(None, None, plan={})
"""
    process = subprocess.Popen(
        [sys.executable, "-c", driver, str(tmp_path), child, str(transaction)],
        env={**os.environ, "PYTHONPATH": str(ROOT / "tools")},
    )
    children = []
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ready = tmp_path / "children"
            if ready.exists() and len(ready.read_text().split()) == 2:
                children = [int(value) for value in ready.read_text().split()]
                break
            time.sleep(0.02)
        assert children, "Installer fixture failed to start"
        with pytest.raises(RuntimeError, match="Another course setup"):
            with setup_lock(tmp_path / ".runtime"):
                pass
        process.send_signal(signum)
        if transaction:
            time.sleep(0.1)
            assert process.poll() is None
            with pytest.raises(RuntimeError, match="Another course setup"):
                with setup_lock(tmp_path / ".runtime"):
                    pass
            (tmp_path / "finish-transaction").touch()
        assert process.wait(timeout=10) == 128 + signum
        assert (tmp_path / "transaction-complete").exists() is transaction
        with setup_lock(tmp_path / ".runtime"):
            for pid in children:
                status = subprocess.run(
                    ["ps", "-o", "stat=", "-p", str(pid)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                # A dead orphan may briefly await its host's reaper.
                assert not status.stdout.strip() or status.stdout.strip().startswith(
                    "Z"
                )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for pid in children:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def runtime_fixture(root, monkeypatch):
    state = State(root)
    prefix = state.generation("sample", "fingerprint")
    (prefix / "binary").write_text("binary")
    component = state.commit("sample", "fingerprint", prefix, ["binary"], {})
    record = {
        "schema": "course-runtime/v1",
        "id": "sample",
        "root": str(root),
        "fingerprint": "source",
        "status": "installed",
        "components": {"sample": component},
        "environment": {"COURSE_PYTHON": str(prefix / "binary")},
    }
    atomic_json(state.runtimes / "sample.json", record)
    monkeypatch.setattr(
        runtime,
        "context",
        lambda _: (
            root,
            {"example": root},
            {
                "runtimes": {"sample": {"group": "regular"}},
                "components": {"sample": {"kind": "commands"}},
            },
        ),
    )
    monkeypatch.setattr(
        runtime.definitions, "bindings", lambda *a: {"labs": {"lab": "sample"}}
    )
    monkeypatch.setattr(runtime.definitions, "fingerprint", lambda *a: "source")
    return state, record


def test_runtime_load_from_actual_private_record(tmp_path, monkeypatch):
    _, record = runtime_fixture(tmp_path, monkeypatch)
    assert runtime.load(tmp_path, "lab")[1] == record


@pytest.mark.parametrize("defect", ["stale", "missing", "symlink", "skipped"])
def test_runtime_rejects_stale_or_unsafe_record(tmp_path, monkeypatch, defect):
    state, record = runtime_fixture(tmp_path, monkeypatch)
    if defect == "stale":
        record["fingerprint"] = "old-source"
    if defect == "skipped":
        record.update(status="skipped", reason="unsupported hardware")
    if defect == "missing":
        Path(record["environment"]["COURSE_PYTHON"]).unlink()
    if defect == "symlink":
        directory = Path(record["components"]["sample"]["prefix"])
        directory.rename(directory.with_name("moved"))
        directory.symlink_to(directory.with_name("moved"), target_is_directory=True)
    atomic_json(state.runtimes / "sample.json", record)
    with pytest.raises(ValueError):
        runtime.load(tmp_path, "lab")


def test_immutable_image_identity_survives_new_component_generation(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        runtime.definitions,
        "bindings",
        lambda *a: {"launchers": {"build_and_test.sbatch": "image-runtime"}},
    )
    state = State(tmp_path)
    directory = tmp_path / ".runtime/images"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(
        runtime,
        "context",
        lambda _: (
            tmp_path,
            {"example": tmp_path},
            {
                "components": {"image": {"kind": "image"}},
                "runtimes": {
                    "image-runtime": {
                        "group": "cuda",
                        "components": ["image"],
                        "optional": True,
                    }
                },
            },
        ),
    )
    identities = []
    for value in ("old", "new"):
        prefix = state.generation("image", value)
        (prefix / "image.sif").write_text(value)
        from course_bootstrap.state import file_digest

        sha = file_digest(prefix / "image.sif")
        identity = "sif://image@sha256:" + sha
        record = state.commit(
            "image",
            value,
            prefix,
            ["image.sif"],
            {"image": str(prefix / "image.sif"), "identity": identity},
        )
        atomic_json(directory / f"image-{sha}.json", record)
        identities.append((identity, str(prefix / "image.sif")))
    for identity, expected in identities:
        runtime.image(tmp_path, identity)
        assert capsys.readouterr().out.strip() == expected
