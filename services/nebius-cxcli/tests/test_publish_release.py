from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)


def test_publish_release_prep_preserves_blank_lines_around_release_sections(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    script_src = Path(__file__).resolve().parents[1] / "publish-release.sh"
    script_dst = repo_root / "publish-release.sh"
    shutil.copy2(script_src, script_dst)
    script_dst.chmod(0o755)

    changelog = repo_root / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "- Prepare release `v0.1.3`.\n\n"
        "## [nebius-cxcli-v0.1.0] - 2026-02-22\n\n"
        "- Initial scaffold.\n",
        encoding="utf-8",
    )

    _run(["git", "init", "-q"], cwd=repo_root)
    _run(["git", "config", "user.name", "Test User"], cwd=repo_root)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo_root)
    _run(["git", "add", "CHANGELOG.md", "publish-release.sh"], cwd=repo_root)
    _run(["git", "commit", "-qm", "init"], cwd=repo_root)

    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    subprocess.run(
        ["bash", "publish-release.sh", "--prep", "0.1.3", "--no-push"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    updated = changelog.read_text(encoding="utf-8")
    assert "## [Unreleased]\n\n## [nebius-cxcli-v0.1.3] - " in updated
    assert "- Prepare release `v0.1.3`.\n\n## [nebius-cxcli-v0.1.0]" in updated


def test_publish_release_prep_sets_upstream_for_new_release_branch(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    origin = tmp_path / "origin.git"
    _run(["git", "init", "--bare", "-q", str(origin)], cwd=tmp_path)

    script_src = Path(__file__).resolve().parents[1] / "publish-release.sh"
    script_dst = repo_root / "publish-release.sh"
    shutil.copy2(script_src, script_dst)
    script_dst.chmod(0o755)

    changelog = repo_root / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "- Prepare release `v0.1.3`.\n\n"
        "## [nebius-cxcli-v0.1.2] - 2026-03-20\n\n"
        "- Previous release.\n",
        encoding="utf-8",
    )

    _run(["git", "init", "-q"], cwd=repo_root)
    _run(["git", "checkout", "-qb", "main"], cwd=repo_root)
    _run(["git", "config", "user.name", "Test User"], cwd=repo_root)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo_root)
    _run(["git", "remote", "add", "origin", str(origin)], cwd=repo_root)
    _run(["git", "add", "CHANGELOG.md", "publish-release.sh"], cwd=repo_root)
    _run(["git", "commit", "-qm", "init"], cwd=repo_root)
    _run(["git", "push", "-u", "origin", "main"], cwd=repo_root)
    _run(["git", "checkout", "-qb", "release-0.1.3"], cwd=repo_root)

    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    subprocess.run(
        ["bash", "publish-release.sh", "--prep", "0.1.3"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    upstream = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert upstream.stdout.strip() == "origin/release-0.1.3"

    remote_branch = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", "refs/heads/release-0.1.3"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert remote_branch.stdout.strip() != ""


def test_publish_release_publish_fails_when_runtime_version_mismatches_tag(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    origin = tmp_path / "origin.git"
    _run(["git", "init", "--bare", "-q", str(origin)], cwd=tmp_path)

    script_src = Path(__file__).resolve().parents[1] / "publish-release.sh"
    script_dst = repo_root / "publish-release.sh"
    shutil.copy2(script_src, script_dst)
    script_dst.chmod(0o755)

    changelog = repo_root / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        "## [nebius-cxcli-v0.1.3] - 2026-03-23\n\n"
        "- Prepare release.\n",
        encoding="utf-8",
    )

    package_dir = repo_root / "src" / "nebius_cxcli"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")

    _run(["git", "init", "-q"], cwd=repo_root)
    _run(["git", "checkout", "-qb", "main"], cwd=repo_root)
    _run(["git", "config", "user.name", "Test User"], cwd=repo_root)
    _run(["git", "config", "user.email", "test@example.com"], cwd=repo_root)
    _run(["git", "remote", "add", "origin", str(origin)], cwd=repo_root)
    _run(["git", "add", "CHANGELOG.md", "publish-release.sh", "src"], cwd=repo_root)
    _run(["git", "commit", "-qm", "init"], cwd=repo_root)
    _run(["git", "push", "-u", "origin", "main"], cwd=repo_root)

    env = os.environ.copy()
    env["NO_COLOR"] = "1"
    result = subprocess.run(
        ["bash", "publish-release.sh", "--publish", "0.1.3"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode != 0
    assert "Runtime version mismatch after tagging." in result.stderr
    remote_tags = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", "refs/tags/nebius-cxcli-v0.1.3"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert remote_tags.stdout.strip() == ""
