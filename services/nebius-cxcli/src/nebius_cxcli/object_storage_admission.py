"""Read-only admission of the bucket that holds deployment authority."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from nebius.api.nebius.storage.v1 import Bucket, StorageClass, VersioningPolicy

if TYPE_CHECKING:
    from .terraform_backend import TerraformBackendSettings


def _overlaps(pattern: str, value: str, *, prefix: bool = False) -> bool:
    # Nebius supports '?' and a single terminal '*'; brackets are literal.
    if not pattern or "*" in pattern[:-1]:
        raise RuntimeError("Backend bucket has an indeterminate anonymous policy")
    wildcard = pattern.endswith("*")
    stem = pattern[:-1] if wildcard else pattern
    if prefix:
        return all(a == "?" or a == b for a, b in zip(stem, value, strict=False)) and (
            wildcard or len(stem) >= len(value)
        )
    expression = "".join("." if char == "?" else re.escape(char) for char in stem)
    return re.fullmatch(expression + (".*" if wildcard else ""), value) is not None


def validate_backend_bucket(settings: TerraformBackendSettings, bucket: Bucket) -> None:
    if bucket.metadata.parent_id != settings.project_id or bucket.metadata.name != settings.bucket:
        raise RuntimeError("Terraform backend bucket identity differs from the selected project")
    storage_class = bucket.spec.default_storage_class
    if storage_class == StorageClass.FILESYSTEM:
        raise RuntimeError("Filesystem buckets cannot provide atomic Terraform/deployment locking")
    if storage_class not in {
        StorageClass.STORAGE_CLASS_UNSPECIFIED,
        StorageClass.STANDARD,
        StorageClass.ENHANCED_THROUGHPUT,
        StorageClass.INTELLIGENT,
    }:
        raise RuntimeError("Terraform backend bucket storage class is unsupported")
    keys = (settings.key, settings.key + ".tflock")
    anonymous_seen = False
    for rule in bucket.spec.bucket_policy.rules:
        if not rule.check_presence("anonymous"):
            continue
        anonymous_seen = True
        if not rule.paths:
            raise RuntimeError("Backend bucket has an indeterminate anonymous policy")
        for pattern in rule.paths:
            if any(_overlaps(pattern, key) for key in keys):
                raise RuntimeError(
                    "Anonymous bucket policy exposes Terraform/deployment backend objects"
                )
    if bucket.status.anonymous_access_enabled and not anonymous_seen:
        raise RuntimeError("Backend bucket anonymous access cannot be verified from its policy")
    if bucket.spec.versioning_policy != VersioningPolicy.ENABLED:
        logging.getLogger(__name__).warning(
            "Terraform backend bucket versioning is not enabled; previous object versions may be unavailable."
        )
