from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

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
from nebius_vpngw.deploy.ssh_push import (
    _VM_HA_SERVICE_ASSET_DESTINATIONS,
    SSHPush,
    VMHAAgentArtifact,
    VMHAAgentArtifactError,
    VMHAAgentArtifactProblem,
    VMHAApplyLockReceipt,
    VMHAStandbyReplacementNotReady,
)
from nebius_vpngw.schema import (
    VMHARole,
    VMHARouteTarget,
    VMHARuntimeBinding,
    VMHARuntimeNodeBinding,
)


def _locked_remote_action(command: str) -> tuple[str, str, bool]:
    parts = shlex.split(command)
    assert parts[:3] == ["sudo", "/usr/bin/python3", "-c"]
    runner = parts[3]
    action = base64.b64decode(parts[4], validate=True).decode("utf-8")
    return runner, action, parts[5] == "1"


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


def _write_vm_ha_agent_wheel(
    wheel: os.PathLike[str],
    *,
    restoration_capability: str = "vm-ha-standby-restoration-v2",
    include_agent_main: bool = True,
    dead_capability_handler: bool = False,
    include_module_entrypoint: bool = True,
    omit_service_asset: str | None = None,
) -> None:
    dist_info = "nebius_vpngw-1.2.3.dist-info"
    members = {
        "nebius_vpngw/agent/vm_ha/restoration.py": (
            f'STANDBY_RESTORATION_CAPABILITY = "{restoration_capability}"\n'
        ).encode(),
        "nebius_vpngw/agent/vm_ha/inhibition.py": (
            b'LIVE_PEER_REPLACEMENT_CAPABILITY = "vm-ha-live-peer-replacement-v4"\n'
        ),
        f"{dist_info}/METADATA": b"Metadata-Version: 2.4\nName: nebius-vpngw\nVersion: 1.2.3\n",
        f"{dist_info}/WHEEL": (b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"),
    }
    for asset_name in (
        "nebius-vpngw-agent.service",
        "nebius-vpngw-esp4-preflight.sh",
        "nebius-vpngw-fix-routes.service",
        "nebius-vpngw-fix-routes.timer",
        "nebius-vpngw-health-monitor.service",
        "nebius-vpngw-ufw-lock.conf",
        "nebius-vpngw-vm-ha-guard.service",
        "nebius-vpngw-vm-ha-ordering.conf",
        "nebius-vpngw-vm-ha-peer-firewall.sh",
        "nebius-vpngw-vm-ha-rearm.service",
        "nebius-vpngw-vm-ha.service",
        "setup-vpngw-firewall.sh",
    ):
        if asset_name == omit_service_asset:
            continue
        members[f"nebius_vpngw/systemd/{asset_name}"] = f"fixture:{asset_name}\n".encode()
    if include_agent_main:
        if dead_capability_handler:
            members["nebius_vpngw/agent/main.py"] = (
                b"from .vm_ha.inhibition import LIVE_PEER_REPLACEMENT_CAPABILITY\n"
                b"from .vm_ha.restoration import STANDBY_RESTORATION_CAPABILITY\n"
                b"AGENT_CAPABILITIES_FLAG = '--agent-capabilities'\n"
                b"AGENT_CAPABILITIES_SCHEMA = 'nebius-vpngw.agent-capabilities.v1'\n"
                b"FEATURES = [LIVE_PEER_REPLACEMENT_CAPABILITY, "
                b"STANDBY_RESTORATION_CAPABILITY]\n"
            )
        else:
            members["nebius_vpngw/agent/main.py"] = (
                b"import argparse\nimport json\n"
                b"from .vm_ha.inhibition import LIVE_PEER_REPLACEMENT_CAPABILITY\n"
                b"from .vm_ha.restoration import STANDBY_RESTORATION_CAPABILITY\n"
                b"def main():\n"
                b"    parser = argparse.ArgumentParser()\n"
                b"    group = parser.add_mutually_exclusive_group()\n"
                b"    group.add_argument('--agent-capabilities', action='store_true')\n"
                b"    args = parser.parse_args()\n"
                b"    if args.agent_capabilities:\n"
                b"        print(json.dumps({'features': "
                b"[LIVE_PEER_REPLACEMENT_CAPABILITY, STANDBY_RESTORATION_CAPABILITY], "
                b"'schema': 'nebius-vpngw.agent-capabilities.v1'}))\n"
                b"        return\n"
                + (
                    b"if __name__ == '__main__':\n    main()\n"
                    if include_module_entrypoint
                    else b""
                )
            )
    record_path = f"{dist_info}/RECORD"
    record_lines = []
    for name, payload in members.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=").decode()
        record_lines.append(f"{name},sha256={digest},{len(payload)}")
    record_lines.append(f"{record_path},,")
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
        archive.writestr(record_path, "\n".join(record_lines) + "\n")


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


def test_build_wheel_rejects_installed_fallback_for_exact_vm_ha_artifact(
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

    assert SSHPush()._build_wheel(allow_installed_fallback=False) is None


def _credential_payload() -> bytes:
    return json.dumps(
        {
            "subject-credentials": {
                "iss": "service-account-a",
                "sub": "service-account-a",
                "kid": "authorized-key-a",
            }
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


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

    def bound_node(
        node_id: str,
        role: VMHARole,
        suffix: str,
        credential_digest: str,
    ) -> VMHARuntimeNodeBinding:
        return VMHARuntimeNodeBinding(
            node_id=node_id,
            role=role,
            compute_id=f"compute-{suffix}",
            network_interface_name="eth0",
            peer_endpoint=f"10.0.0.{suffix}:9443",
            nebius_credentials_path=(
                f"/etc/nebius-vpngw/vm-ha-credentials/{digest}/{node_id}/"
                f"{credential_digest}/nebius-credentials.json"
            ),
            nebius_credentials_sha256=credential_digest,
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
            bound_node("node-a", VMHARole.ACTIVE, "10", "d" * 64),
            bound_node("node-b", VMHARole.PASSIVE, "11", "d" * 64),
        ),
        route_targets=route_targets,
        route_runtime_id=VMHARuntimeBinding.derive_route_runtime_id(
            "cluster-a", "allocation-a", route_targets
        ),
        generation_id=digest,
        configuration_digest=digest,
        static_routes_digest=static_routes_digest,
        bgp_policy_digest=bgp_policy_digest,
        nebius_project_id="project-a",
        nebius_service_account_id="service-account-a",
        nebius_authorized_key_id="authorized-key-a",
    )
    return manifest, binding


def _binding_for_local_credential(
    manifest: InstanceResolvedConfig,
    binding: VMHARuntimeBinding,
    credential_digest: str,
) -> VMHARuntimeBinding:
    node = manifest.vm_ha_node
    generation = manifest.vm_ha_generation
    assert node is not None and generation is not None
    target = SSHPush._nebius_credentials_target(
        node.node_id,
        generation.generation_id,
        credential_digest,
    )
    nodes = tuple(
        item.model_copy(
            update={
                "nebius_credentials_path": target,
                "nebius_credentials_sha256": credential_digest,
            }
        )
        if item.node_id == node.node_id
        else item
        for item in binding.nodes
    )
    return binding.model_copy(update={"nodes": nodes})


def test_vm_ha_render_binds_exact_secret_free_runtime_identity() -> None:
    manifest, binding = _vm_ha_manifest_and_binding()

    rendered = SSHPush._render_vm_ha_config(manifest, binding)
    payload = yaml.safe_load(rendered)

    assert payload["vm_ha"]["runtime_binding"] == binding.model_dump(mode="json")
    assert "PRIVATE KEY" not in rendered
    assert (
        SSHPush._vm_ha_receipt(
            manifest,
            rendered,
            runtime_binding=binding,
            nebius_credentials_path=binding.nodes[0].nebius_credentials_path,
            nebius_credentials_sha256="d" * 64,
        ).generation_id
        == "a" * 64
    )


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
    target = binding.nodes[0].nebius_credentials_path
    receipt = SSHPush._vm_ha_receipt(
        manifest,
        rendered,
        runtime_binding=binding,
        nebius_credentials_path=target,
        nebius_credentials_sha256="d" * 64,
    )

    command = SSHPush._vm_ha_staged_verify_command(receipt)

    assert receipt.staged_file_sha256 in command
    assert f"/vm-ha-staged/{receipt.generation_id}.yaml" in command
    assert "sha256sum --check --status" in command
    assert "root:root:600" in command
    assert target in command


def test_vm_ha_nebius_credential_is_exact_and_mtls_secrets_never_enter_manifest(
    tmp_path,
) -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    content = _credential_payload()
    source = tmp_path / "nebius-credentials.json"
    source.write_bytes(content)
    node = manifest.vm_ha_node
    assert node is not None
    manifest.vm_ha_node = VMHANodeRecord(
        node_id=node.node_id,
        instance_index=node.instance_index,
        role=node.role,
    )
    generation = manifest.vm_ha_generation
    assert generation is not None
    digest = hashlib.sha256(content).hexdigest()
    binding = _binding_for_local_credential(manifest, binding, digest)
    target = SSHPush._nebius_credentials_target(
        node.node_id,
        generation.generation_id,
        digest,
    )
    staged_binding = SSHPush._runtime_binding_for_nebius_credentials(
        inst_cfg=manifest,
        runtime_binding=binding,
        target=target,
        digest=digest,
    )
    rendered = SSHPush._render_vm_ha_config(manifest, staged_binding)

    assert str(source) not in rendered
    assert content.decode() not in rendered
    assert "certificate_authority" not in rendered
    assert "private_key" not in rendered
    assert target in rendered


def test_vm_ha_nebius_credential_rejects_noncanonical_installed_target() -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    with pytest.raises(ValueError, match="non-canonical target"):
        SSHPush._runtime_binding_for_nebius_credentials(
            inst_cfg=manifest,
            runtime_binding=binding,
            target="/etc/nebius-vpngw/vm-ha-credentials/wrong/nebius-credentials.json",
            digest="b" * 64,
        )


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
    def __init__(
        self,
        writes: list[bytes],
        paths: list[tuple[str, str]],
        chmods: list[tuple[str, int]],
        *,
        fail_upload: int | None,
        counter: list[int],
    ) -> None:
        self.writes = writes
        self.paths = paths
        self.chmods = chmods
        self.fail_upload = fail_upload
        self.counter = counter

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def file(self, path: str, mode: str):
        self.paths.append((path, mode))
        current = self.counter[0]
        self.counter[0] += 1
        return _Writable(self.writes, fail=current == self.fail_upload)

    def put(self, local_path: str, remote_path: str) -> None:
        self.paths.append((remote_path, "put"))
        self.writes.append(Path(local_path).read_bytes())

    def chmod(self, path: str, mode: int) -> None:
        self.chmods.append((path, mode))


class _StageClient:
    def __init__(
        self,
        *,
        fail_command_index: int | None = None,
        fail_cleanup: bool = False,
        fail_upload: int | None = None,
    ) -> None:
        self.fail_command_index = fail_command_index
        self.fail_cleanup = fail_cleanup
        self.fail_upload = fail_upload
        self.commands: list[str] = []
        self.writes: list[bytes] = []
        self.paths: list[tuple[str, str]] = []
        self.chmods: list[tuple[str, int]] = []
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
            self.paths,
            self.chmods,
            fail_upload=self.fail_upload,
            counter=self.upload_counter,
        )

    def exec_command(self, command: str, **kwargs):
        index = len(self.commands)
        self.commands.append(command)
        return_code = int(
            index == self.fail_command_index or (self.fail_cleanup and command.startswith("find "))
        )
        output = (
            hashlib.sha256(self.writes[0]).hexdigest().encode()
            if "sudo sha256sum /etc/" in command
            else b""
        )
        return BytesIO(), _CommandStream(output, return_code), BytesIO(b"injected")

    def close(self) -> None:
        self.closed = True


class _ApplyLockClient(_StageClient):
    def __init__(
        self,
        *,
        adoption_return_code: int = 0,
        clear_return_code: int = 0,
    ) -> None:
        super().__init__()
        self.adoption_return_code = adoption_return_code
        self.clear_return_code = clear_return_code

    def exec_command(self, command: str, **kwargs):
        self.commands.append(command)
        action = ""
        if command.startswith("sudo /usr/bin/python3 -c"):
            _runner, action, _create_parent = _locked_remote_action(command)
        if "sudo cat /var/lib/nebius-vpngw/vm-ha/apply.lock" in action:
            output = self.writes[-1]
            return BytesIO(), _CommandStream(output, 0), BytesIO()
        if "sudo cat /var/lib/nebius-vpngw/vm-ha/apply-owner-adoption.json" in action:
            output = self.writes[-1] if self.adoption_return_code == 0 else b""
            return (
                BytesIO(),
                _CommandStream(output, self.adoption_return_code),
                BytesIO(b"redacted remote detail"),
            )
        if "printf 'CLEARED" in action:
            output = b"CLEARED\n" if self.clear_return_code == 0 else b""
            return (
                BytesIO(),
                _CommandStream(output, self.clear_return_code),
                BytesIO(b"mismatch"),
            )
        return BytesIO(), _CommandStream(b"", 0), BytesIO()


class _PutSFTP:
    def __init__(self, *, before_read=None) -> None:
        self.uploads: list[tuple[bytes, str]] = []
        self.before_read = before_read

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def putfo(
        self,
        source,
        target: str,
        *,
        file_size: int,
        confirm: bool,
    ) -> None:
        assert confirm is True
        if self.before_read is not None:
            self.before_read()
        payload = source.read()
        assert len(payload) == file_size
        self.uploads.append((payload, target))


class _ManagedMTLSClient:
    def __init__(self, *, action_response: dict[str, object] | None = None) -> None:
        self.commands: list[str] = []
        self.sftp = _PutSFTP()
        self.action_response = action_response
        self.closed = False

    def connect(self, **kwargs) -> None:
        return None

    def open_sftp(self) -> _PutSFTP:
        return self.sftp

    def exec_command(self, command: str, **kwargs):
        self.commands.append(command)
        if "--vm-ha-mtls-action" in command:
            output = json.dumps(self.action_response).encode()
        elif "mktemp -d" in command:
            output = b"/tmp/nebius-vpngw-agent.ABCDEFGHIJ\n"
        elif "import cffi,cryptography" in command:
            output = json.dumps(
                {
                    "schema": "nebius-vpngw/vm-ha-package-v1",
                    "package_version": "1.2.3",
                    "cryptography_version": "45.0.0",
                    "cffi_version": "1.17.0",
                }
            ).encode()
        elif "--agent-capabilities" in command:
            output = json.dumps(
                {
                    "features": [
                        "vm-ha-live-peer-replacement-v4",
                        "vm-ha-standby-restoration-v2",
                    ],
                    "schema": "nebius-vpngw.agent-capabilities.v1",
                }
            ).encode()
        elif command.startswith("ip rule list") or command.startswith("ip route show"):
            output = b"OK\n"
        else:
            output = b""
        return BytesIO(), _CommandStream(output, 0), BytesIO()

    def close(self) -> None:
        self.closed = True


class _MaterializationClient:
    def __init__(self, return_codes: list[int]) -> None:
        self.return_codes = iter(return_codes)
        self.commands: list[tuple[str, dict]] = []

    def exec_command(self, command: str, **kwargs):
        self.commands.append((command, kwargs))
        return_code = next(self.return_codes)
        return BytesIO(), _CommandStream(b"", return_code), BytesIO(b"not ready")


class _ActivationFailureClient:
    def exec_command(self, command: str, **kwargs):
        assert command == "sudo systemctl restart nebius-vpngw-vm-ha-guard.service"
        assert kwargs == {"get_pty": True, "timeout": 20}
        output = (
            b"Job for nebius-vpngw-vm-ha-guard.service failed because the control "
            b"process exited with error code. TOP_SECRET_REMOTE_DETAIL"
        )
        return BytesIO(), _CommandStream(output, 1), BytesIO()


class _AutoHealingInitializationFailureClient:
    def exec_command(self, command: str, **kwargs):
        assert command == (
            "sudo /usr/bin/python3 -m nebius_vpngw.agent.main "
            "--vm-ha-auto-healing-action initialize"
        )
        assert kwargs == {"get_pty": True, "timeout": 20}
        output = b"standby auto-healing policy action failed: TOP_SECRET_REMOTE_DETAIL"
        return BytesIO(), _CommandStream(output, 2), BytesIO()


def test_replacement_policy_adoption_runs_after_initialize_before_services() -> None:
    commands = SSHPush._vm_ha_control_service_commands(
        initialize_policy=True,
        replacement_policy_request="encoded request",
    )

    assert commands[0].endswith("--vm-ha-auto-healing-action initialize")
    assert commands[1] == (
        "sudo /usr/bin/python3 -m nebius_vpngw.agent.main "
        "--vm-ha-auto-healing-action adopt-replacement "
        "--vm-ha-auto-healing-request 'encoded request'"
    )
    assert commands.index("sudo systemctl restart nebius-vpngw-vm-ha-rearm.service") > 1


def test_required_vm_ha_activation_failure_preserves_sanitized_pty_result(capsys) -> None:
    with pytest.raises(RuntimeError) as raised:
        SSHPush._run_remote_commands(
            _ActivationFailureClient(),
            ("sudo systemctl restart nebius-vpngw-vm-ha-guard.service",),
            required_remote=True,
        )

    message = str(raised.value)
    assert message == (
        "VM-HA activation command failed: restart "
        "nebius-vpngw-vm-ha-guard.service (rc=1; systemd job failed)"
    )
    assert "TOP_SECRET_REMOTE_DETAIL" not in message
    captured = capsys.readouterr()
    assert "TOP_SECRET_REMOTE_DETAIL" not in captured.out
    assert "TOP_SECRET_REMOTE_DETAIL" not in captured.err


def test_auto_healing_initialization_failure_has_closed_step_and_cause(capsys) -> None:
    command = (
        "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-auto-healing-action initialize"
    )

    with pytest.raises(RuntimeError) as raised:
        SSHPush._run_remote_commands(
            _AutoHealingInitializationFailureClient(),
            (command,),
            required_remote=True,
        )

    assert str(raised.value) == (
        "VM-HA activation command failed: initialize standby auto-healing policy "
        "(rc=2; standby auto-healing policy rejected activation)"
    )
    assert "TOP_SECRET_REMOTE_DETAIL" not in str(raised.value)
    captured = capsys.readouterr()
    assert "TOP_SECRET_REMOTE_DETAIL" not in captured.out
    assert "TOP_SECRET_REMOTE_DETAIL" not in captured.err


def test_optional_remote_failure_retains_existing_diagnostic_shape(capsys) -> None:
    client = _StageClient(fail_command_index=0)
    command = "sudo mkdir -p /etc/nebius-vpngw"

    assert SSHPush._run_remote_commands(
        client,
        (command,),
        required_remote=False,
    )

    assert capsys.readouterr().out == (f"[SSHPush] Command failed (rc=1): {command}\ninjected\n")


def test_vm_ha_agent_activation_is_controller_owned() -> None:
    agent_cmd = "sudo systemctl restart nebius-vpngw-agent"

    assert SSHPush._agent_activation_commands(agent_cmd=agent_cmd, vm_ha=True) == ()
    assert SSHPush._agent_activation_commands(agent_cmd=agent_cmd, vm_ha=False) == (agent_cmd,)


def test_vm_ha_package_preparation_proves_cryptography_and_cffi(tmp_path, monkeypatch) -> None:
    manifest, _binding = _vm_ha_manifest_and_binding()
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel)
    client = _ManagedMTLSClient()
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client)
    artifact = VMHAAgentArtifact.from_wheel(wheel, source="test")
    monkeypatch.setattr(
        push,
        "_build_wheel",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("approved artifact rebuilt")),
    )
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification",
        lambda *args, **kwargs: None,
    )

    receipt = push.ensure_vm_ha_agent_package(
        "203.0.113.10",
        manifest,
        {"gateway_group": {"vm_spec": {}}},
        artifact=artifact,
    )

    assert receipt["package_version"] == "1.2.3"
    assert receipt["cryptography_version"] == "45.0.0"
    assert receipt["cffi_version"] == "1.17.0"
    assert receipt["artifact_sha256"] == hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert receipt["capabilities"] == [
        "vm-ha-live-peer-replacement-v4",
        "vm-ha-standby-restoration-v2",
    ]
    assert len(client.sftp.uploads) == 1
    uploaded, remote_wheel = client.sftp.uploads[0]
    assert uploaded == wheel.read_bytes()
    assert remote_wheel == (
        "/tmp/nebius-vpngw-agent.ABCDEFGHIJ/nebius_vpngw-1.2.3-py3-none-any.whl"
    )
    assert "sha256sum --check --status" in client.commands[1]
    assert "--force-reinstall" in client.commands[2]
    assert "--ignore-installed" in client.commands[2]
    assert remote_wheel in client.commands[2]
    assert "import cffi,cryptography" in client.commands[3]
    assert "--agent-capabilities" in client.commands[4]
    assert "nebius_vpngw/systemd/" in client.commands[5]
    assert remote_wheel in client.commands[5]
    assert client.commands[6] == (
        f"rm -f {remote_wheel} && rmdir /tmp/nebius-vpngw-agent.ABCDEFGHIJ"
    )
    assert client.closed


