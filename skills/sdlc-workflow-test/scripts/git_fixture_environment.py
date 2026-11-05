"""Test-only Git configuration isolation; never used by the live verifier."""

from contextlib import contextmanager
import os
from pathlib import Path
import tempfile
from unittest import mock


@contextmanager
def isolated_git_configuration():
    """Keep host settings out of fixtures while allowing explicit hostile inputs."""
    with tempfile.TemporaryDirectory(prefix="sdlc-git-test-") as directory:
        root = Path(directory).resolve()
        user_home = root / "user"
        xdg_config = root / "xdg"
        user_home.mkdir(mode=0o700)
        xdg_config.mkdir(mode=0o700)
        with mock.patch.dict(
            os.environ,
            {
                "HOME": str(user_home),
                "XDG_CONFIG_HOME": str(xdg_config),
                "GIT_CONFIG_NOSYSTEM": "1",
            },
        ):
            yield user_home
