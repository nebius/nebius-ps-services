"""Real rsync transfers against disposable Git sources and an isolated SSH endpoint."""

import json
import os
import pty
import re
import signal
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COURSES = (
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
    "advanced-gpu-communication",
)
RSYNC = shutil.which("rsync")
SSH = shutil.which("ssh")
KUBECTL = shutil.which("kubectl")
pytestmark = pytest.mark.skipif(RSYNC is None, reason="real rsync is required")


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "local clone's directory"
    source = repo / "courses"
    source.mkdir(parents=True)
    shutil.copy2(ROOT / "sync-labs.sh", source / "sync-labs.sh")
    (source / "index.html").write_text("catalog\n")
    (source / "README.md").write_text("shared source\n")
    (source / "lab-guide.html").write_text("shared guide\n")
    (source / "tools").mkdir()
    (source / "tools/course_setup.py").write_text("# shared setup\n")
    (source / "docs").mkdir()
    (source / "docs/grafana.png").write_bytes(b"fixture diagram")
    (repo / "unrelated.txt").write_text("outside selected courses\n")
    for course in COURSES:
        base = source / course
        for folder in ("labs", "slurm", "tools", "reference"):
            (base / folder).mkdir(parents=True)
        for relative, content in {
            "labs/01_example.py": "print('initial')\n",
            "labs/common.py": "# shared helper\n",
            "slurm/run.sbatch": "#!/bin/bash\ntrue\n",
            "tools/aggregate.py": "# runtime tool\n",
            "reference/course.json": json.dumps({"slug": course}),
            "README.md": "Course setup\n",
            "COURSE.md": "Course lessons\n",
            "requirements.txt": "# dependencies\n",
        }.items():
            (base / relative).write_text(content)
        (base / "slurm/run.sbatch").chmod(0o755)
        if (ROOT / course / ".gitignore").is_file():
            shutil.copy2(ROOT / course / ".gitignore", base / ".gitignore")
    advanced = source / "advanced-gpu-communication"
    for relative in (
        "env/nixlbench", "labs/gradient_overlap_compute.py",
        "labs/gradient_overlap_common.py", "labs/training_common.py",
        "labs/course_evidence.py",
    ):
        (advanced / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "advanced-gpu-communication" / relative, advanced / relative)
    (source / "custom-cuda-kernels/CMakeLists.txt").write_text("# build metadata\n")
    text_course = source / "soperator"
    (text_course / "reference").mkdir(parents=True)
    (text_course / "reference/course.json").write_text('{"slug":"soperator"}')
    (text_course / "COURSE.md").write_text("Text-only lessons\n")
    (text_course / "index.html").write_text("Text-only course\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    remote = tmp_path / "remote home"
    remote.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "bash").symlink_to("/bin/bash")
    (binaries / "rsync").symlink_to(RSYNC)
    log = tmp_path / "ssh.jsonl"
    config_log = tmp_path / "ssh-config.jsonl"
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text("Host course-alias\n  HostName 192.0.2.10\n  Port 2222\n")
    kube_log = tmp_path / "kubectl.jsonl"
    shell_log = tmp_path / "shell.json"
    fake_kubectl = binaries / "kubectl"
    fake_kubectl.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, sys