def test_vm_ha_package_upload_holds_approved_inode_across_path_replacement(
    tmp_path,
    monkeypatch,
) -> None:
    manifest, _binding = _vm_ha_manifest_and_binding()
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    replacement = tmp_path / "replacement.whl"
    _write_vm_ha_agent_wheel(wheel)
    replacement.write_bytes(wheel.read_bytes())
    approved_bytes = wheel.read_bytes()
    artifact = VMHAAgentArtifact.from_wheel(wheel, source="test")
    client = _ManagedMTLSClient()
    client.sftp = _PutSFTP(before_read=lambda: os.replace(replacement, wheel))
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification",
        lambda *args, **kwargs: None,
    )

    receipt = push.ensure_vm_ha_agent_package(
        "203.0.113.10",
        manifest,
        {"gateway_group": {"vm_spec": {}}},
        artifact=artifact,
    )

    assert receipt["artifact_sha256"] == hashlib.sha256(approved_bytes).hexdigest()
    assert client.sftp.uploads == [
        (
            approved_bytes,
            "/tmp/nebius-vpngw-agent.ABCDEFGHIJ/nebius_vpngw-1.2.3-py3-none-any.whl",
        )
    ]
    with pytest.raises(VMHAAgentArtifactError) as changed:
        artifact.verify_current()
    assert changed.value.problem is VMHAAgentArtifactProblem.CHANGED


