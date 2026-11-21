"""Sanitized recovery advice, separate from effect and approval authority."""

from __future__ import annotations

import re
import shlex
import socket
import subprocess
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from pathlib import Path

import click

from .apply_diagnostics import (
    DiagnosticFailure,
    Failure,
    ObservationUnavailable,
    PreparationBlocked,
    first_failure,
    observation_pending,
)
from .preparation_cache import PreparationCache


class ConfigurationFailure(ValueError, DiagnosticFailure):
    first = Failure("configuration", "configuration-invalid")


class ArgumentFailure(click.BadParameter):
    """A fixed, product-owned option prerequisite, without user argument values."""

    EXPLANATIONS = {
        "vm-ha-required": "This action requires an explicit gateway_group.vm_ha configuration or --convert-to-ha.",
        "action-conflict": "--convert-to-ha, --rotate-mtls, --standby-auto-healing and --repair are mutually exclusive.",
        "conversion-options-required": "--output and --force require --convert-to-ha.",
        "approval-conflict": "--approve cannot combine with another approval, replacement or peer preparation flag.",
        "specialized-conflict": "Specialized HA actions and repair cannot combine with replacement, peer preparation or another approval mode.",
        "repair-confirmation": "--repair requires its own confirmation and does not support --approve or --dry-run.",
        "ordinary-service-account": "--sa is supported only for ordinary apply.",
    }

    def __init__(self, predicate: str):
        self.explanation = self.EXPLANATIONS[predicate]
        super().__init__(self.explanation)


class WriterBusy(DiagnosticFailure):
    first = Failure("command-lock", "deployment-writer-active")


class ApprovalMismatch(DiagnosticFailure):
    first = Failure("approval", "approval-mismatch")


class RegionalFailure(DiagnosticFailure):
    """A regional child failed; carry only its allowlisted diagnostic code."""

    def __init__(self, side: str, report: dict | None = None):
        if side not in {"a", "b"}:
            raise ValueError("Invalid regional side")
        self.side = side
        recovery = report.get("recovery") if isinstance(report, dict) else None
        code = recovery.get("reason_code") if isinstance(recovery, dict) else None
        self.first = Failure(
            "pair-" + side, code if isinstance(code, str) and code in CAUSES else "unknown-failure"
        )
        cause = recovery.get("cause_code") if isinstance(recovery, dict) else None
        self.cause_code = cause if isinstance(cause, str) and cause in CAUSES else None
        super().__init__(f"Region {side} did not complete; inspect its retained apply evidence")


class PackagePreparationFailure(DiagnosticFailure):
    """Marker for product-owned member and sanitized package detail."""

    member: int
    detail: str | None


class BootstrapKeyFailure(ValueError, DiagnosticFailure):
    first = Failure("ssh-bootstrap", "bootstrap-key-required")


class PredecessorSettlementRequired(DiagnosticFailure):
    first = Failure("predecessor", "predecessor-settlement-required")

    def __init__(
        self,
        message="Predecessor settlement requires approval",
        *,
        approval=None,
        effects=(),
        automatic=False,
    ):
        super().__init__(message)
        self.approval = approval
        self.effects = tuple(effects)
        self.automatic = automatic


class ReplanRejected(ValueError, DiagnosticFailure):
    """Closed predecessor predicates; internal evidence text is never published."""

    def __init__(self, detail: str, *, code: str = "predecessor-evidence-invalid"):
        if code not in {
            "predecessor-evidence-invalid",
            "predecessor-package-unproven",
            "predecessor-not-settled",
            "predecessor-infrastructure-changed",
            "predecessor-credential-unproven",
        }:
            raise ValueError("Unknown predecessor predicate")
        self.first = Failure("predecessor", code)
        super().__init__(detail)


class DeploymentPrerequisite(DiagnosticFailure):
    def __init__(self, code: str):
        if code not in {
            "attached-boot-disk-unavailable",
            "gateway-targets-incomplete",
            "ha-removal-required",
            "approval-required",
        }:
            raise ValueError("Unknown deployment prerequisite")
        self.first = Failure("preflight", code)
        super().__init__(CAUSES[code][0])


class GuestFailure(DiagnosticFailure):
    """Preserve an allowlisted authenticated guest predicate through wrappers."""

    def __init__(self, code: str, stage: str, detail: str):
        if code not in GUEST_FAILURES or stage not in {
            "inspect",
            "predecessor",
            "stage",
            "quiesce",
            "install",
            "publish",
            "reconcile",
            "activate",
            "verify",
            "transport",
        }:
            raise ValueError("Unknown guest failure predicate")
        self.first = Failure("guest-" + stage, "guest-" + code.replace("_", "-"))
        super().__init__(detail)


class SSHAccessFailure(DiagnosticFailure):
    """A sanitized SSH prerequisite; never include stderr or key material."""

    def __init__(self, code: str):
        if code not in SSH_CAUSES:
            raise ValueError("Unknown SSH prerequisite")
        self.code = code
        self.first = Failure("ssh-access", code)
        super().__init__(SSH_CAUSES[code][0])


def ssh_failure_code(error: object) -> str:
    message = str(error).lower()
    if (
        "remote host identification has changed" in message
        or "host identity verification failed" in message
    ):
        return "ssh-host-key-changed"
    if "host key verification failed" in message:
        return "ssh-host-key-unverified"
    if "wrong schema" in message:
        return "ssh-trust-schema-unsupported"
    if "permission denied" in message or "client identity" in message:
        return "ssh-client-authentication-failed"
    return "ssh-trust-unavailable"


class SSHIdentityUnavailable(ValueError, DiagnosticFailure):
    first = Failure("ssh-trust", "retained-ssh-identity-unavailable")


class ApprovalDeclined(click.exceptions.Exit):
    def __init__(self):
        super().__init__(0)


class RepairBlocked(DiagnosticFailure):
    """Repair blocker with replacement advice only after retained ownership proof."""

    def __init__(self, predicate: str, *, ownership_established: bool = False):
        if predicate not in {
            "repair-vm-missing",
            "repair-ssh-unavailable",
            "repair-ssh-authority",
            "repair-package-stalled",
            "repair-worker-blocked",
            "repair-quarantine-access-unproven",
        }:
            raise ValueError("unknown repair blocker")
        self.first = Failure("repair", predicate)
        self.replacement_applicable = ownership_established and predicate in {
            "repair-ssh-unavailable",
            "repair-package-stalled",
        }
        super().__init__(CAUSES[predicate][0])


class RepairWorkerPending(ObservationUnavailable):
    """The retained worker has not completed within this observation allowance."""

    def __init__(self):
        super().__init__("repair-package", "repair-worker-pending")
        self.first = Failure("repair-package", "repair-worker-pending")
        self.args = (CAUSES["repair-worker-pending"][0],)


