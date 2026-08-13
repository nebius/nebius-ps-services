from __future__ import annotations

import json
import os
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from nebius_vpngw.config_loader import (
    InstanceResolvedConfig,
    VMHADigestRecord,
    VMHAGenerationRecord,
    VMHALogicalManifests,
    VMHANodeRecord,
    VMHAReadinessRecord,
)
from nebius_vpngw.deploy.ssh_push import SSHPush
from nebius_vpngw.schema import (
    VMHACredentialReferences,
    VMHARole,
    VMHARuntimeBinding,
    VMHARuntimeNodeBinding,
)


class _FakeDistribution:
    def __init__(self, direct_url: str, version: str = "0.5.4") -> None:
        self._direct_url = direct_url
        self.version = version

    def read_text(self, filename: str) -> str | None:
        if filename == "direct_url.json":
            return self._direct_url
        return None


class _FakeResponse(BytesIO):
    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def test_select_wheel_from_dirs_prefers_installed_version(tmp_path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    older = dist_dir / "nebius_vpngw-0.9.0-py3-none-any.whl"
    preferred = dist_dir / "nebius_vpngw-1.2.3-py3-none-any.whl"
    newest_other = dist_dir / "nebius_vpngw-2.0.0-py3-none-any.whl"

    older.write_text("", encoding="utf-8")
    preferred.write_text("", encoding="utf-8")
    newest_other.write_text("", encoding="utf-8")

    os.utime(older, (1, 1))
    os.utime(preferred, (2, 2))
    os.utime(newest_other, (3, 3))

    with patch("nebius_vpngw.deploy.ssh_push.metadata.version", return_value="1.2.3"):
        selected = SSHPush()._select_wheel_from_dirs([dist_dir])

    assert selected == preferred


def test_build_wheel_uses_environment_override(tmp_path, monkeypatch) -> None:
    wheel_path = tmp_path / "custom.whl"
    wheel_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("VPNGW_AGENT_WHEEL", str(wheel_path))

    selected = SSHPush()._build_wheel()

    assert selected == wheel_path


def test_build_wheel_downloads_wheel_from_install_url(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    direct_url = json.dumps(
        {
            "url": (
                "https://github.com/nebius/nebius-ps-services/releases/download/"
                "nebius-vpngw-v0.5.4/nebius_vpngw-0.5.4-py3-none-any.whl"
            )
        }
    )
    monkeypatch.setattr(SSHPush, "_find_project_root", lambda self: None)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.metadata.distribution",
        lambda _: _FakeDistribution(direct_url),
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.urlopen",
        lambda url, timeout: _FakeResponse(b"wheel-bytes"),
    )

    selected = SSHPush()._build_wheel()

    assert selected is not None
    assert selected.name == "nebius_vpngw-0.5.4-py3-none-any.whl"
    assert selected.read_bytes() == b"wheel-bytes"


def test_build_wheel_falls_back_to_original_local_wheel_when_build_fails(
    tmp_path, monkeypatch
) -> None:
    original_wheel = tmp_path / "nebius_vpngw-0.5.4-py3-none-any.whl"
    original_wheel.write_text("wheel", encoding="utf-8")
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "pyproject.toml").write_text(
        "[project]\nname='nebius-vpngw'\n", encoding="utf-8"
    )
    direct_url = json.dumps({"url": original_wheel.resolve().as_uri()})

    monkeypatch.setattr(SSHPush, "_find_project_root", lambda self: project_root)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stderr="No module named build", stdout=""
        ),
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.metadata.distribution",
        lambda _: _FakeDistribution(direct_url),
    )

    selected = SSHPush()._build_wheel()

    assert selected == original_wheel


def _vm_ha_manifest_and_binding() -> tuple[InstanceResolvedConfig, VMHARuntimeBinding]:
    digest = "a" * 64
    digests = VMHADigestRecord(
        configuration=digest,
        static_routes="b" * 64,
        bgp_policy="c" * 64,
    )
    generation = VMHAGenerationRecord(
        generation_id=digest,
        digests=digests,
        logical_manifests=VMHALogicalManifests(static_routes_json="[]", bgp_policy_json="[]"),
    )
    node = VMHANodeRecord(node_id="node-a", instance_index=0, role=VMHARole.ACTIVE)
    manifest = InstanceResolvedConfig(
        instance_index=0,
        hostname="gateway-0",
        external_ip="203.0.113.10",
        config_yaml=yaml.safe_dump(
            {
                "vm_ha": {
                    "cluster_id": "cluster-a",
                    "node": {"node_id": "node-a", "instance_index": 0, "role": "active"},
                    "generation": {"generation_id": digest, "digests": digests.__dict__},
                }
            },
            sort_keys=False,
        ),
        vm_ha_node=node,
        vm_ha_generation=generation,
        vm_ha_readiness=VMHAReadinessRecord(
            required_node_ids=("node-a", "node-b"),
            generation_id=digest,
            digests=digests,
        ),
    )

    def bound_node(node_id: str, role: VMHARole, suffix: str) -> VMHARuntimeNodeBinding:
        return VMHARuntimeNodeBinding(
            node_id=node_id,
            role=role,
            compute_id=f"compute-{suffix}",
            network_interface_name="eth0",
            peer_endpoint=f"10.0.0.{suffix}:9443",
            credentials=VMHACredentialReferences(
                certificate_authority="/etc/nebius-vpngw/vm-ha/ca.crt",
                certificate=f"/etc/nebius-vpngw/vm-ha/{node_id}.crt",
                private_key=f"/etc/nebius-vpngw/vm-ha/{node_id}.key",
            ),
        )

    binding = VMHARuntimeBinding(
        cluster_id="cluster-a",
        shared_allocation_id="allocation-a",
        nodes=(
            bound_node("node-a", VMHARole.ACTIVE, "10"),
            bound_node("node-b", VMHARole.PASSIVE, "11"),
        ),
        route_runtime_id="cluster-a:allocation-a",
        generation_id=digest,
        configuration_digest=digest,
        static_routes_digest="b" * 64,
        bgp_policy_digest="c" * 64,
    )
    return manifest, binding


