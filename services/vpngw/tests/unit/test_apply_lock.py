from __future__ import annotations

import hashlib
import json
import subprocess
import sys

import pytest

from nebius_vpngw import apply_lock
from nebius_vpngw.deploy.ssh_push import SSHPush


@pytest.mark.parametrize(
    "case", ["canonical", "spaced", "changed", "duplicate", "extra", "symlink", "mode", "large"]
)
def test_streamed_lock_verifier_accepts_only_exact_secure_json(tmp_path, case):
    raw, receipt = SSHPush._vm_ha_apply_lock_payload(
        cluster_id="cluster", node_id="node", generation_id="generation", operation_id="a" * 64
    )
    assert hashlib.sha256(raw).hexdigest() == receipt.record_sha256
    path = tmp_path / "apply.lock"
    payload = json.loads(raw)
    if case == "spaced":
        raw = json.dumps(payload, indent=2).encode()
    elif case == "changed":
        raw = raw.replace(b'"node"', b'"foreign"')
    elif case == "duplicate":
        raw = b'{"node_id":"foreign",' + raw[1:]
    elif case == "extra":
        raw = b'{"extra":true,' + raw[1:]
    elif case == "large":
        raw += b" " * 4096
    path.write_bytes(raw)
    path.chmod(0o600)
    if case == "mode":
        path.chmod(0o644)
    elif case == "symlink":
        original = path
        path = tmp_path / "linked.lock"
        path.symlink_to(original)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            apply_lock.verification_script(),
            str(path),
            receipt.record_sha256,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert (result.returncode == 0) == (case in {"canonical", "spaced"})
    assert not result.stdout
    assert path.read_bytes() == raw