def test_vm_ha_agent_artifact_detects_bytes_changed_after_planning(tmp_path) -> None:
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel)
    artifact = VMHAAgentArtifact.from_wheel(wheel, source="test")

    with wheel.open("ab") as stream:
        stream.write(b"changed")

    with pytest.raises(RuntimeError, match="changed after planning"):
        artifact.verify_current()


def test_vm_ha_agent_artifact_rejects_pre_v2_restoration_capability(tmp_path) -> None:
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(
        wheel,
        restoration_capability="vm-ha-standby-restoration-v1",
    )

    with pytest.raises(
        VMHAAgentArtifactError,
        match="lacks a valid standby restoration capability entry point",
    ) as error:
        VMHAAgentArtifact.from_wheel(wheel, source="test")
    assert error.value.problem is VMHAAgentArtifactProblem.INCOMPATIBLE


def test_vm_ha_agent_artifact_rejects_token_only_pseudo_wheel(tmp_path) -> None:
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel, include_agent_main=False)

    with pytest.raises(
        VMHAAgentArtifactError,
        match="missing required package content",
    ) as error:
        VMHAAgentArtifact.from_wheel(wheel, source="test")
    assert error.value.problem is VMHAAgentArtifactProblem.INCOMPATIBLE


def test_vm_ha_agent_artifact_rejects_dead_capability_tokens(tmp_path) -> None:
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel, dead_capability_handler=True)

    with pytest.raises(
        VMHAAgentArtifactError,
        match="lacks a valid standby restoration capability entry point",
    ) as error:
        VMHAAgentArtifact.from_wheel(wheel, source="test")
    assert error.value.problem is VMHAAgentArtifactProblem.INCOMPATIBLE


