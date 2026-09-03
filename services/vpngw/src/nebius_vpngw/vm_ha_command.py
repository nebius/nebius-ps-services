"""Closed result contract for the idempotent VM-HA convergence command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

VM_HA_RESULT_SCHEMA = "nebius-vpngw/vm-ha-result-v1"
VM_HA_VERIFICATION_SCOPE = "passive-current-state-v1"


class VMHACommandOutcome(str, Enum):
    """Terminal command outcome with a stable exit-code mapping."""

    HEALTHY = "healthy"
    MAINTENANCE = "maintenance"
    PLANNED = "planned"
    ACTION_REQUIRED = "action-required"
    BLOCKED = "blocked"
    FAILED = "failed"


class VMHACommandClassification(str, Enum):
    """Closed classifications emitted by the public command."""

    HEALTHY = "healthy"
    CONVERSION_REQUIRED = "conversion-required"
    CANDIDATE_READY = "candidate-ready"
    STANDBY_REARM = "standby-rearm"
    CONTROLLER_TRANSITION = "controller-transition"
    APPLY_REQUIRED = "apply-required"
    VM_HA_REQUIRED = "vm-ha-required"
    MAINTENANCE_POLICY = "maintenance-policy"
    EXTERNAL_PREREQUISITE = "external-prerequisite"
    AMBIGUOUS_STATE = "ambiguous-state"
    FAILED = "failed"


class VMHACommandHealth(str, Enum):
    """Identity-safe health values derived from strict HA inspection."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    TRANSITIONING = "transitioning"
    MAINTENANCE = "maintenance"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    NOT_CONFIGURED = "not-configured"


@dataclass(frozen=True)
class VMHACommandImpact:
    """Sanitized plan impact with fail-closed approval semantics."""

    summary: str
    destructive: bool | None
    vpn_traffic_interruption: bool | None
    resource_creation: bool | None

    @property
    def approval_required(self) -> bool:
        return not (
            self.destructive is False
            and self.vpn_traffic_interruption is False
            and self.resource_creation is False
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "destructive": self.destructive,
            "vpn_traffic_interruption": self.vpn_traffic_interruption,
            "resource_creation": self.resource_creation,
            "approval_required": self.approval_required,
        }


@dataclass(frozen=True)
class VMHACommandApproval:
    """One noninterchangeable digest-bound approval domain."""

    kind: str
    digest: str
    effects: tuple[str, ...]
    artifact_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "kind": self.kind,
            "digest": self.digest,
            "effects": list(self.effects),
        }
        if self.artifact_sha256 is not None:
            result["artifact_sha256"] = self.artifact_sha256
        return result


@dataclass(frozen=True)
class VMHACommandResult:
    """Stable, sanitized public result for text and JSON renderers."""

    outcome: VMHACommandOutcome
    classification: VMHACommandClassification
    health: VMHACommandHealth
    effective_config_file: Path
    actions: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    impact: VMHACommandImpact | None = None
    approval: VMHACommandApproval | None = None
    next_action: str | None = None
    verification_scope: str = VM_HA_VERIFICATION_SCOPE
    failover_tested: bool = False

    @property
    def exit_code(self) -> int:
        if self.outcome in {
            VMHACommandOutcome.HEALTHY,
            VMHACommandOutcome.MAINTENANCE,
            VMHACommandOutcome.PLANNED,
        }:
            return 0
        if self.outcome is VMHACommandOutcome.ACTION_REQUIRED:
            return 3
        return 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": VM_HA_RESULT_SCHEMA,
            "outcome": self.outcome.value,
            "classification": self.classification.value,
            "health": self.health.value,
            "verification_scope": self.verification_scope,
            "failover_tested": self.failover_tested,
            "effective_config_file": str(self.effective_config_file),
            "actions": list(self.actions),
            "reasons": list(self.reasons),
            "impact": self.impact.to_dict() if self.impact is not None else None,
            "approval": self.approval.to_dict() if self.approval is not None else None,
            "next_action": self.next_action,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    def to_text(self) -> str:
        policy_headlines = {
            "standby-auto-healing-already-enabled": (
                "VM-HA standby auto-healing is already enabled.",
                VMHACommandOutcome.HEALTHY,
                VMHACommandHealth.HEALTHY,
            ),
            "standby-auto-healing-already-disabled": (
                "VM-HA standby auto-healing is already disabled.",
                VMHACommandOutcome.MAINTENANCE,
                VMHACommandHealth.MAINTENANCE,
            ),
            "standby-auto-healing-enabled": (
                "VM-HA standby auto-healing was enabled successfully.",
                VMHACommandOutcome.HEALTHY,
                VMHACommandHealth.HEALTHY,
            ),
            "standby-auto-healing-disabled": (
                "VM-HA standby auto-healing was disabled successfully.",
                VMHACommandOutcome.MAINTENANCE,
                VMHACommandHealth.MAINTENANCE,
            ),
        }
        policy_headline = next(
            (
                headline
                for action, (headline, outcome, health) in policy_headlines.items()
                if action in self.actions and self.outcome is outcome and self.health is health
            ),
            None,
        )
        headline = (
            policy_headline
            or {
                VMHACommandOutcome.HEALTHY: "VM-HA is healthy now.",
                VMHACommandOutcome.MAINTENANCE: (
                    "VM-HA standby auto-healing maintenance is active."
                ),
                VMHACommandOutcome.PLANNED: "VM-HA plan is ready; no changes were made.",
                VMHACommandOutcome.ACTION_REQUIRED: "VM-HA needs operator action.",
                VMHACommandOutcome.BLOCKED: "VM-HA convergence is blocked.",
                VMHACommandOutcome.FAILED: "VM-HA convergence failed safely.",
            }[self.outcome]
        )
        if self.outcome is VMHACommandOutcome.HEALTHY:
            return headline
        human_actions = tuple(
            action for action in self.actions if action != "standby-auto-healing-recovery-cleared"
        )
        lines = [
            headline,
            f"Classification: {self.classification.value}",
            f"Health: {self.health.value}",
            f"Config: {self.effective_config_file}",
        ]
        if human_actions:
            lines.append(f"Actions: {', '.join(human_actions)}")
        if self.reasons:
            lines.append(f"Reasons: {', '.join(self.reasons)}")
        if self.impact is not None:
            lines.append(f"Impact: {self.impact.summary}.")
        if self.approval is not None:
            lines.extend(
                (
                    f"Approval kind: {self.approval.kind}",
                    f"Approval digest: {self.approval.digest}",
                    f"Effects: {', '.join(self.approval.effects)}",
                )
            )
            if self.approval.artifact_sha256 is not None:
                lines.append(f"Artifact SHA-256: {self.approval.artifact_sha256}")
        if self.next_action:
            lines.append(f"Next: {self.next_action}")
        lines.append("Verification: passive current state only; failover was not exercised.")
        return "\n".join(lines)


def dedupe_reason_codes(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Retain stable reason order while dropping empty duplicates."""

    return tuple(dict.fromkeys(value for value in values if value))
