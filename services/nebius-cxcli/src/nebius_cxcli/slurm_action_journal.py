"""Durable Slurm action intent records for upgrade-time controller gaps."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

SLURM_ACTION_JOURNAL_SCHEMA = "nebius-cxcli-slurm-action-journal/v1"

SLURM_ACTION_QUEUED = "Queued"
SLURM_ACTION_DISPATCHING = "Dispatching"
SLURM_ACTION_APPLIED = "Applied"
SLURM_ACTION_REJECTED = "Rejected"
SLURM_ACTION_INDETERMINATE = "Indeterminate"
SLURM_ACTION_ADMISSION_CLOSED = "admission-closed"
SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY = "confirmed-voluntary-exit"
SLURM_LOGIN_EXIT_TIMEOUT_CONTINUATION = (
    "operator-authorized-continuation-after-involuntary-timeout"
)
SLURM_LOGIN_EXIT_DISPOSITIONS = frozenset(
    {
        SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY,
        SLURM_LOGIN_EXIT_TIMEOUT_CONTINUATION,
    }
)

SLURM_ACTION_STATES = frozenset(
    {
        SLURM_ACTION_QUEUED,
        SLURM_ACTION_DISPATCHING,
        SLURM_ACTION_APPLIED,
        SLURM_ACTION_REJECTED,
        SLURM_ACTION_INDETERMINATE,
    }
)
SLURM_ACTION_TERMINAL_STATES = frozenset({SLURM_ACTION_APPLIED, SLURM_ACTION_REJECTED})
SLURM_ACTION_RESULT_STATES = frozenset(
    {SLURM_ACTION_APPLIED, SLURM_ACTION_REJECTED, SLURM_ACTION_INDETERMINATE}
)
SLURM_ACTION_BLOCKING_STATES = frozenset(
    {SLURM_ACTION_QUEUED, SLURM_ACTION_DISPATCHING, SLURM_ACTION_INDETERMINATE}
)
SLURM_ACTION_KINDS = frozenset(
    {"cancel", "requeue", "hold", "requeue-hold", "release", "wait", "refresh"}
)
SLURM_MUTATING_ACTION_KINDS = frozenset({"cancel", "requeue", "hold", "requeue-hold", "release"})
SLURM_ACTION_BROKER_MODES = frozenset(
    {"dispatch-enabled", "accept-only", SLURM_ACTION_ADMISSION_CLOSED}
)
SLURM_LOGIN_OBSERVATION_STATES = frozenset(
    {
        "unknown",
        "protected",
        "target-ready",
        "pending-voluntary-exit",
        "complete",
        "indeterminate",
    }
)

_ALLOWED_TRANSITIONS: Mapping[str, frozenset[str]] = {
    SLURM_ACTION_QUEUED: frozenset({SLURM_ACTION_DISPATCHING, SLURM_ACTION_REJECTED}),
    SLURM_ACTION_DISPATCHING: frozenset(
        {SLURM_ACTION_APPLIED, SLURM_ACTION_REJECTED, SLURM_ACTION_INDETERMINATE}
    ),
    SLURM_ACTION_APPLIED: frozenset(),
    SLURM_ACTION_REJECTED: frozenset(),
    SLURM_ACTION_INDETERMINATE: frozenset({SLURM_ACTION_APPLIED, SLURM_ACTION_REJECTED}),
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _required_text(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be non-empty.")
    return text


def _non_negative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer.")
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer.") from exc
    if result < 0:
        raise ValueError(f"{field} must be a non-negative integer.")
    return result


@dataclass(frozen=True)
class SlurmJobActionBinding:
    """Immutable identity used to prevent JobID reuse from retargeting an action."""

    job_id: str
    user_id: str
    submit_time: str
    restart_baseline: int
    identity_fingerprint: str
    lineage_fingerprint: str

    def __post_init__(self) -> None:
        for field in (
            "job_id",
            "user_id",
            "submit_time",
            "identity_fingerprint",
            "lineage_fingerprint",
        ):
            object.__setattr__(
                self,
                field,
                _required_text(getattr(self, field), field=f"Slurm action binding {field}"),
            )
        object.__setattr__(
            self,
            "restart_baseline",
            _non_negative_int(
                self.restart_baseline,
                field="Slurm action binding restart_baseline",
            ),
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "user_id": self.user_id,
            "submit_time": self.submit_time,
            "restart_baseline": self.restart_baseline,
            "identity_fingerprint": self.identity_fingerprint,
            "lineage_fingerprint": self.lineage_fingerprint,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> SlurmJobActionBinding:
        return cls(
            job_id=str(payload.get("job_id", "") or ""),
            user_id=str(payload.get("user_id", "") or ""),
            submit_time=str(payload.get("submit_time", "") or ""),
            restart_baseline=_non_negative_int(
                payload.get("restart_baseline"),
                field="Slurm action binding restart_baseline",
            ),
            identity_fingerprint=str(payload.get("identity_fingerprint", "") or ""),
            lineage_fingerprint=str(payload.get("lineage_fingerprint", "") or ""),
        )


def new_slurm_action_journal() -> dict[str, Any]:
    created_at = _utc_now()
    return {
        "schema": SLURM_ACTION_JOURNAL_SCHEMA,
        "broker_mode": "accept-only",
        "broker_mode_updated_at": created_at,
        "active_authority_generation": 0,
        "authority_generation_updated_at": created_at,
        "authority_generations": [
            {
                "generation": 0,
                "authority": "upgrade",
                "authority_epoch": "",
                "opened_at": created_at,
            }
        ],
        "partition_restore_boundary": {},
        "target_finalization_boundary": {},
        "controller": {
            "authority": "unknown",
            "connectivity": "unknown",
            "authority_epoch": "",
            "observed_at": "",
            "snapshot_age_seconds": None,
        },
        "login": {
            "state": "unknown",
            "protected_pod_count": 0,
            "active_session_count": 0,
            "target_ready": False,
            "exit_confirmation_requests": [],
            "exit_acknowledgements": [],
            "observed_at": "",
        },
        "actions": [],
    }


def ensure_slurm_action_journal(checkpoint: MutableMapping[str, Any]) -> dict[str, Any]:
    raw_slurm = checkpoint.setdefault("slurm", {})
    if not isinstance(raw_slurm, MutableMapping):
        raise ValueError("External upgrade checkpoint slurm section must be a mapping.")
    raw_journal = raw_slurm.get("action_journal")
    if raw_journal is None:
        journal = new_slurm_action_journal()
        raw_slurm["action_journal"] = journal
        return journal
    if not isinstance(raw_journal, dict):
        raise ValueError("Slurm action journal must be a mutable mapping.")
    validate_slurm_action_journal(raw_journal)
    return raw_journal


def validate_slurm_action_journal(journal: Mapping[str, Any]) -> None:
    if journal.get("schema") != SLURM_ACTION_JOURNAL_SCHEMA:
        raise ValueError("Slurm action journal schema mismatch; no legacy conversion is supported.")
    broker_mode = str(journal.get("broker_mode", "") or "")
    if broker_mode not in SLURM_ACTION_BROKER_MODES:
        raise ValueError("Slurm action journal broker_mode is invalid.")
    _required_text(
        journal.get("broker_mode_updated_at"),
        field="Slurm action journal broker_mode_updated_at",
    )
    active_generation = _non_negative_int(
        journal.get("active_authority_generation"),
        field="Slurm action journal active_authority_generation",
    )
    _required_text(
        journal.get("authority_generation_updated_at"),
        field="Slurm action journal authority_generation_updated_at",
    )
    generations = journal.get("authority_generations")
    if not isinstance(generations, Sequence) or isinstance(generations, (str, bytes, bytearray)):
        raise ValueError("Slurm action journal authority_generations must be a list.")
    if len(generations) != active_generation + 1:
        raise ValueError(
            "Slurm action journal authority_generations must be contiguous through the active "
            "generation."
        )
    for expected_generation, raw_generation in enumerate(generations):
        if not isinstance(raw_generation, Mapping):
            raise ValueError(
                f"Slurm action journal authority generation {expected_generation} must be a "
                "mapping."
            )
        if (
            _non_negative_int(
                raw_generation.get("generation"),
                field=f"Slurm action journal authority generation {expected_generation}",
            )
            != expected_generation
        ):
            raise ValueError("Slurm action journal authority generations must be contiguous.")
        _required_text(
            raw_generation.get("authority"),
            field=f"Slurm action journal authority generation {expected_generation} authority",
        )
        if not isinstance(raw_generation.get("authority_epoch", ""), str):
            raise ValueError(
                f"Slurm action journal authority generation {expected_generation} epoch must be "
                "a string."
            )
        _required_text(
            raw_generation.get("opened_at"),
            field=f"Slurm action journal authority generation {expected_generation} opened_at",
        )

    restore_boundary = journal.get("partition_restore_boundary")
    if not isinstance(restore_boundary, Mapping):
        raise ValueError("Slurm action journal partition_restore_boundary must be a mapping.")
    if restore_boundary:
        sealed_generation = _non_negative_int(
            restore_boundary.get("sealed_generation"),
            field="Slurm partition restore sealed_generation",
        )
        target_generation = _non_negative_int(
            restore_boundary.get("target_generation"),
            field="Slurm partition restore target_generation",
        )
        if (
            target_generation != sealed_generation + 1
            or target_generation != active_generation
            or str(generations[target_generation].get("authority") or "") != "target-singleton"
        ):
            raise ValueError(
                "Slurm partition restore boundary must seal the generation immediately before "
                "the active target-singleton generation."
            )
        target_epoch = _required_text(
            restore_boundary.get("target_authority_epoch"),
            field="Slurm partition restore target_authority_epoch",
        )
        if str(generations[target_generation].get("authority_epoch") or "") != target_epoch:
            raise ValueError(
                "Slurm partition restore target authority epoch differs from its generation."
            )
        _required_text(
            restore_boundary.get("sealed_at"),
            field="Slurm partition restore sealed_at",
        )
        _non_negative_int(
            restore_boundary.get("sealed_action_journal_generation"),
            field="Slurm partition restore sealed_action_journal_generation",
        )
    finalization_boundary = journal.get("target_finalization_boundary")
    if not isinstance(finalization_boundary, Mapping):
        raise ValueError("Slurm action journal target_finalization_boundary must be a mapping.")
    if finalization_boundary:
        if not restore_boundary:
            raise ValueError(
                "Slurm target action finalization requires the partition restore boundary."
            )
        target_generation = _non_negative_int(
            finalization_boundary.get("target_authority_generation"),
            field="Slurm target finalization target_authority_generation",
        )
        if target_generation != active_generation or target_generation != int(
            restore_boundary["target_generation"]
        ):
            raise ValueError(
                "Slurm target action finalization must bind the active target generation."
            )
        target_epoch = _required_text(
            finalization_boundary.get("target_authority_epoch"),
            field="Slurm target finalization target_authority_epoch",
        )
        if target_epoch != str(restore_boundary["target_authority_epoch"]):
            raise ValueError(
                "Slurm target action finalization authority epoch differs from the target "
                "generation."
            )
        _required_text(
            finalization_boundary.get("finalized_at"),
            field="Slurm target finalization finalized_at",
        )
        _non_negative_int(
            finalization_boundary.get("target_action_journal_generation"),
            field="Slurm target finalization target_action_journal_generation",
        )
        if broker_mode != SLURM_ACTION_ADMISSION_CLOSED:
            raise ValueError(
                "Slurm target action finalization requires irreversibly closed admission."
            )
    controller = journal.get("controller")
    if not isinstance(controller, Mapping):
        raise ValueError("Slurm action journal controller observation must be a mapping.")
    for field in ("authority", "connectivity", "authority_epoch", "observed_at"):
        if not isinstance(controller.get(field, ""), str):
            raise ValueError(f"Slurm action journal controller {field} must be a string.")
    snapshot_age = controller.get("snapshot_age_seconds")
    if snapshot_age is not None:
        _non_negative_int(snapshot_age, field="controller snapshot_age_seconds")

    login = journal.get("login")
    if not isinstance(login, Mapping):
        raise ValueError("Slurm action journal login observation must be a mapping.")
    if str(login.get("state", "") or "") not in SLURM_LOGIN_OBSERVATION_STATES:
        raise ValueError("Slurm action journal login state is invalid.")
    for field in ("protected_pod_count", "active_session_count"):
        _non_negative_int(login.get(field), field=f"login {field}")
    if not isinstance(login.get("target_ready"), bool):
        raise ValueError("Slurm action journal login target_ready must be boolean.")
    if not isinstance(login.get("observed_at", ""), str):
        raise ValueError("Slurm action journal login observed_at must be a string.")
    requests = login.get("exit_confirmation_requests")
    if not isinstance(requests, Sequence) or isinstance(requests, (str, bytes, bytearray)):
        raise ValueError("Slurm action journal login exit_confirmation_requests must be a list.")
    request_keys: set[tuple[str, str]] = set()
    for index, request in enumerate(requests):
        if not isinstance(request, Mapping):
            raise ValueError(f"Slurm login exit confirmation request {index} must be a mapping.")
        fingerprint = _required_text(
            request.get("socket_fingerprint"),
            field=f"Slurm login exit confirmation request {index} socket_fingerprint",
        )
        if len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise ValueError(
                f"Slurm login exit confirmation request {index} socket_fingerprint "
                "must be lowercase SHA256."
            )
        absent_at = _required_text(
            request.get("absence_observed_at"),
            field=f"Slurm login exit confirmation request {index} absence_observed_at",
        )
        key = (fingerprint, absent_at)
        if key in request_keys:
            raise ValueError("Slurm login exit confirmation requests must be unique.")
        request_keys.add(key)
    acknowledgements = login.get("exit_acknowledgements")
    if not isinstance(acknowledgements, Sequence) or isinstance(
        acknowledgements, (str, bytes, bytearray)
    ):
        raise ValueError("Slurm action journal login exit_acknowledgements must be a list.")
    acknowledgement_keys: set[tuple[str, str]] = set()
    for index, acknowledgement in enumerate(acknowledgements):
        if not isinstance(acknowledgement, Mapping):
            raise ValueError(f"Slurm login exit acknowledgement {index} must be a mapping.")
        fingerprint = _required_text(
            acknowledgement.get("socket_fingerprint"),
            field=f"Slurm login exit acknowledgement {index} socket_fingerprint",
        )
        if len(fingerprint) != 64 or any(
            character not in "0123456789abcdef" for character in fingerprint
        ):
            raise ValueError(
                f"Slurm login exit acknowledgement {index} socket_fingerprint "
                "must be lowercase SHA256."
            )
        absent_at = _required_text(
            acknowledgement.get("absence_observed_at"),
            field=f"Slurm login exit acknowledgement {index} absence_observed_at",
        )
        _required_text(
            acknowledgement.get("acknowledged_at"),
            field=f"Slurm login exit acknowledgement {index} acknowledged_at",
        )
        _required_text(
            acknowledgement.get("acknowledged_by"),
            field=f"Slurm login exit acknowledgement {index} acknowledged_by",
        )
        disposition = str(
            acknowledgement.get("disposition") or SLURM_LOGIN_EXIT_CONFIRMED_VOLUNTARY
        )
        if disposition not in SLURM_LOGIN_EXIT_DISPOSITIONS:
            raise ValueError(
                f"Slurm login exit acknowledgement {index} disposition is invalid."
            )
        key = (fingerprint, absent_at)
        if key in acknowledgement_keys:
            raise ValueError("Slurm login exit acknowledgements must be unique.")
        acknowledgement_keys.add(key)

    actions = journal.get("actions")
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes, bytearray)):
        raise ValueError("Slurm action journal actions must be a list.")
    seen_action_ids: set[str] = set()
    active_mutation_by_binding: dict[tuple[str, str, str, int, str, str], str] = {}
    for index, raw_action in enumerate(actions):
        if not isinstance(raw_action, Mapping):
            raise ValueError(f"Slurm action record {index} must be a mapping.")
        _validate_action_record(raw_action, index=index)
        action_id = str(raw_action.get("action_id", "") or "")
        if action_id in seen_action_ids:
            raise ValueError(f"Slurm action record {index} repeats action_id {action_id}.")
        seen_action_ids.add(action_id)
        if (
            raw_action.get("kind") in SLURM_MUTATING_ACTION_KINDS
            and raw_action.get("state") not in SLURM_ACTION_TERMINAL_STATES
        ):
            binding = SlurmJobActionBinding.from_payload(raw_action["binding"])
            binding_key = _binding_key(binding)
            existing_action_id = active_mutation_by_binding.get(binding_key)
            if existing_action_id is not None:
                raise ValueError(
                    "Slurm action journal has multiple active mutating actions for immutable "
                    f"job binding {binding.job_id}: {existing_action_id}, {action_id}."
                )
            active_mutation_by_binding[binding_key] = action_id
        action_generation = _non_negative_int(
            raw_action.get("authority_generation"),
            field=f"Slurm action record {index} authority_generation",
        )
        if action_generation > active_generation:
            raise ValueError(
                f"Slurm action record {index} belongs to a future authority generation."
            )
        if (
            restore_boundary
            and action_generation <= int(restore_boundary["sealed_generation"])
            and raw_action.get("state") in SLURM_ACTION_BLOCKING_STATES
        ):
            raise ValueError(
                "Slurm partition restore generation cannot be sealed while an old-generation "
                "action is Queued, Dispatching, or Indeterminate."
            )
    if restore_boundary:
        sealed_generation = int(restore_boundary["sealed_generation"])
        observed_generation = _slurm_action_transition_count(
            actions,
            maximum_authority_generation=sealed_generation,
        )
        if observed_generation != int(restore_boundary["sealed_action_journal_generation"]):
            raise ValueError(
                "Slurm partition restore action generation changed after it was sealed."
            )
    if finalization_boundary:
        target_generation = int(finalization_boundary["target_authority_generation"])
        observed_target_generation = _slurm_action_transition_count(
            actions,
            minimum_authority_generation=target_generation,
            maximum_authority_generation=target_generation,
        )
        if observed_target_generation != int(
            finalization_boundary["target_action_journal_generation"]
        ):
            raise ValueError("Slurm target action generation changed after finalization.")
        if _slurm_action_blockers(actions):
            raise ValueError(
                "Slurm target action generation cannot be finalized while Queued, "
                "Dispatching, or Indeterminate actions remain."
            )


def _validate_action_record(action: Mapping[str, Any], *, index: int) -> None:
    prefix = f"Slurm action record {index}"
    for field in ("action_id", "batch_id", "origin", "accepted_at"):
        _required_text(action.get(field), field=f"{prefix} {field}")
    _non_negative_int(action.get("authority_generation"), field=f"{prefix} authority_generation")
    kind = str(action.get("kind", "") or "")
    if kind not in SLURM_ACTION_KINDS:
        raise ValueError(f"{prefix} kind is invalid.")
    state = str(action.get("state", "") or "")
    if state not in SLURM_ACTION_STATES:
        raise ValueError(f"{prefix} state is invalid.")
    binding = action.get("binding")
    if not isinstance(binding, Mapping):
        raise ValueError(f"{prefix} binding must be a mapping.")
    SlurmJobActionBinding.from_payload(binding)
    history = action.get("transitions")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        raise ValueError(f"{prefix} transitions must be a list.")
    if not history:
        raise ValueError(f"{prefix} transitions must contain the queued transition.")
    previous_state = ""
    for transition_index, raw_transition in enumerate(history):
        if not isinstance(raw_transition, Mapping):
            raise ValueError(f"{prefix} transition {transition_index} must be a mapping.")
        transition_state = str(raw_transition.get("state", "") or "")
        if transition_state not in SLURM_ACTION_STATES:
            raise ValueError(f"{prefix} transition {transition_index} state is invalid.")
        _required_text(raw_transition.get("at"), field=f"{prefix} transition {transition_index} at")
        if transition_index == 0 and transition_state != SLURM_ACTION_QUEUED:
            raise ValueError(f"{prefix} must begin in {SLURM_ACTION_QUEUED} state.")
        if previous_state and transition_state not in _ALLOWED_TRANSITIONS[previous_state]:
            raise ValueError(
                f"{prefix} has invalid transition {previous_state} -> {transition_state}."
            )
        previous_state = transition_state
    if previous_state != state:
        raise ValueError(f"{prefix} state does not match its transition history.")
    if (
        state == SLURM_ACTION_DISPATCHING
        and not str(action.get("dispatched_authority_epoch", "") or "").strip()
    ):
        raise ValueError(f"{prefix} dispatching state requires an authority epoch.")
    if state in SLURM_ACTION_RESULT_STATES and not str(action.get("result", "") or "").strip():
        raise ValueError(f"{prefix} result-bearing state requires a result.")


def set_slurm_action_broker_mode(journal: MutableMapping[str, Any], mode: str) -> None:
    if mode not in SLURM_ACTION_BROKER_MODES:
        raise ValueError(f"Unsupported Slurm action broker mode: {mode}.")
    current = str(journal.get("broker_mode", "") or "")
    if current == SLURM_ACTION_ADMISSION_CLOSED and mode != current:
        raise ValueError("Slurm action admission closure is irreversible for this upgrade.")
    if mode == SLURM_ACTION_ADMISSION_CLOSED:
        validate_slurm_action_journal(journal)
        if current not in {"accept-only", SLURM_ACTION_ADMISSION_CLOSED}:
            raise ValueError(
                "Slurm action admission closes only from the durable accept-only finalization "
                "boundary."
            )
    if journal.get("broker_mode") != mode:
        journal["broker_mode_updated_at"] = _utc_now()
    journal["broker_mode"] = mode


def record_slurm_controller_observation(
    journal: MutableMapping[str, Any],
    *,
    authority: str,
    connectivity: str,
    authority_epoch: str,
    snapshot_age_seconds: int | None,
    observed_at: str | None = None,
) -> None:
    journal["controller"] = {
        "authority": _required_text(authority, field="controller authority"),
        "connectivity": _required_text(connectivity, field="controller connectivity"),
        "authority_epoch": str(authority_epoch or "").strip(),
        "observed_at": observed_at or _utc_now(),
        "snapshot_age_seconds": (
            None
            if snapshot_age_seconds is None
            else _non_negative_int(
                snapshot_age_seconds,
                field="controller snapshot_age_seconds",
            )
        ),
    }


def record_slurm_login_observation(
    journal: MutableMapping[str, Any],
    *,
    state: str,
    protected_pod_count: int,
    active_session_count: int,
    target_ready: bool,
    exit_confirmation_requests: Sequence[Mapping[str, str]] = (),
    observed_at: str | None = None,
) -> None:
    """Record sanitized login-continuity state and exact exit-confirmation tokens."""

    normalized_state = str(state or "").strip()
    if normalized_state not in SLURM_LOGIN_OBSERVATION_STATES:
        raise ValueError(f"Unsupported Slurm login observation state: {state}.")
    if not isinstance(target_ready, bool):
        raise ValueError("Slurm login observation target_ready must be boolean.")
    existing_login = journal.get("login")
    acknowledgements = (
        list(existing_login.get("exit_acknowledgements", ()))
        if isinstance(existing_login, Mapping)
        else []
    )
    journal["login"] = {
        "state": normalized_state,
        "protected_pod_count": _non_negative_int(
            protected_pod_count,
            field="login protected_pod_count",
        ),
        "active_session_count": _non_negative_int(
            active_session_count,
            field="login active_session_count",
        ),
        "target_ready": target_ready,
        "exit_confirmation_requests": [dict(item) for item in exit_confirmation_requests],
        "exit_acknowledgements": acknowledgements,
        "observed_at": observed_at or _utc_now(),
    }
    validate_slurm_action_journal(journal)


def acknowledge_slurm_login_exit(
    journal: MutableMapping[str, Any],
    *,
    socket_fingerprint: str,
    acknowledged_by: str,
    disposition: str,
    acknowledged_at: str | None = None,
) -> dict[str, Any]:
    """Acknowledge one currently absent protected SSH socket by exact fingerprint."""

    validate_slurm_action_journal(journal)
    fingerprint = str(socket_fingerprint or "").strip().lower()
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint
    ):
        raise ValueError("Login exit acknowledgement requires a lowercase SHA256 fingerprint.")
    actor = _required_text(acknowledged_by, field="login exit acknowledged_by")
    normalized_disposition = str(disposition or "").strip()
    if normalized_disposition not in SLURM_LOGIN_EXIT_DISPOSITIONS:
        raise ValueError("Login exit acknowledgement disposition is invalid.")
    login = journal.get("login")
    if not isinstance(login, MutableMapping):
        raise ValueError("Slurm action journal login observation must be mutable.")
    matching_requests = [
        request
        for request in login.get("exit_confirmation_requests", ())
        if isinstance(request, Mapping) and request.get("socket_fingerprint") == fingerprint
    ]
    if len(matching_requests) != 1:
        raise ValueError(
            "Login exit acknowledgement must match exactly one currently pending protected "
            "SSH socket fingerprint."
        )
    absence_observed_at = str(matching_requests[0].get("absence_observed_at") or "").strip()
    acknowledgements = login.get("exit_acknowledgements")
    if not isinstance(acknowledgements, list):
        raise ValueError("Slurm login exit acknowledgements must be mutable.")
    for acknowledgement in acknowledgements:
        if not isinstance(acknowledgement, Mapping):
            continue
        if (
            acknowledgement.get("socket_fingerprint") == fingerprint
            and acknowledgement.get("absence_observed_at") == absence_observed_at
        ):
            existing_disposition = str(acknowledgement.get("disposition") or "").strip()
            if existing_disposition and existing_disposition != normalized_disposition:
                raise ValueError(
                    "Login exit acknowledgement already has a conflicting disposition."
                )
            if not existing_disposition:
                # v1 acknowledgements predate explicit dispositions.  An exact
                # follow-up action may refine that same fingerprint/absence epoch
                # without erasing the original acknowledgement audit fields.
                acknowledgement["disposition"] = normalized_disposition
                acknowledgement["disposition_recorded_at"] = acknowledged_at or _utc_now()
                acknowledgement["disposition_recorded_by"] = actor
                validate_slurm_action_journal(journal)
            return dict(acknowledgement)
    timestamp = acknowledged_at or _utc_now()
    record = {
        "socket_fingerprint": fingerprint,
        "absence_observed_at": absence_observed_at,
        "acknowledged_at": timestamp,
        "acknowledged_by": actor,
        "disposition": normalized_disposition,
    }
    acknowledgements.append(record)
    login["observed_at"] = timestamp
    validate_slurm_action_journal(journal)
    return dict(record)


def _binding_key(binding: SlurmJobActionBinding) -> tuple[str, str, str, int, str, str]:
    return (
        binding.job_id,
        binding.user_id,
        binding.submit_time,
        binding.restart_baseline,
        binding.identity_fingerprint,
        binding.lineage_fingerprint,
    )


def enqueue_slurm_action(
    journal: MutableMapping[str, Any],
    *,
    kind: str,
    binding: SlurmJobActionBinding,
    intended_postcondition: Mapping[str, Any],
    origin: str = "operator",
    accepted_authority_epoch: str = "",
    batch_id: str | None = None,
    action_id: str | None = None,
    accepted_at: str | None = None,
) -> dict[str, Any]:
    validate_slurm_action_journal(journal)
    if journal.get("broker_mode") == SLURM_ACTION_ADMISSION_CLOSED:
        raise ValueError(
            "Slurm action admission is closed because final partition restoration has started."
        )
    normalized_kind = str(kind or "").strip()
    if normalized_kind not in SLURM_ACTION_KINDS:
        raise ValueError(f"Unsupported Slurm action kind: {kind}.")
    normalized_origin = _required_text(origin, field="Slurm action origin")
    actions = journal["actions"]
    if not isinstance(actions, list):
        raise ValueError("Slurm action journal actions must be mutable.")

    binding_key = _binding_key(binding)
    for raw_action in actions:
        if not isinstance(raw_action, Mapping):
            continue
        raw_binding = raw_action.get("binding")
        if not isinstance(raw_binding, Mapping):
            continue
        existing_binding = SlurmJobActionBinding.from_payload(raw_binding)
        existing_state = str(raw_action.get("state", "") or "")
        if existing_state in SLURM_ACTION_TERMINAL_STATES:
            continue
        if (
            existing_binding.job_id == binding.job_id
            and _binding_key(existing_binding) != binding_key
        ):
            raise ValueError(
                f"Slurm JobID {binding.job_id} was reused; the queued action binding no longer "
                "matches the live job."
            )
        if (
            normalized_kind in SLURM_MUTATING_ACTION_KINDS
            and str(raw_action.get("kind", "") or "") in SLURM_MUTATING_ACTION_KINDS
            and _binding_key(existing_binding) == binding_key
        ):
            raise ValueError(f"Slurm job {binding.job_id} already has an active mutating action.")

    timestamp = accepted_at or _utc_now()
    record = {
        "action_id": action_id or uuid4().hex,
        "batch_id": batch_id or uuid4().hex,
        "kind": normalized_kind,
        "origin": normalized_origin,
        "state": SLURM_ACTION_QUEUED,
        "authority_generation": _non_negative_int(
            journal.get("active_authority_generation"),
            field="Slurm action journal active_authority_generation",
        ),
        "binding": binding.as_payload(),
        "intended_postcondition": deepcopy(dict(intended_postcondition)),
        "accepted_authority_epoch": str(accepted_authority_epoch or "").strip(),
        "dispatched_authority_epoch": "",
        "accepted_at": timestamp,
        "result": "",
        "observed_postcondition": {},
        "transitions": [{"state": SLURM_ACTION_QUEUED, "at": timestamp, "detail": "accepted"}],
    }
    actions.append(record)
    return record


def transition_slurm_action(
    journal: MutableMapping[str, Any],
    *,
    action_id: str,
    state: str,
    authority_epoch: str = "",
    result: str = "",
    observed_postcondition: Mapping[str, Any] | None = None,
    detail: str = "",
    transitioned_at: str | None = None,
) -> dict[str, Any]:
    validate_slurm_action_journal(journal)
    normalized_action_id = _required_text(action_id, field="Slurm action_id")
    normalized_state = str(state or "").strip()
    if normalized_state not in SLURM_ACTION_STATES:
        raise ValueError(f"Unsupported Slurm action state: {state}.")
    actions = journal.get("actions")
    if not isinstance(actions, list):
        raise ValueError("Slurm action journal actions must be mutable.")
    record = next(
        (
            item
            for item in actions
            if isinstance(item, dict) and item.get("action_id") == normalized_action_id
        ),
        None,
    )
    if record is None:
        raise ValueError(f"Unknown Slurm action_id: {normalized_action_id}.")
    previous_state = str(record.get("state", "") or "")
    if normalized_state not in _ALLOWED_TRANSITIONS.get(previous_state, frozenset()):
        raise ValueError(f"Invalid Slurm action transition {previous_state} -> {normalized_state}.")
    if normalized_state == SLURM_ACTION_DISPATCHING:
        action_generation = _non_negative_int(
            record.get("authority_generation"),
            field="Slurm action authority_generation",
        )
        active_generation = _non_negative_int(
            journal.get("active_authority_generation"),
            field="Slurm action journal active_authority_generation",
        )
        if action_generation != active_generation:
            raise ValueError(
                "Slurm action belongs to a sealed authority generation and cannot be dispatched."
            )
        normalized_epoch = _required_text(
            authority_epoch,
            field="Slurm dispatch authority_epoch",
        )
        generation_records = journal.get("authority_generations")
        if not isinstance(generation_records, Sequence):  # pragma: no cover - validated above
            raise ValueError("Slurm action journal authority_generations must be a list.")
        raw_generation = generation_records[active_generation]
        if not isinstance(raw_generation, Mapping):  # pragma: no cover - validated above
            raise ValueError("Slurm action authority generation must be a mapping.")
        expected_epoch = str(raw_generation.get("authority_epoch") or "").strip()
        if expected_epoch and normalized_epoch != expected_epoch:
            raise ValueError(
                "Slurm dispatch authority epoch differs from the active target generation."
            )
        record["dispatched_authority_epoch"] = normalized_epoch
    if normalized_state in SLURM_ACTION_RESULT_STATES:
        record["result"] = _required_text(result, field="Slurm action result")
    record["state"] = normalized_state
    record["observed_postcondition"] = deepcopy(dict(observed_postcondition or {}))
    transition = {
        "state": normalized_state,
        "at": transitioned_at or _utc_now(),
        "detail": str(detail or result or "").strip(),
    }
    transitions = record.get("transitions")
    if not isinstance(transitions, list):
        raise ValueError("Slurm action transitions must be mutable.")
    transitions.append(transition)
    return record


def dispatchable_slurm_actions(journal: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    validate_slurm_action_journal(journal)
    if journal.get("broker_mode") != "dispatch-enabled":
        return ()
    active_generation = int(journal["active_authority_generation"])
    return tuple(
        action
        for action in journal.get("actions", [])
        if isinstance(action, Mapping)
        and action.get("state") == SLURM_ACTION_QUEUED
        and action.get("authority_generation") == active_generation
    )


def _slurm_action_transition_count(
    actions: Sequence[Any],
    *,
    minimum_authority_generation: int | None = None,
    maximum_authority_generation: int | None = None,
) -> int:
    generation = 0
    for action in actions:
        if not isinstance(action, Mapping):
            continue
        action_generation = action.get("authority_generation")
        if (
            minimum_authority_generation is not None
            and isinstance(action_generation, int)
            and action_generation < minimum_authority_generation
        ):
            continue
        if (
            maximum_authority_generation is not None
            and isinstance(action_generation, int)
            and action_generation > maximum_authority_generation
        ):
            continue
        transitions = action.get("transitions", [])
        if isinstance(transitions, Sequence) and not isinstance(
            transitions, (str, bytes, bytearray)
        ):
            generation += len(transitions)
    return generation


def slurm_action_journal_generation(journal: Mapping[str, Any]) -> int:
    """Return the monotonic generation of accepted intents and state transitions."""

    validate_slurm_action_journal(journal)
    return _slurm_action_transition_count(journal.get("actions", []))


def begin_target_slurm_action_generation(
    journal: MutableMapping[str, Any],
    *,
    authority_epoch: str,
) -> dict[str, Any]:
    """Seal drained upgrade actions and open target-singleton job control.

    The sealed generation is the immutable partition-restore proof. Actions accepted
    after this boundary remain dispatchable throughout the upgrade, until final action
    admission closes, and do not alter that proof.
    """

    validate_slurm_action_journal(journal)
    normalized_epoch = _required_text(
        authority_epoch,
        field="target Slurm action authority_epoch",
    )
    existing_boundary = journal.get("partition_restore_boundary")
    if isinstance(existing_boundary, Mapping) and existing_boundary:
        if existing_boundary.get("target_authority_epoch") != normalized_epoch:
            raise ValueError(
                "Slurm target authority epoch changed after the partition restore generation "
                "was opened."
            )
        if journal.get("broker_mode") != SLURM_ACTION_ADMISSION_CLOSED:
            set_slurm_action_broker_mode(journal, "dispatch-enabled")
        validate_slurm_action_journal(journal)
        return dict(existing_boundary)

    blockers = slurm_action_partition_restore_blockers(journal)
    if blockers:
        raise ValueError(
            "Slurm target authority generation opens only after every prior action is Applied "
            "or Rejected."
        )
    if journal.get("broker_mode") == SLURM_ACTION_ADMISSION_CLOSED:
        raise ValueError(
            "Slurm target authority generation cannot open after irreversible admission closure."
        )
    sealed_generation = int(journal["active_authority_generation"])
    target_generation = sealed_generation + 1
    generations = journal.get("authority_generations")
    if not isinstance(generations, list):
        raise ValueError("Slurm action journal authority_generations must be mutable.")
    timestamp = _utc_now()
    generations.append(
        {
            "generation": target_generation,
            "authority": "target-singleton",
            "authority_epoch": normalized_epoch,
            "opened_at": timestamp,
        }
    )
    boundary = {
        "sealed_generation": sealed_generation,
        "sealed_action_journal_generation": _slurm_action_transition_count(
            journal.get("actions", []),
            maximum_authority_generation=sealed_generation,
        ),
        "target_generation": target_generation,
        "target_authority_epoch": normalized_epoch,
        "sealed_at": timestamp,
    }
    journal["active_authority_generation"] = target_generation
    journal["authority_generation_updated_at"] = timestamp
    journal["partition_restore_boundary"] = boundary
    set_slurm_action_broker_mode(journal, "dispatch-enabled")
    validate_slurm_action_journal(journal)
    return dict(boundary)


def slurm_action_partition_restore_binding(journal: Mapping[str, Any]) -> dict[str, Any]:
    """Return the frozen pre-target action proof used by partition restoration."""

    validate_slurm_action_journal(journal)
    boundary = journal.get("partition_restore_boundary")
    if not isinstance(boundary, Mapping) or not boundary:
        raise ValueError("Slurm partition restore action generation has not been opened.")
    return {
        "action_journal_generation": int(boundary["sealed_action_journal_generation"]),
        "authority_epoch": str(boundary["target_authority_epoch"]),
        "sealed_authority_generation": int(boundary["sealed_generation"]),
        "target_authority_generation": int(boundary["target_generation"]),
    }


def slurm_action_partition_restore_blockers(
    journal: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    validate_slurm_action_journal(journal)
    boundary = journal.get("partition_restore_boundary")
    maximum_generation = (
        int(boundary["sealed_generation"])
        if isinstance(boundary, Mapping) and boundary
        else int(journal["active_authority_generation"])
    )
    return tuple(
        action
        for action in journal.get("actions", [])
        if isinstance(action, Mapping)
        and action.get("state") in SLURM_ACTION_BLOCKING_STATES
        and int(action.get("authority_generation", maximum_generation + 1)) <= maximum_generation
    )


def _slurm_action_blockers(
    actions: Sequence[Any],
) -> tuple[Mapping[str, Any], ...]:
    return tuple(
        action
        for action in actions
        if isinstance(action, Mapping) and action.get("state") in SLURM_ACTION_BLOCKING_STATES
    )


def slurm_action_finalization_blockers(
    journal: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Return every accepted action that still blocks final restore or cleanup."""

    validate_slurm_action_journal(journal)
    return _slurm_action_blockers(journal.get("actions", []))


