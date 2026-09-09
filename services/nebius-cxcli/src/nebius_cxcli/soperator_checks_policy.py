"""Frozen upstream check policy; temporary execution values never become user intent."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any

import yaml

from .soperator_checks_binding import suspend_auxiliary_checks
from .soperator_checks_login import native_login_commands
from .soperator_checks_phase import (
    ChecksPhase,
    ChecksPhaseContext,
    admission_partition_configuration,
    restored_partition_configuration,
)
from .soperator_passive_policy import compile_passive_policy, passive_phase_overrides

CHECKS_POLICY_SCHEMA = "nebius-cxcli.soperator-checks-policy.v2"
CHECKS_POLICY_ENV = "CXCLI_CHECK_POLICY_SHA256"
_BOOTSTRAP = frozenset(
    {
        "create-user-soperatorchecks",
        "create-user-nebius",
        "manage-jail-state",
        "ssh-check",
        "ensure-dir-snccld-logs",
        "wait-for-topology",
    }
)
_KNOWN_K8S = _BOOTSTRAP | {
    "wait-for-soperatorchecks-srun-ready",
    "manage-jail-state-force",
    "manage-jail-state-dry-run",
    "upgrade-health-checker",
    "soperator-outputs-logs-cleaner",
    "retrigger-checks",
}
_KNOWN_SLURM = frozenset(
    {
        "all-reduce-perf-nccl-in-docker",
        "all-reduce-perf-nccl-with-ib",
        "all-reduce-perf-nccl-without-ib",
        "cuda-samples",
        "dcgmi-diag-r2",
        "dcgmi-diag-r3",
        "ensure-healthy-nodes",
        "extensive-check",
        "gpu-fryer",
        "ib-gpu-perf",
        "mem-perf",
        "enroot-cleanup",
        "prepull-container-image",
    }
)


def checks_digest(value: object) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )


def _merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge(result[key], value)
        elif value is None:
            result.pop(key, None)
        else:
            result[key] = copy.deepcopy(value)
    return result


@dataclass(frozen=True)
class CheckRule:
    name: str
    check_type: str
    bootstrap: bool
    required: bool
    suspend: bool
    dependencies: tuple[str, ...]
    each_worker: bool
    concurrency: int
    script_sha256: str
    requires_gpu: bool = False


@dataclass(frozen=True)
class SoperatorChecksPolicy:
    source_sha256: str
    values_sha256: str
    rules: tuple[CheckRule, ...]
    check_values: Mapping[str, Any]
    execution_specs: Mapping[str, Any] = field(default_factory=dict)
    auxiliary_pvc: str = ""
    auxiliary_spec: Mapping[str, Any] = field(default_factory=dict)
    passive: Mapping[str, Any] = field(default_factory=dict)
    partitions: Mapping[str, Any] = field(default_factory=dict)

    @cached_property
    def sha256(self) -> str:
        # Rendered execution is derived from these frozen source/value inputs.
        # Hash once per immutable policy, excluding its embedded marker/Helm render.
        return checks_digest(
            {
                "schema": CHECKS_POLICY_SCHEMA,
                "source_sha256": self.source_sha256,
                "values_sha256": self.values_sha256,
                "rules": [asdict(rule) for rule in self.rules],
                "check_values": self.check_values,
                "passive": self.passive,
                "partitions": self.partitions,
            }
        )

    @property
    def required(self) -> tuple[CheckRule, ...]:
        """Topological order with upstream's creation-disabled dependency semantics."""
        remaining = {rule.name: rule for rule in self.rules if rule.required}
        ordered: list[CheckRule] = []
        while remaining:
            ready = sorted(
                name
                for name, rule in remaining.items()
                if not set(rule.dependencies) & remaining.keys()
            )
            if not ready:
                raise ValueError("unsupported Soperator checks dependency cycle")
            ordered.extend(remaining.pop(name) for name in ready)
        return tuple(ordered)

    def effective_values(
        self,
        values: Mapping[str, Any],
        *,
        installing: bool,
        context: ChecksPhaseContext | None = None,
    ) -> dict[str, Any]:
        if checks_digest(values) != self.values_sha256:
            raise ValueError("Soperator check policy does not match desired values")
        result = copy.deepcopy(dict(values))
        if context is not None and context.phase in {
            ChecksPhase.SCHEDULES,
            ChecksPhase.ADMISSION,
            ChecksPhase.READY,
        }:
            if context.phase != ChecksPhase.READY:
                cluster = result["slurmCluster"]["overrideValues"]
                cluster["partitionConfiguration"] = admission_partition_configuration(
                    cluster.get("partitionConfiguration"), checks_open=True
                )
            elif context.partition_restoration:
                cluster = result["slurmCluster"]["overrideValues"]
                cluster["partitionConfiguration"] = restored_partition_configuration(
                    cluster["partitionConfiguration"], context.partition_restoration
                )
            return result
        checks = result.setdefault("soperatorActiveChecks", {}).setdefault("overrideValues", {})
        if checks is None:
            checks = {}
            result["soperatorActiveChecks"]["overrideValues"] = checks
        rows = checks.setdefault("checks", {})
        for rule in self.rules:
            row = rows.setdefault(rule.name, {})
            # Even bootstrap recurring schedules must not race operation acceptance.
            row["suspend"] = True
            row["runAfterCreation"] = rule.required and rule.bootstrap
            kind_spec = rule.check_type + "Spec"
            container = row.setdefault(kind_spec, {}).setdefault("jobContainer", {})
            field = "extraEnv"
            original = (
                self.check_values[rule.name]
                .get(kind_spec, {})
                .get("jobContainer", {})
                .get(field, [])
            )
            container[field] = [
                copy.deepcopy(item) for item in original if item.get("name") != CHECKS_POLICY_ENV
            ]
            container[field].append({"name": CHECKS_POLICY_ENV, "value": self.sha256})
        if context is not None:
            cluster = result["slurmCluster"]["overrideValues"]
            cluster["partitionConfiguration"] = admission_partition_configuration(
                cluster.get("partitionConfiguration"),
                checks_open=context.phase == ChecksPhase.ACCEPTANCE,
            )
            if context.phase == ChecksPhase.MAINTENANCE and not context.passive_fallback:
                overrides = passive_phase_overrides(
                    self.passive, context.reservation, fresh_install=context.fresh_install
                )
                if overrides:
                    scripts = cluster.setdefault("slurmScripts", {})
                    scripts["builtIn"] = _merge(scripts.get("builtIn") or {}, overrides)
        elif installing:
            cluster = result["slurmCluster"]["overrideValues"]
            cluster["partitionConfiguration"] = paused_partition_configuration(
                cluster.get("partitionConfiguration")
            )
        return result


