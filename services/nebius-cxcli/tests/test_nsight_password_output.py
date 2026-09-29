"""Execute only a synthetic kubectl producer; never read a cluster Secret."""

import base64
import shutil
import subprocess
from pathlib import Path

import pytest

from nebius_cxcli.nsight_runtime import password_command


def run_password_command(tmp_path, shell, payload):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is not installed")
    kubectl = tmp_path / "kubectl"
    kubectl.write_text("#!/bin/sh\nprintf '%s' \"$CXCLI_TEST_BASE64\"\n")
    kubectl.chmod(0o700)
    command = password_command(
        kubeconfig=Path("fixture config"),
        context="fixture",
        namespace="soperator",
        secret_name="nsight-streamer-auth",
        secret_key="password",
    )
    return subprocess.run(
        [executable, "-f", "-c", command],
        env={"PATH": f"{tmp_path}:/usr/bin:/bin", "CXCLI_TEST_BASE64": payload},
        capture_output=True,
        timeout=5,
        check=False,
    )


@pytest.mark.parametrize("shell", ["sh", "zsh"])
@pytest.mark.parametrize("value", [b"fixture", b"fixture%", b" spaced\\value $() ' "])
def test_password_display_preserves_bytes_and_finishes_line(tmp_path, shell, value):
    result = run_password_command(tmp_path, shell, base64.b64encode(value).decode())
    assert result.returncode == 0
    assert result.stdout == value + b"\n"
    assert result.stderr == b""


@pytest.mark.parametrize("shell", ["sh", "zsh"])
def test_newline_does_not_hide_decoder_failure(tmp_path, shell):
    result = run_password_command(tmp_path, shell, "!")
    assert result.returncode != 0
    assert result.stdout == b""
