"""Private handoff invariants independent of SSH and Kubernetes fixtures."""

import copy
import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import sys
import time

import pytest

from course_bootstrap import handoff as h, sync_workstation as w
from course_bootstrap.state import setup_lock


@pytest.fixture
def receipt():
    return {"cluster_identity": {"kubernetes_uid": "cluster-one"}, "vmagent_uid": "agent-one",
            "vmsingle_uid": "metrics-one", "service_uid": "metrics-service-one",
            "pushgateway_service_uid": "push-one", "metrics_url": "http://metrics.example:8428",
            "pushgateway_url": "http://push.example:9091"}


@pytest.fixture
def destination(tmp_path, receipt):
    root = tmp_path / "courses"
    root.mkdir()
    (root / "source.py").write_text("# verified source\n")
    request = {"content": h.render("workspace-one", receipt), "previous_digest": None,
               "sources": {"schema": "course-source-sync/v1", "files": {
                   "source.py": {"sha256": h.sha("# verified source\n"), "executable": False}}}}
    return root, request


def test_receive_atomic_repeat_and_result_preservation(destination):
    root, request = destination
    result = root / "results/original.json"
    result.parent.mkdir()
    result.write_text("immutable result")
    expected = h.sha(request["content"])
    assert h.receive(root, request) == expected
    assert h.inspect(root)["digest"] == expected
    settings = root / ".course-environment.sh"
    assert settings.stat().st_mode & 0o777 == 0o600
    before = settings.stat().st_mtime_ns
    request["previous_digest"] = expected
    assert h.receive(root, request) == expected
    assert settings.stat().st_mtime_ns == before
    assert result.read_text() == "immutable result"


@pytest.mark.parametrize("field", ["vmagent_uid", "vmsingle_uid", "service_uid", "pushgateway_service_uid", "cluster_identity"])
def test_replacement_with_same_urls_is_rejected(destination, receipt, field):
    root, request = destination
    h.receive(root, request)
    previous = (root / ".course-environment.sh").read_bytes()
    changed = copy.deepcopy(receipt)
    changed[field] = {"kubernetes_uid": "replacement"} if field == "cluster_identity" else "replacement"
    request.update(previous_digest=h.sha(request["content"]), content=h.render("workspace-one", changed))
    with pytest.raises(ValueError, match="replaced"):
        h.receive(root, request)
    assert (root / ".course-environment.sh").read_bytes() == previous


@pytest.mark.parametrize("defect", ["unsafe-mode", "symlink", "directory", "oversized", "malformed", "hardlink"])
def test_inspection_rejects_unsafe_settings(destination, defect):
    root, request = destination
    file = root / ".course-environment.sh"
    if defect == "directory":
        file.mkdir()
    elif defect == "symlink":
        file.symlink_to(root / "missing")
    else:
        file.write_text(request["content"])
        file.chmod(0o600)
        if defect == "unsafe-mode":
            file.chmod(0o644)
        elif defect == "hardlink":
            os.link(file, root / "duplicate")
        elif defect == "oversized":
            file.write_text("#" * (h.LIMIT + 1))
        elif defect == "malformed":
            file.write_text("export COURSE_WORKSPACE=x\n")
    with pytest.raises((OSError, ValueError)):
        h.inspect(root)


@pytest.mark.parametrize("marker", ["v2", "duplicate", "oversized", "bad-identity", "space", "tab"])
def test_malformed_comment_is_not_ignored(receipt, marker):
    content = h.render("workspace-one", receipt)
    if marker in {"space", "tab"}:
        content = (" " if marker == "space" else "\t") + content
    elif marker == "v2":
        content = content.replace("/v1", "/v2")
    elif marker == "duplicate":
        content += content.splitlines()[0] + "\n"
    elif marker == "oversized":
        content = "#" * h.LIMIT + content
    else:
        content = content.replace('"cluster-one"', '"$(touch bad)"')
    with pytest.raises(ValueError):
        h.parse(content)


