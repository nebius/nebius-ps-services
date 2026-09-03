"""Receipt-owned authority for complete project-generation transitions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .project_bundle_transaction import ProjectBundleSafetyError, ProjectBundleTransaction
from .render import ProjectGenerationPlan, project_generation_plan_postimage_sha256
from .soperator_failures import SoperatorSafetyPauseError

CONFIG_GENERATION_TRANSITION_SCHEMA = "nebius-cxcli.config-generation-transition.v2"
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return _sha256_bytes(encoded)


def file_sha256(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


@dataclass(frozen=True)
class ConfigGenerationTransition:
    """One immutable config plus generated-project transition."""

    schema: str
    transition_id: str
    owner: str
    stage: str
    status: str
    from_config_sha256: str
    to_config_sha256: str
    project_preimage_sha256: str
    project_generation_sha256: str
    project_postimage_sha256: str
    planned_at: str
    applied_at: str = ""

    @property
    def evidence_sha256(self) -> str:
        return _sha256_json(asdict(self))


class ConfigTransitionStore(Protocol):
    """Operation receipt adapter used by the project-generation writer."""

    def get(self, stage: str) -> ConfigGenerationTransition | None: ...

    def record(self, transition: ConfigGenerationTransition) -> None: ...


def config_transition_from_payload(payload: Mapping[str, object]) -> ConfigGenerationTransition:
    try:
        transition = ConfigGenerationTransition(
            schema=str(payload.get("schema", "")),
            transition_id=str(payload.get("transition_id", "")),
            owner=str(payload.get("owner", "")),
            stage=str(payload.get("stage", "")),
            status=str(payload.get("status", "")),
            from_config_sha256=str(payload.get("from_config_sha256", "")),
            to_config_sha256=str(payload.get("to_config_sha256", "")),
            project_preimage_sha256=str(payload.get("project_preimage_sha256", "")),
            project_generation_sha256=str(payload.get("project_generation_sha256", "")),
            project_postimage_sha256=str(payload.get("project_postimage_sha256", "")),
            planned_at=str(payload.get("planned_at", "")),
            applied_at=str(payload.get("applied_at", "")),
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("config-generation transition is invalid") from exc
    validate_config_transition(transition)
    return transition


def validate_config_transition(transition: ConfigGenerationTransition) -> None:
    if transition.schema != CONFIG_GENERATION_TRANSITION_SCHEMA:
        raise RuntimeError("config-generation transition has an unsupported schema")
    if transition.status not in {"planned", "applied"}:
        raise RuntimeError("config-generation transition has an invalid status")
    for label, value in (
        ("transition id", transition.transition_id),
        ("owner", transition.owner),
        ("stage", transition.stage),
        ("source config digest", transition.from_config_sha256),
        ("target config digest", transition.to_config_sha256),
        ("project preimage digest", transition.project_preimage_sha256),
        ("project generation digest", transition.project_generation_sha256),
        ("project postimage digest", transition.project_postimage_sha256),
        ("planned timestamp", transition.planned_at),
    ):
        if not value:
            raise RuntimeError(f"config-generation transition requires {label}")
    for value in (
        transition.transition_id,
        transition.from_config_sha256,
        transition.to_config_sha256,
        transition.project_preimage_sha256,
        transition.project_generation_sha256,
        transition.project_postimage_sha256,
    ):
        if not _SHA256.fullmatch(value):
            raise RuntimeError("config-generation transition contains an invalid digest")
    if transition.status == "applied" and not transition.applied_at:
        raise RuntimeError("applied config-generation transition requires a timestamp")
    if transition.status == "planned" and transition.applied_at:
        raise RuntimeError("planned config-generation transition cannot be marked applied")


def validate_config_transition_chain(
    transitions: Sequence[ConfigGenerationTransition],
    *,
    initial_config_sha256: str,
) -> None:
    expected = initial_config_sha256
    seen_stages: set[str] = set()
    planned_seen = False
    for index, transition in enumerate(transitions):
        validate_config_transition(transition)
        if transition.stage in seen_stages:
            raise RuntimeError("config-generation ledger repeats a semantic stage")
        seen_stages.add(transition.stage)
        if transition.from_config_sha256 != expected:
            raise RuntimeError("config-generation ledger has a broken digest chain")
        if planned_seen or (transition.status == "planned" and index != len(transitions) - 1):
            raise RuntimeError("config-generation ledger has more than one planned tail")
        planned_seen = transition.status == "planned"
        expected = transition.to_config_sha256


def assert_config_authority_current(
    transitions: Sequence[ConfigGenerationTransition],
    *,
    initial_config_sha256: str,
    initial_project_snapshot_sha256: str,
    current_config_sha256: str,
    current_project_snapshot_sha256: str,
    current_project_generation_sha256: str | None,
) -> None:
    """Accept only the exact durable config and generated-project state."""

    validate_config_transition_chain(
        transitions,
        initial_config_sha256=initial_config_sha256,
    )
    if not transitions:
        matches = (
            current_config_sha256 == initial_config_sha256
            and current_project_snapshot_sha256 == initial_project_snapshot_sha256
        )
    else:
        tail = transitions[-1]
        postimage_matches = (
            current_config_sha256 == tail.to_config_sha256
            and current_project_snapshot_sha256 == tail.project_postimage_sha256
            and current_project_generation_sha256 == tail.project_generation_sha256
        )
        preimage_snapshot = (
            transitions[-2].project_postimage_sha256
            if len(transitions) > 1
            else initial_project_snapshot_sha256
        )
        preimage_matches = (
            tail.status == "planned"
            and current_config_sha256 == tail.from_config_sha256
            and current_project_snapshot_sha256 == preimage_snapshot
        )
        matches = postimage_matches or preimage_matches
    if not matches:
        raise SoperatorSafetyPauseError(
            "config.yaml or generated project state differs from the last durable operation generation",
            code="config-authority-drift",
        )


def upsert_config_transition(
    transitions: Sequence[ConfigGenerationTransition],
    transition: ConfigGenerationTransition,
    *,
    initial_config_sha256: str,
) -> tuple[ConfigGenerationTransition, ...]:
    validate_config_transition(transition)
    current = list(transitions)
    matches = [index for index, item in enumerate(current) if item.stage == transition.stage]
    if matches:
        index = matches[0]
        existing = current[index]
        if index != len(current) - 1 or existing.status != "planned":
            if existing != transition:
                raise RuntimeError("config-generation transition changed after it was recorded")
            return tuple(current)
        stable_fields = (
            "schema",
            "transition_id",
            "owner",
            "stage",
            "from_config_sha256",
            "to_config_sha256",
            "project_preimage_sha256",
            "project_generation_sha256",
            "project_postimage_sha256",
            "planned_at",
        )
        if any(getattr(existing, field) != getattr(transition, field) for field in stable_fields):
            raise RuntimeError("planned config-generation transition changed during recovery")
        if transition.status != "applied":
            return tuple(current)
        current[index] = transition
    else:
        if any(item.status == "planned" for item in current):
            raise RuntimeError("config-generation ledger already has a planned transition")
        current.append(transition)
    validate_config_transition_chain(current, initial_config_sha256=initial_config_sha256)
    return tuple(current)


def _planned_transition(
    *,
    owner: str,
    stage: str,
    config_path: Path,
    plan: ProjectGenerationPlan,
) -> ConfigGenerationTransition:
    canonical_config = Path(config_path).resolve()
    try:
        from_digest = str(plan.expected_preimages[canonical_config])
        content = plan.writes[canonical_config]
    except KeyError as exc:
        raise SoperatorSafetyPauseError(
            "the admitted project generation does not own config.yaml",
            code="config-authority-missing",
        ) from exc
    to_digest = _sha256_bytes(bytes(content))
    material = {
        "schema": CONFIG_GENERATION_TRANSITION_SCHEMA,
        "owner": owner,
        "stage": stage,
        "from": from_digest,
        "to": to_digest,
        "projectPreimage": plan.preimage_sha256,
        "projectGeneration": plan.sha256,
        "projectPostimage": project_generation_plan_postimage_sha256(
            project_dir=config_path.parent,
            plan=plan,
        ),
    }
    return ConfigGenerationTransition(
        schema=CONFIG_GENERATION_TRANSITION_SCHEMA,
        transition_id=_sha256_json(material),
        owner=owner,
        stage=stage,
        status="planned",
        from_config_sha256=from_digest,
        to_config_sha256=to_digest,
        project_preimage_sha256=plan.preimage_sha256,
        project_generation_sha256=plan.sha256,
        project_postimage_sha256=project_generation_plan_postimage_sha256(
            project_dir=config_path.parent,
            plan=plan,
        ),
        planned_at=_utc_now(),
    )


def apply_project_generation_transition(
    *,
    project_dir: Path,
    config_path: Path,
    owner: str,
    stage: str,
    store: ConfigTransitionStore,
    build_plan: Callable[[], ProjectGenerationPlan],
    assert_authority: Callable[[], None],
    current_project_snapshot_sha256: Callable[[], str],
) -> ConfigGenerationTransition:
    """Commit or recover one exact complete project generation."""

    transaction = ProjectBundleTransaction(project_dir)
    try:
        current_generation = transaction.current_generation_sha256()
    except ProjectBundleSafetyError as exc:
        raise SoperatorSafetyPauseError(
            "the committed project generation cannot be recovered safely",
            code="config-generation-recovery",
        ) from exc
    existing = store.get(stage)
    current_config = file_sha256(config_path)
    current_snapshot = current_project_snapshot_sha256()
    if existing is not None:
        validate_config_transition(existing)
        if current_config == existing.to_config_sha256:
            if (
                current_generation != existing.project_generation_sha256
                or current_snapshot != existing.project_postimage_sha256
            ):
                raise SoperatorSafetyPauseError(
                    "config.yaml matches the operation postimage but generated state does not",
                    code="config-generation-mismatch",
                )
            if existing.status == "planned":
                existing = replace(existing, status="applied", applied_at=_utc_now())
                store.record(existing)
            return existing
        if current_config != existing.from_config_sha256:
            raise SoperatorSafetyPauseError(
                "config.yaml is neither the operation preimage nor its recorded postimage",
                code="config-authority-drift",
            )
        if existing.status == "applied":
            raise SoperatorSafetyPauseError(
                "an applied config generation was rolled back outside the operation",
                code="config-authority-rollback",
            )

    plan = build_plan()
    candidate = _planned_transition(
        owner=owner,
        stage=stage,
        config_path=config_path,
        plan=plan,
    )
    if existing is not None:
        candidate = replace(candidate, planned_at=existing.planned_at)
        stable_fields = (
            "transition_id",
            "owner",
            "stage",
            "from_config_sha256",
            "to_config_sha256",
            "project_preimage_sha256",
            "project_generation_sha256",
            "project_postimage_sha256",
        )
        if any(getattr(candidate, field) != getattr(existing, field) for field in stable_fields):
            raise SoperatorSafetyPauseError(
                "the recovered project generation differs from its planned transition",
                code="config-generation-replan",
            )
        candidate = existing
    else:
        store.record(candidate)

    assert_authority()
    try:
        transaction.commit(
            plan.writes,
            removals=plan.removals,
            expected_preimages=plan.expected_preimages,
            generation_sha256=plan.sha256,
        )
        committed_generation = transaction.current_generation_sha256()
    except ProjectBundleSafetyError as exc:
        raise SoperatorSafetyPauseError(
            "the project generation changed outside the operation",
            code="config-generation-commit",
        ) from exc
    if (
        file_sha256(config_path) != candidate.to_config_sha256
        or committed_generation != candidate.project_generation_sha256
        or current_project_snapshot_sha256() != candidate.project_postimage_sha256
    ):
        raise SoperatorSafetyPauseError(
            "the committed project generation does not match its recorded postimage",
            code="config-generation-postimage",
        )
    applied = replace(candidate, status="applied", applied_at=_utc_now())
    store.record(applied)
    return applied


__all__ = [
    "CONFIG_GENERATION_TRANSITION_SCHEMA",
    "ConfigGenerationTransition",
    "ConfigTransitionStore",
    "apply_project_generation_transition",
    "assert_config_authority_current",
    "config_transition_from_payload",
    "file_sha256",
    "upsert_config_transition",
    "validate_config_transition",
    "validate_config_transition_chain",
]
