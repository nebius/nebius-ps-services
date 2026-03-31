"""GitHub repository and environment secret helpers."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def _repo_slug_from_remote_url(remote_url: str) -> str:
    url = remote_url.strip()
    if not url:
        raise ValueError("Empty git remote URL")

    patterns = [
        r"^git@github\.com:(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, url)
        if not match:
            continue
        owner = match.group("owner")
        repo = match.group("repo")
        return f"{owner}/{repo}"

    raise ValueError(
        f"Unsupported git remote URL '{remote_url}'. "
        "Use --github-repo owner/repo or set origin to a GitHub repository URL."
    )


def detect_github_repo_slug(repo_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "remote", "get-url", "origin"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to resolve git origin remote under '{repo_root}'. "
            "Set --github-repo owner/repo explicitly."
        ) from exc

    try:
        return _repo_slug_from_remote_url(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc


def read_github_token(*, preferred_env: str = "GH_TOKEN") -> str | None:
    if preferred_env:
        preferred = preferred_env.strip()
        if preferred:
            value = os.environ.get(preferred)
            if value:
                return value

    for fallback in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(fallback)
        if value:
            return value
    return None


def build_github_environment_name(*, client_name: str, project_id: str) -> str:
    raw = f"{client_name.strip()}-{project_id.strip()}"
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-").lower()
    if not normalized:
        raise ValueError("Unable to build GitHub environment name from client_name/project_id")
    return normalized[:255]


def _github_request(
    *,
    method: str,
    path: str,
    token: str,
    payload: dict[str, object] | None = None,
) -> dict[str, object] | None:
    base_url = "https://api.github.com"
    url = f"{base_url}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None

    request = Request(url=url, data=data, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    if payload is not None:
        request.add_header("Content-Type", "application/json")

    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            if not body:
                return None
            return json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"GitHub API {method} {path} failed ({exc.code}): {body}") from exc


def _secret_exists(*, path: str, token: str) -> bool:
    request = Request(url=f"https://api.github.com{path}", method="GET")
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")

    try:
        with urlopen(request, timeout=30):
            return True
    except HTTPError as exc:
        if exc.code == 404:
            return False
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"GitHub API GET {path} failed ({exc.code}): {body}") from exc


def repo_secret_exists(*, repo_slug: str, token: str, secret_name: str) -> bool:
    encoded = quote(secret_name, safe="")
    path = f"/repos/{repo_slug}/actions/secrets/{encoded}"
    return _secret_exists(path=path, token=token)


def repo_secrets_presence(*, repo_slug: str, token: str, names: list[str]) -> dict[str, bool]:
    return {
        name: repo_secret_exists(repo_slug=repo_slug, token=token, secret_name=name)
        for name in names
    }


def ensure_github_environment(*, repo_slug: str, token: str, environment_name: str) -> None:
    encoded_environment = quote(environment_name, safe="")
    _github_request(
        method="PUT",
        path=f"/repos/{repo_slug}/environments/{encoded_environment}",
        token=token,
        payload={"deployment_branch_policy": None},
    )


def environment_secret_exists(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    secret_name: str,
) -> bool:
    encoded_environment = quote(environment_name, safe="")
    encoded_secret = quote(secret_name, safe="")
    path = f"/repos/{repo_slug}/environments/{encoded_environment}/secrets/{encoded_secret}"
    return _secret_exists(path=path, token=token)


def environment_secrets_presence(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    names: list[str],
) -> dict[str, bool]:
    return {
        name: environment_secret_exists(
            repo_slug=repo_slug,
            token=token,
            environment_name=environment_name,
            secret_name=name,
        )
        for name in names
    }


def environment_variable_exists(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    variable_name: str,
) -> bool:
    encoded_environment = quote(environment_name, safe="")
    encoded_name = quote(variable_name, safe="")
    path = f"/repos/{repo_slug}/environments/{encoded_environment}/variables/{encoded_name}"
    return _secret_exists(path=path, token=token)


def environment_variables_presence(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    names: list[str],
) -> dict[str, bool]:
    return {
        name: environment_variable_exists(
            repo_slug=repo_slug,
            token=token,
            environment_name=environment_name,
            variable_name=name,
        )
        for name in names
    }


def _encrypt_secret_value(secret_value: str, public_key_b64: str) -> str:
    try:
        from nacl import encoding, public
    except Exception as exc:  # pragma: no cover - runtime integration
        raise RuntimeError(
            'PyNaCl is required to write GitHub secrets. Install dependencies with `pip install -e ".[dev]"`.'
        ) from exc

    key = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(key)
    encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")


def upsert_repo_secret(*, repo_slug: str, token: str, secret_name: str, secret_value: str) -> None:
    key_payload = _github_request(
        method="GET",
        path=f"/repos/{repo_slug}/actions/secrets/public-key",
        token=token,
    )
    if not key_payload:
        raise RuntimeError("GitHub public key response was empty")

    key_id = str(key_payload.get("key_id") or "")
    key = str(key_payload.get("key") or "")
    if not key_id or not key:
        raise RuntimeError("GitHub public key response did not include key_id/key")

    encrypted_value = _encrypt_secret_value(secret_value, key)
    encoded_name = quote(secret_name, safe="")
    _github_request(
        method="PUT",
        path=f"/repos/{repo_slug}/actions/secrets/{encoded_name}",
        token=token,
        payload={"encrypted_value": encrypted_value, "key_id": key_id},
    )


def upsert_repo_secrets(*, repo_slug: str, token: str, secrets: dict[str, str]) -> list[str]:
    updated: list[str] = []
    for secret_name, secret_value in secrets.items():
        upsert_repo_secret(
            repo_slug=repo_slug,
            token=token,
            secret_name=secret_name,
            secret_value=secret_value,
        )
        updated.append(secret_name)
    return updated


def upsert_environment_secret(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    secret_name: str,
    secret_value: str,
) -> None:
    encoded_environment = quote(environment_name, safe="")
    key_payload = _github_request(
        method="GET",
        path=f"/repos/{repo_slug}/environments/{encoded_environment}/secrets/public-key",
        token=token,
    )
    if not key_payload:
        raise RuntimeError("GitHub environment public key response was empty")

    key_id = str(key_payload.get("key_id") or "")
    key = str(key_payload.get("key") or "")
    if not key_id or not key:
        raise RuntimeError("GitHub environment public key response did not include key_id/key")

    encrypted_value = _encrypt_secret_value(secret_value, key)
    encoded_name = quote(secret_name, safe="")
    _github_request(
        method="PUT",
        path=f"/repos/{repo_slug}/environments/{encoded_environment}/secrets/{encoded_name}",
        token=token,
        payload={"encrypted_value": encrypted_value, "key_id": key_id},
    )


def delete_environment_secret(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    secret_name: str,
) -> bool:
    if not environment_secret_exists(
        repo_slug=repo_slug,
        token=token,
        environment_name=environment_name,
        secret_name=secret_name,
    ):
        return False
    encoded_environment = quote(environment_name, safe="")
    encoded_name = quote(secret_name, safe="")
    _github_request(
        method="DELETE",
        path=f"/repos/{repo_slug}/environments/{encoded_environment}/secrets/{encoded_name}",
        token=token,
    )
    return True


def upsert_environment_secrets(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    secrets: dict[str, str],
) -> list[str]:
    ensure_github_environment(repo_slug=repo_slug, token=token, environment_name=environment_name)
    updated: list[str] = []
    for secret_name, secret_value in secrets.items():
        upsert_environment_secret(
            repo_slug=repo_slug,
            token=token,
            environment_name=environment_name,
            secret_name=secret_name,
            secret_value=secret_value,
        )
        updated.append(secret_name)
    return updated


def upsert_environment_variable(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    variable_name: str,
    variable_value: str,
) -> None:
    encoded_environment = quote(environment_name, safe="")
    encoded_name = quote(variable_name, safe="")
    payload = {"name": variable_name, "value": variable_value}
    if environment_variable_exists(
        repo_slug=repo_slug,
        token=token,
        environment_name=environment_name,
        variable_name=variable_name,
    ):
        _github_request(
            method="PATCH",
            path=f"/repos/{repo_slug}/environments/{encoded_environment}/variables/{encoded_name}",
            token=token,
            payload=payload,
        )
        return
    _github_request(
        method="POST",
        path=f"/repos/{repo_slug}/environments/{encoded_environment}/variables",
        token=token,
        payload=payload,
    )


def delete_environment_variable(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    variable_name: str,
) -> bool:
    if not environment_variable_exists(
        repo_slug=repo_slug,
        token=token,
        environment_name=environment_name,
        variable_name=variable_name,
    ):
        return False
    encoded_environment = quote(environment_name, safe="")
    encoded_name = quote(variable_name, safe="")
    _github_request(
        method="DELETE",
        path=f"/repos/{repo_slug}/environments/{encoded_environment}/variables/{encoded_name}",
        token=token,
    )
    return True


def upsert_environment_variables(
    *,
    repo_slug: str,
    token: str,
    environment_name: str,
    variables: dict[str, str],
) -> list[str]:
    ensure_github_environment(repo_slug=repo_slug, token=token, environment_name=environment_name)
    updated: list[str] = []
    for variable_name, variable_value in variables.items():
        upsert_environment_variable(
            repo_slug=repo_slug,
            token=token,
            environment_name=environment_name,
            variable_name=variable_name,
            variable_value=variable_value,
        )
        updated.append(variable_name)
    return updated


__all__ = [
    "build_github_environment_name",
    "delete_environment_secret",
    "delete_environment_variable",
    "detect_github_repo_slug",
    "ensure_github_environment",
    "environment_secrets_presence",
    "environment_variable_exists",
    "environment_variables_presence",
    "read_github_token",
    "repo_secrets_presence",
    "upsert_environment_secrets",
    "upsert_environment_variable",
    "upsert_environment_variables",
    "upsert_repo_secrets",
]
