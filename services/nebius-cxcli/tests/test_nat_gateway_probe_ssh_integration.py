from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
import warnings
from contextlib import suppress
from pathlib import Path

import pytest

from nebius_cxcli.nat_gateway_probes import cloud_init
from nebius_cxcli.ssh_host_identity import generate_host_identity
from test_nat_gateway_probes import evidence, probe_fixture


def cleanup_fixture(name: str, image: str) -> list[str]:
    for command in (
        ["docker", "stop", name],
        ["docker", "rm", name],
        ["docker", "image", "rm", image],
    ):
        # A timed-out client may still have completed; always attempt the
        # remaining exact-resource cleanup, then independently check absence.
        with suppress(OSError, subprocess.TimeoutExpired):
            subprocess.run(command, capture_output=True, timeout=30)
    errors = []
    for kind, command in (
        (
            "container",
            [
                "docker",
                "container",
                "ls",
                "-a",
                "--filter",
                f"name=^/{name}$",
                "--format",
                "{{.Names}}",
            ],
        ),
        (
            "image",
            ["docker", "image", "ls", "--filter", f"reference={image}", "--format", "{{.ID}}"],
        ),
    ):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            if result.returncode or result.stdout.strip():
                errors.append(kind + " absence is unconfirmed")
        except (OSError, subprocess.TimeoutExpired):
            errors.append(kind + " absence is unconfirmed")
    return errors


@pytest.mark.integration
def test_real_private_probe_ssh_through_gateway_in_isolated_linux(tmp_path):
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the isolated Linux SSH fixture")
    try:
        subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("Docker daemon is unavailable")
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/nat_probe_ssh"
    name = "cxcli-nat-probe-ssh-" + uuid.uuid4().hex[:16]
    image = name + ":fixture"
    tmp_path.chmod(0o700)
    pairs = {key: generate_host_identity() for key in ("gateway", "client", "probe", "wrong")}
    for key in ("gateway", "client"):
        path = tmp_path / (key + ".key")
        path.write_text(pairs[key]["ed25519_private"])
        path.chmod(0o600)
    (tmp_path / "known_hosts").write_text("127.0.0.1 " + pairs["gateway"]["ed25519_public"] + "\n")
    (tmp_path / "wrong.pub").write_text(pairs["wrong"]["ed25519_public"])
    probe = probe_fixture()
    probe["host_public_key"] = pairs["probe"]["ed25519_public"]
    (tmp_path / "probe.json").write_text(json.dumps(probe))
    (tmp_path / "result.json").write_bytes(evidence(probe))
    cloud = tmp_path / "cloud-init.yaml"
    cloud.write_text(
        cloud_init(
            probe["config"],
            public_key=pairs["client"]["ed25519_public"],
            host_identity=pairs["probe"],
        )
    )
    cloud.chmod(0o600)
    try:
        subprocess.run(
            ["docker", "build", "-q", "-t", image, str(fixture)],
            check=True,
            capture_output=True,
            timeout=300,
        )
        # Fixture keys remain in the owner-only temporary mount, outside image layers.
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--name",
                name,
                "--network",
                "none",
                "-v",
                f"{tmp_path}:/fixture:ro",
                "-v",
                f"{root / 'src'}:/src:ro",
                "-v",
                f"{fixture / 'check.py'}:/check.py:ro",
                "-e",
                "PYTHONPATH=/src",
                image,
                "python3",
                "/check.py",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr[-4000:]
        assert "PASS: two authenticated SSH hops" in result.stdout
    finally:
        # Exact task-owned resources only; a timed-out client may have launched the container.
        failed = sys.exc_info()[0] is not None
        errors = cleanup_fixture(name, image)
        if errors:
            message = "NAT SSH fixture cleanup unresolved: " + "; ".join(errors)
            if failed:
                warnings.warn(message, RuntimeWarning, stacklevel=1)
            else:
                pytest.fail(message)
