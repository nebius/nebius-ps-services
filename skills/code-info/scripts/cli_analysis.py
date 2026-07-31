"""Static, read-only CLI hierarchy and package-script analysis."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

from scan_common import EXCLUDED_CODE_DIRS, is_test_file, iter_files, package_markers


@dataclass(frozen=True)
class CommandNode:
    command_path: tuple[str, ...]
    framework: str
    source_path: Path
    line: int
    confidence: str = "resolved"


def ast_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = ast_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def call_string(call: ast.Call, position: int = 0, keyword: str = "name") -> str | None:
    if len(call.args) > position and isinstance(call.args[position], ast.Constant):
        value = call.args[position].value
        if isinstance(value, str):
            return value
    for item in call.keywords:
        if item.arg == keyword and isinstance(item.value, ast.Constant):
            value = item.value.value
            if isinstance(value, str):
                return value
    return None


def call_has_dynamic_string(
    call: ast.Call, position: int = 0, keyword: str = "name"
) -> bool:
    if len(call.args) > position:
        return not (
            isinstance(call.args[position], ast.Constant)
            and isinstance(call.args[position].value, str)
        )
    return any(
        item.arg == keyword
        and not (
            isinstance(item.value, ast.Constant) and isinstance(item.value.value, str)
        )
        for item in call.keywords
    )


def normalized_command_name(value: str) -> str:
    return value.strip().split()[0].replace("_", "-") if value.strip() else ""


def python_cli_commands(path: Path) -> list[CommandNode]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return []

    prefix_parent: dict[str, tuple[str, str, str]] = {}
    group_variables: set[str] = set()
    decorator_commands: list[tuple[str, str, int, bool, str]] = []
    argparse_subparser_owner: dict[str, str] = {}
    argparse_parser_prefix: dict[str, tuple[str, ...]] = {}
    argparse_parser_confidence: dict[str, str] = {}
    argparse_nodes: list[CommandNode] = []

    nodes = sorted(ast.walk(tree), key=lambda item: getattr(item, "lineno", 0))
    for node in nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                call = decorator if isinstance(decorator, ast.Call) else None
                dotted = ast_name(call.func if call else decorator)
                if not dotted or not dotted.endswith((".command", ".group")):
                    continue
                parent = dotted.rsplit(".", 1)[0]
                dynamic = bool(call and call_has_dynamic_string(call, keyword="name"))
                name = (
                    f"<dynamic:{node.name.replace('_', '-')}>"
                    if dynamic
                    else normalized_command_name(
                        (call_string(call, keyword="name") if call else "")
                        or node.name.replace("_", "-")
                    )
                )
                confidence = "partial" if dynamic else "resolved"
                is_group = dotted.endswith(".group")
                decorator_commands.append(
                    (parent, name, node.lineno, is_group, confidence)
                )
                if is_group:
                    prefix_parent[node.name] = (parent, name, confidence)
                    group_variables.add(node.name)

        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            dotted = ast_name(call.func)
            if dotted and dotted.endswith(".add_typer") and call.args:
                parent = dotted.rsplit(".", 1)[0]
                child = ast_name(call.args[0])
                raw_name = call_string(call, keyword="name")
                dynamic = call_has_dynamic_string(call, keyword="name") or not raw_name
                if child:
                    name = (
                        normalized_command_name(raw_name)
                        if raw_name
                        else f"<dynamic:{child.replace('_', '-')}>"
                    )
                    prefix_parent[child] = (
                        parent,
                        name,
                        "partial" if dynamic else "resolved",
                    )

        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        target = node.targets[0] if isinstance(node, ast.Assign) else node.target
        target_name = ast_name(target)
        if not target_name or not isinstance(value, ast.Call):
            continue
        called = ast_name(value.func)
        if called and called.endswith("ArgumentParser"):
            argparse_parser_prefix[target_name] = ()
            argparse_parser_confidence[target_name] = "resolved"
        elif called and called.endswith(".add_subparsers"):
            argparse_subparser_owner[target_name] = called.rsplit(".", 1)[0]

    def resolve_prefix(
        variable: str, seen: set[str] | None = None
    ) -> tuple[tuple[str, ...], str]:
        if variable not in prefix_parent:
            return (), "resolved"
        seen = seen or set()
        if variable in seen:
            return (), "partial"
        seen.add(variable)
        parent, name, confidence = prefix_parent[variable]
        parent_path, parent_confidence = resolve_prefix(parent, seen)
        combined_confidence = (
            "partial" if "partial" in {confidence, parent_confidence} else "resolved"
        )
        return (*parent_path, name), combined_confidence

    commands: list[CommandNode] = []
    for parent, name, line, _, confidence in decorator_commands:
        prefix, prefix_confidence = resolve_prefix(parent)
        command_path = (*prefix, name)
        if command_path:
            commands.append(
                CommandNode(
                    command_path,
                    "Python Click/Typer",
                    path,
                    line,
                    "partial"
                    if "partial" in {confidence, prefix_confidence}
                    else "resolved",
                )
            )
    for child, (parent, name, confidence) in prefix_parent.items():
        if child in group_variables:
            continue
        prefix, prefix_confidence = resolve_prefix(parent)
        command_path = (*prefix, name)
        if command_path:
            commands.append(
                CommandNode(
                    command_path,
                    "Python Click/Typer",
                    path,
                    0,
                    "partial"
                    if "partial" in {confidence, prefix_confidence}
                    else "resolved",
                )
            )

    # Resolve argparse parsers iteratively because nested parser assignments can
    # appear after their parent subparser registration.
    assignments = [
        node
        for node in nodes
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and isinstance(node.value, ast.Call)
    ]
    for _ in range(4):
        changed = False
        for node in assignments:
            value = node.value
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            target_name = ast_name(target)
            called = ast_name(value.func)
            if not target_name or not called or not called.endswith(".add_parser"):
                continue
            subparsers = called.rsplit(".", 1)[0]
            owner = argparse_subparser_owner.get(subparsers)
            if owner is None or owner not in argparse_parser_prefix:
                continue
            raw_name = call_string(value)
            dynamic = call_has_dynamic_string(value)
            name = (
                f"<dynamic:{target_name.replace('_', '-')}>"
                if dynamic
                else normalized_command_name(raw_name or "")
            )
            if not name:
                continue
            command_path = (*argparse_parser_prefix[owner], name)
            if argparse_parser_prefix.get(target_name) != command_path:
                argparse_parser_prefix[target_name] = command_path
                confidence = (
                    "partial"
                    if dynamic or argparse_parser_confidence.get(owner) == "partial"
                    else "resolved"
                )
                argparse_parser_confidence[target_name] = confidence
                argparse_nodes.append(
                    CommandNode(
                        command_path,
                        "Python argparse",
                        path,
                        node.lineno,
                        confidence,
                    )
                )
                changed = True
        if not changed:
            break
    for node in nodes:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        called = ast_name(node.value.func)
        if not called or not called.endswith(".add_parser"):
            continue
        subparsers = called.rsplit(".", 1)[0]
        owner = argparse_subparser_owner.get(subparsers)
        if owner is None or owner not in argparse_parser_prefix:
            continue
        raw_name = call_string(node.value)
        dynamic = call_has_dynamic_string(node.value)
        name = (
            "<dynamic:command>" if dynamic else normalized_command_name(raw_name or "")
        )
        if name:
            confidence = (
                "partial"
                if dynamic or argparse_parser_confidence.get(owner) == "partial"
                else "resolved"
            )
            argparse_nodes.append(
                CommandNode(
                    (*argparse_parser_prefix[owner], name),
                    "Python argparse",
                    path,
                    node.lineno,
                    confidence,
                )
            )
    return [*commands, *argparse_nodes]


def non_python_cli_commands(path: Path) -> list[CommandNode]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    suffix = path.suffix.lower()
    patterns: list[tuple[str, re.Pattern[str]]] = []
    if suffix in {".js", ".jsx", ".mjs", ".ts", ".tsx"}:
        patterns.append(
            (
                "JavaScript/TypeScript Commander",
                re.compile(r"\.command\(\s*['\"`]([^'\"`]+)"),
            )
        )
    elif suffix == ".go":
        patterns.append(("Go Cobra", re.compile(r"\bUse:\s*['\"`]([^'\"`]+)")))
    elif suffix == ".rs":
        patterns.append(
            ("Rust clap", re.compile(r"#\[command\([^]]*name\s*=\s*['\"]([^'\"]+)"))
        )
    found: list[CommandNode] = []
    for line_number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith(("//", "#")) and not stripped.startswith("#["):
            continue
        for framework, pattern in patterns:
            if match := pattern.search(stripped):
                name = normalized_command_name(match.group(1))
                if name:
                    found.append(
                        CommandNode((name,), framework, path, line_number, "partial")
                    )
    return found


def detect_cli_commands(root: Path) -> list[CommandNode]:
    commands: list[CommandNode] = []
    for path in iter_files(root, EXCLUDED_CODE_DIRS):
        if is_test_file(path, root):
            continue
        if path.suffix.lower() == ".py":
            commands.extend(python_cli_commands(path))
        else:
            commands.extend(non_python_cli_commands(path))
    unique: dict[tuple[tuple[str, ...], str, Path], CommandNode] = {}
    for command in commands:
        if not command.command_path:
            continue
        capped = command.command_path[:3]
        normalized = CommandNode(
            capped,
            command.framework,
            command.source_path,
            command.line,
            "depth>3 omitted" if len(command.command_path) > 3 else command.confidence,
        )
        unique[(capped, command.framework, command.source_path)] = normalized
    return sorted(
        unique.values(),
        key=lambda item: (len(item.command_path), item.command_path, item.framework),
    )


def package_scripts(root: Path) -> list[tuple[Path, str]]:
    scripts_found: list[tuple[Path, str]] = []
    for marker in package_markers(root):
        if marker.name != "package.json":
            continue
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        scripts = data.get("scripts")
        if not isinstance(scripts, dict):
            continue
        for name in sorted(scripts):
            scripts_found.append((marker, name))
    return scripts_found
