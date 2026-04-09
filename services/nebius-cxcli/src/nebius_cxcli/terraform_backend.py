"""Terraform backend rendering and Nebius remote-state bootstrap helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from .runtime_config import to_plain_data
from .sdk_auth import init_nebius_sdk

DEFAULT_STATE_BUCKET_PREFIX = "tfstate"
DEFAULT_STATE_OBJECT_KEY = "terraform.tfstate"
_BUCKET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
DEFAULT_BUCKET_READY_TIMEOUT_SECONDS = 90.0
DEFAULT_BUCKET_READY_POLL_SECONDS = 2.0


@dataclass(frozen=True)
class TerraformBackendSettings:
    project_id: str
    client_name: str
    region_id: str
    bucket: str
    key: str
    endpoint: str


@dataclass(frozen=True)
class TerraformStateLockInfo:
    lock_id: str
    path: str
    operation: str
    who: str
    version: str
    created: str
    info: str
    bucket: str
    object_key: str


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _as_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_bucket_token(value: str) -> str:
    token = _as_text(value).lower()
    token = re.sub(r"[^a-z0-9.-]+", "-", token)
    token = re.sub(r"-{2,}", "-", token)
    token = re.sub(r"\.{2,}", ".", token)
    token = token.strip("-.")
    return token


def _build_bucket_name(*, client_name: str, project_id: str) -> str:
    override = _normalize_bucket_token(os.environ.get("NEBIUS_CXCLI_TFSTATE_BUCKET_NAME", ""))
    if override:
        if not _BUCKET_NAME_PATTERN.fullmatch(override):
            raise RuntimeError(
                "NEBIUS_CXCLI_TFSTATE_BUCKET_NAME must match Object Storage bucket naming rules "
                "(3-63 chars, lowercase letters/digits/dots/hyphens, start/end alphanumeric)."
            )
        return override

    prefix = _normalize_bucket_token(
        os.environ.get("NEBIUS_CXCLI_TFSTATE_BUCKET_PREFIX", DEFAULT_STATE_BUCKET_PREFIX)
    )
    if not prefix:
        prefix = DEFAULT_STATE_BUCKET_PREFIX
    prefix = prefix[:24].strip("-.") or DEFAULT_STATE_BUCKET_PREFIX
    body = _normalize_bucket_token(f"{client_name}-{project_id}") or "state"
    candidate = f"{prefix}-{body}"
    if len(candidate) <= 63 and _BUCKET_NAME_PATTERN.fullmatch(candidate):
        return candidate

    digest = hashlib.sha256(f"{client_name}:{project_id}".encode()).hexdigest()[:10]
    body_budget = 63 - len(prefix) - len(digest) - 2  # "<prefix>-<body>-<digest>"
    if body_budget < 1:
        body_budget = 1
    truncated = body[:body_budget].rstrip("-.")
    if not truncated:
        truncated = "state"
    name = f"{prefix}-{truncated}-{digest}"
    name = name[:63].strip("-.")
    if not _BUCKET_NAME_PATTERN.fullmatch(name):
        raise RuntimeError(
            "Failed to derive valid Terraform state bucket name from client/project values. "
            "Set NEBIUS_CXCLI_TFSTATE_BUCKET_NAME explicitly."
        )
    return name


def backend_settings_from_config(config: Any) -> TerraformBackendSettings:
    payload = to_plain_data(config)
    root = _as_mapping(payload)
    client_info = _as_mapping(root.get("client_info"))
    nebius = _as_mapping(client_info.get("nebius"))

    client_name = _as_text(client_info.get("client_name"))
    project_id = _as_text(nebius.get("project_id"))
    region_id = _as_text(nebius.get("region_id"))
    if not client_name:
        raise RuntimeError(
            "client_info.client_name is required to derive Terraform backend settings"
        )
    if not project_id:
        raise RuntimeError(
            "client_info.nebius.project_id is required to derive Terraform backend settings"
        )
    if not region_id:
        raise RuntimeError(
            "client_info.nebius.region_id is required to derive Terraform backend settings"
        )

    object_key = _as_text(
        os.environ.get("NEBIUS_CXCLI_TFSTATE_OBJECT_KEY", DEFAULT_STATE_OBJECT_KEY)
    )
    object_key = object_key.lstrip("/") or DEFAULT_STATE_OBJECT_KEY
    endpoint = f"https://storage.{region_id}.nebius.cloud"
    bucket = _build_bucket_name(client_name=client_name, project_id=project_id)
    return TerraformBackendSettings(
        project_id=project_id,
        client_name=client_name,
        region_id=region_id,
        bucket=bucket,
        key=object_key,
        endpoint=endpoint,
    )


def render_backend_tf(settings: TerraformBackendSettings) -> str:
    """Render deterministic non-secret S3 backend configuration."""
    return (
        "terraform {\n"
        '  backend "s3" {\n'
        f"    bucket = {json.dumps(settings.bucket)}\n"
        f"    key    = {json.dumps(settings.key)}\n"
        f"    region = {json.dumps(settings.region_id)}\n"
        "\n"
        "    endpoints = {\n"
        f"      s3 = {json.dumps(settings.endpoint)}\n"
        "    }\n"
        "\n"
        "    use_lockfile = true\n"
        "    skip_credentials_validation = true\n"
        "    skip_region_validation      = true\n"
        "    skip_requesting_account_id  = true\n"
        "    skip_metadata_api_check     = true\n"
        "  }\n"
        "}\n"
    )


def terraform_state_lock_object_key(settings: TerraformBackendSettings) -> str:
    return f"{settings.key}.tflock"


def _require_aws_cli() -> None:
    if shutil.which("aws"):
        return
    raise RuntimeError(
        "aws CLI is required to inspect the remote Terraform state lock object, but it was not found in PATH"
    )


def read_state_lock_info(
    settings: TerraformBackendSettings,
    *,
    extra_env: dict[str, str] | None = None,
) -> TerraformStateLockInfo | None:
    _require_aws_cli()
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)

    object_key = terraform_state_lock_object_key(settings)
    command = [
        "aws",
        "--cli-connect-timeout",
        "5",
        "--cli-read-timeout",
        "5",
        "--endpoint-url",
        settings.endpoint,
        "s3",
        "cp",
        f"s3://{settings.bucket}/{object_key}",
        "-",
    ]
    completed = subprocess.run(
        command,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        if _is_not_found_error(RuntimeError(message)):
            return None
        raise RuntimeError(
            "Failed to inspect remote Terraform state lock object "
            f"`s3://{settings.bucket}/{object_key}` via aws CLI: {message or 'unknown error'}"
        )

    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Remote Terraform state lock object did not contain valid JSON: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Remote Terraform state lock object did not contain a JSON mapping")

    lock_id = _as_text(payload.get("ID"))
    if not lock_id:
        raise RuntimeError("Remote Terraform state lock object is missing required field `ID`")
    return TerraformStateLockInfo(
        lock_id=lock_id,
        path=_as_text(payload.get("Path")),
        operation=_as_text(payload.get("Operation")),
        who=_as_text(payload.get("Who")),
        version=_as_text(payload.get("Version")),
        created=_as_text(payload.get("Created")),
        info=_as_text(payload.get("Info")),
        bucket=settings.bucket,
        object_key=object_key,
    )


def _is_not_found_error(exc: Exception) -> bool:
    message = str(exc).lower()
    tokens = (
        "not found",
        "not_found",
        "statuscode.not_found",
        "resource not found",
        "nosuchbucket",
        "bucket doesn't exist",
    )
    return any(token in message for token in tokens)


def _is_already_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "statuscode.already_exists" in message


def _bucket_is_active(bucket: Any) -> bool:
    status = getattr(bucket, "status", None)
    state = getattr(status, "state", None)
    if state is None:
        return False
    state_name = str(getattr(state, "name", state)).upper()
    if state_name in {"ACTIVE", "STATE.ACTIVE"}:
        return True
    from nebius.api.nebius.storage.v1 import BucketStatus

    return state == BucketStatus.State.ACTIVE


def _wait_for_bucket_ready(
    *,
    buckets,
    lookup,
    bucket_name: str,
) -> None:
    timeout_seconds = float(
        _as_text(os.environ.get("NEBIUS_CXCLI_TFSTATE_READY_TIMEOUT_SECONDS"))
        or DEFAULT_BUCKET_READY_TIMEOUT_SECONDS
    )
    poll_seconds = float(
        _as_text(os.environ.get("NEBIUS_CXCLI_TFSTATE_READY_POLL_SECONDS"))
        or DEFAULT_BUCKET_READY_POLL_SECONDS
    )
    timeout_seconds = max(1.0, timeout_seconds)
    poll_seconds = max(0.25, poll_seconds)

    deadline = time.monotonic() + timeout_seconds
    last_state = "unknown"
    while True:
        bucket = buckets.get_by_name(lookup).wait()
        status = getattr(bucket, "status", None)
        state = getattr(status, "state", None)
        last_state = str(getattr(state, "name", state) or "unknown")
        if _bucket_is_active(bucket):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"Terraform state bucket '{bucket_name}' is not ready yet "
                f"(state={last_state}) after {timeout_seconds:.0f}s."
            )
        time.sleep(poll_seconds)


def _sdk_for_backend_api(project_id: str):
    return init_nebius_sdk(
        endpoint=_as_text(os.environ.get("NEBIUS_ENDPOINT")) or None,
        parent_id=project_id,
        context="Terraform backend bootstrap",
    )


def ensure_state_bucket(settings: TerraformBackendSettings) -> bool:
    """Ensure remote Terraform state bucket exists. Returns True when created."""
    sdk = _sdk_for_backend_api(settings.project_id)
    try:
        from nebius.api.nebius.common.v1 import ResourceMetadata
        from nebius.api.nebius.storage.v1 import (
            BucketServiceClient,
            BucketSpec,
            CreateBucketRequest,
            GetBucketByNameRequest,
            VersioningPolicy,
        )

        buckets = BucketServiceClient(sdk)
        lookup = GetBucketByNameRequest(parent_id=settings.project_id, name=settings.bucket)
        try:
            buckets.get_by_name(lookup).wait()
            _wait_for_bucket_ready(
                buckets=buckets,
                lookup=lookup,
                bucket_name=settings.bucket,
            )
            return False
        except Exception as exc:
            if not _is_not_found_error(exc):
                raise RuntimeError(
                    f"Failed to verify Terraform state bucket '{settings.bucket}': {exc}"
                ) from exc

        try:
            buckets.create(
                CreateBucketRequest(
                    metadata=ResourceMetadata(parent_id=settings.project_id, name=settings.bucket),
                    spec=BucketSpec(versioning_policy=VersioningPolicy.ENABLED),
                )
            ).wait()
            _wait_for_bucket_ready(
                buckets=buckets,
                lookup=lookup,
                bucket_name=settings.bucket,
            )
            return True
        except Exception as exc:
            if _is_already_exists_error(exc):
                try:
                    buckets.get_by_name(lookup).wait()
                    _wait_for_bucket_ready(
                        buckets=buckets,
                        lookup=lookup,
                        bucket_name=settings.bucket,
                    )
                    return False
                except Exception as verify_exc:
                    raise RuntimeError(
                        "Terraform state bucket name is already taken and not accessible in this project: "
                        f"{settings.bucket} ({verify_exc})"
                    ) from verify_exc
            raise RuntimeError(
                f"Failed to create Terraform state bucket '{settings.bucket}': {exc}"
            ) from exc
    finally:
        with suppress(Exception):
            sdk.sync_close()