@dataclass
class Invocation:
    command: str
    config: Path = Path("nebius-vpngw.config.yaml")
    project: str | None = None
    region: str | None = None
    ssh_identity: str | None = None
    sa: str | None = None
    repair: bool = False
    structured: bool = False
    dry_run: bool = False
    emitted: bool = False
    resume_options: tuple[str, ...] = ()
    repair_config: Path | None = None
    selectors_resolved: bool = False
    command_project: str | None = None
    command_region: str | None = None
    preparation: PreparationCache = field(default_factory=PreparationCache, repr=False)

    def command_line(
        self,
        command: str,
        *,
        repair: bool = False,
        resume: bool = False,
        fresh_approval: bool = False,
    ) -> str:
        args = ["nebius-vpngw"]
        if self.ssh_identity:
            args += ["--ssh-identity", self.ssh_identity]
        paired = self.command == "pair apply"
        config = self.repair_config if repair and self.repair_config is not None else self.config
        args += (
            ["pair", "apply", str(self.config)]
            if paired
            else [command, "--local-config-file", str(config)]
        )
        project = self.command_project if self.selectors_resolved else self.project
        region = self.command_region if self.selectors_resolved else self.region
        if project and not paired and command in {"apply", "destroy"}:
            args += ["--project-id", project]
        if region and not paired:
            args += ["--region", region]
        if self.sa and command == "apply" and not paired and not repair:
            args += ["--sa", self.sa]
        if repair:
            args.append("--repair")
        if resume:
            options = iter(self.resume_options)
            for option in options:
                if fresh_approval and option in {
                    "--approve",
                    "--approve-disruption",
                    "--approve-a",
                    "--approve-b",
                    "--approve-vm-ha-migration",
                    "--recover-vm-ha-migration",
                    "--replace-failed-vm-ha-passive",
                }:
                    next(options, None)
                elif not (repair and option == "--dry-run"):
                    args.append(option)
        return shlex.join(args)

    def consume_approval(self) -> None:
        """A settled predecessor cannot authorize its successor or its recovery."""
        remaining = []
        tokens = iter(self.resume_options)
        for token in tokens:
            if token in {
                "--approve",
                "--approve-disruption",
                "--approve-vm-ha-migration",
                "--recover-vm-ha-migration",
                "--replace-failed-vm-ha-passive",
            }:
                next(tokens, None)
            else:
                remaining.append(token)
        self.resume_options = tuple(remaining)

    def select_config(self, path: Path) -> None:
        if path != self.config:
            # Conversion has already published its authoritative target. Resume
            # that target, without requesting a second conversion/output write.
            remaining = []
            tokens = iter(self.resume_options)
            for option in tokens:
                if option in {"--output", "-o"}:
                    next(tokens, None)
                elif option not in {"--force", "-f", "--convert-to-ha"}:
                    remaining.append(option)
            self.resume_options = tuple(remaining)
        self.config = path
        self.repair_config = None


CURRENT: ContextVar[Invocation | None] = ContextVar("deployment_recovery", default=None)


@dataclass(frozen=True)
class Recovery:
    reason_code: str
    reason: str
    next_action: str
    resume_command: str
    repair_command: str
    repair_prerequisite: str
    destroy_command: str | None = None
    fresh_apply_command: str | None = None
    cause_code: str | None = None

    def mapping(self) -> dict:
        return asdict(self)

    def text(self) -> str:
        lines = [
            f"Reason: {self.reason} ({self.reason_code})",
            f"Next action: {self.next_action}",
            f"Resume command: {self.resume_command}",
            f"Repair prerequisite: {self.repair_prerequisite}",
            "Recovery order: normal apply, software repair, then last-resort VM rebuild.",
            "Step 2 — software repair, after authenticated SSH access is available:",
            "  " + self.repair_command,
            "Repair retains the Ubuntu VMs and disks and can interrupt VPN traffic "
            "for longer than two minutes. You will be asked to confirm.",
        ]
        if self.destroy_command and self.fresh_apply_command:
            lines.extend(
                (
                    "Step 3 — last-resort VM rebuild, only if the retained VMs cannot be recovered:",
                    "  " + self.destroy_command,
                    "  " + self.fresh_apply_command,
                    "Destroy deletes gateway VMs and boot-disk contents and prolongs the outage. "
                    "Confirm exact ownership again before destruction. Replacement still requires "
                    "working authentication, DNS and package repositories.",
                )
            )
        return "\n".join(lines)


