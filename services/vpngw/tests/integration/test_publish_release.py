from __future__ import annotations

import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_SOURCE = PROJECT_ROOT / "publish-release.sh"
RUNTIME_VERSION_SOURCE = PROJECT_ROOT / "src" / "nebius_vpngw" / "runtime_version.py"
INIT_SOURCE = PROJECT_ROOT / "src" / "nebius_vpngw" / "__init__.py"


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path, changelog_text: str, version: str = "0.1.0") -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"

    _run(["git", "init", "--bare", str(remote)], cwd=tmp_path)

    (repo / "src" / "nebius_vpngw").mkdir(parents=True)
    shutil.copy2(SCRIPT_SOURCE, repo / "publish-release.sh")
    script_mode = os.stat(repo / "publish-release.sh").st_mode
    os.chmod(repo / "publish-release.sh", script_mode | stat.S_IXUSR)

    (repo / "CHANGELOG.md").write_text(changelog_text, encoding="utf-8")
    (repo / "src" / "nebius_vpngw" / "__init__.py").write_text(
        f'__version__ = "{version}"\n',
        encoding="utf-8",
    )

    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.name", "tester"], cwd=repo)
    _run(["git", "config", "user.email", "tester@example.com"], cwd=repo)
    _run(["git", "remote", "add", "origin", str(remote)], cwd=repo)
    _run(["git", "branch", "-M", "main"], cwd=repo)
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)
    _run(["git", "push", "-u", "origin", "main"], cwd=repo)

    return repo, remote


def test_prep_sets_upstream_on_first_push(tmp_path: Path) -> None:
    repo, remote = _init_repo(
        tmp_path,
        textwrap.dedent(
            """\
            # Changelog

            ## [Unreleased]

            - initial note
            """
        ),
    )

    _run(["git", "checkout", "-b", "release-0.1.0"], cwd=repo)
    result = _run(["./publish-release.sh", "--prep", "0.1.0"], cwd=repo, check=False)

    assert result.returncode == 0, result.stderr
    assert (
        _run(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], cwd=repo).stdout.strip()
        == "origin/release-0.1.0"
    )
    assert (
        _run(["git", "--git-dir", str(remote), "branch", "--list", "release-0.1.0"], cwd=repo).stdout.strip()
        == "release-0.1.0"
    )
    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [nebius-vpngw-v0.1.0]" in changelog
    assert "## [Unreleased]\n\n## [nebius-vpngw-v0.1.0]" in changelog


def test_prep_fails_when_untracked_files_exist(tmp_path: Path) -> None:
    repo, _remote = _init_repo(
        tmp_path,
        textwrap.dedent(
            """\
            # Changelog

            ## [Unreleased]

            - initial note
            """
        ),
    )

    (repo / "stray.tmp").write_text("temp\n", encoding="utf-8")
    result = _run(["./publish-release.sh", "--prep", "0.1.0"], cwd=repo, check=False)

    assert result.returncode != 0
    assert "Working tree is not clean." in result.stderr
    assert "?? stray.tmp" in result.stderr


def test_prep_fails_when_remote_tag_already_exists(tmp_path: Path) -> None:
    repo, remote = _init_repo(
        tmp_path,
        textwrap.dedent(
            """\
            # Changelog

            ## [Unreleased]

            - initial note
            """
        ),
    )

    _run(["git", "tag", "-a", "nebius-vpngw-v0.1.0", "-m", "Release nebius-vpngw-v0.1.0"], cwd=repo)
    _run(["git", "push", "origin", "refs/tags/nebius-vpngw-v0.1.0"], cwd=repo)
    _run(["git", "tag", "-d", "nebius-vpngw-v0.1.0"], cwd=repo)

    result = _run(["./publish-release.sh", "--prep", "0.1.0", "--no-push"], cwd=repo, check=False)

    assert result.returncode != 0
    assert "Remote tag already exists on origin: nebius-vpngw-v0.1.0" in result.stderr
    assert "## [nebius-vpngw-v0.1.0]" not in (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (
        _run(["git", "--git-dir", str(remote), "tag", "--list", "nebius-vpngw-v0.1.0"], cwd=repo).stdout.strip()
        == "nebius-vpngw-v0.1.0"
    )


def test_publish_fails_when_release_section_is_empty(tmp_path: Path) -> None:
    repo, remote = _init_repo(
        tmp_path,
        textwrap.dedent(
            """\
            # Changelog

            ## [Unreleased]

            ## [nebius-vpngw-v0.1.0] - 2026-03-31
            """
        ),
    )

    result = _run(["./publish-release.sh", "--publish", "0.1.0"], cwd=repo, check=False)

    assert result.returncode != 0
    assert "Changelog section for nebius-vpngw-v0.1.0 is empty" in result.stderr
    assert _run(["git", "tag", "--list", "nebius-vpngw-v0.1.0"], cwd=repo).stdout.strip() == ""
    assert (
        _run(["git", "--git-dir", str(remote), "tag", "--list", "nebius-vpngw-v0.1.0"], cwd=repo).stdout.strip()
        == ""
    )


def test_publish_uses_git_version_when_setuptools_scm_is_unavailable(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"

    _run(["git", "init", "--bare", str(remote)], cwd=tmp_path)

    package_dir = repo / "src" / "nebius_vpngw"
    package_dir.mkdir(parents=True)
    shutil.copy2(SCRIPT_SOURCE, repo / "publish-release.sh")
    shutil.copy2(RUNTIME_VERSION_SOURCE, package_dir / "runtime_version.py")
    shutil.copy2(INIT_SOURCE, package_dir / "__init__.py")

    script_mode = os.stat(repo / "publish-release.sh").st_mode
    os.chmod(repo / "publish-release.sh", script_mode | stat.S_IXUSR)

    (repo / "CHANGELOG.md").write_text(
        textwrap.dedent(
            """\
            # Changelog

            ## [Unreleased]

            ## [nebius-vpngw-v0.1.0] - 2026-03-31

            - release note
            """
        ),
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text("[project]\nname = 'nebius-vpngw'\n", encoding="utf-8")
    (package_dir / "_version.py").write_text('__version__ = version = "0.1.1.dev3"\n', encoding="utf-8")
    (repo / "setuptools_scm.py").write_text('raise ImportError("blocked for test")\n', encoding="utf-8")

    _run(["git", "init"], cwd=repo)
    _run(["git", "config", "user.name", "tester"], cwd=repo)
    _run(["git", "config", "user.email", "tester@example.com"], cwd=repo)
    _run(["git", "remote", "add", "origin", str(remote)], cwd=repo)
    _run(["git", "branch", "-M", "main"], cwd=repo)
    _run(["git", "add", "."], cwd=repo)
    _run(["git", "commit", "-m", "init"], cwd=repo)
    _run(["git", "push", "-u", "origin", "main"], cwd=repo)

    result = _run(["./publish-release.sh", "--publish", "0.1.0"], cwd=repo, check=False)

    assert result.returncode == 0, result.stderr
    assert "Runtime version resolved to 0.1.0." in result.stdout
    assert _run(["git", "tag", "--list", "nebius-vpngw-v0.1.0"], cwd=repo).stdout.strip() == "nebius-vpngw-v0.1.0"
    assert (
        _run(["git", "--git-dir", str(remote), "tag", "--list", "nebius-vpngw-v0.1.0"], cwd=repo).stdout.strip()
        == "nebius-vpngw-v0.1.0"
    )