def finalize_target_slurm_action_generation(
    journal: MutableMapping[str, Any],
) -> dict[str, Any]:
    """Freeze the drained target generation after action admission is closed."""

    validate_slurm_action_journal(journal)
    existing = journal.get("target_finalization_boundary")
    if isinstance(existing, Mapping) and existing:
        return dict(existing)
    if journal.get("broker_mode") != SLURM_ACTION_ADMISSION_CLOSED:
        raise ValueError(
            "Slurm target action generation finalizes only after action admission is closed."
        )
    blockers = slurm_action_finalization_blockers(journal)
    if blockers:
        raise ValueError(
            "Slurm target action generation finalizes only after every accepted action is "
            "Applied or Rejected."
        )
    restore_boundary = journal.get("partition_restore_boundary")
    if not isinstance(restore_boundary, Mapping) or not restore_boundary:
        raise ValueError(
            "Slurm target action generation finalization requires the partition restore boundary."
        )
    target_generation = int(restore_boundary["target_generation"])
    timestamp = _utc_now()
    boundary = {
        "target_authority_generation": target_generation,
        "target_authority_epoch": str(restore_boundary["target_authority_epoch"]),
        "target_action_journal_generation": _slurm_action_transition_count(
            journal.get("actions", []),
            minimum_authority_generation=target_generation,
            maximum_authority_generation=target_generation,
        ),
        "finalized_at": timestamp,
    }
    journal["target_finalization_boundary"] = boundary
    journal["authority_generation_updated_at"] = timestamp
    validate_slurm_action_journal(journal)
    return dict(boundary)


def slurm_action_target_finalization_binding(
    journal: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the immutable pre-target proof plus finalized target-generation proof."""

    validate_slurm_action_journal(journal)
    finalization = journal.get("target_finalization_boundary")
    if not isinstance(finalization, Mapping) or not finalization:
        raise ValueError("Slurm target action generation has not been finalized.")
    return {
        **slurm_action_partition_restore_binding(journal),
        "target_action_journal_generation": int(finalization["target_action_journal_generation"]),
    }
