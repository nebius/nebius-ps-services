"""Non-destructive Ubuntu subprocess/repair-bootstrap test with a fake APT.

Run in a disposable Ubuntu container with --init and Python 3.12. No package
installation, service changes, gateway configuration, or cloud calls occur.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src/nebius_vpngw/repair_bootstrap.py"
SECRET = "apt-fixture-private-value"


def bootstrap():
    spec = importlib.util.spec_from_file_location("repair_bootstrap", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trial(mode: str, root: Path) -> None:
    marker = root / "ready.json"
    fake = root / "apt-get"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json,os,signal,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
        f"with open({str(marker.with_suffix('.tmp'))!r},'w') as stream:\n"
        " json.dump({'pids':[os.getpid(),child.pid],'group':os.getpgrp(),'cwd':os.getcwd(),"
        "'blocked':list(signal.pthread_sigmask(signal.SIG_BLOCK,set()))},stream)\n"
        f"os.replace({str(marker.with_suffix('.tmp'))!r},{str(marker)!r})\n"
        f"sys.stderr.write({SECRET!r});sys.stderr.flush()\n"
        "time.sleep(60)\n"
    )
    fake.chmod(0o700)
    module = bootstrap()
    code = (
        module.source()
        + """
migration=sys.modules['_vpngw_runtime_migration']
remote=sys.modules['_vpngw_repair_remote']
def observe(_request):
    with migration.apt_indexes():
        raise AssertionError('fake APT must not finish')
remote.control=observe
"""
    )
    if mode == "refresh-timeout":
        code += "migration.APT_REFRESH_TIMEOUT=0.5\n"
    if mode == "request-deadline":
        code += "_repair_bootstrap_deadline=time.monotonic()+10.5\n"
    if mode == "inherited-alarm":
        code += "signal.setitimer(signal.ITIMER_REAL,0.5)\n"
    if mode == "spawn-interrupted":
        code += f"""
import os
original_spawn=migration.subprocess.Popen
def interrupt_spawn(*args,**kwargs):
    child=original_spawn(*args,**kwargs)
    cutoff=time.monotonic()+5
    while not os.path.exists({str(marker)!r}) and time.monotonic()<cutoff:
        time.sleep(0.01)
    os.kill(os.getpid(),signal.SIGTERM)
    return child
migration.subprocess.Popen=interrupt_spawn
"""
    code += "remote.control_main(deadline=_repair_bootstrap_deadline)\n"
    process = subprocess.Popen(
        [sys.executable, "-I", "-S", "-B", "-c", module.LOADER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"]},
        start_new_session=True,
    )
    owned = None
    started = time.monotonic()
    try:
        assert process.stdin is not None
        process.stdin.write(base64.b64encode(code.encode()) + b'\n{"action":"inspect"}')
        process.stdin.close()
        process.stdin = None
        while not marker.exists() and process.poll() is None and time.monotonic() - started < 10:
            time.sleep(0.01)
        assert marker.exists(), "fake APT did not start"
        owned = json.loads(marker.read_text())
        if mode.startswith("SIG"):
            os.kill(process.pid, getattr(signal, mode))
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 1, (mode, process.returncode)
        assert time.monotonic() - started < 10
        expected = (
            "apt_repository_timeout"
            if mode == "refresh-timeout"
            else "package_observation_interrupted"
            if mode in {"SIGTERM", "SIGHUP", "spawn-interrupted"}
            else "package_observation_deadline"
        )
        assert json.loads(stdout) == {
            "status": "failed",
            "stage": "inspect",
            "error_code": expected,
        }
        assert not stderr, "guest emitted unexpected diagnostics"
        assert SECRET.encode() not in stdout
        assert not {signal.SIGALRM, signal.SIGTERM, signal.SIGHUP}.intersection(owned["blocked"])
        assert not Path(owned["cwd"]).exists(), "APT temporary workspace survived"
        # --init reaps the orphaned grandchild after the owned group is killed.
        deadline = time.monotonic() + 3
        while (
            any(Path(f"/proc/{pid}").exists() for pid in owned["pids"])
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert all(not Path(f"/proc/{pid}").exists() for pid in owned["pids"]), (
            "APT descendant survived"
        )
        print(f"PASS: {mode}; safe reply, child-group cleanup, temporary-state cleanup", flush=True)
    finally:
        if owned is not None:
            try:
                os.killpg(owned["group"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=5)


def main() -> None:
    if not Path("/.dockerenv").is_file():
        raise RuntimeError("Use a disposable Ubuntu container with --init")
    for mode in (
        "SIGALRM",
        "SIGTERM",
        "SIGHUP",
        "refresh-timeout",
        "request-deadline",
        "inherited-alarm",
        "spawn-interrupted",
    ):
        with tempfile.TemporaryDirectory(prefix="apt-observation-native-") as directory:
            trial(mode, Path(directory))


if __name__ == "__main__":
    main()
