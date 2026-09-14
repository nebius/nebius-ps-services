"""Real rsync transfers against disposable Git sources and an isolated SSH endpoint."""

import json
import os
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
)
RSYNC = shutil.which("rsync")
pytestmark = pytest.mark.skipif(RSYNC is None, reason="real rsync is required")


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "local clone's directory"
    source = repo / "courses"
    source.mkdir(parents=True)
    shutil.copy2(ROOT / "sync-labs.sh", source / "sync-labs.sh")
    (source / "index.html").write_text("catalog\n")
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
            "requirements.txt": "# dependencies\n",
        }.items():
            (base / relative).write_text(content)
        (base / "slurm/run.sbatch").chmod(0o755)
        shutil.copy2(ROOT / course / ".gitignore", base / ".gitignore")
    (source / "custom-cuda-kernels/CMakeLists.txt").write_text("# build metadata\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    remote = tmp_path / "remote home"
    remote.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "bash").symlink_to("/bin/bash")
    (binaries / "rsync").symlink_to(RSYNC)
    log = tmp_path / "ssh.jsonl"
    # This transport preserves SSH's argument boundary and executes the receiver
    # command under a separate HOME. No DNS queries or SSH connections are made.
    fake_ssh = binaries / "ssh"
    fake_ssh.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
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
with open(os.environ['TEST_SSH_LOG'], 'a') as stream:
    stream.write(json.dumps(dict(host=target, user=user, options=options,
                                 transfer=transfer, command=command)) + '\\n')
if os.environ.get('TEST_SSH_PAUSE') == ('transfer' if transfer else 'preflight'):
    import time
    Path(os.environ['TEST_PAUSE_MARKER']).touch()
    time.sleep(3)
failure = os.environ.get('TEST_SSH_FAIL')
if failure == 'preflight' or failure == 'transfer' and transfer:
    print('fixture: SSH/transfer failure', file=sys.stderr)
    sys.exit(255)
env = os.environ.copy()
env['HOME'] = os.environ['TEST_REMOTE_HOME']
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
        "TEST_REMOTE_HOME": str(remote),
        "TEST_REMOTE_BIN": str(remote_bin),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "NO_COLOR": "",
    }

    class Workspace:
        def start(self, *args, extra_env=None):
            return subprocess.Popen(
                ["/bin/bash", str(source / "sync-labs.sh"), *args],
                cwd=tmp_path,
                env={**env, **(extra_env or {})},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )

        def run(self, *args, extra_env=None):
            return subprocess.run(
                ["/bin/bash", str(source / "sync-labs.sh"), *args],
                cwd=tmp_path,
                env={**env, **(extra_env or {})},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
            )

        def calls(self):
            return [json.loads(line) for line in log.read_text().splitlines()]

    result = Workspace()
    result.repo, result.source, result.remote = repo, source, remote
    result.log, result.binaries = log, binaries
    return result


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
    assert {p.name for p in dest.iterdir()} == {*COURSES, "index.html"}
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
    assert [call["transfer"] for call in workspace.calls()] == [False, True]


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
    result = workspace.run("--dry-run", "slurm-login")
    assert_ok(result)
    assert snapshot(workspace.remote) == before
    assert transferred(result)
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
    assert len(calls) == 2
    for call in calls:
        assert call["host"] == host
        assert call["user"] == user
        opts = call["options"]
        assert opts[opts.index("-p") + 1] == "2222"
        assert opts[opts.index("-i") + 1] == str(identity)
        assert "StrictHostKeyChecking=no" not in opts
    assert (workspace.remote / "my-courses/index.html").is_file()
    assert not (workspace.remote / "courses").exists()


def test_discovers_new_course_with_original_folder_name(workspace):
    new = workspace.source / "new course's name"
    (new / "labs").mkdir(parents=True)
    (new / "reference").mkdir()
    (new / "reference/course.json").write_text('{"slug": "different-slug"}')
    (new / "labs/example.py").write_text("# future course\n")
    result = workspace.run("slurm-login")
    assert_ok(result)
    assert "Synced 6 courses" in result.stdout
    assert (workspace.remote / "courses" / new.name / "labs/example.py").is_file()


@pytest.mark.parametrize(
    "args",
    [
        (),
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
            relative.parts[0] in COURSES or relative == Path("index.html")
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
