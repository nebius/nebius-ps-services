"""Unified orchestration with real local processes and bounded fake installations."""

import io
import json
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from course_bootstrap import catalog, controller, orchestration, system
from course_bootstrap.installer import Installer
from course_bootstrap.state import State, setup_lock

ROOT = Path(__file__).resolve().parents[1]


def command_component(*, depends=(), fail=False, packages=()):
    return {
        "kind": "commands",
        "depends": list(depends),
        "system_packages": list(packages),
        "artifacts": ["ready"],
        "commands": [
            {
                "argv": [
                    sys.executable,
                    "-c",
                    "raise SystemExit(7)"
                    if fail
                    else "from pathlib import Path; Path('ready').write_text('ready')",
                ]
            }
        ],
    }


@pytest.fixture
def setup_fixture(tmp_path, monkeypatch):
    definitions = {
        "components": {
            "native": command_component(),
            "broken": command_component(fail=True),
            "dependent": command_component(depends=["broken"]),
            "image": {
                "kind": "image",
                "artifacts": ["image.sif"],
                "uri": "docker://fixture@sha256:" + "0" * 64,
            },
            "image-dependent": command_component(depends=["image"]),
        },
        "runtimes": {},
    }
    for name in definitions["components"]:
        definitions["runtimes"][name] = {
            "components": [name],
            "environment": {"COURSE_PUBLISH_PYTHON": sys.executable},
        }
    monkeypatch.setattr(catalog, "discover", lambda _: (tmp_path, {}))
    monkeypatch.setattr(catalog, "load_catalog", lambda: definitions)
    monkeypatch.setattr(controller, "ubuntu", lambda: {})
    monkeypatch.setattr(controller, "capacity", lambda: [])
    monkeypatch.setattr(controller, "install_packages", lambda *a, **kw: None)
    monkeypatch.setattr(controller.mount_root, "prepare", lambda: None)
    monkeypatch.setattr(
        Installer,
        "image",
        lambda self, spec, prefix, name: (prefix / "image.sif").write_text(
            "fake image"
        ),
    )

    def run(names):
        controller.setup(
            tmp_path / "tools/lab-setup.py",
            lambda **kw: None,
            plan={"runtimes": names, "courses": {"fixture": names}},
        )

    return SimpleNamespace(
        root=tmp_path,
        definitions=definitions,
        run=run,
        report=lambda: json.loads((tmp_path / ".runtime/setup.json").read_text()),
    )


def test_partial_success_initializes_publication_and_preserves_it(setup_fixture, monitoring_settings):
    f = setup_fixture
    settings = f.root / ".course-environment.sh"
    settings.write_text(monitoring_settings())
    settings.chmod(0o600)
    names = ["native", "broken", "dependent", "image-dependent"]
    with pytest.raises(RuntimeError, match="Successful labs"):
        f.run(names)
    report = f.report()
    assert report["components"]["native"]["status"] == "installed"
    assert report["components"]["image"]["status"] == "installed"
    assert report["components"]["broken"]["status"] == "failed"
    assert report["components"]["dependent"]["status"] == "dependency-blocked"
    publication = f.root / ".runtime/publications"
    (publication / "runs/7.json").write_text("retained history")
    before = {
        p.relative_to(publication): p.read_bytes()
        for p in publication.rglob("*")
        if p.is_file()
    }
    state = State(f.root)
    native = (state.records / "native.json").read_bytes()
    with pytest.raises(RuntimeError):
        f.run(names)
    assert f.report()["components"]["native"]["status"] == "reused"
    assert (state.records / "native.json").read_bytes() == native
    assert {
        p.relative_to(publication): p.read_bytes()
        for p in publication.rglob("*")
        if p.is_file()
    } == before


@pytest.mark.parametrize("fatal", [False, True])
def test_namespace_failure_is_isolated_and_native_readiness_survives(
    setup_fixture, monkeypatch, fatal
):
    def fail():
        if fatal:
            os.write(2, b"namespace fixture diagnostic\n")
            os._exit(91)
        raise RuntimeError("namespace prerequisites missing")

    monkeypatch.setattr(controller.mount_root, "prepare", fail)
    f = setup_fixture
    with pytest.raises(RuntimeError):
        f.run(["native", "image-dependent"])
    assert (
        json.loads((f.root / ".runtime/runtimes/native.json").read_text())["status"]
        == "installed"
    )
    assert f.report()["components"]["image"]["status"] == "failed"
    assert f.report()["components"]["image-dependent"]["status"] == "dependency-blocked"
    if fatal:
        assert (
            "namespace fixture diagnostic"
            in (f.root / ".runtime/setup-logs/container-worker.log").read_text()
        )
    with setup_lock(f.root / ".runtime"):
        pass


