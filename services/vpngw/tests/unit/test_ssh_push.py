from __future__ import annotations

import hashlib
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
    VMHACredentialSourceReferences,
    VMHARole,
    VMHARouteTarget,
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
    static_routes_digest = hashlib.sha256(b"[]").hexdigest()
    bgp_policy_digest = hashlib.sha256(b"[]").hexdigest()
    digests = VMHADigestRecord(
        configuration=digest,
        static_routes=static_routes_digest,
        bgp_policy=bgp_policy_digest,
    )
    generation = VMHAGenerationRecord(
        generation_id=digest,
        digests=digests,
        logical_manifests=VMHALogicalManifests(static_routes_json="[]", bgp_policy_json="[]"),
    )
    node = VMHANodeRecord(
        node_id="node-a",
        instance_index=0,
        role=VMHARole.ACTIVE,
        credential_sources=VMHACredentialSourceReferences(
            certificate_authority="/operator/ca",
            certificate="/operator/cert",
            private_key="/operator/key",
            nebius_credentials="/operator/nebius",
        ),
    )
    manifest = InstanceResolvedConfig(
        instance_index=0,
        hostname="gateway-0",
        external_ip="203.0.113.10",
        config_yaml=yaml.safe_dump(
            {
                "vm_ha": {
                    "cluster_id": "cluster-a",
                    "node": {"node_id": "node-a", "instance_index": 0, "role": "active"},
                    "generation": {
                        "generation_id": digest,
                        "digests": digests.__dict__,
                        "logical_manifests": {
                            "static_routes_json": "[]",
                            "bgp_policy_json": "[]",
                        },
                    },
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

    route_targets = (
        VMHARouteTarget(
            project_id="project-a",
            network_id="network-a",
            workload_subnet_id="subnet-a",
            route_table_id="route-table-a",
        ),
    )
    binding = VMHARuntimeBinding(
        cluster_id="cluster-a",
        shared_allocation_id="allocation-a",
        nodes=(
            bound_node("node-a", VMHARole.ACTIVE, "10"),
            bound_node("node-b", VMHARole.PASSIVE, "11"),
        ),
        route_targets=route_targets,
        route_runtime_id=VMHARuntimeBinding.derive_route_runtime_id(
            "cluster-a", "allocation-a", route_targets
        ),
        generation_id=digest,
        configuration_digest=digest,
        static_routes_digest=static_routes_digest,
        bgp_policy_digest=bgp_policy_digest,
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
    receipt = SSHPush._vm_ha_receipt(
        manifest,
        rendered,
        (("/etc/nebius-vpngw/vm-ha/node-a.key", "d" * 64),),
    )

    command = SSHPush._vm_ha_staged_verify_command(receipt)

    assert receipt.staged_file_sha256 in command
    assert f"/vm-ha-staged/{receipt.generation_id}.yaml" in command
    assert "sha256sum --check --status" in command
    assert "root:root:600" in command
    assert "/etc/nebius-vpngw/vm-ha/node-a.key" in command


def test_vm_ha_credential_bundle_is_exact_and_never_enters_manifest(tmp_path) -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    source_values = {
        "certificate_authority": b"fixture-ca",
        "certificate": b"fixture-certificate",
        "private_key": b"fixture-private-key",
        "nebius_credentials": b'{"fixture":"renewable"}',
    }
    source_paths = {}
    for name, value in source_values.items():
        path = tmp_path / name
        path.write_bytes(value)
        source_paths[name] = str(path)
    sources = VMHACredentialSourceReferences(**source_paths)
    node = manifest.vm_ha_node
    assert node is not None
    manifest.vm_ha_node = VMHANodeRecord(
        node_id=node.node_id,
        instance_index=node.instance_index,
        role=node.role,
        credential_sources=sources,
    )

    payloads = SSHPush._credential_source_payloads(
        node_id=node.node_id,
        sources=sources,
    )
    bundle_digest = SSHPush._credential_bundle_digest(payloads)
    generation = manifest.vm_ha_generation
    assert generation is not None
    base = (
        f"/etc/nebius-vpngw/vm-ha-credentials/{generation.generation_id}/"
        f"{node.node_id}/{bundle_digest}"
    )
    references = VMHACredentialReferences(
        certificate_authority=f"{base}/ca.crt",
        certificate=f"{base}/{node.node_id}.crt",
        private_key=f"{base}/{node.node_id}.key",
        nebius_credentials=f"{base}/nebius-credentials.json",
    )
    targets = SSHPush._credential_targets(
        node.node_id, generation.generation_id, bundle_digest, references
    )
    staged_binding = SSHPush._runtime_binding_for_credential_bundle(
        inst_cfg=manifest,
        runtime_binding=binding,
        credential_digests=tuple(
            (target, digest) for (_, target), (_, _, digest) in zip(targets, payloads, strict=True)
        ),
    )
    rendered = SSHPush._render_vm_ha_config(manifest, staged_binding)

    assert [label for label, _, _ in payloads] == list(source_values)
    assert [content for _, content, _ in payloads] == list(source_values.values())
    assert all(str(path) not in rendered for path in source_paths.values())
    assert all(value.decode() not in rendered for value in source_values.values())
    assert base in rendered


def test_vm_ha_credential_bundle_rejects_cross_node_sources_and_targets() -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    node = manifest.vm_ha_node
    assert node is not None
    other_sources = node.credential_sources.model_copy(
        update={"private_key": "/operator/other-node.key"}
    )
    with pytest.raises(ValueError, match="source bundle"):
        SSHPush().stage_vm_ha_config(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            credential_sources=other_sources,
        )
    wrong_targets = binding.nodes[0].credentials.model_copy(
        update={"certificate": "/etc/nebius-vpngw/vm-ha-credentials/wrong/node-b.crt"}
    )
    with pytest.raises(ValueError, match="canonical credential targets"):
        SSHPush._credential_targets(node.node_id, "a" * 64, "b" * 64, wrong_targets)


class _CommandStream(BytesIO):
    def __init__(self, payload: bytes, return_code: int) -> None:
        super().__init__(payload)
        self.channel = SimpleNamespace(recv_exit_status=lambda: return_code)


class _Writable:
    def __init__(self, writes: list[bytes], *, fail: bool = False) -> None:
        self.writes = writes
        self.fail = fail

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def write(self, value) -> None:
        if self.fail:
            raise OSError("injected upload failure")
        self.writes.append(value.encode() if isinstance(value, str) else bytes(value))


class _StageSFTP:
    def __init__(self, writes: list[bytes], *, fail_upload: int | None, counter: list[int]) -> None:
        self.writes = writes
        self.fail_upload = fail_upload
        self.counter = counter

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def file(self, path: str, mode: str):
        current = self.counter[0]
        self.counter[0] += 1
        return _Writable(self.writes, fail=current == self.fail_upload)

    def chmod(self, path: str, mode: int) -> None:
        return None


class _StageClient:
    def __init__(
        self,
        *,
        fail_command_index: int | None = None,
        fail_upload: int | None = None,
    ) -> None:
        self.fail_command_index = fail_command_index
        self.fail_upload = fail_upload
        self.commands: list[str] = []
        self.writes: list[bytes] = []
        self.upload_counter = [0]
        self.closed = False

    def load_system_host_keys(self) -> None:
        return None

    def load_host_keys(self, _path: str) -> None:
        return None

    def set_missing_host_key_policy(self, policy) -> None:
        return None

    def connect(self, **kwargs) -> None:
        return None

    def open_sftp(self) -> _StageSFTP:
        return _StageSFTP(
            self.writes,
            fail_upload=self.fail_upload,
            counter=self.upload_counter,
        )

    def exec_command(self, command: str, **kwargs):
        index = len(self.commands)
        self.commands.append(command)
        return_code = int(index == self.fail_command_index)
        output = (
            hashlib.sha256(self.writes[0]).hexdigest().encode()
            if "sudo sha256sum /etc/" in command
            else b""
        )
        return BytesIO(), _CommandStream(output, return_code), BytesIO(b"injected")

    def close(self) -> None:
        self.closed = True


def _credential_stage_fixture(tmp_path):
    manifest, binding = _vm_ha_manifest_and_binding()
    node = manifest.vm_ha_node
    assert node is not None
    source_paths = {}
    for name in ("certificate_authority", "certificate", "private_key", "nebius_credentials"):
        path = tmp_path / name
        path.write_bytes(f"fixture-{name}".encode())
        source_paths[name] = str(path)
    sources = VMHACredentialSourceReferences(**source_paths)
    manifest.vm_ha_node = VMHANodeRecord(
        node_id=node.node_id,
        instance_index=node.instance_index,
        role=node.role,
        credential_sources=sources,
    )
    rendered_binding = binding
    return manifest, rendered_binding, sources


def test_vm_ha_stage_installs_exact_private_bundle_before_manifest(tmp_path) -> None:
    manifest, binding, sources = _credential_stage_fixture(tmp_path)
    client = _StageClient()
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    receipt = push.stage_vm_ha_config(
        "203.0.113.10",
        manifest,
        {},
        runtime_binding=binding,
        credential_sources=sources,
    )

    assert receipt.credential_sha256
    assert all("/vm-ha-credentials/" in target for target, _ in receipt.credential_sha256)
    credential_installs = [
        command
        for command in client.commands
        if "-m 0600" in command and "/vm-ha-credentials/" in command
    ]
    assert len(credential_installs) == 4
    assert all(command.endswith(".new") for command in credential_installs)
    assert sum("root:root:600" in command for command in client.commands) == 8
    assert client.commands.index(credential_installs[0]) < next(
        index
        for index, command in enumerate(client.commands)
        if "/vm-ha-staged/" in command and "install -o root" in command
    )


def test_vm_ha_stage_rejects_missing_explicit_host_pin_before_remote_io(
    tmp_path, monkeypatch
) -> None:
    manifest, binding, sources = _credential_stage_fixture(tmp_path)
    client = _StageClient()
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())
    monkeypatch.setenv("VPNGW_SSH_KNOWN_HOSTS_FILE", str(tmp_path / "missing-known-hosts"))

    with pytest.raises(ValueError, match="non-empty readable regular file"):
        push.stage_vm_ha_config(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            credential_sources=sources,
        )

    assert client.commands == []
    assert client.writes == []
    assert client.closed


def test_vm_ha_stage_classifies_missing_pinned_host_identity(tmp_path, monkeypatch) -> None:
    manifest, binding, sources = _credential_stage_fixture(tmp_path)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("gateway ssh-ed25519 AAAAfixture\n", encoding="utf-8")
    monkeypatch.setenv("VPNGW_SSH_KNOWN_HOSTS_FILE", str(known_hosts))

    class RejectingStageClient(_StageClient):
        def connect(self, **kwargs) -> None:
            raise _HostIdentityRejected("Server not found in known_hosts")

    client = RejectingStageClient()
    push = SSHPush()
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: client,
        RejectPolicy=lambda: object(),
        SSHException=_HostIdentityRejected,
        BadHostKeyException=(),
    )

    with pytest.raises(RuntimeError, match="SSH host identity verification failed"):
        push.stage_vm_ha_config(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            credential_sources=sources,
        )

    assert client.commands == []
    assert client.closed


@pytest.mark.parametrize("fail_command_index", range(0, 30))
def test_vm_ha_stage_aborts_on_every_remote_credential_step(
    tmp_path,
    fail_command_index: int,
) -> None:
    manifest, binding, sources = _credential_stage_fixture(tmp_path)
    client = _StageClient(fail_command_index=fail_command_index)
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    with pytest.raises(RuntimeError):
        push.stage_vm_ha_config(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            credential_sources=sources,
        )

    assert client.closed


@pytest.mark.parametrize("fail_upload", range(0, 5))
def test_vm_ha_stage_aborts_on_every_manifest_or_credential_upload(
    tmp_path, fail_upload: int
) -> None:
    manifest, binding, sources = _credential_stage_fixture(tmp_path)
    client = _StageClient(fail_upload=fail_upload)
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    with pytest.raises((OSError, RuntimeError)):
        push.stage_vm_ha_config(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            credential_sources=sources,
        )

    assert client.closed


class _DeactivationClient:
    def __init__(self, return_code: int) -> None:
        self.return_code = return_code
        self.command = ""
        self.closed = False

    def load_system_host_keys(self) -> None:
        return None

    def load_host_keys(self, _path: str) -> None:
        return None

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
    paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())
    push = SSHPush()
    push._paramiko = paramiko

    changed = push.deactivate_vm_ha("203.0.113.10", {"gateway_group": {"vm_spec": {}}})

    assert changed
    assert client.command.index("disable --now") < client.command.index("rm -f")
    assert client.command.index("30-vm-ha.conf") < client.command.index("daemon-reload")
    assert client.closed

    retired = _DeactivationClient(return_code=0)
    push._paramiko = SimpleNamespace(SSHClient=lambda: retired, RejectPolicy=lambda: object())
    push.deactivate_vm_ha("203.0.113.11", {"gateway_group": {"vm_spec": {}}}, retire_member=True)
    assert "stale=1" in retired.command
    assert "nebius-vpngw-agent.service" in retired.command
    assert "nebius-vpngw-health-monitor.service" in retired.command
    assert "nebius-vpngw-fix-routes.timer" in retired.command
    assert "nebius-vpngw-fix-routes.service" in retired.command
    assert "config-resolved.yaml" in retired.command

    failing = _DeactivationClient(return_code=1)
    push._paramiko = SimpleNamespace(SSHClient=lambda: failing, RejectPolicy=lambda: object())
    with pytest.raises(RuntimeError, match="deactivation failed"):
        push.deactivate_vm_ha("203.0.113.10", {"gateway_group": {"vm_spec": {}}})


class _VerificationClient(_DeactivationClient):
    def exec_command(self, command: str, **kwargs):
        self.command = command
        output = b"VM_HA_TERMINAL_NON_HA=1\n" if self.return_code == 0 else b""
        return BytesIO(), _CommandStream(output, self.return_code), BytesIO(b"incomplete")


class _LegacyInspectionClient(_DeactivationClient):
    def __init__(self, payload: dict, return_code: int = 0) -> None:
        super().__init__(return_code)
        self.payload = payload

    def exec_command(self, command: str, **kwargs):
        self.command = command
        output = json.dumps(self.payload).encode() if self.return_code == 0 else b""
        return BytesIO(), _CommandStream(output, self.return_code), BytesIO(b"denied")


def _legacy_identity_payload() -> dict:
    return {
        "status": "vm-ha",
        "cluster_id": "cluster",
        "allocation_id": "shared-private",
        "instance_index": 0,
        "node_id": "node-active",
        "role": "active",
        "nodes": [
            {
                "node_id": "node-active",
                "role": "active",
                "compute_id": "compute-0",
                "network_interface_name": "eth0",
            },
            {
                "node_id": "node-passive",
                "role": "passive",
                "compute_id": "compute-1",
                "network_interface_name": "eth0",
            },
        ],
    }


def test_legacy_vm_ha_inspection_is_exact_pinned_and_secret_free() -> None:
    client = _LegacyInspectionClient(_legacy_identity_payload())
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    with patch("nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification") as configure:
        identity = push.inspect_legacy_vm_ha_identity(
            "203.0.113.10", "gateway-0", {"gateway_group": {"vm_spec": {}}}
        )

    assert identity is not None
    assert identity.allocation_id == "shared-private"
    assert identity.node_id == "node-active"
    assert "config-resolved.yaml" not in client.command
    assert "private_key" not in client.command
    assert "base64 -d | sudo python3" in client.command
    assert configure.call_args.kwargs["hostname"] == "gateway-0"
    assert configure.call_args.kwargs["transport_host"] == "203.0.113.10"
    assert client.closed


def test_legacy_vm_ha_inspection_denial_fails_closed() -> None:
    client = _LegacyInspectionClient({}, return_code=1)
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    with (
        patch("nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification"),
        pytest.raises(RuntimeError, match="could not be read"),
    ):
        push.inspect_legacy_vm_ha_identity(
            "203.0.113.10", "gateway-0", {"gateway_group": {"vm_spec": {}}}
        )

    assert client.closed


def test_vm_ha_deactivation_verification_checks_terminal_state() -> None:
    client = _VerificationClient(return_code=0)
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    push.verify_vm_ha_deactivated(
        "203.0.113.11", {"gateway_group": {"vm_spec": {}}}, retire_member=True
    )

    assert "VM_HA_TERMINAL_NON_HA=1" in client.command
    assert "/etc/nebius-vpngw/vm-ha-credentials" in client.command
    assert "nebius-vpngw-agent.service" in client.command
    assert "nebius-vpngw-health-monitor.service" in client.command
    assert "nebius-vpngw-fix-routes.timer" in client.command
    assert "nebius-vpngw-fix-routes.service" in client.command
    assert 'systemctl is-active --quiet "$unit"' in client.command
    assert 'systemctl is-enabled --quiet "$unit"' in client.command
    assert client.closed

    failing = _VerificationClient(return_code=1)
    push._paramiko = SimpleNamespace(SSHClient=lambda: failing, RejectPolicy=lambda: object())
    with pytest.raises(RuntimeError, match="terminal deactivation verification failed"):
        push.verify_vm_ha_deactivated(
            "203.0.113.11", {"gateway_group": {"vm_spec": {}}}, retire_member=True
        )


class _ConnectFailClient:
    def load_system_host_keys(self) -> None:
        return None

    def load_host_keys(self, _path: str) -> None:
        return None

    def set_missing_host_key_policy(self, policy) -> None:
        return None

    def connect(self, **kwargs) -> None:
        raise TimeoutError("unreachable")

    def close(self) -> None:
        return None


class _HostIdentityRejected(Exception):
    pass


class _HostIdentityRejectingClient(_ConnectFailClient):
    def connect(self, **kwargs) -> None:
        raise _HostIdentityRejected("pinned host key does not match")


def test_vm_ha_activation_classifies_host_identity_rejection(monkeypatch) -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    rendered = SSHPush._render_vm_ha_config(manifest, binding)
    receipt = SSHPush._vm_ha_receipt(manifest, rendered)
    push = SSHPush()
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: _HostIdentityRejectingClient(),
        RejectPolicy=lambda: object(),
        BadHostKeyException=_HostIdentityRejected,
    )
    monkeypatch.setattr("nebius_vpngw.agent.main.vm_ha_runtime_blockers", lambda: ())

    with pytest.raises(RuntimeError, match="SSH host identity verification failed"):
        push.push_config_and_reload(
            "203.0.113.10",
            manifest,
            {"gateway_group": {"vm_spec": {}}},
            staged_receipt=receipt,
            runtime_binding=binding,
        )


def test_vm_ha_activation_fails_closed_on_ssh_connect(monkeypatch) -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    rendered = SSHPush._render_vm_ha_config(manifest, binding)
    receipt = SSHPush._vm_ha_receipt(manifest, rendered)
    push = SSHPush()
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: _ConnectFailClient(), RejectPolicy=lambda: object()
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
        SSHClient=lambda: _ConnectFailClient(), RejectPolicy=lambda: object()
    )
    ordinary = SimpleNamespace(instance_index=0)

    with pytest.raises(RuntimeError, match="required SSH connection failed"):
        push.push_config_and_reload(
            "203.0.113.10",
            ordinary,
            {"gateway_group": {"vm_spec": {}}},
            fail_closed=True,
        )
