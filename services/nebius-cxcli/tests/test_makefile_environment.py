from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _write_executable(path: Path, payload: str) -> None:
    path.write_text(payload, encoding="utf-8")
    path.chmod(0o755)


_FAKE_UV = """#!/bin/sh
set -eu
printf '%s|%s\\n' "${UV_PROJECT_ENVIRONMENT:-}" "$*" >> "$FAKE_UV_LOG"
if [ "${FAKE_UV_FAIL_LOCK:-0}" = "1" ] && [ "${1:-}" = "lock" ]; then
    exit 42
fi
if [ "${1:-}" = "sync" ] && [ -n "${FAKE_SYNC_GUARD:-}" ]; then
    if ! mkdir "$FAKE_SYNC_GUARD" 2>/dev/null; then
        touch "$FAKE_SYNC_OVERLAP"
        exit 43
    fi
    trap 'rmdir "$FAKE_SYNC_GUARD" 2>/dev/null || true' EXIT
    sleep "${FAKE_SYNC_DELAY:-0}"
fi
exit 0
"""


def _fake_uv_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    fake_uv = tmp_path / "fake-uv"
    log = tmp_path / "uv.log"
    _write_executable(fake_uv, _FAKE_UV)
    environment = {
        **os.environ,
        "FAKE_UV_LOG": str(log),
    }
    return fake_uv, environment


def _run_make_env(
    *,
    venv: Path,
    uv: Path,
    environment: dict[str, str],
    python: str = sys.executable,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "env",
            f"VENV={venv}",
            f"PYTHON={python}",
            f"UV={uv}",
        ],
        cwd=_project_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_make_env_maps_a_safe_custom_environment_to_uv(tmp_path: Path) -> None:
    fake_uv, environment = _fake_uv_environment(tmp_path)
    venv = tmp_path / "custom-venv"

    result = _run_make_env(venv=venv, uv=fake_uv, environment=environment)

    assert result.returncode == 0, result.stderr
    calls = Path(environment["FAKE_UV_LOG"]).read_text(encoding="utf-8").splitlines()
    assert calls[0] == (
        f"{venv.resolve()}|lock --check --python {sys.executable} --no-python-downloads"
    )
    assert any(
        call.startswith(f"{venv.resolve()}|sync --locked --python ")
        and call.endswith(" --no-python-downloads")
        for call in calls
    )
    run_calls = [call for call in calls if "|run " in call]
    assert len(run_calls) == 2
    assert all(call.startswith(f"{venv.resolve()}|run --locked --no-sync ") for call in run_calls)
    assert all("--no-python-downloads" in call for call in run_calls)
    assert all("--active" not in call for call in calls)


def test_make_env_ignores_an_unrelated_active_environment(tmp_path: Path) -> None:
    fake_uv, environment = _fake_uv_environment(tmp_path)
    environment["VIRTUAL_ENV"] = str(tmp_path / "foreign-active-venv")
    venv = tmp_path / "selected-venv"

    result = _run_make_env(venv=venv, uv=fake_uv, environment=environment)

    assert result.returncode == 0, result.stderr
    calls = Path(environment["FAKE_UV_LOG"]).read_text(encoding="utf-8")
    assert str(venv.resolve()) in calls
    assert environment["VIRTUAL_ENV"] not in calls
    assert "--active" not in calls


def test_make_env_fails_before_sync_when_uv_is_missing(tmp_path: Path) -> None:
    missing_uv = tmp_path / "missing-uv"
    venv = tmp_path / "custom-venv"

    result = _run_make_env(venv=venv, uv=missing_uv, environment={**os.environ})

    assert result.returncode != 0
    assert "uv is required for repository development" in result.stderr
    assert not venv.exists()


def test_make_env_propagates_locked_failure_without_rewriting_lock(tmp_path: Path) -> None:
    fake_uv, environment = _fake_uv_environment(tmp_path)
    environment["FAKE_UV_FAIL_LOCK"] = "1"
    lock_path = _project_root() / "uv.lock"
    before = lock_path.read_bytes()

    result = _run_make_env(
        venv=tmp_path / "custom-venv",
        uv=fake_uv,
        environment=environment,
    )

    assert result.returncode != 0
    assert lock_path.read_bytes() == before
    calls = Path(environment["FAKE_UV_LOG"]).read_text(encoding="utf-8").splitlines()
    assert calls == [
        f"{(tmp_path / 'custom-venv').resolve()}|lock --check "
        f"--python {sys.executable} --no-python-downloads"
    ]


def test_make_env_does_not_modify_an_unrecognized_directory(tmp_path: Path) -> None:
    fake_uv, environment = _fake_uv_environment(tmp_path)
    venv = tmp_path / "not-a-venv"
    venv.mkdir()
    sentinel = venv / "preserve-me"
    sentinel.write_text("unchanged\n", encoding="utf-8")

    result = _run_make_env(venv=venv, uv=fake_uv, environment=environment)

    assert result.returncode != 0
    assert "Refusing an unrecognized VENV path." in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "unchanged\n"
    assert not Path(environment["FAKE_UV_LOG"]).exists()


