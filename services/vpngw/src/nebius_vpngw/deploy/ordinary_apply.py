"""Ordinary apply planning and receipt validation over the existing SSH trust owner."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename

from .. import ordinary_bootstrap, ordinary_routes
from ..config_loader import InstanceResolvedConfig
from .ssh_policy import configure_paramiko_host_verification

ASSETS = (
    (
        "nebius-vpngw-ordinary-agent.service",
        "/etc/systemd/system/nebius-vpngw-agent.service",
        0o644,
    ),
    (
        "nebius-vpngw-ordinary-agent-ordering.conf",
        "/etc/systemd/system/nebius-vpngw-agent.service.d/override.conf",
        0o644,
    ),
    (
        "nebius-vpngw-ordinary-fix-routes.service",
        "/etc/systemd/system/nebius-vpngw-fix-routes.service",
        0o644,
    ),
    ("nebius-vpngw-fix-routes.timer", "/etc/systemd/system/nebius-vpngw-fix-routes.timer", 0o644),
    (
        "nebius-vpngw-ordinary-health-monitor.service",
        "/etc/systemd/system/nebius-vpngw-health-monitor.service",
        0o644,
    ),
    ("setup-vpngw-firewall.sh", "/usr/local/bin/setup-vpngw-firewall.sh", 0o755),
    ("nebius-vpngw-esp4-preflight.sh", "/usr/local/bin/nebius-vpngw-esp4-preflight.sh", 0o755),
)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def digest(value: Any) -> str:
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def artifact(wheel: Path, config: str) -> dict[str, Any]:
    raw = wheel.read_bytes()
    resolved = yaml.safe_load(config)
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Agent artifact contains duplicate members")
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
                ]
        return {
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
            raise RuntimeError("Direct URL dependencies require a separately built wheel set")
        name = canonicalize_name(req.name)
        version = observed["dependencies"].get(name)
        if not version or version not in req.specifier:
            return False
        pending.extend(
            (child, tuple(sorted(req.extras)) or ("",))
            for child in observed["requirements"].get(name, [])
        )
    return True


def resolve_dependencies(
    manifest: dict[str, Any], observed: dict[str, Any], directory: Path
) -> list[Path]:
    if dependencies_satisfied(manifest["requirements"], observed):
        return []
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError(
            "Dependency changes need local uv resolution; install uv and rerun dry-run"
        )
    machine = observed["machine"]
    if machine not in {"x86_64", "aarch64"}:
        raise RuntimeError("Unsupported gateway wheel platform")
    source = directory / "requirements.in"
    output = directory / "requirements.txt"
    source.write_text("\n".join(manifest["requirements"]) + "\n")
    # uv uses existing output pins as preferences, preserving every compatible
    # installed version while resolving necessary changes for the target platform.
    output.write_text(
        "\n".join(
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
        raise RuntimeError("Required dependency resolution failed before gateway mutation")
    paths = []
    selected: dict[str, str] = {}
    for line in output.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        req = Requirement(line)
        pins = list(req.specifier)
        if len(pins) != 1 or pins[0].operator != "==" or req.url:
            raise RuntimeError("Dependency resolution did not produce exact versions")
        name, version = canonicalize_name(req.name), pins[0].version
        selected[name] = version
        if observed["dependencies"].get(name) == version:
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
            raise RuntimeError("No compatible binary wheel for a required dependency")
        item = sorted(candidates, key=lambda value: value["filename"])[0]
        if not item["url"].startswith("https://files.pythonhosted.org/"):
            raise RuntimeError("Untrusted dependency download origin")
        with urlopen(item["url"], timeout=60) as response:
            raw = response.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024 or sha(raw) != item["digests"]["sha256"]:
            raise RuntimeError("Dependency wheel integrity verification failed")
        path = directory / item["filename"]
        if path.parent != directory:
            raise RuntimeError("Dependency wheel has an invalid filename")
        path.write_bytes(raw)
        paths.append(path)
    changed = {
        name for name, version in selected.items() if observed["dependencies"].get(name) != version
    }
    for name, requirements in observed["requirements"].items():
        if name in changed or name == "nebius-vpngw":
            continue
        for text in requirements:
            req = Requirement(text)
            dependency = canonicalize_name(req.name)
            if req.marker and not req.marker.evaluate({**observed["markers"], "extra": ""}):
                continue
            if dependency in changed and selected[dependency] not in req.specifier:
                raise RuntimeError("Required dependency update conflicts with installed software")
    return paths


def remote(
    ssh: Any, target: str, instance: InstanceResolvedConfig, local: dict, request: dict
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
        payload = ordinary_bootstrap.source_frame() + payload
        deadline = time.monotonic() + 630
        stdin, stdout, stderr = client.exec_command(
            ordinary_bootstrap.COMMAND,
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
            if stage not in {
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
            raise RuntimeError(f"Ordinary deployment failed at {stage}; no success is confirmed")
        return result
    except (OSError, ValueError) as error:
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
    ssh: Any, target: str, instance: InstanceResolvedConfig, local: dict, *, target_identity: str
) -> OrdinaryPlan:
    wheel = ssh._build_wheel(allow_installed_fallback=ssh._find_project_root() is None)
    if wheel is None:
        raise RuntimeError("Ordinary deployment requires a deployable agent wheel")
    manifest = artifact(wheel, instance.config_yaml)
    result = remote(ssh, target, instance, local, {"action": "inspect", "manifest": manifest})
    observed = result["observation"]
    route_plan = ordinary_routes.RouteRetirementPlan.build(
        observed["route_ownership"], manifest["route_projection"]
    )
    manifest["route_plan"] = route_plan.payload
    temporary = tempfile.TemporaryDirectory(prefix="vpngw-dependency-plan-")
    try:
        dependencies = resolve_dependencies(manifest, observed, Path(temporary.name))
        manifest["dependency_wheels"] = {p.name: sha(p.read_bytes()) for p in dependencies}
        expected = dict(observed["dependencies"])
        expected["nebius-vpngw"] = manifest["version"]
        for path in dependencies:
            name, version, _build, _tags = parse_wheel_filename(path.name)
            expected[str(name)] = str(version)
        manifest["expected_dependencies"] = expected
        effects = [
            f"remove obsolete static route {item['route']['dst']} via {item['link']['name']}"
            for item in route_plan.payload["deletions"]
        ]
        for path, asset in manifest["assets"].items():
            if (
                "predecessors" in asset
                and observed["assets"].get(path) not in asset["predecessors"]
            ):
                raise RuntimeError(
                    "Product service override has unknown contents; no gateway changes are admitted"
                )
        pending = observed.get("operation")
        if pending:
            if not pending.get("recoverable"):
                raise RuntimeError(
                    "Ordinary operation remains unresolved; no gateway changes are admitted"
                )
            effects.append("replace settled interrupted operation after fresh approval")
        if (
            observed["files"] != manifest["files"]
            or observed["package_version"] != manifest["version"]
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


def execute_plan(ssh: Any, plan: OrdinaryPlan, local: dict, *, approved: bool) -> dict:
    if not plan.noop and not approved:
        raise RuntimeError("Disruption approval is required before gateway mutation")
    if sha(plan.wheel.read_bytes()) != plan.manifest["wheel_sha256"]:
        raise RuntimeError("Agent artifact changed after planning")
    request: dict[str, Any] = {
        "action": "execute",
        "manifest": plan.manifest,
        "request_id": os.urandom(16).hex(),
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
    result = remote(ssh, plan.target, plan.instance, local, request)
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
    return result
