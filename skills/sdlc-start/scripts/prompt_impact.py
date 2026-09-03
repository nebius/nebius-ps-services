"""Agentic SDLC adapter for owner-validated prompt impact evidence."""

from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Agentic SDLC currently targets POSIX hosts.
    fcntl = None  # type: ignore[assignment]


SHARED_PROJECT_SPEC_SCRIPTS = (
    Path(__file__).resolve().parents[2] / "maintain-project-specs" / "scripts"
)
if str(SHARED_PROJECT_SPEC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_PROJECT_SPEC_SCRIPTS))

from project_specs_lib.contracts import (  # noqa: E402
    ProjectSpecError,
    _read_file as owner_read_file,
    canonical_digest,
)
from project_specs_lib.impact import (  # noqa: E402
    CLAIM_SCHEMA as SHARED_CLAIM_SCHEMA,
    public_impact_status,
    validate_prompt_impact as validate_shared_prompt_impact,
)


CLAIM_SCHEMA = "agentic-sdlc/prompt-impact-claim-v1"
RECEIPT_SCHEMA = "agentic-sdlc/prompt-impact-receipt-v1"
LEDGER_SCHEMA = "agentic-sdlc/prompt-impact-ledger-v1"
EXECUTION_BASIS_SCHEMA = "agentic-sdlc/prompt-impact-execution-basis-v1"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
REVISION_RE = re.compile(r"r[0-9]{4}\Z")
FEATURE_RE = re.compile(r"FEAT-[0-9]{3,}\Z")
ATTEMPT_RE = re.compile(r"attempt-([0-9]{4,12})\.json\Z")
MAX_GENERATION = 999_999_999_999


class PromptImpactError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def claim_path(run_dir: Path) -> Path:
    return run_dir / "prompt-impact-claim.json"


def _derive_prompt_impact(
    project_root: Path, *, claim: dict[str, object], **arguments: object
) -> dict[str, object]:
    """Use the pure shared validator without inheriting lifecycle-owned schemas."""

    shared_claim = {**claim, "schema": SHARED_CLAIM_SCHEMA}
    receipt = validate_shared_prompt_impact(
        project_root,
        workflow="agentic-sdlc",
        claim=shared_claim,
        **arguments,
    )
    return {**receipt, "schema": RECEIPT_SCHEMA}


def _root(run_dir: Path) -> Path:
    return run_dir / "prompt-impact"


def _require_regular(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise PromptImpactError("PROMPT_IMPACT_REQUIRED", f"{label} is missing") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size > 4 * 1024 * 1024
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or (hasattr(os, "getuid") and metadata.st_uid != os.getuid())
    ):
        raise PromptImpactError("RUN_STATE_INVALID", f"{label} is unsafe")


