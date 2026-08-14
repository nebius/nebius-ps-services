from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname, urlopen

import yaml

from ..config_loader import InstanceResolvedConfig
from ..schema import (
    VMHACredentialReferences,
    VMHACredentialSourceReferences,
    VMHARuntimeBinding,
)
from .ssh_policy import configure_paramiko_host_verification


def _host_identity_failure(
    error: Exception, paramiko: Any, ssh_target: str
) -> RuntimeError | None:
    bad_host_key = getattr(paramiko, "BadHostKeyException", ())
    if isinstance(error, bad_host_key):
        return RuntimeError(f"SSH host identity verification failed for {ssh_target}")
    ssh_exception = getattr(paramiko, "SSHException", ())
    message = str(error).lower()
    if isinstance(error, ssh_exception) and (
        "known_hosts" in message or "host key" in message or "known host" in message
    ):
        return RuntimeError(f"SSH host identity verification failed for {ssh_target}")
    return None


@dataclass(frozen=True)
class VMHAStageReceipt:
    """Secret-free acknowledgement for one exact staged node generation."""

    node_id: str
    generation_id: str
    configuration_digest: str
    static_routes_digest: str
    bgp_policy_digest: str
    staged_file_sha256: str
    credential_sha256: tuple[tuple[str, str], ...]