# Only product-owned predicates select human explanations. Exception messages
# may contain credentials or provider response bodies and never enter this map.
GUEST_FAILURES = {
    "handoff_import_failed": (
        "The gateway could not import the streamed ordinary-to-HA handoff code.",
        "Verify the selected artifact is complete, then resume the original apply operation.",
        "An intact artifact and settled deployment writers are required before repair.",
    ),
    "handoff_execution_failed": (
        "The gateway handoff process failed; the underlying cause is unknown.",
        "Inspect the gateway journals and reconcile the original operation before retrying apply.",
        "Establish the submitted operation's outcome and settle its writers before repair.",
    ),
    "asset_retirement_unproven": (
        "Obsolete service asset ownership is unproven.",
        "Preserve the files and restore the completed predecessor manifest before retrying apply.",
        "Repair cannot infer ownership of unknown service assets.",
    ),
    "asset_retirement_changed": (
        "An obsolete product asset differs from its recorded bytes, mode or ownership.",
        "Preserve the modified asset and reconcile its ownership before retrying apply.",
        "Repair must not delete administrator-modified or symlinked assets.",
    ),
    "replacement_guard_required": (
        "The replacement candidate lacks its required forwarding guard.",
        "Resume the retained replacement operation to establish and verify its candidate guard.",
        "The replacement owner must settle and verify its exact candidate before repair.",
    ),
    "replacement_guard_changed": (
        "The replacement candidate's guard differs from its approved identity.",
        "Inspect the exact candidate and retained replacement evidence before resuming.",
        "Resolve the candidate guard mismatch and settle the replacement owner first.",
    ),
    "replacement_cutover_required": (
        "The replacement candidate is not authorized for cutover.",
        "Resume the retained replacement workflow through its verified cutover boundary.",
        "The replacement owner must finish or settle its retained work before repair.",
    ),
    "approval_required": (
        "The guest request lacks its exact deployment approval.",
        "Review the current deployment plan and approve its exact digest before retrying.",
        "Repair requires its own confirmation and cannot reuse missing deployment authority.",
    ),
    "stale_plan": (
        "The gateway changed after its deployment plan was inspected.",
        "Inspect current gateway state and approve a refreshed plan before retrying.",
        "Exclude competing writers and refresh exact gateway ownership first.",
    ),
    "runtime_drift": (
        "The gateway's local VPN runtime no longer passes verification.",
        "Inspect VPN service health and the retained operation, then resume apply or use repair.",
        "Establish the current package worker's outcome and exact retained VM ownership first.",
    ),
    "ordinary_route_state_invalid": (
        "The gateway's saved local route state failed validation; ordinary upgrade cannot use it.",
        "Inspect the saved software state, then review the separately confirmed apply --repair plan.",
        "Repair must prove exact retained ownership and quiesce prior writers before rebuilding software state.",
    ),
    "final_artifact": (
        "The installed artifact or runtime differs from the approved deployment.",
        "Inspect package verification and select the exact approved artifact before resuming.",
        "A verified artifact and settled package workers are required first.",
    ),
    "receipt": (
        "The gateway's verification receipt could not be confirmed.",
        "Inspect current gateway health and reconcile the retained operation before retrying.",
        "Verify exact resource ownership and settle the existing operation first.",
    ),
    "ordinary_environment_changed": (
        "The gateway's service or network environment changed after inspection.",
        "Resolve the service or network change, then inspect and approve a fresh plan.",
        "The gateway environment and ownership must be verified before reconstruction.",
    ),
    "ordinary_asset_conflict": (
        "A managed service asset conflicts with the approved deployment.",
        "Inspect the conflicting service override and resolve its ownership before retrying.",
        "Resolve managed service asset conflicts before repair.",
    ),
    "route_plan_changed": (
        "The gateway's route plan changed after approval.",
        "Inspect current routes and approve the resulting fresh route plan.",
        "Settle the route owner and verify exact route ownership first.",
    ),
    "route_predecessor_changed": (
        "The gateway's retained route predecessor changed.",
        "Reconcile the original route operation and verify current routes before retrying.",
        "Settle the route owner and verify exact route ownership first.",
    ),
    "identity_drift": (
        "The observed gateway identity differs from the approved VM.",
        "Verify the exact configured VM, disk and SSH identity before retrying.",
        "Exact retained VM ownership and authenticated access are required first.",
    ),
    "package_quiescence_unproven": (
        "A package or service writer has not been proven stopped.",
        "Inspect the retained worker and wait for its known outcome before resuming.",
        "Existing package and service writers must be settled first.",
    ),
    "repair_native_command": (
        "A native package or service command failed during repair.",
        "Inspect the retained repair worker diagnostics, resolve the reported blocker, then resume repair.",
        "Restore native package and service prerequisites and settle the existing worker first.",
    ),
    "repair_service_inhibition": (
        "Repair could not verify that managed services remain inhibited.",
        "Inspect the retained repair guard and service state before resuming repair.",
        "Restore verified service inhibition through the retained repair operation first.",
    ),
    "repair_underlay_ownership": (
        "Repair cannot prove ownership of the retained physical-route configuration.",
        "Inspect the exact repair archive and conflicting network drop-in; resolve ownership before retrying.",
        "Repeating repair cannot replace missing ownership evidence or authorize foreign-route removal.",
    ),
    "repair_boot_persistence": (
        "Repair's boot protection is not enabled persistently.",
        "Inspect boot protection and resume the retained repair operation after resolving its blocker.",
        "Persistent boot protection must be verified before package reconstruction.",
    ),
    "repair_boot_protection": (
        "Repair's boot protection differs from its verified payload.",
        "Inspect the retained guard evidence and resolve the changed protection before resuming.",
        "Exact boot protection must be verified before package reconstruction.",
    ),
    "repair_service_original": (
        "A service definition changed while repair archived its original state.",
        "Exclude the competing service writer and reconcile the retained repair evidence.",
        "Service definitions and their retained ownership must be stable first.",
    ),
    "repair_repository_unavailable": (
        "Signed Ubuntu package repository metadata is unavailable on the gateway.",
        "Restore VM DNS, outbound access and repository configuration, then resume repair.",
        "The gateway must reach its signed package repositories before repair can proceed.",
    ),
    "apt_repository_timeout": (
        "The Ubuntu package metadata refresh exceeded its 180-second limit.",
        "Check repository access and latency from the gateway, then resume the same command.",
        "The signed metadata refresh must complete within its bounded allowance.",
    ),
    "package_observation_deadline": (
        "The enclosing package inspection or staging time budget expired.",
        "Inspect the bounded stage and its prerequisites, then resume the same command.",
        "The remaining request or phase budget may be shorter than the 180-second APT limit.",
    ),
    "package_observation_interrupted": (
        "The bounded package control request was interrupted.",
        "Resume the same command after the interruption is resolved.",
        "Existing durable package workers retain their original lifecycle and authority.",
    ),
    "apt_repository_dns": (
        "APT reported a repository or proxy DNS resolution failure.",
        "Check the gateway's configured DNS and repository/proxy resolution, then resume.",
        "Repository and proxy names must resolve from the gateway.",
    ),
    "apt_repository_transport": (
        "APT reported a connection failure while accessing package metadata.",
        "Check gateway routing, outbound policy and repository/proxy access, then resume.",
        "The configured package sources must be reachable from the gateway.",
    ),
    "apt_repository_tls": (
        "APT reported a TLS validation or handshake failure.",
        "Check gateway time, trusted CAs and repository/proxy TLS configuration; keep verification enabled.",
        "The configured TLS connection must pass verification.",
    ),
    "apt_repository_signature": (
        "APT could not authenticate repository metadata with its configured signing keys.",
        "Review repository signing keys and metadata integrity; do not bypass signature verification.",
        "Package metadata must pass the configured signature checks.",
    ),
    "apt_repository_metadata_time": (
        "APT reported repository metadata outside its validity dates.",
        "Check both gateway time and repository freshness, then resume.",
        "Valid metadata dates are required; this error alone does not prove the VM clock is wrong.",
    ),
    "apt_repository_configuration": (
        "APT reported invalid repository configuration or unavailable release metadata.",
        "Review the configured sources, distribution and release metadata, then resume.",
        "Configured package sources must provide valid signed release metadata.",
    ),
    "repair_handoff_authority": (
        "The interrupted package worker differs from its original approved identity.",
        "Reconcile the original package reservation and exact VM identity, then resume repair.",
        "Original package authority and exclusive deployment ownership must be verified first.",
    ),
    "repair_handoff_foreign_freeze": (
        "Another operation has frozen the interrupted package worker.",
        "Resolve that operation and its process freeze, then resume repair.",
        "Repair cannot take over a process frozen by another operation.",
    ),
    "repair_handoff_unverified": (
        "Repair could not verify safe retirement of the interrupted package worker; the exact cause is unknown.",
        "Inspect retained worker diagnostics and systemd process control, then resume repair.",
        "Exact worker identity, settled requests and supported process control must be verified first.",
    ),
}

