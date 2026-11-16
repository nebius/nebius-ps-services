"""Ordinary apply planning and receipt validation over the existing SSH trust owner."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from contextlib import ExitStack
from dataclasses import dataclass, field
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

from .. import dependency_scope, ingress_policy, ordinary_bootstrap, ordinary_routes
from ..config_loader import InstanceResolvedConfig
from ..deployment_assets import ORDINARY as ASSETS
from ..replay_window import (
    REPLAY_WINDOW_CAPABILITY,
    require_replay_window_capability,
    wheel_supports_replay_window,
)
from . import ingress_ssh
from .recovery import GUEST_FAILURES, GuestFailure, WriterBusy
from .ssh_policy import configure_paramiko_host_verification

UFW_ORDERING_PATH = "/etc/systemd/system/ufw.service.d/override.conf"
PREVIOUS_UFW_ORDERING = (
    b"[Unit]\nAfter=network-online.target cloud-init.service\nWants=network-online.target\n"
)
AGENT_ORDERING_PATH = "/etc/systemd/system/nebius-vpngw-agent.service.d/override.conf"
# Exact product-owned bytes from released 0.6.0-0.6.3. Both are replaced by the
# canonical charon-systemd ordering; administrator overrides remain inadmissible.
PREVIOUS_AGENT_ORDERINGS = (
    b"[Unit]\nAfter=strongswan-starter.service frr.service\n",
    b"[Unit]\nAfter=strongswan-starter.service frr.service\n"
    b"Wants=strongswan-starter.service frr.service\n",
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: Any) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def verify_asset_predecessors(
    manifest: dict, observed: dict, *, error_type: type[RuntimeError] = RuntimeError
) -> None:
    """Admit only known product bytes before any package or asset mutation."""
    for path, asset in manifest["assets"].items():
        if "predecessors" in asset and observed["assets"].get(path) not in asset["predecessors"]:
            raise error_type(
                "Product service override has unknown contents; no gateway changes are admitted"
            )


def artifact(wheel: Path, config: str) -> dict[str, Any]:
    raw = wheel.read_bytes()
    resolved = yaml.safe_load(config)
    with zipfile.ZipFile(wheel) as archive:
        ingress_policy.require_wheel(archive)
        policy = ingress_policy.from_config(resolved)
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Agent artifact contains duplicate members")
        require_replay_window_capability(
            resolved,
            (REPLAY_WINDOW_CAPABILITY,) if wheel_supports_replay_window(archive) else (),
        )
        meta_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        meta = BytesParser().parsebytes(archive.read(meta_name))
        package = {
            name: sha(archive.read(name))
            for name in names
            if name.startswith("nebius_vpngw/") and not name.endswith("/")
        }
        if "nebius_vpngw/agent/ordinary.py" not in package:
            raise RuntimeError("Selected agent wheel does not implement ordinary confirmation")
        if isinstance(resolved, dict) and resolved.get("vm_ha") is None:
            for source in ordinary_routes.OWNERSHIP_SOURCES:
                local = Path(ordinary_routes.__file__).parent / source
                if package.get("nebius_vpngw/" + source) != sha(local.read_bytes()):
                    raise RuntimeError(
                        "Selected agent wheel does not match ordinary route ownership; "
                        "build or select the wheel for this CLI"
                    )
        tree = ast.parse(archive.read("nebius_vpngw/agent/state_store.py"))
        render_versions = [
            node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and any(
                isinstance(target, ast.Name) and target.id == "RENDER_VERSION"
                for target in node.targets
            )
        ]
        if len(render_versions) != 1 or type(render_versions[0]) is not int:
            raise RuntimeError("Selected agent wheel has no exact render identity")
        assets = {}
        for name, path, mode in ASSETS:
            member = f"nebius_vpngw/systemd/{name}"
            assets[path] = {"member": member, "sha256": sha(archive.read(member)), "mode": mode}
            if name == "nebius-vpngw-ordinary-agent-ordering.conf":
                assets[path]["predecessors"] = [
                    None,
                    sha(archive.read(member)),
                    sha(archive.read("nebius_vpngw/systemd/nebius-vpngw-agent-ordering.conf")),
                    *(sha(raw) for raw in PREVIOUS_AGENT_ORDERINGS),
                ]
            elif name == "nebius-vpngw-ufw-ordering.conf":
                # Upgrade only the exact product-owned override. Waiting for
                # network-online conflicts with the distro's network-pre order.
                assets[path]["predecessors"] = [
                    None,
                    sha(archive.read(member)),
                    sha(PREVIOUS_UFW_ORDERING),
                ]
        assets[str(ingress_policy.EXECUTABLE)] = {
            "member": "nebius_vpngw/ingress_policy.py",
            "sha256": sha(archive.read("nebius_vpngw/ingress_policy.py")),
            "mode": 0o644,
        }
        return {
            "ingress_policy": policy,
            "wheel_name": wheel.name,
            "wheel_sha256": sha(raw),
            "config_sha256": sha(config.encode()),
            "version": meta["Version"],
            "render_version": render_versions[0],
            "requirements": meta.get_all("Requires-Dist") or [],
            "files": {**package, meta_name: sha(archive.read(meta_name))},
            "assets": assets,
            "package_identity": digest(
                {name.removeprefix("nebius_vpngw/"): value for name, value in package.items()}
            ),
            "dependency_wheels": {},
            "route_projection": ordinary_routes.projection(resolved)
            if isinstance(resolved, dict) and resolved.get("vm_ha") is None
            else None,
        }


def pin_agent_artifact(
    ssh: Any, instances: tuple[InstanceResolvedConfig, ...], local: dict, lifetimes: ExitStack
) -> Path:
    """Admit mandatory ingress capability before provisioning or retiring any VM."""
    selected = ssh._build_wheel()
    if selected is None:
        raise ingress_policy.IngressCapabilityError(
            "Managed ingress requires a deployable matching agent wheel; set VPNGW_AGENT_WHEEL"
        )
    directory = Path(
        lifetimes.enter_context(tempfile.TemporaryDirectory(prefix="vpngw-agent-wheel-"))
    )
    pinned = directory / selected.name
    shutil.copyfile(selected, pinned)
    pinned.chmod(0o600)
    for instance in instances:
        artifact(pinned, instance.config_yaml)
    # Keep one private snapshot even if the selected source wheel changes later.
    # inspect_plan/execute_plan retain their exact artifact digest revalidation.
    ssh._wheel_path = pinned
    ssh._wheel_is_installed_fallback = False
    return pinned


def dependencies_satisfied(requirements: list[str], observed: dict[str, Any]) -> bool:
    pending: list[tuple[str, tuple[str, ...]]] = [(text, ("",)) for text in requirements]
    seen = set()
    while pending:
        text, extras = pending.pop()
        if (text, extras) in seen:
            continue
        seen.add((text, extras))
        req = Requirement(text)
        if req.marker and not any(
            req.marker.evaluate({**observed["markers"], "extra": extra}) for extra in extras
        ):
            continue
        if req.url:
            raise dependency_scope.DependencyFailure('dependency-direct-url', 'Direct URL dependencies require a separately built wheel set')
        name = canonicalize_name(req.name)
        version = observed["dependencies"].get(name)
        if not version or version not in req.specifier:
            return False
        pending.extend(
            (child, tuple(sorted(req.extras)) or ("",))
            for child in observed["requirements"].get(name, [])
        )
    return True


def require_dependency_resolver() -> str:
    """Require the controller tool before any reconstruction can disrupt hosts."""
    uv = shutil.which("uv")
    if not uv:
        raise dependency_scope.DependencyFailure(
            "dependency-resolver-unavailable",
            "Dependency changes need local uv resolution; install uv on this controller and rerun"
        )
    return uv


def resolve_dependencies(
    manifest: dict[str, Any], observed: dict[str, Any], directory: Path, *, reinstall: bool = False
) -> list[Path]:
    from .recovery import CURRENT

    context = CURRENT.get()
    if context is None:
        return _resolve_dependencies(manifest, observed, directory, reinstall=reinstall)
    # Only resolver inputs participate. Caller still obtains fresh guest
    # observations, verifies approval and rechecks package bytes before effects.
    key = digest({
        "requirements": manifest["requirements"],
        "inventory": {name: observed.get(name) for name in (
            "dependencies", "requirements", "markers", "machine", "python", "wheel_tags",
        )},
        "reinstall": reinstall,
    })
    return context.preparation.files(
        key, directory,
        lambda: _resolve_dependencies(manifest, observed, directory, reinstall=reinstall),
    )


def _resolve_dependencies(
    manifest: dict[str, Any], observed: dict[str, Any], directory: Path, *, reinstall: bool = False
) -> list[Path]:
    if not reinstall and dependencies_satisfied(manifest["requirements"], observed):
        return []
    uv = require_dependency_resolver()
    machine = observed["machine"]
    if machine not in {"x86_64", "aarch64"}:
        raise dependency_scope.DependencyFailure('dependency-platform-unsupported', 'Unsupported gateway wheel platform')
    source = directory / "requirements.in"
    output = directory / "requirements.txt"
    source.write_text("\n".join(manifest["requirements"]) + "\n")
    # uv uses existing output pins as preferences, preserving every compatible
    # installed version while resolving necessary changes for the target platform.
    output.write_text(
        ""
        if reinstall
        else "\n".join(
            f"{name}=={version}" for name, version in sorted(observed["dependencies"].items())
        )
        + "\n"
    )
    result = subprocess.run(
        [
            uv,
            "pip",
            "compile",
            str(source),
            "-o",
            str(output),
            "--python-platform",
            f"{machine}-unknown-linux-gnu",
            "--python-version",
            observed["python"],
            "--no-annotate",
            "--no-header",
            "--no-config",
            "--no-build",
            "--no-python-downloads",
        ],
        capture_output=True,
        timeout=120,
    )
    if result.returncode:
        raise dependency_scope.DependencyFailure('dependency-resolution-failed', 'Required dependency resolution failed before gateway mutation')
    paths = []
    selected: dict[str, str] = {}
    for line in output.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        pins = list(req.specifier)
        if len(pins) != 1 or pins[0].operator != "==" or req.url:
            raise dependency_scope.DependencyFailure('dependency-manifest-invalid', 'Dependency resolution did not produce exact versions')
        name, version = canonicalize_name(req.name), pins[0].version
        selected[name] = version
        if not reinstall and observed["dependencies"].get(name) == version:
            continue
        with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
            release = json.load(response)
        supported = set(observed["wheel_tags"])
        candidates = []
        for item in release["urls"]:
            if item.get("packagetype") == "bdist_wheel" and not item.get("yanked"):
                _, _, _, tags = parse_wheel_filename(item["filename"])
                if supported.intersection(str(tag) for tag in tags):
                    candidates.append(item)
        if not candidates:
            raise dependency_scope.DependencyFailure('dependency-wheel-unavailable', 'No compatible binary wheel for a required dependency')
        item = sorted(candidates, key=lambda value: value["filename"])[0]
        paths.append(_download_dependency(item, directory))
    dependency_scope.check_shared_conflicts(observed, selected, parse_requirement=Requirement)
    return paths


def prepare_dependency_manifest(manifest: dict, inventory: dict, wheels: list[Path]) -> None:
    versions = {**inventory["dependencies"], "nebius-vpngw": manifest["version"]}
    requirements = {**inventory["requirements"], "nebius-vpngw": manifest["requirements"]}
    for path in wheels:
        key, version, _build, _tags = parse_wheel_filename(path.name)
        with zipfile.ZipFile(path) as archive:
            members = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            if len(members) != 1:
                raise dependency_scope.DependencyFailure('dependency-manifest-invalid', 'Selected dependency wheel metadata is ambiguous')
            metadata = BytesParser().parsebytes(archive.read(members[0]))
        versions[str(key)] = str(version)
        requirements[str(key)] = metadata.get_all("Requires-Dist") or []
    roots = ("nebius-vpngw", "pip")
    selected = dependency_scope.closure(
        roots, inventory["requirements"], inventory["markers"], parse_requirement=Requirement
    ) | dependency_scope.closure(
        roots, requirements, inventory["markers"], parse_requirement=Requirement
    )
    dependency_scope.project(inventory, selected)
    manifest["expected_dependencies"] = {
        key: value for key, value in versions.items() if key in selected
    }
    manifest["expected_requirements"] = {
        key: value for key, value in requirements.items() if key in selected
    }


def refresh_dependency_observation(
    previous: dict, current: dict, inventory: dict, manifest: dict
) -> dict:
    """Expand the approval scope without accepting changes during resolution."""
    selected = set(manifest["expected_dependencies"])
    predecessor = {**previous, **dependency_scope.project(inventory, selected)}
    if predecessor != current:
        raise RuntimeError("Gateway state changed during dependency planning; rerun apply")
    return current


def _download_dependency(item: dict, directory: Path) -> Path:
    """The resolver and resume path share exactly the same payload admission."""
    path = directory / item["filename"]
    if path.parent != directory or path.is_symlink():
        raise dependency_scope.DependencyFailure('dependency-identity-invalid', 'Dependency wheel has an invalid filename')
    name, version, _build, _tags = parse_wheel_filename(path.name)
    if not item["url"].startswith("https://files.pythonhosted.org/"):
        raise dependency_scope.DependencyFailure('dependency-wheel-origin', 'Untrusted dependency download origin')
    with urlopen(item["url"], timeout=60) as response:
        raw = response.read(64 * 1024 * 1024 + 1)
    if len(raw) > 64 * 1024 * 1024 or sha(raw) != item["digests"]["sha256"]:
        raise dependency_scope.DependencyFailure('dependency-wheel-integrity', 'Dependency wheel integrity verification failed')
    if name == "vici":
        from ..ipsec_observation import VICI_VERSION, VICI_WHEEL_SHA256

        if str(version) != VICI_VERSION or sha(raw) != VICI_WHEEL_SHA256:
            raise dependency_scope.DependencyFailure('dependency-wheel-integrity', 'VICI wheel differs from the qualified dependency')
    path.write_bytes(raw)
    return path


def restore_dependencies(manifest: dict, observed: dict, directory: Path) -> list[Path]:
    """Fetch only the exact approved filenames and hashes; never resolve again."""
    paths = []
    for filename, expected in sorted(manifest["dependency_wheels"].items()):
        if Path(filename).name != filename or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise dependency_scope.DependencyFailure('dependency-identity-invalid', 'Invalid approved dependency identity')
        name, version, _build, tags = parse_wheel_filename(filename)
        if not set(observed["wheel_tags"]).intersection(str(tag) for tag in tags):
            raise dependency_scope.DependencyFailure('dependency-wheel-incompatible', 'Approved dependency is incompatible with the current gateway')
        with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
            release = json.load(response)
        matches = [
            item
            for item in release["urls"]
            if item.get("filename") == filename
            and item.get("packagetype") == "bdist_wheel"
            and item.get("digests", {}).get("sha256") == expected
        ]
        if len(matches) != 1:
            raise dependency_scope.DependencyFailure('dependency-wheel-unavailable', 'Exact approved dependency is unavailable; no packages changed')
        # Yanked status affects new resolution, not a previously approved hash.
        paths.append(_download_dependency(matches[0], directory))
    return paths


class RemoteGuestWriterBusy(WriterBusy):
    """Authenticated guest admission is waiting for its deployment lock."""


class RemoteTransportUnavailable(RuntimeError):
    """Preserve a transport cause separately from rejected guest admission."""


def remote(
    ssh: Any,
    target: str,
    instance: InstanceResolvedConfig,
    local: dict,
    request: dict,
    *,
    bootstrap: Any = ordinary_bootstrap,
) -> dict:
    payload = json.dumps(request).encode()
    if len(payload) > 128 * 1024 * 1024:
        raise RuntimeError("Ordinary deployment request exceeded its limit")
    paramiko = ssh._ensure_paramiko()
    client = paramiko.SSHClient()
    try:
        configure_paramiko_host_verification(
            client,
            paramiko,
            policy=ssh._ssh_policy,
            hostname=instance.hostname if ssh._ssh_policy else None,
            transport_host=target if ssh._ssh_policy else None,
        )
        vm_spec = (local.get("gateway_group") or {}).get("vm_spec") or {}
        ssh._connect_client(
            client,
            hostname=target,
            username=vm_spec.get("ssh_username") or os.environ.get("VPNGW_SSH_USER", "ubuntu"),
            vm_spec=vm_spec,
        )
        ingress_ssh.preflight(client, instance.config_yaml)
        payload = bootstrap.source_frame() + payload
        deadline = time.monotonic() + 630
        stdin, stdout, stderr = client.exec_command(
            bootstrap.COMMAND,
            timeout=630,
        )
        sent = 0
        while sent < len(payload):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Ordinary deployment deadline exceeded during upload")
            stdin.channel.settimeout(remaining)
            count = stdin.channel.send(payload[sent : sent + 65536])
            if not count:
                raise RuntimeError("Ordinary deployment transport closed during upload")
            sent += count
        stdin.channel.shutdown_write()
        data = bytearray()
        channel = stdout.channel
        while True:
            if time.monotonic() >= deadline:
                channel.close()
                raise RuntimeError(
                    "Ordinary deployment deadline exceeded; remote result is unverified"
                )
            while channel.recv_ready():
                data.extend(channel.recv(65536))
                if len(data) > 2 * 1024 * 1024:
                    raise RuntimeError("Ordinary deployment response exceeded its limit")
            while channel.recv_stderr_ready():
                channel.recv_stderr(65536)  # never project remote command/config output
            if channel.exit_status_ready() and not channel.recv_ready():
                break
            time.sleep(0.02)
        rc = channel.recv_exit_status()
        result = json.loads(data)
        if not isinstance(result, dict) or rc or result.get("status") == "failed":
            stage = result.get("stage", "transport") if isinstance(result, dict) else "transport"
            if not isinstance(stage, str) or stage not in {
                "inspect",
                "predecessor",
                "stage",
                "quiesce",
                "install",
                "publish",
                "reconcile",
                "activate",
                "verify",
            }:
                stage = "transport"
            code = result.get("error_code") if isinstance(result, dict) else None
            display = getattr(bootstrap, "DISPLAY_NAME", "Ordinary deployment")
            if code == "repair_writer_busy":
                raise RemoteGuestWriterBusy(f"{display} waiting at {stage}; existing writer is still settling")
            if isinstance(code, str) and code in dependency_scope.DependencyFailure.CODES:
                raise dependency_scope.DependencyFailure(code, f"{display} dependency admission failed")
            if isinstance(code, str) and code in GUEST_FAILURES:
                raise GuestFailure(code, stage, f"{display} failed at {stage}; {GUEST_FAILURES[code][0]}")
            raise RuntimeError(f"{display} failed at {stage}; no success is confirmed")
        return result
    except OSError as error:
        display = getattr(bootstrap, "DISPLAY_NAME", "Ordinary deployment")
        raise RemoteTransportUnavailable(
            f"{display} transport is unavailable; remote result is unverified"
        ) from error
    except ValueError as error:
        raise RuntimeError(
            "Ordinary deployment transport or receipt verification failed"
        ) from error
    finally:
        client.close()


@dataclass
class OrdinaryPlan:
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
    def noop(self) -> bool:
        return not self.effects

    @property
    def digest(self) -> str:
        return digest(
            {
                "target": self.target_identity,
                "host": self.instance.hostname,
                "transport": self.target,
                "predecessor": self.observation,
                "desired": self.manifest,
                "effects": self.effects,
            }
        )

    def close(self) -> None:
        self.temporary.cleanup()


def inspect_plan(
    ssh: Any,
    target: str,
    instance: InstanceResolvedConfig,
    local: dict,
    *,
    target_identity: str,
    resume_manifest: dict | None = None,
) -> OrdinaryPlan:
    wheel = ssh._build_wheel(allow_installed_fallback=ssh._find_project_root() is None)
    if wheel is None:
        raise RuntimeError("Ordinary deployment requires a deployable agent wheel")
    manifest = artifact(wheel, instance.config_yaml)
    if resume_manifest is not None:
        from ..apply_approval import validate

        validate(resume_manifest.get("command_approval", {}))
        if any(
            resume_manifest[key] != manifest[key]
            for key in ("wheel_sha256", "config_sha256", "files", "assets")
        ):
            raise RuntimeError("Interrupted ordinary package authority changed")
        manifest = json.loads(json.dumps(resume_manifest))
    result = remote(ssh, target, instance, local, {"action": "inspect", "manifest": manifest})
    observed = result["observation"]
    inventory = result["dependency_inventory"]
    if resume_manifest is not None and manifest["runtime_plan"] != observed["runtime_plan"]:
        raise RuntimeError("Interrupted ordinary package authority changed native closure")
    manifest["runtime_plan"] = observed["runtime_plan"]
    pending_runtime = observed.get("runtime_pending")
    if pending_runtime is not None:
        saved = pending_runtime["manifest"]
        if resume_manifest is not None:
            from ..apply_approval import manifest_identity

            if (
                manifest_identity(saved) != manifest_identity(manifest)
                or saved.get("command_approval") != manifest["command_approval"]
            ):
                raise RuntimeError("Interrupted ordinary package authority changed")
        if any(
            saved[key] != manifest[key]
            for key in ("wheel_sha256", "config_sha256", "files", "assets")
        ):
            raise RuntimeError(
                "Interrupted apply requires the same approved artifact and configuration"
            )
        manifest = saved
    route_plan = ordinary_routes.RouteRetirementPlan.build(
        observed["route_ownership"], manifest["route_projection"]
    )
    if pending_runtime is None and resume_manifest is None:
        manifest["route_plan"] = route_plan.payload
    temporary = tempfile.TemporaryDirectory(prefix="vpngw-dependency-plan-")
    try:
        dependencies = []
        if pending_runtime is None and resume_manifest is None:
            dependencies = resolve_dependencies(
                manifest, {**observed, **inventory}, Path(temporary.name)
            )
            manifest["dependency_wheels"] = {p.name: sha(p.read_bytes()) for p in dependencies}
            prepare_dependency_manifest(manifest, inventory, dependencies)
        elif pending_runtime is None and any(
            observed["dependencies"].get(name) != version
            for name, version in manifest["expected_dependencies"].items()
        ):
            dependencies = restore_dependencies(manifest, observed, Path(temporary.name))
        if resume_manifest is None:
            fresh = remote(
                ssh, target, instance, local, {"action": "inspect", "manifest": manifest}
            )["observation"]
            observed = refresh_dependency_observation(observed, fresh, inventory, manifest)
        effects = [
            f"remove obsolete static route {item['route']['dst']} via {item['link']['name']}"
            for item in route_plan.payload["deletions"]
        ]
        verify_asset_predecessors(manifest, observed)
        pending = observed.get("operation")
        if pending:
            if not pending.get("recoverable"):
                raise RuntimeError(
                    "Ordinary operation remains unresolved; no gateway changes are admitted"
                )
            effects.append("resume the settled interrupted operation with its original identity")
        if not observed["runtime"]["ready"] or pending_runtime:
            effects.append("install and verify the approved charon-systemd runtime closure")
        if (
            observed["files"] != manifest["files"]
            or observed["package_version"] != manifest["version"]
            or dependency_scope.product_metadata_changed(manifest, observed)
        ):
            effects.append("install agent wheel")
        if observed["assets"] != {
            path: item["sha256"] for path, item in manifest["assets"].items()
        } or observed["asset_modes"] != {
            path: [item["mode"], 0, 0] for path, item in manifest["assets"].items()
        }:
            effects.append("update service assets")
        if dependencies:
            effects.append(
                "install required dependency changes: " + ", ".join(p.name for p in dependencies)
            )
        if observed["config_sha256"] != manifest["config_sha256"]:
            effects.append("publish resolved configuration")
        if not observed["verified"] or observed["agent_state"] != "active":
            effects.append("repair or verify unconfirmed local runtime")
        if effects:
            if (observed.get("environment") or {}).get("error"):
                raise RuntimeError(
                    "Ordinary gateway environment could not be verified; no gateway changes are admitted"
                )
            effects.append(
                "quiesce management agent, health monitor and route maintenance; reconcile firewall, netplan, IPsec, XFRM and FRR (VPN interruption possible)"
            )
        return OrdinaryPlan(
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


def stage_plan(ssh: Any, plan: OrdinaryPlan, local: dict, *, approved: bool) -> dict | None:
    """Stage one exact closure; callers stage every member before activation."""
    if plan.noop:
        return None
    if not approved:
        raise RuntimeError("Disruption approval is required before gateway mutation")
    wheel = plan.wheel.read_bytes()
    if sha(wheel) != plan.manifest["wheel_sha256"]:
        raise RuntimeError("Agent artifact changed after planning")
    result = remote(
        ssh,
        plan.target,
        plan.instance,
        local,
        {
            "action": "stage-package",
            "manifest": plan.manifest,
            "wheel": base64.b64encode(wheel).decode(),
            "dependency_wheels": {
                p.name: base64.b64encode(p.read_bytes()).decode() for p in plan.dependency_paths
            },
        },
    )
    expected = {
        "manifest": digest(plan.manifest),
        "runtime_plan": digest(plan.manifest["runtime_plan"]),
    }
    if result != {"status": "staged", "receipt": expected}:
        raise RuntimeError("Ordinary durable package staging receipt differs")
    return expected


def execute_plan(
    ssh: Any,
    plan: OrdinaryPlan,
    local: dict,
    *,
    approved: bool,
    prepare_replacement: str | None = None,
) -> dict:
    if not plan.noop and not approved:
        raise RuntimeError("Disruption approval is required before gateway mutation")
    if sha(plan.wheel.read_bytes()) != plan.manifest["wheel_sha256"]:
        raise RuntimeError("Agent artifact changed after planning")
    request: dict[str, Any] = {
        "action": "execute",
        "manifest": plan.manifest,
        "request_id": (plan.observation.get("runtime_pending") or {}).get("operation")
        or (plan.observation.get("operation") or {}).get("id")
        or os.urandom(16).hex(),
        "predecessor": digest(plan.observation),
        "plan_digest": plan.digest,
        "approval": plan.digest if approved else None,
        "noop": plan.noop,
    }
    if not plan.noop:
        request.update(
            wheel=base64.b64encode(plan.wheel.read_bytes()).decode(),
            config=base64.b64encode(plan.instance.config_yaml.encode()).decode(),
            dependency_wheels=[
                {"name": p.name, "content": base64.b64encode(p.read_bytes()).decode()}
                for p in plan.dependency_paths
            ],
        )
    if prepare_replacement is not None:
        request["prepare_replacement"] = prepare_replacement
    result = remote(ssh, plan.target, plan.instance, local, request)
    if prepare_replacement is not None:
        expected = {
            "operation": prepare_replacement,
            "boot_id": plan.observation["boot_id"],
            "config_sha256": plan.manifest["config_sha256"],
            "package_identity": plan.manifest["package_identity"],
            "request_id": request["request_id"],
        }
        if result != {"status": "prepared", "receipt": expected}:
            raise RuntimeError("Replacement preparation did not confirm its exact passive artifact")
        return result
    receipt = result.get("receipt") or {}
    if (
        result.get("status") not in {"applied", "unchanged"}
        or receipt.get("schema") != "nebius-vpngw.ordinary-apply.v1"
        or receipt.get("status") not in {"applied", "unchanged"}
        or receipt.get("request_id") != request["request_id"]
        or receipt.get("render_version") != plan.manifest["render_version"]
        or receipt.get("config_sha256") != plan.manifest["config_sha256"]
        or receipt.get("boot_id") != plan.observation["boot_id"]
        or receipt.get("package_identity") != plan.manifest["package_identity"]
    ):
        raise RuntimeError("Ordinary deployment returned an inexact confirmation")
    ingress_ssh.verify_fresh(
        ssh,
        plan.target,
        plan.instance,
        local,
        boot_id=plan.observation["boot_id"],
        config_sha256=plan.manifest["config_sha256"],
    )
    return result
