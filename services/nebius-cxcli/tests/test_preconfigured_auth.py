import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from nebius_cxcli import preconfigured_auth as auth


@pytest.fixture
def configured(tmp_path, monkeypatch):
    for name in auth.CONFLICTS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("NEBIUS_ENDPOINT", raising=False)
    now = int(time.time())
    value = {
        "schema": "preconfigured-static/v1",
        "token": "fixture-token-with-no-provider-authority",
        "service_account_id": "serviceaccount-fixture",
        "project_id": "project-fixture",
        "issued_at": now - 10,
        "expires_at": now + 1000,
        "deadline": now + 600,
    }
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setenv(auth.MODE, "preconfigured-static")
    monkeypatch.setenv(auth.FILE, str(path))
    return path, value


def test_protected_static_file_does_not_disclose_bearer(configured):
    _, value = configured
    parsed = auth.load("project-fixture")
    assert value["token"] not in repr(parsed)
    with pytest.raises(RuntimeError, match="project mismatch"):
        auth.load("project-other")


@pytest.mark.parametrize("change", ["permissions", "expired", "schema", "symlink"])
def test_unsafe_or_expired_input_rejected(configured, change):
    path, value = configured
    if change == "permissions":
        path.chmod(0o644)
    elif change == "expired":
        value["deadline"] = int(time.time()) - 1
        path.write_text(json.dumps(value))
    elif change == "schema":
        value["signing_key"] = "not-supported"
        path.write_text(json.dumps(value))
    else:
        target = path.with_name("actual.json")
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises(RuntimeError):
        auth.load()


def test_fifo_auth_source_is_rejected_without_waiting_for_a_writer(configured, monkeypatch):
    path = configured[0].with_name("auth.pipe")
    os.mkfifo(path, 0o600)
    monkeypatch.setenv(auth.FILE, str(path))
    # A subprocess bounds the regression: opening a FIFO must not block before
    # descriptor-based regular-file validation can reject it.
    script = """
from nebius_cxcli.preconfigured_auth import load
try:
    load()
except RuntimeError as exc:
    assert str(exc) == 'Preconfigured static authentication file is invalid or unsafe'
else:
    raise AssertionError('FIFO admitted as a credential file')
"""
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, timeout=5)


def test_mixed_identity_and_renewable_requests_cannot_fall_back(configured, monkeypatch):
    from nebius_cxcli.sdk_auth import init_nebius_sdk

    with pytest.raises(RuntimeError, match="cannot use"):
        init_nebius_sdk(require_renewable_auth=True)
    with pytest.raises(RuntimeError, match="cannot use"):
        init_nebius_sdk(profile="other")
    monkeypatch.setenv("NEBIUS_AUTH_PRIVATE_KEY_FILE", "/not-readable")
    with pytest.raises(RuntimeError, match="mixed credential"):
        init_nebius_sdk()


def config():
    return {
        "client_info": {"client_name": "test", "nebius": {"project_id": "project-fixture"}},
        "infra": {"components": [{"id": "vm", "enabled": True, "inputs": {"platform": "cpu-d3"}}]},
        "apps": {"charts": []},
    }


@pytest.mark.parametrize(
    "change", ["gpu", "cluster", "fabric", "identity", "component", "app", "dependency"]
)
def test_cpu_vm_admission_rejects_unreviewed_authority(change):
    value = config()
    row = value["infra"]["components"][0]
    if change == "gpu":
        row["inputs"]["platform"] = "gpu-b200"
    elif change == "cluster":
        row["inputs"]["gpu_cluster_enabled"] = True
    elif change == "fabric":
        row["inputs"]["gpu_cluster_infiniband_fabric"] = "fabric-fixture"
    elif change == "identity":
        row["inputs"]["service_account_id"] = "serviceaccount-other"
    elif change == "component":
        row["id"] = "mysterybox"
    elif change == "app":
        value["apps"]["charts"] = [{"enabled": True}]
    else:
        row["depends_on"] = ["access"]
    with pytest.raises(RuntimeError):
        auth.admit_cpu_vm_config(value)


def test_cpu_vm_admission_rejects_native_component_bindings():
    value = config()
    value["infra"]["components"][0]["bindings"] = {
        "inputs.name": {
            "source_component": "vm",
            "source_instance": "other",
            "source_output": "instance_id",
        }
    }
    with pytest.raises(RuntimeError, match="without runtime identities or dependencies"):
        auth.admit_cpu_vm_config(value)


