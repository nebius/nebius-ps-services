"""Metadata-only KMS/SecretStash access; never reads secret versions or payloads."""

from sdk.inventory import metadata_inventory


def security_metadata(sdk, *, project_id: str, kind: str):
    if kind not in {"secret", "symmetric-key", "asymmetric-key"}:
        raise ValueError("choose secret, symmetric-key or asymmetric-key")
    return metadata_inventory(sdk, kind=kind, parent_id=project_id)
