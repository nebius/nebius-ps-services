from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from threading import Lock

from .config import Settings


class IAMError(RuntimeError):
    """Raised when SDK authentication cannot be initialized."""


def _load_sdk_class():
    from nebius.sdk import SDK

    return SDK


class SDKProvider:
    """Creates and caches an authenticated Nebius SDK client."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sdk = None
        self._lock = Lock()
        self._temp_private_key_path: Path | None = None

    def _ensure_private_key_file(self) -> str:
        if self._settings.nebius_auth_private_key_file:
            return self._settings.nebius_auth_private_key_file

        pem = self._settings.nebius_auth_private_key_pem
        if not pem:
            raise IAMError(
                "Service account auth selected but no private key is available "
                "(NEBIUS_AUTH_PRIVATE_KEY_FILE or NEBIUS_AUTH_PRIVATE_KEY_PEM)."
            )

        if self._temp_private_key_path is None:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix="mbx-sa-key-",
                suffix=".pem",
                delete=False,
                encoding="utf-8",
            ) as handle:
                handle.write(pem)
                handle.flush()
                os.fchmod(handle.fileno(), stat.S_IRUSR | stat.S_IWUSR)
                self._temp_private_key_path = Path(handle.name)

        return str(self._temp_private_key_path)

    def get_sdk(self):
        with self._lock:
            if self._sdk is not None:
                return self._sdk

            SDK = _load_sdk_class()

            try:
                if self._settings.nebius_iam_token:
                    self._sdk = SDK(credentials=self._settings.nebius_iam_token)
                    return self._sdk

                private_key_file = self._ensure_private_key_file()
                kwargs: dict[str, object] = {
                    "service_account_id": self._settings.nebius_sa_id,
                    "service_account_public_key_id": self._settings.nebius_auth_public_key_id,
                    "service_account_private_key_file_name": private_key_file,
                }
                if self._settings.nebius_api_endpoint:
                    kwargs["domain"] = self._settings.nebius_api_endpoint

                self._sdk = SDK(**kwargs)
                return self._sdk
            except Exception as exc:  # pragma: no cover - runtime integration
                raise IAMError(f"Failed to initialize Nebius SDK authentication: {exc}") from exc
