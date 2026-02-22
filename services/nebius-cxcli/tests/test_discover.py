from __future__ import annotations

import subprocess
from pathlib import Path

from nebius_cxcli.discover_ops import discover_configs


def _git_init_and_commit(repo_root: Path) -> None:
    subprocess.run(
        ["git", "init", "-q"],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "add", "."], check=True, cwd=repo_root, capture_output=True, text=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-m", "init"],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )


def test_discover_include_all(tmp_path: Path, monkeypatch) -> None:
    config_path = (
        tmp_path
        / "nebius-deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
        / "config.yaml"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("version: v1\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    payload = discover_configs(deployments_dir="nebius-deployments", include_all=True)

    assert payload == {
        "include": [
            {
                "config": "nebius-deployments/instances/client-a--tenant-123/prod/client-a-prod/config.yaml"
            }
        ]
    }


def test_discover_changed_files_works_on_initial_commit(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    config_path = (
        repo_root
        / "deployments"
        / "instances"
        / "client-a--tenant-123"
        / "prod"
        / "client-a-prod"
        / "config.yaml"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("version: v1\n", encoding="utf-8")
    _git_init_and_commit(repo_root)

    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.delenv("GITHUB_EVENT_BEFORE", raising=False)
    payload = discover_configs(
        deployments_dir="deployments",
        include_all=False,
        repo_root=repo_root,
    )

    assert payload == {
        "include": [
            {
                "config": (
                    "deployments/instances/client-a--tenant-123/prod/client-a-prod/config.yaml"
                )
            }
        ]
    }
