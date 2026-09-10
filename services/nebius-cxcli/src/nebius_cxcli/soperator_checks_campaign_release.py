"""Keep campaign reservation release in the campaign's durable ownership ledger."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .slurm_jobs import (
    SlurmJobControlRecord,
    applied_slurm_held_job_records,
    slurm_job_control_is_held,
)


@dataclass
class CampaignChecksRelease:
    owner: str
    reservation: str
    load_events: Callable[[], tuple[Mapping[str, Any], ...]]
    record_event: Callable[[Mapping[str, Any]], None]
    present: Callable[[], bool]
    observe: Callable[[], Mapping[str, Any]]
    delete: Callable[[], None]
    read_job: Callable[[str], SlurmJobControlRecord | None]
    authority: Callable[[], object]

    def _event(self, proof: Mapping[str, Any], action: str) -> bool:
        if proof.get("owner") != self.owner or proof.get("reservation") != self.reservation:
            raise RuntimeError("campaign checks release owner changed")
        rows = [
            row
            for row in self.load_events()
            if row.get("action") == action and row.get("reservation_name") == self.reservation
        ]
        if any(row.get("checks_release_binding") != proof for row in rows):
            raise RuntimeError("campaign reservation release has a different acceptance binding")
        return bool(rows)

    def _record(self, proof: Mapping[str, Any], action: str) -> None:
        self.record_event(
            {
                "action": action,
                "reservation_name": self.reservation,
                "checks_release_binding": dict(proof),
            }
        )

    def verify(self, proof: Mapping[str, Any]) -> None:
        if not self._event(proof, "maintenance-reservation-delete-applied"):
            raise RuntimeError("campaign checks reservation release receipt is missing")
        if self.present():
            raise RuntimeError("released campaign reservation was recreated")

    def release(self, proof: Mapping[str, Any]) -> None:
        if self._event(proof, "maintenance-reservation-delete-applied"):
            self.verify(proof)
            return
        intended = self._event(proof, "maintenance-reservation-delete-intent")
        present = self.present()
        if not present and not intended:
            raise RuntimeError("campaign reservation disappeared without a release intent")
        # Releasing the barrier must not release an existing customer's held job.
        for held in applied_slurm_held_job_records(self.load_events()):
            live = self.read_job(held.job_id)
            if live is not None and (
                live.identity_sha256 != held.identity_sha256 or not slurm_job_control_is_held(live)
            ):
                raise RuntimeError("campaign held-job protection changed before schedule handoff")
        if present:
            observed = self.observe()
            if observed.get("fingerprint") != proof.get("fingerprint") or observed.get("users") != [
                "root"
            ]:
                raise RuntimeError("campaign checks reservation changed before release")
        if not intended:
            self._record(proof, "maintenance-reservation-delete-intent")
        if present:
            self.authority()
            self.delete()
        if self.present():
            raise RuntimeError("campaign checks reservation release is incomplete")
        self._record(proof, "maintenance-reservation-delete-applied")
