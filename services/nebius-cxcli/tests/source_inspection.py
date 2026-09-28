"""Read exact top-level definitions for source-contract tests.

Runtime line numbers can become stale when an editable checkout changes after
import. These checks inspect the current file, independently of those offsets.
"""

from __future__ import annotations

import ast
import inspect
from functools import lru_cache
from pathlib import Path
from types import FunctionType


@lru_cache(maxsize=8)
def _function_sources(source: str) -> dict[str, list[str]]:
    lines = source.splitlines(keepends=True)
    functions: dict[str, list[str]] = {}
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min([node.lineno, *(item.lineno for item in node.decorator_list)])
            functions.setdefault(node.name, []).append("".join(lines[start - 1 : node.end_lineno]))
    return functions


def function_source(function: FunctionType) -> str:
    """Resolve one top-level function in its defining file, including reexports."""
    function = inspect.unwrap(function)
    assert function.__qualname__ == function.__name__, "Expected a top-level function"
    filename = inspect.getsourcefile(function)
    assert filename is not None, f"No source file for {function.__name__}"
    source = Path(filename).read_text(encoding="utf-8")
    matches = _function_sources(source).get(function.__name__, [])
    assert len(matches) == 1, (
        f"Expected exactly one top-level definition of {function.__name__}; found {len(matches)}"
    )
    return matches[0]
