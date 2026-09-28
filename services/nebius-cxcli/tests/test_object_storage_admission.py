from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from nebius.api.nebius.common.v1 import ResourceMetadata
from nebius.api.nebius.storage.v1 import (
    Bucket,
    BucketPolicy,
    BucketSpec,
    BucketStatus,
    StorageClass,
    VersioningPolicy,
)

from nebius_cxcli import terraform_backend
from nebius_cxcli.deployment_state import deployment_state_prefix
from nebius_cxcli.object_storage_admission import validate_backend_bucket
from test_deployment_state import settings


def _settings():
    return replace(settings(), key="terraform.tfstate")


def bucket(*, paths=(), storage_class=StorageClass.STANDARD, versioning=VersioningPolicy.ENABLED):
    settings = _settings()
    return Bucket(
        metadata=ResourceMetadata(parent_id=settings.project_id, name=settings.bucket),
        spec=BucketSpec(
            default_storage_class=storage_class,
            versioning_policy=versioning,
            bucket_policy=BucketPolicy(
                rules=[
                    BucketPolicy.Rule(
                        paths=paths,
                        roles=["storage.viewer"],
                        anonymous=BucketPolicy.Rule.AnonymousAccess(),
                    )
                ]
                if paths
                else []
            ),
        ),
        status=BucketStatus(state=BucketStatus.State.ACTIVE, anonymous_access_enabled=bool(paths)),
    )


@pytest.mark.parametrize(
    "pattern",
    [
        "*",
        "terraform.tfstate",
        "terraform.tfstate.tflock",
        "terraform.tfstat?",
        "terraform*",
        "*bad*",
        "",
    ],
)
def test_rejects_anonymous_access_to_present_and_future_authority(pattern):
    with pytest.raises(RuntimeError, match="policy"):
        validate_backend_bucket(_settings(), bucket(paths=[pattern]))


@pytest.mark.parametrize(
    "paths",
    [
        (),
        ("public/*",),
        ("nebius-cxcli/*",),
        ("terraform.tfstate-public*",),
        ("[t]erraform.tfstate",),
        (deployment_state_prefix(_settings()),),
    ],
)
def test_allows_private_backend_and_disjoint_public_prefixes(paths):
    validate_backend_bucket(_settings(), bucket(paths=paths))


@pytest.mark.parametrize(
    "change", ["filesystem", "project", "name", "unknown-policy", "empty-policy"]
)
def test_rejects_unsafe_or_unverifiable_backend(change):
    value = bucket()
    if change == "filesystem":
        value.spec.default_storage_class = StorageClass.FILESYSTEM
    elif change == "project":
        value.metadata.parent_id = "another-project"
    elif change == "name":
        value.metadata.name = "another-bucket"
    elif change == "empty-policy":
        value.spec.bucket_policy = BucketPolicy(
            rules=[BucketPolicy.Rule(anonymous=BucketPolicy.Rule.AnonymousAccess())]
        )
    else:
        value.status.anonymous_access_enabled = True
    with pytest.raises(RuntimeError):
        validate_backend_bucket(_settings(), value)


def test_warns_about_disabled_versioning_without_mutating_bucket(caplog):
    value = bucket(versioning=VersioningPolicy.DISABLED)
    validate_backend_bucket(_settings(), value)
    assert "versioning is not enabled" in caplog.text
    assert value.spec.versioning_policy == VersioningPolicy.DISABLED


@pytest.mark.parametrize("mode", ["reuse", "create", "race"])
def test_every_bucket_bootstrap_path_checks_the_ready_bucket(monkeypatch, mode):
    from nebius.api.nebius.storage import v1

    calls = []
    unsafe = bucket(storage_class=StorageClass.FILESYSTEM)

    class Buckets:
        def get_by_name(self, lookup):
            calls.append("get")
            if len(calls) == 1 and mode != "reuse":
                raise RuntimeError("NoSuchBucket")
            return SimpleNamespace(wait=lambda: unsafe)

        def create(self, request):
            calls.append("create")
            assert request.spec.versioning_policy == VersioningPolicy.ENABLED
            if mode == "race":
                raise RuntimeError("StatusCode.ALREADY_EXISTS")
            return SimpleNamespace(wait=lambda: None)

    monkeypatch.setattr(
        terraform_backend,
        "_sdk_for_backend_api",
        lambda _: SimpleNamespace(sync_close=lambda: calls.append("close")),
    )
    monkeypatch.setattr(v1, "BucketServiceClient", lambda _: Buckets())
    with pytest.raises(RuntimeError, match="Filesystem buckets"):
        terraform_backend.ensure_state_bucket(_settings())
    assert calls[-1] == "close"
    assert ("create" in calls) == (mode != "reuse")
