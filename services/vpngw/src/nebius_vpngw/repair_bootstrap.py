"""Stdlib-only repair admission, independent of the installed product package."""

from __future__ import annotations

import base64
import shlex
from pathlib import Path

SOURCE_LIMIT = 4 * 1024 * 1024
DISPLAY_NAME = "Repair"
LOADER = (
    "import base64,signal,sys,time;_repair_bootstrap_deadline=time.monotonic()+600;"
    "\ndef _repair_expired(_signum,_frame):\n raise TimeoutError('repair_request_interrupted')\n"
    "for _sig in (signal.SIGALRM,signal.SIGTERM,signal.SIGHUP): signal.signal(_sig,_repair_expired)\n"
    "signal.alarm(600);"
    f"_source=sys.stdin.buffer.readline({SOURCE_LIMIT + 1});"
    f"\nif len(_source)>{SOURCE_LIMIT} or not _source.endswith(b'\\n'):"
    "\n raise RuntimeError('repair_source_limit')\n"
    "exec(compile(base64.b64decode(_source[:-1],validate=True),'<repair-bootstrap>','exec'))"
)
COMMAND = "sudo -n PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -c " + shlex.quote(LOADER)
SOURCES = (
    ("_vpngw_release_identity", "release_identity.py"),
    ("_vpngw_deployment_assets", "deployment_assets.py"),
    ("_vpngw_repair_state", "repair_state.py"),
    ("_vpngw_underlay_state", "underlay_state.py"),
    ("_vpngw_runtime_migration", "runtime_migration.py"),
    ("_vpngw_package_worker", "package_worker.py"),
    ("_vpngw_prior_package_worker", "package_worker.py"),
    ("_vpngw_dependency_scope", "dependency_scope.py"),
    ("_vpngw_python_package_recovery", "python_package_recovery.py"),
    ("_vpngw_repair_guest", "repair_guest.py"),
    ("_vpngw_repair_runtime", "repair_runtime.py"),
    ("_vpngw_repair_remote", "deploy/repair_remote.py"),
)


def source() -> str:
    root = Path(__file__).parent
    result = "import base64,sys,types\n"
    for name, relative in SOURCES:
        encoded = base64.b64encode((root / relative).read_bytes()).decode()
        result += (
            f"_m=types.ModuleType({name!r});_m.__file__={relative!r};"
            f"_m._SOURCE=base64.b64decode({encoded!r});sys.modules[{name!r}]=_m;"
            f"exec(compile(_m._SOURCE,{relative!r},'exec'),_m.__dict__)\n"
        )
    return (
        result
        + "_vpngw_worker=sys.modules['_vpngw_package_worker'];_vpngw_worker.use_repair_backend()\n"
    )


def worker_source() -> str:
    return source() + "_vpngw_worker.main(sys.argv[1],int(sys.argv[2]),sys.argv[3])\n"


def source_frame() -> bytes:
    code = (
        source()
        + "sys.modules['_vpngw_repair_remote'].control_main(deadline=_repair_bootstrap_deadline)\n"
    )
    frame = base64.b64encode(code.encode()) + b"\n"
    if len(frame) > SOURCE_LIMIT:
        raise RuntimeError("Repair source bundle exceeds limit")
    return frame
