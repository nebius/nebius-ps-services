#!/usr/bin/env python3
"""Prevent new service/domain definitions from accumulating in cli.py."""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from pathlib import Path
from typing import Any

from ratchet_baseline import merge_base_payload

SCHEMA = "nebius-cxcli.cli-architecture-ratchet.v1"


def _is_command_callback(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr in {"callback", "command"}:
            return True
    return False


def _definition_tokens(source: str) -> tuple[str, ...]:
    tree = ast.parse(source)
    tokens: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_command_callback(node):
                continue
            kind = "async-function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            tokens.append(f"{kind}:{node.name}")
        elif isinstance(node, ast.ClassDef):
            tokens.append(f"class:{node.name}")
    return tuple(sorted(tokens))


def _load_baseline(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise RuntimeError(f"{path} must use schema {SCHEMA}")
    definitions = payload.get("allowed_top_level_definitions")
    if not isinstance(definitions, list) or not all(isinstance(item, str) for item in definitions):
        raise RuntimeError("CLI architecture baseline has an invalid definition allowlist")
    return payload


def _assert_subset(current: list[str] | tuple[str, ...], allowed: list[str], *, label: str) -> None:
    excess = Counter(current) - Counter(allowed)
    if excess:
        rendered = ", ".join(
            token if count == 1 else f"{token} ({count})" for token, count in sorted(excess.items())
        )
        raise RuntimeError(f"{label} adds top-level CLI implementation definitions: {rendered}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "measure"))
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--base-ref")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.source.resolve(strict=True)
    definitions = _definition_tokens(source.read_text(encoding="utf-8"))
    if args.action == "measure":
        if args.output is None:
            raise RuntimeError("measure requires --output")
        payload = {
            "allowed_top_level_definitions": definitions,
            "schema": SCHEMA,
        }
        args.output.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return 0
    if args.baseline is None:
        raise RuntimeError("check requires --baseline")
    baseline_path = args.baseline.resolve(strict=True)
    baseline = _load_baseline(baseline_path)
    allowed = baseline["allowed_top_level_definitions"]
    _assert_subset(definitions, allowed, label="cli.py")
    previous = merge_base_payload(args.project_root.resolve(), baseline_path, args.base_ref)
    if previous is not None:
        if previous.get("schema") != SCHEMA:
            raise RuntimeError(f"merge-base CLI architecture baseline must use schema {SCHEMA}")
        previous_allowed = previous.get("allowed_top_level_definitions")
        if not isinstance(previous_allowed, list) or not all(
            isinstance(item, str) for item in previous_allowed
        ):
            raise RuntimeError("merge-base CLI architecture baseline has an invalid allowlist")
        _assert_subset(allowed, previous_allowed, label="CLI architecture baseline")
    print(
        f"CLI architecture ratchet passed: {len(definitions)} current implementation "
        f"definition(s), {len(allowed)} allowed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
