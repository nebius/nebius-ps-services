"""HA package approval, independent from ordinary and HA runtime admission."""

from __future__ import annotations

import tempfile
import zipfile
from dataclasses import dataclass, field
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from packaging.utils import parse_wheel_filename

from ..config_loader import InstanceResolvedConfig
from ..ordinary_operations import digest
from . import ordinary_apply


class VMHAAgentPackageError(RuntimeError):
    """Package rejection composed only of fixed text and closed failure categories.

    Never construct this exception from raw SSH output, paths, or another
    exception's text: its message is safe for the operator-facing CLI.
    """


# Deliberately owned here, rather than weakening the ordinary approval contract.
PACKAGE_FIELDS = (
    "boot_id",
    "config_sha256",
    "ha_config_digest",
    "files",
    "assets",
    "asset_modes",
    "dependencies",
    "requirements",
    "markers",
    "wheel_tags",
    "python",
    "machine",
    "package_version",
)


def package_predecessor(observed: dict[str, Any]) -> dict[str, Any]:
    return {key: observed[key] for key in PACKAGE_FIELDS}


def artifact(wheel: Path, config: str) -> dict[str, Any]:
    from .ssh_push import _VM_HA_SERVICE_ASSET_DESTINATIONS

    manifest = ordinary_apply.artifact(wheel, config)
    manifest["ordinary_assets"] = manifest["assets"]
    with zipfile.ZipFile(wheel) as archive:
        codec = "agent/vm_ha_checkpoint.py"
        if (
            archive.read("nebius_vpngw/" + codec)
            != (Path(__file__).parents[1] / codec).read_bytes()
        ):
            raise VMHAAgentPackageError("HA wheel must contain this CLI's current checkpoint codec")
        destinations = (
            *_VM_HA_SERVICE_ASSET_DESTINATIONS,
            (
                "nebius-vpngw-agent-ordering.conf",
                "/etc/systemd/system/nebius-vpngw-agent.service.d/override.conf",
                0o644,
            ),
        )
        manifest["assets"] = {
            path: {
                "member": f"nebius_vpngw/systemd/{name}",
                "sha256": ordinary_apply.sha(archive.read(f"nebius_vpngw/systemd/{name}")),
                "mode": mode,
            }
            for name, path, mode in destinations
        }
        ufw = ordinary_apply.UFW_ORDERING_PATH
        manifest["assets"][ufw]["predecessors"] = manifest["ordinary_assets"][ufw]["predecessors"]
    return manifest


@dataclass
class VMHAPackagePlan:
    target: str
    instance: InstanceResolvedConfig
    wheel: Path
    manifest: dict[str, Any]
    observation: dict[str, Any]
    dependency_paths: list[Path]
    effects: list[str]
    target_identity: str
    temporary: Any = field(repr=False)

    @property
    def digest(self) -> str:
        return digest(self.envelope)

    @property
    def envelope(self) -> dict[str, Any]:
        return {
            "target": self.target_identity,
            "host": self.instance.hostname,
            "transport": self.target,
            "predecessor": self.observation,
            "desired": self.manifest,
            "effects": self.effects,
        }

    def close(self) -> None:
        self.temporary.cleanup()


def verify_prepared(
    ssh: Any,
    plan: VMHAPackagePlan,
    local: dict,
    *,
    checkpoint_conversion: dict[str, str] | None = None,
    convert: bool = True,
) -> None:
    """Prove the approved installed package without replaying its old predecessor."""

    current = ordinary_apply.remote(
        ssh,
        plan.target,
        plan.instance,
        local,
        {"action": "inspect-package", "manifest": plan.manifest},
    )["observation"]
    manifest = plan.manifest
    if not (
        current["boot_id"] == plan.observation["boot_id"]
        and current["config_sha256"] == plan.observation["config_sha256"]
        and current["ha_config_digest"] == plan.observation["ha_config_digest"]
        and current["files"] == manifest["files"]
        and current["package_version"] == manifest["version"]
        and current["dependencies"] == manifest["expected_dependencies"]
        and current["requirements"] == manifest["expected_requirements"]
        and current["assets"] == {path: item["sha256"] for path, item in manifest["assets"].items()}
        and current["asset_modes"]
        == {path: [item["mode"], 0, 0] for path, item in manifest["assets"].items()}
    ):
        raise VMHAAgentPackageError("VM-HA recovered package differs from approved postcondition")
    from .checkpoint_conversion import guard_package

    guard_package(
        ssh,
        plan.target,
        plan.instance,
        local,
        artifact=manifest["wheel_sha256"],
        boot_id=plan.observation["boot_id"],
        expected=checkpoint_conversion if convert else None,
        publish=convert,
    )


def inspect_plan(
    ssh: Any,
    target: str,
    instance: InstanceResolvedConfig,
    local: dict,
    *,
    target_identity: str,
    wheel: Path | None = None,
) -> VMHAPackagePlan:
    if wheel is None:
        wheel = ssh._build_wheel(allow_installed_fallback=False)
    if wheel is None:
        raise RuntimeError("HA deployment requires a deployable agent wheel")
    manifest = artifact(wheel, instance.config_yaml)
    observed = package_predecessor(
        ordinary_apply.remote(
            ssh, target, instance, local, {"action": "inspect-package", "manifest": manifest}
        )["observation"]
    )
    for path, item in manifest["assets"].items():
        if "predecessors" in item and observed["assets"].get(path) not in item["predecessors"]:
            raise VMHAAgentPackageError(
                "Product service override has unknown contents; no gateway changes are admitted"
            )
    temporary = tempfile.TemporaryDirectory(prefix="vpngw-ha-dependency-plan-")
    try:
        dependencies = ordinary_apply.resolve_dependencies(manifest, observed, Path(temporary.name))
        manifest["dependency_wheels"] = {
            path.name: ordinary_apply.sha(path.read_bytes()) for path in dependencies
        }
        expected = dict(observed["dependencies"])
        expected["nebius-vpngw"] = manifest["version"]
        for path in dependencies:
            name, version, _build, _tags = parse_wheel_filename(path.name)
            expected[str(name)] = str(version)
        manifest["expected_dependencies"] = expected
        expected_requirements = dict(observed["requirements"])
        expected_requirements["nebius-vpngw"] = manifest["requirements"]
        for path in dependencies:
            name, _version, _build, _tags = parse_wheel_filename(path.name)
            with zipfile.ZipFile(path) as archive:
                metadata = next(
                    item for item in archive.namelist() if item.endswith(".dist-info/METADATA")
                )
                expected_requirements[str(name)] = (
                    BytesParser().parsebytes(archive.read(metadata)).get_all("Requires-Dist") or []
                )
        manifest["expected_requirements"] = expected_requirements
        effects = []
        if (
            observed["files"] != manifest["files"]
            or observed["package_version"] != manifest["version"]
        ):
            effects.append("install exact HA agent wheel")
        if dependencies:
            effects.append("install selected required dependencies")
        if observed["assets"] != {
            path: item["sha256"] for path, item in manifest["assets"].items()
        } or observed["asset_modes"] != {
            path: [item["mode"], 0, 0] for path, item in manifest["assets"].items()
        }:
            effects.append("publish exact HA assets at canonical activation")
        return VMHAPackagePlan(
            target,
            instance,
            wheel,
            manifest,
            observed,
            dependencies,
            effects,
            target_identity,
            temporary,
        )
    except Exception:
        temporary.cleanup()
        raise