def test_unmarked_settings_are_rejected_without_migration(destination):
    root, request = destination
    file = root / ".course-environment.sh"
    file.write_text(request["content"].split("\n", 1)[1])
    file.chmod(0o600)
    before = file.read_bytes()
    for action in (lambda: h.inspect(root), lambda: h.receive(root, request)):
        with pytest.raises(ValueError, match="identity is required"):
            action()
        assert file.read_bytes() == before


def test_source_and_lock_failure_leave_settings_unchanged(destination):
    root, request = destination
    h.receive(root, request)
    before = h.inspect(root)
    request["previous_digest"] = before["digest"]
    with setup_lock(root / ".runtime"), pytest.raises(RuntimeError, match="Another"):
        h.receive(root, request)
    (root / "source.py").write_text("changed")
    with pytest.raises(ValueError, match="source differs"):
        h.receive(root, request)
    assert h.inspect(root) == before


def test_atomic_replace_failure_preserves_previous(destination, monkeypatch):
    root, request = destination
    file = root / ".course-environment.sh"
    file.write_text("# existing valid settings\n" + request["content"])
    file.chmod(0o600)
    before = h.inspect(root)
    request["previous_digest"] = before["digest"]
    monkeypatch.setattr(h.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError):
        h.receive(root, request)
    assert h.inspect(root) == before
    assert not list(root.glob(".course-settings-*"))


def test_changed_preflight_is_rejected(destination):
    root, request = destination
    h.receive(root, request)
    with pytest.raises(ValueError, match="changed during"):
        h.receive(root, request)


def test_corrupt_publication_is_not_reset(destination):
    root, request = destination
    h.receive(root, request)
    publication = root / ".runtime/publications"
    publication.mkdir(mode=0o700)
    file = publication / "identity.json"
    file.write_text("corrupt")
    request["previous_digest"] = h.inspect(root)["digest"]
    with pytest.raises((ValueError, FileNotFoundError)):
        h.receive(root, request)
    assert file.read_text() == "corrupt"


def test_local_only_refuses_configured_destination(destination):
    root, request = destination
    h.receive(root, request)
    with pytest.raises(ValueError, match="unconfigured destination"):
        h.receive(root, {"local_only": True, "sources": request["sources"]})


def test_lost_ack_is_resolved_only_by_new_digest(destination, monkeypatch):
    _, request = destination
    calls = []
    def remote(profile, action, payload=None):
        calls.append(action)
        if action == "receive":
            raise RuntimeError("lost acknowledgment")
        return {"digest": h.sha(request["content"])}
    monkeypatch.setattr(w, "remote", remote)
    w.deliver({}, request)
    assert calls == ["receive", "inspect"]
    request["previous_digest"] = h.sha(request["content"])
    with pytest.raises(RuntimeError, match="unresolved"):
        w.deliver({}, request)


def test_unresolved_delivery_blocks_login(destination, monkeypatch):
    _, request = destination
    monkeypatch.setattr(w, "remote", lambda *a, **k: {"digest": "different"})
    with pytest.raises(RuntimeError, match="unresolved"):
        w.deliver({}, request)