def test_image_failure_continues_independent_images(setup_fixture, monkeypatch):
    f = setup_fixture
    f.definitions["components"]["other-image"] = dict(
        f.definitions["components"]["image"]
    )
    f.definitions["runtimes"]["other-image"] = {"components": ["other-image"]}

    def image(self, spec, prefix, name):
        if name == "image":
            raise RuntimeError("ordinary image build failure")
        (prefix / "image.sif").write_text("independent")

    monkeypatch.setattr(Installer, "image", image)
    with pytest.raises(RuntimeError):
        f.run(["native", "image-dependent", "other-image"])
    assert f.report()["components"]["other-image"]["status"] == "installed"


def test_worker_message_cannot_publish_missing_receipt(setup_fixture, monkeypatch):
    def forged(_operation, _progress, _cancel, **_kwargs):
        return {"image": {"status": "installed", "reason": "claimed"}}, 0, None

    monkeypatch.setattr(controller, "supervise", forged)
    with pytest.raises(RuntimeError):
        setup_fixture.run(["native", "image"])
    assert setup_fixture.report()["components"]["image"]["status"] == "failed"


@pytest.mark.parametrize("defect", ["checksum", "unsafe-plan", "broken-packages"])
def test_unsafe_failure_stops_later_components(setup_fixture, monkeypatch, defect):
    f = setup_fixture
    f.definitions["components"]["a-first"] = command_component()
    f.definitions["runtimes"]["a-first"] = {"components": ["a-first"]}

    def failed(*args, **kwargs):
        if defect == "unsafe-plan":
            raise system.UnsafePackages("protected platform")
        raise RuntimeError("ordinary package failure")

    if defect == "checksum":
        original = Installer.install

        def install(self, name):
            if name == "a-first":
                raise RuntimeError("Download checksum mismatch: fixture")
            original(self, name)

        monkeypatch.setattr(Installer, "install", install)
    else:
        monkeypatch.setattr(controller, "install_packages", failed)
        monkeypatch.setattr(system, "capture", lambda _: "incomplete package")
    with pytest.raises(RuntimeError):
        f.run(["a-first", "native"])
    assert not (f.root / ".runtime/components/native.json").exists()
    assert f.report()["errors"]


def test_ordinary_prerequisite_failure_checks_health_and_continues_independent_work(
    setup_fixture, monkeypatch
):
    f = setup_fixture
    f.definitions["components"]["broken"]["system_packages"] = ["missing-package"]
    checked = []

    def install(run, *, packages, apptainer):
        if "missing-package" in packages:
            raise RuntimeError("ordinary package failure")

    monkeypatch.setattr(controller, "install_packages", install)
    monkeypatch.setattr(system, "package_health", lambda: checked.append("health"))
    monkeypatch.setattr(
        system,
        "installed",
        lambda name: checked.append(name) or name != "missing-package",
    )
    with pytest.raises(RuntimeError):
        f.run(["broken", "dependent", "native"])
    assert "health" in checked and "missing-package" in checked
    assert f.report()["components"]["native"]["status"] == "installed"


def test_worker_reuse_damage_and_native_first_order(setup_fixture):
    f = setup_fixture
    names = ["image-dependent", "native"]
    f.run(names)
    state = State(f.root)
    before = {p.name: p.read_bytes() for p in state.records.glob("*.json")}
    f.run(names)
    assert all(v["status"] == "reused" for v in f.report()["components"].values())
    assert before == {p.name: p.read_bytes() for p in state.records.glob("*.json")}
    image = json.loads((state.records / "image.json").read_text())
    (Path(image["prefix"]) / "image.sif").write_text("corruption")
    f.run(names)
    assert before["native.json"] == (state.records / "native.json").read_bytes()
    assert before["image.json"] != (state.records / "image.json").read_bytes()
    assert (
        before["image-dependent.json"]
        != (state.records / "image-dependent.json").read_bytes()
    )
    assert Path(image["prefix"]).is_dir()
    native, images = orchestration.partitions(
        f.definitions, catalog.order(f.definitions, ["image-dependent", "native"])
    )
    assert native == ["native"] and images == ["image", "image-dependent"]


