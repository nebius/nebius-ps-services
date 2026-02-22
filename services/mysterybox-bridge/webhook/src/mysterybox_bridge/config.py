from __future__ import annotations

import os
from dataclasses import dataclass


class SettingsError(ValueError):
    """Raised when required runtime settings are missing or invalid."""


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Settings:
    listen_host: str
    listen_port: int
    secret_endpoint_path: str
    log_level: str

    nebius_project_id: str
    nebius_iam_token: str | None
    nebius_sa_id: str | None
    nebius_auth_public_key_id: str | None
    nebius_auth_private_key_pem: str | None
    nebius_auth_private_key_file: str | None
    nebius_api_endpoint: str | None

    webhook_auth_header: str | None
    webhook_auth_token: str | None

    secret_id_cache_ttl_seconds: int
    readiness_check_ttl_seconds: int = 30

    @classmethod
    def from_env(cls) -> Settings:
        settings = cls(
            listen_host=_env("LISTEN_HOST") or "0.0.0.0",
            listen_port=_env_int("LISTEN_PORT", 8080),
            secret_endpoint_path=_env("SECRET_ENDPOINT_PATH") or "/v1/secret",
            log_level=(_env("LOG_LEVEL") or "INFO").upper(),
            nebius_project_id=_env("NEBIUS_PROJECT_ID") or "",
            nebius_iam_token=_env("NEBIUS_IAM_TOKEN"),
            nebius_sa_id=_env("NEBIUS_SA_ID"),
            nebius_auth_public_key_id=_env("NEBIUS_AUTH_PUBLIC_KEY_ID"),
            nebius_auth_private_key_pem=os.getenv("NEBIUS_AUTH_PRIVATE_KEY_PEM"),
            nebius_auth_private_key_file=_env("NEBIUS_AUTH_PRIVATE_KEY_FILE"),
            nebius_api_endpoint=_env("NEBIUS_API_ENDPOINT"),
            webhook_auth_header=_env("MYSTERYBOX_WEBHOOK_AUTH_HEADER"),
            webhook_auth_token=_env("MYSTERYBOX_WEBHOOK_AUTH_TOKEN"),
            secret_id_cache_ttl_seconds=_env_int("MYSTERYBOX_SECRET_ID_CACHE_TTL", 300),
            readiness_check_ttl_seconds=_env_int("MYSTERYBOX_READINESS_CACHE_TTL", 30),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.nebius_project_id:
            raise SettingsError("NEBIUS_PROJECT_ID is required")

        has_token = bool(self.nebius_iam_token)
        has_service_account = bool(
            self.nebius_sa_id
            and self.nebius_auth_public_key_id
            and (self.nebius_auth_private_key_file or self.nebius_auth_private_key_pem)
        )
        if not (has_token or has_service_account):
            raise SettingsError(
                "Missing Nebius auth settings. Provide either NEBIUS_IAM_TOKEN, or "
                "NEBIUS_SA_ID + NEBIUS_AUTH_PUBLIC_KEY_ID + "
                "(NEBIUS_AUTH_PRIVATE_KEY_FILE or NEBIUS_AUTH_PRIVATE_KEY_PEM)."
            )

        if self.listen_port < 1 or self.listen_port > 65535:
            raise SettingsError("LISTEN_PORT must be between 1 and 65535")
        if self.secret_id_cache_ttl_seconds < 1:
            raise SettingsError("MYSTERYBOX_SECRET_ID_CACHE_TTL must be >= 1")
        if self.readiness_check_ttl_seconds < 1:
            raise SettingsError("MYSTERYBOX_READINESS_CACHE_TTL must be >= 1")

        if self.webhook_auth_header and not self.webhook_auth_token:
            raise SettingsError(
                "MYSTERYBOX_WEBHOOK_AUTH_TOKEN is required when "
                "MYSTERYBOX_WEBHOOK_AUTH_HEADER is set"
            )
        if self.webhook_auth_token and not self.webhook_auth_header:
            raise SettingsError(
                "MYSTERYBOX_WEBHOOK_AUTH_HEADER is required when "
                "MYSTERYBOX_WEBHOOK_AUTH_TOKEN is set"
            )