def test_identity_is_verified_with_static_token_and_no_renewal(configured, monkeypatch):
    import nebius.sdk

    seen = {}

    class SDK:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def whoami(self, **kwargs):
            assert "auth_options" not in kwargs
            return SimpleNamespace(
                wait=lambda: SimpleNamespace(
                    service_account_profile=SimpleNamespace(
                        info=SimpleNamespace(
                            metadata=SimpleNamespace(
                                id="serviceaccount-fixture", parent_id="project-fixture"
                            )
                        )
                    )
                )
            )

    monkeypatch.setattr(nebius.sdk, "SDK", SDK)
    sdk = auth.client(auth.load())
    assert isinstance(sdk, SDK)
    assert set(seen) == {"credentials", "parent_id", "domain", "user_agent_prefix"}


def test_static_provider_render_omits_signing_credentials(configured):
    from nebius_cxcli.infra_render import _terraform_providers_block

    text = _terraform_providers_block(preconfigured_static=True)
    assert "service_account" not in text
    assert configured[1]["token"] not in text
    assert "service_account" in _terraform_providers_block()


def test_static_runtime_preparation_never_bootstraps_or_exports_managed_auth(
    configured, monkeypatch
):
    from nebius_cxcli import cli
    from nebius_cxcli.object_storage_transport import StorageCredentials

    def forbidden(*args, **kwargs):
        raise AssertionError("managed authentication reached")

    monkeypatch.setattr(cli, "_capture_runtime_auth_operator_environment", forbidden)
    monkeypatch.setattr(cli, "_create_or_recreate_runtime_auth_profile", forbidden)
    monkeypatch.setattr(cli, "_export_runtime_auth_material", forbidden)
    monkeypatch.setattr(auth, "admit_cpu_vm_config", lambda _: None)
    monkeypatch.setattr(auth, "client", lambda _: SimpleNamespace(sync_close=lambda **_: None))
    monkeypatch.setattr(StorageCredentials, "from_environment", lambda: object())
    value = SimpleNamespace(
        client_info=SimpleNamespace(
            nebius=SimpleNamespace(project_id="project-fixture"), client_name="test"
        )
    )
    cli._ensure_runtime_auth_material(value, need_terraform=True)
    runtime = cli._terraform_runtime_env(value)
    assert runtime["NEBIUS_IAM_TOKEN"] == configured[1]["token"]
    assert not any("private_key" in key or "credentials_file" in key for key in runtime)


def test_static_mode_rejects_managed_key_issuance_and_export(configured, monkeypatch):
    from nebius_cxcli import cli

    def forbidden(*args, **kwargs):
        raise AssertionError("managed credential owner reached")

    monkeypatch.setattr(cli, "_runtime_auth_project_lock", forbidden)
    monkeypatch.setattr(cli, "issue_service_account_object_storage_access_key", forbidden)
    cached = SimpleNamespace(
        s3_access_key_id="",
        s3_secret_access_key="",
        project_id="project-fixture",
        client_name="test",
    )
    for owner in (cli._ensure_runtime_auth_s3_material, cli._export_runtime_auth_material):
        with pytest.raises(RuntimeError, match="does not permit"):
            owner(cached)


def test_static_bootstrap_ci_fails_before_loading_existing_cache(configured, monkeypatch):
    from typer.testing import CliRunner

    from nebius_cxcli import cli

    def forbidden(*args, **kwargs):
        raise AssertionError("bootstrap side effects or managed cache reached")

    for name in (
        "_load_context",
        "_runtime_auth_cache_material",
        "_ensure_runtime_auth_s3_material",
        "_export_runtime_auth_material",
    ):
        monkeypatch.setattr(cli, name, forbidden)
    result = CliRunner().invoke(cli.app, ["bootstrap-ci", "config.yaml"])
    assert result.exit_code != 0
    assert "does not permit" in result.output


def test_expired_static_mode_never_falls_back_to_wireguard_or_operator_identity(
    configured, monkeypatch
):
    import nebius.sdk

    from nebius_cxcli import sdk_auth
    from nebius_cxcli.wireguard_secret_api import wireguard_api

    path, value = configured
    value["deadline"] = int(time.time()) - 1
    path.write_text(json.dumps(value))

    def forbidden(*args, **kwargs):
        raise AssertionError("alternate identity reached")

    monkeypatch.setattr(nebius.sdk, "SDK", forbidden)
    monkeypatch.setattr(sdk_auth, "_run_iam_token_cli", forbidden)
    with pytest.raises(RuntimeError, match="does not permit"):
        sdk_auth.acquire_operator_access_token()
    for source in (None, "/existing/signing-credentials.json"):
        with (
            pytest.raises(RuntimeError, match="does not permit"),
            wireguard_api(credentials_file=source),
        ):
            pytest.fail("WireGuard client admitted")