VM_HA_CONVERSION_PREREQUISITES = {
    "candidate-conflicts-with-source": (
        "The proposed HA configuration conflicts with the source configuration.",
        "Choose another --output and rerun apply --convert-to-ha; --force cannot overwrite a conflicting candidate.",
        "Resolve the candidate conflict first. Repair targets the existing source deployment until an authoritative HA configuration is available.",
    ),
    "candidate-permissions-not-private": (
        "The verified HA configuration is not restricted to its owner.",
        "Rerun apply --convert-to-ha with --force to republish the exact candidate with mode 0600.",
        "Republish the verified HA configuration with mode 0600 before repairing its existing VMs.",
    ),
    "ordinary-ssh-trust-required": (
        "The existing gateway has no verified SSH identity for HA conversion.",
        "Run ordinary apply for the source configuration to establish SSH trust, then rerun apply --convert-to-ha.",
        "Establish exact VM ownership and verified SSH access before repair or conversion.",
    ),
    "conversion-input-required": (
        "HA conversion requires peer and passive-address input.",
        "Prepare the peer and passive public address, then rerun apply --convert-to-ha interactively.",
        "Complete the conversion inputs first. Repair cannot create the missing HA configuration.",
    ),
    "peer-input-required": (
        "HA conversion is missing required peer settings.",
        "Complete peer preparation and rerun apply --convert-to-ha, retaining any existing passive allocation.",
        "Complete peer settings and publish the HA configuration before repairing the HA deployment.",
    ),
    "passive-public-ip-required": (
        "HA conversion requires an available passive public address.",
        "Provide an existing unattached passive public IP or confirm its reservation with apply --convert-to-ha.",
        "Resolve the passive address and publish the HA configuration before repairing the HA deployment.",
    ),
}

SSH_CAUSES = {
    "ssh-trust-schema-unsupported": (
        "The saved SSH trust receipt uses an unsupported format.",
        "Run apply to validate and convert supported legacy trust; preserve the original receipt.",
        "Establish exact current SSH identity before repair.",
    ),
    "ssh-host-key-changed": (
        "A gateway SSH host key differs from its saved trusted identity.",
        "Reconcile the exact VM identity using authenticated provisioning or independently verified pins, then rerun apply.",
        "Verify the changed host identity before repair; repair does not establish SSH trust.",
    ),
    "ssh-host-key-unverified": (
        "The gateway SSH host key could not be verified.",
        "Restore an exact independently verified host pin and rerun apply.",
        "Establish working verified SSH access before repair.",
    ),
    "ssh-client-authentication-failed": (
        "The configured SSH client identity could not authenticate.",
        "Restore access for the configured SSH public identity, then rerun apply.",
        "Establish working SSH client authentication before repair.",
    ),
    "ssh-trust-unavailable": (
        "Exact gateway SSH trust is unavailable or invalid.",
        "Run apply with authenticated retained host-key evidence or an exact trusted-hosts override.",
        "Establish verified SSH access before repair.",
    ),
    "ssh-trust-or-address-unavailable": (
        "A gateway management address or exact SSH trust is unavailable.",
        "Restore the current management address and verified SSH access, then rerun apply.",
        "Establish exact VM ownership and verified SSH access before repair.",
    ),
    "member-address-unavailable": (
        "A gateway management address is unavailable.",
        "Restore management reachability for the exact retained VM, then rerun apply.",
        "Establish management reachability before repair.",
    ),
}


