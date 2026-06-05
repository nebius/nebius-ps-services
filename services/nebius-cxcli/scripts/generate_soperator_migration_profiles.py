#!/usr/bin/env python3
"""Refresh committed Soperator migration profile history from GitHub releases.

This is the bootstrap profile-history generator. It records explicit upstream
release records and maps each release to committed compatibility axes. It does
not yet download chart tarballs or fingerprint CRDs, rendered templates, image
sets, or Slurm component schemas; that deeper contract extraction belongs to
the next generator hardening phase.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml

RELEASES_URL = "https://api.github.com/repos/nebius/soperator/releases"
RELEASES_PER_PAGE = 100


def _normalize_version(tag: str) -> str:
    return str(tag or "").strip().removeprefix("v")


def _generation(version: str) -> str:
    major = int(version.split(".", 1)[0])
    if major <= 1:
        return "legacy-v1"
    return f"v{major}"


def _migration_class(generation: str) -> str:
    return "adopt-or-install" if generation == "v4" else "storage-and-layout-migration"


def _profile_id(generation: str) -> str:
    return f"{generation}-to-target"


def _fetch_releases() -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    page = 1
    while True:
        url = f"{RELEASES_URL}?per_page={RELEASES_PER_PAGE}&page={page}"
        with urllib.request.urlopen(url, timeout=30) as response:
            payload = json.load(response)
        if not isinstance(payload, list):
            raise RuntimeError("GitHub releases API returned a non-list payload")
        rows = [row for row in payload if isinstance(row, dict)]
        releases.extend(rows)
        if len(rows) < RELEASES_PER_PAGE:
            break
        page += 1
    return releases


def _profile_payload(releases: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for release in reversed(releases):
        tag = str(release.get("tag_name", "") or "").strip()
        version = _normalize_version(tag)
        if not version:
            continue
        generation = _generation(version)
        rows.append(
            {
                "version": version,
                "upstream_tag": tag,
                "published_at": str(release.get("published_at", "") or "")[:10],
                "generation": generation,
                "profile_id": _profile_id(generation),
                "migration_class": _migration_class(generation),
            }
        )
    return {
        "schema": "nebius-cxcli-soperator-migration-profiles/v1",
        "source": "https://github.com/nebius/soperator/releases",
        "generated_from": "github-releases-api",
        "target_policy": "component_sources.yaml pinned soperator chart version",
        "generator_scope": "release-metadata-and-compatibility-axes",
        "future_generator_scope": (
            "chart-tarball-crd-template-image-and-slurm-contract-fingerprints"
        ),
        "profile_groups": {
            "legacy-v1-to-target": {
                "title": "Legacy v1 Soperator to pinned target",
                "requires_aligned_sfs": True,
                "migration_class": "storage-and-layout-migration",
                "compatibility_axes": {
                    "compute_layout": "replace-and-roll",
                    "storage_layout": "create-aligned-sfs-and-migrate",
                    "slurm_components": [
                        "SlurmCluster",
                        "NodeSet",
                        "NodeConfigurator",
                        "SConfigController",
                        "slurmrestd",
                        "MariaDB",
                        "OpenKruise",
                        "ActiveChecks",
                    ],
                },
            },
            "v2-to-target": {
                "title": "Soperator 2.x to pinned target",
                "requires_aligned_sfs": True,
                "migration_class": "storage-and-layout-migration",
                "compatibility_axes": {
                    "compute_layout": "replace-and-roll",
                    "storage_layout": "create-aligned-sfs-and-migrate",
                    "slurm_components": [
                        "SlurmCluster",
                        "NodeSet",
                        "NodeConfigurator",
                        "SConfigController",
                        "slurmrestd",
                        "MariaDB",
                        "OpenKruise",
                        "ActiveChecks",
                    ],
                },
            },
            "v3-to-target": {
                "title": "Soperator 3.x to pinned target",
                "requires_aligned_sfs": True,
                "migration_class": "storage-and-layout-migration",
                "compatibility_axes": {
                    "compute_layout": "replace-and-roll",
                    "storage_layout": "create-aligned-sfs-and-migrate",
                    "slurm_components": [
                        "SlurmCluster",
                        "NodeSet",
                        "NodeConfigurator",
                        "SConfigController",
                        "slurmrestd",
                        "MariaDB",
                        "OpenKruise",
                        "ActiveChecks",
                    ],
                },
            },
            "v4-to-target": {
                "title": "Soperator 4.x to pinned target",
                "requires_aligned_sfs": False,
                "migration_class": "adopt-or-install",
                "compatibility_axes": {
                    "compute_layout": "adopt-or-reconcile",
                    "storage_layout": "adopt-existing-or-create-if-missing",
                    "slurm_components": [
                        "SlurmCluster",
                        "NodeSet",
                        "NodeConfigurator",
                        "NodeSetPowerState",
                        "SConfigController",
                        "slurmrestd",
                        "MariaDB",
                        "OpenKruise",
                        "ActiveChecks",
                        "JailedConfig",
                    ],
                },
            },
        },
        "releases": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/nebius_cxcli/soperator_migration_profiles.yaml"),
    )
    args = parser.parse_args()
    payload = _profile_payload(_fetch_releases())
    args.output.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(payload['releases'])} Soperator migration profile records to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
