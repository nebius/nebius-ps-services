from __future__ import annotations

import socket
import struct
from contextlib import closing

import pytest
from botocore import exceptions

from nebius_cxcli.object_storage_errors import classify_sdk_error
from nebius_cxcli.object_storage_protocol import MAX_HEADER_BYTES, MAX_OBJECT_BYTES, receive_frame


@pytest.mark.parametrize(
    "header_size, body_size", [(MAX_HEADER_BYTES + 1, 0), (2, MAX_OBJECT_BYTES + 1)]
)
def test_oversized_frame_is_rejected_before_allocating_payload(header_size, body_size):
    left, right = socket.socketpair()
    with closing(left), closing(right):
        left.sendall(struct.pack("!IQ", header_size, body_size))
        with pytest.raises(ValueError, match="size limit"):
            receive_frame(right)


@pytest.mark.parametrize(
    "error, code",
    [
        (exceptions.FlexibleChecksumError(error_msg="private-value"), "BadDigest"),
        (
            exceptions.SSLError(endpoint_url="https://private.invalid", error="private-value"),
            "TLS validation failure",
        ),
        (
            exceptions.ReadTimeoutError(endpoint_url="https://private.invalid"),
            "transport read timeout",
        ),
        (
            exceptions.ClientError(
                {
                    "Error": {"Code": "AccessDenied", "Message": "private-value"},
                    "ResponseMetadata": {"HTTPStatusCode": 403},
                },
                "PutObject",
            ),
            "AccessDenied",
        ),
        (ValueError("private-value"), "unknown service failure"),
    ],
)
def test_sdk_errors_never_forward_raw_diagnostics(error, code):
    result = classify_sdk_error(error)
    assert result.code == code
    assert "private" not in str(result)
