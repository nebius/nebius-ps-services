"""Read one immutable payload version in memory; never print or persist values."""

from sdk.runtime import CloudError, rpc


def read_secret(*, sdk, secret_id, version_id=""):
    from nebius.api.nebius.mysterybox.v1 import GetPayloadRequest, PayloadServiceClient

    if not secret_id:
        raise ValueError("An exact SecretStash secret ID is required")
    result = rpc(
        PayloadServiceClient(sdk).get, GetPayloadRequest(secret_id=secret_id, version_id=version_id)
    )
    if not result.version_id or (version_id and result.version_id != version_id):
        raise CloudError("SECRET_VERSION_MISMATCH")
    values = {}
    size = 0
    for entry in result.data:
        if not entry.key or entry.key in values or not entry.check_presence("string_value"):
            raise CloudError("SECRET_PAYLOAD_SCHEMA")
        size += len(entry.key.encode()) + len(entry.string_value.encode())
        if size > 32768:
            raise CloudError("SECRET_PAYLOAD_TOO_LARGE")
        values[entry.key] = entry.string_value
    return result.version_id, values
