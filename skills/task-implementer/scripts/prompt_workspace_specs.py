#!/usr/bin/env python3
"""Private steering ledger and committed specification document validation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

SHARED_PROJECT_SPEC_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "maintain-project-specs" / "scripts"
)
if str(SHARED_PROJECT_SPEC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_PROJECT_SPEC_SCRIPTS))

PROJECT_AGENT_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "project-agent-instructions" / "scripts"
)
if str(PROJECT_AGENT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_AGENT_SCRIPTS))

from project_specs_lib.contracts import (  # noqa: E402
    ProjectSpecError,
    _read_file as owner_read_file,
    canonical_digest as owner_canonical_digest,
)
from project_specs_lib.impact import (  # noqa: E402
    CLAIM_SCHEMA as IMPACT_CLAIM_SCHEMA,
    RECEIPT_SCHEMA as IMPACT_RECEIPT_SCHEMA,
    public_impact_status,
    validate_prompt_impact,
)
from project_agent_instructions_lib.contracts import (  # noqa: E402
    ProjectInstructionsError,
    _validate_manifest_shape as validate_project_agent_manifest,
)
from project_agent_instructions_lib.workflow import (  # noqa: E402
    _validate_decision as validate_project_agent_decision,
)

from prompt_workspace_core import (  # noqa: E402
    REVISION_RE,
    PromptWorkspaceError,
    ensure_private_dir,
    iso_seconds,
    load_json_object,
    now_utc,
    require_mode,
    required_string,
    stable_json,
    write_atomic,
    write_exclusive,
)


STEERING_SCHEMA = "task-implementer/steering-ledger-v1"
STEERING_DISPOSITIONS = {"pending", "applied", "blocked", "no_effect"}
REFINEMENT_SCHEMA = "task-implementer/requirements-refinement-v1"
REFINEMENT_STATUSES = {"extracting", "needs_clarification", "ready"}
REFINEMENT_CATEGORIES = (
    "outcomes",
    "actors",
    "context",
    "functional_requirements",
    "constraints",
    "acceptance_criteria",
    "verification",
    "non_goals",
    "assumptions",
    "dependencies",
    "references",
    "live_experiment_environment",
)
QUESTION_ID_RE = re.compile(r"Q-((?!0+\Z)[0-9]{3,})\Z")
REQUIREMENTS_SCHEMA = "maintain-project-specs/requirements-v1"
DESIGN_SCHEMA = "maintain-project-specs/design-v1"
MAX_SPEC_BYTES = 1024 * 1024
REQUIREMENT_ID_RE = re.compile(r"TI-REQ-((?!0+\Z)[0-9]{3,})\Z")
DESIGN_ID_RE = re.compile(r"TI-DES-((?!0+\Z)[0-9]{3,})\Z")
INTERNAL_STATE_PATTERNS = (
    re.compile(r"prompt-[0-9a-f]{32}"),
    re.compile(r"run-[0-9]{8}t[0-9]{6}z-[0-9a-f]{8}"),
    re.compile(r"inputs/r[0-9]{4}/prompt\.md"),
    re.compile(r"(?:^|/)task-implementer/projects/"),
    re.compile(r"\br[0-9]{4}\b"),
    re.compile(r"\btask-[0-9]+\b"),
    re.compile(r"\bcheckpoint-[0-9]+\b"),
    re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])"),
)
IMPACT_LEDGER_SCHEMA = "task-implementer/prompt-impact-ledger-v1"
IMPACT_PLAN_SCHEMA = "task-implementer/prompt-impact-plan-v1"
IMPACT_ATTEMPT_RE = re.compile(r"attempt-([0-9]{4,12})\.json\Z")
IMPACT_MAX_GENERATION = 999_999_999_999


def refinement_path(run_dir: Path) -> Path:
    return run_dir / "requirements-refinement.json"


def load_requirements_refinement(
    run_dir: Path,
    *,
    required: bool = False,
    _candidate: dict[str, object] | None = None,
) -> dict[str, object] | None:
    path = refinement_path(run_dir)
    if _candidate is None and not path.exists():
        if required:
            raise PromptWorkspaceError(
                "REQUIREMENTS_REFINEMENT_REQUIRED",
                "requirements refinement state is missing",
            )
        return None
    if _candidate is None:
        if path.is_symlink() or not path.is_file():
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "requirements refinement state is unsafe"
            )
        require_mode(path, 0o600, "requirements refinement state")
        value = load_json_object(path, "requirements refinement state")
    else:
        value = dict(_candidate)
    required_keys = {
        "schema",
        "prompt_id",
        "revision",
        "intent_sha256",
        "status",
        "extracted",
        "questions",
        "compiled_requirements_sha256",
        "updated_at",
    }
    if (
        set(value) != required_keys
        or value.get("schema") != REFINEMENT_SCHEMA
        or REVISION_RE.fullmatch(str(value.get("revision") or "")) is None
        or re.fullmatch(r"prompt-[0-9a-f]{32}", str(value.get("prompt_id") or ""))
        is None
        or re.fullmatch(r"[0-9a-f]{64}", str(value.get("intent_sha256") or "")) is None
        or value.get("status") not in REFINEMENT_STATUSES
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "requirements refinement identity is invalid"
        )
    extracted = value.get("extracted")
    if not isinstance(extracted, dict) or set(extracted) != set(REFINEMENT_CATEGORIES):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "requirements refinement categories are invalid"
        )
    for category, statements in extracted.items():
        if not isinstance(statements, list) or any(
            not isinstance(item, str) or not item.strip() for item in statements
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID",
                f"requirements refinement category is invalid: {category}",
            )
    questions = value.get("questions")
    if not isinstance(questions, list):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "requirements refinement questions are invalid"
        )
    seen: set[str] = set()
    open_material = False
    for question in questions:
        if not isinstance(question, dict) or set(question) != {
            "id",
            "question",
            "material",
            "status",
            "answer",
            "source",
            "source_revision",
            "conflict",
        }:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "requirements refinement question is invalid"
            )
        question_id = str(question.get("id") or "")
        if QUESTION_ID_RE.fullmatch(question_id) is None or question_id in seen:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "requirements refinement question ID is invalid"
            )
        seen.add(question_id)
        if (
            not isinstance(question.get("question"), str)
            or not str(question["question"]).strip()
            or not isinstance(question.get("material"), bool)
            or question.get("status") not in {"open", "answered", "reopened"}
            or question.get("source") not in {None, "chat", "prompt"}
            or (
                question.get("source_revision") is not None
                and REVISION_RE.fullmatch(str(question["source_revision"])) is None
            )
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID",
                "requirements refinement question fields are invalid",
            )
        if question["status"] == "answered":
            if (
                not isinstance(question.get("answer"), str)
                or not str(question["answer"]).strip()
                or question.get("source") is None
                or question.get("source_revision") is None
            ):
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "answered refinement question lacks provenance"
                )
        elif question.get("answer") is not None and not isinstance(
            question.get("answer"), str
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "requirements refinement answer is invalid"
            )
        if question["material"] and question["status"] in {"open", "reopened"}:
            open_material = True
    compiled = value.get("compiled_requirements_sha256")
    if compiled is not None and re.fullmatch(r"[0-9a-f]{64}", str(compiled)) is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "compiled requirements digest is invalid"
        )
    _timestamp(value.get("updated_at"), "requirements refinement update")
    if value["status"] == "ready" and (open_material or compiled is None):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID",
            "ready requirements refinement has an open material question or no compiled digest",
        )
    return value


def begin_requirements_refinement(
    run_dir: Path,
    prompt_id: str,
    revision: str,
    intent_sha256: str,
    *,
    predecessor_dir: Path | None = None,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    prior = load_requirements_refinement(predecessor_dir or run_dir, required=False)
    questions = [] if prior is None else list(prior["questions"])
    value: dict[str, object] = {
        "schema": REFINEMENT_SCHEMA,
        "prompt_id": prompt_id,
        "revision": revision,
        "intent_sha256": intent_sha256,
        "status": "extracting",
        "extracted": {category: [] for category in REFINEMENT_CATEGORIES},
        "questions": questions,
        "compiled_requirements_sha256": None,
        "updated_at": iso_seconds(clock()),
    }
    write_atomic(refinement_path(run_dir), stable_json(value))
    return value


def save_requirements_refinement(
    run_dir: Path, value: dict[str, object]
) -> dict[str, object]:
    validated = load_requirements_refinement(run_dir, required=True, _candidate=value)
    assert validated is not None
    write_atomic(refinement_path(run_dir), stable_json(value))
    return validated


def prompt_impact_claim_path(run_dir: Path) -> Path:
    return run_dir / "prompt-impact-claim.json"


def _impact_root(run_dir: Path) -> Path:
    return run_dir / "prompt-impact"


def _read_impact_json(path: Path, label: str) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise PromptWorkspaceError(
            "PROMPT_IMPACT_REQUIRED", f"{label} is missing"
        ) from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > 4 * 1024 * 1024
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise PromptWorkspaceError("RUN_STATE_INVALID", f"{label} is unsafe")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PromptWorkspaceError("RUN_STATE_INVALID", f"{label} is unsafe") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise PromptWorkspaceError(
                "CONCURRENT_MODIFICATION", f"{label} changed while opening"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 4 * 1024 * 1024:
                raise PromptWorkspaceError("RUN_STATE_INVALID", f"{label} is too large")
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise PromptWorkspaceError(
                "CONCURRENT_MODIFICATION", f"{label} changed while reading"
            )
    finally:
        os.close(descriptor)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", f"{label} is invalid"
        ) from error
    if not isinstance(value, dict):
        raise PromptWorkspaceError("RUN_STATE_INVALID", f"{label} is invalid")
    return value


def _load_impact_claim(run_dir: Path) -> dict[str, object]:
    path = prompt_impact_claim_path(run_dir)
    if not path.exists():
        raise PromptWorkspaceError(
            "PROMPT_IMPACT_REQUIRED",
            "the latest requirements refinement has no complete prompt impact claim",
        )
    value = _read_impact_json(path, "prompt impact claim")
    if value.get("schema") != IMPACT_CLAIM_SCHEMA:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prompt impact claim schema is invalid"
        )
    return value


def _load_prompt_impact(run_dir: Path) -> tuple[dict[str, object], str] | None:
    root = _impact_root(run_dir)
    ledger_path = root / "ledger.json"
    if not ledger_path.exists():
        return None
    if root.is_symlink() or not root.is_dir():
        raise PromptWorkspaceError("RUN_STATE_INVALID", "prompt impact state is unsafe")
    require_mode(root, 0o700, "prompt impact directory")
    ledger = _read_impact_json(ledger_path, "prompt impact ledger")
    if (
        set(ledger) != {"schema", "workflow", "current"}
        or ledger.get("schema") != IMPACT_LEDGER_SCHEMA
        or ledger.get("workflow") != "task-implementer"
        or not isinstance(ledger.get("current"), dict)
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prompt impact ledger is invalid"
        )
    current = dict(ledger["current"])
    if set(current) != {"generation", "revision", "path", "sha256"}:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prompt impact pointer is invalid"
        )
    generation = current.get("generation")
    expected_path = (
        f"attempt-{generation:04d}.json"
        if isinstance(generation, int) and not isinstance(generation, bool)
        else None
    )
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
        or generation > IMPACT_MAX_GENERATION
        or current.get("path") != expected_path
        or REVISION_RE.fullmatch(str(current.get("revision") or "")) is None
        or re.fullmatch(r"[0-9a-f]{64}", str(current.get("sha256") or "")) is None
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prompt impact pointer is invalid"
        )
    attempt_path = root / str(current["path"])
    receipt = _read_impact_json(attempt_path, "prompt impact attempt")
    receipt_sha256 = owner_canonical_digest(receipt)
    if (
        receipt.get("schema") != IMPACT_RECEIPT_SCHEMA
        or receipt.get("workflow") != "task-implementer"
        or receipt.get("generation") != generation
        or receipt.get("revision") != current.get("revision")
        or receipt_sha256 != current.get("sha256")
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prompt impact attempt is invalid"
        )
    return receipt, receipt_sha256


def load_current_prompt_impact(
    run_dir: Path, *, required: bool = False
) -> tuple[dict[str, object], str] | None:
    value = _load_prompt_impact(run_dir)
    if value is None and required:
        raise PromptWorkspaceError(
            "PROMPT_IMPACT_REQUIRED",
            "the active run predates prompt impact receipts and requires forward reconciliation",
        )
    return value


def _impact_plan_path(run_dir: Path) -> Path:
    return _impact_root(run_dir) / "plan-basis.json"


def settle_prompt_impact_plan(
    run_dir: Path,
    coordinator: dict[str, object],
    impact: dict[str, object],
    impact_sha256: str,
) -> dict[str, object]:
    """Bind one coordinator plan basis to the latest settled prompt revision."""

    if impact.get("plan_action") == "replan_required" and (
        coordinator.get("prompt_revision") != impact.get("revision")
        or coordinator.get("prompt_intent_sha256") != impact.get("intent_sha256")
    ):
        raise PromptWorkspaceError(
            "REPLAN_REQUIRED",
            "material prompt impact requires a plan based on the latest revision",
        )
    value: dict[str, object] = {
        "schema": IMPACT_PLAN_SCHEMA,
        "plan_sha256": coordinator.get("plan_sha256"),
        "plan_basis_revision": coordinator.get("prompt_revision"),
        "plan_basis_intent_sha256": coordinator.get("prompt_intent_sha256"),
        "latest_settled_revision": impact.get("revision"),
        "latest_settled_intent_sha256": impact.get("intent_sha256"),
        "impact_sha256": impact_sha256,
        "spec_receipt_sha256": impact.get("spec_receipt_sha256"),
        "spec_transition_sha256": impact.get("spec_transition_sha256"),
        "plan_action": impact.get("plan_action"),
    }
    if (
        any(
            re.fullmatch(r"[0-9a-f]{64}", str(value.get(key) or "")) is None
            for key in (
                "plan_sha256",
                "plan_basis_intent_sha256",
                "latest_settled_intent_sha256",
                "impact_sha256",
                "spec_receipt_sha256",
            )
        )
        or REVISION_RE.fullmatch(str(value.get("plan_basis_revision") or "")) is None
        or REVISION_RE.fullmatch(str(value.get("latest_settled_revision") or ""))
        is None
        or (
            value.get("spec_transition_sha256") is not None
            and re.fullmatch(r"[0-9a-f]{64}", str(value.get("spec_transition_sha256")))
            is None
        )
        or value.get("plan_action") not in {"retain_plan", "replan_required"}
    ):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prompt impact plan basis is invalid"
        )
    write_atomic(_impact_plan_path(run_dir), stable_json(value))
    return value


def verify_prompt_impact_plan(
    run_dir: Path, coordinator: dict[str, object], project_root: Path
) -> dict[str, object]:
    """Freeze progression when plan or latest impact evidence is stale."""

    impact_value = load_current_prompt_impact(run_dir, required=True)
    assert impact_value is not None
    impact, impact_sha256 = impact_value
    path = _impact_plan_path(run_dir)
    if not path.exists():
        raise PromptWorkspaceError(
            "PROMPT_IMPACT_REQUIRED",
            "the active plan predates impact settlement and requires forward reconciliation",
        )
    value = _read_impact_json(path, "prompt impact plan basis")
    required = {
        "schema",
        "plan_sha256",
        "plan_basis_revision",
        "plan_basis_intent_sha256",
        "latest_settled_revision",
        "latest_settled_intent_sha256",
        "impact_sha256",
        "spec_receipt_sha256",
        "spec_transition_sha256",
        "plan_action",
    }
    if (
        set(value) != required
        or value.get("schema") != IMPACT_PLAN_SCHEMA
        or value.get("plan_sha256") != coordinator.get("plan_sha256")
        or value.get("plan_basis_revision") != coordinator.get("prompt_revision")
        or value.get("plan_basis_intent_sha256")
        != coordinator.get("prompt_intent_sha256")
        or value.get("latest_settled_revision") != impact.get("revision")
        or value.get("latest_settled_intent_sha256") != impact.get("intent_sha256")
        or value.get("impact_sha256") != impact_sha256
        or value.get("spec_receipt_sha256") != impact.get("spec_receipt_sha256")
        or value.get("spec_transition_sha256") != impact.get("spec_transition_sha256")
        or value.get("plan_action") != impact.get("plan_action")
    ):
        raise PromptWorkspaceError(
            "REPLAN_REQUIRED", "prompt impact plan basis is stale"
        )
    binding = load_json_object(run_dir / "manifest.json", "prompt binding")
    revisions = binding.get("revisions")
    latest = revisions[-1] if isinstance(revisions, list) and revisions else None
    if (
        not isinstance(latest, dict)
        or latest.get("revision") != impact.get("revision")
        or (latest.get("intent_sha256") or latest.get("sha256"))
        != impact.get("intent_sha256")
    ):
        raise PromptWorkspaceError(
            "PROMPT_IMPACT_REQUIRED",
            "the latest accepted prompt has no impact settlement",
        )
    if impact.get("plan_action") == "replan_required" and value.get(
        "plan_basis_revision"
    ) != value.get("latest_settled_revision"):
        raise PromptWorkspaceError(
            "REPLAN_REQUIRED", "material prompt impact needs replanning"
        )
    for kind in ("requirements", "design"):
        spec_path = project_root / "docs" / f"{kind}.md"
        try:
            raw, _text = owner_read_file(spec_path, f"{kind} specification")
        except ProjectSpecError as error:
            raise PromptWorkspaceError("SPEC_CONFLICT", error.message) from error
        if _digest(raw) != impact.get(f"{kind}_sha256"):
            raise PromptWorkspaceError(
                "REPLAN_REQUIRED",
                "canonical project specs drifted after impact settlement",
            )
    return value


def _publish_prompt_impact(
    run_dir: Path,
    project_root: Path,
    run_state: dict[str, object],
    refinement: dict[str, object],
) -> tuple[dict[str, object], str]:
    with _impact_publication_lock(run_dir):
        return _publish_prompt_impact_locked(
            run_dir, project_root, run_state, refinement
        )


@contextmanager
def _impact_publication_lock(run_dir: Path):
    root = _impact_root(run_dir)
    ensure_private_dir(root)
    path = root / "publication.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "prompt impact publication lock is unsafe"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
        ):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "prompt impact publication lock is unsafe"
            )
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + 10
        if os.name == "posix":
            import fcntl

            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise PromptWorkspaceError(
                            "WORKSPACE_BUSY",
                            "another prompt impact publication is active",
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        else:  # pragma: no cover - Windows support follows the workspace lock.
            import msvcrt

            if opened.st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            while True:
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise PromptWorkspaceError(
                            "WORKSPACE_BUSY",
                            "another prompt impact publication is active",
                        )
                    time.sleep(0.05)
            try:
                yield
            finally:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(descriptor)


def _impact_ledger_fingerprint(run_dir: Path) -> str | None:
    path = _impact_root(run_dir) / "ledger.json"
    if not path.exists():
        return None
    return owner_canonical_digest(_read_impact_json(path, "prompt impact ledger"))


def _max_impact_generation(root: Path) -> int:
    maximum = 0
    for path in root.iterdir():
        if not path.name.startswith("attempt-"):
            continue
        match = IMPACT_ATTEMPT_RE.fullmatch(path.name)
        if match is None:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "impact attempt name is invalid"
            )
        _read_impact_json(path, "prompt impact attempt")
        maximum = max(maximum, int(match.group(1)))
    if maximum >= IMPACT_MAX_GENERATION:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "impact generation is exhausted"
        )
    return maximum


def _write_impact_ledger_cas(
    run_dir: Path, expected_fingerprint: str | None, ledger: dict[str, object]
) -> None:
    if _impact_ledger_fingerprint(run_dir) != expected_fingerprint:
        raise PromptWorkspaceError(
            "CONCURRENT_MODIFICATION", "prompt impact ledger changed during publication"
        )
    write_atomic(_impact_root(run_dir) / "ledger.json", stable_json(ledger))


def _publish_prompt_impact_locked(
    run_dir: Path,
    project_root: Path,
    run_state: dict[str, object],
    refinement: dict[str, object],
) -> tuple[dict[str, object], str]:
    claim = _load_impact_claim(run_dir)
    expected_ledger = _impact_ledger_fingerprint(run_dir)
    current = _load_prompt_impact(run_dir)
    current_receipt = current[0] if current is not None else None
    current_sha256 = current[1] if current is not None else None
    if current_receipt is not None:
        current_transition = current_receipt.get("spec_transition")
        retry_prior_spec = (
            current_transition.get("prior_spec_receipt_sha256")
            if isinstance(current_transition, dict)
            else current_receipt.get("spec_receipt_sha256")
        )
        try:
            retry = validate_prompt_impact(
                project_root,
                workflow="task-implementer",
                prompt_id=str(run_state["prompt_id"]),
                revision=str(run_state["latest_revision"]),
                prompt_sha256=str(run_state["latest_sha256"]),
                intent_sha256=str(run_state["latest_intent_sha256"]),
                refinement=refinement,
                claim=claim,
                prior_impact_sha256=current_receipt.get("prior_impact_sha256"),
                prior_spec_receipt_sha256=retry_prior_spec,
                generation=int(current_receipt["generation"]),
            )
        except ProjectSpecError as error:
            raise PromptWorkspaceError(
                "PROMPT_IMPACT_REQUIRED", error.message
            ) from error
        if owner_canonical_digest(retry) == current_sha256:
            if _impact_ledger_fingerprint(run_dir) != expected_ledger:
                raise PromptWorkspaceError(
                    "CONCURRENT_MODIFICATION",
                    "prompt impact ledger changed during publication",
                )
            return current_receipt, str(current_sha256)
    root = _impact_root(run_dir)
    ensure_private_dir(root)
    generation = (
        1 if current_receipt is None else int(current_receipt["generation"]) + 1
    )
    maximum = _max_impact_generation(root)
    while True:
        try:
            receipt = validate_prompt_impact(
                project_root,
                workflow="task-implementer",
                prompt_id=str(run_state["prompt_id"]),
                revision=str(run_state["latest_revision"]),
                prompt_sha256=str(run_state["latest_sha256"]),
                intent_sha256=str(run_state["latest_intent_sha256"]),
                refinement=refinement,
                claim=claim,
                prior_impact_sha256=current_sha256,
                prior_spec_receipt_sha256=(
                    current_receipt.get("spec_receipt_sha256")
                    if current_receipt is not None
                    else None
                ),
                generation=generation,
            )
        except ProjectSpecError as error:
            raise PromptWorkspaceError(
                "PROMPT_IMPACT_REQUIRED", error.message
            ) from error
        attempt_name = f"attempt-{generation:04d}.json"
        attempt_path = root / attempt_name
        receipt_sha256 = owner_canonical_digest(receipt)
        if not attempt_path.exists():
            try:
                write_exclusive(attempt_path, stable_json(receipt))
            except FileExistsError:
                maximum = _max_impact_generation(root)
                generation = maximum + 1
                continue
            else:
                break
        existing = _read_impact_json(attempt_path, "prompt impact attempt")
        if owner_canonical_digest(existing) == receipt_sha256:
            receipt = existing
            break
        generation = maximum + 1
    receipt_sha256 = owner_canonical_digest(receipt)
    ledger = {
        "schema": IMPACT_LEDGER_SCHEMA,
        "workflow": "task-implementer",
        "current": {
            "generation": generation,
            "revision": receipt["revision"],
            "path": attempt_name,
            "sha256": receipt_sha256,
        },
    }
    _write_impact_ledger_cas(run_dir, expected_ledger, ledger)
    return receipt, receipt_sha256


def steering_path(run_dir: Path) -> Path:
    return run_dir / "steering.json"


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise PromptWorkspaceError("RUN_STATE_INVALID", f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PromptWorkspaceError("RUN_STATE_INVALID", f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PromptWorkspaceError("RUN_STATE_INVALID", f"{label} has no UTC offset")
    return iso_seconds(parsed)


def load_steering_ledger(
    run_dir: Path,
    revisions: list[dict[str, object]],
) -> dict[str, object]:
    """Load and validate optional mutable steering dispositions."""

    path = steering_path(run_dir)
    if not path.exists():
        return {"schema": STEERING_SCHEMA, "events": []}
    if path.is_symlink() or not path.is_file():
        raise PromptWorkspaceError("RUN_STATE_INVALID", "steering ledger is unsafe")
    require_mode(path, 0o600, "steering ledger")
    value = load_json_object(path, "steering ledger")
    if set(value) != {"schema", "events"} or value.get("schema") != STEERING_SCHEMA:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering ledger schema is invalid"
        )
    events = value.get("events")
    if not isinstance(events, list):
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering ledger events are invalid"
        )
    revision_index = {str(revision.get("revision")): revision for revision in revisions}
    seen: set[str] = set()
    previous_number = 0
    pending_seen = False
    validated: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict) or set(event) != {
            "revision",
            "sha256",
            "submitted_at",
            "disposition",
            "resolved_at",
        }:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering ledger event is invalid"
            )
        revision_id = event.get("revision")
        match = REVISION_RE.fullmatch(str(revision_id or ""))
        if match is None or revision_id in seen:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering ledger revision is invalid"
            )
        number = int(match.group(1))
        if number <= previous_number or revision_id not in revision_index:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering ledger revisions are out of order"
            )
        manifest_revision = revision_index[str(revision_id)]
        if event.get("sha256") != manifest_revision.get("sha256"):
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering ledger digest is invalid"
            )
        disposition = event.get("disposition")
        if disposition not in STEERING_DISPOSITIONS:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering disposition is invalid"
            )
        submitted_at = _timestamp(event.get("submitted_at"), "steering submission")
        resolved_at = event.get("resolved_at")
        if disposition == "pending":
            pending_seen = True
            if resolved_at is not None:
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "pending steering has a resolution time"
                )
        elif pending_seen:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID",
                "steering resolutions are not an ordered prefix",
            )
        elif resolved_at is None:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "resolved steering has no resolution time"
            )
        else:
            resolved_at = _timestamp(resolved_at, "steering resolution")
            if datetime.fromisoformat(str(resolved_at)) < datetime.fromisoformat(
                submitted_at
            ):
                raise PromptWorkspaceError(
                    "RUN_STATE_INVALID", "steering resolved before submission"
                )
        validated.append(
            {
                "revision": revision_id,
                "sha256": event["sha256"],
                "submitted_at": submitted_at,
                "disposition": disposition,
                "resolved_at": resolved_at,
            }
        )
        seen.add(str(revision_id))
        previous_number = number
    return {"schema": STEERING_SCHEMA, "events": validated}


def record_steering_revision(
    run_dir: Path,
    revisions: list[dict[str, object]],
    revision_id: str,
    submitted_at: datetime,
) -> dict[str, object]:
    """Record one accepted immutable revision as pending steering."""

    ledger = load_steering_ledger(run_dir, revisions)
    events = list(ledger["events"])
    existing = [event for event in events if event["revision"] == revision_id]
    if existing:
        return existing[0]
    revision = next(
        (item for item in revisions if item.get("revision") == revision_id),
        None,
    )
    if revision is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering revision is not in the manifest"
        )
    event: dict[str, object] = {
        "revision": revision_id,
        "sha256": revision["sha256"],
        "submitted_at": iso_seconds(submitted_at),
        "disposition": "pending",
        "resolved_at": None,
    }
    events.append(event)
    ledger["events"] = events
    write_atomic(steering_path(run_dir), stable_json(ledger))
    return event


def resolve_steering_revision(
    run_dir: Path,
    revisions: list[dict[str, object]],
    revision_id: str,
    disposition: str,
    *,
    clock: Callable[[], datetime] = now_utc,
) -> dict[str, object]:
    """Resolve one pending steering event without changing immutable snapshots."""

    if disposition not in STEERING_DISPOSITIONS - {"pending"}:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering resolution disposition is invalid"
        )
    ledger = load_steering_ledger(run_dir, revisions)
    events = list(ledger["events"])
    matches = [event for event in events if event["revision"] == revision_id]
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering revision has no unique ledger event"
        )
    event = matches[0]
    if event["disposition"] != "pending":
        if event["disposition"] != disposition:
            raise PromptWorkspaceError(
                "RUN_STATE_INVALID", "steering revision is already resolved"
            )
        return event
    oldest_pending = next(item for item in events if item["disposition"] == "pending")
    if oldest_pending["revision"] != revision_id:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering revisions must resolve in order"
        )
    resolved = clock()
    if resolved.tzinfo is None or resolved.utcoffset() is None:
        raise PromptWorkspaceError(
            "RUN_STATE_INVALID", "steering resolution clock must be timezone-aware"
        )
    event["disposition"] = disposition
    event["resolved_at"] = iso_seconds(resolved)
    ledger["events"] = events
    write_atomic(steering_path(run_dir), stable_json(ledger))
    return event


def pending_steering_revisions(
    run_dir: Path,
    revisions: list[dict[str, object]],
) -> list[str]:
    ledger = load_steering_ledger(run_dir, revisions)
    return [
        str(event["revision"])
        for event in ledger["events"]
        if event["disposition"] == "pending"
    ]


def _spec_contract(kind: str) -> tuple[str, str, str, re.Pattern[str], str]:
    if kind == "requirements":
        return (
            REQUIREMENTS_SCHEMA,
            "requirements.md",
            "agentic-sdlc.requirements.v1",
            REQUIREMENT_ID_RE,
            "Requirements",
        )
    if kind == "design":
        return (
            DESIGN_SCHEMA,
            "design.md",
            "agentic-sdlc.design.v1",
            DESIGN_ID_RE,
            "Design",
        )
    raise AssertionError(kind)


def spec_markers(kind: str) -> tuple[str, str]:
    schema, _, _, _, _ = _spec_contract(kind)
    return (
        f"<!-- maintain-project-specs:{kind}:start schema={schema} -->",
        f"<!-- maintain-project-specs:{kind}:end -->",
    )


def spec_repo_path(workspace: dict[str, object], kind: str) -> tuple[Path, str]:
    _, filename, _, _, _ = _spec_contract(kind)
    repo_root = Path(required_string(workspace, "repo_root", "workspace manifest"))
    source_root = Path(required_string(workspace, "source_root", "workspace manifest"))
    path = source_root / "docs" / filename
    relative = path.relative_to(repo_root).as_posix()
    return path, relative


def _require_safe_spec_path(path: Path) -> None:
    docs = path.parent
    if docs.exists() and (docs.is_symlink() or not docs.is_dir()):
        raise PromptWorkspaceError("SPEC_CONFLICT", "specification docs path is unsafe")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise PromptWorkspaceError(
                "SPEC_CONFLICT", "specification document path is unsafe"
            )
        if stat.S_IMODE(path.stat().st_mode) & 0o022:
            raise PromptWorkspaceError(
                "SPEC_CONFLICT", "specification document is group or world writable"
            )


def _canonical_managed_text(value: str) -> bytes:
    normalized = "\n".join(line.rstrip() for line in value.splitlines()).strip()
    return (normalized + "\n").encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _surrounding_digest(prefix: bytes, suffix: bytes) -> str:
    return _digest(prefix + b"\0" + suffix)


def _required_record_field(section: str, label: str, identifier: str) -> str:
    matches = re.findall(
        rf"(?m)^- {re.escape(label)}:\s*(\S.*?)\s*$",
        section,
    )
    if len(matches) != 1:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{identifier} has no unique {label} field"
        )
    return matches[0]


def _require_bulleted_subsection(section: str, heading: str, identifier: str) -> None:
    match = re.search(
        rf"(?ms)^#### {re.escape(heading)}\s*\n"
        r"(.*?)(?=^#### |^### |^## |\Z)",
        section,
    )
    if match is None or re.search(r"(?m)^-\s+\S", match.group(1)) is None:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{identifier} has no {heading} evidence"
        )


def _require_bulleted_heading(body: str, heading: str, relative: str) -> None:
    if body.count(f"## {heading}") != 1:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has no unique {heading} section"
        )
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)",
        body,
    )
    if match is None or re.search(r"(?m)^-\s+\S", match.group(1)) is None:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has no {heading} entries"
        )


def _append_envelope(raw: bytes) -> tuple[bytes, bytes]:
    newline = b"\r\n" if b"\r\n" in raw else b"\n"
    if raw.endswith(newline + newline):
        separator = b""
    elif raw.endswith(newline):
        separator = newline
    else:
        separator = newline + newline
    return raw + separator, newline


def _new_envelope(kind: str) -> tuple[bytes, bytes]:
    _, _, _, _, title = _spec_contract(kind)
    return f"# {title}\n\n".encode("utf-8"), b"\n"


def _read_spec_at_commit(
    workspace: dict[str, object], relative: str, commit: str
) -> bytes | None:
    repo_root = Path(required_string(workspace, "repo_root", "workspace manifest"))
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{commit}:{relative}"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise PromptWorkspaceError(
            "ENVIRONMENT_BLOCKER", "Git could not inspect specification evidence"
        ) from exc
    if result.returncode != 0:
        return None
    return result.stdout


def inspect_spec_document(
    workspace: dict[str, object],
    kind: str,
    *,
    commit: str | None = None,
) -> dict[str, object]:
    """Validate one managed document without exposing private workflow state."""

    schema, _, owner_schema, id_re, title = _spec_contract(kind)
    path, relative = spec_repo_path(workspace, kind)
    if commit is None:
        _require_safe_spec_path(path)
        if not path.exists():
            raw = None
        else:
            if path.stat().st_size > MAX_SPEC_BYTES:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", "specification document is too large"
                )
            raw = path.read_bytes()
    else:
        raw = _read_spec_at_commit(workspace, relative, commit)
    if raw is None:
        return {
            "kind": kind,
            "path": relative,
            "exists": False,
            "managed": False,
            "managed_sha256": None,
            "file_sha256": None,
            "surrounding_sha256": "absent",
            "rendered_surrounding_sha256": _surrounding_digest(*_new_envelope(kind)),
            "ids": [],
            "requirements": {},
            "statuses": {},
            "record_sha256": {},
        }
    if len(raw) > MAX_SPEC_BYTES or b"\x00" in raw:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document is invalid or too large"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document is not valid UTF-8"
        ) from exc
    if re.search(
        rf"(?m)^schema:\s*['\"]?{re.escape(owner_schema)}['\"]?\s*$",
        text,
    ):
        raise PromptWorkspaceError(
            "SPEC_OWNER_CONFLICT",
            f"{relative} is owned by Agentic SDLC",
        )
    start_marker, end_marker = spec_markers(kind)
    legacy_start = f"<!-- task-implementer:{kind}:start"
    legacy_end = f"<!-- task-implementer:{kind}:end -->"
    if legacy_start in text or legacy_end in text:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} requires canonical owner migration"
        )
    start_count = text.count(start_marker)
    end_count = text.count(end_marker)
    if start_count == end_count == 0:
        return {
            "kind": kind,
            "path": relative,
            "exists": True,
            "managed": False,
            "managed_sha256": None,
            "file_sha256": _digest(raw),
            "surrounding_sha256": _digest(raw),
            "rendered_surrounding_sha256": _surrounding_digest(*_append_envelope(raw)),
            "ids": [],
            "requirements": {},
            "statuses": {},
            "record_sha256": {},
        }
    if start_count != 1 or end_count != 1:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has malformed managed markers"
        )
    start = text.index(start_marker)
    body_start = start + len(start_marker)
    end = text.index(end_marker)
    if end <= body_start:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has reversed managed markers"
        )
    prefix = text[:start]
    body = text[body_start:end]
    suffix = text[end + len(end_marker) :]
    private_paths = {
        str(workspace.get(key))
        for key in ("repo_root", "source_root", "prompt_root", "runs_root")
        if isinstance(workspace.get(key), str) and str(workspace.get(key))
    }
    if any(pattern.search(body) for pattern in INTERNAL_STATE_PATTERNS) or any(
        private_path in body for private_path in private_paths
    ):
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} exposes private workflow state"
        )
    id_prefix = "TI-REQ-" if kind == "requirements" else "TI-DES-"
    heading_pattern = re.compile(
        rf"(?m)^###\s+({re.escape(id_prefix)}[0-9]{{3,}}):\s+.+$"
    )
    ids = [match.group(1) for match in heading_pattern.finditer(body)]
    managed_id_headings = re.findall(
        r"(?m)^###\s+(TI-(?:REQ|DES)-[^:\s]+):\s+.+$",
        body,
    )
    if (
        ids != managed_id_headings
        or len(ids) != len(set(ids))
        or any(id_re.fullmatch(item) is None for item in ids)
    ):
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", f"{relative} has duplicate or invalid managed IDs"
        )
    requirements: dict[str, list[str]] = {}
    statuses: dict[str, str] = {}
    record_sha256: dict[str, str] = {}
    if kind == "requirements":
        _require_bulleted_heading(body, "Task Implementer Open Questions", relative)
        _require_bulleted_heading(
            body, "Task Implementer Requirements Change Log", relative
        )
        for item in ids:
            section_match = re.search(
                rf"(?ms)^###\s+{re.escape(item)}:\s+.*?\n"
                r"(.*?)(?=^###\s+TI-REQ-|^## |\Z)",
                body,
            )
            if section_match is None:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid requirement record"
                )
            section = section_match.group(1)
            status = _required_record_field(section, "Status", item)
            if status not in {"active", "satisfied", "superseded"}:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid requirement status"
                )
            statuses[item] = status
            for label in ("Requirement", "Constraints", "Non-goals"):
                _required_record_field(section, label, item)
            _require_bulleted_subsection(section, "Acceptance criteria", item)
            _require_bulleted_subsection(section, "Verification", item)
            record_sha256[item] = _digest(
                _canonical_managed_text(section_match.group(0))
            )
    else:
        _require_bulleted_heading(body, "Task Implementer Design Change Log", relative)
        for item in ids:
            section_match = re.search(
                rf"(?ms)^###\s+{re.escape(item)}:\s+.*?\n"
                r"(.*?)(?=^###\s+TI-DES-|^## |\Z)",
                body,
            )
            if section_match is None:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid design record"
                )
            section = section_match.group(1)
            status = _required_record_field(section, "Status", item)
            if status not in {"planned", "implemented", "superseded"}:
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid design status"
                )
            statuses[item] = status
            mapping = _required_record_field(section, "Requirements", item)
            if (
                re.fullmatch(
                    r"TI-REQ-[0-9]{3,}(?:\s*,\s*TI-REQ-[0-9]{3,})*",
                    mapping,
                )
                is None
            ):
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} has no valid requirement mapping"
                )
            refs = re.findall(r"TI-REQ-[0-9]{3,}", mapping)
            if len(refs) != len(set(refs)):
                raise PromptWorkspaceError(
                    "SPEC_CONFLICT", f"{item} repeats a requirement mapping"
                )
            requirements[item] = list(dict.fromkeys(refs))
            for label in (
                "Selected approach",
                "Boundaries and interfaces",
                "Validation",
                "Rollback",
            ):
                _required_record_field(section, label, item)
            _require_bulleted_subsection(section, "Alternatives considered", item)
            _require_bulleted_subsection(section, "Implementation evidence", item)
            record_sha256[item] = _digest(
                _canonical_managed_text(section_match.group(0))
            )
    surrounding_sha256 = _surrounding_digest(
        prefix.encode("utf-8"), suffix.encode("utf-8")
    )
    return {
        "kind": kind,
        "path": relative,
        "exists": True,
        "managed": True,
        "schema": schema,
        "title": title,
        "managed_sha256": _digest(_canonical_managed_text(body)),
        "file_sha256": _digest(raw),
        "surrounding_sha256": surrounding_sha256,
        "rendered_surrounding_sha256": surrounding_sha256,
        "ids": ids,
        "requirements": requirements,
        "statuses": statuses,
        "record_sha256": record_sha256,
    }


def _spec_documents_are_tracked(workspace: dict[str, object]) -> bool:
    repo_root = Path(required_string(workspace, "repo_root", "workspace manifest"))
    for kind in ("requirements", "design"):
        _path, relative = spec_repo_path(workspace, kind)
        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_root),
                    "ls-files",
                    "--error-unmatch",
                    "--",
                    relative,
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PromptWorkspaceError(
                "ENVIRONMENT_BLOCKER", "Git could not verify specification tracking"
            ) from exc
        if result.returncode != 0:
            return False
    return True


def inspect_spec_documents(
    workspace: dict[str, object], *, commit: str | None = None
) -> dict[str, object]:
    requirements = inspect_spec_document(workspace, "requirements", commit=commit)
    design = inspect_spec_document(workspace, "design", commit=commit)
    requirement_ids = set(map(str, requirements["ids"]))
    for design_id, refs in dict(design["requirements"]).items():
        unknown = set(map(str, refs)) - requirement_ids
        if unknown:
            raise PromptWorkspaceError(
                "SPEC_CONFLICT",
                f"{design_id} maps unknown requirements: {', '.join(sorted(unknown))}",
            )
    complete_managed_specs = all(
        bool(record["exists"]) and bool(record["managed"])
        for record in (requirements, design)
    )
    if complete_managed_specs:
        applicable_requirements = {
            identifier
            for identifier, status in dict(requirements["statuses"]).items()
            if status != "superseded"
        }
        current_designs = {
            identifier
            for identifier, status in dict(design["statuses"]).items()
            if status != "superseded"
        }
        covered_requirements = {
            str(requirement)
            for design_id, mappings in dict(design["requirements"]).items()
            if design_id in current_designs
            for requirement in mappings
        }
        if covered_requirements != applicable_requirements:
            missing = sorted(applicable_requirements - covered_requirements)
            extra = sorted(covered_requirements - applicable_requirements)
            details = []
            if missing:
                details.append(f"unmapped requirements: {', '.join(missing)}")
            if extra:
                details.append(f"non-applicable mappings: {', '.join(extra)}")
            raise PromptWorkspaceError("SPEC_CONFLICT", "; ".join(details))
    next_requirement = (
        max(
            (
                int(REQUIREMENT_ID_RE.fullmatch(item).group(1))
                for item in requirement_ids
            ),
            default=0,
        )
        + 1
    )
    design_ids = set(map(str, design["ids"]))
    next_design = (
        max(
            (int(DESIGN_ID_RE.fullmatch(item).group(1)) for item in design_ids),
            default=0,
        )
        + 1
    )
    receipt = None
    if (
        complete_managed_specs
        and commit is None
        and _spec_documents_are_tracked(workspace)
    ):
        source_root = Path(
            required_string(workspace, "source_root", "workspace manifest")
        )
        validator = (
            Path(__file__).resolve().parents[2]
            / "maintain-project-specs"
            / "scripts"
            / "validate_project_specs.py"
        )
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(validator),
                    "--project-root",
                    str(source_root),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
            authoritative = json.loads(completed.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise PromptWorkspaceError(
                "SPEC_CONFLICT", "shared specification validation could not run"
            ) from error
        if completed.returncode != 0 or not isinstance(authoritative, dict):
            raise PromptWorkspaceError(
                "SPEC_CONFLICT", "shared specification validation failed"
            )
        receipt = authoritative
    return {
        "requirements": requirements,
        "design": design,
        "next_requirement_id": f"TI-REQ-{next_requirement:03d}",
        "next_design_id": f"TI-DES-{next_design:03d}",
        "project_agent_spec_receipt": receipt,
    }


def verify_requirements_refinement_contract(
    workspace: dict[str, object],
    run_dir: Path,
    run_state: dict[str, object],
) -> dict[str, object]:
    """Bind a ready refinement ledger to the latest prompt and managed specs."""

    refinement = load_requirements_refinement(run_dir, required=True)
    assert refinement is not None
    if (
        refinement.get("prompt_id") != run_state.get("prompt_id")
        or refinement.get("revision") != run_state.get("latest_revision")
        or refinement.get("intent_sha256") != run_state.get("latest_intent_sha256")
        or refinement.get("status") != "ready"
    ):
        raise PromptWorkspaceError(
            "REQUIREMENTS_REFINEMENT_REQUIRED",
            "latest prompt intent has no ready requirements refinement contract",
        )
    inspected = inspect_spec_documents(workspace)
    requirements = inspected["requirements"]
    if not requirements.get("managed") or requirements.get(
        "managed_sha256"
    ) != refinement.get("compiled_requirements_sha256"):
        raise PromptWorkspaceError(
            "REQUIREMENTS_REFINEMENT_REQUIRED",
            "compiled requirements digest does not match managed product truth",
        )
    project_root = Path(required_string(workspace, "source_root", "workspace manifest"))
    impact, impact_sha256 = _publish_prompt_impact(
        run_dir, project_root, run_state, refinement
    )
    return {
        "refinement": refinement,
        "specs": inspected,
        "impact": impact,
        "impact_sha256": impact_sha256,
        "public_impact": public_impact_status(impact),
    }


def _contract_git(
    git_root: Path, arguments: list[str], label: str, *, text: bool = False
) -> bytes | str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(git_root), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=text,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"could not {label}"
        ) from error
    if completed.returncode != 0:
        raise PromptWorkspaceError("EXECUTION_STATE_INVALID", f"could not {label}")
    return completed.stdout


def _require_exact_contract_checkout(git_root: Path, contract_commit: str) -> None:
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", contract_commit) is None:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent contract commit is invalid"
        )
    head = str(
        _contract_git(
            git_root, ["rev-parse", "HEAD"], "inspect contract HEAD", text=True
        )
    ).strip()
    status = _contract_git(
        git_root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        "inspect contract worktree",
    )
    if head != contract_commit or status != b"":
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "project-agent contract checkout is not clean and exact",
        )


def _contract_blob(
    git_root: Path, contract_commit: str, path: Path, label: str
) -> bytes:
    try:
        relative = path.resolve().relative_to(git_root).as_posix()
    except ValueError as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"{label} escaped the contract checkout"
        ) from error
    result = _contract_git(
        git_root,
        ["show", f"{contract_commit}:{relative}"],
        f"read committed {label}",
    )
    assert isinstance(result, bytes)
    return result


def _project_agent_private_member(
    private_root: Path, raw_path: object, label: str
) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"project-agent {label} path is invalid"
        )
    path = (private_root / raw_path).resolve()
    if (
        path.parent != private_root.resolve()
        or path.is_symlink()
        or not path.is_file()
        or stat.S_IMODE(path.stat().st_mode) != 0o600
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", f"project-agent {label} is missing or unsafe"
        )
    return path


def _verify_carried_project_agent_render(
    private_root: Path,
    receipt_path: Path,
    state: dict[str, object],
) -> dict[str, object]:
    """Statically revalidate an unchanged render carried to a descendant commit."""

    manifest_path = _project_agent_private_member(
        private_root, state.get("manifest_path"), "manifest"
    )
    decision_path = _project_agent_private_member(
        private_root, state.get("decision_path"), "decision"
    )
    rules_path = _project_agent_private_member(
        private_root, state.get("rules_path"), "rendered rules"
    )
    try:
        manifest_raw = manifest_path.read_bytes()
        decision_raw = decision_path.read_bytes()
        rendered = rules_path.read_bytes()
        manifest = json.loads(manifest_raw)
        decision = json.loads(decision_raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent carried render is invalid"
        ) from error
    if not isinstance(manifest, dict) or not isinstance(decision, dict):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent carried render is invalid"
        )
    try:
        validate_project_agent_manifest(manifest)
        disposition, body, decision_sha256, _approval = validate_project_agent_decision(
            decision, manifest
        )
    except ProjectInstructionsError as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent carried render is invalid"
        ) from error
    expected_manifest_state = {
        "project_root": manifest.get("project_root"),
        "git_root": manifest.get("git_root"),
        "project_scope": manifest.get("project_scope"),
        "spec_owner": manifest.get("spec_owner"),
        "requirements": manifest.get("requirements"),
        "design": manifest.get("design"),
        "spec_receipt": manifest.get("spec_receipt"),
    }
    if (
        any(state.get(key) != value for key, value in expected_manifest_state.items())
        or manifest.get("spec_receipt")
        != {
            "path": str(receipt_path.resolve()),
            "sha256": _digest(receipt_path.read_bytes()),
        }
        or state.get("manifest_file_sha256") != _digest(manifest_raw)
        or state.get("decision_file_sha256") != _digest(decision_raw)
        or state.get("decision_sha256") != decision_sha256
        or state.get("disposition") != disposition
        or state.get("rules_sha256") != _digest(rendered)
        or rendered != (body or b"")
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent carried render is stale"
        )
    return {
        "status": "ok",
        "disposition": disposition,
        "repository_mutated": False,
    }


def verify_project_agent_contract(
    workspace: dict[str, object],
    run_dir: Path,
    project_root: Path,
    contract_commit: str,
) -> dict[str, object]:
    """Verify current project-agent state before a managed-spec dispatch."""

    selected = project_root.resolve()
    try:
        completed = subprocess.run(
            ["git", "-C", str(selected), "rev-parse", "--show-toplevel"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent contract root is invalid"
        ) from error
    contract_workspace = dict(workspace)
    git_root = Path(completed.stdout.strip()).resolve()
    _require_exact_contract_checkout(git_root, contract_commit)
    contract_workspace["repo_root"] = str(git_root)
    contract_workspace["source_root"] = str(selected)
    inspected = inspect_spec_documents(contract_workspace)
    receipt = inspected["project_agent_spec_receipt"]
    if receipt is None:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT",
            "current managed specs have no project-agent validation receipt",
        )
    committed_requirements = _contract_blob(
        git_root,
        contract_commit,
        selected / "docs" / "requirements.md",
        "requirements",
    )
    committed_design = _contract_blob(
        git_root,
        contract_commit,
        selected / "docs" / "design.md",
        "design",
    )
    if (
        _digest(committed_requirements) != dict(receipt["requirements"])["sha256"]
        or _digest(committed_design) != dict(receipt["design"])["sha256"]
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID",
            "project-agent spec receipt is not bound to the contract commit",
        )
    private_root = run_dir / "orchestration" / "project-agent-instructions"
    receipt_path = run_dir / "orchestration" / "project-agent-spec-receipt.json"
    state_path = private_root / "state.json"
    if (
        private_root.is_symlink()
        or not private_root.is_dir()
        or stat.S_IMODE(private_root.stat().st_mode) != 0o700
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent private root is missing or unsafe"
        )
    for path, label in ((receipt_path, "spec receipt"), (state_path, "state")):
        if (
            path.is_symlink()
            or not path.is_file()
            or stat.S_IMODE(path.stat().st_mode) != 0o600
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", f"project-agent {label} is missing or unsafe"
            )
    try:
        recorded_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent private state is invalid"
        ) from error
    recorded_contract = dict(recorded_receipt)
    current_contract = dict(receipt)
    recorded_head = recorded_contract.pop("git_head", None)
    current_contract.pop("git_head", None)
    receipt_ancestry_valid = False
    if (
        recorded_contract == current_contract
        and isinstance(recorded_head, str)
        and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", recorded_head) is not None
    ):
        try:
            _contract_git(
                git_root,
                ["merge-base", "--is-ancestor", recorded_head, contract_commit],
                "verify project-agent receipt ancestry",
            )
        except PromptWorkspaceError:
            pass
        else:
            receipt_ancestry_valid = True
    receipt_carried_forward = (
        recorded_contract == current_contract and receipt_ancestry_valid
    )
    if not isinstance(state, dict) or (
        recorded_receipt != receipt and not receipt_carried_forward
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent spec receipt is stale"
        )
    receipt_record = state.get("spec_receipt")
    if (
        state.get("schema") != "project-agent-instructions.render-state.v1"
        or state.get("spec_owner") != "maintain-project-specs"
        or state.get("project_root") != str(selected)
        or state.get("disposition")
        not in {
            "needed",
            "existing-sufficient",
            "not-needed",
        }
        or state.get("repository_mutated") is not False
        or not isinstance(receipt_record, dict)
        or receipt_record.get("path") != str(receipt_path.resolve())
        or receipt_record.get("sha256") != _digest(receipt_path.read_bytes())
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent rendered state is stale"
        )
    helper = (
        Path(__file__).resolve().parents[2]
        / "project-agent-instructions"
        / "scripts"
        / "project_agent_instructions.py"
    )
    if receipt_carried_forward and recorded_receipt != receipt:
        verified = _verify_carried_project_agent_render(
            private_root, receipt_path, state
        )
        completed_returncode = 0
    else:
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(helper),
                    "render",
                    "--private-root",
                    str(private_root),
                    "--manifest",
                    str(state.get("manifest_path", "")),
                    "--decision",
                    str(state.get("decision_path", "")),
                    "--output",
                    str(state.get("rules_path", "")),
                    "--state",
                    str(state_path),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            verified = json.loads(completed.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "project-agent verification could not run"
            ) from error
        completed_returncode = completed.returncode
    if (
        completed_returncode != 0
        or not isinstance(verified, dict)
        or verified.get("status") != "ok"
        or verified.get("disposition") != state.get("disposition")
        or verified.get("repository_mutated") is not False
    ):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent verification failed"
        )
    manifest_path = (private_root / str(state.get("manifest_path", ""))).resolve()
    if manifest_path.parent != private_root.resolve():
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent manifest path is unsafe"
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent manifest is invalid"
        ) from error
    ancestors = manifest.get("ancestor_project_instructions")
    if not isinstance(ancestors, list):
        raise PromptWorkspaceError(
            "EXECUTION_STATE_INVALID", "project-agent instruction chain is invalid"
        )
    for entry in ancestors:
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "project-agent instruction chain is invalid"
            )
        blob = _contract_blob(
            git_root,
            contract_commit,
            Path(str(entry.get("path", ""))),
            "ancestor project instruction",
        )
        if _digest(blob) != entry["sha256"]:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "ancestor project instruction is not bound to the contract commit",
            )
    active = manifest.get("active_project_instruction")
    active_path = active.get("path") if isinstance(active, dict) else None
    if active_path is not None:
        if not isinstance(active_path, str):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "active project instruction is invalid"
            )
        active = Path(active_path)
        try:
            active_digest = _digest(active.read_bytes())
        except OSError as error:
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID", "active project instruction is unreadable"
            ) from error
        if (
            _digest(
                _contract_blob(
                    git_root, contract_commit, active, "active project instruction"
                )
            )
            != active_digest
        ):
            raise PromptWorkspaceError(
                "EXECUTION_STATE_INVALID",
                "active project instruction is not bound to the contract commit",
            )
    _require_exact_contract_checkout(git_root, contract_commit)
    return {
        "status": "ok",
        "disposition": state["disposition"],
        "rules_path": str(private_root / str(state["rules_path"])),
        "rules_sha256": state["rules_sha256"],
        "active_instruction_path": active_path,
        "repository_mutated": False,
    }


def new_spec_document(kind: str, managed_body: str) -> bytes:
    """Render a missing specification document with its canonical envelope."""

    _, _, _, _, title = _spec_contract(kind)
    start, end = spec_markers(kind)
    body = managed_body.strip()
    return f"# {title}\n\n{start}\n{body}\n{end}\n".encode("utf-8")


def append_managed_region(raw: bytes, kind: str, managed_body: str) -> bytes:
    """Append one managed region to a generic document without changing its bytes."""

    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document is not valid UTF-8"
        ) from exc
    start, end = spec_markers(kind)
    if start.encode() in raw or end.encode() in raw:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document already has managed markers"
        )
    prefix, newline = _append_envelope(raw)
    body = managed_body.strip().replace("\r\n", "\n").encode("utf-8")
    if newline == b"\r\n":
        body = body.replace(b"\n", b"\r\n")
    return (
        prefix
        + start.encode("utf-8")
        + newline
        + body
        + newline
        + end.encode("utf-8")
        + newline
    )


def replace_managed_region(raw: bytes, kind: str, managed_body: str) -> bytes:
    """Replace only a validated managed region and preserve its envelope bytes."""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document is not valid UTF-8"
        ) from exc
    start_marker, end_marker = spec_markers(kind)
    if text.count(start_marker) != 1 or text.count(end_marker) != 1:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document has malformed managed markers"
        )
    start = text.index(start_marker) + len(start_marker)
    end = text.index(end_marker)
    if end <= start:
        raise PromptWorkspaceError(
            "SPEC_CONFLICT", "specification document has reversed managed markers"
        )
    newline = "\r\n" if "\r\n" in text else "\n"
    body = managed_body.strip().replace("\r\n", "\n").replace("\n", newline)
    return (text[:start] + newline + body + newline + text[end:]).encode("utf-8")