def test_vm_ha_agent_artifact_rejects_uninvoked_capability_main(tmp_path) -> None:
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel, include_module_entrypoint=False)

    with pytest.raises(
        VMHAAgentArtifactError,
        match="lacks a valid standby restoration capability entry point",
    ) as error:
        VMHAAgentArtifact.from_wheel(wheel, source="test")
    assert error.value.problem is VMHAAgentArtifactProblem.INCOMPATIBLE


def test_vm_ha_agent_artifact_rejects_missing_service_asset(tmp_path) -> None:
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(
        wheel,
        omit_service_asset="nebius-vpngw-vm-ha.service",
    )

    with pytest.raises(
        VMHAAgentArtifactError,
        match="missing required package content",
    ) as error:
        VMHAAgentArtifact.from_wheel(wheel, source="test")
    assert error.value.problem is VMHAAgentArtifactProblem.INCOMPATIBLE


def test_vm_ha_agent_artifact_detects_same_bytes_path_replacement(tmp_path) -> None:
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    replacement = tmp_path / "replacement.whl"
    _write_vm_ha_agent_wheel(wheel)
    _write_vm_ha_agent_wheel(replacement)
    artifact = VMHAAgentArtifact.from_wheel(wheel, source="test")
    os.replace(replacement, wheel)

    with pytest.raises(VMHAAgentArtifactError, match="changed after planning") as error:
        artifact.verify_current()
    assert error.value.problem is VMHAAgentArtifactProblem.CHANGED


def test_vm_ha_agent_artifact_selection_detects_disappearance(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "repo"
    dist_dir = project_root / "dist"
    dist_dir.mkdir(parents=True)
    wheel = dist_dir / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel)
    original = VMHAAgentArtifact.from_wheel.__func__

    def disappear(cls, path, *, source):
        path.unlink()
        return original(cls, path, source=source)

    monkeypatch.setattr(SSHPush, "_find_project_root", lambda self: project_root)
    monkeypatch.setattr(
        VMHAAgentArtifact,
        "from_wheel",
        classmethod(disappear),
    )

    with pytest.raises(
        VMHAAgentArtifactError,
        match="changed before inspection",
    ) as error:
        SSHPush().resolve_vm_ha_agent_artifact()
    assert error.value.problem is VMHAAgentArtifactProblem.CHANGED


def test_vm_ha_agent_artifact_rejects_symlinked_wheel(tmp_path) -> None:
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel)
    linked = tmp_path / "nebius_vpngw-1.2.4-py3-none-any.whl"
    linked.symlink_to(wheel)

    with pytest.raises(RuntimeError, match="non-symlink"):
        VMHAAgentArtifact.from_wheel(linked, source="test")


def test_vm_ha_control_service_refresh_is_exact_pinned_and_bounded(monkeypatch) -> None:
    manifest, _binding = _vm_ha_manifest_and_binding()
    client = _ManagedMTLSClient()
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification",
        lambda *args, **kwargs: None,
    )

    push.refresh_vm_ha_control_services(
        "203.0.113.10",
        manifest,
        {"gateway_group": {"vm_spec": {}}},
    )

    assert client.commands[0] == "sudo systemctl daemon-reload"
    assert any("reset-failed" in command for command in client.commands)
    assert client.commands[-3:] == [
        "sudo systemctl is-active --quiet nebius-vpngw-vm-ha-guard.service",
        "sudo systemctl is-active --quiet nebius-vpngw-vm-ha.service",
        "sudo systemctl is-active --quiet nebius-vpngw-vm-ha-rearm.service",
    ]
    assert not any("auto-healing-action initialize" in command for command in client.commands)
    assert client.closed


def test_vm_ha_mtls_action_uses_exact_ssh_and_validates_public_evidence(
    monkeypatch,
) -> None:
    response = {
        "schema": "nebius-vpngw/vm-ha-mtls-action-v1",
        "action": "status",
        "result": {
            "state": "missing",
            "cluster_id": None,
            "node_id": None,
            "compute_id": None,
            "epoch": None,
            "certificate_fingerprint": None,
            "spki_fingerprint": None,
            "peer_fingerprints": [],
            "operation_id": None,
            "operation_kind": None,
            "target_epoch": None,
            "peer_target_epoch": None,
            "preserve_local": None,
            "inhibited": False,
            "inhibition_operation_id": None,
            "phase": None,
            "recovery": None,
        },
    }
    client = _ManagedMTLSClient(action_response=response)
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification",
        lambda *args, **kwargs: calls.append(kwargs),
    )

    observed = push.run_vm_ha_mtls_action(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        action="status",
        request={},
    )

    assert observed == response
    assert calls == [
        {
            "policy": push._ssh_policy,
            "hostname": "gateway-0",
            "transport_host": "203.0.113.10",
        }
    ]
    command = client.commands[0]
    assert "--vm-ha-mtls-action status" in command
    encoded = shlex.split(command)[-1]
    assert json.loads(base64.b64decode(encoded, validate=True)) == {}
    assert client.closed


def test_vm_ha_mtls_action_rejects_an_empty_peer_snapshot() -> None:
    with pytest.raises(ValueError, match="snapshot result is invalid"):
        SSHPush._validate_vm_ha_mtls_action_result(
            "activate",
            {
                "cluster_id": "cluster-a",
                "node_id": "node-a",
                "compute_id": "compute-a",
                "epoch": 1,
                "certificate_fingerprint": "a" * 64,
                "spki_fingerprint": "b" * 64,
                "peers": [],
            },
        )


def test_vm_ha_peer_firewall_runs_verified_asset_without_full_firewall() -> None:
    commands = SSHPush._vm_ha_peer_firewall_commands(vm_ha=True)

    assert commands == (
        "sudo /usr/local/bin/nebius-vpngw-vm-ha-peer-firewall.sh "
        "/etc/nebius-vpngw/config-resolved.yaml",
    )
    assert all("setup-vpngw-firewall.sh" not in command for command in commands)
    assert SSHPush._vm_ha_peer_firewall_commands(vm_ha=False) == ()


def test_vm_ha_activation_clears_prior_start_limits_before_retry() -> None:
    commands = SSHPush._vm_ha_reset_failed_commands(vm_ha=True)

    assert len(commands) == 1
    assert "systemctl reset-failed" in commands[0]
    assert "nebius-vpngw-vm-ha.service" in commands[0]
    assert "frr.service" in commands[0]
    assert "strongswan-starter.service" in commands[0]
    assert "nebius-vpngw-agent.service" in commands[0]
    assert SSHPush._vm_ha_reset_failed_commands(vm_ha=False) == ()


