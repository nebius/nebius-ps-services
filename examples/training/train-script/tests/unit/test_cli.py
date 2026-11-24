import subprocess
import sys

from typer.testing import CliRunner

from nebius_train.cli import app


def test_help_and_version():
    runner = CliRunner()
    for command in (["--help"], ["--version"], ["train", "--help"], ["download", "--help"]):
        assert runner.invoke(app, command).exit_code == 0


def test_missing_config_is_error():
    assert CliRunner().invoke(app, ["train", "--config", "/missing/config.yaml"]).exit_code != 0


def test_cli_import_does_not_import_torch():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import nebius_train.cli; assert 'torch' not in sys.modules",
        ],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0


def test_malformed_yaml_does_not_echo_input(tmp_path):
    path = tmp_path / "malformed.yaml"
    marker = "SYNTHETIC_PRIVATE_INPUT"
    path.write_text(f"field: [{marker}\n")
    result = subprocess.run(
        [sys.executable, "-m", "nebius_train", "validate", "--config", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert marker not in result.stdout + result.stderr
