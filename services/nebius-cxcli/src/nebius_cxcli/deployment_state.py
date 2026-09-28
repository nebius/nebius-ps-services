"""Captured deployment inputs and command-local recovery checkpoints.

Journals describe attempts, not ownership of live resources. Terraform and fresh
cluster observations establish the actual state before execution and recovery.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import stat
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from .generated_manifest import GENERATED_MANIFEST_FILENAME
from .paths import ProjectPaths
from .terraform_backend import TerraformBackendSettings

SCHEMA = "nebius-cxcli.deployment.v2"
MAX_OBJECT_BYTES = 64 * 1024 * 1024


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()


def _safe_relative(value: str) -> Path:
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or path.is_absolute()
        or any(p in {".", ".."} for p in path.parts)
    ):
        raise ValueError("Deployment generation contains an unsafe path")
    if str(path) != value or "\\" in value:
        raise ValueError("Deployment generation contains a noncanonical path")
    return Path(*path.parts)


def _read_regular(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("Deployment inputs must be single-link regular files")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            value = stream.read(MAX_OBJECT_BYTES + 1)
        if len(value) > MAX_OBJECT_BYTES:
            raise RuntimeError("Deployment input exceeds the generation size limit")
        return value
    finally:
        os.close(fd)


@dataclass(frozen=True)
class DeploymentGeneration:
    """Exact render-owned files and execution configuration, without runtime secrets."""

    manifest: Mapping[str, Any]
    files: Mapping[str, str]

    @property
    def identity(self) -> str:
        return digest(self.as_payload())

    def as_payload(self) -> dict[str, Any]:
        return {"schema": SCHEMA, "manifest": dict(self.manifest), "files": dict(self.files)}

    @classmethod
    def capture(cls, paths: ProjectPaths, manifest: Mapping[str, Any]) -> DeploymentGeneration:
        files: dict[str, str] = {}
        candidates = [
            *paths.infra_dir.glob("*.tf"),
            *paths.infra_dir.glob("*.tf.json"),
            *paths.infra_dir.glob("*.tfvars"),
            *paths.infra_dir.glob("*.tfvars.json"),
            *paths.infra_dir.glob(".terraform.lock.hcl"),
            *(path for path in paths.flux_dir.rglob("*") if not path.is_dir() or path.is_symlink()),
            *(
                path
                for path in (paths.generated_dir / "grafana_dashboards").rglob("*")
                if not path.is_dir() or path.is_symlink()
            ),
            *paths.reports_dir.glob("soperator-release-snapshot-*.json"),
        ]
        for path in sorted(set(candidates)):
            if paths.generated_dir.is_symlink() or any(
                parent.is_symlink()
                for parent in path.parents
                if parent.is_relative_to(paths.generated_dir)
            ):
                raise RuntimeError("Deployment generation cannot contain symlinked directories")
            relative = path.relative_to(paths.generated_dir).as_posix()
            files[relative] = base64.b64encode(_read_regular(path)).decode("ascii")
        # Quota observations and local locators are not desired state. Preserve
        # execution fields (including source identities, backend, and tool pins).
        frozen = json.loads(canonical_json(manifest))
        frozen.pop("quota", None)
        frozen.pop("source_contract", None)
        frozen.pop("paths", None)
        declared_root = Path(manifest.get("paths", {}).get("generated_dir") or paths.generated_dir)
        if not declared_root.is_absolute():
            declared_root = paths.repo_root / declared_root
        for target in frozen.get("deploy", {}).get("targets", []):
            raw = target.get("flux_dir")
            if raw:
                path = Path(raw)
                if not path.is_absolute():
                    path = paths.repo_root / path
                target["flux_dir"] = path.relative_to(declared_root).as_posix()
        result = cls(frozen, files)
        if len(canonical_json(result.as_payload())) > MAX_OBJECT_BYTES:
            raise RuntimeError("Deployment generation exceeds the size limit")
        return result

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any], *, expected_id: str) -> DeploymentGeneration:
        if payload.get("schema") != SCHEMA or digest(payload) != expected_id:
            raise RuntimeError("Deployment generation failed its identity check")
        manifest, files = payload.get("manifest"), payload.get("files")
        if not isinstance(manifest, dict) or not isinstance(files, dict):
            raise RuntimeError("Deployment generation is incomplete")
        for name, value in files.items():
            _safe_relative(name)
            if not isinstance(value, str):
                raise RuntimeError("Deployment generation contains invalid file data")
            base64.b64decode(value, validate=True)
        return cls(manifest, files)

    def manifest_for_paths(self, paths: ProjectPaths) -> dict[str, Any]:
        """Bind portable locators without changing frozen content or writing files."""
        manifest = json.loads(canonical_json(self.manifest))
        manifest["paths"] = {
            "generated_dir": paths.generated_dir.relative_to(paths.repo_root).as_posix(),
            "infra_dir": paths.infra_dir.relative_to(paths.repo_root).as_posix(),
            "flux_dir": paths.flux_dir.relative_to(paths.repo_root).as_posix(),
            "reports_dir": paths.reports_dir.relative_to(paths.repo_root).as_posix(),
        }
        manifest["source_contract"] = {
            "config_path": paths.config_path.relative_to(paths.repo_root).as_posix()
        }
        for target in manifest.get("deploy", {}).get("targets", []):
            if target.get("flux_dir"):
                target["flux_dir"] = (
                    (paths.generated_dir / _safe_relative(target["flux_dir"]))
                    .relative_to(paths.repo_root)
                    .as_posix()
                )
        return manifest

    def materialize(self, paths: ProjectPaths) -> dict[str, Any]:
        """Write a new private execution cache; never overwrite a rendered bundle."""
        manifest = self.manifest_for_paths(paths)
        paths.generated_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name, encoded in self.files.items():
            path = paths.generated_dir / _safe_relative(name)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(base64.b64decode(encoded, validate=True))
        output = paths.generated_dir / GENERATED_MANIFEST_FILENAME
        output.write_bytes(canonical_json(manifest) + b"\n")
        output.chmod(0o600)
        return manifest


@dataclass(frozen=True)
class ObjectVersion:
    value: Mapping[str, Any]
    etag: str


class ConditionalObjectStore(Protocol):
    def read(self, key: str) -> ObjectVersion | None: ...
    def write(self, key: str, value: Mapping[str, Any], *, etag: str | None) -> str: ...


def deployment_state_prefix(settings: TerraformBackendSettings) -> str:
    return "nebius-cxcli/deployments/" + digest(
        {
            "project": settings.project_id,
            "endpoint": settings.endpoint,
            "bucket": settings.bucket,
            "key": settings.key,
        }
    ).removeprefix("sha256:")


class DeploymentState:
    """Local recovery for one command or one explicitly selected deployment attempt."""

    def __init__(
        self,
        store: ConditionalObjectStore,
        settings: TerraformBackendSettings,
        *,
        assert_held: Callable[[], object],
        attempt: str = "",
        command: str = "",
        baseline_generation: str = "",
        baseline_targets: Sequence[str] = (),
    ) -> None:
        self.store = store
        self.assert_held = assert_held
        self.prefix = deployment_state_prefix(settings)
        self.attempt = attempt
        self.command = command
        self.baseline_generation = baseline_generation
        self.baseline_targets = tuple(baseline_targets)
        suffix = f"/attempts/{attempt}.json" if attempt else f"/{command or 'state'}.json"
        self.record_key = self.prefix + suffix

    def read(self) -> ObjectVersion | None:
        record = self.store.read(self.record_key)
        if record is None and self.command:
            completed = self.latest_completed()
            if completed:
                record = ObjectVersion(
                    {"schema": SCHEMA, "active": None, "accepted": completed}, ""
                )
        if record is not None:
            self._validate_record(record)
        if (
            self.command
            and self.baseline_generation
            and record is not None
            and record.value["active"] is None
            and not self._matches_baseline(record.value["accepted"])
        ):
            completed = self.latest_completed()
            if self._matches_baseline(completed):
                previous = record.value["accepted"]
                if previous and previous["generation"] == completed["generation"]:
                    completed = {
                        **completed,
                        "evidence": self._merge_evidence(completed, previous["evidence"]),
                    }
                # Project current completion without changing the terminal command
                # journal. Preserve its CAS version for the next command begin.
                record = ObjectVersion({**record.value, "accepted": completed}, record.etag)
        return record

    def _matches_baseline(self, accepted: Any) -> bool:
        if not isinstance(accepted, Mapping):
            return False
        targets = accepted.get("evidence", {}).get("targets", {})
        return set(self.baseline_targets) <= set(targets) and (
            self.baseline_generation == accepted.get("generation")
            or any(
                self.baseline_generation == row.get("effectiveGeneration")
                for row in targets.values()
            )
        )

    @staticmethod
    def _validate_record(record: ObjectVersion) -> None:
        value = record.value
        if value.get("schema") != SCHEMA:
            raise RuntimeError("Unsupported deployment state schema")
        if set(value) != {"schema", "active", "accepted"}:
            raise RuntimeError("Incomplete deployment authority record")
        for field in ("active", "accepted"):
            row = value[field]
            if row is None:
                continue
            if not isinstance(row, Mapping) or not re.fullmatch(
                r"sha256:[a-f0-9]{64}", str(row.get("generation", ""))
            ):
                raise RuntimeError("Invalid deployment generation authority")
            required = ("plan", "stages", "recovery") if field == "active" else ("evidence",)
            if any(not isinstance(row.get(key), Mapping) for key in required):
                raise RuntimeError("Incomplete deployment execution authority")
            if field == "accepted":
                for key in ("identities", "targets"):
                    values = row["evidence"].get(key, {})
                    if not isinstance(values, Mapping) or any(
                        not isinstance(item, Mapping) for item in values.values()
                    ):
                        raise RuntimeError("Invalid deployment completion evidence")

    def generation(self, identity: str) -> DeploymentGeneration:
        record = self.store.read(self.prefix + "/generations/" + identity.removeprefix("sha256:"))
        if record is None:
            raise RuntimeError("Authoritative deployment generation is missing")
        return DeploymentGeneration.from_payload(record.value, expected_id=identity)

    def assert_publishable(self, identity: str) -> None:
        record = self.read()
        active = record.value.get("active") if record else None
        if active and active.get("generation") != identity:
            raise RuntimeError(
                "A different deployment is active. Rerun deploy on its rendered generation "
                "before publishing new desired configuration."
            )

    def begin(
        self,
        generation: DeploymentGeneration,
        *,
        plan: Mapping[str, Any],
        recovery: Mapping[str, Any] | None = None,
    ) -> ObjectVersion:
        self.assert_held()
        record = self.read()
        self.assert_publishable(generation.identity)
        if record and record.value.get("active"):
            return record
        key = self.prefix + "/generations/" + generation.identity.removeprefix("sha256:")
        existing = self.store.read(key)
        if existing:
            DeploymentGeneration.from_payload(existing.value, expected_id=generation.identity)
        else:
            self.assert_held()
            self.store.write(key, generation.as_payload(), etag=None)
        value = {
            "schema": SCHEMA,
            "accepted": record.value.get("accepted") if record else None,
            "active": {
                "generation": generation.identity,
                "plan": dict(plan),
                "stages": {},
                "recovery": dict(recovery or {}),
            },
        }
        return self._write(value, record)

    def latest_completed(self) -> Any:
        """Optional local evidence; never consult a shared backend or active attempt."""
        record = self.store.read(self.prefix + "/state.json")
        if record is not None:
            self._validate_record(record)
        return record.value.get("accepted") if record else None

    def complete_installation_reconciliation(self, record, *, receipt_id: str) -> ObjectVersion:
        """Commit only metadata evidence; retain the exact accepted deployment."""
        from .installation_reconciliation import RECEIPTS, pending_reconciliation

        active = record.value.get("active") or {}
        target = active.get("plan", {}).get("target")
        receipt = active.get("recovery", {}).get("installationReconciliation")
        if (
            not target
            or not pending_reconciliation(record.value, target)
            or digest(receipt) != receipt_id
        ):
            raise RuntimeError("Installation reconciliation completion differs from its intent")
        accepted = json.loads(canonical_json(record.value["accepted"]))
        accepted["evidence"].setdefault(RECEIPTS, {})[receipt_id] = receipt_id
        return self._write({"schema": SCHEMA, "active": None, "accepted": accepted}, record)

    def clear_accepted(self, *, target_ref: str) -> None:
        """Forget a deleted cluster baseline only after its destroy owner proved completion."""
        record = self.read()
        if record is None:
            return
        if record.value.get("active"):
            raise RuntimeError("An active deployment must finish before cluster destruction")
        accepted = record.value.get("accepted")
        if accepted and target_ref not in accepted.get("evidence", {}).get("identities", {}):
            return  # An unrelated completion report is not destroy authority.
        if accepted:
            evidence = json.loads(canonical_json(accepted["evidence"]))
            evidence.get("identities", {}).pop(target_ref, None)
            evidence.get("targets", {}).pop(target_ref, None)
            evidence["selectedTargets"] = [
                ref for ref in evidence.get("selectedTargets", []) if ref != target_ref
            ]
            accepted = {**accepted, "evidence": evidence} if evidence.get("identities") else None
        self._write({"schema": SCHEMA, "active": None, "accepted": accepted}, record)

    def register(
        self, generation: DeploymentGeneration, *, evidence: Mapping[str, Any]
    ) -> ObjectVersion:
        """Accept independently verified onboarding without an unrecoverable active deploy."""
        self.assert_held()
        record = self.read()
        if record and record.value["active"] is not None:
            raise RuntimeError("Finish the active deployment before onboarding")
        accepted = record.value["accepted"] if record else None
        prior_identities = accepted["evidence"].get("identities", {}) if accepted else {}
        incoming = evidence.get("identities", {})
        if not incoming or any(
            not identity or (ref in prior_identities and prior_identities[ref] != identity)
            for ref, identity in incoming.items()
        ):
            raise RuntimeError("Onboarding differs from accepted cluster ownership")
        targets = {
            ref: {
                "generation": generation.identity,
                "identity": dict(identity),
                "registrationVerified": True,
            }
            for ref, identity in incoming.items()
        }
        evidence = self._merge_evidence(accepted, {**evidence, "targets": targets})
        key = self.prefix + "/generations/" + generation.identity.removeprefix("sha256:")
        existing = self.store.read(key)
        if existing:
            DeploymentGeneration.from_payload(existing.value, expected_id=generation.identity)
        else:
            self.assert_held()
            self.store.write(key, generation.as_payload(), etag=None)
        return self._write(
            {
                "schema": SCHEMA,
                "active": None,
                "accepted": {"generation": generation.identity, "evidence": dict(evidence)},
            },
            record,
        )

    def checkpoint(
        self,
        record: ObjectVersion,
        *,
        stage: str,
        evidence: Mapping[str, Any],
        acceptance: Mapping[str, Any] | None = None,
    ) -> ObjectVersion:
        value = json.loads(canonical_json(record.value))
        if not value.get("active"):
            raise RuntimeError("No active deployment to checkpoint")
        value["active"]["stages"][stage] = {
            key: item for key, item in evidence.items() if key != "recovery"
        }
        if "recovery" in evidence:
            value["active"]["recovery"] = evidence["recovery"]
        if acceptance is not None:
            value["active"]["acceptance"] = dict(acceptance)
        return self._write(value, record)

    def accept(
        self,
        record: ObjectVersion,
        *,
        evidence: Mapping[str, Any],
        derived_generations: Sequence[DeploymentGeneration] = (),
    ) -> ObjectVersion:
        active = record.value.get("active")
        if not isinstance(active, Mapping):
            raise RuntimeError("No active deployment to accept")
        semantic = active.get("plan", {}).get("semanticPlan")
        if semantic is not None and set(evidence.get("targets", {})) != set(
            semantic["selectedTargets"]
        ):
            raise RuntimeError("Acceptance is missing selected target evidence")
        from .deployment_plan import soperator_target

        target = soperator_target(
            self.generation(active["generation"]).manifest.get("runtime_config", {})
        )
        previous_targets = (
            (record.value.get("accepted") or {}).get("evidence", {}).get("targets", {})
        )
        for ref, row in evidence.get("targets", {}).items():
            requires_jail = (target is not None and ref == target[0]) or previous_targets.get(
                ref, {}
            ).get("jailState")
            if requires_jail and (not row.get("jailState") or not row.get("effectiveGeneration")):
                raise RuntimeError(
                    "Soperator acceptance cannot omit its effective jail-state authority"
                )
        # Blobs are immutable prerequisites. A failed or lost-ack state CAS cannot
        # expose a pointer to missing bytes; retry validates the existing blob.
        from .deployment_jail_state import accepted_effective_generation

        required = {
            row["effectiveGeneration"]
            for row in evidence.get("targets", {}).values()
            if row.get("effectiveGeneration")
        }
        supplied = {item.identity: item for item in derived_generations}
        if set(supplied) != required:
            raise RuntimeError("Acceptance effective-generation blobs differ from target evidence")
        for identity, generation in supplied.items():
            self.assert_held()
            key = self.prefix + "/generations/" + identity.removeprefix("sha256:")
            previous = self.store.read(key)
            if previous:
                DeploymentGeneration.from_payload(previous.value, expected_id=identity)
            else:
                self.store.write(key, generation.as_payload(), etag=None)
        for ref, row in evidence.get("targets", {}).items():
            if row.get("jailState") or row.get("effectiveGeneration"):
                if row.get("generation") != active["generation"]:
                    raise RuntimeError("Accepted jail state does not bind the active request")
                accepted_effective_generation(self, ref, row)
        evidence = self._merge_evidence(record.value.get("accepted"), evidence)
        return self._write(
            {
                "schema": SCHEMA,
                "active": None,
                "accepted": {"generation": active["generation"], "evidence": dict(evidence)},
            },
            record,
        )

    @staticmethod
    def _merge_evidence(accepted: Any, evidence: Mapping[str, Any]) -> dict[str, Any]:
        previous = accepted.get("evidence", {}) if accepted else {}
        result = {**previous, **evidence}
        for key in ("identities", "targets"):
            result[key] = {**previous.get(key, {}), **evidence.get(key, {})}
        return result

    def _write(self, value: Mapping[str, Any], record: ObjectVersion | None) -> ObjectVersion:
        self.assert_held()
        etag = self.store.write(
            self.record_key, value, etag=(record.etag or None) if record else None
        )
        self.assert_held()
        if self.record_key != self.prefix + "/state.json" and value.get("active") is None:
            # Optional local completion evidence never points at an unfinished attempt.
            latest_key = self.prefix + "/state.json"
            try:
                latest = self.store.read(latest_key)
                if latest is not None:
                    self._validate_record(latest)
                completed = dict(value)
                if self.attempt and completed.get("accepted") and latest:
                    accepted = completed["accepted"]
                    completed["accepted"] = {
                        **accepted,
                        "evidence": self._merge_evidence(
                            latest.value.get("accepted"), accepted["evidence"]
                        ),
                    }
                self.store.write(latest_key, completed, etag=latest.etag if latest else None)
            except (OSError, ValueError, RuntimeError):
                logging.getLogger(__name__).warning(
                    "Deployment completed, but its optional local completion index could not be updated"
                )
        return ObjectVersion(dict(value), etag)


_APPLICATION_GENERATION: ContextVar[tuple[Path, str, Mapping | None] | None] = ContextVar(
    "admitted_application_generation", default=None
)


@contextmanager
def admitted_application_generation(
    paths: ProjectPaths, generation: DeploymentGeneration, *, observations=None
):
    """Prevent a public execution command from switching a published candidate."""
    token = _APPLICATION_GENERATION.set(
        (paths.generated_dir.resolve(), generation.identity, observations)
    )
    try:
        yield
    finally:
        _APPLICATION_GENERATION.reset(token)


def assert_admitted_application_generation(paths: ProjectPaths, manifest: Mapping) -> None:
    expected = _APPLICATION_GENERATION.get()
    if expected is not None and paths.generated_dir.resolve() == expected[0]:
        from .generated_manifest import source_config_digest

        if (
            DeploymentGeneration.capture(paths, manifest).identity != expected[1]
            or source_config_digest(paths.config_path) != manifest["render"]["source_config_sha256"]
        ):
            raise RuntimeError("Published chart upgrade generation changed before execution")


def assert_admitted_application_targets(paths: ProjectPaths, observations: Mapping) -> None:
    expected = _APPLICATION_GENERATION.get()
    if expected is None or paths.generated_dir.resolve() != expected[0] or expected[2] is None:
        return
    for ref, previous in expected[2].items():
        if any(
            previous.get(key) != observations.get(ref, {}).get(key)
            for key in ("kubernetes_uid", "cluster_id")
        ):
            raise RuntimeError("Chart upgrade cluster identity changed before execution")