def test_vm_ha_materialization_waits_for_controller_convergence() -> None:
    client = _MaterializationClient([1, 1, 0])
    sleeps: list[float] = []

    SSHPush._wait_for_vm_ha_materialization(client, sleeper=sleeps.append)

    assert len(client.commands) == 3
    assert sleeps == [5.0, 5.0]
    assert all("--vm-ha-materialized" in command for command, _ in client.commands)
    assert all(kwargs == {"get_pty": True, "timeout": 20} for _, kwargs in client.commands)


def test_vm_ha_materialization_wait_is_bounded() -> None:
    client = _MaterializationClient([1, 1, 1])
    sleeps: list[float] = []

    with pytest.raises(RuntimeError, match="bounded wait"):
        SSHPush._wait_for_vm_ha_materialization(
            client,
            attempts=3,
            interval_seconds=0.25,
            sleeper=sleeps.append,
        )

    assert len(client.commands) == 3
    assert sleeps == [0.25, 0.25]


def _credential_stage_fixture(tmp_path):
    manifest, binding = _vm_ha_manifest_and_binding()
    node = manifest.vm_ha_node
    assert node is not None
    source = tmp_path / "nebius-credentials.json"
    content = _credential_payload()
    source.write_bytes(content)
    source.chmod(0o600)
    manifest.vm_ha_node = VMHANodeRecord(
        node_id=node.node_id,
        instance_index=node.instance_index,
        role=node.role,
    )
    rendered_binding = _binding_for_local_credential(
        manifest,
        binding,
        hashlib.sha256(content).hexdigest(),
    )
    return manifest, rendered_binding, str(source)


def test_vm_ha_apply_lock_is_exact_atomic_and_cleared_only_by_receipt(tmp_path) -> None:
    manifest, binding, _sources = _credential_stage_fixture(tmp_path)
    install_client = _ApplyLockClient()
    clear_client = _ApplyLockClient()
    clients = iter((install_client, clear_client))
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: next(clients), RejectPolicy=lambda: object())

    with patch("nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification") as configure:
        receipt = push.install_vm_ha_apply_lock(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            operation_id="d" * 64,
        )
        push.clear_vm_ha_apply_lock(
            "203.0.113.10",
            manifest,
            {},
            receipt=receipt,
        )

    payload = json.loads(install_client.writes[-1])
    assert payload == {
        "apply_locked": True,
        "cluster_id": "cluster-a",
        "generation_id": "a" * 64,
        "node_id": "node-a",
        "operation_id": "d" * 64,
        "schema": "nebius-vpngw/vm-ha-apply-lock-v2",
    }
    install_wrapper = next(
        command
        for command in install_client.commands
        if command.startswith("sudo /usr/bin/python3")
    )
    runner, install_command, create_parent = _locked_remote_action(install_wrapper)
    assert create_parent is True
    assert "/var/lib/nebius-vpngw/vm-ha/rearm.lock" in runner
    assert "fcntl.LOCK_EX | fcntl.LOCK_NB" in runner
    assert "time.monotonic()" in runner
    assert "import nebius_vpngw" not in runner
    assert "/usr/bin/flock" not in install_wrapper
    assert install_command.index("apply.lock.new") < install_command.index("sudo mv")
    assert "/var/lib/nebius-vpngw/vm-ha/mtls/inhibition.json" in install_command
    assert install_command.index("mtls/inhibition.json") < install_command.index("apply.lock.new")
    assert "root:root:600" in install_command
    assert install_command.index("if sudo test -e") < install_command.index("sudo mv")
    assert install_command.count(receipt.record_sha256) == 2
    clear_wrapper = next(
        command for command in clear_client.commands if command.startswith("sudo /usr/bin/python3")
    )
    _runner, clear_command, create_parent = _locked_remote_action(clear_wrapper)
    assert create_parent is False
    assert clear_command.index(receipt.record_sha256) < clear_command.index("sudo rm")
    assert "sudo test ! -e" in clear_command
    assert configure.call_args_list[0].kwargs["hostname"] == "gateway-0"
    assert install_client.closed and clear_client.closed


def test_vm_ha_apply_owner_adoption_is_bound_to_the_exact_lock(tmp_path) -> None:
    manifest, binding, _sources = _credential_stage_fixture(tmp_path)
    client = _ApplyLockClient()
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())
    lock_receipt = VMHAApplyLockReceipt(
        cluster_id="cluster-a",
        node_id="node-a",
        generation_id="a" * 64,
        operation_id="d" * 64,
        record_sha256="e" * 64,
    )

    with patch("nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification"):
        receipt = push.install_vm_ha_apply_owner_adoption(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            lock_receipt=lock_receipt,
        )

    payload = json.loads(client.writes[-1])
    generation = manifest.vm_ha_generation
    assert generation is not None
    assert payload == {
        "allocation_id": "allocation-a",
        "cluster_id": "cluster-a",
        "digests": {
            "bgp_policy": generation.digests.bgp_policy,
            "configuration": generation.digests.configuration,
            "static_routes": generation.digests.static_routes,
        },
        "generation_id": "a" * 64,
        "node_id": "node-a",
        "operation_id": "d" * 64,
        "peer_node_id": "node-b",
        "schema": "nebius-vpngw/vm-ha-apply-owner-adoption-v1",
    }
    wrapper = next(
        command for command in client.commands if command.startswith("sudo /usr/bin/python3")
    )
    _runner, action, create_parent = _locked_remote_action(wrapper)
    assert create_parent is False
    assert lock_receipt.record_sha256 in action
    assert action.index("apply.lock") < action.index("apply-owner-adoption.json.new")
    assert action.index("apply-owner-adoption.json.new") < action.index("sudo mv")
    assert action.count(receipt.record_sha256) == 2
    assert client.closed


def test_vm_ha_apply_owner_adoption_reports_writer_timeout_without_stderr(tmp_path) -> None:
    manifest, binding, _sources = _credential_stage_fixture(tmp_path)
    client = _ApplyLockClient(adoption_return_code=45)
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())
    lock_receipt = VMHAApplyLockReceipt(
        cluster_id="cluster-a",
        node_id="node-a",
        generation_id="a" * 64,
        operation_id="d" * 64,
        record_sha256="e" * 64,
    )

    with (
        patch("nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification"),
        pytest.raises(RuntimeError, match="writer lock timeout") as error,
    ):
        push.install_vm_ha_apply_owner_adoption(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            lock_receipt=lock_receipt,
        )

    assert "redacted remote detail" not in str(error.value)
    assert client.closed


def test_vm_ha_apply_lock_clear_rejects_changed_remote_record(tmp_path) -> None:
    manifest, _binding, _sources = _credential_stage_fixture(tmp_path)
    client = _ApplyLockClient(clear_return_code=42)
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())
    receipt = VMHAApplyLockReceipt(
        cluster_id="cluster-a",
        node_id="node-a",
        generation_id="a" * 64,
        operation_id="d" * 64,
        record_sha256="e" * 64,
    )

    with (
        patch("nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification"),
        pytest.raises(RuntimeError, match="clear failed"),
    ):
        push.clear_vm_ha_apply_lock(
            "203.0.113.10",
            manifest,
            {},
            receipt=receipt,
        )

    assert client.closed


def test_vm_ha_apply_lock_clear_rejects_receipt_for_another_cluster(tmp_path) -> None:
    manifest, _binding, _sources = _credential_stage_fixture(tmp_path)
    client = _ApplyLockClient()
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())
    receipt = VMHAApplyLockReceipt(
        cluster_id="cluster-b",
        node_id="node-a",
        generation_id="a" * 64,
        operation_id="d" * 64,
        record_sha256="e" * 64,
    )

    with pytest.raises(ValueError, match="does not match the node manifest"):
        push.clear_vm_ha_apply_lock(
            "203.0.113.10",
            manifest,
            {},
            receipt=receipt,
        )

    assert client.commands == []