@pytest.mark.parametrize("after_native", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
def test_interrupted_rerun_preserves_verified_readiness(
    setup_fixture, monkeypatch, after_native, cancelled
):
    f = setup_fixture
    names = ["native", "image-dependent"]
    f.run(names)
    state = State(f.root)
    before = {p.name: p.read_bytes() for p in state.runtimes.glob("*.json")}
    original = controller.phase

    def stop(installer, components, *args, **kwargs):
        if after_native:
            original(installer, components, *args, **kwargs)
        if cancelled:
            raise SystemExit(143)
        raise ValueError("fatal setup stop")

    monkeypatch.setattr(controller, "phase", stop)
    monkeypatch.setattr(
        Installer,
        "install",
        lambda *a: pytest.fail("Revalidation attempted installation"),
    )
    with pytest.raises(SystemExit if cancelled else RuntimeError) as stopped:
        f.run(names)
    if cancelled:
        assert stopped.value.code == 143
    assert before == {p.name: p.read_bytes() for p in state.runtimes.glob("*.json")}
    assert all(row["status"] == "reused" for row in f.report()["runtimes"])
    assert f.report()["errors"]


@pytest.mark.parametrize(
    "component,defect",
    [("native", "artifact"), ("image", "artifact"), ("image", "source")],
)
def test_interrupted_rerun_does_not_promote_damaged_or_missing_components(
    setup_fixture, monkeypatch, component, defect
):
    f = setup_fixture
    f.run(["native", "image-dependent"])
    state = State(f.root)
    record = json.loads((state.records / f"{component}.json").read_text())
    if defect == "artifact":
        (Path(record["prefix"]) / next(iter(record["artifacts"]))).unlink()
    else:
        f.definitions["components"][component]["uri"] += "changed"

    def stop(*args, **kwargs):
        raise SystemExit(143)

    monkeypatch.setattr(controller, "phase", stop)
    with pytest.raises(SystemExit):
        f.run(["native", "broken", "image-dependent"])
    rows = {r["runtime"]: r for r in f.report()["runtimes"]}
    assert (
        rows[component if component == "native" else "image-dependent"]["status"]
        not in orchestration.SUCCESS
    )
    assert rows["broken"]["status"] not in orchestration.SUCCESS
    assert (
        rows["image-dependent" if component == "native" else "native"]["status"]
        == "reused"
    )
    assert not (state.records / "broken.json").exists()


def wait_for(path, process, timeout=8):
    end = time.monotonic() + timeout
    while not path.exists():
        assert process.poll() is None, process.communicate()
        if time.monotonic() > end:
            pytest.fail(f"Timed out waiting for {path.name}")
        time.sleep(0.02)


WORKER_DRIVER = r'''
import os, signal, sys, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from course_bootstrap import worker
from course_bootstrap.installer import Installer
from course_bootstrap.progress import Progress
from course_bootstrap.state import State, setup_lock
root, mode = Path(sys.argv[2]), sys.argv[3]
state = State(root)
real_fork = os.fork
def checked_fork():
    assert not any(t.name == 'setup-progress' for t in threading.enumerate())
    pid = real_fork()
    if pid and mode == 'creation':
        os.kill(os.getpid(), signal.SIGTERM)
    return pid
worker.os.fork = checked_fork
def operation(cancel, channel):
    assert threading.active_count() == 1
    (root / 'worker.pid').write_text(str(os.getpid()))
    if mode in ('creation', 'namespace', 'between'):
        if mode == 'namespace':
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, worker.SIGNALS)
            (root / 'ready').touch()
            while not (root / 'release').exists(): time.sleep(.01)
            signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        else:
            (root / 'ready').touch()
            while cancel.signum is None: time.sleep(.01)
        cancel.check()
        (root / 'scheduled-after-cancel').touch()
    else:
        code = """import os, subprocess, sys, time
from pathlib import Path
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
Path('descendant.pid').write_text(str(p.pid))
Path('ready').touch()
while not Path('release').exists(): time.sleep(.01)
p.terminate(); p.wait()
Path('finished').touch()
"""
        Installer(root, {}, {}, state).run([sys.executable, '-c', code], cwd=root, transaction=mode == 'transaction')
        cancel.check()
        (root / 'scheduled-after-cancel').touch()
with setup_lock(state.root), worker.Cancellation() as cancel, Progress() as progress:
    result = worker.supervise(operation, progress, cancel)
    (root / 'settled').write_text(str(result[1]))
    cancel.check()
'''


@pytest.mark.parametrize(
    "mode", ["creation", "namespace", "between", "ordinary", "transaction"]
)
def test_worker_cancellation_keeps_lock_and_cleans_children(tmp_path, mode):
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-c",
            WORKER_DRIVER,
            str(ROOT / "tools"),
            str(tmp_path),
            mode,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        wait_for(tmp_path / "worker.pid", process)
        if mode != "creation":
            wait_for(tmp_path / "ready", process)
            process.send_signal(signal.SIGTERM)
        if mode in {"namespace", "transaction"}:
            time.sleep(0.15)
            assert process.poll() is None
            with pytest.raises(RuntimeError, match="Another course setup"):
                with setup_lock(tmp_path / ".runtime"):
                    pass
            process.send_signal(signal.SIGINT)
            (tmp_path / "release").touch()
        out, err = process.communicate(timeout=8)
        assert process.returncode == 128 + signal.SIGTERM, (out, err)
        assert not (tmp_path / "scheduled-after-cancel").exists()
        assert (tmp_path / "finished").exists() is (mode == "transaction")
        assert (tmp_path / "settled").exists()
        with setup_lock(tmp_path / ".runtime"):
            pass
        if (tmp_path / "descendant.pid").exists():
            pid = (tmp_path / "descendant.pid").read_text()
            status = subprocess.run(
                ["ps", "-o", "stat=", "-p", pid], capture_output=True, text=True
            ).stdout.strip()
            assert not status or status.startswith("Z")
    finally:
        (tmp_path / "release").touch()
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.communicate(timeout=8)


def test_spinner_animates_on_pty_and_clears_terminal(tmp_path):
    master, slave = pty.openpty()
    code = "import sys,time;sys.path.insert(0,sys.argv[1]);from course_bootstrap.progress import Progress\nwith Progress(interval=.03) as p:\n p.update('slow fixture');time.sleep(.25);p.message('done')"
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(ROOT / "tools")],
        stdout=slave,
        stderr=slave,
        env={**os.environ, "TERM": "xterm"},
    )
    os.close(slave)
    chunks = []
    try:
        while select.select([master], [], [], 2)[0]:
            try:
                data = os.read(master, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        assert process.wait(timeout=3) == 0
    finally:
        os.close(master)
    output = b"".join(chunks).decode()
    assert output.count("\x1b[2K") >= 4
    assert all(frame + " slow fixture" in output for frame in "|/-\\")
    assert output.endswith("done\r\n")


def test_redirected_progress_is_periodic_plain_text():
    from course_bootstrap.progress import Progress

    stream = io.StringIO()
    with Progress(stream=stream, interval=0.02, plain_interval=0.04) as progress:
        progress.update(
            "slow fixture", component="build", total=2, log="private/build.log"
        )
        time.sleep(0.15)
    output = stream.getvalue()
    assert output.count("slow fixture") >= 3
    assert "\x1b" not in output and "\r" not in output
    assert "0/2 components" in output and "private/build.log" in output


def test_worker_creation_error_restores_signal_mask_and_progress(monkeypatch):
    from course_bootstrap import worker
    from course_bootstrap.progress import Progress

    before = signal.pthread_sigmask(signal.SIG_BLOCK, [])

    def unavailable():
        raise OSError("no pipe descriptors")

    monkeypatch.setattr(worker.os, "pipe", unavailable)
    try:
        with (
            worker.Cancellation() as cancel,
            Progress(stream=io.StringIO()) as progress,
        ):
            with pytest.raises(OSError, match="pipe"):
                worker.supervise(lambda *a: None, progress, cancel)
            assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == before
            assert progress.thread is not None and progress.thread.is_alive()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, before)


