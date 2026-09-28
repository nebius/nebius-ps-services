from __future__ import annotations

import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import nebius_cxcli.terraform_backend as terraform_backend
from nebius_cxcli import object_storage_transport
from nebius_cxcli.object_storage_errors import ObjectStorageError
from nebius_cxcli.object_storage_transport import ObjectStorageResponse
from nebius_cxcli.runtime_config import wrap_runtime_config
from nebius_cxcli.terraform_backend import (
    backend_settings_from_config,
    read_state_lock_info,
    render_backend_tf,
    terraform_state_lock_object_key,
)


def _config(
    *, client_name: str = "client-a", project_id: str = "project-456", region: str = "eu-north1"
):
    return wrap_runtime_config(
        {
            "version": "v1",
            "client_info": {
                "client_name": client_name,
                "nebius": {
                    "tenant_id": "tenant-123",
                    "project_id": project_id,
                    "region_id": region,
                },
                "notifications": {"email_enabled": True, "email": None},
            },
            "infra": {"components": []},
            "apps": {"charts": []},
        }
    )


def test_backend_settings_are_deterministic_for_client_project() -> None:
    settings = backend_settings_from_config(_config())
    assert settings.bucket.startswith("tfstate-")
    assert "client-a" in settings.bucket
    assert "project-456" in settings.bucket
    assert settings.key == "terraform.tfstate"
    assert settings.endpoint == "https://storage.eu-north1.nebius.cloud"
    assert len(settings.bucket) <= 63
    assert re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", settings.bucket)


def test_backend_settings_bucket_name_is_truncated_safely_for_long_client_name() -> None:
    very_long_client = "client-" + ("x" * 90)
    settings = backend_settings_from_config(_config(client_name=very_long_client))
    assert len(settings.bucket) <= 63
    assert settings.bucket.startswith("tfstate-")
    assert re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", settings.bucket)


def test_render_backend_tf_is_non_secret_and_includes_locking() -> None:
    settings = backend_settings_from_config(_config())
    rendered = render_backend_tf(settings)
    assert 'backend "s3"' in rendered
    assert f'bucket = "{settings.bucket}"' in rendered
    assert "use_lockfile                = true" in rendered
    assert "access_key" not in rendered
    assert "secret_key" not in rendered


def test_terraform_state_lock_object_key_appends_tflock_suffix() -> None:
    settings = backend_settings_from_config(_config())
    assert terraform_state_lock_object_key(settings) == "terraform.tfstate.tflock"


@pytest.mark.parametrize("missing", [False, True])
def test_read_state_lock_info(monkeypatch, missing):
    settings = backend_settings_from_config(_config())
    calls = []

    def request(operation, key, **kwargs):
        calls.append((operation, key, kwargs))
        if missing:
            raise ObjectStorageError("NoSuchKey")
        return ObjectStorageResponse(
            body=(
                b'{"ID":"lock-123","Operation":"OperationTypeApply","Info":"","Who":"fixture@host",'
                b'"Version":"1.15.5","Created":"2026-03-19T02:04:39Z","Path":"bucket/terraform.tfstate"}'
            )
        )

    @contextmanager
    def transport(*args, **kwargs):
        assert kwargs["extra_env"] == {"AWS_ACCESS_KEY_ID": "test-key"}
        yield SimpleNamespace(request=request)

    monkeypatch.setattr(object_storage_transport, "object_storage_transport", transport)
    result = read_state_lock_info(settings, extra_env={"AWS_ACCESS_KEY_ID": "test-key"})
    assert calls[0][:2] == ("get_object", "terraform.tfstate.tflock")
    assert calls[0][2]["deadline"] > terraform_backend.time.monotonic()
    if missing:
        assert result is None
    else:
        assert result.lock_id == "lock-123"
        assert result.who == "fixture@host"
        assert result.bucket == settings.bucket
        assert result.object_key == "terraform.tfstate.tflock"


def test_is_not_found_error_accepts_storage_nosuchbucket_shape() -> None:
    exc = RuntimeError(
        "Request error NOT_FOUND: NoSuchBucket: Bucket doesn't exist; request_id: abc; trace_id: def"
    )
    assert terraform_backend._is_not_found_error(exc) is True


def test_is_not_found_error_rejects_unrelated_error() -> None:
    exc = RuntimeError("Request error INVALID_ARGUMENT: malformed request")
    assert terraform_backend._is_not_found_error(exc) is False


def test_bucket_is_active_uses_state_name() -> None:
    bucket = SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="ACTIVE")))
    assert terraform_backend._bucket_is_active(bucket) is True


def test_wait_for_bucket_ready_retries_until_active(monkeypatch: pytest.MonkeyPatch) -> None:
    states = [
        SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="CREATING"))),
        SimpleNamespace(status=SimpleNamespace(state=SimpleNamespace(name="ACTIVE"))),
    ]
    calls = {"value": 0}

    class _FakeBuckets:
        def get_by_name(self, _lookup):  # type: ignore[no-untyped-def]
            idx = min(calls["value"], len(states) - 1)
            calls["value"] += 1
            return SimpleNamespace(wait=lambda: states[idx])

    monkeypatch.setenv("NEBIUS_CXCLI_TFSTATE_READY_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("NEBIUS_CXCLI_TFSTATE_READY_POLL_SECONDS", "0.01")
    monkeypatch.setattr(terraform_backend.time, "sleep", lambda _seconds: None)

    terraform_backend._wait_for_bucket_ready(
        buckets=_FakeBuckets(),
        lookup=object(),
        bucket_name="bucket-1",
    )
    assert calls["value"] >= 2