class SSHPush:
    """Push per-VM config and trigger agent reload via SSH using Paramiko.

    Looks for the following optional fields in the loaded YAML config under
    `gateway_group.vm_spec`:
      - ssh_username (default: "ubuntu")
      - ssh_private_key_path (if omitted, relies on SSH agent/known defaults)
    """

    def __init__(self) -> None:
        # Lazy import to avoid hard dependency when running dry-run
        self._paramiko = None
        self._wheel_path: Path | None = None
        self._temp_wheel_dir: Path | None = None

    def _ensure_paramiko(self):
        if self._paramiko is None:
            import paramiko  # type: ignore

            self._paramiko = paramiko
        return self._paramiko

    @staticmethod
    def _render_vm_ha_config(
        inst_cfg: InstanceResolvedConfig,
        runtime_binding: VMHARuntimeBinding,
    ) -> str:
        """Bind authoritative post-provision identities into one node manifest."""

        node = inst_cfg.vm_ha_node
        generation = inst_cfg.vm_ha_generation
        readiness = inst_cfg.vm_ha_readiness
        if node is None or generation is None or readiness is None:
            raise ValueError("VM-HA staging requires a complete resolved node manifest")
        bound_nodes = [item for item in runtime_binding.nodes if item.node_id == node.node_id]
        if len(bound_nodes) != 1 or bound_nodes[0].role.value != node.role.value:
            raise ValueError("VM-HA runtime binding does not match the staged node")
        payload = yaml.safe_load(inst_cfg.config_yaml)
        if not isinstance(payload, dict) or not isinstance(payload.get("vm_ha"), dict):
            raise ValueError("VM-HA resolved YAML has no node manifest")
        if not (
            runtime_binding.cluster_id == payload["vm_ha"].get("cluster_id")
            and runtime_binding.generation_id == generation.generation_id
            and runtime_binding.configuration_digest == generation.digests.configuration
            and runtime_binding.static_routes_digest == generation.digests.static_routes
            and runtime_binding.bgp_policy_digest == generation.digests.bgp_policy
        ):
            raise ValueError("VM-HA runtime binding does not match the staged generation")
        manifests = (payload["vm_ha"].get("generation") or {}).get("logical_manifests")
        expected_manifests = generation.logical_manifests
        if manifests != {
            "static_routes_json": expected_manifests.static_routes_json,
            "bgp_policy_json": expected_manifests.bgp_policy_json,
        }:
            raise ValueError("VM-HA staged logical manifests differ from the committed generation")
        if (
            hashlib.sha256(manifests["static_routes_json"].encode()).hexdigest()
            != runtime_binding.static_routes_digest
            or hashlib.sha256(manifests["bgp_policy_json"].encode()).hexdigest()
            != runtime_binding.bgp_policy_digest
        ):
            raise ValueError("VM-HA staged logical manifest digest mismatch")

        payload["vm_ha"]["runtime_binding"] = runtime_binding.model_dump(mode="json")
        return yaml.safe_dump(payload, sort_keys=False)

    @staticmethod
    def _credential_targets(
        node_id: str,
        generation_id: str,
        bundle_digest: str,
        references: VMHACredentialReferences,
    ) -> tuple[tuple[str, str], ...]:
        base = f"/etc/nebius-vpngw/vm-ha-credentials/{generation_id}/{node_id}/{bundle_digest}"
        expected = (
            ("certificate_authority", f"{base}/ca.crt"),
            ("certificate", f"{base}/{node_id}.crt"),
            ("private_key", f"{base}/{node_id}.key"),
            ("nebius_credentials", f"{base}/nebius-credentials.json"),
        )
        actual = tuple((name, str(getattr(references, name))) for name, _ in expected)
        if actual != expected:
            raise ValueError("VM-HA runtime binding has non-canonical credential targets")
        return actual

    @staticmethod
    def _read_credential_file(path_text: str, *, label: str, node_id: str) -> bytes:
        try:
            descriptor = os.open(path_text, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError
                chunks: list[bytes] = []
                while chunk := os.read(descriptor, 1024 * 1024):
                    chunks.append(chunk)
            finally:
                os.close(descriptor)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"VM-HA credential source {label} for {node_id} is not a readable regular file"
            ) from exc
        return b"".join(chunks)

    @classmethod
    def _credential_source_payloads(
        cls,
        *,
        node_id: str,
        sources: VMHACredentialSourceReferences,
    ) -> tuple[tuple[str, bytes, str], ...]:
        payloads: list[tuple[str, bytes, str]] = []
        for label in (
            "certificate_authority",
            "certificate",
            "private_key",
            "nebius_credentials",
        ):
            content = cls._read_credential_file(
                str(getattr(sources, label)), label=label, node_id=node_id
            )
            payloads.append((label, content, hashlib.sha256(content).hexdigest()))
        return tuple(payloads)

    @staticmethod
    def _credential_bundle_digest(payloads: tuple[tuple[str, bytes, str], ...]) -> str:
        identity = "\n".join(f"{label}:{digest}" for label, _, digest in payloads)
        return hashlib.sha256(identity.encode("ascii")).hexdigest()

    @classmethod
    def _runtime_binding_for_credential_bundle(
        cls,
        *,
        inst_cfg: InstanceResolvedConfig,
        runtime_binding: VMHARuntimeBinding,
        credential_digests: tuple[tuple[str, str], ...],
    ) -> VMHARuntimeBinding:
        node = inst_cfg.vm_ha_node
        generation = inst_cfg.vm_ha_generation
        if node is None or generation is None or len(credential_digests) != 4:
            raise ValueError("VM-HA credential bundle identity is incomplete")
        labels = (
            "certificate_authority",
            "certificate",
            "private_key",
            "nebius_credentials",
        )
        payload_identity = tuple(
            (label, b"", digest)
            for label, (_target, digest) in zip(labels, credential_digests, strict=True)
        )
        bundle_digest = cls._credential_bundle_digest(payload_identity)
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
        expected_targets = cls._credential_targets(
            node.node_id,
            generation.generation_id,
            bundle_digest,
            references,
        )
        if tuple(target for target, _ in credential_digests) != tuple(
            target for _, target in expected_targets
        ):
            raise ValueError("VM-HA credential receipt has non-canonical target paths")
        nodes = tuple(
            item.model_copy(update={"credentials": references})
            if item.node_id == node.node_id
            else item
            for item in runtime_binding.nodes
        )
        return runtime_binding.model_copy(update={"nodes": nodes})

    @staticmethod
    def _vm_ha_receipt(
        inst_cfg: InstanceResolvedConfig,
        rendered_config: str,
        credential_sha256: tuple[tuple[str, str], ...] = (),
    ) -> VMHAStageReceipt:
        node = inst_cfg.vm_ha_node
        generation = inst_cfg.vm_ha_generation
        readiness = inst_cfg.vm_ha_readiness
        if node is None or generation is None or readiness is None:
            raise ValueError("VM-HA staging requires a complete resolved node manifest")
        if generation.generation_id != readiness.generation_id:
            raise ValueError("VM-HA generation and readiness identities do not match")
        if generation.digests != readiness.digests:
            raise ValueError("VM-HA generation and readiness digests do not match")
        return VMHAStageReceipt(
            node_id=node.node_id,
            generation_id=generation.generation_id,
            configuration_digest=generation.digests.configuration,
            static_routes_digest=generation.digests.static_routes,
            bgp_policy_digest=generation.digests.bgp_policy,
            staged_file_sha256=hashlib.sha256(rendered_config.encode("utf-8")).hexdigest(),
            credential_sha256=credential_sha256,
        )

    @staticmethod
    def _vm_ha_staged_verify_command(receipt: VMHAStageReceipt) -> str:
        path = f"/etc/nebius-vpngw/vm-ha-staged/{receipt.generation_id}.yaml"
        checks = [f"echo '{receipt.staged_file_sha256}  {path}' | sudo sha256sum --check --status"]
        for target, digest in receipt.credential_sha256:
            checks.append(
                f"sudo test \"$(sudo stat -c '%U:%G:%a' {target})\" = root:root:600 && "
                f"echo '{digest}  {target}' | sudo sha256sum --check --status"
            )
        return " && ".join(checks)

    @staticmethod
    def _credential_install_commands(
        *,
        base: str,
        temporary: str,
        target: str,
        digest: str,
    ) -> tuple[str, ...]:
        pending = f"{target}.new"
        return (
            f"sudo install -d -o root -g root -m 0700 {base}",
            f"sudo install -o root -g root -m 0600 {temporary} {pending}",
            f"sudo test \"$(sudo stat -c '%U:%G:%a' {pending})\" = root:root:600",
            f"echo '{digest}  {pending}' | sudo sha256sum --check --status",
            f"sudo mv {pending} {target}",
            f"sudo test \"$(sudo stat -c '%U:%G:%a' {target})\" = root:root:600",
            f"echo '{digest}  {target}' | sudo sha256sum --check --status",
        )

    def stage_vm_ha_config(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
        *,
        runtime_binding: VMHARuntimeBinding,
        credential_sources: VMHACredentialSourceReferences,
    ) -> VMHAStageReceipt:
        """Stage and verify one node without activating it or reloading services."""

        node = inst_cfg.vm_ha_node
        assert node is not None
        if credential_sources != node.credential_sources:
            raise ValueError("VM-HA credential source bundle does not match the staged node")
        source_payloads = self._credential_source_payloads(
            node_id=node.node_id,
            sources=credential_sources,
        )
        generation = inst_cfg.vm_ha_generation
        assert generation is not None
        bundle_digest = self._credential_bundle_digest(source_payloads)
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
        targets = self._credential_targets(
            node.node_id, generation.generation_id, bundle_digest, references
        )
        credentials = tuple(
            (label, target, content, digest)
            for (label, content, digest), (_, target) in zip(source_payloads, targets, strict=True)
        )
        credential_digests = tuple((target, digest) for _, target, _, digest in credentials)
        staged_binding = self._runtime_binding_for_credential_bundle(
            inst_cfg=inst_cfg,
            runtime_binding=runtime_binding,
            credential_digests=credential_digests,
        )
        rendered_config = self._render_vm_ha_config(inst_cfg, staged_binding)
        receipt = self._vm_ha_receipt(
            inst_cfg,
            rendered_config,
            credential_digests,
        )
        paramiko = self._ensure_paramiko()
        gg = local_cfg.get("gateway_group") or {}
        vm_spec = gg.get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        key_path: str | None = vm_spec.get("ssh_private_key_path") or os.environ.get(
            "VPNGW_SSH_KEY"
        )
        key_file = Path(key_path).expanduser() if key_path else None
        client = paramiko.SSHClient()
        upload_directory: str | None = None
        try:
            configure_paramiko_host_verification(client, paramiko)
            client.connect(
                hostname=ssh_target,
                username=username,
                key_filename=str(key_file) if key_file else None,
                look_for_keys=True,
                allow_agent=True,
                timeout=15,
            )
            temporary = f"/tmp/nebius-vpngw-vm-ha-stage-{inst_cfg.instance_index}.yaml"
            destination = f"/etc/nebius-vpngw/vm-ha-staged/{receipt.generation_id}.yaml"
            with client.open_sftp() as sftp, sftp.file(temporary, "w") as stream:
                stream.write(rendered_config)
            upload_directory = f"/tmp/nebius-vpngw-vm-ha-upload-{inst_cfg.instance_index}"
            stdin, stdout, stderr = client.exec_command(
                f"install -d -m 0700 {upload_directory}", timeout=30
            )
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError(
                    f"VM-HA credential upload preparation failed for {receipt.node_id}"
                )
            for label, target, content, digest in credentials:
                credential_temporary = f"{upload_directory}/{label}"
                with client.open_sftp() as sftp, sftp.file(credential_temporary, "wb") as stream:
                    stream.write(content)
                    sftp.chmod(credential_temporary, 0o600)
                for command in self._credential_install_commands(
                    base=base,
                    temporary=credential_temporary,
                    target=target,
                    digest=digest,
                ):
                    stdin, stdout, stderr = client.exec_command(command, timeout=30)
                    if stdout.channel.recv_exit_status() != 0:
                        raise RuntimeError(
                            f"VM-HA credential installation failed for {receipt.node_id}:{label}"
                        )
            command = (
                "sudo install -d -m 0700 /etc/nebius-vpngw/vm-ha-staged && "
                f"sudo install -o root -g root -m 0600 {temporary} {destination} && "
                f"sudo sha256sum {destination}"
            )
            stdin, stdout, stderr = client.exec_command(command, timeout=30)
            return_code = stdout.channel.recv_exit_status()
            observed = stdout.read().decode().strip().split(maxsplit=1)[0]
            if return_code != 0 or observed != receipt.staged_file_sha256:
                raise RuntimeError(f"VM-HA stage verification failed for {receipt.node_id}")
            return receipt
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            if upload_directory is not None:
                try:
                    client.exec_command(f"find {upload_directory} -depth -delete", timeout=30)
                except Exception:
                    pass
            client.close()

    def deactivate_vm_ha(self, ssh_target: str, local_cfg: dict) -> bool:
        """Remove stale HA activation state before an ordinary agent restart."""

        if not ssh_target:
            raise ValueError("VM-HA deactivation requires an SSH target")
        paramiko = self._ensure_paramiko()
        vm_spec = (local_cfg.get("gateway_group") or {}).get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        key_path: str | None = vm_spec.get("ssh_private_key_path") or os.environ.get(
            "VPNGW_SSH_KEY"
        )
        key_file = Path(key_path).expanduser() if key_path else None
        client = paramiko.SSHClient()
        command = """sudo /bin/bash -lc '
set -eu
stale=0
for path in /etc/nebius-vpngw/vm-ha-enabled /etc/systemd/system/nebius-vpngw-vm-ha.service /etc/systemd/system/nebius-vpngw-vm-ha-guard.service /etc/systemd/system/strongswan-starter.service.d/30-vm-ha.conf /etc/systemd/system/strongswan.service.d/30-vm-ha.conf /etc/systemd/system/frr.service.d/30-vm-ha.conf /etc/systemd/system/nebius-vpngw-agent.service.d/30-vm-ha.conf /etc/nebius-vpngw/vm-ha-staged /etc/nebius-vpngw/vm-ha-credentials /etc/nebius-vpngw/vm-ha /var/lib/nebius-vpngw/vm-ha; do
  if [ -e "$path" ]; then stale=1; fi
done
for unit in nebius-vpngw-vm-ha.service nebius-vpngw-vm-ha-guard.service; do
  if systemctl list-unit-files --no-legend "$unit" 2>/dev/null | grep -q "^$unit"; then
    stale=1
  fi
done
if [ "$stale" -eq 0 ]; then
  printf "VM_HA_DEACTIVATED=0\\n"
  exit 0
fi
for unit in nebius-vpngw-vm-ha.service nebius-vpngw-vm-ha-guard.service; do
  if systemctl list-unit-files --no-legend "$unit" 2>/dev/null | grep -q "^$unit"; then
    systemctl disable --now "$unit"
  fi
done
rm -f /etc/nebius-vpngw/vm-ha-enabled
rm -f /etc/systemd/system/nebius-vpngw-vm-ha.service
rm -f /etc/systemd/system/nebius-vpngw-vm-ha-guard.service
rm -f /etc/systemd/system/strongswan-starter.service.d/30-vm-ha.conf
rm -f /etc/systemd/system/strongswan.service.d/30-vm-ha.conf
rm -f /etc/systemd/system/frr.service.d/30-vm-ha.conf
rm -f /etc/systemd/system/nebius-vpngw-agent.service.d/30-vm-ha.conf
for path in /etc/nebius-vpngw/vm-ha-staged /etc/nebius-vpngw/vm-ha-credentials /etc/nebius-vpngw/vm-ha /var/lib/nebius-vpngw/vm-ha; do
  if [ -d "$path" ]; then find "$path" -depth -delete; fi
done
systemctl daemon-reload
printf "VM_HA_DEACTIVATED=%s\\n" "$stale"
'"""
        try:
            configure_paramiko_host_verification(client, paramiko)
            client.connect(
                hostname=ssh_target,
                username=username,
                key_filename=str(key_file) if key_file else None,
                look_for_keys=True,
                allow_agent=True,
                timeout=15,
            )
            stdin, stdout, stderr = client.exec_command(command, get_pty=True, timeout=60)
            return_code = stdout.channel.recv_exit_status()
            if return_code != 0:
                detail = stderr.read().decode().strip()
                raise RuntimeError(f"VM-HA deactivation failed: {detail or return_code}")
            return "VM_HA_DEACTIVATED=1" in stdout.read().decode().splitlines()
        except Exception as error:
            identity_failure = _host_identity_failure(error, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from error
            raise
        finally:
            client.close()

    def _find_project_root(self) -> Path | None:
        """Locate repo root based on the installed module path."""
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "pyproject.toml").exists():
                return parent
        return None

    def _select_wheel_from_dirs(self, search_dirs: list[Path]) -> Path | None:
        """Pick the best available wheel from search dirs, preferring current version."""
        version = None
        try:
            version = metadata.version("nebius-vpngw")
        except metadata.PackageNotFoundError:
            version = None

        candidates: list[Path] = []
        for directory in search_dirs:
            if not directory.exists():
                continue
            wheels = sorted(
                directory.glob("nebius_vpngw-*.whl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if not wheels:
                continue
            if version:
                for wheel in wheels:
                    if f"nebius_vpngw-{version}-" in wheel.name:
                        return wheel
            candidates.extend(wheels)

        if not candidates:
            return None
        return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]

    def _download_install_wheel(self, wheel_url: str, wheel_name: str) -> Path | None:
        if self._temp_wheel_dir is None:
            self._temp_wheel_dir = Path(tempfile.mkdtemp(prefix="nebius-vpngw-wheel-"))

        target_path = self._temp_wheel_dir / wheel_name
        if target_path.exists():
            return target_path

        print("[SSHPush] Downloading the originally installed release wheel...")
        try:
            with urlopen(wheel_url, timeout=60) as response, target_path.open("wb") as target:
                shutil.copyfileobj(response, target)
        except Exception as e:
            if target_path.exists():
                target_path.unlink()
            print(f"[SSHPush] WARNING: Failed to download the original install wheel: {e}")
            return None
        return target_path

    def _wheel_from_install_metadata(self) -> Path | None:
        try:
            distribution = metadata.distribution("nebius-vpngw")
        except metadata.PackageNotFoundError:
            return None

        direct_url_raw = distribution.read_text("direct_url.json")
        if not direct_url_raw:
            return None

        try:
            direct_url = json.loads(direct_url_raw)
        except json.JSONDecodeError:
            print("[SSHPush] WARNING: Could not parse direct_url.json for installed package")
            return None

        wheel_url = direct_url.get("url")
        if not isinstance(wheel_url, str) or not wheel_url:
            return None

        parsed_url = urlparse(wheel_url)
        wheel_name = Path(unquote(parsed_url.path)).name
        if not wheel_name.endswith(".whl"):
            return None

        if parsed_url.scheme == "file":
            local_path_str = url2pathname(unquote(parsed_url.path))
            if parsed_url.netloc:
                local_path_str = f"//{parsed_url.netloc}{local_path_str}"
            local_path = Path(local_path_str)
            if local_path.is_file():
                print(f"[SSHPush] Reusing install wheel from local file: {local_path.name}")
                return local_path
            print(
                "[SSHPush] WARNING: The original install wheel is no longer present at the "
                "recorded file path"
            )
            return None

        if parsed_url.scheme in {"http", "https"}:
            downloaded_wheel = self._download_install_wheel(wheel_url, wheel_name)
            if downloaded_wheel:
                print(f"[SSHPush] Reusing install wheel from original URL: {downloaded_wheel.name}")
            return downloaded_wheel

        return None

    def _build_wheel(self) -> Path | None:
        """Build the nebius-vpngw wheel package if not already built."""
        if self._wheel_path and self._wheel_path.exists():
            return self._wheel_path

        wheel_override = os.environ.get("VPNGW_AGENT_WHEEL")
        if wheel_override:
            override_path = Path(wheel_override).expanduser()
            if override_path.is_file():
                self._wheel_path = override_path
                print(f"[SSHPush] Using wheel from VPNGW_AGENT_WHEEL: {override_path.name}")
                return self._wheel_path
            print(f"[SSHPush] WARNING: VPNGW_AGENT_WHEEL not found: {override_path}")

        # Find project root (where pyproject.toml is) from the installed module path.
        project_root = self._find_project_root()
        if not project_root:
            # Release/pipx install: look for a local wheel in cwd or ./dist
            local_wheel = self._select_wheel_from_dirs([Path.cwd(), Path.cwd() / "dist"])
            if local_wheel:
                self._wheel_path = local_wheel
                print(f"[SSHPush] Using local wheel: {self._wheel_path.name}")
                return self._wheel_path
            install_wheel = self._wheel_from_install_metadata()
            if install_wheel:
                self._wheel_path = install_wheel
                print(f"[SSHPush] Using install wheel: {self._wheel_path.name}")
                return self._wheel_path
            print(
                "[SSHPush] WARNING: No local wheel found. "
                "Set VPNGW_AGENT_WHEEL, keep the original release URL/file accessible, "
                "or run apply from a directory containing the wheel (or ./dist)."
            )
            return None

        dist_dir = project_root / "dist"

        # Always attempt to build latest wheel if pyproject is present
        if (project_root / "pyproject.toml").exists():
            # Clean old wheels to prevent stale dependencies
            if dist_dir.exists():
                old_wheels = list(dist_dir.glob("nebius_vpngw-*.whl"))
                if old_wheels:
                    print(
                        f"[SSHPush] Removing {len(old_wheels)} old wheel(s) to ensure fresh build..."
                    )
                    for old_wheel in old_wheels:
                        old_wheel.unlink()

            print("[SSHPush] Building nebius-vpngw wheel package with python -m build...")
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "build", "--wheel"],
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                if result.returncode != 0:
                    print(f"[SSHPush] Wheel build failed: {result.stderr}")
            except FileNotFoundError:
                print(
                    "[SSHPush] WARNING: 'build' module not found. Install with: pip install build"
                )
            except Exception as e:
                print(f"[SSHPush] Wheel build error: {e}")

        # Reuse newest existing wheel (works with python -m build)
        selected_wheel = self._select_wheel_from_dirs([dist_dir]) if dist_dir.exists() else None
        if selected_wheel:
            self._wheel_path = selected_wheel
            print(f"[SSHPush] Using wheel: {selected_wheel.name}")
            return selected_wheel

        install_wheel = self._wheel_from_install_metadata()
        if install_wheel:
            print(
                "[SSHPush] WARNING: Fresh build unavailable; falling back to the originally "
                "installed wheel. Local source changes will not be deployed."
            )
            self._wheel_path = install_wheel
            print(f"[SSHPush] Using fallback wheel: {self._wheel_path.name}")
            return self._wheel_path

        if dist_dir.exists():
            print("[SSHPush] No wheel found in dist/ after build attempt")
        else:
            print("[SSHPush] dist/ directory not found; wheel not built")
        return None

    def push_config_and_reload(
        self,
        ssh_target: str,
        inst_cfg: InstanceResolvedConfig,
        local_cfg: dict,
        *,
        staged_receipt: VMHAStageReceipt | None = None,
        runtime_binding: VMHARuntimeBinding | None = None,
        fail_closed: bool = False,
    ) -> None:
        required_remote = staged_receipt is not None or fail_closed
        if staged_receipt is not None:
            if runtime_binding is None:
                raise ValueError("VM-HA activation requires its authoritative runtime binding")
            from ..agent.main import vm_ha_runtime_blockers

            blockers = vm_ha_runtime_blockers()
            if blockers:
                raise RuntimeError(f"VM-HA activation BLOCKED: {', '.join(blockers)}")
        if not ssh_target:
            print(f"[SSHPush] No SSH target for instance {inst_cfg.instance_index}; skipping")
            return

        paramiko = self._ensure_paramiko()
        gg = local_cfg.get("gateway_group") or {}
        vm_spec = gg.get("vm_spec") or {}
        username: str = vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu")
        key_path: str | None = vm_spec.get("ssh_private_key_path") or os.environ.get(
            "VPNGW_SSH_KEY"
        )
        key_file = Path(key_path).expanduser() if key_path else None

        print(f"[SSHPush] Connecting to {ssh_target} as {username} ...")
        client = paramiko.SSHClient()
        try:
            configure_paramiko_host_verification(client, paramiko)
            client.connect(
                hostname=ssh_target,
                username=username,
                key_filename=str(key_file) if key_file else None,
                look_for_keys=True,
                allow_agent=True,
                timeout=15,
            )
        except Exception as e:
            error_msg = str(e).lower()
            print(f"[SSHPush] SSH connect failed to {ssh_target}: {e}")

            identity_failure = _host_identity_failure(e, paramiko, ssh_target)
            if identity_failure is not None:
                raise identity_failure from e

            # Provide helpful guidance for common network issues
            if "timed out" in error_msg or "timeout" in error_msg:
                print("\n" + "=" * 80)
                print("⚠️  NETWORK CONNECTIVITY ISSUE DETECTED")
                print("=" * 80)
                print("The VM appears to be unreachable. This can happen if:")
                print("  1. The VM is still booting (cloud-init may be installing packages)")
                print("  2. Network configuration issues during VM initialization")
                print("  3. Firewall or security group blocking SSH access")
                print("\nRECOMMENDED ACTIONS:")
                print("  • Wait 2-3 minutes and try running 'apply' again")
                print("  • Check VM status in Nebius Console (serial logs can show boot issues)")
                print("  • If the issue persists, restart the VM from the console and retry")
                print("  • As a last resort, run: nebius-vpngw destroy -y && nebius-vpngw apply")
                print("=" * 80 + "\n")
            if required_remote:
                raise RuntimeError(
                    f"{'VM-HA activation' if staged_receipt is not None else 'required'} "
                    f"SSH connection failed for {ssh_target}"
                ) from e
            return

        restart_agent = False

        # Always deploy the latest agent package from local build
        print("[SSHPush] Deploying latest nebius-vpngw package...")
        wheel_path = self._build_wheel()
        if wheel_path and wheel_path.exists():
            wheel_version = None
            wheel_parts = wheel_path.name.split("-")
            if len(wheel_parts) >= 2:
                wheel_version = wheel_parts[1]
            try:
                with client.open_sftp() as sftp:
                    remote_wheel = f"/tmp/{wheel_path.name}"
                    sftp.put(str(wheel_path), remote_wheel)
                    print(f"[SSHPush] Uploaded {wheel_path.name}")

                # Install/upgrade the wheel with dependencies
                # Use --break-system-packages on Ubuntu 24.04+ which has PEP 668 restrictions
                # Use --ignore-installed to avoid conflicts with system-managed packages like typing_extensions
                # Use 'python3 -m pip' instead of 'pip3' for Ubuntu 24.04 compatibility
                install_cmd = f"sudo python3 -m pip install --upgrade --ignore-installed --break-system-packages {remote_wheel}"
                stdin, stdout, stderr = client.exec_command(install_cmd, get_pty=True, timeout=120)
                rc = stdout.channel.recv_exit_status()
                out = stdout.read().decode().strip()
                err = stderr.read().decode().strip()
                if rc == 0:
                    restart_agent = True
                    # Reinstall just our package to avoid stale version metadata, without touching deps
                    reinstall_cmd = (
                        f"sudo python3 -m pip install --upgrade --force-reinstall "
                        f"--no-deps --break-system-packages {remote_wheel}"
                    )
                    stdin_re, stdout_re, stderr_re = client.exec_command(
                        reinstall_cmd, get_pty=True, timeout=120
                    )
                    rc_re = stdout_re.channel.recv_exit_status()
                    re_out = stdout_re.read().decode().strip()
                    re_err = stderr_re.read().decode().strip()
                    if rc_re != 0:
                        if required_remote:
                            raise RuntimeError("VM-HA package reinstall verification failed")
                        print("[SSHPush] WARNING: Forced reinstall failed; continuing anyway")
                        if re_out:
                            print(
                                f"[SSHPush] stdout: {re_out[-500:]}"
                                if len(re_out) > 500
                                else f"[SSHPush] stdout: {re_out}"
                            )
                        if re_err:
                            print(
                                f"[SSHPush] stderr: {re_err[-500:]}"
                                if len(re_err) > 500
                                else f"[SSHPush] stderr: {re_err}"
                            )

                    # Verify package actually installed by importing it
                    verify_cmd = (
                        'python3 -c "import importlib.metadata as m, nebius_vpngw; '
                        "print('version=' + m.version('nebius-vpngw')); "
                        "print('path=' + nebius_vpngw.__file__)\""
                    )
                    stdin_check, stdout_check, stderr_check = client.exec_command(
                        verify_cmd, timeout=10
                    )
                    rc_check = stdout_check.channel.recv_exit_status()
                    verify_out = stdout_check.read().decode().strip()
                    verify_err = stderr_check.read().decode().strip()
                    if rc_check == 0 and verify_out:
                        lines = [line.strip() for line in verify_out.splitlines() if line.strip()]
                        version_line = next(
                            (line for line in lines if line.startswith("version=")), ""
                        )
                        path_line = next((line for line in lines if line.startswith("path=")), "")
                        if version_line:
                            installed_version = version_line.split("=", 1)[1]
                            installed_path = path_line.split("=", 1)[1] if path_line else None
                            print(
                                "[SSHPush] Package installed/upgraded successfully: "
                                f"nebius-vpngw {installed_version}"
                            )
                            if wheel_version and installed_version != wheel_version:
                                print(
                                    "[SSHPush] WARNING: Installed version does not match "
                                    f"wheel ({installed_version} != {wheel_version}). "
                                    "A system package may be shadowing the installed wheel."
                                )
                                if installed_path:
                                    print(f"[SSHPush] Installed package path: {installed_path}")
                        else:
                            print("[SSHPush] WARNING: Could not read installed package version")
                    else:
                        if required_remote:
                            raise RuntimeError("VM-HA installed package import check failed")
                        print(
                            "[SSHPush] WARNING: pip install succeeded but package import check failed"
                        )
                        if verify_err:
                            print(
                                f"[SSHPush] stderr: {verify_err[-500:]}"
                                if len(verify_err) > 500
                                else f"[SSHPush] stderr: {verify_err}"
                            )
                    # Install/refresh systemd unit - read from package systemd/ directory
                    import nebius_vpngw

                    systemd_dir = Path(nebius_vpngw.__file__).parent / "systemd"
                    service_unit_file = systemd_dir / "nebius-vpngw-agent.service"

                    if service_unit_file.exists():
                        service_unit = service_unit_file.read_text()
                    else:
                        # Fallback to embedded content if file not found
                        service_unit = """[Unit]
Description=Nebius VPNGW Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/bin/python3 -m nebius_vpngw.agent.main
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""

                    try:
                        with client.open_sftp() as sftp:
                            with sftp.file("/tmp/nebius-vpngw-agent.service", "w") as f:
                                f.write(service_unit)
                            print("[SSHPush] Staged systemd unit update")

                            # Deploy route fix service and timer
                            # Use installed package location (works both in dev and deployed)
                            import nebius_vpngw

                            systemd_dir = Path(nebius_vpngw.__file__).parent / "systemd"

                            fix_routes_service = systemd_dir / "nebius-vpngw-fix-routes.service"
                            fix_routes_timer = systemd_dir / "nebius-vpngw-fix-routes.timer"

                            if fix_routes_service.exists():
                                with sftp.file("/tmp/nebius-vpngw-fix-routes.service", "w") as f:
                                    f.write(fix_routes_service.read_text())
                                print("[SSHPush] Staged route fix service")

                            if fix_routes_timer.exists():
                                with sftp.file("/tmp/nebius-vpngw-fix-routes.timer", "w") as f:
                                    f.write(fix_routes_timer.read_text())
                                print("[SSHPush] Staged route fix timer")

                            # Deploy health monitoring service
                            health_monitor_service = (
                                systemd_dir / "nebius-vpngw-health-monitor.service"
                            )
                            if health_monitor_service.exists():
                                with sftp.file(
                                    "/tmp/nebius-vpngw-health-monitor.service", "w"
                                ) as f:
                                    f.write(health_monitor_service.read_text())
                                print("[SSHPush] Staged health monitoring service")

                            firewall_script = systemd_dir / "setup-vpngw-firewall.sh"
                            if firewall_script.exists():
                                with sftp.file("/tmp/setup-vpngw-firewall.sh", "w") as f:
                                    f.write(firewall_script.read_text())
                                print("[SSHPush] Staged firewall setup script")

                            esp4_preflight_script = systemd_dir / "nebius-vpngw-esp4-preflight.sh"
                            if esp4_preflight_script.exists():
                                with sftp.file("/tmp/nebius-vpngw-esp4-preflight.sh", "w") as f:
                                    f.write(esp4_preflight_script.read_text())
                                print("[SSHPush] Staged ESP4 preflight helper")

                            for asset_name in (
                                "nebius-vpngw-vm-ha-guard.service",
                                "nebius-vpngw-vm-ha.service",
                                "nebius-vpngw-vm-ha-ordering.conf",
                            ):
                                asset = systemd_dir / asset_name
                                if asset.exists():
                                    with sftp.file(f"/tmp/{asset_name}", "w") as f:
                                        f.write(asset.read_text())
                                    print(f"[SSHPush] Staged {asset_name}")
                    except Exception as e:
                        if required_remote:
                            raise RuntimeError("VM-HA systemd asset staging failed") from e
                        print(f"[SSHPush] Failed to stage systemd unit: {e}")
                else:
                    print(f"[SSHPush] Package installation failed (rc={rc})")
                    if out:
                        print(
                            f"[SSHPush] stdout: {out[-500:]}"
                            if len(out) > 500
                            else f"[SSHPush] stdout: {out}"
                        )
                    if err:
                        print(
                            f"[SSHPush] stderr: {err[-500:]}"
                            if len(err) > 500
                            else f"[SSHPush] stderr: {err}"
                        )
                    if required_remote:
                        raise RuntimeError("VM-HA package installation failed")
                    print("[SSHPush] WARNING: Continuing with config push, but agent may not work")
            except Exception as e:
                if required_remote:
                    raise RuntimeError("VM-HA package deployment failed") from e
                print(f"[SSHPush] Failed to deploy package: {e}")
                print("[SSHPush] WARNING: Continuing with config push, but agent may not work")
        else:
            if required_remote:
                raise RuntimeError("VM-HA activation requires a deployable agent wheel")
            print("[SSHPush] WARNING: Could not build wheel, skipping package deployment")

        # Upload to /tmp then move with sudo
        if staged_receipt is not None:
            assert runtime_binding is not None
            staged_binding = self._runtime_binding_for_credential_bundle(
                inst_cfg=inst_cfg,
                runtime_binding=runtime_binding,
                credential_digests=staged_receipt.credential_sha256,
            )
            rendered_config = self._render_vm_ha_config(inst_cfg, staged_binding)
            expected = self._vm_ha_receipt(
                inst_cfg, rendered_config, staged_receipt.credential_sha256
            )
            if staged_receipt != expected:
                client.close()
                raise ValueError("VM-HA activation receipt does not match the node manifest")
            tmp_path = f"/etc/nebius-vpngw/vm-ha-staged/{staged_receipt.generation_id}.yaml"
        else:
            tmp_path = f"/tmp/nebius-config-{inst_cfg.instance_index}.yaml"
            try:
                with client.open_sftp() as sftp, sftp.file(tmp_path, "w") as f:
                    f.write(inst_cfg.config_yaml)
                print(f"[SSHPush] Uploaded temp config to {tmp_path}")
            except Exception as e:
                print(f"[SSHPush] SFTP upload failed: {e}")
                client.close()
                return

        agent_cmd = (
            "sudo systemctl restart nebius-vpngw-agent"
            if restart_agent
            else "sudo systemctl is-active --quiet nebius-vpngw-agent && sudo systemctl reload nebius-vpngw-agent || sudo systemctl start nebius-vpngw-agent"
        )
        config_install_cmd = (
            f"sudo install -o root -g root -m 0600 {tmp_path} /etc/nebius-vpngw/config-resolved.yaml"
            if staged_receipt is not None
            else f"sudo mv {tmp_path} /etc/nebius-vpngw/config-resolved.yaml"
        )
        config_mode = "0600" if staged_receipt is not None else "0644"

        # Move into place and trigger reload
        cmds = [
            "sudo mkdir -p /etc/nebius-vpngw",
            *(
                [self._vm_ha_staged_verify_command(staged_receipt)]
                if staged_receipt is not None
                else []
            ),
            config_install_cmd,
            "sudo chown root:root /etc/nebius-vpngw/config-resolved.yaml",
            f"sudo chmod {config_mode} /etc/nebius-vpngw/config-resolved.yaml",
            # Install route fix service and timer if staged
            "if [ -f /tmp/nebius-vpngw-fix-routes.service ]; then sudo mv /tmp/nebius-vpngw-fix-routes.service /etc/systemd/system/nebius-vpngw-fix-routes.service; fi",
            "if [ -f /tmp/nebius-vpngw-fix-routes.timer ]; then sudo mv /tmp/nebius-vpngw-fix-routes.timer /etc/systemd/system/nebius-vpngw-fix-routes.timer; fi",
            "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.service ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.service; fi",
            "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.timer ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-fix-routes.timer; fi",
            # Install health monitoring service if staged
            "if [ -f /tmp/nebius-vpngw-health-monitor.service ]; then sudo mv /tmp/nebius-vpngw-health-monitor.service /etc/systemd/system/nebius-vpngw-health-monitor.service; fi",
            "if [ -f /etc/systemd/system/nebius-vpngw-health-monitor.service ]; then sudo chmod 0644 /etc/systemd/system/nebius-vpngw-health-monitor.service; fi",
            "if [ -f /tmp/setup-vpngw-firewall.sh ]; then sudo mv /tmp/setup-vpngw-firewall.sh /usr/local/bin/setup-vpngw-firewall.sh; fi",
            "if [ -f /usr/local/bin/setup-vpngw-firewall.sh ]; then sudo chmod 0755 /usr/local/bin/setup-vpngw-firewall.sh; fi",
            "if [ -f /tmp/nebius-vpngw-esp4-preflight.sh ]; then sudo mv /tmp/nebius-vpngw-esp4-preflight.sh /usr/local/bin/nebius-vpngw-esp4-preflight.sh; fi",
            "if [ -f /usr/local/bin/nebius-vpngw-esp4-preflight.sh ]; then sudo chmod 0755 /usr/local/bin/nebius-vpngw-esp4-preflight.sh; fi",
            "if [ -f /tmp/nebius-vpngw-vm-ha-guard.service ]; then sudo mv /tmp/nebius-vpngw-vm-ha-guard.service /etc/systemd/system/nebius-vpngw-vm-ha-guard.service; fi",
            "if [ -f /tmp/nebius-vpngw-vm-ha.service ]; then sudo mv /tmp/nebius-vpngw-vm-ha.service /etc/systemd/system/nebius-vpngw-vm-ha.service; fi",
            "if [ -f /tmp/nebius-vpngw-vm-ha-ordering.conf ]; then sudo install -d -m 0755 /etc/systemd/system/strongswan-starter.service.d /etc/systemd/system/strongswan.service.d /etc/systemd/system/frr.service.d /etc/systemd/system/nebius-vpngw-agent.service.d; sudo install -m 0644 /tmp/nebius-vpngw-vm-ha-ordering.conf /etc/systemd/system/strongswan-starter.service.d/30-vm-ha.conf; sudo install -m 0644 /tmp/nebius-vpngw-vm-ha-ordering.conf /etc/systemd/system/strongswan.service.d/30-vm-ha.conf; sudo install -m 0644 /tmp/nebius-vpngw-vm-ha-ordering.conf /etc/systemd/system/frr.service.d/30-vm-ha.conf; sudo install -m 0644 /tmp/nebius-vpngw-vm-ha-ordering.conf /etc/systemd/system/nebius-vpngw-agent.service.d/30-vm-ha.conf; fi",
            # Refresh systemd unit if staged
            "if [ -f /tmp/nebius-vpngw-agent.service ]; then sudo mv /tmp/nebius-vpngw-agent.service /etc/systemd/system/nebius-vpngw-agent.service; fi",
            "sudo chmod 0644 /etc/systemd/system/nebius-vpngw-agent.service",
            "sudo systemctl daemon-reload",
            *(
                [
                    "sudo install -o root -g root -m 0600 /dev/null /etc/nebius-vpngw/vm-ha-enabled",
                    "sudo systemctl enable nebius-vpngw-vm-ha-guard.service nebius-vpngw-vm-ha.service",
                    "sudo systemctl restart nebius-vpngw-vm-ha-guard.service",
                    "sudo systemctl restart nebius-vpngw-vm-ha.service",
                ]
                if staged_receipt is not None
                else []
            ),
            # Enable and start route fix timer (only if service file exists)
            "if [ -f /etc/systemd/system/nebius-vpngw-fix-routes.timer ]; then sudo systemctl enable --now nebius-vpngw-fix-routes.timer; fi",
            # Enable and start health monitoring service (only if service file exists)
            "if [ -f /etc/systemd/system/nebius-vpngw-health-monitor.service ]; then sudo systemctl enable --now nebius-vpngw-health-monitor.service; fi",
            # Run route fix immediately before starting agent (non-fatal if unavailable)
            'if python3 -c "import nebius_vpngw" >/dev/null 2>&1; then sudo /usr/bin/python3 -m nebius_vpngw.agent.fix_routes > /var/log/vpngw-fix-routes.log 2>&1 || true; fi',
            # Apply firewall rules (including MSS clamp and ICMP allowances)
            "if [ -f /usr/local/bin/setup-vpngw-firewall.sh ]; then sudo /usr/local/bin/setup-vpngw-firewall.sh > /var/log/vpngw-firewall-setup.log 2>&1 || true; fi",
            # Start or reload agent
            agent_cmd,
            *(
                [
                    "sudo systemctl is-active --quiet nebius-vpngw-vm-ha-guard.service",
                    "sudo systemctl is-active --quiet nebius-vpngw-vm-ha.service",
                    "sudo /usr/bin/python3 -m nebius_vpngw.agent.main --vm-ha-ready",
                ]
                if staged_receipt is not None
                else []
            ),
        ]
        had_failures = False
        for cmd in cmds:
            try:
                stdin, stdout, stderr = client.exec_command(cmd, get_pty=True, timeout=20)
                rc = stdout.channel.recv_exit_status()
                if rc != 0:
                    err = stderr.read().decode().strip()
                    print(f"[SSHPush] Command failed (rc={rc}): {cmd}\n{err}")
                    if required_remote:
                        raise RuntimeError(f"VM-HA activation command failed: {cmd}: {err or rc}")
                    had_failures = True
                else:
                    # Suppress noisy per-command logs on success
                    pass
            except Exception as e:
                if required_remote:
                    raise RuntimeError(f"VM-HA activation command failed: {cmd}") from e
                print(f"[SSHPush] Exec failed for: {cmd} -> {e}")
                had_failures = True

        if not had_failures:
            if restart_agent:
                print("[SSHPush] Applied config, systemd unit, and restarted agent")
            else:
                print("[SSHPush] Applied config, systemd unit, and reloaded agent")

        # Verify routing table health after route fix ran
        try:

            def _check_routing_health() -> tuple[str, str]:
                stdin, stdout, stderr = client.exec_command(
                    "ip rule list | grep -q 'lookup 220' && echo 'EXISTS' || echo 'OK'",
                    timeout=10,
                )
                table220 = stdout.read().decode().strip()

                stdin, stdout, stderr = client.exec_command(
                    "ip route show 169.254.0.0/16 2>/dev/null | grep -q eth0 && echo 'EXISTS' || echo 'OK'",
                    timeout=10,
                )
                apipa = stdout.read().decode().strip()
                return table220, apipa

            table220_status, apipa_status = _check_routing_health()
            if table220_status != "OK" or apipa_status != "OK":
                import time

                time.sleep(5)
                table220_status, apipa_status = _check_routing_health()

            if table220_status == "OK" and apipa_status == "OK":
                print("[SSHPush] ✓ Routing table clean (Table 220 and broad APIPA removed)")
            else:
                if table220_status == "EXISTS":
                    print(
                        "[SSHPush] ⚠ Table 220 policy route still exists (may impact VPN routing)"
                    )
                if apipa_status == "EXISTS":
                    print(
                        "[SSHPush] ⚠ Broad APIPA route (169.254.0.0/16) still exists (should be removed for XFRM tunnels)"
                    )
        except Exception:
            # Non-critical check, don't fail deployment
            pass

        # Ensure FRR is installed (cloud-init can fail if repo/version is unavailable)
        try:
            stdin, stdout, stderr = client.exec_command(
                "dpkg -l frr 2>/dev/null | grep -q '^ii'", timeout=10
            )
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                print("[SSHPush] ⚠ FRR package missing; attempting install...")
                install_cmd = (
                    "sudo bash -lc '"
                    "set -e;"
                    'if ! dpkg -l frr 2>/dev/null | grep -q "^ii"; then '
                    "command -v curl >/dev/null 2>&1 || (apt-get update && apt-get install -y curl); "
                    "if [ ! -f /etc/apt/sources.list.d/frr.list ]; then "
                    "curl -s https://deb.frrouting.org/frr/keys.asc | tee /usr/share/keyrings/frrouting.asc > /dev/null; "
                    "UBUNTU_CODENAME=$(lsb_release -cs); "
                    'echo "deb [signed-by=/usr/share/keyrings/frrouting.asc] https://deb.frrouting.org/frr $UBUNTU_CODENAME frr-stable" > /etc/apt/sources.list.d/frr.list; '
                    "fi; "
                    "apt-get update; "
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y frr frr-pythontools; "
                    "fi'"
                )
                stdin, stdout, stderr = client.exec_command(install_cmd, timeout=300, get_pty=True)
                install_rc = stdout.channel.recv_exit_status()
                if install_rc == 0:
                    print("[SSHPush] ✓ FRR installed")
                else:
                    err = stderr.read().decode().strip()
                    print(f"[SSHPush] ✗ FRR install failed: {err}")
        except Exception as e:
            print(f"[SSHPush] ⚠ FRR install check failed: {e}")

        # Ensure swanctl is installed (required for if_id_in/out and VICI config loading)
        try:
            stdin, stdout, stderr = client.exec_command(
                "dpkg -l strongswan-swanctl 2>/dev/null | grep -q '^ii'", timeout=10
            )
            rc = stdout.channel.recv_exit_status()
            if rc != 0:
                print("[SSHPush] ⚠ strongswan-swanctl missing; attempting install...")
                install_cmd = (
                    "sudo bash -lc '"
                    "set -e;"
                    "apt-get update; "
                    "DEBIAN_FRONTEND=noninteractive apt-get install -y strongswan-swanctl'"
                )
                stdin, stdout, stderr = client.exec_command(install_cmd, timeout=300, get_pty=True)
                install_rc = stdout.channel.recv_exit_status()
                if install_rc == 0:
                    print("[SSHPush] ✓ strongswan-swanctl installed")
                else:
                    err = stderr.read().decode().strip()
                    print(f"[SSHPush] ✗ strongswan-swanctl install failed: {err}")
        except Exception as e:
            print(f"[SSHPush] ⚠ strongswan-swanctl install check failed: {e}")

        # Verify service is actually running
        try:
            print("[SSHPush] Verifying service status...")
            stdin, stdout, stderr = client.exec_command(
                "sudo systemctl is-active nebius-vpngw-agent", timeout=10
            )
            rc = stdout.channel.recv_exit_status()
            status = stdout.read().decode().strip()

            if rc == 0 and status == "active":
                print("[SSHPush] ✓ nebius-vpngw-agent is running")
            else:
                print(f"[SSHPush] ✗ nebius-vpngw-agent is NOT running (status: {status})")
                # Get detailed status for troubleshooting
                stdin, stdout, stderr = client.exec_command(
                    "sudo systemctl status nebius-vpngw-agent --no-pager -l", timeout=10
                )
                detailed_status = stdout.read().decode()
                print(f"[SSHPush] Service status:\n{detailed_status}")

            # Verify strongSwan (account for different service names) and FRR
            strongswan_checks = [
                ("strongswan-starter", "sudo systemctl is-active strongswan-starter"),
                ("strongswan-swanctl", "sudo systemctl is-active strongswan-swanctl"),
                (
                    "charon",
                    "pgrep -x charon >/dev/null && echo active || echo inactive",
                ),
            ]
            strongswan_statuses = []
            strongswan_ok = False
            for name, cmd in strongswan_checks:
                stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                rc = stdout.channel.recv_exit_status()
                svc_status = stdout.read().decode().strip()
                strongswan_statuses.append(f"{name}={svc_status or rc}")
                if rc == 0 and svc_status == "active":
                    print(f"[SSHPush] ✓ strongSwan is running ({name})")
                    strongswan_ok = True
                    break
            if not strongswan_ok:
                joined = ", ".join(strongswan_statuses)
                print(f"[SSHPush] ✗ strongSwan appears inactive (checked: {joined})")

            # FRR check - wait up to 15 seconds for FRR to start
            frr_active = False
            svc_status = "unknown"
            for attempt in range(3):  # 3 attempts, 5 seconds apart
                stdin, stdout, stderr = client.exec_command(
                    "sudo systemctl is-active frr", timeout=10
                )
                rc = stdout.channel.recv_exit_status()
                svc_status = stdout.read().decode().strip()
                if rc == 0 and svc_status == "active":
                    print("[SSHPush] ✓ frr is running")
                    frr_active = True
                    break
                elif attempt < 2:  # Don't sleep on last attempt
                    import time

                    time.sleep(5)

            if not frr_active:
                print(f"[SSHPush] ✗ frr is NOT running (status: {svc_status})")

            # Check BGP session status via FRR instead of TCP port probing
            # (TCP probes fail when BGP sessions are already established)
            try:
                defaults_mode = (
                    (local_cfg.get("defaults", {}) or {}).get("routing", {}) or {}
                ).get("mode", "bgp")

                # Collect BGP peers for this instance (active and passive tunnels)
                bgp_peers = []
                for conn in local_cfg.get("connections") or []:
                    routing_mode = conn.get("routing_mode") or defaults_mode
                    if routing_mode != "bgp":
                        continue
                    for tun in conn.get("tunnels") or []:
                        if int(tun.get("gateway_instance_index", 0)) != inst_cfg.instance_index:
                            continue
                        ha_role = tun.get("ha_role", "active")
                        if ha_role == "disable":
                            continue  # Skip only explicitly disabled tunnels
                        r_ip = tun.get("inner_remote_ip")
                        if r_ip:
                            bgp_peers.append(r_ip)

                if bgp_peers:
                    # Wait for IPsec tunnels to establish before testing connectivity
                    import json
                    import time

                    print("[SSHPush] Waiting for IPsec tunnels to establish...")
                    time.sleep(10)

                    print(f"[SSHPush] Verifying tunnel connectivity to {len(bgp_peers)} peer(s)...")

                    # Step 1: Test ping connectivity to BGP peers
                    all_peers_reachable = True
                    for peer_ip in bgp_peers:
                        cmd = f"ping -c 2 -W 2 {peer_ip} >/dev/null 2>&1 && echo OK || echo FAIL"
                        stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                        result = stdout.read().decode().strip()
                        if result == "OK":
                            print(f"[SSHPush] ✓ Tunnel connectivity OK: {peer_ip} is reachable")
                        else:
                            print(
                                f"[SSHPush] ✗ Tunnel connectivity FAILED: {peer_ip} is NOT reachable"
                            )
                            all_peers_reachable = False

                    if not all_peers_reachable:
                        print(
                            "[SSHPush] WARNING: Some peers are not reachable. BGP may not establish."
                        )

                    # Step 2: Wait for BGP sessions to establish (up to 60 seconds)
                    print("[SSHPush] Waiting for BGP sessions to establish...")
                    max_wait_time = 60
                    start_time = time.time()
                    all_established = False
                    last_states: dict[str, str] = {}

                    while (time.time() - start_time) < max_wait_time:
                        cmd = "sudo vtysh -c 'show bgp summary json' 2>/dev/null || echo '{}'"
                        stdin, stdout, stderr = client.exec_command(cmd, timeout=10)
                        output = stdout.read().decode().strip()

                        try:
                            bgp_summary = json.loads(output) if output != "{}" else {}
                            ipv4_peers = bgp_summary.get("ipv4Unicast", {}).get("peers", {})

                            established_count = 0
                            current_states: dict[str, str] = {}

                            for peer_ip in bgp_peers:
                                peer_info = ipv4_peers.get(peer_ip, {})
                                state = peer_info.get("state", "Unknown")
                                current_states[peer_ip] = state

                                if state == "Established":
                                    established_count += 1

                            # Print state changes
                            for peer_ip, state in current_states.items():
                                if peer_ip not in last_states or last_states[peer_ip] != state:
                                    elapsed = int(time.time() - start_time)
                                    if state == "Established":
                                        print(
                                            f"[SSHPush] ✓ BGP session with {peer_ip} is Established (after {elapsed}s)"
                                        )
                                    elif state != "Unknown":
                                        print(
                                            f"[SSHPush]   BGP session with {peer_ip}: {state} (waiting...)"
                                        )

                            last_states = current_states

                            if established_count == len(bgp_peers):
                                all_established = True
                                break

                            # Wait 3 seconds before checking again
                            time.sleep(3)

                        except (json.JSONDecodeError, Exception):
                            # FRR might not be fully started yet
                            time.sleep(3)
                            continue

                    # Final status report
                    if all_established:
                        elapsed = int(time.time() - start_time)
                        print(
                            f"[SSHPush] ✓ All BGP sessions established successfully (took {elapsed}s)"
                        )
                    else:
                        elapsed = int(time.time() - start_time)
                        print(f"[SSHPush] ⚠ BGP sessions not yet established after {elapsed}s")
                        print(
                            f"[SSHPush]   Current states: {', '.join([f'{ip}={state}' for ip, state in last_states.items()])}"
                        )
                        print(
                            "[SSHPush]   BGP sessions may take additional time to establish. Check with: nebius-vpngw status"
                        )
            except Exception:
                # BGP check is informational only, don't fail deployment
                pass
        except Exception as e:
            print(f"[SSHPush] Failed to verify service status: {e}")

        try:
            client.close()
        except Exception:
            pass  # Ignore Paramiko cleanup warnings
