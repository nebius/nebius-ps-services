"""Bounded direct SecretStash access. Never shells out or selects a fallback identity."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def decode_payload(result: Any, version_id: str = "") -> tuple[str, dict[str, str]]:
    values: dict[str, str] = {}
    if not result.version_id or (version_id and version_id != result.version_id):
        raise RuntimeError("SecretStash returned a different version")
    for entry in result.data:
        if not entry.key or entry.key in values or not entry.check_presence("string_value"):
            raise RuntimeError("SecretStash payload schema is invalid")
        values[entry.key] = entry.string_value
    if len(json.dumps(values)) > 32768:
        raise RuntimeError("SecretStash payload exceeds its size bound")
    return result.version_id, values


def validate_auth(value: Any) -> None:
    if not isinstance(value, dict) or set(value) - {
        "profile",
        "config_file",
        "credentials_file",
        "workload_identity",
    }:
        raise ValueError("Unsupported SecretStash Nebius authentication settings")
    if "workload_identity" in value:
        wi = value["workload_identity"]
        if (
            set(value) != {"workload_identity"}
            or not isinstance(wi, dict)
            or set(wi) != {"service_account_id", "token_file"}
            or any(not isinstance(v, str) or not v for v in wi.values())
        ):
            raise ValueError("Select only workload_identity with service_account_id and token_file")
    elif "credentials_file" in value and len(value) != 1:
        raise ValueError("Select credentials_file or profile/config_file, not both")
    elif any(not isinstance(v, str) or not v for v in value.values()):
        raise ValueError("Nebius authentication settings must be nonempty references")


@contextmanager
def sdk_context(project_id: str, auth: dict[str, Any] | None = None):
    from nebius.aio.cli_config import Config
    from nebius.sdk import SDK

    from .sdk_auth import NEBIUS_SDK_USER_AGENT_PREFIX

    auth = auth or {}
    validate_auth(auth)
    if not project_id:
        raise ValueError("SecretStash requires an explicit project_id")
    try:
        if "workload_identity" in auth:
            from .workload_identity import workload_identity_sdk

            sdk = workload_identity_sdk(
                **auth["workload_identity"],
                parent_id=project_id,
                user_agent_prefix=NEBIUS_SDK_USER_AGENT_PREFIX,
            )
        else:
            kwargs: dict[str, Any] = {
                "parent_id": project_id,
                "user_agent_prefix": NEBIUS_SDK_USER_AGENT_PREFIX,
                "federation_invitation_no_browser_open": True,
            }
            if "credentials_file" in auth:
                kwargs["credentials_file_name"] = Path(auth["credentials_file"]).expanduser()
            else:
                config_options: dict[str, Any] = {"no_env": True, "no_parent_id": True}
                if "profile" in auth:
                    config_options["profile"] = auth["profile"]
                if "config_file" in auth:
                    config_options["config_file"] = Path(auth["config_file"]).expanduser()
                kwargs["config_reader"] = Config(**config_options)
            sdk = SDK(**kwargs)
    except Exception:
        raise RuntimeError("SecretStash SDK authentication initialization failed") from None
    try:
        yield sdk
    except BaseException:
        from contextlib import suppress

        with suppress(Exception):
            sdk.sync_close(timeout=10)
        raise
    else:
        try:
            sdk.sync_close(timeout=10)
        except Exception:
            raise RuntimeError("SecretStash SDK cleanup failed") from None


def payload(reference: dict[str, Any], *, version_id: str = "") -> tuple[str, dict[str, str]]:
    from nebius.api.nebius.mysterybox.v1 import GetPayloadRequest, PayloadServiceClient

    if not reference.get("secret_id"):
        raise RuntimeError("SecretStash container ID is unresolved; finish infrastructure setup")
    try:
        with sdk_context(reference["project_id"], reference.get("nebius_auth")) as sdk:
            result = (
                PayloadServiceClient(sdk)
                .get(
                    GetPayloadRequest(
                        secret_id=reference["secret_id"],
                        version_id=version_id or reference.get("version_id", ""),
                    ),
                    timeout=30,
                    auth_timeout=30,
                    per_retry_timeout=10,
                    retries=3,
                )
                .wait()
            )
            return decode_payload(result, version_id or reference.get("version_id", ""))
    except Exception:
        raise RuntimeError(
            "SecretStash payload read failed; check the exact reference and payload-viewer access"
        ) from None


def oauth_pair(
    auth: dict[str, Any], *, base: Path, binding: dict[str, str] | None = None
) -> tuple[str, str, dict[str, str]]:
    """Return values only in memory; the binding contains references, never payloads."""
    from .tailscale_config import secret

    if binding and binding["source"] == "secretstash" or not binding and auth.get("secretstash"):
        if binding and not auth.get("secretstash"):
            raise RuntimeError(
                "Recovery requires an explicit SecretStash authentication reference for the original version"
            )
        ref = dict(auth.get("secretstash", {}))
        if binding:
            ref.update({k: binding[k] for k in ("project_id", "secret_id", "version_id")})
        version, values = payload(ref)
        if set(values) != {"client_id", "client_secret"} or not all(values.values()):
            raise RuntimeError(
                "OAuth SecretStash version requires client_id and client_secret together"
            )
        if binding and values["client_id"] != binding["client_id"]:
            raise RuntimeError(
                "Original OAuth client identity is unavailable; reconcile before rotation"
            )
        pin = {
            "source": "secretstash",
            "project_id": ref["project_id"],
            "secret_id": ref["secret_id"],
            "version_id": version,
            "client_id": values["client_id"],
        }
        return values["client_id"], values["client_secret"], pin
    ref = binding or auth
    if not ref.get("client_id") or not ref.get("client_secret_ref"):
        raise RuntimeError(
            "Scoped OAuth credentials are not configured; administrator handoff required"
        )
    return (
        ref["client_id"],
        secret(ref["client_secret_ref"], base=base),
        {
            "source": "reference",
            "client_id": ref["client_id"],
            "client_secret_ref": ref["client_secret_ref"],
        },
    )
