"""Bounded stdin transport for the complete, package-independent guest runner."""

from __future__ import annotations

import base64
import shlex
from pathlib import Path

from . import ordinary_operations, ordinary_routes

SOURCE_LIMIT = 4 * 1024 * 1024
LOADER = (
    "import base64,signal,sys,time;"
    "_ordinary_bootstrap_deadline=time.monotonic()+600;signal.alarm(600);"
    f"_source=sys.stdin.buffer.readline({SOURCE_LIMIT + 1});"
    f"\nif len(_source)>{SOURCE_LIMIT} or not _source.endswith(b'\\n'):"
    "\n raise RuntimeError('ordinary_source_limit')\n"
    "exec(compile(base64.b64decode(_source[:-1],validate=True),'<ordinary-bootstrap>','exec'))"
)
COMMAND = "sudo -n env PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -B -c " + shlex.quote(LOADER)


def source_frame() -> bytes:
    source = base64.b64encode(
        (Path(__file__).parent / "deploy" / "ordinary_remote.py").read_bytes()
    ).decode()
    code = (
        ordinary_operations.streamed_bootstrap()
        + ordinary_routes.streamed_bootstrap()
        + f"exec(compile(base64.b64decode({source!r}),'<ordinary-transaction>','exec'))"
    )
    frame = base64.b64encode(code.encode()) + b"\n"
    if len(frame) > SOURCE_LIMIT:
        raise RuntimeError("Ordinary deployment source exceeded its limit")
    return frame
