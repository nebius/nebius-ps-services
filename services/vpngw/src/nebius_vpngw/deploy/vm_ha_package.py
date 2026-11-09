"""HA package approval, independent from ordinary and HA runtime admission."""

from __future__ import annotations

import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from packaging.utils import parse_wheel_filename
from paramiko import AuthenticationException, SSHException  # type: ignore[import-untyped]

from ..config_loader import InstanceResolvedConfig
from ..ordinary_operations import digest
from . import ordinary_apply
from .apply_diagnostics import DiagnosticFailure, Failure, first_failure


class VMHAAgentPackageError(DiagnosticFailure):
    """Package rejection composed only of fixed text and closed failure categories.

    Never construct this exception from raw SSH output, paths, or another
    exception's text: its message is safe for the operator-facing CLI.
    """


@contextmanager
def package_step(step: str) -> Iterator[None]:
    """Preserve the first package boundary; never project arbitrary remote text."""
    if step not in {
        "package-admission",
        "inspect-predecessor",
        "checkpoint-admission",
        "stage-payload",
        "guarded-install",
        "checkpoint-publication",
        "handoff-package",
    }:
        raise ValueError("invalid package diagnostic step")
    try:
        yield
    except Exception as error:
        if first_failure(error) is not None:
            raise
        cause = next(
            (
                reason
                for kind, reason in (
                    (TimeoutError, "timeout"),
                    (PermissionError, "permission"),
                    (AuthenticationException, "authentication"),
                    (SSHException, "ssh-transport"),
                    (EOFError, "connection-closed"),
                    (OSError, "io"),
                    (ValueError, "invalid-data"),
                    (RuntimeError, "runtime"),
                )
                if isinstance(error, kind)
            ),
            "unclassified",
        )
        detail = f"VM-HA package preparation failed at {step} ({cause})"
        if isinstance(error, VMHAAgentPackageError):
            detail += f": {error}"
        failure = VMHAAgentPackageError(detail)
        failure.first = Failure("prepare-packages", step + "-failed", ("cause-" + cause,))
        raise failure from error


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
    "runtime_plan",
)


def package_predecessor(observed: dict[str, Any]) -> dict[str, Any]:
    return {key: observed[key] for key in PACKAGE_FIELDS}


