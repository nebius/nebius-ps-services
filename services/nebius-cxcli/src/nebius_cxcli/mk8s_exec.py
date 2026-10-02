"""Lightweight canonical MK8s ExecCredential command and private cache.

Only a cache miss loads the full runtime-auth provider. Kubernetes invokes this
command in a new process, even when its command-lifetime credential is cached.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Annotated, Any

import typer

from .atomic_files import _write_text_atomic

_fcntl: ModuleType | None
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - non-POSIX runtime fallback
    _fcntl = None


CredentialProvider = Callable[..., dict[str, str]]


def _acquire_status(
    *,
    provider: CredentialProvider,
    project_id: str,
    client_name: str,
    endpoint: str | None,
    require_renewable_auth: bool = False,
) -> dict[str, str]:
    from .sdk_auth import concise_refresh_logs

    with concise_refresh_logs():
        return provider(
            project_id=project_id,
            client_name=client_name,
            endpoint=endpoint,
            require_renewable_auth=require_renewable_auth,
        )


_MK8S_EXEC_CREDENTIAL_CACHE_SCHEMA = "nebius-cxcli/mk8s-exec-credential-cache-v1"
_MK8S_EXEC_CREDENTIAL_REFRESH_SAFETY_SECONDS = 300.0
_MK8S_EXEC_CREDENTIAL_FALLBACK_SECONDS = 30.0
_MK8S_EXEC_CREDENTIAL_REFRESH_FAILURE_COOLDOWN_SECONDS = 60.0


def _mk8s_exec_credential_binding_sha256(
    *,
    project_id: str,
    client_name: str,
    endpoint: str | None,
    require_renewable_auth: bool = False,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "project_id": project_id,
                "client_name": client_name,
                "endpoint": endpoint or "",
                "require_renewable_auth": require_renewable_auth,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _mk8s_exec_credential_expiration(status: Mapping[str, Any]) -> datetime | None:
    return _mk8s_exec_credential_timestamp(status.get("expirationTimestamp"))


def _mk8s_exec_credential_timestamp(value: Any) -> datetime | None:
    raw_value = "" if value is None else str(value).strip()
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _mk8s_exec_credential_cache_payload(
    *,
    binding_sha256: str,
    status: Mapping[str, str],
    refresh_failed_at: datetime | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": _MK8S_EXEC_CREDENTIAL_CACHE_SCHEMA,
        "binding_sha256": binding_sha256,
        "status": dict(status),
        "cached_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    if refresh_failed_at is not None:
        payload["refresh_failed_at"] = (
            refresh_failed_at.astimezone(UTC)
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )
    return payload


def _read_mk8s_exec_credential_cache(
    path: Path,
    *,
    binding_sha256: str,
) -> tuple[dict[str, str], datetime, datetime | None] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise RuntimeError("MK8s exec credential cache must be an owner-only regular file.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != _MK8S_EXEC_CREDENTIAL_CACHE_SCHEMA
        or payload.get("binding_sha256") != binding_sha256
        or not isinstance(payload.get("status"), dict)
    ):
        return None
    status = {
        str(key): str(value)
        for key, value in payload["status"].items()
        if isinstance(key, str) and isinstance(value, str)
    }
    if not str(status.get("token") or "").strip():
        return None
    expiration = _mk8s_exec_credential_expiration(status)
    if expiration is None:
        return None
    refresh_failed_at = _mk8s_exec_credential_timestamp(payload.get("refresh_failed_at"))
    return status, expiration, refresh_failed_at


@contextmanager
def _locked_mk8s_exec_credential_cache(path: Path) -> Iterator[None]:
    if _fcntl is None:
        raise RuntimeError("MK8s exec credential cache locking is unavailable on this platform.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent_metadata = path.parent.stat()
    if (
        path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or parent_metadata.st_mode & 0o077
        or (hasattr(os, "getuid") and parent_metadata.st_uid != os.getuid())
    ):
        raise RuntimeError("MK8s exec credential cache directory must be owner-controlled.")
    lock_path = path.with_name(f".{path.name}.lock")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock_path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or (
            hasattr(os, "getuid") and metadata.st_uid != os.getuid()
        ):
            raise RuntimeError(
                "MK8s exec credential cache lock must be an owner-only regular file."
            )
        _fcntl.flock(fd, _fcntl.LOCK_EX)
        yield
    finally:
        with suppress(OSError):
            _fcntl.flock(fd, _fcntl.LOCK_UN)
        os.close(fd)


def _mk8s_exec_credential_status(
    *,
    provider: CredentialProvider,
    project_id: str,
    client_name: str,
    endpoint: str | None,
    cache_file: Path | None,
    require_renewable_auth: bool = False,
) -> dict[str, str]:
    if cache_file is None:
        return _acquire_status(
            provider=provider,
            project_id=project_id,
            client_name=client_name,
            endpoint=endpoint,
            require_renewable_auth=require_renewable_auth,
        )
    binding_sha256 = _mk8s_exec_credential_binding_sha256(
        project_id=project_id,
        client_name=client_name,
        endpoint=endpoint,
        require_renewable_auth=require_renewable_auth,
    )
    with _locked_mk8s_exec_credential_cache(cache_file):
        cached = _read_mk8s_exec_credential_cache(
            cache_file,
            binding_sha256=binding_sha256,
        )
        now = datetime.now(UTC)
        if (
            cached is not None
            and (cached[1] - now).total_seconds() > _MK8S_EXEC_CREDENTIAL_REFRESH_SAFETY_SECONDS
        ):
            return cached[0]
        refresh_failure_age = (
            (now - cached[2]).total_seconds()
            if cached is not None and cached[2] is not None
            else None
        )
        if (
            cached is not None
            and (cached[1] - now).total_seconds() > _MK8S_EXEC_CREDENTIAL_FALLBACK_SECONDS
            and refresh_failure_age is not None
            and 0 <= refresh_failure_age < _MK8S_EXEC_CREDENTIAL_REFRESH_FAILURE_COOLDOWN_SECONDS
        ):
            return cached[0]
        try:
            status = _acquire_status(
                provider=provider,
                project_id=project_id,
                client_name=client_name,
                endpoint=endpoint,
                require_renewable_auth=require_renewable_auth,
            )
        except Exception:
            fallback_now = datetime.now(UTC)
            if (
                cached is not None
                and (cached[1] - fallback_now).total_seconds()
                > _MK8S_EXEC_CREDENTIAL_FALLBACK_SECONDS
            ):
                with suppress(OSError):
                    _write_text_atomic(
                        cache_file,
                        json.dumps(
                            _mk8s_exec_credential_cache_payload(
                                binding_sha256=binding_sha256,
                                status=cached[0],
                                refresh_failed_at=fallback_now,
                            ),
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        file_mode=0o600,
                    )
                return cached[0]
            raise
        expiration = _mk8s_exec_credential_expiration(status)
        cache_now = datetime.now(UTC)
        if (
            expiration is not None
            and (expiration - cache_now).total_seconds() > _MK8S_EXEC_CREDENTIAL_FALLBACK_SECONDS
        ):
            _write_text_atomic(
                cache_file,
                json.dumps(
                    _mk8s_exec_credential_cache_payload(
                        binding_sha256=binding_sha256,
                        status=status,
                    ),
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                file_mode=0o600,
            )
        return status


def create_mk8s_token_command(provider: CredentialProvider) -> Callable[..., None]:
    """Bind credential acquisition without importing the CLI composition root."""

    def command(
        project_id: Annotated[
            str | None,
            typer.Option("--project-id", help="Project ID used to resolve cached runtime auth."),
        ] = None,
        client_name: Annotated[
            str | None,
            typer.Option("--client-name", help="Client name used to resolve cached runtime auth."),
        ] = None,
        endpoint: Annotated[
            str | None,
            typer.Option("--endpoint", help="Optional Nebius API endpoint override."),
        ] = None,
        cache_file: Annotated[
            Path | None,
            typer.Option(
                "--cache-file",
                hidden=True,
                help="Owner-only command-lifetime ExecCredential cache.",
            ),
        ] = None,
        require_renewable_auth: Annotated[
            bool,
            typer.Option(
                "--require-renewable-auth",
                hidden=True,
                help="Reject one-shot IAM tokens for long-running exec authentication.",
            ),
        ] = False,
    ) -> None:
        """Emit ExecCredential JSON for MK8s kubeconfig exec auth."""
        try:
            status = _mk8s_exec_credential_status(
                provider=provider,
                project_id=project_id or "",
                client_name=client_name or "",
                endpoint=endpoint,
                cache_file=cache_file,
                require_renewable_auth=require_renewable_auth,
            )
            print(
                json.dumps(
                    {
                        "apiVersion": "client.authentication.k8s.io/v1",
                        "kind": "ExecCredential",
                        "status": status,
                    }
                )
            )
        except Exception as exc:  # pragma: no cover - CLI surface
            reason = "timeout" if isinstance(exc, TimeoutError) else "credential-exchange-failed"
            print(
                f"ERROR: Unable to create an MK8s exec credential (reason: {reason}).",
                file=sys.stderr,
            )
            raise typer.Exit(code=1) from None

    return command


def create_app(provider: CredentialProvider) -> typer.Typer:
    """Build the lightweight command group with an entrypoint-owned provider."""
    app = typer.Typer(add_completion=False)

    @app.callback()
    def command_group() -> None:
        """Keep the same subcommand argv shape as the full CLI."""

    app.command("mk8s-token", hidden=True)(create_mk8s_token_command(provider))
    return app