def test_worker_rejects_fifo_log_without_blocking_cancellation(tmp_path):
    log = tmp_path / "container-worker.log"
    os.mkfifo(log, 0o600)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            """
import io, signal, sys
sys.path.insert(0, sys.argv[1])
from course_bootstrap import worker
from course_bootstrap.progress import Progress
before = signal.pthread_sigmask(signal.SIG_BLOCK, [])
def unexpected_fork():
    raise AssertionError('Unsafe log reached worker creation')
worker.os.fork = unexpected_fork
with worker.Cancellation() as cancel, Progress(stream=io.StringIO()) as progress:
    try:
        worker.supervise(lambda *args: None, progress, cancel, log=sys.argv[2])
    except (OSError, ValueError):
        pass
    else:
        raise AssertionError('Unsafe log was accepted')
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == before
    assert progress.thread is not None and progress.thread.is_alive()
""",
            str(ROOT / "tools"),
            str(log),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_transitive_failure_is_dependency_blocked_in_course_summary(setup_fixture):
    with pytest.raises(RuntimeError):
        setup_fixture.run(["broken", "dependent", "native"])
    rows = {r["runtime"]: r["status"] for r in setup_fixture.report()["runtimes"]}
    assert rows == {
        "broken": "failed",
        "dependent": "dependency-blocked",
        "native": "installed",
    }
    assert setup_fixture.report()["courses"]["fixture"]["dependency-blocked"] == 1


