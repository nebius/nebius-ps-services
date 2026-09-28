"""Allowlisted S3 failures; provider messages and credential-bearing URLs stay private."""

from __future__ import annotations

TRANSPORT_FAILURES = frozenset(
    {
        "transport read timeout",
        "transport connect timeout",
        "endpoint connection failure",
        "transport connection closed",
        "transport connection reset",
        "RequestTimeout",
        "request deadline exceeded",
        "worker unavailable",
    }
)
SERVICE_FAILURES = frozenset(
    {
        "NoSuchKey",
        "NoSuchBucket",
        "NotFound",
        "PreconditionFailed",
        "ConditionalRequestConflict",
        "KeyAlreadyExists",
        "AccessDenied",
        "ExpiredToken",
        "InvalidToken",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "RequestTimeTooSkewed",
        "SlowDown",
        "InternalError",
        "ServiceUnavailable",
        "InvalidRequest",
        "BadDigest",
        "404",
        "409",
        "412",
        "500",
        "503",
        "TLS validation failure",
        "invalid response",
        "object too large",
        "invalid credentials",
        "unknown service failure",
    }
)


class ObjectStorageError(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None) -> None:
        self.code = (
            code
            if isinstance(code, str) and code in TRANSPORT_FAILURES | SERVICE_FAILURES
            else "unknown service failure"
        )
        self.status = status if isinstance(status, int) and 100 <= status <= 599 else None
        super().__init__(f"Object Storage: {self.code}")

    @property
    def ambiguous(self) -> bool:
        return self.code in TRANSPORT_FAILURES

    @property
    def conflict(self) -> bool:
        return self.code in {"PreconditionFailed", "ConditionalRequestConflict", "409", "412"}

    @property
    def absent(self) -> bool:
        return self.code in {"NoSuchKey", "NoSuchBucket", "NotFound", "404"}


def classify_sdk_error(error: Exception) -> ObjectStorageError:
    """Translate documented SDK types/codes, never inspect or forward exception text."""
    from botocore import exceptions as errors

    if isinstance(error, errors.ClientError):
        response = error.response
        return ObjectStorageError(
            str(response.get("Error", {}).get("Code", "")),
            status=response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
        )
    for kind, code in (
        (errors.SSLError, "TLS validation failure"),
        (errors.ConnectTimeoutError, "transport connect timeout"),
        (errors.ReadTimeoutError, "transport read timeout"),
        (errors.EndpointConnectionError, "endpoint connection failure"),
        (errors.ConnectionClosedError, "transport connection closed"),
        (errors.IncompleteReadError, "transport connection closed"),
        (errors.ResponseStreamingError, "transport connection closed"),
        (errors.ChecksumError, "BadDigest"),
        (errors.FlexibleChecksumError, "BadDigest"),
        (ConnectionResetError, "transport connection reset"),
    ):
        if isinstance(error, kind):
            return ObjectStorageError(code)
    return ObjectStorageError("unknown service failure")
