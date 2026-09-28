from __future__ import annotations

from pathlib import Path
from types import FunctionType, ModuleType

import pytest

from source_inspection import function_source


def load_module(path: Path, source: str) -> ModuleType:
    path.write_text(source, encoding="utf-8")
    module = ModuleType("source_fixture")
    exec(compile(source, str(path), "exec"), vars(module))
    return module


@pytest.mark.parametrize("prefix", ["", "async "])
@pytest.mark.parametrize("line_change", ["insert", "remove"])
def test_source_lookup_survives_line_changes_after_import(tmp_path, prefix, line_change):
    path = tmp_path / "owner.py"
    previous = "def previous():\n    # original comment\n    return 0\n\n"
    expected = f"{prefix}def target():\n    return 'expected'\n"
    module = load_module(path, previous + expected)
    # Resolve through a reexport, whose attribute name differs from the owner.
    consumer = ModuleType("consumer")
    consumer.public_alias = module.target
    assert function_source(consumer.public_alias) == expected

    if line_change == "insert":
        previous = previous.replace("    return 0", "    # inserted comment\n    return 0")
    else:
        previous = previous.replace("    # original comment\n", "")
    path.write_text(previous + expected, encoding="utf-8")

    assert function_source(consumer.public_alias) == expected


@pytest.mark.parametrize("replacement,count", [("", 0), ("def target():\n    return 2\n", 2)])
def test_source_lookup_rejects_missing_or_duplicate_definitions(tmp_path, replacement, count):
    path = tmp_path / "owner.py"
    source = "def target():\n    return 1\n"
    module = load_module(path, source)
    path.write_text(source + replacement if count else replacement, encoding="utf-8")

    with pytest.raises(AssertionError, match=f"definition of target; found {count}"):
        function_source(module.target)


def test_source_lookup_ignores_nested_names_and_reads_current_body(tmp_path):
    path = tmp_path / "owner.py"
    nested = "def previous():\n    def target():\n        return 'nested'\n    return target\n\n"
    target = "def target():\n    return 'before'\n"
    module = load_module(path, nested + target)
    assert function_source(module.target) == target
    changed = target.replace("'before'", "'after'")
    path.write_text(nested + changed, encoding="utf-8")

    assert function_source(module.target) == changed
    with pytest.raises(AssertionError, match="top-level function"):
        function_source(module.previous())


def test_source_lookup_includes_decorators(tmp_path):
    path = tmp_path / "owner.py"
    decorator = "def decorate(function):\n    return function\n\n"
    expected = "@decorate\ndef target():\n    return 'expected'\n"
    module = load_module(path, decorator + expected)

    assert isinstance(module.target, FunctionType)
    assert function_source(module.target) == expected
