"""Selective native passive diagnostics; unknown execution stays enabled."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import kubernetes_process
from .soperator_passive_scheduler import scheduler_from_cluster_spec

# Reviewed upstream chart bundles, including scripts, configuration, rendering
# and image defaults. A newly changed upstream implementation is not implicitly
# safe to suppress just because it retains a familiar filename.
REVIEWED_BUNDLES = frozenset(
    {
        # Soperator 4.1.9: unchanged native runners; builtIn gates script and JSON entries.
        "eb812e7ff5ed51cd094d13f627642e0554f053f64a0c3c36d390533efd1b6c17",
        "c1f4ea57913aa3359f887bdae15b4b2def80886539f4028ca57c53b0441c723f",
        "d0312c256ef9fde31b0c17df63b8c555029be90e9fb64c075491211ad1b792c0",
    }
)
# Freeze proof roles as well as executable bytes. Some native scripts hide
# failed queries behind a successful exit; completion is supporting evidence.
PROOF_ROLES = {
    "alloc_gpus_busy.drain.sh": "supporting-only",
    "alloc_gpus_busy.undrain.sh": "supporting-only",
    "alloc_mem_used.drain.sh": "required-measurement",
    "alloc_mem_used.undrain.sh": "required-measurement",
    "boot_disk_full.sh": "required-measurement",
    "gpu_health_check.py": "required-measurement",
    "nvme_raid_health.sh": "supporting-only",
}
DIAGNOSTICS = frozenset(PROOF_ROLES)
BASE_SCRIPTS = (
    "check_runner.py",
    "prolog.sh",
    "epilog.sh",
    "hc_program.sh",
    "pyxis_caching_importer.sh",
)


def passive_bundle_digest(chart: Path) -> str:
    paths = [
        *sorted((chart / "slurm_scripts").glob("*")),
        chart / "templates/slurm-scripts-cm.yaml",
        chart / "values.yaml",
    ]
    manifest = {
        str(p.relative_to(chart)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
        if p.is_file()
    }
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=32)
def _opaque_data(chart: str, bundle: str, overrides: str) -> dict[str, str] | None:
    if not (Path(chart) / "templates/slurm-scripts-cm.yaml").is_file():
        return None
    rendered = kubernetes_process.run(
        [
            "helm",
            "template",
            "slurm-cluster",
            chart,
            "--namespace",
            "soperator",
            "--show-only",
            "templates/slurm-scripts-cm.yaml",
            "-f",
            "-",
        ],
        input=overrides,
        text=True,
        capture_output=True,
        timeout=120,
        check=True,
    )
    documents = [
        row
        for row in yaml.safe_load_all(rendered.stdout)
        if isinstance(row, dict)
        and row.get("kind") == "ConfigMap"
        and row.get("metadata", {}).get("name") == "slurm-scripts"
    ]
    if len(documents) != 1 or not isinstance(documents[0].get("data"), dict):
        raise ValueError("upstream passive configuration cannot be frozen unambiguously")
    return documents[0]["data"]


def _rendered_scheduler(chart: str, overrides: str) -> dict[str, Any]:
    rendered = kubernetes_process.run(
        [
            "helm",
            "template",
            "slurm-cluster",
            chart,
            "--namespace",
            "soperator",
            "--show-only",
            "templates/slurm-cluster-cr.yaml",
            "-f",
            "-",
        ],
        input=overrides,
        text=True,
        capture_output=True,
        timeout=120,
        check=True,
    )
    clusters = [
        row
        for row in yaml.safe_load_all(rendered.stdout)
        if isinstance(row, dict) and row.get("kind") == "SlurmCluster"
    ]
    if len(clusters) != 1 or not isinstance(clusters[0].get("spec"), dict):
        raise ValueError("desired passive scheduler cannot be frozen unambiguously")
    return scheduler_from_cluster_spec(clusters[0]["spec"])


def compile_passive_policy(source: Path, values: Mapping[str, Any]) -> dict[str, Any]:
    from .soperator_deployment_profile import auxiliary_checks_suspended

    fast = auxiliary_checks_suspended(values)
    chart = source / "helm/slurm-cluster"
    bundle = passive_bundle_digest(chart)
    fallback: dict[str, Any] = {
        "supported": False,
        "bundle": bundle,
        "reason": "unreviewed upstream passive execution contract; keeping diagnostics enabled",
        "scheduler": _rendered_scheduler(
            str(chart),
            json.dumps(values.get("slurmCluster", {}).get("overrideValues", {}), sort_keys=True),
        ),
        "opaque": _opaque_data(
            str(chart),
            bundle,
            json.dumps(values.get("slurmCluster", {}).get("overrideValues", {}), sort_keys=True),
        ),
    }
    # Fast Dev/Test disables diagnostics permanently through native controls.
    # Standard maintenance suppression still requires reviewed executable semantics.
    if not fast and bundle not in REVIEWED_BUNDLES:
        return fallback
    defaults = yaml.safe_load((chart / "values.yaml").read_text())["slurmScripts"]
    cluster = values.get("slurmCluster", {}).get("overrideValues", {})
    if not fast and cluster.get("customSlurmConfig"):
        return {
            **fallback,
            "reason": "custom Slurm execution configuration; leaving passive policy unchanged",
        }
    scheduler = fallback["scheduler"]
    if scheduler["HealthCheckProgram"] != "/opt/slurm_scripts/hc_program.sh":
        return {**fallback, "reason": "custom passive scheduler; keeping diagnostics enabled"}
    if any(
        scheduler[key] != [default]
        for key, default in {
            "Prolog": "/opt/slurm_scripts/prolog.sh",
            "Epilog": "/opt/slurm_scripts/epilog.sh",
        }.items()
    ):
        return {**fallback, "reason": "custom job lifecycle hook; keeping diagnostics enabled"}
    overrides = cluster.get("slurmScripts") or {}
    if not isinstance(overrides, Mapping):
        raise ValueError("Slurm scripts overrides must be a mapping")
    if overrides.get("extra") or any(overrides.get(name) for name in BASE_SCRIPTS):
        return {
            **fallback,
            "reason": "custom passive runner or extra scripts; keeping diagnostics enabled",
        }
    configured = overrides.get("builtIn") or {}
    if not isinstance(configured, Mapping) or set(configured) - set(defaults["builtIn"]):
        return {
            **fallback,
            "reason": "unknown passive script configuration; keeping diagnostics enabled",
        }
    entries: dict[str, Any] = {}
    scripts = {name: (chart / "slurm_scripts" / name).read_text() for name in BASE_SCRIPTS}
    for name, default in defaults["builtIn"].items():
        override = configured.get(name) or {}
        if not isinstance(override, Mapping) or set(override) - {
            "enabled",
            "customContent",
            "customConfig",
        }:
            return fallback
        if override.get("customContent") or override.get("customConfig"):
            return {
                **fallback,
                "reason": "custom passive executable or configuration; keeping diagnostics enabled",
            }
        enabled = override.get("enabled", default["enabled"])
        if not isinstance(enabled, bool):
            raise ValueError("passive script enabled must be boolean")
        if not enabled:
            continue
        entries[name] = json.loads((chart / "slurm_scripts" / (name + ".json")).read_text())
        scripts[name] = (chart / "slurm_scripts" / name).read_text()
    if fast:
        opaque = fallback["opaque"]
        if not isinstance(opaque, Mapping) or set(opaque) != {"checks.json", *scripts}:
            raise ValueError(
                "Fast deployment passive render does not preserve native script controls"
            )
        if any(opaque[name] != script.rstrip("\n") for name, script in scripts.items()):
            raise ValueError("Fast deployment passive render changes native operational scripts")
        try:
            rendered_entries = json.loads(opaque["checks.json"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Fast deployment passive render has invalid check configuration"
            ) from exc
        if rendered_entries != [entries[name] for name in sorted(entries)]:
            raise ValueError(
                "Fast deployment passive render does not preserve native check controls"
            )
    return {
        "supported": True,
        "bundle": bundle,
        "reason": "",
        "scheduler": scheduler,
        "opaque": fallback["opaque"],
        "entries": entries,
        "scripts": scripts,
        "diagnostics": sorted(DIAGNOSTICS & entries.keys()),
        "proofRoles": {name: PROOF_ROLES[name] for name in sorted(DIAGNOSTICS & entries.keys())},
    }


def passive_phase_overrides(
    policy: Mapping[str, Any], reservation: str, *, fresh_install: bool = False
) -> dict[str, Any]:
    """Preserve complete native configs, adding only an owned reservation exclusion."""
    if not policy.get("supported") or (not reservation and not fresh_install):
        return {}
    if fresh_install:
        return {name: {"enabled": False} for name in policy["diagnostics"]}
    result = {}
    for name in policy["diagnostics"]:
        config = copy.deepcopy(policy["entries"][name])
        config["skip_for_reservation_prefixes"] = [
            *config.get("skip_for_reservation_prefixes", []),
            reservation,
        ]
        result[name] = {"customConfig": json.dumps(config, sort_keys=True)}
    return result
