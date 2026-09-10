"""Checks belong to the full-stack maintenance boundary, not its release child."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .slurm_jobs import SlurmPartitionPauseRecord
from .soperator_checks import SoperatorChecksExecution
from .soperator_checks_handoff import ChecksScheduleHandoff
from .soperator_checks_lifecycle import ChecksLifecycle
from .soperator_checks_phase import ChecksPhaseContext
from .soperator_checks_policy import SoperatorChecksPolicy, compile_checks_policy
from .soperator_passive_checks import PassiveDiagnostics


class SoperatorCampaignChecks:
    def __init__(
        self,
        *,
        operation_id: str,
        reports_dir: Path,
        source_dir: Path,
        cluster_name: str,
        kubernetes: Callable[[list[str], Mapping[str, Any] | None], Mapping[str, Any]],
        slurm: Callable[[str], str],
        assert_authority: Callable[[], object],
        load_target: Callable[[], tuple[SoperatorChecksPolicy, tuple[str, ...], tuple[str, ...]]],
        apply_target: Callable[[SoperatorChecksPolicy, ChecksPhaseContext], None],
        partition_preimages: Callable[[], tuple[SlurmPartitionPauseRecord, ...]],
        reservation: str,
        emit: Callable[[str], object],
        target_writers: Callable[[], tuple[tuple[str, str], ...]],
        release_maintenance: Callable[[Mapping[str, Any]], None],
        verify_maintenance_released: Callable[[Mapping[str, Any]], None],
        recover_target: Callable[[SoperatorChecksExecution], object],
    ) -> None:
        self.operation_id = operation_id
        self.reports_dir = reports_dir
        self.kube = kubernetes
        self.slurm = slurm
        self.authority = assert_authority
        self.load_target = load_target
        self.apply_target = apply_target
        self.partition_preimages = partition_preimages
        self.reservation = reservation
        self.emit = emit
        self.target_writers = target_writers
        self.release_maintenance = release_maintenance
        self.verify_maintenance_released = verify_maintenance_released
        self.recover_target = recover_target
        source_policy = compile_checks_policy(
            source_dir,
            {
                "soperatorActiveChecks": {"enabled": True},
                "slurmCluster": {"overrideValues": {"clusterName": cluster_name}},
            },
        )
        self.source = self._execution(source_policy, "source")

    def _execution(self, policy: SoperatorChecksPolicy, phase: str) -> SoperatorChecksExecution:
        execution = SoperatorChecksExecution(
            policy=policy,
            operation_id=self.operation_id + ":" + phase,
            receipt_path=self.reports_dir
            / (
                "soperator-campaign-checks-"
                + self.operation_id.removeprefix("sha256:")[:24]
                + "-"
                + phase
                + ".json"
            ),
            kubernetes=self.kube,
            slurm=self.slurm,
            assert_authority=self.authority,
            handoff_owner=self.operation_id,
            emit=self.emit,
        )
        if phase == "target":
            execution.lifecycle = ChecksLifecycle(
                execution,
                lambda context: self.apply_target(policy, context),
                preimages=self.partition_preimages,
            )
        return execution

    def pause_source_passive(self) -> None:
        self.source.prepare_reservation(self.reservation, installing=False)
        if self.source.state.get("targetApplyIntent"):
            return
        PassiveDiagnostics(self.source).pause_source()

    def authorize_admission(self) -> None:
        policy, _, _ = self.load_target()
        target = self._execution(policy, "target")
        target.require_lifecycle().authorize_admission()

    def finish_admission(self) -> None:
        policy, _, _ = self.load_target()
        target = self._execution(policy, "target")
        target.require_lifecycle().finish()

    def enter(self, record: Callable[[Mapping[str, Any]], None]) -> None:
        self.emit("Quiescing upstream checks before campaign scheduling changes")
        result = self.source.quiesce_source()
        record(
            {
                "action": "upstream-checks-quiesced",
                "policySha256": self.source.policy.sha256,
                **result,
            }
        )

    def before_segment(self, segment: str) -> None:
        self.authority()
        if segment == "final-readiness":
            policy, _workers, _gpu_workers = self.load_target()
            target = self._execution(policy, "target")
            if target.state.get("scheduleRelease") or target.state.get("phase") in {
                "accepted",
                "restored",
            }:
                # An interrupted steady-policy apply may also leave later
                # releases suspended. Finish its authenticated handoff before
                # the parent requires the entire graph to be Ready again.
                self.finalize(already_released=target.state.get("phase") == "restored")
                return
        # Bind the original barrier before the first child mutation and retain
        # its complete resource/time contract across every later segment.
        self.source.prepare_reservation(self.reservation, installing=False)
        if segment == "soperator-release":
            if not self.source.state.get("targetApplyIntent"):
                self.source.quiesce_source()
                self.source.state["targetApplyIntent"] = True
                self.source._save()
            return
        policy, _workers, _gpu_workers = self.load_target()
        target = self._execution(policy, "target")
        target.verify_deferred_diagnostics(
            allow_acceptance=segment == "final-readiness"
            and target.state.get("phase") == "acceptance"
        )
        observed = target._reservation(self.reservation)
        allowed_users = [["root"]]
        if segment == "final-readiness" and target.state.get("phase") == "acceptance":
            allowed_users.append(["root", "soperatorchecks"])
        if observed["users"] not in allowed_users:
            raise RuntimeError(
                "campaign reservation authorizes diagnostics before final acceptance"
            )
        if segment == "final-readiness":
            # Replay any interrupted staged apply while maintenance still
            # excludes recurring diagnostics. Full graph and GPU validation
            # follow this boundary; fresh acceptance must follow both.
            target.prepare_reservation(self.reservation, installing=False)
            self.emit("Preparing the final deferred check policy before runtime validation")
            self.apply_target(policy, target.require_lifecycle().context())
            target.verify_deferred_diagnostics(
                allow_acceptance=target.state.get("phase") == "acceptance"
            )
            self.source.complete_source_handoff(self.target_writers())

    def finalize(self, *, already_released: bool) -> Mapping[str, Any]:
        policy, workers, gpu_workers = self.load_target()
        target = self._execution(policy, "target")
        handoff = self._handoff(target)
        if already_released:
            if target.state.get("phase") != "restored" or not self._policy_restored(target):
                raise RuntimeError("completed campaign has no restored check acceptance evidence")
            handoff.verify()
            return target.verify_acceptance()
        if target.state.get("phase") not in {"accepted", "restored"}:
            target.verify_deferred_diagnostics(
                allow_acceptance=target.state.get("phase") == "acceptance"
            )
            target._reservation(self.reservation)
            self.emit("Running fresh upstream diagnostics after final runtime validation")
            target.require_lifecycle().before_acceptance()
            target.accept(reservation=self.reservation, workers=workers, gpu_workers=gpu_workers)
        self.recover_target(target)
        self.emit("Releasing accepted maintenance before restoring upstream check schedules")
        handoff.restore(
            apply_policy=target.require_lifecycle().schedules,
            policy_restored=lambda: self._policy_restored(target),
            complete_source_handoff=lambda: self.source.complete_source_handoff(
                self.target_writers()
            ),
        )
        self.verify_handoff()
        return target.verify_acceptance()

    def _handoff(self, target: SoperatorChecksExecution) -> ChecksScheduleHandoff:
        return ChecksScheduleHandoff(
            target,
            owner=self.operation_id,
            release=self.release_maintenance,
            verify_released=self.verify_maintenance_released,
            admission=target.require_lifecycle().admission,
        )

    @staticmethod
    def _policy_restored(target: SoperatorChecksExecution) -> bool:
        from .soperator_checks_scheduling import scheduling_inventory

        return scheduling_inventory(target, deferred=False) is not None

    def verify_handoff(self) -> None:
        policy, _workers, _gpu_workers = self.load_target()
        target = self._execution(policy, "target")
        if target.state.get("phase") != "restored" or not self._policy_restored(target):
            raise RuntimeError("campaign checks policy restoration is incomplete")
        target.verify_acceptance()
        self.verify_barrier()

    def verify_barrier(self) -> None:
        policy, _workers, _gpu_workers = self.load_target()
        target = self._execution(policy, "target")
        if target.state.get("phase") != "restored":
            raise RuntimeError("campaign checks have not restored their temporary authorization")
        self._handoff(target).verify()
        target.require_lifecycle().admission.verify()
