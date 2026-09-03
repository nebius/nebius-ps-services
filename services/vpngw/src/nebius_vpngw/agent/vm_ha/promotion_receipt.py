"""Internal v1 promotion-receipt identity contract."""

from __future__ import annotations

import hashlib
import json

PROMOTION_RECEIPT_SCHEMA = "nebius-vpngw/vm-ha-promotion-receipt-v1"
PROMOTION_RECEIPT_FILENAME = "promotion-receipt.json"


def promotion_receipt_id_v1(
    *,
    allocation_id: str,
    first_operation_id: str,
    generation_id: str,
    intent: str,
    owner_node_id: str,
    ownership_epoch: str,
    route_operation_id: str,
) -> str:
    """Return the byte-stable v1 identity for one terminal promotion."""

    identity = {
        "allocation_id": allocation_id,
        "first_operation_id": first_operation_id,
        "generation_id": generation_id,
        "intent": intent,
        "owner_node_id": owner_node_id,
        "ownership_epoch": ownership_epoch,
        "route_operation_id": route_operation_id,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
