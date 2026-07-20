#!/usr/bin/env python3
"""Compare JSON evidence snapshots without flattening structural distinctions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from evidence_redaction import is_sensitive_name, redact_text, redact_value

SCHEMA_VERSION = 1
DEFAULT_IGNORES = ("/collected_at",)
MAX_INPUT_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_DIFFERENCES = 1000
MAX_DIFFERENCES = 10000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare known-good and known-bad JSON evidence. Differences use "
            "JSON Pointer paths and volatile fields are ignored only explicitly."
        )
    )
    parser.add_argument("good", type=Path, help="Known-good JSON snapshot.")
    parser.add_argument("bad", type=Path, help="Known-bad JSON snapshot.")
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        metavar="JSON_POINTER",
        help="Ignore an exact JSON Pointer and descendants; repeat as needed.",
    )
    parser.add_argument(
        "--out", type=Path, help="Write comparison JSON atomically instead of stdout."
    )
    parser.add_argument(
        "--max-differences",
        type=int,
        default=DEFAULT_MAX_DIFFERENCES,
        help=(
            "Maximum differences retained in output "
            f"(default: {DEFAULT_MAX_DIFFERENCES}, max: {MAX_DIFFERENCES})."
        ),
    )
    return parser.parse_args()


def read_json(path: Path, label: str) -> Any:
    if path.is_symlink():
        raise ValueError(f"refusing symlink {label} input")
    if not path.is_file():
        raise ValueError(f"{label} input is not a regular file")
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_INPUT_BYTES + 1)
        if len(content) > MAX_INPUT_BYTES:
            raise ValueError(f"{label} input exceeds {MAX_INPUT_BYTES} bytes")
        return json.loads(content.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} input is not UTF-8 JSON") from error
    except json.JSONDecodeError as error:
        raise ValueError(
            f"invalid JSON in {label} input: line {error.lineno}"
        ) from error


def pointer(parts: tuple[str, ...]) -> str:
    if not parts:
        return ""
    escaped = (part.replace("~", "~0").replace("/", "~1") for part in parts)
    return "/" + "/".join(escaped)


def ignored(path: str, ignores: set[str]) -> bool:
    return any(path == item or path.startswith(f"{item}/") for item in ignores)


def sensitive_parts(parts: tuple[str, ...]) -> bool:
    return any(is_sensitive_name(part) for part in parts)


def shown(value: Any, exists: bool, hide: bool) -> Any:
    if not exists:
        return None
    return "[REDACTED]" if hide else redact_value(value)


def compare(
    good: Any,
    bad: Any,
    ignores: set[str],
    parts: tuple[str, ...] = (),
) -> Iterator[dict[str, Any]]:
    current = pointer(parts)
    if ignored(current, ignores):
        return
    hide = sensitive_parts(parts)
    if type(good) is not type(bad):
        yield {
            "path": redact_text(current),
            "status": "changed",
            "good": shown(good, True, hide),
            "bad": shown(bad, True, hide),
        }
        return
    if isinstance(good, dict):
        for key in sorted(set(good) | set(bad), key=str):
            child_parts = (*parts, str(key))
            child_path = pointer(child_parts)
            if ignored(child_path, ignores):
                continue
            in_good = key in good
            in_bad = key in bad
            child_hide = sensitive_parts(child_parts)
            if not in_good or not in_bad:
                value = good.get(key) if in_good else bad.get(key)
                yield {
                    "path": redact_text(child_path),
                    "status": "removed" if in_good else "added",
                    "good": shown(value, in_good, child_hide),
                    "bad": shown(value, in_bad, child_hide),
                }
            else:
                yield from compare(good[key], bad[key], ignores, child_parts)
        return
    if isinstance(good, list):
        for index in range(max(len(good), len(bad))):
            child_parts = (*parts, str(index))
            child_path = pointer(child_parts)
            if ignored(child_path, ignores):
                continue
            in_good = index < len(good)
            in_bad = index < len(bad)
            if not in_good or not in_bad:
                value = good[index] if in_good else bad[index]
                yield {
                    "path": redact_text(child_path),
                    "status": "removed" if in_good else "added",
                    "good": shown(value, in_good, hide),
                    "bad": shown(value, in_bad, hide),
                }
            else:
                yield from compare(good[index], bad[index], ignores, child_parts)
        return
    if good == bad:
        return
    yield {
        "path": redact_text(current),
        "status": "changed",
        "good": shown(good, True, hide),
        "bad": shown(bad, True, hide),
    }


def write_atomic(path: Path, content: str) -> None:
    if path.is_symlink():
        raise ValueError("refusing symlink output path")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("output parent is not a directory")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=parent,
            prefix=".comparison.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main() -> int:
    args = parse_args()
    try:
        good = read_json(args.good, "good")
        bad = read_json(args.bad, "bad")
        ignores = set(DEFAULT_IGNORES) | set(args.ignore)
        if any(item and not item.startswith("/") for item in ignores):
            raise ValueError("ignore values must be JSON Pointers beginning with /")
        if not 1 <= args.max_differences <= MAX_DIFFERENCES:
            raise ValueError(f"max differences must be between 1 and {MAX_DIFFERENCES}")
        differences: list[dict[str, Any]] = []
        difference_count = 0
        for difference in compare(good, bad, ignores):
            difference_count += 1
            if len(differences) < args.max_differences:
                differences.append(difference)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "good": "good",
            "bad": "bad",
            "ignored_fields": [redact_text(item) for item in sorted(ignores)],
            "difference_count": difference_count,
            "reported_difference_count": len(differences),
            "differences_truncated": difference_count > len(differences),
            "differences": differences,
        }
        content = (
            json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
        )
        if args.out is None:
            sys.stdout.write(content)
        else:
            write_atomic(args.out, content)
    except (RecursionError, ValueError) as error:
        print(f"compare_evidence.py: {error}", file=sys.stderr)
        return 2
    except OSError:
        print("compare_evidence.py: input or output I/O failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