def test_vm_ha_stage_installs_only_nebius_credential_before_manifest(tmp_path) -> None:
    manifest, binding, credential_path = _credential_stage_fixture(tmp_path)
    client = _StageClient()
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    receipt = push.stage_vm_ha_config(
        "203.0.113.10",
        manifest,
        {},
        runtime_binding=binding,
        nebius_credentials_path=credential_path,
    )

    assert "/vm-ha-credentials/" in receipt.nebius_credentials_path
    assert receipt.nebius_credentials_sha256
    credential_installs = [
        command
        for command in client.commands
        if "-m 0600" in command and "/vm-ha-credentials/" in command
    ]
    assert len(credential_installs) == 1
    assert all(command.endswith(".new") for command in credential_installs)
    assert sum("root:root:600" in command for command in client.commands) == 2
    assert all(b"PRIVATE KEY" not in write for write in client.writes)
    staging_directory = "/tmp/nebius-vpngw-vm-ha-upload-0"
    staged_manifest = f"{staging_directory}/config.yaml"
    assert (staged_manifest, "w") in client.paths
    assert (staged_manifest, 0o600) in client.chmods
    assert f"find {staging_directory} -depth -delete" in client.commands
    assert client.commands.index(credential_installs[0]) < next(
        index
        for index, command in enumerate(client.commands)
        if "/vm-ha-staged/" in command and "install -o root" in command
    )


def test_vm_ha_stage_rechecks_private_source_permissions_before_remote_io(
    tmp_path,
) -> None:
    manifest, binding, credential_path = _credential_stage_fixture(tmp_path)
    os.chmod(credential_path, 0o644)
    client = _StageClient()
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    with pytest.raises(ValueError, match="not an exact private file"):
        push.stage_vm_ha_config(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            nebius_credentials_path=credential_path,
        )

    assert client.commands == []
    assert client.writes == []


def test_vm_ha_stage_rejects_missing_explicit_host_pin_before_remote_io(
    tmp_path, monkeypatch
) -> None:
    manifest, binding, credential_path = _credential_stage_fixture(tmp_path)
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
            nebius_credentials_path=credential_path,
        )

    assert client.commands == []
    assert client.writes == []
    assert client.closed


def test_vm_ha_stage_classifies_missing_pinned_host_identity(tmp_path, monkeypatch) -> None:
    manifest, binding, credential_path = _credential_stage_fixture(tmp_path)
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
            nebius_credentials_path=credential_path,
        )

    assert client.commands == []
    assert client.closed


@pytest.mark.parametrize("fail_command_index", range(0, 9))
def test_vm_ha_stage_aborts_on_every_remote_nebius_credential_step(
    tmp_path,
    fail_command_index: int,
) -> None:
    manifest, binding, credential_path = _credential_stage_fixture(tmp_path)
    client = _StageClient(fail_command_index=fail_command_index)
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    with pytest.raises(RuntimeError):
        push.stage_vm_ha_config(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            nebius_credentials_path=credential_path,
        )

    assert client.closed


@pytest.mark.parametrize("fail_upload", range(0, 2))
def test_vm_ha_stage_aborts_on_every_manifest_or_credential_upload(
    tmp_path, fail_upload: int
) -> None:
    manifest, binding, credential_path = _credential_stage_fixture(tmp_path)
    client = _StageClient(fail_upload=fail_upload)
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    with pytest.raises((OSError, RuntimeError)):
        push.stage_vm_ha_config(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            nebius_credentials_path=credential_path,
        )

    assert "find /tmp/nebius-vpngw-vm-ha-upload-0 -depth -delete" in client.commands
    assert client.closed


def test_vm_ha_stage_fails_closed_when_private_staging_cleanup_fails(tmp_path) -> None:
    manifest, binding, credential_path = _credential_stage_fixture(tmp_path)
    client = _StageClient(fail_cleanup=True)
    push = SSHPush()
    push._paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())

    with pytest.raises(RuntimeError, match="private staging cleanup failed for node-a"):
        push.stage_vm_ha_config(
            "203.0.113.10",
            manifest,
            {},
            runtime_binding=binding,
            nebius_credentials_path=credential_path,
        )

    assert client.closed


class _DeactivationClient:
    def __init__(self, return_code: int) -> None:
        self.return_code = return_code
        self.command = ""
        self.commands: list[str] = []
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
        self.commands.append(command)
        if "VM_HA_STALE" in command:
            return BytesIO(), _CommandStream(b"VM_HA_STALE=1\n", 0), BytesIO()
        output = b"VM_HA_DEACTIVATED=1\n" if self.return_code == 0 else b""
        return BytesIO(), _CommandStream(output, self.return_code), BytesIO(b"failed")

    def close(self) -> None:
        self.closed = True


class _RemovalPhaseClient(_DeactivationClient):
    def __init__(self, *, node_id: str = "node-a") -> None:
        super().__init__(0)
        self.node_id = node_id

    def exec_command(self, command: str, **kwargs):
        self.command = command
        self.commands.append(command)
        if "--agent-capabilities" in command:
            payload = json.dumps(
                {
                    "schema": "nebius-vpngw.agent-capabilities.v1",
                    "features": [
                        "vm-ha-live-peer-replacement-v4",
                        "vm-ha-standby-replacement-inhibition-v2",
                    ],
                }
            ).encode()
            return BytesIO(), _CommandStream(payload, 0), BytesIO()
        if "--vm-ha-removal-inhibit" in command:
            schema = "nebius-vpngw/vm-ha-removal-inhibition-v1"
        elif "--vm-ha-removal-ready" in command:
            schema = "nebius-vpngw/vm-ha-removal-quiescent-v1"
        elif "--vm-ha-standby-replacement-inhibit" in command:
            schema = "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1"
        elif "--vm-ha-standby-replacement-ready" in command:
            schema = "nebius-vpngw/vm-ha-standby-replacement-quiescent-v1"
        elif "--vm-ha-standby-replacement-peer-binding" in command:
            schema = "vm-ha-live-peer-replacement-v4"
        elif "--vm-ha-standby-replacement-release" in command:
            schema = "nebius-vpngw/vm-ha-standby-replacement-release-v1"
        else:
            return (
                BytesIO(),
                _CommandStream(b"VM_HA_MUTATION_SERVICES_STOPPED=1\n", 0),
                BytesIO(),
            )
        payload = json.dumps(
            {
                "schema": schema,
                "cluster_id": "cluster-a",
                "node_id": self.node_id,
                "generation_id": "a" * 64,
                "operation_id": "d" * 64,
            }
        ).encode()
        return BytesIO(), _CommandStream(payload, 0), BytesIO()


def test_vm_ha_removal_inhibits_both_writers_before_deactivation() -> None:
    issued = [_RemovalPhaseClient(), _RemovalPhaseClient(), _RemovalPhaseClient()]
    clients = list(issued)
    push = SSHPush()
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: clients.pop(0),
        RejectPolicy=lambda: object(),
    )

    inhibition = push.inhibit_vm_ha_removal(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        node_id="node-a",
        operation_id="d" * 64,
    )
    push.verify_vm_ha_removal_quiescent(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        inhibition=inhibition,
    )
    push.stop_vm_ha_mutation_services(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
    )

    assert clients == []
    assert "--vm-ha-removal-inhibit" in issued[0].commands[-1]
    assert "--vm-ha-removal-ready" in issued[1].commands[-1]
    assert issued[2].commands[-1].index("vm-ha-rearm.service") < issued[2].commands[-1].index(
        "vm-ha.service"
    )
    assert all(client.closed for client in issued)


def test_vm_ha_apply_inhibition_requires_quiescent_exact_owner() -> None:
    client = _RemovalPhaseClient()
    push = SSHPush()
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: client,
        RejectPolicy=lambda: object(),
    )

    push.verify_vm_ha_apply_inhibition_quiescent(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        node_id="node-a",
        operation_id="d" * 64,
    )

    assert "--vm-ha-removal-ready" in client.commands[-1]
    assert client.closed


