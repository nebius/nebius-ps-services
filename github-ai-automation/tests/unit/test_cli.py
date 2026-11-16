from typer.testing import CliRunner

from github_ai_automation.cli import app


def test_cli():
    runner = CliRunner()
    assert runner.invoke(app, ["health"]).stdout.strip() == "ok"
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert runner.invoke(app, ["--version"]).exit_code == 0
