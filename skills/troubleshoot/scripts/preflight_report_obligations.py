#!/usr/bin/env python3
"""Check report-obligation schemas before activating updated hooks."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import stat
from typing import Any


FILE_NAME = "troubleshoot-report-obligation.json"
MAX_BYTES = 2048
V1_SCHEMA = "codex/troubleshoot-report-obligation-v1"
V2_SCHEMA = "codex/troubleshoot-report-obligation-v2"
V3_SCHEMA = "codex/troubleshoot-report-obligation-v3"
SCHEMA_STATUSES = {
    "v1": {"active", "delivered", "fallback"},
    "v2": {"active", "delivered", "advisory_incomplete", "fallback"},
    "v3": {
        "active",
        "delivered",
        "advisory_incomplete",
        "sensitive_detected",
        "fallback",
    },
}


class PreflightError(ValueError):
    """A sidecar could not be classified safely."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PreflightError("duplicate JSON key")
        result[key] = value
    return result


def _read_sidecar(path: Path) -> tuple[str, str]:
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink():
        raise PreflightError("sidecar is not a regular file")
    if file_stat.st_size > MAX_BYTES:
        raise PreflightError("sidecar exceeds the size limit")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        raw = os.read(descriptor, MAX_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_BYTES:
        raise PreflightError("sidecar exceeds the size limit")
    try:
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise PreflightError("sidecar is not valid JSON") from exc
    if not isinstance(data, dict):
        raise PreflightError("sidecar is not an object")
    schema = data.get("schema")
    status = data.get("status")
    if schema == V1_SCHEMA:
        family = "v1"
    elif schema == V2_SCHEMA:
        family = "v2"
    elif schema == V3_SCHEMA:
        family = "v3"
    else:
        family = "other"
    supported = SCHEMA_STATUSES.get(family, set())
    return family, status if status in supported else "other"


def scan(state_root: Path) -> tuple[dict[str, object], int]:
    counts: Counter[tuple[str, str]] = Counter()
    invalid = 0
    if not state_root.exists():
        return {"invalid": 0, "other": {}, "v1": {}, "v2": {}, "v3": {}}, 0
    if state_root.is_symlink() or not state_root.is_dir():
        return {
            "invalid": 1,
            "other": {},
            "v1": {},
            "v2": {},
            "v3": {},
        }, 3

    for directory, dirnames, filenames in os.walk(state_root, followlinks=False):
        base = Path(directory)
        safe_dirnames: list[str] = []
        for name in dirnames:
            candidate = base / name
            if not candidate.is_symlink():
                safe_dirnames.append(name)
        dirnames[:] = safe_dirnames
        if FILE_NAME not in filenames:
            continue
        try:
            family, status_value = _read_sidecar(base / FILE_NAME)
            counts[(family, status_value)] += 1
            if family == "other" or status_value == "other":
                invalid += 1
        except (OSError, PreflightError):
            invalid += 1

    summary: dict[str, object] = {"invalid": invalid}
    all_statuses = set().union(*SCHEMA_STATUSES.values()) | {"other"}
    for schema in ("other", "v1", "v2", "v3"):
        summary[schema] = {
            status: counts[(schema, status)]
            for status in sorted(all_statuses)
            if counts[(schema, status)]
        }
    active_legacy = counts[("v1", "active")] + counts[("v2", "active")]
    if invalid:
        return summary, 3
    if active_legacy:
        return summary, 2
    return summary, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Print schema/status counts only and fail if active legacy or invalid "
            "troubleshoot report obligations exist."
        )
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    codex_root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    summary, result = scan(args.state_root or codex_root / "task-state")
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