def test_parent_renderer_failure_reaps_worker_and_releases_lock(tmp_path):
    from course_bootstrap import worker
    from course_bootstrap.progress import Progress

    class BrokenRestart(Progress):
        def start(self):
            if hasattr(self, "started_once"):
                raise RuntimeError("renderer thread unavailable")
            self.started_once = True
            super().start()

    def operation(cancel, channel):
        while cancel.signum is None:
            time.sleep(0.01)
        cancel.check()

    before = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    state = State(tmp_path)
    with (
        setup_lock(state.root),
        worker.Cancellation() as cancel,
        BrokenRestart(stream=io.StringIO()) as progress,
    ):
        with pytest.raises(RuntimeError, match="renderer thread"):
            worker.supervise(operation, progress, cancel)
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == before
    with setup_lock(state.root):
        pass
    with pytest.raises(ChildProcessError):
        os.waitpid(-1, os.WNOHANG)


@pytest.mark.parametrize(
    "missing",
    [
        None,
        "tools/install_fabric_tools.py",
        "tools/course_publication.py",
        "tools/course_bootstrap/worker.py",
        "tools/course_bootstrap/receipts.py",
    ],
)
def test_retirement_requires_verified_complete_replacements(
    tmp_path, monkeypatch, missing
):
    import hashlib
    import verify_source_sync
    from course_bootstrap import retirement

    definitions = json.loads((ROOT / "tools/course_bootstrap/retired.json").read_text())
    approved = tmp_path / "approved"
    approved.mkdir()
    (approved / "retired.json").write_text(
        json.dumps({"regular-lab-setup.py": [hashlib.sha256(b"old").hexdigest()]})
    )
    monkeypatch.setattr(retirement, "__file__", str(approved / "retirement.py"))
    files = {}
    for name in definitions["replacement_files"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / name).read_bytes())
        if name != missing:
            files[name] = verify_source_sync.identity(path)
    old = tmp_path / "tools/regular-lab-setup.py"
    old.write_bytes(b"old")
    monkeypatch.setattr(
        sys, "argv", ["verify_source_sync.py", "verify", "--root", str(tmp_path)]
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"schema": "course-source-sync/v1", "files": files})),
    )
    verify_source_sync.main()
    assert old.exists() is (missing is not None)


def test_cancellation_during_discovery_never_starts_installation(monkeypatch):
    from course_bootstrap import cli, selection

    called = []
    monkeypatch.setattr(sys, "argv", ["lab-setup.py"])
    monkeypatch.setattr(cli.catalog, "discover", lambda _: (ROOT, {}))
    monkeypatch.setattr(cli.catalog, "load_catalog", lambda: {})

    def discover(*args, **kwargs):
        os.kill(os.getpid(), signal.SIGTERM)
        return {"runtimes": []}

    monkeypatch.setattr(selection, "resolve", discover)
    monkeypatch.setattr(
        controller, "setup", lambda *args, **kwargs: called.append(True)
    )
    with pytest.raises(SystemExit) as error:
        cli.main(ROOT / "tools/lab-setup.py")
    assert error.value.code == 128 + signal.SIGTERM
    assert not called
    import threading

    assert not any(t.name == "setup-progress" for t in threading.enumerate())