def test_saved_connection_ignores_changed_environment(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("fixture")
    kube = tmp_path / "kubeconfig"
    kube.write_text("fixture")
    saved = dict(config=str(config), target="accepted", kubeconfig=str(kube), context="accepted-context")
    for variable in w.SETUP_INPUTS.values():
        monkeypatch.setenv(variable, "changed")
    assert w.connection(w.parser().parse_args([]), saved) == saved


def test_removed_monitoring_action():
    script = Path(__file__).resolve().parents[1] / "tools/lab-setup.py"
    result = subprocess.run([sys.executable, "-B", str(script), "monitoring", "--help"], capture_output=True)
    assert result.returncode != 0


def test_prelogin_prompt_has_foreground_terminal():
    pid, terminal = pty.fork()
    if pid == 0:
        try:
            command = [sys.executable, "-c", (
                "import os; fd=os.open('/dev/tty', os.O_RDWR); "
                "os.write(fd, b'Host confirmation: '); "
                "answer=os.read(fd, 100); print('ANSWER='+answer.decode().strip())"
            )]
            result = w.execute(command, capture_output=True, text=True, timeout=5)
            assert result.returncode == 0
            assert result.stdout.strip() == "ANSWER=yes"
            assert os.tcgetpgrp(0) == os.getpgrp()
            os.write(1, b"TERMINAL_RESTORED\n")
            os._exit(0)
        except BaseException:
            os._exit(1)
    output = b""
    answered = False
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            if select.select([terminal], [], [], 0.1)[0]:
                try:
                    data = os.read(terminal, 4096)
                except OSError:
                    break
                if not data:
                    break
                output += data
                if b"Host confirmation: " in output and not answered:
                    os.write(terminal, b"yes\n")
                    answered = True
            if b"TERMINAL_RESTORED" in output:
                break
        assert answered and b"TERMINAL_RESTORED" in output, output
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        os.waitpid(pid, 0)
        os.close(terminal)


@pytest.mark.parametrize("leader_exit", [None, 0, 1])
def test_failure_terminates_descendants_after_leader_exits(tmp_path, leader_exit):
    marker = tmp_path / "descendant.pid"
    descendant = (
        "import os, signal, time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)"
    )
    leader = (
        "import subprocess, sys, time; from pathlib import Path; "
        f"subprocess.Popen([sys.executable, '-c', {descendant!r}]"
        + (", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL" if leader_exit == 1 else "")
        + "); "
        f"marker=Path({str(marker)!r})\n"
        "while not marker.exists(): time.sleep(.01)\n"
        + (f"sys.exit({leader_exit})\n" if leader_exit is not None else "time.sleep(30)\n")
    )
    try:
        error = subprocess.CalledProcessError if leader_exit == 1 else subprocess.TimeoutExpired
        with pytest.raises(error):
            w.execute([sys.executable, "-c", leader], capture_output=True, check=True, timeout=1)
        pid = int(marker.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
            if not result.stdout.strip() or result.stdout.strip().startswith("Z"):
                break
            time.sleep(.05)
        else:
            pytest.fail("Owned descendant survived timeout cleanup")
    finally:
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM, signal.SIGHUP])
@pytest.mark.parametrize("phase", ["created", "handlers"])
def test_cancellation_during_process_creation_reaps_child(tmp_path, stop_signal, phase):
    marker = tmp_path / "child.pid"
    child = "import time; time.sleep(30)"
    script = f'''
import os, signal, subprocess, sys
from pathlib import Path
sys.path.insert(0, {str(Path(w.__file__).resolve().parents[1])!r})
from course_bootstrap import sync_workstation as w
original_popen, original_signal = subprocess.Popen, signal.signal

def popen(*args, **kwargs):
    process = original_popen(*args, **kwargs)
    Path({str(marker)!r}).write_text(str(process.pid))
    if {phase!r} == "created":
        os.kill(os.getpid(), {int(stop_signal)})
    return process

def install(number, handler):
    previous = original_signal(number, handler)
    if {phase!r} == "handlers" and getattr(handler, "__name__", "") == "cancel" and number == signal.SIGINT:
        os.kill(os.getpid(), {int(stop_signal)})
    return previous

subprocess.Popen, signal.signal = popen, install
w.execute([sys.executable, "-c", {child!r}], capture_output=True, timeout=5)
'''
    try:
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True,
                                text=True, timeout=10)
        assert result.returncode == 128 + stop_signal, result.stderr
        pid = int(marker.read_text())
        status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
        assert not status.stdout.strip() or status.stdout.strip().startswith("Z")
    finally:
        if marker.exists():
            try:
                os.killpg(int(marker.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_process_creation_restores_signal_mask_and_handlers():
    signals = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    mask = signal.pthread_sigmask(signal.SIG_BLOCK, [])
    handlers = {number: signal.getsignal(number) for number in signals}
    with pytest.raises(FileNotFoundError):
        w.execute(["/nonexistent/course-sync-command"])
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == mask
    assert {number: signal.getsignal(number) for number in signals} == handlers
    code = "import signal; print(','.join(str(int(s)) for s in sorted(signal.pthread_sigmask(signal.SIG_BLOCK, []))))"
    result = w.execute([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == ",".join(str(int(s)) for s in sorted(mask))
    assert signal.pthread_sigmask(signal.SIG_BLOCK, []) == mask
    assert {number: signal.getsignal(number) for number in signals} == handlers
