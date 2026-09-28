"""Explicit deployment coverage through native upstream controls, never GPU inference."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal

from .soperator_passive_policy import DIAGNOSTICS

if TYPE_CHECKING:
    from .soperator_checks_policy import SoperatorChecksPolicy

FAST_DEV_TEST = "fast-dev-test"
STANDARD = "standard"
FAST_DEPLOY_NOTICE = (
    "Fast deploy — Dev/Test only. Slurm readiness and a test job are verified. "
    "GPU health and performance qualification are disabled."
)
WAIVED_GPU_CHECKS = frozenset(
    {
        "cuda-samples",
        "all-reduce-perf-nccl-in-docker",
        "all-reduce-perf-nccl-with-ib",
        "all-reduce-perf-nccl-without-ib",
        "gpu-fryer",
        "ib-gpu-perf",
        "mem-perf",
        "dcgmi-diag-r2",
        "dcgmi-diag-r3",
        "extensive-check",
    }
)
WAIVED_CHECKS = WAIVED_GPU_CHECKS | {
    "ssh-check",
    "wait-for-soperatorchecks-srun-ready",
    "ensure-healthy-nodes",
    "prepull-container-image",
    "retrigger-checks",
    "upgrade-health-checker",
}
_RETAINED_DEPENDENCIES = {"enroot-cleanup": ["manage-jail-state"]}


def deployment_profile(values: Mapping[str, Any]) -> str:
    if "diagnosticsProfile" in values:
        raise ValueError(
            "values.diagnosticsProfile was removed; use values.deploymentProfile: "
            "fast-dev-test for Dev/Test or standard. Finish active operations with "
            "their original executable before changing configuration."
        )
    profile = values.get("deploymentProfile", STANDARD)
    if profile not in (STANDARD, FAST_DEV_TEST):
        raise ValueError("values.deploymentProfile must be standard or fast-dev-test")
    return str(profile)


def resolve_create_profile(
    values: Mapping[str, Any] | None,
    *,
    fast_deploy: bool | None,
    choose: Callable[[bool | None], bool] | None = None,
) -> dict[str, Any]:
    """Seed from CLI/file; always ask interactively, otherwise default to Standard."""
    result = copy.deepcopy(dict(values or {}))
    supplied_profile = deployment_profile(result)
    selected = fast_deploy
    if selected is None and "deploymentProfile" in result:
        selected = supplied_profile == FAST_DEV_TEST
    if choose is not None:
        selected = choose(selected)
    result["deploymentProfile"] = FAST_DEV_TEST if selected else STANDARD
    return result


def validate_profile_workers(values: Mapping[str, Any]) -> None:
    """Reject unsupported materialized workers before saving or rendering intent."""
    if deployment_profile(values) != STANDARD:
        return
    for node in values.get("nodesets") or ():
        count = node.get("slurmd", {}).get("resources", {}).get("gpu")
        if node.get("gpu", {}).get("enabled") is True and type(count) is int and count == 1:
            raise ValueError(
                "One-GPU workers require deploymentProfile: fast-dev-test (Dev/Test only). "
                f"Worker '{node.get('name', 'unnamed')}' uses Standard; revise its GPU sizing "
                "or explicitly choose Fast Dev/Test before saving or rendering."
            )


def _recorded_check_controls(explicit: Mapping[str, Any]) -> str:
    """Only display recorded boolean controls, never commands or arbitrary values."""
    controls = []
    active = (explicit.get("soperator-activechecks") or {}).get("checks") or {}
    passive = (explicit.get("slurmScripts") or {}).get("builtIn") or {}
    for prefix, entries, fields in (
        ("active", active, ("enabled", "suspend", "runAfterCreation")),
        ("passive", passive, ("enabled",)),
    ):
        for name, settings in sorted(entries.items()):
            if not isinstance(settings, Mapping):
                continue
            for field in fields:
                value = settings.get(field)
                if type(value) is bool:
                    controls.append(f"{prefix} {name}.{field}={str(value).lower()}")
    suffix = f"; +{len(controls) - 3} more" if len(controls) > 3 else ""
    return "; ".join(controls[:3]) + suffix


def deployment_profile_summary(
    values: Mapping[str, Any],
    *,
    target: str,
    stage: Literal["saved", "rendered", "deployment"],
    explicit: Mapping[str, Any] | None = None,
    policy: SoperatorChecksPolicy | None = None,
    values_path: str | None = None,
) -> tuple[str, ...]:
    """Project existing intent/evidence into text without resolving or compiling anything."""
    profile = (
        deployment_profile(values)
        if stage == "saved"
        else FAST_DEV_TEST
        if auxiliary_checks_suspended(values)
        else STANDARD
    )
    label = {
        "saved": "Saved configuration (not rendered)",
        "rendered": "Published render",
        "deployment": "Frozen deployment inputs",
    }[stage]
    lines = [f"Soperator {target} — {label}: {profile}"]
    if profile == FAST_DEV_TEST:
        lines.append(
            "  Fast Dev/Test: service readiness and ordinary-user Slurm smoke tests; "
            "GPU health and performance qualification waived."
        )
        if stage == "saved":
            lines.append("  Reviewed active/passive diagnostics will be disabled when rendered.")
        else:
            coverage = values["cxcliDiagnostics"]
            checks = (
                values.get("soperatorActiveChecks", {}).get("overrideValues", {}).get("checks", {})
            )
            scripts = (
                values.get("slurmCluster", {})
                .get("overrideValues", {})
                .get("slurmScripts", {})
                .get("builtIn", {})
            )
            active = sum(
                checks.get(name, {}).get("enabled") is False for name in coverage["waived"]
            )
            passive = sum(
                scripts.get(name, {}).get("enabled") is False
                for name in coverage["passiveDiagnostics"]
            )
            lines.append(f"  Reviewed diagnostics disabled: {active} active, {passive} passive.")
        lines.append("  Profile retains required bootstrap and operational hooks.")
    else:
        lines.append(
            "  Standard: native diagnostic defaults and valid configured overrides preserved."
        )
        if policy is not None:
            passive_count = (
                str(len(policy.passive.get("diagnostics", ())))
                if policy.passive.get("supported") is True
                else "unknown (unsupported passive inventory)"
            )
            lines.append(
                f"  Effective enabled checks: {len(policy.rules)} active; "
                f"reviewed passive diagnostics: {passive_count}."
            )
    if explicit:
        controls = _recorded_check_controls(explicit)
        if controls:
            lines.append(f"  Explicitly recorded controls: {controls}")
    if values_path is not None:
        lines.append(f"  Generated values: {values_path}")
    return tuple(lines)


def _coverage() -> dict[str, Any]:
    return {
        "profile": FAST_DEV_TEST,
        "scope": "service-and-ordinary-user-slurm-readiness",
        "waived": sorted(WAIVED_CHECKS),
        "passiveDiagnostics": sorted(DIAGNOSTICS),
        "reason": "Dev/Test deployment omits GPU health and performance qualification",
    }


def _check_controls(checks: Mapping[str, Any]) -> None:
    for name, check in checks.items():
        if not isinstance(check, Mapping):
            raise ValueError("Fast deployment requires mapping check controls")
        if name in WAIVED_CHECKS and set(check) - {
            "enabled",
            "suspend",
            "runAfterCreation",
            "schedule",
            "dependsOn",
        }:
            raise ValueError("Fast deployment cannot suppress custom diagnostic execution")
        if "enabled" in check and type(check["enabled"]) is not bool:
            raise ValueError("Fast deployment requires boolean check controls")
        if (
            name in _RETAINED_DEPENDENCIES
            and "dependsOn" in check
            and check["dependsOn"] != _RETAINED_DEPENDENCIES[name]
        ):
            raise ValueError("Fast deployment conflicts with check dependencies")
        if (name in WAIVED_CHECKS and check.get("enabled", False) is not False) or (
            name not in WAIVED_CHECKS and check.get("enabled") is False
        ):
            raise ValueError("Fast deployment conflicts with explicit check controls")


def apply_deployment_profile(values: dict[str, Any], profile: str) -> None:
    """Use native controls; upstream contract verification precedes deployment."""
    if profile == STANDARD:
        return
    if profile != FAST_DEV_TEST:
        raise ValueError("values.deploymentProfile must be standard or fast-dev-test")
    overrides = values["soperatorActiveChecks"]["overrideValues"]
    checks = overrides.setdefault("checks", {})
    if not isinstance(checks, dict):
        raise ValueError("Fast deployment requires mapping check controls")
    _check_controls(checks)
    for name in sorted(WAIVED_CHECKS):
        checks.setdefault(name, {})["enabled"] = False
    for name, dependencies in _RETAINED_DEPENDENCIES.items():
        checks.setdefault(name, {})["dependsOn"] = list(dependencies)
    # Recurring housekeeping stays enabled without gating installation.
    checks.setdefault("enroot-cleanup", {})["runAfterCreation"] = False
    built_in = (
        values["slurmCluster"]["overrideValues"]
        .setdefault("slurmScripts", {})
        .setdefault("builtIn", {})
    )
    for name in sorted(DIAGNOSTICS):
        row = built_in.setdefault(name, {})
        if row.get("enabled", False) is not False:
            raise ValueError("Fast deployment conflicts with explicit passive diagnostics")
        row["enabled"] = False
    values["cxcliDiagnostics"] = _coverage()


def validate_diagnostics_profile(
    values: Mapping[str, Any], upstream_checks: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate native controls; the caller freezes source and verifies their render."""
    if "cxcliDiagnostics" not in values:
        return {}
    coverage = values["cxcliDiagnostics"]
    if coverage != _coverage():
        raise ValueError("Invalid fast deployment coverage metadata; rerender required")
    checks = values["soperatorActiveChecks"]["overrideValues"].get("checks", {})
    if not isinstance(checks, Mapping):
        raise ValueError("Fast deployment requires mapping check controls")
    _check_controls(checks)
    for name in WAIVED_CHECKS:
        source = upstream_checks.get(name)
        if not isinstance(source, Mapping) or source.get("checkType") not in {"slurmJob", "k8sJob"}:
            raise ValueError("Fast deployment waiver does not match upstream checks")
        if checks.get(name, {}).get("enabled") is not False:
            raise ValueError("Fast deployment must disable every listed diagnostic check")
    for name, source in upstream_checks.items():
        if name in WAIVED_CHECKS or not isinstance(source, Mapping):
            continue
        dependencies = source.get("dependsOn", [])
        if not isinstance(dependencies, list) or any(
            not isinstance(dependency, str) or not dependency for dependency in dependencies
        ):
            raise ValueError("Fast deployment dependency contract changed upstream")
        retained = [dep for dep in dependencies if dep not in WAIVED_CHECKS]
        if checks.get(name, {}).get("dependsOn", dependencies) != retained:
            raise ValueError("Fast deployment must preserve every non-waived check dependency")
    return copy.deepcopy(coverage)


def diagnostics_notice(contract: Mapping[str, Any]) -> str:
    if "diagnostics" not in contract:
        return ""
    if contract["diagnostics"] != _coverage():
        raise ValueError("Invalid fast deployment coverage in acceptance contract")
    return FAST_DEPLOY_NOTICE


def auxiliary_checks_suspended(values: Mapping[str, Any]) -> bool:
    if "cxcliDiagnostics" not in values:
        return False
    if values["cxcliDiagnostics"] != _coverage():
        raise ValueError("Invalid fast deployment coverage metadata")
    return True


def rendered_deployment_profile(paths: Any) -> str:
    """Read only frozen rendered coverage when selecting a recovery graph."""
    import yaml

    path = paths.flux_dir / "configmap-terraform-fluxcd-values.yaml"
    documents = list(yaml.safe_load_all(path.read_text()))
    if len(documents) != 1 or not isinstance(documents[0], Mapping):
        raise ValueError("Soperator frozen deployment values are invalid")
    values = yaml.safe_load(documents[0].get("data", {}).get("values.yaml", ""))
    if not isinstance(values, Mapping):
        raise ValueError("Soperator frozen deployment values are unavailable")
    return FAST_DEV_TEST if auxiliary_checks_suspended(values) else STANDARD
