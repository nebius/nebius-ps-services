from __future__ import annotations

import pytest
from typer.testing import CliRunner

from github_report.cli import app

pytestmark = pytest.mark.integration


def test_cli_help_smoke() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Rank GitHub contributors" in result.stdout