CAUSES = {
    "repair-quarantine-access-unproven": (
        "A stopped gateway lacks verified boot protection and an existing stateful SSH policy for quarantine.",
        "Inspect the exact predecessor guard evidence and existing security-group policy before retrying.",
        "Repair cannot broaden stateless firewall rules to establish quarantine access.",
    ),
    **SSH_CAUSES,
    "ordinary-upgrade-required": (
        "The retained ordinary gateway does not match the selected release artifact.",
        "Run apply --convert-to-ha with the original ordinary configuration to upgrade and verify it first.",
        "Use ordinary apply first; repair requires a diagnosed local software failure and its own confirmation.",
    ),
    "conversion-lifecycle-unavailable": (
        "An HA command exists but its scoped lifecycle evidence is unavailable.",
        "Restore the authoritative HA lifecycle and resume with its HA configuration.",
        "Resolve the existing HA operation before any ordinary reconstruction.",
    ),
    "gateway-release-newer": (
        "A gateway has a newer VPNGW release than the selected CLI release.",
        "Upgrade the CLI to that release or newer and rerun apply.",
        "Repair cannot downgrade a newer release; upgrade the CLI first.",
    ),
    "gateway-release-unknown": (
        "The installed VPNGW release identity could not be verified.",
        "Restore readable release metadata and rerun apply; unknown is not an absent installation.",
        "Preserve installed evidence and resolve the identity before reconstruction.",
    ),
    "candidate-path-unsafe": (
        "The HA output path aliases the source or is not a safe regular file.",
        "Choose a distinct unlinked output file and rerun apply --convert-to-ha.",
        "Choose and validate the authoritative configuration before repair.",
    ),
    "candidate-changed-during-inspection": (
        "The HA candidate changed while it was being verified.",
        "Exclude concurrent configuration writers, review the candidate and retry conversion.",
        "Configuration and exact deployment ownership must be stable first.",
    ),
    "candidate-not-published-after-reservation": (
        "HA conversion could not publish its configuration after requesting a passive address.",
        "Retry apply --convert-to-ha; the deterministic allocation identity will be reconciled before reuse.",
        "Resolve the allocation request and publish the HA configuration before repairing HA.",
    ),
    "conversion-failed-before-cloud-effects": (
        "HA conversion preparation failed before any cloud changes.",
        "Review the source configuration and rerun apply --convert-to-ha.",
        "Correct the source configuration and required conversion inputs first.",
    ),
    "source-changed-before-candidate-publication": (
        "The source configuration changed before the HA candidate was published.",
        "Review the source and rerun apply --convert-to-ha; any retained passive allocation will be reused.",
        "Establish stable desired configuration and exact ownership first.",
    ),
    "candidate-publication-failed": (
        "The generated HA configuration could not be saved.",
        "Check the output path and rerun apply --convert-to-ha; any retained passive allocation will be reused.",
        "Publish and validate the HA configuration before repairing HA.",
    ),
    "conversion-file-unavailable": (
        "A conversion configuration or record is missing.",
        "Restore the recorded HA YAML, or run apply directly with the authoritative HA configuration.",
        "Resolve configuration provenance and exact deployment identity before repair.",
    ),
    "conversion-file-unsafe": (
        "A conversion file is not an owned, unlinked, safely protected regular file.",
        "Correct the file ownership, links or permissions before retrying conversion.",
        "Resolve configuration provenance and exact deployment identity before repair.",
    ),
    "conversion-output-on-ha-input": (
        "HA YAML already describes the converted deployment.",
        "Run apply with this HA YAML without --output or --force.",
        "Resolve configuration provenance and exact deployment identity before repair.",
    ),
    "conversion-record-invalid": (
        "The conversion record cannot be validated.",
        "Run apply directly with the authoritative HA YAML; preserve the invalid record for inspection.",
        "Resolve configuration provenance and exact deployment identity before repair.",
    ),
    "conversion-source-changed": (
        "The original Single YAML changed after conversion.",
        "Review the changes and run apply directly with the authoritative HA YAML; conversion does not merge changed source intent.",
        "Resolve configuration provenance and exact deployment identity before repair.",
    ),
    "conversion-scope-changed": (
        "The conversion configuration or selectors identify a different deployment.",
        "Restore the original project, region and gateway scope, or use the intended HA YAML directly.",
        "Resolve configuration provenance and exact deployment identity before repair.",
    ),
    "conversion-output-conflict": (
        "The requested conversion destination conflicts with the existing candidate.",
        "Use the recorded HA YAML directly or the original matching conversion destination.",
        "Resolve configuration provenance and exact deployment identity before repair.",
    ),
    "conversion-record-changed": (
        "A conversion file changed during inspection.",
        "Stop concurrent file writers and retry with the authoritative HA YAML.",
        "Resolve configuration provenance and exact deployment identity before repair.",
    ),
    **VM_HA_CONVERSION_PREREQUISITES,
    "managed-mtls-permissions": (
        "Managed HA certificate storage does not have the required private ownership or permissions.",
        "Use the repair command below to reconstruct managed certificate storage after resolving its prerequisites.",
        "Establish exact VM ownership, authenticated SSH access and no active deployment writer; unsafe or foreign-owned storage must be resolved first.",
    ),
    "managed-mtls-action-failed": (
        "A managed HA certificate operation failed on a gateway; the underlying cause is unknown.",
        "Inspect the gateway's managed certificate diagnostics, resolve the blocker, then resume the same operation.",
        "Establish exact VM ownership, authenticated SSH access and no active deployment writer first.",
    ),
    "package-reservation-conflict": (
        "The package request differs from the interrupted operation's approved reservation.",
        "Verify the original configuration, artifact and retained operation before resuming; do not replace its reservation.",
        "Establish the existing package worker's outcome and exact retained VM ownership before repair.",
    ),
    "ssh-bootstrap-conflict": (
        "The selected or published SSH trust differs from the deployment's verified receipt.",
        "Verify the original configuration, retained VM identities and managed SSH trust before resuming the same operation.",
        "Resolve the SSH trust conflict and establish authenticated access to the exact retained VMs before repair.",
    ),
    "repair-worker-pending": (
        "The retained repair package worker has not completed within the observation allowance.",
        "Rerun the same apply --repair command to observe and resume the retained worker.",
        "Observe the existing worker and settle its submitted work; do not start a competing rebuild.",
    ),
    "repair-worker-blocked": (
        "The retained repair package worker cannot safely continue from its current state.",
        "Inspect the retained worker diagnostics and receipts, resolve its blocker, then resume the same apply --repair command.",
        "Reconcile the existing package worker while retaining its guards and receipts before resuming repair.",
    ),
    **{"guest-" + code.replace("_", "-"): value for code, value in GUEST_FAILURES.items()},
    "dependency-resolver-unavailable": (
        "The controller cannot find uv, which is required to prepare dependency changes.",
        "Install uv on this controller and make it available in PATH, then retry.",
        "The controller dependency resolver must be available before repair.",
    ),
    "dependency-resolution-failed": (
        "The controller could not resolve the required Python dependencies.",
        "Check required versions and package repository access, resolve the conflict, then retry.",
        "Dependency resolution must succeed before reconstruction.",
    ),
    "dependency-platform-unsupported": (
        "The gateway architecture is unsupported by dependency preparation.",
        "Verify that the selected gateway uses a supported x86_64 or aarch64 architecture.",
        "A supported gateway platform is required before repair.",
    ),
    "dependency-direct-url": (
        "A managed dependency uses an unsupported direct URL requirement.",
        "Select a verified artifact with supported package requirements before retrying.",
        "Prepare a supported, verified dependency set before reconstruction.",
    ),
    "dependency-metadata-conflict": (
        "Conflicting installed versions exist in the managed dependency metadata.",
        "Inspect the affected package metadata and resolve the conflicting installation, then retry.",
        "Managed package metadata must have an unambiguous identity before repair.",
    ),
    "dependency-shared-conflict": (
        "The required dependency update conflicts with other installed software.",
        "Resolve the shared package requirement conflict before retrying the deployment.",
        "Repair cannot bypass requirements of other software using shared packages.",
    ),
    "dependency-wheel-unavailable": (
        "A required compatible or previously approved dependency wheel is unavailable.",
        "Restore access to the exact required wheel or review a fresh supported artifact and plan.",
        "Every required dependency wheel must be available and verified before repair.",
    ),
    "dependency-wheel-incompatible": (
        "A saved dependency wheel is incompatible with the current gateway platform.",
        "Verify the gateway's Python and platform identity, then review a matching artifact and plan.",
        "Compatible verified dependency wheels are required before repair.",
    ),
    "dependency-wheel-integrity": (
        "A dependency wheel failed its approved integrity check.",
        "Restore the exact verified wheel and retry; preserve the approved digest.",
        "Dependency integrity must be established before reconstruction.",
    ),
    "dependency-wheel-origin": (
        "A dependency download points to an untrusted origin.",
        "Resolve the package metadata or repository source and retry with verified wheels.",
        "Dependency downloads must use the trusted origin before repair.",
    ),
    "dependency-identity-invalid": (
        "A dependency filename or approved digest is invalid.",
        "Inspect the artifact and retained package manifest, then select verified dependency identities.",
        "Valid dependency identities are required before repair.",
    ),
    "dependency-manifest-invalid": (
        "The resolved dependency versions or wheel metadata are ambiguous.",
        "Correct the selected artifact or dependency resolution and retry with exact package versions.",
        "An unambiguous verified dependency manifest is required before repair.",
    ),
    **{
        f"package-preparation-failed-member-{member}": (
            f"Package preparation failed on member {member}.",
            "Inspect the retained member package diagnostic, resolve its blocker, then resume the same command.",
            "Settle the original package worker and restore package preparation prerequisites first.",
        )
        for member in (1, 2)
    },
    "attached-boot-disk-unavailable": (
        "A configured gateway's attached boot disk is unavailable.",
        "Restore authoritative access to the exact attached disk and verify its identity, then retry.",
        "Repair requires verified retained VMs and disks.",
    ),
    "gateway-targets-incomplete": (
        "Provisioning returned an incomplete gateway target set.",
        "Resume apply to reconcile the original provisioning operations and establish every configured VM.",
        "Every configured retained VM must exist and have verified access first.",
    ),
    "ha-removal-required": (
        "Apply VM-HA removal separately before replacing gateway infrastructure.",
        "Review and complete the supported VM-HA removal workflow before requesting infrastructure replacement.",
        "Repair retains the existing VMs; it does not replace their infrastructure.",
    ),
    "approval-required": (
        "This plan requires its displayed exact approval option or interactive confirmation.",
        "Review the emitted plan, then approve its exact digest or rerun interactively.",
        "Repair requires its own default-No confirmation.",
    ),
    "retained-ssh-identity-unavailable": (
        "The original SSH host identity for a retained member is unavailable.",
        "Restore authoritative provisioning or pinned host-key evidence for the retained VM, then retry.",
        "Repair requires authenticated SSH with verified host identity.",
    ),
    "predecessor-settlement-required": (
        "The settled predecessor must be retired before reviewing a new deployment plan.",
        "Use apply to review predecessor settlement, then approve the fresh deployment plan separately.",
        "Repair handles predecessor retirement after confirmation; retained VM ownership and prior request outcomes must be verifiable.",
    ),
    "predecessor-evidence-invalid": (
        "The interrupted deployment's retained evidence is incomplete or no longer matches its owners.",
        "Inspect the retained operation and restore exact ownership evidence, then rerun apply for a fresh review.",
        "Establish exact retained VM ownership and settle existing writers before repair.",
    ),
    "predecessor-package-unproven": (
        "The installed VPN software cannot be verified against the interrupted deployment's approved artifact.",
        "Inspect the original package worker and artifact receipts; resume that work or use repair once its owner is settled.",
        "The original package worker must have a known outcome and no active writer before repair.",
    ),
    "predecessor-credential-unproven": (
        "The interrupted enrollment's original key or credential evidence cannot be verified.",
        "Restore its original protected key and enrollment records, then rerun apply to review settlement.",
        "Verify the original runtime identity and settle its IAM operations before repair; do not replace the key to bypass recovery.",
    ),
    "predecessor-not-settled": (
        "A participating owner of the interrupted deployment has not reached a verified stopped or completed state.",
        "Resume or reconcile that owner's original operation, then rerun apply to review settlement.",
        "Settle all participating operations and exclude their writers before repair.",
    ),
    "predecessor-infrastructure-changed": (
        "The new configuration changes infrastructure or access identity that predecessor settlement must retain.",
        "Restore the predecessor's infrastructure and access settings to recover it, then review the intended change separately.",
        "Repair retains infrastructure; verify the exact existing VM, disk and access identities first.",
    ),
    "bootstrap-key-required": (
        "Creating a gateway member requires a bootstrap SSH public key matching the selected operator identity.",
        "Provide gateway_group.vm_spec.ssh_public_key and verify it matches --ssh-identity, then retry.",
        "Valid SSH access to the retained VMs is required for repair.",
    ),
    "approval-mismatch": (
        "The supplied approval digest does not match this deployment plan.",
        "Review the current plan and approve its exact digest, then retry.",
        "Review the current recovery plan before authorizing any effects.",
    ),
    "approval-not-applicable": (
        "The supplied approval does not apply to the current VM-HA state.",
        "Rerun without --approve, then review the current plan before approving it.",
        "Repair requires its own default-No confirmation.",
    ),
    "repair-vm-missing": (
        "Repair requires every configured existing VM; at least one is missing.",
        "Verify the exact configured resources and use the supported provisioning or replacement workflow.",
        "All retained VMs and their ownership must be established first.",
    ),
    "repair-ssh-unavailable": (
        "Repair could not reach authenticated SSH on a retained VM.",
        "Restore management access, then resume the same repair command.",
        "Authenticated SSH to the exact retained VMs is required first.",
    ),
    "repair-ssh-authority": (
        "Repair could not establish the retained gateway SSH identity from protected lifecycle evidence.",
        "Restore the exact HA lifecycle authority for the selected configuration and retained VMs, then retry repair.",
        "The unmarked retained member requires unchanged, protected HA lifecycle evidence and its existing SSH pin.",
    ),
    "repair-package-stalled": (
        "The retained repair package worker has not converged after its bounded retries.",
        "Inspect its retained package receipts and restore package access before resuming repair.",
        "Settle the existing package worker and restore signed package repositories first.",
    ),
    "controller-transition-in-progress": (
        "The controller is still completing its retained operation.",
        "Resume vm-ha to continue observing the same operation.",
        "Allow the controller operation to settle before starting repair.",
    ),
    "controller-no-progress": (
        "No new controller progress was observed within the observation window.",
        "Inspect controller service journals and resume vm-ha after resolving the stalled prerequisite.",
        "Establish the outcome of the controller operation and exclude active writers first.",
    ),
    "controller-observation-budget-exhausted": (
        "The controller made progress, but its observation deadline expired.",
        "Resume vm-ha to continue observing the retained operation.",
        "Observe and settle the controller operation before starting repair.",
    ),
    "controller-observation-unavailable": (
        "Authoritative controller observation is temporarily unavailable.",
        "Restore observation access and resume vm-ha for the retained operation.",
        "Observe exact ownership and the controller operation's outcome first.",
    ),
    "controller-observation-contradictory": (
        "Controller observations do not match the retained operation's authority.",
        "Inspect the exact member identities and retained controller evidence before resuming vm-ha.",
        "Resolve contradictory ownership and operation evidence first.",
    ),
    "deployment-writer-active": (
        "Another deployment operation still owns this gateway.",
        "Wait for that writer to finish, then resume the retained operation.",
        "The existing writer must release the deployment lock first.",
    ),
    "dependency-verification-failed": (
        "The managed VPN package dependencies do not match the approved package set.",
        "Inspect the managed dependency closure and select a matching artifact or fresh plan.",
        "Settle package workers and restore signed package repository access first.",
    ),
    "artifact-unavailable": (
        "The selected VPN agent wheel is unavailable or incompatible with this command.",
        "Build or select the matching wheel with VPNGW_AGENT_WHEEL, then retry.",
        "A matching, verified agent wheel is required first.",
    ),
    "verification-failed": (
        "Required deployment postconditions could not be verified.",
        "Inspect the reported verification predicates and current gateway status before retrying.",
        "Verify exact resource ownership and settle outstanding operations first.",
    ),
    "health-observations-did-not-agree": (
        "Consecutive fresh observations did not confirm healthy HA after convergence.",
        "Run status, resolve the remaining health prerequisites, then resume vm-ha.",
        "Verify exact resource ownership and settle outstanding operations first.",
    ),
    "desired-state-changed": (
        "An interrupted deployment uses different settings from the current configuration.",
        "Resolve the pending operation through its original owners, then review a fresh plan.",
        "Previous writers and submitted operations must be settled before reconstruction.",
    ),
    "artifact-changed": (
        "The selected deployment wheel's SHA-256 differs from the interrupted operation's pinned artifact; CLI version alone does not establish a match.",
        "Collect a support bundle with support-bundle before recovery; resume with the verified original artifact or review a successor after settlement.",
        "Settle the original package worker and select a valid repair artifact.",
    ),
    "checkpoint-binding-changed": (
        "The saved deployment no longer matches the observed VM identities.",
        "Verify the exact configured VMs and their ownership before retrying.",
        "Exact VM and allocation ownership must be established first.",
    ),
    "approval-state-changed": (
        "Deployment state changed after approval.",
        "Inspect the current state and approve the resulting fresh plan.",
        "Refresh authority and exclude other deployment writers first.",
    ),
    "command-execution-changed": (
        "The saved command belongs to a different deployment execution.",
        "Inspect and settle the retained execution before reviewing a successor.",
        "The retained operation must be settled first.",
    ),
    "package-parent-changed": (
        "The package operation belongs to a different deployment.",
        "Reconcile the original package operation before retrying.",
        "Wait for package workers and establish their terminal outcome first.",
    ),
    "route-lock-deadline": (
        "Another gateway operation held the route lock for the observation deadline.",
        "Let the current route writer finish, then resume the same command.",
        "The existing route writer must finish or be conclusively settled before repair.",
    ),
    "route-operation-pending": (
        "The gateway retains an unfinished ordinary deployment operation.",
        "Resume and settle that operation through its original command, then retry.",
        "Establish the original operation's outcome and stop its writers before repair.",
    ),
    "route-authority-conflict": (
        "The gateway's route ownership or saved route evidence differs from the request.",
        "Inspect the retained route owner and reconcile its original operation before retrying.",
        "Verify exact route ownership and settle conflicting operations before repair.",
    ),
    "route-owner-import-failed": (
        "The gateway could not load the route handoff code.",
        "Verify the installed package and selected artifact; use repair after settling existing work.",
        "Working SSH access, an intact artifact and settled deployment writers are required first.",
    ),
    "route-owner-integrity-failed": (
        "The route handoff source failed its integrity check.",
        "Select an intact verified artifact and retry after checking the deployment's state.",
        "Restore artifact integrity and settle deployment writers before repair.",
    ),
    "route-handoff-unknown": (
        "The gateway route handoff failed without a recognized diagnostic; the cause is unknown.",
        "Inspect gateway service journals and the retained route operation before retrying.",
        "Establish exact ownership, working access and the original operation's outcome first.",
    ),
    "durable-intent-changed": (
        "The saved operation changed while it was being prepared.",
        "Inspect the durable operation and retry after its writer has stopped.",
        "Exclude the competing writer and settle its submitted work first.",
    ),
    "invalid-arguments": (
        "The command arguments or selected configuration path are invalid.",
        "Correct the reported arguments and check the command's --help, then retry.",
        "Correct the arguments and provide a readable, valid configuration first.",
    ),
    "configuration-invalid": (
        "The configuration could not be loaded or validated.",
        "Validate the configuration and required environment variables, then retry.",
        "A valid configuration and its required environment variables are needed first.",
    ),
    "authentication-or-provider-unavailable": (
        "Authentication or required provider access is unavailable.",
        "Restore the selected authentication and required access, then retry.",
        "Working authentication and provider access are required first.",
    ),
    "runtime-credential-authorization-failed": (
        "The runtime identity check was denied by the provider.",
        "Verify the configured runtime service account's required access, then retry apply.",
        "The configured runtime identity needs the required provider access before repair.",
    ),
    "runtime-credential-provider-timeout": (
        "Provider identity verification timed out; credential validity is unknown.",
        "Wait for provider availability, then retry apply with the same configuration.",
        "A fresh authenticated identity check must succeed before repair.",
    ),
    "runtime-credential-provider-unavailable": (
        "The provider was unavailable during runtime identity verification.",
        "Restore provider connectivity and availability, then retry the same apply command.",
        "Provider access and a fresh authenticated identity check are required before repair.",
    ),
    "runtime-credential-dns-unavailable": (
        "The provider hostname could not be resolved during runtime identity verification.",
        "Restore DNS for the provider, then retry the same apply command.",
        "Provider DNS and a fresh authenticated identity check are required before repair.",
    ),
    "runtime-credential-identity-unverified": (
        "The runtime identity could not be verified; the cause is unknown.",
        "Inspect provider and authentication availability, then retry the same apply command.",
        "A fresh authenticated identity check must succeed before repair.",
    ),
    "dns-unavailable": (
        "A required hostname could not be resolved.",
        "Restore DNS for the provider and package repositories, then retry.",
        "Required hostnames must resolve and signed repositories must be reachable.",
    ),
    "operation-timeout": (
        "An operation or observation exceeded its deadline; completion is not yet known.",
        "Resume the same operation to reconcile its original requests and observe completion.",
        "Determine the outcome of accepted requests and exclude active workers first.",
    ),
    "waiting-for-authority": (
        "The operation is pending because authoritative observation is unavailable.",
        "Restore observation access and resume the retained operation.",
        "Observe and settle the existing operation before starting reconstruction.",
    ),
    "guest-response-unavailable": (
        "The gateway ended the deployment request without a usable response; its cause and completion are unknown.",
        "Inspect the retained guest operation and resume the same command to reconcile its original request.",
        "Establish the original request's outcome and exclude active guest workers before repair.",
    ),
    "interrupted": (
        "The command was interrupted; previously submitted work may still be running.",
        "Resume the same command to inspect retained work before admitting new effects.",
        "Exclude prior writers and settle submitted work first.",
    ),
    "activation-recovery-exhausted": (
        "The three automatic HA activation recovery attempts have been used without verified completion.",
        "Inspect the failed activation, then use the repair command below to rebuild the retained VPN stack.",
        "Verify VM ownership and settle remaining deployment or provider work before reconstruction.",
    ),
    "unchanged-repair-refused": (
        "The same HA activation correction already failed and the observed failure has not changed.",
        "Resolve the reported local fault, or use the repair command below to rebuild the retained VPN stack.",
        "Verify VM ownership and settle remaining deployment or provider work before reconstruction.",
    ),
    "replacement-ssh-identity-unavailable": (
        "The original SSH host identity for the missing standby is unavailable.",
        "Restore its original private SSH host key matching the exact pin, then retry apply.",
        "Restore the exact host identity before replacing or repairing the standby.",
    ),
    "unknown-failure": (
        "The command could not complete; the cause is unknown.",
        "Inspect the reported phase, deployment status and service journals before retrying.",
        "Establish exact VM ownership, working access and no active deployment writer first.",
    ),
}