def artifact(wheel: Path, config: str) -> dict[str, Any]:
    from .ssh_push import _VM_HA_SERVICE_ASSET_DESTINATIONS

    manifest = ordinary_apply.artifact(wheel, config)
    manifest["ordinary_assets"] = manifest["assets"]
    with zipfile.ZipFile(wheel) as archive:
        from ..handoff_bootstrap import SOURCES

        for _module, relative in SOURCES:
            if (
                archive.read("nebius_vpngw/" + relative)
                != (Path(__file__).parents[1] / relative).read_bytes()
            ):
                raise VMHAAgentPackageError("HA wheel must contain this CLI's exact package owner")
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
        agent = ordinary_apply.AGENT_ORDERING_PATH
        manifest["assets"][agent]["predecessors"] = manifest["ordinary_assets"][agent][
            "predecessors"
        ]
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
    uploaded_payload_identity: str | None = field(default=None, repr=False)
    reservation: dict | None = field(default=None, repr=False)

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
    allow_new_boot: bool = False,
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
        (allow_new_boot or current["boot_id"] == plan.observation["boot_id"])
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
        boot_id=current["boot_id"],
        # Read-only verification still needs the exact authority for an already
        # converted checkpoint whose activation receipt remains pending.
        expected=checkpoint_conversion,
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
    resume_parent: str | None = None,
    resume_manifest: dict | None = None,
    reservation: dict | None = None,
) -> VMHAPackagePlan:
    if wheel is None:
        wheel = ssh._build_wheel(allow_installed_fallback=False)
    if wheel is None:
        raise RuntimeError("HA deployment requires a deployable agent wheel")
    manifest = artifact(wheel, instance.config_yaml)
    if resume_manifest is not None:
        if (resume_parent is None and reservation is None) or any(
            resume_manifest[key] != manifest[key]
            for key in ("wheel_sha256", "config_sha256", "files", "assets")
        ):
            raise VMHAAgentPackageError("Interrupted HA package authority changed")
        manifest = dict(resume_manifest)
    if resume_parent is not None:
        manifest["ha_parent"] = resume_parent
    if reservation is not None:
        from .ordinary_handoff import inspect_reservation

        envelope = reservation["request"]["package"]
        if (
            envelope["target"] != target_identity
            or envelope["transport"] != target
            or envelope["host"] != instance.hostname
            or any(
                envelope["desired"][key] != manifest[key]
                for key in ("wheel_sha256", "config_sha256", "files", "assets")
            )
            or envelope["desired"].get("ha_parent") != resume_parent
        ):
            raise VMHAAgentPackageError("Saved package reservation differs from approved target")
        observed = inspect_reservation(ssh, target, instance, local, reservation)
        if observed["state"] == "BLOCKED":
            raise VMHAAgentPackageError("Saved package owner authority is blocked")
        if observed["state"] != "NOT_SUBMITTED":
            return VMHAPackagePlan(
                target,
                instance,
                wheel,
                dict(envelope["desired"]),
                dict(envelope["predecessor"]),
                [],
                list(envelope["effects"]),
                target_identity,
                tempfile.TemporaryDirectory(prefix="vpngw-ha-dependency-plan-"),
                reservation=reservation,
            )
    full_observation = ordinary_apply.remote(
        ssh, target, instance, local, {"action": "inspect-package", "manifest": manifest}
    )["observation"]
    observed = package_predecessor(full_observation)
    if resume_manifest is not None and manifest["runtime_plan"] != observed["runtime_plan"]:
        raise VMHAAgentPackageError("Interrupted HA package authority changed native closure")
    manifest["runtime_plan"] = observed["runtime_plan"]
    pending = full_observation.get("runtime_pending")
    if pending is not None:
        saved = pending["manifest"]
        if resume_manifest is not None:
            from ..apply_approval import manifest_identity

            if saved.get("ha_parent") != resume_parent or manifest_identity(
                saved
            ) != manifest_identity(manifest):
                raise VMHAAgentPackageError("Interrupted HA package authority changed")
        if any(
            saved[key] != manifest[key]
            for key in ("wheel_sha256", "config_sha256", "files", "assets")
        ):
            raise VMHAAgentPackageError(
                "Interrupted apply requires the same approved artifact and configuration"
            )
        manifest = saved
    ordinary_apply.verify_asset_predecessors(manifest, observed, error_type=VMHAAgentPackageError)
    temporary = tempfile.TemporaryDirectory(prefix="vpngw-ha-dependency-plan-")
    try:
        dependencies = []
        if pending is None and resume_manifest is None:
            dependencies = ordinary_apply.resolve_dependencies(
                manifest, observed, Path(temporary.name)
            )
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
                        BytesParser().parsebytes(archive.read(metadata)).get_all("Requires-Dist")
                        or []
                    )
            manifest["expected_requirements"] = expected_requirements
        elif pending is None:
            dependencies = ordinary_apply.restore_dependencies(
                manifest, observed, Path(temporary.name)
            )
        effects = ["stage and verify approved daemon package and startup policy"]
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


def stage(ssh: Any, plan: VMHAPackagePlan, local: dict, *, artifact: Any) -> dict:
    """Durable, non-disruptive staging; caller completes the all-member barrier."""
    import base64

    with artifact.open_verified() as stream:
        wheel = stream.read()
    if ordinary_apply.sha(wheel) != plan.manifest["wheel_sha256"]:
        raise VMHAAgentPackageError("HA staged artifact differs from approved plan")
    payload_identity = digest(
        {
            "target": plan.target,
            "member": plan.target_identity,
            "hostname": plan.instance.hostname,
            "boot": plan.observation.get("boot_id"),
            "runtime_plan": plan.manifest["runtime_plan"],
            "wheel_name": plan.manifest.get("wheel_name"),
            "wheel": plan.manifest["wheel_sha256"],
            "dependencies": plan.manifest["dependency_wheels"],
        }
    )
    request = {"action": "stage-package", "manifest": plan.manifest}
    if plan.uploaded_payload_identity != payload_identity and plan.reservation is None:
        request.update(
            wheel=base64.b64encode(wheel).decode(),
            dependency_wheels={
                p.name: base64.b64encode(p.read_bytes()).decode() for p in plan.dependency_paths
            },
        )
    # This is only an upload hint. The canonical guest staging owner still
    # rehashes every archive/wheel and publishes the full current manifest,
    # including a lifecycle parent bound after initial non-disruptive staging.
    result = ordinary_apply.remote(
        ssh,
        plan.target,
        plan.instance,
        local,
        request,
    )
    expected = {
        "manifest": digest(plan.manifest),
        "runtime_plan": digest(plan.manifest["runtime_plan"]),
    }
    if result != {"status": "staged", "receipt": expected}:
        raise VMHAAgentPackageError("HA durable package staging receipt differs")
    plan.uploaded_payload_identity = payload_identity
    return expected