def _read(path: Path, label: str) -> dict[str, Any]:
    _require_regular(path, label)
    metadata = path.lstat()
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PromptImpactError("RUN_STATE_INVALID", f"{label} is unsafe") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise PromptImpactError(
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
                raise PromptImpactError("RUN_STATE_INVALID", f"{label} is too large")
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
            raise PromptImpactError(
                "CONCURRENT_MODIFICATION", f"{label} changed while reading"
            )
    finally:
        os.close(descriptor)
    try:
        value = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PromptImpactError("RUN_STATE_INVALID", f"{label} is invalid") from error
    if not isinstance(value, dict):
        raise PromptImpactError("RUN_STATE_INVALID", f"{label} is invalid")
    return value


def _stable(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise PromptImpactError("RUN_STATE_INVALID", "prompt impact directory is unsafe")
    if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
        raise PromptImpactError("RUN_STATE_INVALID", "prompt impact directory is unsafe")
    path.chmod(0o700)


def _atomic(path: Path, value: object) -> None:
    _ensure_dir(path.parent)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_stable(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _exclusive(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_stable(value))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    path.chmod(0o600)


@contextmanager
def _publication_lock(run_dir: Path):
    if fcntl is None:
        raise PromptImpactError(
            "ENVIRONMENT_BLOCKER", "prompt impact publication requires a POSIX host"
        )
    root = _root(run_dir)
    _ensure_dir(root)
    path = root / "publication.lock"
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as error:
        raise PromptImpactError(
            "RUN_STATE_INVALID", "prompt impact publication lock is unsafe"
        ) from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (hasattr(os, "getuid") and opened.st_uid != os.getuid())
        ):
            raise PromptImpactError(
                "RUN_STATE_INVALID", "prompt impact publication lock is unsafe"
            )
        os.fchmod(descriptor, 0o600)
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise PromptImpactError(
                        "WORKSPACE_BUSY", "another prompt impact publication is active"
                    )
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _ledger_fingerprint(run_dir: Path) -> str | None:
    path = _root(run_dir) / "ledger.json"
    if not path.exists():
        return None
    return canonical_digest(_read(path, "prompt impact ledger"))


def _max_attempt_generation(root: Path) -> int:
    maximum = 0
    for path in root.iterdir():
        if not path.name.startswith("attempt-"):
            continue
        match = ATTEMPT_RE.fullmatch(path.name)
        if match is None:
            raise PromptImpactError("RUN_STATE_INVALID", "impact attempt name is invalid")
        _require_regular(path, "prompt impact attempt")
        maximum = max(maximum, int(match.group(1)))
    if maximum >= MAX_GENERATION:
        raise PromptImpactError("RUN_STATE_INVALID", "impact generation is exhausted")
    return maximum


def _publish_ledger_cas(
    run_dir: Path, expected_fingerprint: str | None, ledger: dict[str, object]
) -> None:
    if _ledger_fingerprint(run_dir) != expected_fingerprint:
        raise PromptImpactError(
            "CONCURRENT_MODIFICATION", "prompt impact ledger changed during publication"
        )
    _atomic(_root(run_dir) / "ledger.json", ledger)


def load_current(
    run_dir: Path, *, required: bool = False
) -> tuple[dict[str, Any], str] | None:
    root = _root(run_dir)
    ledger_path = root / "ledger.json"
    if not ledger_path.exists():
        if required:
            raise PromptImpactError(
                "PROMPT_IMPACT_REQUIRED",
                "the active run predates prompt impact receipts and requires forward reconciliation",
            )
        return None
    if root.is_symlink() or not root.is_dir() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise PromptImpactError("RUN_STATE_INVALID", "prompt impact directory is unsafe")
    ledger = _read(ledger_path, "prompt impact ledger")
    if (
        set(ledger) != {"schema", "workflow", "current"}
        or ledger.get("schema") != LEDGER_SCHEMA
        or ledger.get("workflow") != "agentic-sdlc"
        or not isinstance(ledger.get("current"), dict)
    ):
        raise PromptImpactError("RUN_STATE_INVALID", "prompt impact ledger is invalid")
    current = dict(ledger["current"])
    generation = current.get("generation")
    name = f"attempt-{generation:04d}.json" if isinstance(generation, int) else None
    if (
        set(current) != {"generation", "revision", "path", "sha256"}
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
        or generation > MAX_GENERATION
        or current.get("path") != name
        or REVISION_RE.fullmatch(str(current.get("revision") or "")) is None
        or SHA256_RE.fullmatch(str(current.get("sha256") or "")) is None
    ):
        raise PromptImpactError("RUN_STATE_INVALID", "prompt impact pointer is invalid")
    receipt = _read(root / str(name), "prompt impact attempt")
    receipt_sha256 = canonical_digest(receipt)
    if (
        receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("workflow") != "agentic-sdlc"
        or receipt.get("generation") != generation
        or receipt.get("revision") != current.get("revision")
        or receipt_sha256 != current.get("sha256")
    ):
        raise PromptImpactError("RUN_STATE_INVALID", "prompt impact attempt is invalid")
    return receipt, receipt_sha256


def publish(
    project_root: Path,
    run_dir: Path,
    binding: dict[str, object],
    refinement: dict[str, object],
) -> tuple[dict[str, Any], str]:
    with _publication_lock(run_dir):
        return _publish_locked(project_root, run_dir, binding, refinement)


def _publish_locked(
    project_root: Path,
    run_dir: Path,
    binding: dict[str, object],
    refinement: dict[str, object],
) -> tuple[dict[str, Any], str]:
    revisions = binding.get("revisions")
    latest = revisions[-1] if isinstance(revisions, list) and revisions else None
    if not isinstance(latest, dict):
        raise PromptImpactError("RUN_STATE_INVALID", "prompt binding has no latest revision")
    claim = _read(claim_path(run_dir), "prompt impact claim")
    if claim.get("schema") != CLAIM_SCHEMA:
        raise PromptImpactError("RUN_STATE_INVALID", "prompt impact claim schema is invalid")
    expected_ledger = _ledger_fingerprint(run_dir)
    current = load_current(run_dir, required=False)
    prior = current[0] if current is not None else None
    prior_sha256 = current[1] if current is not None else None
    if prior is not None:
        prior_transition = prior.get("spec_transition")
        retry_prior_spec = (
            prior_transition.get("prior_spec_receipt_sha256")
            if isinstance(prior_transition, dict)
            else prior.get("spec_receipt_sha256")
        )
        try:
            retry = _derive_prompt_impact(
                project_root,
                prompt_id=str(binding["prompt_id"]),
                revision=str(latest["revision"]),
                prompt_sha256=str(latest["sha256"]),
                intent_sha256=str(latest.get("intent_sha256") or latest["sha256"]),
                refinement=refinement,
                claim=claim,
                prior_impact_sha256=prior.get("prior_impact_sha256"),
                prior_spec_receipt_sha256=retry_prior_spec,
                generation=int(prior["generation"]),
            )
        except ProjectSpecError as error:
            raise PromptImpactError("PROMPT_IMPACT_REQUIRED", error.message) from error
        if canonical_digest(retry) == prior_sha256:
            if _ledger_fingerprint(run_dir) != expected_ledger:
                raise PromptImpactError(
                    "CONCURRENT_MODIFICATION",
                    "prompt impact ledger changed during publication",
                )
            return prior, str(prior_sha256)
    root = _root(run_dir)
    _ensure_dir(root)
    generation = 1 if prior is None else int(prior["generation"]) + 1
    maximum = _max_attempt_generation(root)
    while True:
        try:
            receipt = _derive_prompt_impact(
                project_root,
                prompt_id=str(binding["prompt_id"]),
                revision=str(latest["revision"]),
                prompt_sha256=str(latest["sha256"]),
                intent_sha256=str(latest.get("intent_sha256") or latest["sha256"]),
                refinement=refinement,
                claim=claim,
                prior_impact_sha256=prior_sha256,
                prior_spec_receipt_sha256=(
                    prior.get("spec_receipt_sha256") if prior is not None else None
                ),
                generation=generation,
            )
        except ProjectSpecError as error:
            raise PromptImpactError("PROMPT_IMPACT_REQUIRED", error.message) from error
        name = f"attempt-{generation:04d}.json"
        attempt_path = root / name
        receipt_sha256 = canonical_digest(receipt)
        if not attempt_path.exists():
            try:
                _exclusive(attempt_path, receipt)
            except FileExistsError:
                maximum = _max_attempt_generation(root)
                generation = maximum + 1
                continue
            else:
                break
        existing = _read(attempt_path, "prompt impact attempt")
        if canonical_digest(existing) == receipt_sha256:
            receipt = existing
            break
        generation = maximum + 1
    receipt_sha256 = canonical_digest(receipt)
    _publish_ledger_cas(
        run_dir,
        expected_ledger,
        {
            "schema": LEDGER_SCHEMA,
            "workflow": "agentic-sdlc",
            "current": {
                "generation": generation,
                "revision": receipt["revision"],
                "path": name,
                "sha256": receipt_sha256,
            },
        },
    )
    return receipt, receipt_sha256


def verify_current(
    run_dir: Path, project_root: Path
) -> tuple[dict[str, Any], str]:
    current = load_current(run_dir, required=True)
    assert current is not None
    receipt, receipt_sha256 = current
    binding = _read(run_dir / "prompt.json", "prompt binding")
    revisions = binding.get("revisions")
    latest = revisions[-1] if isinstance(revisions, list) and revisions else None
    if (
        not isinstance(latest, dict)
        or binding.get("prompt_id") != receipt.get("prompt_id")
        or latest.get("revision") != receipt.get("revision")
        or (latest.get("intent_sha256") or latest.get("sha256"))
        != receipt.get("intent_sha256")
    ):
        raise PromptImpactError(
            "PROMPT_IMPACT_REQUIRED", "the latest accepted prompt has no impact settlement"
        )
    for kind in ("requirements", "design"):
        path = project_root / "docs" / f"{kind}.md"
        try:
            raw, _text = owner_read_file(path, f"{kind} specification")
        except ProjectSpecError as error:
            raise PromptImpactError("SPEC_CONFLICT", error.message) from error
        if hashlib.sha256(raw).hexdigest() != receipt.get(f"{kind}_sha256"):
            raise PromptImpactError(
                "REPLAN_REQUIRED", "canonical project specs drifted after impact settlement"
            )
    return receipt, receipt_sha256


def _basis_path(run_dir: Path, feature_id: str) -> Path:
    if FEATURE_RE.fullmatch(feature_id) is None:
        raise PromptImpactError("RUN_STATE_INVALID", "feature ID is invalid")
    return _root(run_dir) / "execution" / f"{feature_id}.json"


def settle_execution(
    run_dir: Path,
    feature_id: str,
    plan_digest: str,
    project_root: Path,
    allow_basis_creation: bool = False,
) -> dict[str, object]:
    if SHA256_RE.fullmatch(plan_digest) is None:
        raise PromptImpactError("RUN_STATE_INVALID", "execution plan digest is invalid")
    receipt, receipt_sha256 = verify_current(run_dir, project_root)
    path = _basis_path(run_dir, feature_id)
    existing = _read(path, "prompt impact execution basis") if path.exists() else None
    if (
        existing is None
        and not allow_basis_creation
        and receipt.get("plan_action") == "replan_required"
    ):
        raise PromptImpactError(
            "REPLAN_REQUIRED",
            "material impact for active pre-basis execution needs a new plan",
        )
    if (
        existing is not None
        and existing.get("plan_digest") == plan_digest
        and receipt.get("plan_action") == "replan_required"
        and existing.get("latest_settled_revision") != receipt.get("revision")
    ):
        raise PromptImpactError(
            "REPLAN_REQUIRED", "material prompt impact requires a new execution plan"
        )
    plan_basis_revision = (
        existing.get("plan_basis_revision")
        if existing is not None
        and existing.get("plan_digest") == plan_digest
        and receipt.get("plan_action") == "retain_plan"
        else receipt["revision"]
    )
    value = {
        "schema": EXECUTION_BASIS_SCHEMA,
        "feature_id": feature_id,
        "plan_digest": plan_digest,
        "plan_basis_revision": plan_basis_revision,
        "latest_settled_revision": receipt["revision"],
        "impact_sha256": receipt_sha256,
        "spec_receipt_sha256": receipt["spec_receipt_sha256"],
        "spec_transition_sha256": receipt.get("spec_transition_sha256"),
        "plan_action": receipt["plan_action"],
    }
    _atomic(path, value)
    return value


def verify_execution(
    run_dir: Path, feature_id: str, plan_digest: str, project_root: Path
) -> dict[str, object]:
    receipt, receipt_sha256 = verify_current(run_dir, project_root)
    value = _read(_basis_path(run_dir, feature_id), "prompt impact execution basis")
    if (
        set(value)
        != {
            "schema",
            "feature_id",
            "plan_digest",
            "plan_basis_revision",
            "latest_settled_revision",
            "impact_sha256",
            "spec_receipt_sha256",
            "spec_transition_sha256",
            "plan_action",
        }
        or value.get("schema") != EXECUTION_BASIS_SCHEMA
        or value.get("feature_id") != feature_id
        or value.get("plan_digest") != plan_digest
        or value.get("latest_settled_revision") != receipt.get("revision")
        or value.get("impact_sha256") != receipt_sha256
        or value.get("spec_receipt_sha256") != receipt.get("spec_receipt_sha256")
        or value.get("spec_transition_sha256")
        != receipt.get("spec_transition_sha256")
        or value.get("plan_action") != receipt.get("plan_action")
        or (
            receipt.get("plan_action") == "replan_required"
            and value.get("plan_basis_revision") != receipt.get("revision")
        )
    ):
        raise PromptImpactError("REPLAN_REQUIRED", "execution prompt impact basis is stale")
    return value


def public_execution_bases(run_dir: Path) -> list[dict[str, str]]:
    directory = _root(run_dir) / "execution"
    if not directory.exists():
        return []
    if directory.is_symlink() or not directory.is_dir():
        raise PromptImpactError("RUN_STATE_INVALID", "impact execution directory is unsafe")
    rows: list[dict[str, str]] = []
    for path in sorted(directory.glob("FEAT-*.json")):
        value = _read(path, "prompt impact execution basis")
        feature_id = str(value.get("feature_id") or "")
        revision = str(value.get("plan_basis_revision") or "")
        if FEATURE_RE.fullmatch(feature_id) is None or REVISION_RE.fullmatch(revision) is None:
            raise PromptImpactError("RUN_STATE_INVALID", "impact execution basis is invalid")
        rows.append({"feature_id": feature_id, "plan_basis_revision": revision})
    return rows


__all__ = [
    "CLAIM_SCHEMA",
    "PromptImpactError",
    "claim_path",
    "load_current",
    "publish",
    "public_impact_status",
    "public_execution_bases",
    "settle_execution",
    "verify_current",
    "verify_execution",
]
