"""Package approval projections, independent of installed product imports.

The caller supplies its requirement parser. Full metadata is discovery input,
never part of an approval unless explicitly selected by the retained manifest.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any


class DependencyFailure(RuntimeError):
    """Closed dependency predicates usable by standalone guest and controller code."""

    CODES = frozenset({
        "dependency-resolver-unavailable",
        "dependency-resolution-failed",
        "dependency-platform-unsupported",
        "dependency-direct-url",
        "dependency-metadata-conflict",
        "dependency-shared-conflict",
        "dependency-wheel-unavailable",
        "dependency-wheel-incompatible",
        "dependency-wheel-integrity",
        "dependency-wheel-origin",
        "dependency-identity-invalid",
        "dependency-manifest-invalid",
    })

    def __init__(self, code: str, detail: str):
        if code not in self.CODES:
            raise ValueError("Unknown dependency predicate")
        self.code = code
        super().__init__(detail)


def name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def inventory(distributions: Iterable[Any], markers: dict) -> dict:
    versions: dict[str, str] = {}
    requirements: dict[str, list[str]] = {}
    candidates: dict[str, list[dict]] = {}
    for item in distributions:
        key = name(item.metadata["Name"] or "")
        if not key:
            continue
        dependencies = list(item.requires or [])
        candidates.setdefault(key, []).append(
            {
                "prefix": str(Path(str(item.locate_file(""))).resolve()),
                "version": item.version,
                "requirements": dependencies,
            }
        )
        if key not in versions:
            versions[key] = item.version
            requirements[key] = dependencies
    return {
        "dependencies": versions,
        "requirements": requirements,
        "candidates": candidates,
        "markers": markers,
    }


def closure(
    roots: Iterable[str], requirements: dict, markers: dict, *, parse_requirement: Callable
) -> set[str]:
    pending = [(root, ("",)) for root in roots]
    seen = set()
    selected = set()
    while pending:
        text, extras = pending.pop()
        if (text, extras) in seen:
            continue
        seen.add((text, extras))
        requirement = parse_requirement(text)
        if requirement.marker and not any(
            requirement.marker.evaluate({**markers, "extra": extra}) for extra in extras
        ):
            continue
        if requirement.url:
            raise DependencyFailure(
                "dependency-direct-url", "Direct URL dependencies require a separately built wheel set"
            )
        key = name(requirement.name)
        selected.add(key)
        child_extras = tuple(sorted({"", *requirement.extras}))
        pending.extend((child, child_extras) for child in requirements.get(key, []))
    return selected


def names(manifest: dict, observed: dict, *, parse_requirement: Callable) -> set[str]:
    if "expected_dependencies" in manifest:
        # The exact map also preserves the wider scope of an already approved
        # predecessor. Never silently rewrite old package authority.
        return set(manifest["expected_dependencies"])
    current = observed["requirements"]
    desired = {**current, "nebius-vpngw": manifest.get("requirements", [])}
    return closure(
        ("nebius-vpngw", "pip"), current, observed["markers"], parse_requirement=parse_requirement
    ) | closure(
        ("nebius-vpngw", "pip"), desired, observed["markers"], parse_requirement=parse_requirement
    )


def project(observed: dict, selected: set[str]) -> dict:
    for key in selected:
        prefixes: dict[str, str] = {}
        for item in observed["candidates"].get(key, []):
            if prefixes.setdefault(item["prefix"], item["version"]) != item["version"]:
                raise DependencyFailure(
                    "dependency-metadata-conflict",
                    "conflicting installed package metadata in VPNGW dependency closure",
                )
    return {
        field: {key: value for key, value in observed[field].items() if key in selected}
        for field in ("dependencies", "requirements")
    }


def product_metadata_changed(manifest: dict, observed: dict) -> bool:
    return "expected_requirements" in manifest and observed.get("requirements", {}).get(
        "nebius-vpngw"
    ) != manifest["expected_requirements"].get("nebius-vpngw")


def check_shared_conflicts(
    observed: dict, desired: dict[str, str], *, parse_requirement: Callable
) -> None:
    changed = {
        key for key, version in desired.items() if observed["dependencies"].get(key) != version
    }
    if not changed:
        return
    for key, selected in observed["requirements"].items():
        if key in changed or key == "nebius-vpngw":
            continue
        candidates = observed.get("candidates", {}).get(key, [{"requirements": selected}])
        for candidate in candidates:
            for text in candidate["requirements"]:
                # Unrelated damaged metadata cannot veto an independent package
                # update. Parse every possible reference to a changed package.
                match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", text)
                if match is None or name(match.group(1)) not in changed:
                    continue
                requirement = parse_requirement(text)
                if requirement.marker and not requirement.marker.evaluate(
                    {**observed["markers"], "extra": ""}
                ):
                    continue
                if requirement.url or desired[name(requirement.name)] not in requirement.specifier:
                    raise DependencyFailure(
                        "dependency-shared-conflict",
                        "Required dependency update conflicts with installed software",
                    )
