from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "nebius_cxcli"


def _imports_cli(module_path: Path) -> bool:
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 1 and node.module == "cli":
                return True
            if node.module == "nebius_cxcli.cli":
                return True
        if isinstance(node, ast.Import) and any(
            alias.name == "nebius_cxcli.cli" for alias in node.names
        ):
            return True
    return False


def test_only_package_entrypoint_imports_typer_composition_root() -> None:
    offenders = [
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        if path.name not in {"__main__.py", "cli.py"} and _imports_cli(path)
    ]

    assert offenders == []


def test_soperator_materialization_has_one_canonical_owner() -> None:
    cli_tree = ast.parse(
        (PACKAGE_ROOT / "cli.py").read_text(encoding="utf-8"),
        filename="cli.py",
    )
    cli_definitions = {
        node.name
        for node in cli_tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }

    assert "_materialize_soperator_component_defaults" not in cli_definitions
    assert "_materialize_soperator_render_only_values" not in cli_definitions
