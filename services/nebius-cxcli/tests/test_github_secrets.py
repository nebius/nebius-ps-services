from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest

import nebius_cxcli.github_secrets as github_secrets
from nebius_cxcli.github_secrets import (
    _repo_slug_from_remote_url,
    build_github_environment_name,
    delete_environment_secret,
    delete_environment_variable,
    detect_github_repo_slug,
    ensure_github_environment,
    read_github_token,
    upsert_environment_secrets,
    upsert_environment_variables,
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


def test_build_github_environment_name_normalizes_tokens() -> None:
    assert (
        build_github_environment_name(client_name="Client A", project_id="project-123")
        == "client-a-project-123"
    )


def test_ensure_github_environment_puts_encoded_environment(monkeypatch) -> None:
    from urllib.error import HTTPError

    captured: dict[str, object] = {}

    def missing(request, **kwargs):
        raise HTTPError(request.full_url, 404, "missing", {}, None)

    monkeypatch.setattr(github_secrets, "urlopen", missing)

    monkeypatch.setattr(
        github_secrets,
        "_github_request",
        lambda *, method, path, token, payload=None: captured.update(
            {
                "method": method,
                "path": path,
                "token": token,
                "payload": payload,
            }
        ),
    )

    ensure_github_environment(
        repo_slug="owner/repo",
        token="gh-token",
        environment_name="Client A / project-123",
    )

    assert captured == {
        "method": "PUT",
        "path": f"/repos/owner/repo/environments/{quote('Client A / project-123', safe='')}",
        "token": "gh-token",
        "payload": {},
    }


def test_upsert_environment_secrets_ensures_environment_then_upserts_each_secret(
    monkeypatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        github_secrets,
        "ensure_github_environment",
        lambda *, repo_slug, token, environment_name: calls.append(
            (
                "ensure",
                {
                    "repo_slug": repo_slug,
                    "token": token,
                    "environment_name": environment_name,
                },
            )
        ),
    )
    monkeypatch.setattr(
        github_secrets,
        "upsert_environment_secret",
        lambda *, repo_slug, token, environment_name, secret_name, secret_value: calls.append(
            (
                "secret",
                {
                    "repo_slug": repo_slug,
                    "token": token,
                    "environment_name": environment_name,
                    "secret_name": secret_name,
                    "secret_value": secret_value,
                },
            )
        ),
    )

    updated = upsert_environment_secrets(
        repo_slug="owner/repo",
        token="gh-token",
        environment_name="client-a-project-123",
        secrets={
            "NEBIUS_SA_ID": "sa-123",
            "FLUX_GITHUB_TOKEN": "flux-token",
        },
    )

    assert updated == ["NEBIUS_SA_ID", "FLUX_GITHUB_TOKEN"]
    assert calls == [
        (
            "ensure",
            {
                "repo_slug": "owner/repo",
                "token": "gh-token",
                "environment_name": "client-a-project-123",
            },
        ),
        (
            "secret",
            {
                "repo_slug": "owner/repo",
                "token": "gh-token",
                "environment_name": "client-a-project-123",
                "secret_name": "NEBIUS_SA_ID",
                "secret_value": "sa-123",
            },
        ),
        (
            "secret",
            {
                "repo_slug": "owner/repo",
                "token": "gh-token",
                "environment_name": "client-a-project-123",
                "secret_name": "FLUX_GITHUB_TOKEN",
                "secret_value": "flux-token",
            },
        ),
    ]


def test_upsert_environment_variables_ensures_environment_then_upserts_each_variable(
    monkeypatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    monkeypatch.setattr(
        github_secrets,
        "ensure_github_environment",
        lambda *, repo_slug, token, environment_name: calls.append(
            (
                "ensure",
                {
                    "repo_slug": repo_slug,
                    "token": token,
                    "environment_name": environment_name,
                },
            )
        ),
    )
    monkeypatch.setattr(
        github_secrets,
        "upsert_environment_variable",
        lambda *, repo_slug, token, environment_name, variable_name, variable_value: calls.append(
            (
                "variable",
                {
                    "repo_slug": repo_slug,
                    "token": token,
                    "environment_name": environment_name,
                    "variable_name": variable_name,
                    "variable_value": variable_value,
                },
            )
        ),
    )

    updated = upsert_environment_variables(
        repo_slug="owner/repo",
        token="gh-token",
        environment_name="client-a-project-123",
        variables={
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
        },
    )

    assert updated == ["SMTP_HOST", "SMTP_PORT"]
    assert calls == [
        (
            "ensure",
            {
                "repo_slug": "owner/repo",
                "token": "gh-token",
                "environment_name": "client-a-project-123",
            },
        ),
        (
            "variable",
            {
                "repo_slug": "owner/repo",
                "token": "gh-token",
                "environment_name": "client-a-project-123",
                "variable_name": "SMTP_HOST",
                "variable_value": "smtp.example.com",
            },
        ),
        (
            "variable",
            {
                "repo_slug": "owner/repo",
                "token": "gh-token",
                "environment_name": "client-a-project-123",
                "variable_name": "SMTP_PORT",
                "variable_value": "587",
            },
        ),
    ]


@pytest.mark.parametrize("exists", [True, False])
def test_environment_variable_presence_queries_encoded_resource(monkeypatch, exists) -> None:
    from io import BytesIO
    from urllib.error import HTTPError

    paths = []

    def urlopen(request, timeout):
        paths.append((request.method, request.full_url))
        if not exists:
            raise HTTPError(request.full_url, 404, "Not Found", {}, BytesIO(b"{}"))
        return BytesIO(b"{}")

    monkeypatch.setattr(github_secrets, "urlopen", urlopen)
    assert github_secrets.environment_variables_presence(
        repo_slug="owner/repo",
        token="test-token",
        environment_name="Client A / project",
        names=["SMTP_HOST"],
    ) == {"SMTP_HOST": exists}
    assert paths == [
        (
            "GET",
            "https://api.github.com/repos/owner/repo/environments/Client%20A%20%2F%20project/variables/SMTP_HOST",
        )
    ]


def test_delete_environment_variable_deletes_when_present(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        github_secrets,
        "environment_variable_exists",
        lambda *, repo_slug, token, environment_name, variable_name: True,
    )
    monkeypatch.setattr(
        github_secrets,
        "_github_request",
        lambda *, method, path, token, payload=None: captured.update(
            {
                "method": method,
                "path": path,
                "token": token,
                "payload": payload,
            }
        ),
    )

    deleted = delete_environment_variable(
        repo_slug="owner/repo",
        token="gh-token",
        environment_name="client-a-project-123",
        variable_name="SMTP_HOST",
    )

    assert deleted is True
    assert captured == {
        "method": "DELETE",
        "path": f"/repos/owner/repo/environments/{quote('client-a-project-123', safe='')}/variables/SMTP_HOST",
        "token": "gh-token",
        "payload": None,
    }


def test_delete_environment_secret_deletes_when_present(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        github_secrets,
        "environment_secret_exists",
        lambda *, repo_slug, token, environment_name, secret_name: True,
    )
    monkeypatch.setattr(
        github_secrets,
        "_github_request",
        lambda *, method, path, token, payload=None: captured.update(
            {
                "method": method,
                "path": path,
                "token": token,
                "payload": payload,
            }
        ),
    )

    deleted = delete_environment_secret(
        repo_slug="owner/repo",
        token="gh-token",
        environment_name="client-a-project-123",
        secret_name="SMTP_PASSWORD",
    )

    assert deleted is True
    assert captured == {
        "method": "DELETE",
        "path": f"/repos/owner/repo/environments/{quote('client-a-project-123', safe='')}/secrets/SMTP_PASSWORD",
        "token": "gh-token",
        "payload": None,
    }


@pytest.mark.parametrize(
    "policy",
    [
        {"protected_branches": True, "custom_branch_policies": False},
        {"protected_branches": False, "custom_branch_policies": True},
    ],
)
@pytest.mark.parametrize("operation", ["ensure", "secrets", "variables"])
def test_environment_sync_preserves_existing_protection(monkeypatch, policy, operation):
    import json
    from contextlib import nullcontext
    from io import BytesIO

    reads = []
    writes = []

    def read(request, **kwargs):
        reads.append(request.method)
        return nullcontext(BytesIO(json.dumps({"deployment_branch_policy": policy}).encode()))

    monkeypatch.setattr(github_secrets, "urlopen", read)
    monkeypatch.setattr(github_secrets, "_github_request", lambda **kwargs: writes.append(kwargs))
    monkeypatch.setattr(github_secrets, "upsert_environment_secret", lambda **kwargs: None)
    monkeypatch.setattr(github_secrets, "upsert_environment_variable", lambda **kwargs: None)
    kwargs = dict(repo_slug="owner/repo", token="fixture", environment_name="protected")
    if operation == "ensure":
        ensure_github_environment(**kwargs)
    elif operation == "secrets":
        upsert_environment_secrets(**kwargs, secrets={"FIXTURE": "fixture"})
    else:
        upsert_environment_variables(**kwargs, variables={"FIXTURE": "fixture"})
    assert reads == ["GET"]
    assert writes == []


@pytest.mark.parametrize("status", [401, 403, 500])
def test_environment_sync_does_not_create_after_failed_lookup(monkeypatch, status):
    from urllib.error import HTTPError

    def fail(request, **kwargs):
        raise HTTPError(request.full_url, status, "fixture", {}, None)

    writes = []
    monkeypatch.setattr(github_secrets, "urlopen", fail)
    monkeypatch.setattr(github_secrets, "_github_request", lambda **kwargs: writes.append(kwargs))
    with pytest.raises(RuntimeError, match=str(status)):
        ensure_github_environment(repo_slug="owner/repo", token="fixture", environment_name="test")
    assert writes == []
