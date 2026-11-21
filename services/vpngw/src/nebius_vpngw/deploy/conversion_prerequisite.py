"""Compose existing ordinary owners before HA preparation, without new authority."""

from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path
from typing import Any

import click

from .apply_command import CommandJournal, ReplanReady
from .apply_plan import CommandPlan, Evidence, Outcome
from .apply_result import ApplyReport
from .ha_command import pending_prerequisites
from .ha_conversion import ConversionFailure
from .recovery import CURRENT, ApprovalDeclined, GuestFailure, Invocation
from .vm_ha_lifecycle import VMHALifecycleStatus, VMHALifecycleStore


def ha_owns(candidate: Path, identity: dict[str, str]) -> bool:
    """Prepared YAML is not ownership; read only the existing scoped authorities."""
    snapshot = VMHALifecycleStore(candidate).read_hardened(
        expected_project_id=identity["project"], expected_gateway_name=identity["gateway"]
    )
    lifecycle = snapshot.state if snapshot is not None else None
    journal = CommandJournal(identity["project"], identity["gateway"])
    if pending_prerequisites(journal, lifecycle=lifecycle) is not None:
        return True
    if lifecycle is not None and lifecycle.status not in {
        VMHALifecycleStatus.REMOVED,
        VMHALifecycleStatus.DESTROYED,
    }:
        return True
    state = journal.snapshot()
    transaction = state["transaction"] if state is not None else None
    if (
        transaction
        and lifecycle is None
        and any(
            effect.key == "runtime" and effect.owner == "ha-lifecycle"
            for effect in CommandPlan.parse(transaction["plan"]).effects
        )
    ):
        raise ConversionFailure("conversion-lifecycle-unavailable")
    return False


def repair_diagnosed(error: BaseException | None) -> bool:
    """Only authenticated, failed local verification can justify reconstruction."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, GuestFailure) and error.first is not None:
            return (error.first.phase, error.first.predicate) in {
                ("guest-verify", "guest-runtime-drift"),
                ("guest-verify", "guest-final-artifact"),
                ("guest-inspect", "guest-ordinary-route-state-invalid"),
            }
        error = error.__cause__
    return False


def ready(report: ApplyReport) -> bool:
    payload = report.mapping()
    return (
        report.result.outcome in {Outcome.APPLIED, Outcome.RESUMED, Outcome.UNCHANGED}
        and all(
            value is Evidence.READY
            for value in (
                report.result.configuration,
                report.result.local_runtime,
                report.result.authority,
            )
        )
        and report.result.inhibition_retained is False
        and not payload.get("pending")
        and not report.cancelled
        and not report.projection_failed
    )


def run(api: Any, report: ApplyReport, *, source: Path, options: dict, interactive: bool) -> bool:
    """Return whether ordinary effects changed the HA approval predecessor."""
    from .ha_convergence import ReportedExit

    context = CURRENT.get()
    previous_repair_config = context.repair_config if context is not None else None
    if context is not None:
        context.repair_config = source

    def execute(*, repair: bool = False) -> tuple[ApplyReport, BaseException | None]:
        child = ApplyReport(dry_run=bool(options.get("dry_run")))
        ordinary = {
            "local_config_file": source,
            "project_id": options.get("project_id"),
            "region": options.get("region"),
            "dry_run": child.dry_run,
            "approve_disruption": None if repair else options.get("approve_disruption"),
            "repair": repair,
            "recreate_gw": False,
            "sa": None,
            "resume_replacement": None,
            "prepare_vm_ha_peer_rotation": False,
            "approve_vm_ha_migration": None,
            "recover_vm_ha_migration": None,
            "replace_failed_vm_ha_passive": None,
            "replace_missing_vm_ha_standby": None,
            "result_report": child,
        }
        repair_context = (
            replace(
                context or Invocation("apply", source),
                config=source,
                repair_config=source,
                repair=True,
                resume_options=(),
                dry_run=False,
            )
            if repair
            else None
        )
        token = CURRENT.set(repair_context) if repair_context is not None else None
        try:
            api._apply_impl(**ordinary)
        except ReplanReady:
            raise
        except ApprovalDeclined as error:
            child.cancelled = True
            return child, error
        except click.exceptions.Exit as error:
            if error.exit_code:
                child.failed(error)
            return child, error
        except (Exception, KeyboardInterrupt) as error:
            child.failed(error)
            return child, error
        finally:
            if token is not None:
                CURRENT.reset(token)
        return child, None

    def stop(child: ApplyReport, error: BaseException | None) -> None:
        # The outer command alone renders and owns the lock. Preserve the ordinary
        # journal/approval only when stopping here, never as HA progress evidence.
        for item in fields(child):
            setattr(report, item.name, getattr(child, item.name))
        planned = child.dry_run and (
            error is None or isinstance(error, click.exceptions.Exit) and error.exit_code == 0
        )
        code = (
            130
            if isinstance(error, KeyboardInterrupt)
            else (0 if child.cancelled or planned else 1)
        )
        raise ReportedExit(code) from error

    child, error = execute()
    changed = child.started or child.result.outcome in {Outcome.APPLIED, Outcome.RESUMED}
    if not ready(child):
        if not child.dry_run and interactive and repair_diagnosed(error):
            api.typer.echo(
                "Ordinary upgrade failed local software verification. "
                "Inspecting the explicitly confirmed repair path before HA conversion.",
                err=True,
            )
            repaired, repair_error = execute(repair=True)
            if not ready(repaired):
                stop(repaired, repair_error)
            changed = True
            # Repair completion is not a substitute for ordinary package, runtime
            # and route proof under the original source configuration.
            options = {**options, "approve_disruption": None}
            child, error = execute()
        if not ready(child):
            stop(child, error)
    if context is not None:
        context.repair_config = previous_repair_config
        if changed:
            context.consume_approval()
    return changed
