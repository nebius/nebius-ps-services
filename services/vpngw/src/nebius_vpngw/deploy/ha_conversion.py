"""Protected conversion provenance; this record never grants resource ownership."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from .apply_context import select
from .apply_diagnostics import DiagnosticFailure, Failure
from .recovery import CURRENT


class ConversionFailure(ValueError, DiagnosticFailure):
    def __init__(self, code: str):
        self.first = Failure("conversion", code)
        super().__init__(code)


def _safe(path: Path, *, private: bool = False, missing: bool = False) -> None:
    for parent in (path, *path.parents):
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            if parent == path and missing:
                continue
            raise ConversionFailure("conversion-file-unavailable") from None
        if stat.S_ISLNK(metadata.st_mode):
            raise ConversionFailure("conversion-file-unsafe")
        if parent == path and (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.getuid()
            or (private and stat.S_IMODE(metadata.st_mode) != 0o600)
        ):
            raise ConversionFailure("conversion-file-unsafe")


def _identity(api: Any, source: dict[str, Any], region: str | None) -> dict[str, str]:
    effective = select(source)
    group = effective.get("gateway_group") or {}
    return {
        "project": api._resolve_cloud_field(
            effective.get("project_id"), field="project_id", required=True
        ),
        "region": api._resolve_vm_ha_region(effective, explicit_region=region),
        "gateway": api._resolve_cloud_field(
            group.get("name"), field="gateway_group.name", required=True
        ),
    }


def resolve(
    api: Any,
    *,
    source_path: Path,
    output: Path | None,
    force: bool,
    dry_run: bool,
    region: str | None,
    ordinary_prerequisite: Any = None,
    **kwargs: Any,
) -> Any:
    source_path = source_path.expanduser().absolute()
    _safe(source_path)
    source, source_fingerprint = api._read_safe_yaml_mapping(
        source_path, label="The source configuration"
    )
    group = source.get("gateway_group") or {}
    if (group.get("vm_ha") or {}).get("enabled") is True:
        if output is not None or force:
            raise ConversionFailure("conversion-output-on-ha-input")
        return api._VMHAEffectiveConfig(source_path, ())

    locator = source_path.with_name(source_path.name + ".ha-conversion.json")
    _safe(locator, private=True, missing=True)
    saved = api._read_regular_file_snapshot(locator)
    identity = _identity(api, source, region)
    if saved is not None:
        try:
            record = json.loads(saved[0])
            if (
                not isinstance(record, dict)
                or set(record)
                != {
                    "schema",
                    "source",
                    "source_sha256",
                    "candidate",
                    "initial_candidate_sha256",
                    "identity",
                }
                or record["schema"] != "nebius-vpngw.ha-conversion.v1"
                or not isinstance(record["candidate"], str)
                or not Path(record["candidate"]).is_absolute()
                or not isinstance(record["initial_candidate_sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", record["initial_candidate_sha256"]) is None
            ):
                raise ValueError("invalid record")
        except (ValueError, TypeError, UnicodeError):
            raise ConversionFailure("conversion-record-invalid") from None
        if record["source"] != str(source_path.resolve()):
            raise ConversionFailure("conversion-source-changed")
        if record["identity"] != identity:
            raise ConversionFailure("conversion-scope-changed")
        destination = Path(record["candidate"])
        _safe(destination)
        api._safe_destination_fingerprint(source_path, destination)
        candidate, candidate_fingerprint = api._read_safe_yaml_mapping(
            destination, label="The recorded HA configuration"
        )
        if ((candidate.get("gateway_group") or {}).get("vm_ha") or {}).get(
            "enabled"
        ) is not True or _identity(api, candidate, region) != identity:
            raise ConversionFailure("conversion-scope-changed")
        # The protected record and exact scope identify the recovery YAML even
        # when its edited content or environment prerequisites fail validation.
        # Validation still precedes selection and all deployment effects.
        if context := CURRENT.get():
            context.repair_config = destination
        api._load_config_with_region_override(destination, region=region)
        if record["source_sha256"] != source_fingerprint.sha256:
            raise ConversionFailure("conversion-source-changed")
        if output is not None and output.expanduser().absolute() != destination:
            raise ConversionFailure("conversion-output-conflict")
        if (
            api._file_fingerprint(source_path) != source_fingerprint
            or api._file_fingerprint(locator) != saved[1]
        ):
            raise ConversionFailure("conversion-record-changed")
        if ordinary_prerequisite is not None:
            ordinary_prerequisite(source_path, destination, identity)
            if (
                api._file_fingerprint(source_path) != source_fingerprint
                or api._file_fingerprint(locator) != saved[1]
                or api._file_fingerprint(destination) != candidate_fingerprint
            ):
                raise ConversionFailure("conversion-record-changed")
        if stat.S_IMODE(destination.stat().st_mode) != 0o600:
            if not force:
                if context := CURRENT.get():
                    context.repair_config = destination
                raise ConversionFailure("candidate-permissions-not-private")
            if dry_run:
                return api._vm_ha_action_required(
                    config_path=destination,
                    classification=api.VMHACommandClassification.CANDIDATE_READY,
                    health=api.VMHACommandHealth.NOT_CONFIGURED,
                    reason="candidate-permissions-not-private",
                    next_action="rerun conversion with --force without --dry-run to repair file permissions",
                )
            snapshot = api._read_regular_file_snapshot(destination)
            if snapshot is None or snapshot[1] != candidate_fingerprint:
                raise ConversionFailure("conversion-record-changed")
            api._atomic_write_text(
                destination, snapshot[0].decode(), expected_fingerprint=candidate_fingerprint
            )
        elif api._file_fingerprint(destination) != candidate_fingerprint:
            raise ConversionFailure("conversion-record-changed")
        return api._VMHAEffectiveConfig(destination, ("candidate-reused",))

    destination = output or api._default_vm_ha_candidate_path(source_path)
    destination = destination.expanduser().absolute()
    _safe(destination, missing=True)
    effective = api._resolve_vm_ha_effective_config(
        source_path=source_path,
        output=destination,
        force=force,
        dry_run=dry_run,
        region=region,
        ordinary_prerequisite=(lambda: ordinary_prerequisite(source_path, destination, identity))
        if ordinary_prerequisite is not None
        else None,
        **kwargs,
    )
    if isinstance(effective, api.VMHACommandResult):
        return effective
    if dry_run:
        return effective
    _safe(effective.path, private=True)
    candidate, candidate_fingerprint = api._read_safe_yaml_mapping(
        effective.path, label="The generated HA configuration"
    )
    if api._file_fingerprint(source_path) != source_fingerprint:
        raise ConversionFailure("conversion-source-changed")
    prepared_source = select(source)
    api._apply_nebius_region_precedence(prepared_source, explicit_region=region)
    if not api.is_vm_ha_conversion_candidate(prepared_source, candidate):
        raise ConversionFailure("conversion-output-conflict")
    record = {
        "schema": "nebius-vpngw.ha-conversion.v1",
        "source": str(source_path.resolve()),
        "source_sha256": source_fingerprint.sha256,
        "candidate": str(effective.path.resolve()),
        "initial_candidate_sha256": candidate_fingerprint.sha256,
        "identity": identity,
    }
    if api._file_fingerprint(effective.path) != candidate_fingerprint:
        raise ConversionFailure("conversion-record-changed")
    api._atomic_write_text(
        locator, json.dumps(record, sort_keys=True) + "\n", expected_fingerprint=None
    )
    return effective
