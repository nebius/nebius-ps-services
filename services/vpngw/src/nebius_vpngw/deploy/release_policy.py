"""One release admission policy shared by every deployment owner."""

from __future__ import annotations

import base64
from collections.abc import Callable
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from zipfile import ZipFile

from packaging.version import InvalidVersion, Version

from .. import ordinary_bootstrap, release_identity
from .apply_diagnostics import DiagnosticFailure, Failure


class ReleaseBlocked(DiagnosticFailure):
    def __init__(self, assessment: str):
        self.first = Failure("release-admission", "gateway-release-" + assessment)
        super().__init__(
            "A gateway has a newer VPNGW release; upgrade the CLI before applying."
            if assessment == "newer"
            else "Installed gateway release identity is unverified; restore readable release metadata before applying."
        )


def classify(observed: dict, selected: str) -> str:
    try:
        desired = Version(selected)
        if observed.get("state") == "absent" and observed.get("version") is None:
            return "absent"
        value = observed.get("version")
        if observed.get("state") != "present" or not isinstance(value, str) or len(value) > 128:
            return "unknown"
        installed = Version(value)
    except (InvalidVersion, TypeError):
        return "unknown"
    return "newer" if installed > desired else "older" if installed < desired else "matched"


def require(observed: dict, selected: str, *, owned_resume: bool = False) -> str:
    assessment = classify(observed, selected)
    if assessment == "newer" or assessment == "unknown" and not owned_resume:
        raise ReleaseBlocked(assessment)
    return assessment


def wheel_version(wheel: Path) -> str:
    with ZipFile(wheel) as archive:
        metadata = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Selected wheel has ambiguous release metadata")
        value = BytesParser().parsebytes(archive.read(metadata[0]))
        if value.get("Name", "").replace("_", "-").lower() != "nebius-vpngw":
            raise ValueError("Selected wheel is not a VPNGW release")
        return str(Version(value["Version"]))


class Bootstrap:
    COMMAND = ordinary_bootstrap.COMMAND
    DISPLAY_NAME = "Gateway release inspection"

    @staticmethod
    def source_frame() -> bytes:
        source = Path(release_identity.__file__).read_bytes()
        source += b"\nimport json\nprint(json.dumps(observe()))\n"
        return base64.b64encode(source) + b"\n"


def inspect(ssh: Any, target: str, instance: Any, local: dict) -> dict:
    from .ordinary_apply import remote

    return remote(ssh, target, instance, local, {}, bootstrap=Bootstrap)


def require_targets(
    ssh: Any,
    targets: dict,
    instances: tuple,
    local: dict,
    wheel: Path,
    *,
    resume: Callable[[str], bool] | None = None,
) -> None:
    if not targets:
        return
    selected = wheel_version(wheel)
    by_name = {instance.hostname: instance for instance in instances}
    # Observe the entire selected set before admitting any member's effects.
    observations = {
        name: inspect(ssh, target, by_name[name], local) for name, target in sorted(targets.items())
    }
    if any(classify(observed, selected) == "newer" for observed in observations.values()):
        raise ReleaseBlocked("newer")
    for name, observed in observations.items():
        require(
            observed,
            selected,
            owned_resume=bool(
                classify(observed, selected) == "unknown" and resume is not None and resume(name)
            ),
        )


def package_resume(
    ssh: Any, target: str, instance: Any, local: dict, wheel: Path, journal: Any, targets: dict
) -> bool:
    from ..apply_approval import digest
    from . import ordinary_apply, ordinary_command, ordinary_handoff, package_reservation

    artifact = ordinary_apply.sha(wheel.read_bytes())
    snapshot = journal.snapshot()
    tx = snapshot["transaction"] if snapshot else None
    if tx is None or tx["phase"] != "pending" or tx["plan"]["artifact_sha256"] != artifact:
        return False
    if (local.get("gateway_group") or {}).get("vm_ha") is None:
        manifest = ordinary_command.package_manifests(
            journal, local=local, targets=targets, artifact=artifact
        ).get(instance.hostname)
        return bool(
            manifest
            and ordinary_apply.remote(
                ssh,
                target,
                instance,
                local,
                {"action": "release-resume", "manifest": manifest},
            ).get("owned_resume")
            is True
        )
    runtime = snapshot["bindings"].get("runtime")
    if not isinstance(runtime, dict):
        return False
    owner = package_reservation.recovery_owner(journal.root, tx["execution_id"], digest(runtime))
    saved = package_reservation.read(*owner, instance.hostname)
    if saved is None:
        return False
    package = saved["request"]["package"]
    if (
        package["host"] != instance.hostname
        or package["transport"] != target
        or package["desired"]["wheel_sha256"] != artifact
        or package["desired"]["config_sha256"] != ordinary_apply.sha(instance.config_yaml.encode())
    ):
        return False
    observed = ordinary_handoff.inspect_reservation(ssh, target, instance, local, saved)
    return observed["state"] in {
        "READY_TO_CONTINUE",
        "RUNNING_OWNED",
        "WAIT_EXTERNAL_LOCK",
        "PREPARED",
    }
