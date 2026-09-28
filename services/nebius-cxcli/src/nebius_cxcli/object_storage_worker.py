"""Private exec worker. No CLI initialization, credential discovery or write replay."""

from __future__ import annotations

import socket
import sys
from contextlib import closing
from typing import Any

from .object_storage_errors import ObjectStorageError, classify_sdk_error
from .object_storage_protocol import MAX_OBJECT_BYTES, receive_frame, send_frame


def execute(client: Any, request: dict[str, Any], body: bytes) -> tuple[dict[str, Any], bytes]:
    operation = request["operation"]
    params = {"Bucket": request["bucket"], "Key": request["key"]}
    if operation in {"put_object", "delete_object"}:
        if request.get("etag"):
            params["IfMatch"] = request["etag"]
        elif operation == "put_object" and request.get("create_only") is True:
            params["IfNoneMatch"] = "*"
        else:
            raise ObjectStorageError("InvalidRequest")
    if operation == "put_object":
        params.update(
            Body=body, ContentType="application/json", Metadata=request.get("metadata") or {}
        )
    if operation not in {"head_object", "get_object", "put_object", "delete_object"}:
        raise ObjectStorageError("InvalidRequest")
    response = getattr(client, operation)(**params)
    output = b""
    if operation == "get_object":
        limit = min(int(request["max_bytes"]), MAX_OBJECT_BYTES)
        with closing(response["Body"]) as stream:
            if response.get("ContentLength", limit + 1) > limit:
                raise ObjectStorageError("object too large")
            data = bytearray()
            while block := stream.read(min(1024 * 1024, limit + 1 - len(data))):
                data.extend(block)
                if len(data) > limit:
                    raise ObjectStorageError("object too large")
            output = bytes(data)
            if len(output) != response.get("ContentLength"):
                raise ObjectStorageError("transport connection closed")
    return {"etag": response.get("ETag", ""), "metadata": response.get("Metadata", {})}, output


def main(fd: int) -> None:
    import boto3
    from botocore.config import Config

    with socket.socket(fileno=fd) as channel:
        config, _ = receive_frame(channel)
        session = boto3.session.Session()
        with closing(
            session.client(
                "s3",
                endpoint_url=config["endpoint"],
                region_name=config["region"],
                aws_access_key_id=config["access_key"],
                aws_secret_access_key=config["secret_key"],
                config=Config(
                    connect_timeout=5,
                    read_timeout=30,
                    retries={"mode": "standard", "total_max_attempts": 1},
                ),
            )
        ) as client:
            send_frame(channel, {"ready": True})
            while True:
                try:
                    request, body = receive_frame(channel)
                except EOFError:
                    return
                try:
                    response, output = execute(client, request, body)
                except Exception as exc:
                    error = exc if isinstance(exc, ObjectStorageError) else classify_sdk_error(exc)
                    send_frame(channel, {"error": error.code, "status": error.status})
                else:
                    send_frame(channel, response, output)


if __name__ == "__main__":
    main(int(sys.argv[1]))
