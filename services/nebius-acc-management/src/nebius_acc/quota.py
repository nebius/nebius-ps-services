from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from .config_template import QUOTA_SCHEMA_VERSION
from .errors import ConfigError


@dataclass(frozen=True)
class QuotaEntry:
    name: str
    limit: int
    region: str


@dataclass(frozen=True)
class QuotaSpec:
    per_region: dict[str, list[QuotaEntry]]
    per_project: dict[str, list[QuotaEntry]]

    def quotas_for_project(self, project_name: str, region_id: str | None) -> list[QuotaEntry]:
        region_quotas = self.per_region.get(region_id or "", []) if region_id else []
        project_quotas = self.per_project.get(project_name, [])
        # Project-specific quotas override region quotas for the same quota+region.
        return _merge_quota_lists(region_quotas, project_quotas)

    def project_names(self) -> list[str]:
        return list(self.per_project.keys())

    def is_empty(self) -> bool:
        return not self.per_region and not self.per_project


_UNIT_MULTIPLIERS = {
    "B": Decimal(1),
    "KB": Decimal(1000),
    "MB": Decimal(1000) ** 2,
    "GB": Decimal(1000) ** 3,
    "TB": Decimal(1000) ** 4,
    "PB": Decimal(1000) ** 5,
    "KIB": Decimal(1024),
    "MIB": Decimal(1024) ** 2,
    "GIB": Decimal(1024) ** 3,
    "TIB": Decimal(1024) ** 4,
    "PIB": Decimal(1024) ** 5,
}


def load_quota_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Quota file not found: {path}")
    text = path.read_text()
    if path.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(text)
    elif path.suffix.lower() == ".json":
        data = json.loads(text)
    else:
        raise ConfigError("Quota file must be .json, .yaml, or .yml")
    if data is None:
        raise ConfigError("Quota file is empty")
    if not isinstance(data, dict):
        raise ConfigError("Quota file must contain a JSON/YAML object")
    return data


def parse_quota_file_spec(data: dict[str, Any]) -> QuotaSpec:
    version = data.get("version")
    if version is None:
        raise ConfigError("Quota file version is required")
    if not isinstance(version, int) or version < 1:
        raise ConfigError("Quota file version must be an integer >= 1")
    if version != QUOTA_SCHEMA_VERSION:
        raise ConfigError(
            f"Unsupported quota file version: {version}. "
            f"Expected version {QUOTA_SCHEMA_VERSION}."
        )
    tenant_id = data.get("tenant_id")
    if not tenant_id or not isinstance(tenant_id, str):
        raise ConfigError("Quota file requires tenant_id")
    regions = data.get("regions")
    projects = data.get("projects")

    per_region: dict[str, list[QuotaEntry]] = {}
    per_project: dict[str, list[QuotaEntry]] = {}

    if regions is not None:
        if not isinstance(regions, dict):
            raise ConfigError("regions must be a mapping of region IDs to quota entries")
        for region_id, entries in regions.items():
            if not isinstance(region_id, str) or not region_id.strip():
                raise ConfigError("region IDs must be non-empty strings")
            per_region[region_id] = parse_quota_entries(
                entries,
                default_region=region_id,
                require_region=False,
                context=f"regions.{region_id}",
            )

    if projects is not None:
        if not isinstance(projects, dict):
            raise ConfigError("projects must be a mapping of project names to quota entries")
        for project_name, entries in projects.items():
            if not isinstance(project_name, str) or not project_name.strip():
                raise ConfigError("project names must be non-empty strings")
            per_project[project_name] = parse_quota_entries(
                entries,
                default_region=None,
                require_region=True,
                context=f"projects.{project_name}",
            )

    if not per_region and not per_project:
        raise ConfigError("Quota file must define regions or projects")

    return QuotaSpec(per_region=per_region, per_project=per_project)