def compile_checks_policy(source_dir: Path, values: Mapping[str, Any]) -> SoperatorChecksPolicy:
    """Compile only understood execution contracts from an already verified source tree."""
    active = values.get("soperatorActiveChecks", {})
    if not isinstance(active, Mapping) or active.get("enabled", True) is not True:
        raise ValueError(
            "Soperator lifecycle requires upstream ActiveChecks; review the target policy"
        )
    if values.get("soperator", {}).get("soperatorChecks", {}).get("enabled", True) is not True:
        raise ValueError("Soperator lifecycle requires the upstream checks controller")
    overrides = active.get("overrideValues") or {}
    if not isinstance(overrides, Mapping):
        raise ValueError("Soperator ActiveChecks overrides must be a mapping")
    if {"waitForChecks", "srunReadyPartition"} & overrides.keys():
        raise ValueError(
            "waitForChecks is unsupported upstream; remove it from the target configuration"
        )
    chart = source_dir / "helm/soperator-activechecks"
    defaults = yaml.safe_load((chart / "values.yaml").read_text())
    if not isinstance(defaults, Mapping) or not isinstance(defaults.get("checks"), Mapping):
        raise ValueError("unsupported upstream ActiveChecks values contract")
    effective = _merge(defaults, overrides)
    login_commands = native_login_commands(
        source_dir,
        str(values.get("slurmCluster", {}).get("overrideValues", {}).get("clusterName", "")),
    )
    rules: list[CheckRule] = []
    for name, check in effective["checks"].items():
        if not isinstance(check, Mapping):
            raise ValueError("invalid upstream check definition")
        if not check.get("enabled", False):
            continue
        kind = check.get("checkType")
        if (
            (kind == "slurmJob" and name not in _KNOWN_SLURM)
            or (kind == "k8sJob" and name not in _KNOWN_K8S)
            or kind not in {"slurmJob", "k8sJob"}
        ):
            raise ValueError(f"unsupported Soperator check execution contract: {name}")
        spec_key = str(kind) + "Spec"
        spec = check.get(spec_key, {})
        if spec.get("sbatchScriptRefName") or spec.get("scriptRefName"):
            raise ValueError(f"unsupported external Soperator check script: {name}")
        original = defaults["checks"].get(name, {}).get(spec_key, {})
        script_key = "sbatchScriptFile" if kind == "slurmJob" else "scriptFile"
        if "pythonScriptFile" in original:
            script_key = "pythonScriptFile"
        script_file = spec.get(script_key)
        if script_file != original.get(script_key) or any(
            key in spec for key in ("sbatchScript", "script", "pythonScript")
        ):
            raise ValueError(f"unsupported custom Soperator check script: {name}")
        expected_container = dict(original.get("jobContainer", {}))
        bound_command = spec.get("jobContainer", {}).get("command")
        if name in login_commands and bound_command == login_commands[name]:
            expected_container["command"] = login_commands[name]
        if any(
            spec.get("jobContainer", {}).get(key) != expected_container.get(key)
            for key in ("command", "args", "image")
        ) or check.get("podTemplateNameRef"):
            raise ValueError(f"unsupported custom Soperator check container: {name}")
        if script_file:
            path = (chart / str(script_file)).resolve()
            if not path.is_relative_to(chart.resolve()):
                raise ValueError("Soperator check script escapes verified chart")
            script = path.read_text()
            if name in login_commands and bound_command == login_commands[name]:
                script = login_commands[name][-1]
        else:
            if spec.get("jobContainer", {}).get("command") != original.get("jobContainer", {}).get(
                "command"
            ):
                raise ValueError(f"unsupported custom Soperator check command: {name}")
            script = json.dumps(spec, sort_keys=True)
        if kind == "slurmJob" and re.search(r"#SBATCH\s+(?:--nodes(?:=|\s)|-N\s*)[2-9]", script):
            raise ValueError(f"unsupported multi-node Soperator check: {name}")
        if name in _BOOTSTRAP and re.search(r"\b(?:srun|sbatch)\s", script):
            raise ValueError(f"bootstrap check now requires scheduling: {name}")
        for flag in ("suspend", "runAfterCreation"):
            if flag in check and not isinstance(check[flag], bool):
                raise ValueError(f"invalid check {flag}: {name}")
        limit = spec.get("maxNumberOfJobs", 0)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(f"invalid check concurrency: {name}")
        rules.append(
            CheckRule(
                name=name,
                check_type=str(kind),
                bootstrap=name in _BOOTSTRAP,
                required=check.get("runAfterCreation", False),
                suspend=check.get("suspend", False),
                dependencies=tuple(check.get("dependsOn") or ()),
                each_worker=spec.get("eachWorkerJobs", False) is True,
                concurrency=min(limit or 200, 200),
                script_sha256=checks_digest(script),
                requires_gpu=any(
                    item.get("name") == "SBATCH_GPUS_PER_NODE"
                    for item in spec.get("jobContainer", {}).get("extraEnv", [])
                ),
            )
        )
    policy = SoperatorChecksPolicy(
        source_sha256=checks_digest(
            {
                str(p.relative_to(chart)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(chart.rglob("*"))
                if p.is_file()
            }
        ),
        values_sha256=checks_digest(values),
        rules=tuple(sorted(rules, key=lambda r: r.name)),
        check_values=copy.deepcopy(effective["checks"]),
        passive=compile_passive_policy(source_dir, values),
        partitions=copy.deepcopy(
            values.get("slurmCluster", {}).get("overrideValues", {}).get("partitionConfiguration")
            or {}
        ),
    )
    if not policy.required:
        raise ValueError("Soperator target has no required acceptance checks")
    temporary = policy.effective_values(values, installing=False)
    specs = _render_execution_specs(chart, temporary["soperatorActiveChecks"]["overrideValues"])
    if set(specs) != {rule.name for rule in policy.rules}:
        raise ValueError("rendered upstream check inventory differs from the compiled policy")
    missing_schedules = [spec for spec in specs.values() if "schedule" not in spec]
    if missing_schedules:
        default_schedule = _default_check_schedule(source_dir)
        for spec in missing_schedules:
            spec["schedule"] = default_schedule
    jail = [
        volume
        for volume in effective.get("jobContainer", {}).get("volumes", [])
        if volume.get("name") == "jail"
    ]
    auxiliary_pvc = str(jail[0]["persistentVolumeClaim"]["claimName"]) if len(jail) == 1 else ""
    if (
        chart / "templates/run-extensive-check-on-reservations.yaml"
    ).is_file() and not auxiliary_pvc:
        raise ValueError("Upstream auxiliary checks require one authoritative jail binding")
    auxiliary_spec = {}
    if (chart / "templates/run-extensive-check-on-reservations.yaml").is_file():
        rendered = _cached_execution_specs(
            str(chart), yaml.safe_dump(dict(temporary["soperatorActiveChecks"]["overrideValues"]))
        )
        auxiliary = [
            row["spec"]
            for row in rendered
            if row.get("kind") == "CronJob"
            and row.get("metadata", {}).get("name") == "run-extensive-check-on-reservations"
        ]
        if len(auxiliary) != 1 or not auxiliary[0].get("schedule"):
            raise ValueError("upstream auxiliary scheduling contract is unavailable")
        auxiliary_spec = auxiliary[0]
    return replace(
        policy, execution_specs=specs, auxiliary_pvc=auxiliary_pvc, auxiliary_spec=auxiliary_spec
    )


def _default_check_schedule(source_dir: Path) -> str:
    """Apply the served CRD default from the caller-verified upstream release."""
    path = source_dir / "helm/soperator-crds/templates/slurmcluster-crd.yaml"
    if not path.is_file():
        raise ValueError("upstream ActiveCheck schedule default is unavailable")
    defaults = []
    for row in yaml.safe_load_all(path.read_text()):
        if (
            not isinstance(row, Mapping)
            or row.get("metadata", {}).get("name") != "activechecks.slurm.nebius.ai"
        ):
            continue
        for version in row.get("spec", {}).get("versions", []):
            if version.get("served") is True:
                defaults.append(
                    version.get("schema", {})
                    .get("openAPIV3Schema", {})
                    .get("properties", {})
                    .get("spec", {})
                    .get("properties", {})
                    .get("schedule", {})
                    .get("default")
                )
    if (
        not defaults
        or any(not isinstance(value, str) or not value.strip() for value in defaults)
        or len(set(defaults)) != 1
    ):
        raise ValueError("upstream ActiveCheck schedule defaults are missing or ambiguous")
    return defaults[0]


def _render_execution_specs(chart: Path, overrides: Mapping[str, Any]) -> dict[str, Any]:
    # The embedded policy marker includes the full chart and desired-value digests,
    # so any verified source/value change invalidates this bounded render cache.
    return {
        row["metadata"]["name"]: copy.deepcopy(row["spec"])
        for row in _cached_execution_specs(str(chart), yaml.safe_dump(dict(overrides)))
        if row.get("kind") == "ActiveCheck"
    }


@lru_cache(maxsize=8)
def _cached_execution_specs(chart: str, override_yaml: str) -> tuple[dict[str, Any], ...]:
    result = subprocess.run(
        [
            "helm",
            "template",
            "soperator-activechecks",
            str(chart),
            "--namespace",
            "soperator",
            "--values",
            "-",
        ],
        input=override_yaml,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise ValueError("could not render the verified upstream ActiveChecks execution contract")
    return tuple(row for row in yaml.safe_load_all(result.stdout) if isinstance(row, dict))


def paused_partition_configuration(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("configType") != "structured":
        raise ValueError("initial checks isolation requires structured Slurm partitions")
    result = copy.deepcopy(dict(value))
    partitions = result.get("partitions")
    if not isinstance(partitions, list) or not partitions:
        raise ValueError("initial checks isolation requires explicit Slurm partitions")
    for partition in partitions:
        if not isinstance(partition, dict) or not partition.get("name"):
            raise ValueError("invalid Slurm partition for initial isolation")
        config = str(partition.get("config") or "")
        config = re.sub(r"(?:^|\s)State=\S+", "", config)
        partition["config"] = (config.strip() + " State=DOWN").strip()
    return result


def operation_checks_documents(
    documents: Sequence[dict[str, Any]],
    policy: SoperatorChecksPolicy,
    *,
    installing: bool,
    outer_namespace: str,
    outer_name: str,
    context: ChecksPhaseContext | None = None,
) -> list[dict[str, Any]]:
    result = copy.deepcopy(list(documents))
    matches = [
        doc
        for doc in result
        if doc.get("kind") == "ConfigMap"
        and doc.get("metadata", {}).get("name") == "terraform-fluxcd-values"
    ]
    if len(matches) != 1:
        raise ValueError("Soperator operation requires exactly one upstream values ConfigMap")
    data = matches[0]["data"]
    values = yaml.safe_load(data["values.yaml"])
    outer = [
        doc
        for doc in result
        if doc.get("kind") == "HelmRelease"
        and doc.get("metadata", {}).get("name") == outer_name
        and doc.get("metadata", {}).get("namespace") == outer_namespace
    ]
    if len(outer) != 1 or outer[0].get("spec", {}).get("values") != values:
        raise ValueError("Soperator operation requires exact matching umbrella inline values")
    effective = policy.effective_values(values, installing=installing, context=context)
    data["values.yaml"] = yaml.safe_dump(effective, sort_keys=False)
    # The generated umbrella consumes inline values, not this evidence ConfigMap.
    outer[0]["spec"]["values"] = copy.deepcopy(effective)
    if context is None or context.phase not in {
        ChecksPhase.SCHEDULES,
        ChecksPhase.ADMISSION,
        ChecksPhase.READY,
    }:
        suspend_auxiliary_checks(outer[0], values)
    return result


def propose_checks_target(values: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Explicit proposal for a newly reviewed target, never a legacy runtime fallback."""
    result = copy.deepcopy(dict(values))
    changes: list[str] = []
    for key in ("soperator-checks", "soperator-activechecks"):
        row = result.setdefault(key, {})
        if row.get("enabled") is not True:
            row["enabled"] = True
            changes.append(f"values.{key}.enabled = true")
    active = result["soperator-activechecks"]
    for key in ("waitForChecks", "srunReadyPartition"):
        if key in active:
            del active[key]
            changes.append("remove unsupported values.soperator-activechecks." + key)
    # Display each removal. No attempt to guess the provenance of old false values.
    for name, row in (active.get("checks") or {}).items():
        if (
            isinstance(row, dict)
            and row.get("enabled", True)
            and row.get("runAfterCreation") is False
            and name != "wait-for-topology"
        ):
            del row["runAfterCreation"]
            changes.append(
                f"values.soperator-activechecks.checks.{name}.runAfterCreation = upstream default"
            )
    return result, tuple(changes)


def _owned_check_values(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: values.get(key) for key in ("soperator-checks", "soperator-activechecks")}


def freeze_checks_proposal(values: Mapping[str, Any]) -> str:
    target, changes = propose_checks_target(values)
    reset = [
        name
        for name, row in (values.get("soperator-activechecks", {}).get("checks") or {}).items()
        if isinstance(row, Mapping)
        and row.get("enabled", True)
        and row.get("runAfterCreation") is False
        and name != "wait-for-topology"
    ]
    return json.dumps(
        {
            "schema": "nebius-cxcli.soperator-checks-proposal.v1",
            "before": checks_digest(_owned_check_values(values)),
            "after": checks_digest(_owned_check_values(target)),
            "resetCreation": sorted(reset),
            "changes": list(changes),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def parse_checks_proposal(frozen: str) -> dict[str, Any]:
    proposal = json.loads(frozen)
    if (
        not isinstance(proposal, dict)
        or set(proposal) != {"schema", "before", "after", "resetCreation", "changes"}
        or proposal.get("schema") != "nebius-cxcli.soperator-checks-proposal.v1"
    ):
        raise ValueError("unsupported frozen Soperator checks proposal")
    if any(
        not isinstance(proposal[key], str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", proposal[key])
        for key in ("before", "after")
    ):
        raise ValueError("invalid frozen Soperator checks proposal digest")
    if not isinstance(proposal["resetCreation"], list) or any(
        not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]+", name)
        for name in proposal["resetCreation"]
    ):
        raise ValueError("invalid frozen Soperator checks proposal names")
    if not isinstance(proposal["changes"], list) or any(
        not isinstance(row, str) or "\n" in row or "\r" in row for row in proposal["changes"]
    ):
        raise ValueError("invalid frozen Soperator checks proposal changes")
    return proposal


def apply_checks_proposal(
    values: Mapping[str, Any], frozen: str
) -> tuple[dict[str, Any], tuple[str, ...]]:
    proposal = parse_checks_proposal(frozen)
    observed = checks_digest(_owned_check_values(values))
    if observed not in {proposal["before"], proposal["after"]}:
        raise ValueError("Soperator checks configuration differs from the frozen proposal")
    result = copy.deepcopy(dict(values))
    for key in ("soperator-checks", "soperator-activechecks"):
        result.setdefault(key, {})["enabled"] = True
    active = result["soperator-activechecks"]
    active.pop("waitForChecks", None)
    active.pop("srunReadyPartition", None)
    for name in proposal["resetCreation"]:
        row = active.get("checks", {}).get(name)
        if not isinstance(row, dict):
            raise ValueError("frozen Soperator check override disappeared")
        row.pop("runAfterCreation", None)
    if checks_digest(_owned_check_values(result)) != proposal["after"]:
        raise ValueError("frozen Soperator checks proposal result differs")
    return result, tuple(proposal["changes"])


def checks_proposal_changed(frozen: str) -> bool:
    proposal = parse_checks_proposal(frozen)
    return proposal["before"] != proposal["after"]