def test_vm_ha_standby_replacement_uses_capability_gated_transfer_inhibition() -> None:
    issued = [_RemovalPhaseClient(), _RemovalPhaseClient(), _RemovalPhaseClient()]
    clients = list(issued)
    push = SSHPush()
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: clients.pop(0),
        RejectPolicy=lambda: object(),
    )

    inhibition = push.inhibit_vm_ha_standby_replacement(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        node_id="node-a",
        operation_id="d" * 64,
    )
    push.verify_vm_ha_standby_replacement_quiescent(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        inhibition=inhibition,
    )
    push.release_vm_ha_standby_replacement_inhibition(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        inhibition=inhibition,
    )

    assert clients == []
    assert all("--agent-capabilities" in client.commands[0] for client in issued)
    assert "--vm-ha-standby-replacement-inhibit" in issued[0].commands[-1]
    assert "--vm-ha-standby-replacement-ready" in issued[1].commands[-1]
    assert "--vm-ha-standby-replacement-release" in issued[2].commands[-1]
    assert all(client.closed for client in issued)


def test_vm_ha_standby_replacement_waits_for_controller_acknowledgement() -> None:
    inhibition = {
        "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
        "operation_id": "d" * 64,
    }
    quiescent = {
        **inhibition,
        "schema": "nebius-vpngw/vm-ha-standby-replacement-quiescent-v1",
    }
    push = SSHPush()
    command = Mock(
        side_effect=(
            VMHAStandbyReplacementNotReady("pending"),
            quiescent,
        )
    )
    push._run_vm_ha_removal_agent_command = command  # type: ignore[method-assign]
    clock = iter((0.0, 1.0))
    sleeper = Mock()

    push.verify_vm_ha_standby_replacement_quiescent(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        inhibition=inhibition,
        timeout_seconds=30.0,
        poll_seconds=1.0,
        monotonic=lambda: next(clock),
        sleeper=sleeper,
    )

    assert command.call_count == 2
    sleeper.assert_called_once_with(1.0)


def test_vm_ha_standby_replacement_does_not_retry_permanent_ready_failure() -> None:
    push = SSHPush()
    command = Mock(side_effect=RuntimeError("foreign identity"))
    push._run_vm_ha_removal_agent_command = command  # type: ignore[method-assign]
    sleeper = Mock()

    with pytest.raises(RuntimeError, match="foreign identity"):
        push.verify_vm_ha_standby_replacement_quiescent(
            "203.0.113.10",
            "gateway-0",
            {"gateway_group": {"vm_spec": {}}},
            inhibition={"operation_id": "d" * 64},
            monotonic=lambda: 0.0,
            sleeper=sleeper,
        )

    command.assert_called_once()
    sleeper.assert_not_called()


def test_vm_ha_standby_replacement_default_wait_spans_one_cloud_request_deadline() -> None:
    inhibition = {"operation_id": "d" * 64}
    quiescent = {
        **inhibition,
        "schema": "nebius-vpngw/vm-ha-standby-replacement-quiescent-v1",
    }
    push = SSHPush()
    command = Mock(
        side_effect=(
            VMHAStandbyReplacementNotReady("pending"),
            VMHAStandbyReplacementNotReady("pending"),
            quiescent,
        )
    )
    push._run_vm_ha_removal_agent_command = command  # type: ignore[method-assign]
    clock = iter((0.0, 30.1, 59.5))
    sleeper = Mock()

    push.verify_vm_ha_standby_replacement_quiescent(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        inhibition=inhibition,
        monotonic=lambda: next(clock),
        sleeper=sleeper,
    )

    assert command.call_count == 3
    assert sleeper.call_count == 2


def test_vm_ha_standby_replacement_makes_final_probe_after_deadline() -> None:
    inhibition = {"operation_id": "d" * 64}
    quiescent = {
        **inhibition,
        "schema": "nebius-vpngw/vm-ha-standby-replacement-quiescent-v1",
    }
    push = SSHPush()
    command = Mock(
        side_effect=(
            VMHAStandbyReplacementNotReady("pending"),
            VMHAStandbyReplacementNotReady("pending"),
            quiescent,
        )
    )
    push._run_vm_ha_removal_agent_command = command  # type: ignore[method-assign]
    clock = iter((0.0, 59.5, 60.1))
    sleeper = Mock()

    push.verify_vm_ha_standby_replacement_quiescent(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        inhibition=inhibition,
        monotonic=lambda: next(clock),
        sleeper=sleeper,
    )

    assert command.call_count == 3
    sleeper.assert_called_once_with(0.5)


def test_vm_ha_standby_replacement_final_probe_still_fails_closed() -> None:
    push = SSHPush()
    command = Mock(side_effect=VMHAStandbyReplacementNotReady("pending"))
    push._run_vm_ha_removal_agent_command = command  # type: ignore[method-assign]
    clock = iter((0.0, 60.1))
    sleeper = Mock()

    with pytest.raises(
        VMHAStandbyReplacementNotReady,
        match="did not become controller-quiescent within the bounded wait",
    ):
        push.verify_vm_ha_standby_replacement_quiescent(
            "203.0.113.10",
            "gateway-0",
            {"gateway_group": {"vm_spec": {}}},
            inhibition={"operation_id": "d" * 64},
            monotonic=lambda: next(clock),
            sleeper=sleeper,
        )

    assert command.call_count == 2
    sleeper.assert_not_called()


def test_vm_ha_standby_replacement_publishes_peer_binding_without_service_command() -> None:
    client = _RemovalPhaseClient()
    push = SSHPush()
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: client,
        RejectPolicy=lambda: object(),
    )
    inhibition = {
        "schema": "nebius-vpngw/vm-ha-standby-replacement-inhibition-v1",
        "cluster_id": "cluster-a",
        "node_id": "node-a",
        "generation_id": "a" * 64,
        "operation_id": "d" * 64,
    }

    push.commit_vm_ha_standby_replacement_peer_binding(
        "203.0.113.10",
        "gateway-0",
        {"gateway_group": {"vm_spec": {}}},
        inhibition=inhibition,
    )

    assert "--vm-ha-standby-replacement-peer-binding" in client.commands[-1]
    assert all("systemctl" not in command for command in client.commands)


def test_vm_ha_deactivation_is_ordered_and_fail_closed() -> None:
    client = _DeactivationClient(return_code=0)
    paramiko = SimpleNamespace(SSHClient=lambda: client, RejectPolicy=lambda: object())
    push = SSHPush()
    push._paramiko = paramiko

    changed = push.deactivate_vm_ha("203.0.113.10", {"gateway_group": {"vm_spec": {}}})

    assert changed
    _runner, removal, create_parent = _locked_remote_action(client.commands[-1])
    assert create_parent is True
    assert removal.index("disable --now nebius-vpngw-vm-ha-rearm.service") < removal.index(
        "rm -f /etc/nebius-vpngw/vm-ha-enabled"
    )
    assert removal.index("30-vm-ha.conf") < removal.index("daemon-reload")
    state_cleanup = (
        "find /var/lib/nebius-vpngw/vm-ha -mindepth 1 -depth "
        "! -path /var/lib/nebius-vpngw/vm-ha/rearm.lock -delete"
    )
    assert removal.index("daemon-reload") < removal.index(state_cleanup)
    assert "find /var/lib/nebius-vpngw/vm-ha -depth -delete" not in removal
    assert 'rearm_lock="$state_dir/rearm.lock"' in client.commands[0]
    assert client.closed

    retired = _DeactivationClient(return_code=0)
    push._paramiko = SimpleNamespace(SSHClient=lambda: retired, RejectPolicy=lambda: object())
    push.deactivate_vm_ha("203.0.113.11", {"gateway_group": {"vm_spec": {}}}, retire_member=True)
    assert "stale=1" in retired.commands[0]
    _runner, retired_action, _create_parent = _locked_remote_action(retired.commands[-1])
    assert "nebius-vpngw-agent.service" in retired_action
    assert "nebius-vpngw-health-monitor.service" in retired_action
    assert "nebius-vpngw-fix-routes.timer" in retired_action
    assert "nebius-vpngw-fix-routes.service" in retired_action
    assert "config-resolved.yaml" in retired_action

    failing = _DeactivationClient(return_code=1)
    push._paramiko = SimpleNamespace(SSHClient=lambda: failing, RejectPolicy=lambda: object())
    with pytest.raises(RuntimeError, match="deactivation failed"):
        push.deactivate_vm_ha("203.0.113.10", {"gateway_group": {"vm_spec": {}}})