def error_chain(error: BaseException | None):
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)


def advice(
    invocation: Invocation,
    error: BaseException | None = None,
    *,
    codes: tuple[str, ...] = (),
    pending: bool = False,
) -> Recovery:
    from ..config_loader import MissingEnvironmentVariablesError
    from ..dependency_scope import DependencyFailure
    from ..ingress_policy import IngressCapabilityError, IngressPolicyError
    from ..nebius_auth import error_chain_has_cli_authentication_failure
    from ..replay_window import ReplayWindowCapabilityError
    from ..vm_ha_credentials import VMHACredentialIdentityError
    from .ssh_push import VMHAAgentArtifactError
    from .vm_ha_cloud import nebius_request_error_code_is

    diagnostic = first_failure(error)
    candidates = ([diagnostic.predicate] if diagnostic else []) + list(codes)
    # Convergence wrappers keep bounded predicates and causes after dropping
    # the raw exception. Recover only codes present in the allowlisted table.
    candidates += [code.removeprefix("predicate-").removeprefix("cause-") for code in candidates]
    aliases = {
        "approval-digest-stale-or-incorrect": "approval-mismatch",
        "input-interrupted": "interrupted",
        "timeout": "operation-timeout",
        "provider-timeout": "operation-timeout",
        "ssh-authentication-failed": "authentication-or-provider-unavailable",
        "permission-denied": "authentication-or-provider-unavailable",
        "runtime-credential-authentication-failed": "authentication-or-provider-unavailable",
    }
    candidates = [aliases.get(value, value) for value in candidates]
    code = next((value for value in candidates if value in CAUSES), "unknown-failure")
    code = next(
        (
            value
            for value in candidates
            if value in CAUSES
            and (value.startswith("controller-observation-") or value == "controller-no-progress")
        ),
        code,
    )
    if code in {"activation-recovery-exhausted", "unchanged-repair-refused"}:
        # The settled corrective guard is the current blocker. An earlier
        # timeout or transport failure must not hide that exhausted allowance.
        pass
    elif (pending or observation_pending(error)) and code == "unknown-failure":
        code = "waiting-for-authority"
    elif error is not None and error_chain_has_cli_authentication_failure(error):
        code = "authentication-or-provider-unavailable"
    else:
        for item in error_chain(error):
            if isinstance(item, DependencyFailure) and item.code in DependencyFailure.CODES:
                code = item.code
                break
            if isinstance(
                item, (VMHAAgentArtifactError, ReplayWindowCapabilityError, IngressCapabilityError)
            ):
                code = "artifact-unavailable"
                break
            if any(
                nebius_request_error_code_is(item, value)
                for value in (
                    "UNAUTHENTICATED",
                    "PERMISSION_DENIED",
                )
            ):
                code = "authentication-or-provider-unavailable"
                break
            if nebius_request_error_code_is(item, "DEADLINE_EXCEEDED"):
                code = "operation-timeout"
                break
            if isinstance(item, PreparationBlocked):
                assert item.first is not None
                code = item.first.predicate
                break
            if isinstance(item, click.UsageError):
                code = "invalid-arguments"
                break
            if isinstance(item, (KeyboardInterrupt, EOFError, click.Abort)):
                code = "interrupted"
                break
            if isinstance(item, socket.gaierror):
                code = "dns-unavailable"
                break
            if isinstance(item, (TimeoutError, subprocess.TimeoutExpired)):
                code = "operation-timeout"
                break
            if isinstance(item, (MissingEnvironmentVariablesError, IngressPolicyError)):
                code = "configuration-invalid"
                break
            if (
                isinstance(item, VMHACredentialIdentityError)
                and item.reason == "authentication-failed"
            ):
                code = "authentication-or-provider-unavailable"
                break
            if isinstance(item, VMHACredentialIdentityError):
                identity_code = "runtime-credential-" + item.reason
                if identity_code in CAUSES:
                    code = identity_code
                    break
    if code == "unknown-failure" and diagnostic is not None:
        if diagnostic.phase.startswith("verify"):
            code = "verification-failed"
        elif any("dependenc" in value for value in (diagnostic.predicate, *diagnostic.reasons)):
            code = "dependency-verification-failed"
    reason, action, prerequisite = CAUSES[code]
    if code == "predecessor-settlement-required":
        action = (
            "Run "
            + invocation.command_line("apply")
            + " to review settlement, then approve the fresh deployment plan separately."
        )
    cause_code = None
    for item in error_chain(error):
        if isinstance(item, RegionalFailure):
            cause_code = item.cause_code
            if cause_code is not None:
                cause_reason, action, prerequisite = CAUSES[cause_code]
                reason += " " + cause_reason
            reason = f"Region {item.side}: {reason}"
            break
        if isinstance(item, VMHACredentialIdentityError) and item.reason == "authentication-failed":
            reason = (
                "The selected runtime credential failed authentication (authentication-failed)."
            )
            break
        if isinstance(item, PackagePreparationFailure):
            cause_code = code
            code = f"package-preparation-failed-member-{item.member}"
            if cause_code != "unknown-failure" and not cause_code.startswith(
                "package-preparation-failed-member-"
            ):
                if item.detail:
                    reason = item.detail + ". " + reason
                reason = f"Package preparation failed on member {item.member}. {reason}"
            else:
                detail = item.detail or "unexpected package-preparation error; the cause is unknown"
                reason = f"Package preparation failed on member {item.member}. {detail}"
                action = detail + "; correct the reported problem and resume the same command."
            break
        if isinstance(item, ArgumentFailure):
            reason = item.explanation
            break
        if isinstance(item, MissingEnvironmentVariablesError):
            names = [
                name for name in item.variables if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            ]
            reason = "Missing environment variables for placeholders: " + ", ".join(names)
            action = "Set the required variables in the current shell without printing their values, then retry."
            break
    if code == "configuration-invalid":
        from pydantic import ValidationError

        # Paths are metadata; unknown input keys and validation messages can
        # contain secrets. Only render schema-owned names and retired fields.
        from ..schema import VPNGatewayConfig

        def field_names(value):
            if not isinstance(value, dict):
                return set()
            names = set(value.get("properties", {}))
            for child in value.values():
                if isinstance(child, dict):
                    names.update(field_names(child))
            return names

        allowed = field_names(VPNGatewayConfig.model_json_schema()) | {
            "initial_security_group_ids",
            "ssh_allowed_cidrs",
        }
        for item in error_chain(error):
            if isinstance(item, IngressPolicyError):
                fields = sorted(set(item.fields) & allowed)
                reason = "Unsupported gateway configuration fields."
                if fields:
                    reason = "Unsupported gateway configuration fields: " + ", ".join(fields) + "."
                action = (
                    "Remove unsupported gateway fields, validate the configuration, then retry."
                )
                break
            if isinstance(item, ValidationError):
                paths = sorted(
                    {
                        ".".join(str(part) for part in detail["loc"])
                        for detail in item.errors(
                            include_input=False, include_context=False, include_url=False
                        )
                        if detail["loc"]
                        and all(isinstance(part, int) or part in allowed for part in detail["loc"])
                    }
                )
                if paths:
                    reason = "Configuration validation failed at: " + ", ".join(paths) + "."
                if any(
                    detail.get("msg")
                    == "Value error, gateway_group.initial_security_group_ids was renamed to gateway_group.security_group_ids; apply now reconciles existing attachments"
                    for detail in item.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                ):
                    reason = "gateway_group.initial_security_group_ids was renamed to gateway_group.security_group_ids."
                break
    replacement = invocation.repair and any(
        isinstance(item, RepairBlocked) and item.replacement_applicable
        for item in error_chain(error)
    )
    if invocation.sa and invocation.command != "pair apply":
        prerequisite += (
            " Repair uses the current operator authentication; verify its deployment access."
            " The --sa option is supported only for ordinary apply."
        )
    resume_command = invocation.command_line(
        invocation.command,
        repair=invocation.repair,
        resume=True,
        fresh_approval=code
        in {"approval-mismatch", "approval-state-changed", "approval-not-applicable"},
    )
    if code == "candidate-permissions-not-private" and not {"--force", "-f"}.intersection(
        invocation.resume_options
    ):
        resume_command += " --force"
    return Recovery(
        code,
        reason,
        action,
        resume_command,
        invocation.command_line("apply", repair=True),
        prerequisite,
        invocation.command_line("destroy") if replacement else None,
        invocation.command_line("apply") if replacement else None,
        cause_code,
    )
