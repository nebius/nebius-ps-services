from __future__ import annotations

import base64

import pytest
from nebius.api.nebius.mysterybox.v1 import Payload

from mysterybox_bridge.mysterybox_client import MysteryBoxClient, MysteryBoxClientError


def test_payload_to_text_supports_string_payload() -> None:
    payload = Payload()
    payload.string_value = "plain-text"

    value = MysteryBoxClient._payload_to_text(payload)

    assert value == "plain-text"


def test_payload_to_text_supports_binary_payload() -> None:
    payload = Payload()
    payload.binary_value = b"\x00\x01\x02"

    value = MysteryBoxClient._payload_to_text(payload)

    assert value == base64.b64encode(b"\x00\x01\x02").decode("ascii")


def test_payload_to_text_rejects_payload_without_active_field() -> None:
    payload = Payload()

    with pytest.raises(MysteryBoxClientError, match="no active payload field"):
        MysteryBoxClient._payload_to_text(payload)
