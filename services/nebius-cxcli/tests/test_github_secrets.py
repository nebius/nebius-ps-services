from __future__ import annotations

from pathlib import Path

from nebius_cxcli.github_secrets import (
    _repo_slug_from_remote_url,
    detect_github_repo_slug,
    read_github_token,
)


def test_repo_slug_from_remote_url_supports_common_forms() -> None:
    assert _repo_slug_from_remote_url("git@github.com:nebius/nebius-ps-services.git") == (
        "nebius/nebius-ps-services"
    )
    assert _repo_slug_from_remote_url("https://github.com/nebius/nebius-ps-services.git") == (
        "nebius/nebius-ps-services"
    )
    assert _repo_slug_from_remote_url("ssh://git@github.com/nebius/nebius-ps-services.git") == (
        "nebius/nebius-ps-services"
    )


def test_detect_github_repo_slug_from_git_origin(tmp_path: Path) -> None:
    import subprocess

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "git@github.com:nebius/nebius-ps-services.git"],
        cwd=repo_root,
        check=True,
    )

    assert detect_github_repo_slug(repo_root) == "nebius/nebius-ps-services"


def test_read_github_token_prefers_requested_env(monkeypatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("MY_GH_TOKEN", "token-123")

    assert read_github_token(preferred_env="MY_GH_TOKEN") == "token-123"


def test_read_github_token_falls_back_to_github_token(monkeypatch) -> None:
    monkeypatch.delenv("MY_GH_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "token-456")

    assert read_github_token(preferred_env="MY_GH_TOKEN") == "token-456"
