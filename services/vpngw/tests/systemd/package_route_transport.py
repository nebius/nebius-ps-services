"""Pre-install route inspection against the authentic 0.6.4 guest interpreter."""

from __future__ import annotations

import base64
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

from nebius_vpngw.deploy.ordinary_runtime_handoff import _BOOTSTRAP, owner_entry


def verify_predecessor_route_transport(system_python, environment) -> dict:
    """Seed only the fixture authority; selected inspection must never change it."""
    seeded = system_python("""
import inspect,json
from importlib.metadata import version
from nebius_vpngw import ordinary_operations as operations
from nebius_vpngw.agent.ordinary_route_authority import RouteAuthority
from nebius_vpngw.apply_approval import digest
assert version('nebius-vpngw') == '0.6.4'
assert 'deadline' not in inspect.signature(operations.mutation_lock).parameters
owner = RouteAuthority()
assert not owner.path.exists() and not owner.path.is_symlink()
assert owner.snapshot() == {'authority': None, 'journal': None}
owner.reserve('a' * 64, digest(owner.snapshot()))
print(json.dumps({'snapshot': owner.snapshot(), 'path': str(owner.path)}))
""")
    authority = Path(seeded["path"])
    original = authority.read_bytes()
    lock = Path("/run/nebius-vpngw/fix-routes.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    frame = base64.b64encode(owner_entry().encode()) + b'\n{"action":"inspect"}\n'
    results = {}
    for case in ("uncontended", "released", "deadline"):
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        child = None
        started = time.monotonic()
        try:
            if case != "uncontended":
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            child = subprocess.Popen(
                ["/usr/bin/python3", "-B", "-c", _BOOTSTRAP],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            if case == "released":
                # Deliver before releasing the real Linux lock. No product source
                # or clock is patched in this authentic installed-package case.
                child.stdin.write(frame)
                child.stdin.flush()
                time.sleep(0.5)
                assert child.poll() is None, "Inspection did not wait for the held lock"
                fcntl.flock(fd, fcntl.LOCK_UN)
                out, err = child.communicate(timeout=20)
            else:
                out, err = child.communicate(frame, timeout=20)
            elapsed = time.monotonic() - started
            if case == "deadline":
                assert child.returncode == 1 and json.loads(out) == {
                    "error": {
                        "schema": "nebius-vpngw.route-handoff-error.v1",
                        "reason": "route-lock-deadline",
                    }
                }
                assert not err and 9 <= elapsed < 20
            else:
                assert child.returncode == 0, f"Route inspection failed: {case}"
                assert json.loads(out) == seeded["snapshot"]
            assert authority.read_bytes() == original, "Inspection changed route authority"
            results[case] = {"exit": child.returncode, "seconds": elapsed, "unchanged": True}
        finally:
            os.close(fd)
            if child is not None and child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    # Remove only the exact fixture record, after proving the command left it intact.
    assert authority.read_bytes() == original
    authority.unlink()
    return {"predecessor": "0.6.4", "cases": results, "fixture_removed": True}