with open(os.environ['TEST_KUBE_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
context = sys.argv[1:] == ['config', 'current-context']
if os.environ.get('TEST_KUBE_FAIL') == ('context' if context else 'services'):
    print('fixture: Kubernetes unavailable', file=sys.stderr)
    sys.exit(1)
print(os.environ.get('TEST_KUBE_CONTEXT', 'test-context') if context else
      os.environ.get('TEST_KUBE_SERVICES',
                     'soperator-login-svc|LoadBalancer|TCP:22,|192.0.2.10/,'),
      end='\\n' if context else '')
"""
    )
    fake_kubectl.chmod(0o755)
    fake_shell = binaries / "login-shell"
    fake_shell.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, signal, sys
from pathlib import Path
record = dict(cwd=os.getcwd(), args=sys.argv[1:], tty=os.isatty(0), pid=os.getpid())
if os.environ.get('TEST_SHELL_WAIT'):
    record['foreground'] = os.tcgetpgrp(0) == os.getpgrp()
    signal.signal(signal.SIGINT, lambda *_: sys.exit(42))
Path(os.environ['TEST_SHELL_LOG']).write_text(json.dumps(record))
if os.environ.get('TEST_WORK_DIR_LOG'):
    assert not Path(Path(os.environ['TEST_WORK_DIR_LOG']).read_text().strip()).exists()
if os.environ.get('TEST_SHELL_WAIT'):
    signal.pause()
sys.exit(int(os.environ.get('TEST_SHELL_STATUS', '0')))
"""
    )
    fake_shell.chmod(0o755)
    # This transport preserves SSH's argument boundary and executes the receiver
    # command under a separate HOME. No DNS queries or SSH connections are made.
    fake_ssh = binaries / "ssh"
    fake_ssh.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if '-G' in args:
    with open(os.environ['TEST_SSH_CONFIG_LOG'], 'a') as stream:
        stream.write(json.dumps(args) + '\\n')
    if 'TEST_SSH_CONFIG_OUTPUT' in os.environ:
        print(os.environ['TEST_SSH_CONFIG_OUTPUT'])
        sys.exit(int(os.environ.get('TEST_SSH_CONFIG_STATUS', '0')))
    sys.exit(subprocess.call([os.environ['TEST_REAL_SSH'], '-F',
                             os.environ['TEST_SSH_CONFIG'], *args]))
options = []
user = None
while args and args[0].startswith('-'):
    flag = args.pop(0)
    options.append(flag)
    if flag in ('-o', '-p', '-i', '-l'):
        value = args.pop(0)
        options.append(value)
        if flag == '-l':
            user = value
target = args.pop(0)
if '@' in target:
    user, target = target.split('@', 1)
command = ' '.join(args)
transfer = '--server' in command
interactive = '-t' in options
phase = 'login' if interactive else 'transfer' if transfer else 'preflight'
with open(os.environ['TEST_SSH_LOG'], 'a') as stream:
    stream.write(json.dumps(dict(host=target, user=user, options=options,
                                 transfer=transfer, interactive=interactive,
                                 command=command)) + '\\n')
if os.environ.get('TEST_SSH_PAUSE') == phase:
    import time
    Path(os.environ['TEST_PAUSE_MARKER']).touch()
    time.sleep(3)
failure = os.environ.get('TEST_SSH_FAIL')
if failure == phase:
    print('fixture: SSH/transfer failure', file=sys.stderr)
    sys.exit(255)
env = os.environ.copy()
env['HOME'] = os.environ['TEST_REMOTE_HOME']
env['SHELL'] = os.environ['TEST_REMOTE_SHELL']
if interactive:
    if os.environ.get('TEST_LOGIN_BAD_HOME'):
        env['HOME'] += '/missing'
    os.execve('/bin/sh', ['sh', '-c', command], env)
if os.environ.get('TEST_NO_REMOTE_RSYNC'):
    env['PATH'] = os.environ['TEST_REMOTE_BIN']
sys.exit(subprocess.call(['/bin/sh', '-c', command], env=env))
"""
    )
    fake_ssh.chmod(0o755)
    remote_bin = tmp_path / "remote-bin"
    remote_bin.mkdir()
    (remote_bin / "sh").symlink_to("/bin/sh")
    env = {
        **os.environ,
        "PATH": f"{binaries}{os.pathsep}{os.environ['PATH']}",
        "TEST_SSH_LOG": str(log),
        "TEST_SSH_CONFIG_LOG": str(config_log),
        "TEST_SSH_CONFIG": str(ssh_config),
        "TEST_REAL_SSH": SSH or "ssh",
        "TEST_KUBE_LOG": str(kube_log),
        "TEST_SHELL_LOG": str(shell_log),
        "TEST_REMOTE_SHELL": str(fake_shell),
        "TEST_REMOTE_HOME": str(remote),
        "TEST_REMOTE_BIN": str(remote_bin),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "NO_COLOR": "",
    }

    terminals = []
    processes = []

    class Workspace:
        def start(self, *args, extra_env=None, terminal=True, controlling=False):
            stdin = subprocess.DEVNULL
            if terminal:
                import termios

                master, stdin = pty.openpty()
                attributes = termios.tcgetattr(stdin)
                attributes[3] &= ~termios.ECHO
                termios.tcsetattr(stdin, termios.TCSANOW, attributes)
                terminals.extend((master, stdin))
                self.master = master

            def control_terminal():
                import fcntl
                import termios

                os.setsid()
                fcntl.ioctl(0, termios.TIOCSCTTY, 0)

            process = subprocess.Popen(
                ["/bin/bash", str(source / "sync-labs.sh"), *args],
                cwd=tmp_path,
                env={**env, **(extra_env or {})},
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=not controlling,
                preexec_fn=control_terminal if controlling else None,
            )
            processes.append(process)
            return process

        def run(self, *args, extra_env=None, terminal=True):
            process = self.start(*args, extra_env=extra_env, terminal=terminal)
            stdout, stderr = process.communicate(timeout=20)
            return subprocess.CompletedProcess(
                process.args, process.returncode, stdout, stderr
            )

        def calls(self):
            return [json.loads(line) for line in log.read_text().splitlines()]

    result = Workspace()
    result.repo, result.source, result.remote = repo, source, remote
    result.log, result.binaries = log, binaries
    result.config_log = config_log
    result.kube_log, result.shell_log = kube_log, shell_log
    yield result
    for process in processes:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)
    for descriptor in terminals:
        os.close(descriptor)


def assert_ok(result):
    assert result.returncode == 0, result.stdout + result.stderr


def transferred(result):
    return re.findall(r"^[<>]f\S*\s+(.*)$", result.stdout, re.MULTILINE)


def snapshot(root):
    records = {}
    for path in [root, *root.rglob("*")]:
        info = path.lstat()
        content = None
        if path.is_symlink():
            content = os.readlink(path)
        elif path.is_file():
            content = path.read_bytes()
        records[str(path.relative_to(root))] = (
            info.st_mode,
            info.st_mtime_ns,
            content,
        )
    return records


def test_initial_complete_working_tree_transfer(workspace):
    course = workspace.source / COURSES[0]
    (course / "labs/01_example.py").write_text("print('uncommitted change')\n")
    (course / "labs/new lab's example.py").write_text("# new untracked lab\n")
    (course / "labs/helper-link.py").symlink_to("common.py")
    (course / "labs/outside-link").symlink_to("/etc/passwd")
    for path in (
        "results/result.json",
        "logs/run.log",
        ".venv/bin/python",
        "labs/__pycache__/example.pyc",
        "profile.nsys-rep",
    ):
        file = course / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("ignored generated file\n")
    build = workspace.source / "custom-cuda-kernels/build-debug"
    build.mkdir()
    (build / "artifact").write_text("compiled\n")
    result = workspace.run("login.example.com")
    assert_ok(result)
    dest = workspace.remote / "courses"
    assert {p.name for p in dest.iterdir()} == {
        *COURSES,
        "soperator",
        "index.html",
        "README.md",
        "lab-guide.html",
        "docs",
        "tools",
    }
    assert (dest / "soperator/COURSE.md").read_text() == "Text-only lessons\n"
    assert (dest / "soperator/index.html").read_text() == "Text-only course\n"
    assert not (dest / "soperator/labs").exists()
    for name in COURSES:
        for relative in (
            "labs/01_example.py",
            "labs/common.py",
            "slurm/run.sbatch",
            "tools/aggregate.py",
            "requirements.txt",
            "README.md",
            "reference/course.json",
        ):
            local = workspace.source / name / relative
            remote = dest / name / relative
            assert remote.read_bytes() == local.read_bytes()
            assert int(remote.stat().st_mtime) == int(local.stat().st_mtime)
        assert stat.S_IMODE((dest / name / "slurm/run.sbatch").stat().st_mode) == 0o755
    assert (dest / "custom-cuda-kernels/CMakeLists.txt").is_file()
    for relative in (
        "env/nixlbench", "labs/gradient_overlap_compute.py",
        "labs/gradient_overlap_common.py", "labs/training_common.py",
        "labs/course_evidence.py",
    ):
        local = workspace.source / "advanced-gpu-communication" / relative
        remote = dest / "advanced-gpu-communication" / relative
        assert remote.read_bytes() == local.read_bytes()
        assert stat.S_IMODE(remote.stat().st_mode) == stat.S_IMODE(local.stat().st_mode)
    assert not list(dest.rglob("*-lab-kit.zip"))
    copied = dest / COURSES[0] / "labs"
    assert (copied / "new lab's example.py").is_file()
    assert os.readlink(copied / "helper-link.py") == "common.py"
    assert not (copied / "outside-link").is_symlink()
    assert not (dest / COURSES[0] / ".venv").exists()
    assert not (dest / COURSES[0] / "results").exists()
    assert not (dest / COURSES[0] / "logs").exists()
    assert not (copied / "__pycache__").exists()
    assert not list(dest.rglob("*.nsys-rep"))
    assert not (dest / "custom-cuda-kernels/build-debug").exists()
    assert not list(dest.rglob(".git"))
    assert "\x1b" not in result.stdout + result.stderr
    assert [call["transfer"] for call in workspace.calls()] == [False, True, False]
    assert [call["interactive"] for call in workspace.calls()] == [False, False, True]


def test_incremental_local_wins_and_remote_only_survives(workspace):
    assert_ok(workspace.run("192.0.2.10"))
    settled = snapshot(workspace.remote)
    repeated = workspace.run("192.0.2.10")
    assert_ok(repeated)
    assert transferred(repeated) == []
    assert snapshot(workspace.remote) == settled
    dest = workspace.remote / "courses" / COURSES[0]
    (dest / "results").mkdir()
    result_file = dest / "results/result.json"
    result_file.write_text("valuable remote result\n")
    (dest / "labs/remote-experiment.py").write_text("# remote experiment\n")
    unchanged = workspace.run("192.0.2.10")
    assert_ok(unchanged)
    assert transferred(unchanged) == []
    relative = "labs/01_example.py"
    (workspace.source / COURSES[0] / relative).write_text(
        "print('new local contents')\n"
    )
    (dest / relative).write_text("remote changes\n")
    future = time.time() + 5000
    os.utime(dest / relative, (future, future))
    (workspace.source / COURSES[0] / "labs/common.py").unlink()
    changed = workspace.run("192.0.2.10")
    assert_ok(changed)
    assert (dest / relative).read_text() == "print('new local contents')\n"
    assert transferred(changed) == [f"{COURSES[0]}/{relative}"], changed.stdout
    assert (dest / "labs/common.py").is_file()
    assert (dest / "labs/remote-experiment.py").is_file()
    assert result_file.read_text() == "valuable remote result\n"
    settled = snapshot(workspace.remote)
    repeated = workspace.run("192.0.2.10")
    assert_ok(repeated)
    assert transferred(repeated) == []
    assert snapshot(workspace.remote) == settled


def test_permission_only_change_converges_without_content_transfer(workspace):
    assert_ok(workspace.run("host"))
    relative = Path(COURSES[0]) / "labs/01_example.py"
    source = workspace.source / relative
    source.chmod(0o755)
    changed = workspace.run("host")
    assert_ok(changed)
    assert transferred(changed) == []
    assert (
        stat.S_IMODE((workspace.remote / "courses" / relative).stat().st_mode) == 0o755
    )
    settled = snapshot(workspace.remote)
    repeated = workspace.run("host")
    assert_ok(repeated)
    assert transferred(repeated) == []
    assert snapshot(workspace.remote) == settled


@pytest.mark.parametrize("existing", [False, True])
def test_dry_run_leaves_destination_untouched(workspace, existing):
    if existing:
        assert_ok(workspace.run("slurm-login"))
    (workspace.source / COURSES[0] / "labs/01_example.py").write_text("# changed\n")
    before = snapshot(workspace.remote)
    offset = len(workspace.calls()) if workspace.log.exists() else 0
    result = workspace.run("--dry-run", "slurm-login", terminal=False)
    assert_ok(result)
    assert snapshot(workspace.remote) == before
    assert transferred(result)
    assert not any(call["interactive"] for call in workspace.calls()[offset:])
    assert "Preview complete" in result.stdout


@pytest.mark.parametrize(
    ("target", "host", "user"),
    [
        ("login.example.com", "login.example.com", "root"),
        ("192.0.2.10", "192.0.2.10", "root"),
        ("1.1.1.1", "1.1.1.1", "root"),
        ("slurm-login", "slurm-login", "root"),
        ("root@192.0.2.10", "192.0.2.10", "root"),
        ("nebius@192.0.2.10", "192.0.2.10", "nebius"),
        ("student@192.0.2.10", "192.0.2.10", "student"),
        ("student@login.example.com", "login.example.com", "student"),
        ("2001:db8::10", "2001:db8::10", "root"),
        ("[2001:db8::10]", "2001:db8::10", "root"),
        ("student@[2001:db8::10]", "2001:db8::10", "student"),
        ("student@2001:db8::10", "2001:db8::10", "student"),
    ],
)
def test_target_and_ssh_settings(workspace, target, host, user):
    identity = workspace.repo / "key's name with spaces"
    identity.write_text("fixture placeholder; never read as a real key\n")
    result = workspace.run(
        "--dest",
        "my-courses",
        "--port",
        "02222",
        "--identity",
        str(identity),
        "--",
        target,
    )
    assert_ok(result)
    calls = workspace.calls()
    assert len(calls) == 3
    for call in calls:
        assert call["host"] == host
        assert call["user"] == user
        opts = call["options"]
        assert opts[opts.index("-p") + 1] == "2222"
        assert opts[opts.index("-i") + 1] == str(identity)
        assert "StrictHostKeyChecking=no" not in opts
        assert ("-t" in opts) == call["interactive"]
        assert ("-T" in opts) != call["interactive"]
    shell = json.loads(workspace.shell_log.read_text())
    assert shell["cwd"] == str(workspace.remote / "my-courses")
    assert shell["args"] == ["-il"]
    assert shell["tty"]
    assert not workspace.kube_log.exists()
    assert (workspace.remote / "my-courses/index.html").is_file()
    assert not (workspace.remote / "courses").exists()


def test_discovers_new_course_with_original_folder_name(workspace):
    new = workspace.source / "new course's name"
    (new / "labs").mkdir(parents=True)
    (new / "reference").mkdir()
    (new / "reference/course.json").write_text('{"slug": "different-slug"}')
    (new / "COURSE.md").write_text("New course\n")
    (new / "labs/example.py").write_text("# future course\n")
    result = workspace.run("slurm-login")
    assert_ok(result)
    assert f"Synced {len(COURSES) + 2} courses" in result.stdout
    assert (workspace.remote / "courses" / new.name / "labs/example.py").is_file()


@pytest.mark.parametrize(
    "args",
    [
        ("",),
        ("--unknown",),
        ("a", "b"),
        ("--port",),
        ("--port", "0", "host"),
        ("--port", "65536", "host"),
        ("--port", "12x", "host"),
        ("--port", "-1", "host"),
        ("--identity", "/nonexistent/key", "host"),
        ("--dest", "../escape", "host"),
        ("--dest", "/tmp", "host"),
        ("--dest", ".", "host"),
        ("--dest", "a/b", "host"),
        ("--", "-evil"),
        ("host;true",),
        ("$(true)",),
        ("a@b@c",),
        ("user@",),
        ("host:22",),
        ("[host]",),
    ],
)
def test_bad_input_fails_before_ssh(workspace, args):
    result = workspace.run(*args)
    assert result.returncode != 0
    assert "ERROR:" in result.stderr
    assert not workspace.log.exists()


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_unsafe_remote_destination_rejected(workspace, kind):
    destination = workspace.remote / "courses"
    if kind == "symlink":
        elsewhere = workspace.remote / "elsewhere"
        elsewhere.mkdir()
        destination.symlink_to(elsewhere, target_is_directory=True)
    else:
        destination.write_text("existing file\n")
    before = snapshot(workspace.remote)
    result = workspace.run("host")
    assert result.returncode != 0
    assert "preflight failed" in result.stderr
    assert snapshot(workspace.remote) == before
    assert len(workspace.calls()) == 1


def test_missing_remote_rsync_and_connection_failure(workspace):
    result = workspace.run("host", extra_env={"TEST_NO_REMOTE_RSYNC": "1"})
    assert result.returncode != 0
    assert "rsync is required on the login node" in result.stderr
    result = workspace.run("host", extra_env={"TEST_SSH_FAIL": "preflight"})
    assert result.returncode != 0
    assert "preflight failed" in result.stderr
    assert not list(workspace.remote.iterdir())


def test_transfer_failure_can_be_retried(workspace):
    result = workspace.run("host", extra_env={"TEST_SSH_FAIL": "transfer"})
    assert result.returncode != 0
    assert "Sync incomplete" in result.stderr
    assert "Synced" not in result.stdout
    assert_ok(workspace.run("host"))


def test_bare_target_preflight_failure_explains_account_selection(workspace):
    result = workspace.run("host", extra_env={"TEST_SSH_FAIL": "preflight"})
    assert result.returncode == 255
    assert "default SSH user is root" in result.stderr
    assert "user@host" in result.stderr
    assert "local username" not in result.stderr
    assert not (workspace.remote / "courses").exists()


def test_partial_transfer_retry_converges_and_then_changes_nothing(workspace):
    payload = workspace.source / COURSES[0] / "labs/zz_payload.dat"
    payload.write_bytes(os.urandom(4 * 1024 * 1024))
    # Rate-limit real rsync only during the interrupted run, giving the test a
    # deterministic window after one file arrives and before the next completes.
    rsync = workspace.binaries / "rsync"
    rsync.unlink()
    rsync.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "if os.environ.get('TEST_RSYNC_LIMIT'):\n"
        "    args.insert(0, '--bwlimit=1024')\n"
        f"os.execv({RSYNC!r}, [{RSYNC!r}, *args])\n"
    )
    rsync.chmod(0o755)
    completed = workspace.remote / "courses" / COURSES[0] / "labs/01_example.py"
    remote_payload = completed.with_name(payload.name)
    process = workspace.start("host", extra_env={"TEST_RSYNC_LIMIT": "1"})
    try:
        deadline = time.monotonic() + 10
        while process.poll() is None:
            partials = list(completed.parent.glob(".zz_payload.dat.*"))
            if completed.exists() and any(p.stat().st_size > 0 for p in partials):
                break
            assert time.monotonic() < deadline, "partial transfer did not start"
            time.sleep(0.01)
        assert process.poll() is None
        assert not remote_payload.exists()
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 143
        assert "cancelled" in stderr.lower()
        assert "Synced" not in stdout
        assert (
            completed.read_bytes()
            == (workspace.source / COURSES[0] / "labs/01_example.py").read_bytes()
        )
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)
    result = completed.parent / "remote-result.txt"
    result.write_text("keep this result\n")
    assert_ok(workspace.run("host"))
    for source in workspace.source.rglob("*"):
        relative = source.relative_to(workspace.source)
        if source.is_file() and (
            relative.parts[0] in COURSES
            or relative.as_posix() in {"index.html", "README.md", "lab-guide.html", "docs/grafana.png", "tools/course_setup.py"}
        ):
            destination = workspace.remote / "courses" / relative
            assert destination.read_bytes() == source.read_bytes()
            assert stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(
                source.stat().st_mode
            )
            assert int(destination.stat().st_mtime) == int(source.stat().st_mtime)
    assert result.read_text() == "keep this result\n"
    settled = snapshot(workspace.remote)
    repeated = workspace.run("host")
    assert_ok(repeated)
    assert transferred(repeated) == []
    assert snapshot(workspace.remote) == settled


@pytest.mark.parametrize("phase", ["preflight", "transfer"])
@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT, signal.SIGHUP])
def test_cancellation_stops_pending_remote_work(workspace, phase, stop_signal):
    marker = workspace.repo / "remote-command-started"
    process = workspace.start(
        "host",
        extra_env={"TEST_SSH_PAUSE": phase, "TEST_PAUSE_MARKER": str(marker)},
    )
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and process.poll() is None:
            assert time.monotonic() < deadline, "remote command did not start"
            time.sleep(0.01)
        assert marker.exists()
        before = snapshot(workspace.remote)
        process.send_signal(stop_signal)
        # communicate also waits for inherited pipes to close: a surviving SSH
        # transport cannot silently outlive the parent and finish the copy.
        stdout, stderr = process.communicate(timeout=1.5)
        assert process.returncode == 128 + stop_signal
        assert "Synced" not in stdout
        assert "cancelled" in stderr.lower()
        assert snapshot(workspace.remote) == before
    finally:
        # Only this test's new process group can be terminated on failure.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)


def test_rejects_symlink_source_directory(workspace):
    labs = workspace.source / COURSES[0] / "labs"
    moved = workspace.repo / "outside-labs"
    labs.rename(moved)
    labs.symlink_to(moved, target_is_directory=True)
    result = workspace.run("host")
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not workspace.log.exists()


def test_help_without_dependencies_and_missing_tool_error(workspace):
    empty = workspace.repo / "empty-bin"
    empty.mkdir()
    result = workspace.run("--help", extra_env={"PATH": str(empty)})
    assert_ok(result)
    for option in ("--dry-run", "--dest", "--port", "--identity", "IPv4/IPv6"):
        assert option in result.stdout
    result = workspace.run("host", extra_env={"PATH": str(empty)})
    assert result.returncode != 0
    assert "Required command not found: git" in result.stderr
    assert not workspace.log.exists()


@pytest.mark.parametrize(
    ("ingress", "host"),
    [
        ("192.0.2.10/,", "192.0.2.10"),
        ("2001:db8::10/,", "2001:db8::10"),
        ("/login.example.com,", "login.example.com"),
        ("192.0.2.10/login.example.com,192.0.2.10/other.example.com,", "192.0.2.10"),
    ],
)
@pytest.mark.parametrize("override", [False, True])
def test_discovers_endpoint_and_service_port(workspace, ingress, host, override):
    services = (
        "soperator-login-headless-svc|ClusterIP|TCP:22,|\n"
        "internal-login|NodePort|TCP:22,|\n"
        f"soperator-login-svc|LoadBalancer|TCP:2222,|{ingress}"
    )
    args = ("--port", "2200") if override else ()
    result = workspace.run(*args, extra_env={"TEST_KUBE_SERVICES": services})
    assert_ok(result)
    calls = workspace.calls()
    assert len(calls) == 3
    for call in calls:
        assert call["user"] == "root"
        assert call["host"] == host
        options = call["options"]
        assert options[options.index("-p") + 1] == ("2200" if override else "2222")
    kube = [json.loads(line) for line in workspace.kube_log.read_text().splitlines()]
    assert kube[0] == ["config", "current-context"]
    assert kube[1][:-1] == [
        "--context",
        "test-context",
        "get",
        "svc",
        "-n",
        "soperator",
        "-l",
        "app.kubernetes.io/component=login",
        "--request-timeout=15s",
        "-o",
    ]
    assert kube[1][-1].startswith("jsonpath=")
    assert json.loads(workspace.shell_log.read_text())["cwd"] == str(
        workspace.remote / "courses"
    )


@pytest.mark.parametrize(
    ("services", "message"),
    [
        ("", "No login LoadBalancer"),
        ("internal|ClusterIP|TCP:22,|", "No login LoadBalancer"),
        ("login|LoadBalancer|TCP:22,|", "no external endpoint"),
        ("login|LoadBalancer|TCP:22,|/,", "Invalid external"),
        ("login|LoadBalancer|TCP:22,|<pending>/,", "TARGET must"),
        ("login|LoadBalancer|TCP:22,|user@192.0.2.10/,", "Invalid external"),
        ("login|LoadBalancer|TCP:22,|bad;host/,", "TARGET must"),
        ("login|LoadBalancer|TCP:22,|192.0.2.10/", "Malformed login ingress"),
        ("login|LoadBalancer|TCP:22,|192.0.2.10/foo/bar,", "Malformed login ingress"),
        ("login|LoadBalancer|UDP:22,|192.0.2.10/,", "exactly one TCP port"),
        ("login|LoadBalancer|TCP:22,TCP:23,|192.0.2.10/,", "exactly one TCP port"),
        ("login|LoadBalancer|TCP:0,|192.0.2.10/,", "Invalid login Service port"),
        ("login|LoadBalancer|TCP:65536,|192.0.2.10/,", "Invalid login Service port"),
        ("login|LoadBalancer|TCP:22,|192.0.2.10/,|extra", "Malformed"),
        ("bad name|LoadBalancer|TCP:22,|192.0.2.10/,", "Malformed"),
        (
            "login|LoadBalancer|TCP:22,|192.0.2.10/,192.0.2.11/,",
            "Multiple external login endpoints",
        ),
        (
            "login-a|LoadBalancer|TCP:22,|192.0.2.10/,\n"
            "login-b|LoadBalancer|TCP:22,|192.0.2.11/,",
            "Multiple login LoadBalancer Services",
        ),
    ],
)
def test_discovery_failure_never_connects(workspace, services, message):
    result = workspace.run(extra_env={"TEST_KUBE_SERVICES": services})
    assert result.returncode != 0
    assert message in result.stderr
    assert not workspace.log.exists()
    assert not workspace.shell_log.exists()
    assert not list(workspace.remote.iterdir())
    if "Multiple" in message:
        assert "192.0.2.10" in result.stderr
        assert "192.0.2.11" in result.stderr
        assert "explicit target" in result.stderr


@pytest.mark.parametrize("phase", ["context", "services"])
def test_kubernetes_error_never_connects(workspace, phase):
    result = workspace.run(extra_env={"TEST_KUBE_FAIL": phase})
    assert result.returncode != 0
    assert "explicit target" in result.stderr
    assert not workspace.log.exists()


def test_empty_context_never_connects(workspace):
    result = workspace.run(extra_env={"TEST_KUBE_CONTEXT": ""})
    assert result.returncode != 0
    assert "No current kubectl context" in result.stderr
    assert not workspace.log.exists()


def test_missing_kubectl_only_blocks_discovery(workspace):
    # Restrict PATH to fixture tools and symlink ordinary dependencies explicitly.
    (workspace.binaries / "kubectl").unlink()
    for name in ("git", "mktemp", "find", "dirname", "cat", "chmod", "sh"):
        (workspace.binaries / name).symlink_to(shutil.which(name))
    env = {"PATH": str(workspace.binaries)}
    result = workspace.run(extra_env=env)
    assert result.returncode != 0
    assert "Required command not found: kubectl" in result.stderr
    assert not workspace.log.exists()
    assert_ok(workspace.run("192.0.2.10", extra_env=env))
    assert not workspace.kube_log.exists()


@pytest.mark.parametrize("args", [(), ("192.0.2.10",)])
def test_nonterminal_fails_before_discovery_or_transfer(workspace, args):
    result = workspace.run(*args, terminal=False)
    assert result.returncode != 0
    assert "interactive terminal is required" in result.stderr
    assert not workspace.kube_log.exists()
    assert not workspace.log.exists()
    assert not list(workspace.remote.iterdir())


def test_discovery_dry_run_without_terminal_preserves_destination(workspace):
    before = snapshot(workspace.remote)
    result = workspace.run("--dry-run", terminal=False)
    assert_ok(result)
    assert snapshot(workspace.remote) == before
    assert len(workspace.calls()) == 2
    assert not any(call["interactive"] for call in workspace.calls())
    assert not workspace.shell_log.exists()


@pytest.mark.parametrize("status", [0, 7, 255])
def test_interactive_handoff_cleanup_and_exit_status(workspace, status):
    temporary_log = workspace.repo / "temporary-dir"
    mktemp = workspace.binaries / "mktemp"
    mktemp.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys\n"
        "from pathlib import Path\n"
        f"result = subprocess.check_output([{shutil.which('mktemp')!r}, *sys.argv[1:]], text=True)\n"
        "Path(os.environ['TEST_WORK_DIR_LOG']).write_text(result)\n"
        "print(result, end='')\n"
    )
    mktemp.chmod(0o755)
    process = workspace.start(
        "root@192.0.2.10",
        extra_env={
            "TEST_SHELL_STATUS": str(status),
            "TEST_WORK_DIR_LOG": str(temporary_log),
        },
    )
    stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == status, stdout + stderr
    assert "Synced" in stdout
    shell = json.loads(workspace.shell_log.read_text())
    assert shell["pid"] == process.pid  # No background handoff or waiting parent.
    assert shell["cwd"] == str(workspace.remote / "courses")
    assert not Path(temporary_log.read_text().strip()).exists()


def test_failed_login_directory_change_does_not_launch_shell(workspace):
    result = workspace.run("host", extra_env={"TEST_LOGIN_BAD_HOME": "1"})
    assert result.returncode != 0
    assert "Synced" in result.stdout
    assert not workspace.shell_log.exists()


def test_ctrl_c_is_owned_by_foreground_ssh_after_handoff(workspace):
    process = workspace.start(
        "host", extra_env={"TEST_SHELL_WAIT": "1"}, controlling=True
    )
    deadline = time.monotonic() + 10
    while not workspace.shell_log.exists() and process.poll() is None:
        assert time.monotonic() < deadline, "interactive shell did not start"
        time.sleep(0.01)
    assert workspace.shell_log.exists()
    shell = json.loads(workspace.shell_log.read_text())
    assert shell["foreground"]
    assert shell["pid"] == process.pid
    os.write(workspace.master, b"\x03")
    stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 42, stdout + stderr
    assert "Sync cancelled" not in stderr


@pytest.mark.parametrize("phase", ["preflight", "transfer", "login"])
def test_ssh_failure_does_not_launch_shell(workspace, phase):
    result = workspace.run("host", extra_env={"TEST_SSH_FAIL": phase})
    assert result.returncode != 0
    assert not workspace.shell_log.exists()
    if phase != "login":
        assert not any(call["interactive"] for call in workspace.calls())


@pytest.mark.skipif(KUBECTL is None, reason="real kubectl is required")
@pytest.mark.parametrize("pending", [False, True])
def test_real_kubectl_projection_against_local_api(workspace, pending):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from urllib.parse import parse_qs, urlsplit

    requests = []
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": "training-gateway",
            "namespace": "soperator",
            "labels": {"app.kubernetes.io/component": "login"},
        },
        "spec": {"type": "LoadBalancer", "ports": [{"protocol": "TCP", "port": 2222}]},
        "status": {}
        if pending
        else {"loadBalancer": {"ingress": [{"ip": "192.0.2.10"}]}},
    }
    responses = {
        "/api": {"kind": "APIVersions", "apiVersion": "v1", "versions": ["v1"]},
        "/apis": {"kind": "APIGroupList", "apiVersion": "v1", "groups": []},
        "/api/v1": {
            "kind": "APIResourceList",
            "apiVersion": "v1",
            "groupVersion": "v1",
            "resources": [
                {
                    "name": "services",
                    "singularName": "service",
                    "namespaced": True,
                    "kind": "Service",
                    "shortNames": ["svc"],
                    "verbs": ["get", "list"],
                }
            ],
        },
        "/api/v1/namespaces/soperator/services": {
            "apiVersion": "v1",
            "kind": "ServiceList",
            "metadata": {},
            "items": [service],
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            request = urlsplit(self.path)
            requests.append(request)
            payload = json.dumps(responses.get(request.path, {})).encode()
            self.send_response(200 if request.path in responses else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    kubeconfig = workspace.repo / "kubeconfig.json"
    kubeconfig.write_text(
        json.dumps(
            {
                "apiVersion": "v1",
                "kind": "Config",
                "current-context": "local-test",
                "clusters": [
                    {
                        "name": "local",
                        "cluster": {"server": f"http://127.0.0.1:{server.server_port}"},
                    }
                ],
                "contexts": [{"name": "local-test", "context": {"cluster": "local"}}],
                "users": [],
            }
        )
    )
    kubectl = workspace.binaries / "kubectl"
    kubectl.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        f"os.execv({KUBECTL!r}, [{KUBECTL!r}, "
        f"'--cache-dir', {str(workspace.repo / 'kube-cache')!r}, *sys.argv[1:]])\n"
    )
    thread.start()
    try:
        result = workspace.run(extra_env={"KUBECONFIG": str(kubeconfig)})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if pending:
        assert result.returncode != 0
        assert "no external endpoint" in result.stderr
        assert not workspace.log.exists()
    else:
        assert_ok(result)
        for call in workspace.calls():
            assert call["host"] == "192.0.2.10"
            assert call["user"] == "root"
            assert call["options"][call["options"].index("-p") + 1] == "2222"
    service_requests = [r for r in requests if r.path.endswith("/services")]
    assert service_requests
    assert all(
        parse_qs(r.query)["labelSelector"] == ["app.kubernetes.io/component=login"]
        for r in service_requests
    )


def test_sync_only_writes_private_receipt_without_shell(workspace, tmp_path):
    receipt = tmp_path / "connection.json"
    result = workspace.run(
        "--sync-only", "--receipt", str(receipt), "192.0.2.10", terminal=False
    )
    assert_ok(result)
    record = json.loads(receipt.read_text())
    assert record["schema"] == "course-sync/v1"
    assert record["target"] == "root@192.0.2.10"
    assert record["port"] == 22
    assert receipt.stat().st_mode & 0o077 == 0
    assert not workspace.shell_log.exists()
    assert (workspace.remote / "courses/gpu-fundamentals/labs/01_example.py").is_file()
    before = receipt.read_bytes()
    result = workspace.run(
        "--sync-only", "--receipt", str(receipt), "192.0.2.10", terminal=False
    )
    assert result.returncode != 0
    assert receipt.read_bytes() == before


@pytest.mark.parametrize("sync_only", [False, True])
def test_receipt_pins_ssh_alias_port_across_all_phases(workspace, tmp_path, sync_only):
    receipt = tmp_path / "connection.json"
    identity = tmp_path / "identity"
    identity.touch(mode=0o600)
    result = workspace.run(
        *(["--sync-only"] if sync_only else []),
        "--identity", str(identity), "--receipt", str(receipt), "student@course-alias",
        terminal=not sync_only,
    )
    assert_ok(result)
    record = json.loads(receipt.read_text())
    assert record["target"] == "student@course-alias"
    assert record["port"] == 2222
    assert record["identity_file"] == str(identity)
    calls = workspace.calls()
    assert len(calls) == (2 if sync_only else 3)
    for call in calls:
        options = call["options"]
        assert options[options.index("-p") + 1] == "2222"
        assert options[options.index("-i") + 1] == str(identity)
        assert call["host"] == "course-alias" and call["user"] == "student"
    config_calls = [json.loads(line) for line in workspace.config_log.read_text().splitlines()]
    assert len(config_calls) == 1
    assert config_calls[0][-1] == "student@course-alias"
    assert config_calls[0][config_calls[0].index("-i") + 1] == str(identity)
    assert ("BatchMode=yes" in config_calls[0]) == sync_only


@pytest.mark.parametrize("discovery,override", [(False, True), (True, False), (True, True)])
def test_receipt_preserves_explicit_and_discovered_port_precedence(
    workspace, tmp_path, discovery, override
):
    receipt = tmp_path / "connection.json"
    result = workspace.run(
        "--sync-only", "--receipt", str(receipt),
        *(["--port", "2200"] if override else []),
        *([] if discovery else ["student@course-alias"]),
        extra_env={"TEST_KUBE_SERVICES": "login|LoadBalancer|TCP:2222,|192.0.2.10/,"},
        terminal=False,
    )
    assert_ok(result)
    expected = 2200 if override else 2222
    assert json.loads(receipt.read_text())["port"] == expected
    assert not workspace.config_log.exists()
    for call in workspace.calls():
        assert call["options"][call["options"].index("-p") + 1] == str(expected)


@pytest.mark.parametrize("output,status", [
    ("port 2222", "255"), ("", "0"), ("hostname example", "0"),
    ("port 22\nport 2222", "0"), ("port 22\nport 22", "0"),
    ("port 0", "0"), ("port 65536", "0"), ("port -1", "0"),
    ("port invalid", "0"), ("port 22 extra", "0"),
])
def test_receipt_rejects_unresolved_port_before_remote_effects(workspace, tmp_path, output, status):
    receipt = tmp_path / "connection.json"
    result = workspace.run(
        "--sync-only", "--receipt", str(receipt), "course-alias", terminal=False,
        extra_env={"TEST_SSH_CONFIG_OUTPUT": output, "TEST_SSH_CONFIG_STATUS": status},
    )
    assert result.returncode != 0
    assert "SSH port" in result.stderr
    assert not workspace.log.exists()
    assert not list(workspace.remote.iterdir())
    assert not receipt.exists()


def test_no_receipt_keeps_ssh_configuration_with_transport(workspace):
    assert_ok(workspace.run("--sync-only", "course-alias", terminal=False))
    assert not workspace.config_log.exists()
    assert all("-p" not in call["options"] for call in workspace.calls())


def test_dry_run_receipt_rejected_before_configuration_or_remote_access(workspace, tmp_path):
    receipt = tmp_path / "connection.json"
    result = workspace.run("--dry-run", "--receipt", str(receipt), "course-alias", terminal=False)
    assert result.returncode != 0
    assert "--receipt is unavailable with --dry-run" in result.stderr
    assert not workspace.config_log.exists()
    assert not workspace.log.exists()
    assert not receipt.exists()


@pytest.mark.parametrize("name", ["README.md", "lab-guide.html", "docs/grafana.png", "tools/course_setup.py"])
@pytest.mark.parametrize("defect", ["missing", "symlink"])
def test_shared_guide_must_be_a_regular_file_before_transfer(workspace, name, defect):
    path = workspace.source / name
    path.unlink()
    if defect == "symlink":
        path.symlink_to(workspace.source / "index.html")
    result = workspace.run("host")
    assert result.returncode != 0
    assert "Missing regular shared file" in result.stderr
    assert not workspace.remote.exists() or not list(workspace.remote.iterdir())


def test_shared_guide_updates_incrementally_and_dry_run_preserves_it(workspace):
    assert_ok(workspace.run("host"))
    for name in ("README.md", "lab-guide.html", "docs/grafana.png", "tools/course_setup.py"):
        assert (workspace.remote / "courses" / name).read_bytes() == (
            workspace.source / name
        ).read_bytes()
        (workspace.source / name).write_text("updated shared content\n")
    before = snapshot(workspace.remote)
    assert_ok(workspace.run("--dry-run", "host", terminal=False))
    assert snapshot(workspace.remote) == before
    assert_ok(workspace.run("host"))
    for name in ("README.md", "lab-guide.html", "docs/grafana.png", "tools/course_setup.py"):
        assert (
            workspace.remote / "courses" / name
        ).read_text() == "updated shared content\n"
    assert transferred(workspace.run("host")) == []
