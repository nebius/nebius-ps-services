from __future__ import annotations

import base64

from .cache import TTLCache
from .config import Settings


class MysteryBoxClientError(RuntimeError):
    """Raised when bridge cannot resolve/read MysteryBox data."""


class MysteryBoxClient:
    """SDK-backed MysteryBox read client for ESO webhook requests."""

    def __init__(self, settings: Settings, sdk) -> None:
        self._settings = settings
        self._sdk = sdk
        self._secret_id_cache: TTLCache[str, str] = TTLCache()
        self._readiness_cache: TTLCache[str, bool] = TTLCache()

        from nebius.api.nebius.mysterybox.v1 import PayloadServiceClient, SecretServiceClient

        self._secret_service = SecretServiceClient(self._sdk)
        self._payload_service = PayloadServiceClient(self._sdk)

    def readiness_check(self) -> None:
        """Run a lightweight auth/API check and cache successful result briefly."""
        cache_key = "__ready__"
        if self._readiness_cache.get(cache_key):
            return

        from nebius.api.nebius.mysterybox.v1 import ListSecretsRequest

        try:
            self._secret_service.list(
                ListSecretsRequest(
                    parent_id=self._settings.nebius_project_id,
                    page_size=1,
                )
            ).wait()
        except Exception as exc:  # pragma: no cover - runtime integration
            raise MysteryBoxClientError(f"Readiness auth/API check failed: {exc}") from exc

        self._readiness_cache.set(
            cache_key,
            True,
            ttl_seconds=self._settings.readiness_check_ttl_seconds,
        )

    @staticmethod
    def _is_secret_id(reference: str) -> bool:
        return reference.startswith("mbsec-")

    def _resolve_secret_id(self, secret_reference: str) -> str:
        if self._is_secret_id(secret_reference):
            return secret_reference

        cached = self._secret_id_cache.get(secret_reference)
        if cached:
            return cached

        from nebius.api.nebius.mysterybox.v1 import GetSecretByNameRequest

        try:
            response = self._secret_service.get_by_name(
                GetSecretByNameRequest(
                    parent_id=self._settings.nebius_project_id,
                    name=secret_reference,
                )
            ).wait()
        except Exception as exc:  # pragma: no cover - runtime integration
            raise MysteryBoxClientError(
                f"Failed to resolve MysteryBox secret by name '{secret_reference}': {exc}"
            ) from exc

        secret_id = getattr(getattr(response, "metadata", None), "id", "")
        if not secret_id:
            raise MysteryBoxClientError(
                f"MysteryBox secret '{secret_reference}' resolved but no metadata.id found"
            )

        self._secret_id_cache.set(
            secret_reference,
            secret_id,
            ttl_seconds=self._settings.secret_id_cache_ttl_seconds,
        )
        return secret_id

    @staticmethod
    def _payload_to_text(payload) -> str:
        oneof = getattr(payload, "payload", None)
        if oneof is None:
            raise MysteryBoxClientError("MysteryBox payload entry has no active payload field")

        field = getattr(oneof, "field", None)
        value = getattr(oneof, "value", None)

        if field == "string_value":
            if not isinstance(value, str):
                raise MysteryBoxClientError("MysteryBox string payload has invalid type")
            return value

        if field == "binary_value":
            if not isinstance(value, (bytes, bytearray)):
                raise MysteryBoxClientError("MysteryBox binary payload has invalid type")
            return base64.b64encode(bytes(value)).decode("ascii")

        raise MysteryBoxClientError(f"MysteryBox payload field '{field}' is not supported")

    def get_value(self, *, secret_reference: str, key: str, version: str | None) -> str:
        if not key:
            raise MysteryBoxClientError("Missing payload key")

        secret_id = self._resolve_secret_id(secret_reference)

        from nebius.api.nebius.mysterybox.v1 import GetPayloadByKeyRequest

        request_kwargs: dict[str, str] = {
            "secret_id": secret_id,
            "key": key,
        }
        if version:
            request_kwargs["version_id"] = version

        try:
            response = self._payload_service.get_by_key(
                GetPayloadByKeyRequest(**request_kwargs)
            ).wait()
        except Exception as exc:  # pragma: no cover - runtime integration
            raise MysteryBoxClientError(
                "Failed to read MysteryBox payload for secret "
                f"'{secret_reference}' key '{key}': {exc}"
            ) from exc

        payload = getattr(response, "data", None)
        if payload is None:
            raise MysteryBoxClientError(
                f"No payload data returned for secret '{secret_reference}' key '{key}'"
            )
        return self._payload_to_text(payload)
