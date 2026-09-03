#!/usr/bin/env python3
"""Enforce non-increasing Ruff-format and mypy debt while it is retired."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ratchet_baseline import merge_base_payload

SCHEMA = "nebius-cxcli.quality-ratchets.v1"


def _load_baseline(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise RuntimeError(f"{path} must use schema {SCHEMA}")
    return payload


def _check_baseline_direction(current: dict[str, Any], previous: dict[str, Any]) -> None:
    if previous.get("schema") != SCHEMA:
        raise RuntimeError(f"merge-base quality baseline must use schema {SCHEMA}")
    current_maximum = current.get("mypy_max_errors")
    previous_maximum = previous.get("mypy_max_errors")
    if (
        isinstance(current_maximum, bool)
        or not isinstance(current_maximum, int)
        or isinstance(previous_maximum, bool)
        or not isinstance(previous_maximum, int)
    ):
        raise RuntimeError("quality baselines must define integer mypy_max_errors")
    if current_maximum > previous_maximum:
        raise RuntimeError(
            "quality baseline regression: mypy_max_errors may only decrease "
            f"({previous_maximum} -> {current_maximum})"
        )
    current_format = current.get("ruff_format_unformatted_files")
    previous_format = previous.get("ruff_format_unformatted_files")
    if not isinstance(current_format, list) or not all(
        isinstance(item, str) for item in current_format
    ):
        raise RuntimeError("quality baseline has invalid ruff_format_unformatted_files")
    if not isinstance(previous_format, list) or not all(
        isinstance(item, str) for item in previous_format
    ):
        raise RuntimeError("merge-base quality baseline has invalid format allowlist")
    added = sorted(set(current_format) - set(previous_format))
    if added:
        raise RuntimeError(
            "quality baseline regression: Ruff format allowlist may only shrink: "
            + ", ".join(added)
        )


def _python_files(project_root: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for root in (project_root / "src", project_root / "tests", project_root / "scripts")
            for path in root.rglob("*.py")
            if "_version.py" not in path.parts
        )
    )


def _ruff_format_offenders(project_root: Path) -> tuple[str, ...]:
    offenders: list[str] = []
    for path in _python_files(project_root):
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "format", "--check", str(path)],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 1:
            offenders.append(path.relative_to(project_root).as_posix())
            continue
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"Ruff format inspection failed for {path}: {detail}")
    return tuple(offenders)


def _check_format(project_root: Path, baseline: dict[str, Any]) -> int:
    allowed = baseline.get("ruff_format_unformatted_files")
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        raise RuntimeError("quality baseline has invalid ruff_format_unformatted_files")
    offenders = _ruff_format_offenders(project_root)
    newly_unformatted = sorted(set(offenders) - set(allowed))
    if newly_unformatted:
        print("Ruff format debt increased:", file=sys.stderr)
        for path in newly_unformatted:
            print(f"  {path}", file=sys.stderr)
        return 1
    print(
        f"Ruff format ratchet passed: {len(offenders)} current offender(s), "
        f"{len(allowed)} baseline offender(s)."
    )
    return 0


def _mypy_error_count(project_root: Path) -> tuple[int, str]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "src/nebius_cxcli",
            "--no-error-summary",
            "--show-error-codes",
        ],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode not in {0, 1}:
        raise RuntimeError(f"mypy inspection failed with exit {result.returncode}:\n{output}")
    return sum(": error:" in line for line in output.splitlines()), output


def _check_mypy(project_root: Path, baseline: dict[str, Any]) -> int:
    maximum = baseline.get("mypy_max_errors")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
        raise RuntimeError("quality baseline has invalid mypy_max_errors")
    errors, output = _mypy_error_count(project_root)
    if errors > maximum:
        print(output, file=sys.stderr)
        print(
            f"mypy debt increased: {errors} error(s), baseline maximum {maximum}", file=sys.stderr
        )
        return 1
    print(f"mypy ratchet passed: {errors} error(s), baseline maximum {maximum}.")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gate", choices=("baseline", "format", "mypy", "measure"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--base-ref")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    project_root = args.project_root.resolve()
    if args.gate == "measure":
        errors, _output = _mypy_error_count(project_root)
        print(
            json.dumps(
                {
                    "mypy_max_errors": errors,
                    "ruff_format_unformatted_files": _ruff_format_offenders(project_root),
                    "schema": SCHEMA,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.baseline is None:
        raise RuntimeError("--baseline is required for baseline, format, and mypy gates")
    baseline_path = args.baseline.resolve()
    baseline = _load_baseline(baseline_path)
    previous = merge_base_payload(project_root, baseline_path, args.base_ref)
    if previous is not None:
        _check_baseline_direction(baseline, previous)
    if args.gate == "baseline":
        if previous is not None:
            print("Quality baseline direction passed.")
        elif args.base_ref:
            print("Quality baseline was absent at the merge base; initial adoption accepted.")
        else:
            print("Quality baseline has no requested merge-base comparison.")
        return 0
    if args.gate == "format":
        return _check_format(project_root, baseline)
    return _check_mypy(project_root, baseline)


if __name__ == "__main__":
    raise SystemExit(main())
