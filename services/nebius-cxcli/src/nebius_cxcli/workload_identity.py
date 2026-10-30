"""Explicit, renewable Kubernetes workload identity through the Nebius SDK."""

from pathlib import Path
from typing import Any


def workload_identity_sdk(
    *,
    service_account_id: str,
    token_file: str | Path,
    parent_id: str,
    user_agent_prefix: str = "nebius-cxcli",
    domain: str = "api.nebius.cloud",
) -> Any:
    """Keep the projected path: resolving its backing symlink breaks rotation."""
    if not service_account_id or not parent_id or not str(token_file):
        raise ValueError("Workload identity requires service_account_id, token_file and parent_id")
    path = Path(token_file).expanduser()
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 32768:
        raise ValueError("Workload identity requires an absolute, readable projected token file")
    from nebius.aio.token.federated_credentials import FederatedCredentialsBearer
    from nebius.sdk import SDK

    sdk = None
    try:
        bearer = FederatedCredentialsBearer(
            federated_credentials=str(path),
            service_account_id=service_account_id,
        )
        sdk = SDK(
            credentials=bearer,
            parent_id=parent_id,
            domain=domain,
            user_agent_prefix=user_agent_prefix,
        )
        bearer.set_channel(sdk)
        return sdk
    except Exception:
        if sdk is not None:
            from contextlib import suppress

            with suppress(Exception):
                sdk.sync_close(timeout=10)
        raise RuntimeError("Nebius workload identity initialization failed") from None