def parse_quota_entries(
    raw: Any,
    *,
    default_region: str | None,
    require_region: bool,
    context: str,
) -> list[QuotaEntry]:
    if raw in (None, {}):
        return []
    if isinstance(raw, dict):
        raw_list = [raw]
    elif isinstance(raw, list):
        raw_list = raw
    else:
        raise ConfigError(f"{context} quotas must be a list or an object")

    quotas: list[QuotaEntry] = []
    for item in raw_list:
        if not isinstance(item, dict):
            raise ConfigError(f"{context} quota entry must be an object")
        name = item.get("quota")
        if not name or not isinstance(name, str):
            raise ConfigError(f"{context} quota entry requires a string 'quota'")
        raw_limit = item.get("limit")
        if raw_limit is None:
            raise ConfigError(f"{context} quota '{name}' requires a limit")
        limit = parse_limit_value(raw_limit)
        region = item.get("region") or default_region
        if not region or not isinstance(region, str):
            raise ConfigError(
                f"{context} quota '{name}' requires a region (set 'region' or use region key)"
            )
        if default_region and item.get("region") and item["region"] != default_region:
            raise ConfigError(
                f"{context} quota '{name}' region must match '{default_region}'"
            )
        if require_region and not item.get("region"):
            raise ConfigError(f"{context} quota '{name}' requires a region")
        quotas.append(QuotaEntry(name=name, limit=limit, region=region))
    return quotas


def merge_quota_specs(base: QuotaSpec, override: QuotaSpec) -> QuotaSpec:
    per_region: dict[str, list[QuotaEntry]] = {}
    for region_id in set(base.per_region) | set(override.per_region):
        per_region[region_id] = _merge_quota_lists(
            base.per_region.get(region_id, []),
            override.per_region.get(region_id, []),
        )

    per_project: dict[str, list[QuotaEntry]] = {}
    for project_name in set(base.per_project) | set(override.per_project):
        per_project[project_name] = _merge_quota_lists(
            base.per_project.get(project_name, []),
            override.per_project.get(project_name, []),
        )

    return QuotaSpec(per_region=per_region, per_project=per_project)


def _merge_quota_lists(base: list[QuotaEntry], override: list[QuotaEntry]) -> list[QuotaEntry]:
    if not base and not override:
        return []
    override_map = {(entry.name, entry.region): entry for entry in override}
    base_keys = {(entry.name, entry.region) for entry in base}
    merged: list[QuotaEntry] = []
    for entry in base:
        key = (entry.name, entry.region)
        merged.append(override_map.get(key, entry))
    for entry in override:
        key = (entry.name, entry.region)
        if key not in base_keys:
            merged.append(entry)
    return merged


def parse_limit_value(value: Any) -> int:
    if isinstance(value, bool):
        raise ConfigError("Quota limit must be a number, not a boolean")
    if isinstance(value, int):
        return _validate_non_negative(value)
    if isinstance(value, float):
        return _validate_non_negative(_decimal_to_int(Decimal(str(value))))
    if isinstance(value, str):
        return _validate_non_negative(_parse_limit_string(value))
    raise ConfigError(f"Unsupported quota limit value: {value}")


def _parse_limit_string(raw: str) -> int:
    cleaned = raw.strip()
    if not cleaned:
        raise ConfigError("Quota limit cannot be empty")
    match = re.match(r"^([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)?$", cleaned)
    if not match:
        raise ConfigError(f"Invalid quota limit format: '{raw}'")
    number_str, unit = match.groups()
    try:
        number = Decimal(number_str)
    except InvalidOperation as exc:
        raise ConfigError(f"Invalid quota limit number: '{raw}'") from exc
    if not unit:
        return _decimal_to_int(number)
    unit_key = unit.upper()
    if unit_key not in _UNIT_MULTIPLIERS:
        raise ConfigError(f"Unsupported quota unit '{unit}'. Use KiB/MiB/GiB/TiB/PiB or plain numbers.")
    return _decimal_to_int(number * _UNIT_MULTIPLIERS[unit_key])


def _decimal_to_int(value: Decimal) -> int:
    rounded = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(rounded)


def _validate_non_negative(value: int) -> int:
    if value < 0:
        raise ConfigError("Quota limit must be non-negative")
    return value
