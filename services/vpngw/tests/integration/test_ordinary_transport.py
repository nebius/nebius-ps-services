"""Complete ordinary SSH bootstrap, in the explicit disposable Linux fixture."""

from __future__ import annotations

import base64
import errno
import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from nebius_vpngw import ordinary_bootstrap

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("VPNGW_ISOLATED_SYSTEMD_TEST") != "1",
        reason="requires disposable Linux fixture",
    ),
]


@pytest.mark.parametrize("padding", [0, 200_000])
def test_complete_ordinary_bootstrap_and_request_use_stdin(padding):
    assert Path("/.dockerenv").is_file() and os.geteuid() == 0
    request = {
        "action": "inspect-package",
        "manifest": {"files": {}, "assets": {}},
        "padding": "x" * padding,
    }
    result = subprocess.run(
        shlex.split(ordinary_bootstrap.COMMAND),
        input=ordinary_bootstrap.source_frame() + json.dumps(request).encode(),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode()
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "inspected"
    assert (
        receipt["observation"]["boot_id"]
        == Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    )
    assert receipt["observation"]["route_ownership"] is None


def test_prior_full_source_argument_exceeds_the_actual_linux_limit():
    assert Path("/.dockerenv").is_file()
    old_code = base64.b64decode(ordinary_bootstrap.source_frame()).decode()
    # Freeze the minimum argument that cannot be passed on this running kernel.
    # The real current source already exceeds this on a 4 KiB-page guest.
    old_code += "\n#" + "x" * max(0, os.sysconf("SC_PAGE_SIZE") * 32 - len(old_code))
    with pytest.raises(OSError) as failure:
        subprocess.run(["/usr/bin/python3", "-c", old_code], timeout=30)
    assert failure.value.errno == errno.E2BIG