class _VerificationClient(_DeactivationClient):
    def exec_command(self, command: str, **kwargs):
        self.command = command
        output = b"VM_HA_TERMINAL_NON_HA=1\n" if self.return_code == 0 else b""
        return BytesIO(), _CommandStream(output, self.return_code), BytesIO(b"incomplete")


def test_vm_ha_deactivation_binds_stable_identity_to_transport_address() -> None:
    deactivation = _DeactivationClient(return_code=0)
    verification = _VerificationClient(return_code=0)
    clients = [deactivation, verification]
    policy = object()
    push = SSHPush(ssh_policy=policy)  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(
        SSHClient=lambda: clients.pop(0),
        RejectPolicy=lambda: object(),
    )

    with patch("nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification") as configure:
        push.deactivate_vm_ha(
            "203.0.113.10",
            {"gateway_group": {"vm_spec": {}}},
            instance_name="gateway-0",
        )
        push.verify_vm_ha_deactivated(
            "203.0.113.10",
            {"gateway_group": {"vm_spec": {}}},
            instance_name="gateway-0",
        )

    assert clients == []
    assert [call.kwargs["hostname"] for call in configure.call_args_list] == [
        "gateway-0",
        "gateway-0",
    ]
    assert [call.kwargs["transport_host"] for call in configure.call_args_list] == [
        "203.0.113.10",
        "203.0.113.10",
    ]


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
    assert 'test -f "$rearm_lock"' in client.command
    assert '! -path "$rearm_lock"' in client.command
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


def test_vm_ha_activation_classifies_host_identity_rejection(monkeypatch, tmp_path) -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel)
    artifact = VMHAAgentArtifact.from_wheel(wheel, source="test")
    rendered = SSHPush._render_vm_ha_config(manifest, binding)
    receipt = SSHPush._vm_ha_receipt(
        manifest,
        rendered,
        runtime_binding=binding,
        nebius_credentials_path=binding.nodes[0].nebius_credentials_path,
        nebius_credentials_sha256="d" * 64,
    )
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
            agent_artifact=artifact,
        )


def test_vm_ha_activation_fails_closed_on_ssh_connect(monkeypatch, tmp_path) -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel)
    artifact = VMHAAgentArtifact.from_wheel(wheel, source="test")
    rendered = SSHPush._render_vm_ha_config(manifest, binding)
    receipt = SSHPush._vm_ha_receipt(
        manifest,
        rendered,
        runtime_binding=binding,
        nebius_credentials_path=binding.nodes[0].nebius_credentials_path,
        nebius_credentials_sha256="d" * 64,
    )
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
            agent_artifact=artifact,
        )


def test_vm_ha_activation_reuses_approved_artifact_without_rebuild(
    monkeypatch,
    tmp_path,
) -> None:
    manifest, binding = _vm_ha_manifest_and_binding()
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    _write_vm_ha_agent_wheel(wheel)
    artifact = VMHAAgentArtifact.from_wheel(wheel, source="test")
    rendered = SSHPush._render_vm_ha_config(manifest, binding)
    receipt = SSHPush._vm_ha_receipt(
        manifest,
        rendered,
        runtime_binding=binding,
        nebius_credentials_path=binding.nodes[0].nebius_credentials_path,
        nebius_credentials_sha256="d" * 64,
    )
    client = _ManagedMTLSClient()
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client)
    monkeypatch.setattr("nebius_vpngw.agent.main.vm_ha_runtime_blockers", lambda: ())
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        push,
        "_build_wheel",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("approved artifact was rebuilt during activation")
        ),
    )
    sleeps: list[float] = []
    monkeypatch.setattr("nebius_vpngw.deploy.ssh_push.time.sleep", sleeps.append)

    push.push_config_and_reload(
        "203.0.113.10",
        manifest,
        {"gateway_group": {"vm_spec": {}}},
        staged_receipt=receipt,
        runtime_binding=binding,
        agent_artifact=artifact,
    )

    assert client.sftp.uploads == []
    assert all("pip install" not in command for command in client.commands)
    activation_commands = "\n".join(client.commands)
    for source, _destination, _mode in _VM_HA_SERVICE_ASSET_DESTINATIONS:
        assert f"/tmp/{source}" not in activation_commands
    assert (
        "sudo /usr/local/bin/nebius-vpngw-vm-ha-peer-firewall.sh "
        "/etc/nebius-vpngw/config-resolved.yaml"
    ) in client.commands
    assert (
        "sudo systemd-tmpfiles --create /usr/lib/tmpfiles.d/nebius-vpngw-ufw-lock.conf"
    ) in client.commands
    assert sleeps == [5.0, 5.0]


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


def test_ordinary_push_installs_only_ordinary_assets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "nebius_vpngw-1.2.3-py3-none-any.whl"
    wheel.write_bytes(b"ordinary-wheel")
    client = _StageClient()
    push = SSHPush(ssh_policy=object())  # type: ignore[arg-type]
    push._paramiko = SimpleNamespace(SSHClient=lambda: client)
    monkeypatch.setattr(
        "nebius_vpngw.deploy.ssh_push.configure_paramiko_host_verification",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(push, "_build_wheel", lambda: wheel)
    sleeps: list[float] = []
    monkeypatch.setattr("nebius_vpngw.deploy.ssh_push.time.sleep", sleeps.append)

    push.push_config_and_reload(
        "203.0.113.10",
        SimpleNamespace(
            instance_index=0,
            hostname="gateway-0",
            config_yaml="gateway: {}\n",
        ),
        {"gateway_group": {"vm_spec": {}}},
    )

    staged_paths = {path for path, _mode in client.paths}
    commands = "\n".join(client.commands)
    assert {
        "/tmp/nebius-vpngw-agent.service",
        "/tmp/nebius-vpngw-fix-routes.service",
        "/tmp/nebius-vpngw-fix-routes.timer",
        "/tmp/nebius-vpngw-health-monitor.service",
        "/tmp/setup-vpngw-firewall.sh",
        "/tmp/nebius-vpngw-esp4-preflight.sh",
    }.issubset(staged_paths)
    forbidden = (
        "vm-ha",
        "30-vm-ha.conf",
        "ufw-lock",
        "/etc/nebius-vpngw/vm-ha-enabled",
    )
    assert all(not any(marker in path for marker in forbidden) for path in staged_paths)
    assert all(marker not in commands for marker in forbidden)
    assert sleeps == [5.0, 5.0, 5.0]


def test_configured_public_key_disables_paramiko_authentication_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    client = SimpleNamespace(connect=lambda **kwargs: observed.update(kwargs))
    auth = SimpleNamespace(
        paramiko_connect_kwargs=lambda: {
            "pkey": object(),
            "look_for_keys": False,
            "allow_agent": False,
            "password": None,
        }
    )
    resolve = Mock(return_value=auth)
    monkeypatch.setattr("nebius_vpngw.deploy.ssh_push.resolve_ssh_client_auth", resolve)

    SSHPush()._connect_client(  # noqa: SLF001
        client,
        hostname="203.0.113.10",
        username="operator",
        vm_spec={"ssh_public_key": "ssh-ed25519 fixture"},
    )

    resolve.assert_called_once()
    assert observed["look_for_keys"] is False
    assert observed["allow_agent"] is False
    assert observed["password"] is None
    assert "key_filename" not in observed
