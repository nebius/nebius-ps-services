"""Frozen source diagnostics stay separate from target maintenance overlays."""

from pathlib import Path

from .soperator_checks import SoperatorChecksExecution
from .soperator_checks_policy import compile_checks_policy
from .soperator_passive_checks import PassiveDiagnostics


class SourceChecksMaintenance:
    def __init__(self, target: SoperatorChecksExecution, source_dir: Path, cluster_name: str):
        self.checks = SoperatorChecksExecution(
            policy=compile_checks_policy(
                source_dir,
                {
                    "soperatorActiveChecks": {"enabled": True},
                    "slurmCluster": {"overrideValues": {"clusterName": cluster_name}},
                },
            ),
            operation_id=target.state["operation"] + ":source",
            receipt_path=target.path.with_name(target.path.stem + "-source.json"),
            kubernetes=target.kube,
            slurm=target.slurm,
            assert_authority=target.authority,
            emit=target.emit,
        )

    def quiesce(self) -> None:
        if not self.checks.state.get("targetApplyIntent"):
            self.checks.quiesce_source()

    def pause_passive(self, reservation: str) -> None:
        if not self.checks.state.get("targetApplyIntent"):
            self.checks.prepare_reservation(reservation, installing=False)
            PassiveDiagnostics(self.checks).pause_source()

    def before_target_apply(self) -> None:
        if not self.checks.state.get("targetApplyIntent"):
            self.checks.state["targetApplyIntent"] = True
            self.checks._save()

    def complete(self, writers: tuple[tuple[str, str], ...]) -> object:
        return self.checks.complete_source_handoff(writers)