def test_make_env_rejects_a_symlinked_environment(tmp_path: Path) -> None:
    fake_uv, environment = _fake_uv_environment(tmp_path)
    target = tmp_path / "target-venv"
    target.mkdir()
    (target / "pyvenv.cfg").write_text("home = test\n", encoding="utf-8")
    venv = tmp_path / "linked-venv"
    venv.symlink_to(target, target_is_directory=True)

    result = _run_make_env(venv=venv, uv=fake_uv, environment=environment)

    assert result.returncode != 0
    assert "Refusing a symlinked VENV path." in result.stderr
    assert (target / "pyvenv.cfg").is_file()


def test_make_env_rejects_project_and_ancestor_paths(tmp_path: Path) -> None:
    fake_uv, environment = _fake_uv_environment(tmp_path)
    project = _project_root()

    for unsafe in (project, project.parent, Path(project.anchor)):
        result = _run_make_env(venv=unsafe, uv=fake_uv, environment=environment)
        assert result.returncode != 0
        assert "Refusing an unsafe VENV path." in result.stderr


def test_make_env_rejects_whitespace_before_uv_runs(tmp_path: Path) -> None:
    fake_uv, environment = _fake_uv_environment(tmp_path)
    venv = tmp_path / "custom venv"

    result = _run_make_env(venv=venv, uv=fake_uv, environment=environment)

    assert result.returncode != 0
    assert "Refusing a VENV path containing whitespace." in result.stderr
    assert not Path(environment["FAKE_UV_LOG"]).exists()


def test_make_env_serializes_concurrent_syncs(tmp_path: Path) -> None:
    fake_uv, environment = _fake_uv_environment(tmp_path)
    environment.update(
        {
            "FAKE_SYNC_DELAY": "0.2",
            "FAKE_SYNC_GUARD": str(tmp_path / "sync-active"),
            "FAKE_SYNC_OVERLAP": str(tmp_path / "sync-overlap"),
        }
    )
    venv = tmp_path / "shared-venv"
    command = [
        "make",
        "--no-print-directory",
        "env",
        f"VENV={venv}",
        f"PYTHON={sys.executable}",
        f"UV={fake_uv}",
    ]

    processes = [
        subprocess.Popen(
            command,
            cwd=_project_root(),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    results = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [0, 0], results
    assert not Path(environment["FAKE_SYNC_OVERLAP"]).exists()
    calls = Path(environment["FAKE_UV_LOG"]).read_text(encoding="utf-8")
    assert calls.count("|sync --locked") == 2


def test_makefile_uses_one_locked_uv_authority() -> None:
    makefile = (_project_root() / "Makefile").read_text(encoding="utf-8")

    assert "UV ?= uv" in makefile
    assert '$(UV_ENV) "$(UV)" lock --check --python "$(PYTHON)" --no-python-downloads' in makefile
    assert '"$(UV)" sync --locked' in makefile
    assert '"$(UV)" run --locked --no-sync --no-python-downloads' in makefile
    assert '"$(UV)" build --wheel' in makefile
    assert '--build-constraints "$$build_constraints" --require-hashes' in makefile
    assert '"$(UV)" venv' in makefile
    assert '"$(UV)" export --locked --no-dev --no-emit-project' in makefile
    assert '"$(UV)" pip install' in makefile
    assert '"$(UV)" pip check' in makefile
    assert "--active" not in makefile
    assert "ensurepip" not in makefile
    assert "python -m pip" not in makefile
    assert ".[dev]" not in makefile
    assert ".installed" not in makefile
    assert "check_dev_environment.py" not in makefile
    assert "install_dev_environment.py" not in makefile
    assert "\nvenv:" not in makefile
    assert "\ninstall:" not in makefile


def test_makefile_serializes_wheel_verification_after_build() -> None:
    makefile = (_project_root() / "Makefile").read_text(encoding="utf-8")

    assert "verify-wheel-cli: build verify-wheel-cli-dist" not in makefile
    assert (
        "verify-wheel-cli: build\n\t$(MAKE) --no-print-directory verify-wheel-cli-dist"
    ) in makefile
    assert "Expected exactly one wheel in dist/" in makefile
    assert '--console-script "$$wheel_venv/bin/nebius-cxcli"' in makefile


def test_pyproject_uses_a_uv_development_group() -> None:
    with (_project_root() / "pyproject.toml").open("rb") as stream:
        pyproject = tomllib.load(stream)

    project = pyproject["project"]
    assert "dev" not in project.get("optional-dependencies", {})
    dev = pyproject["dependency-groups"]["dev"]
    assert not any(requirement.startswith("build") for requirement in dev)
    assert any(requirement.startswith("setuptools>=83.0.0,<84.0.0") for requirement in dev)
    assert any(requirement.startswith("setuptools-scm") for requirement in dev)
    assert pyproject["tool"]["uv"] == {
        "required-version": ">=0.12.9,<0.13",
        "default-groups": ["dev"],
    }


def test_current_environment_matches_the_locked_project() -> None:
    environment = {**os.environ}
    environment.pop("VIRTUAL_ENV", None)
    result = subprocess.run(
        ["uv", "sync", "--locked", "--check", "--no-python-downloads"],
        cwd=_project_root(),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