def test_vm_ha_render_binds_exact_secret_free_runtime_identity() -> None:
    manifest, binding = _vm_ha_manifest_and_binding()

    rendered = SSHPush._render_vm_ha_config(manifest, binding)
    payload = yaml.safe_load(rendered)

    assert payload["vm_ha"]["runtime_binding"] == binding.model_dump(mode="json")
    assert "PRIVATE KEY" not in rendered
    assert SSHPush._vm_ha_receipt(manifest, rendered).generation_id == "a" * 64


def test_vm_ha_render_rejects_binding_for_another_generation() -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    mismatched = binding.model_copy(
        update={"static_routes_digest": "d" * 64},
    )

    with pytest.raises(ValueError, match="staged generation"):
        SSHPush._render_vm_ha_config(manifest, mismatched)


def test_vm_ha_activation_rechecks_staged_bytes_before_install() -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    rendered = SSHPush._render_vm_ha_config(manifest, binding)
    receipt = SSHPush._vm_ha_receipt(manifest, rendered)

    command = SSHPush._vm_ha_staged_verify_command(receipt)

    assert receipt.staged_file_sha256 in command
    assert f"/vm-ha-staged/{receipt.generation_id}.yaml" in command
    assert "sha256sum --check --status" in command


class _CommandStream(BytesIO):
    def __init__(self, payload: bytes, return_code: int) -> None:
        super().__init__(payload)
        self.channel = SimpleNamespace(recv_exit_status=lambda: return_code)


class _DeactivationClient:
    def __init__(self, return_code: int) -> None:
        self.return_code = return_code
        self.command = ""
        self.closed = False

    def set_missing_host_key_policy(self, policy) -> None:
        return None

    def connect(self, **kwargs) -> None:
        return None

    def exec_command(self, command: str, **kwargs):
        self.command = command
        output = b"VM_HA_DEACTIVATED=1\n" if self.return_code == 0 else b""
        return BytesIO(), _CommandStream(output, self.return_code), BytesIO(b"failed")

    def close(self) -> None:
        self.closed = True


def test_vm_ha_deactivation_is_ordered_and_fail_closed() -> None:
    client = _DeactivationClient(return_code=0)
    paramiko = SimpleNamespace(SSHClient=lambda: client, AutoAddPolicy=lambda: object())
    push = SSHPush()
    push._paramiko = paramiko

    changed = push.deactivate_vm_ha("203.0.113.10", {"gateway_group": {"vm_spec": {}}})

    assert changed
    assert client.command.index("disable --now") < client.command.index("rm -f")
    assert client.command.index("30-vm-ha.conf") < client.command.index("daemon-reload")
    assert client.closed

    failing = _DeactivationClient(return_code=1)
    push._paramiko = SimpleNamespace(SSHClient=lambda: failing, AutoAddPolicy=lambda: object())
    with pytest.raises(RuntimeError, match="deactivation failed"):
        push.deactivate_vm_ha("203.0.113.10", {"gateway_group": {"vm_spec": {}}})


class _ConnectFailClient:
    def set_missing_host_key_policy(self, policy) -> None:
        return None

    def connect(self, **kwargs) -> None:
        raise TimeoutError("unreachable")

    def close(self) -> None:
        return None


def test_vm_ha_activation_fails_closed_on_ssh_connect(monkeypatch) -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    rendered = SSHPush._render_vm_ha_config(manifest, binding)
    receipt = SSHPush._vm_ha_receipt(manifest, rendered)
    push = SSHPush()
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: _ConnectFailClient(), AutoAddPolicy=lambda: object()
    )
    monkeypatch.setattr(
        "nebius_vpngw.agent.main.vm_ha_runtime_blockers",
        lambda: (),
    )

    with pytest.raises(RuntimeError, match="activation SSH connection failed"):
        push.push_config_and_reload(
            "203.0.113.10",
            manifest,
            {"gateway_group": {"vm_spec": {}}},
            staged_receipt=receipt,
            runtime_binding=binding,
        )


def test_ha_to_non_ha_push_fails_closed_after_deactivation() -> None:
    push = SSHPush()
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: _ConnectFailClient(), AutoAddPolicy=lambda: object()
    )
    ordinary = SimpleNamespace(instance_index=0)

    with pytest.raises(RuntimeError, match="required SSH connection failed"):
        push.push_config_and_reload(
            "203.0.113.10",
            ordinary,
            {"gateway_group": {"vm_spec": {}}},
            fail_closed=True,
        )
