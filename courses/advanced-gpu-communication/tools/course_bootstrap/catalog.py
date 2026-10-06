"""Authoritative dependency recipes and launcher bindings, shared with execution."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .state import digest, file_digest, regular


NAME = re.compile(r"[a-z0-9][a-z0-9_.-]*\Z")


def discover(script):
    script = Path(script).absolute()
    for parent in (script, *script.parents):
        if parent.is_symlink():
            raise ValueError("Course setup must not be reached through a symlink")
    root = script.parent.parent
    if (
        (root / "reference/course.json").is_file()
        and not (root.parent / "reference/course.json").exists()
        and (root.parent / "tools/course_bootstrap/catalog.json").is_file()
    ):
        root = root.parent
    if (root / "reference/course.json").is_file():
        courses = [root]
    else:
        courses = sorted(p.parent.parent for p in root.glob("*/reference/course.json"))
    if not courses:
        raise ValueError("No course metadata found beside the installed setup tools")
    result = {}
    for course in courses:
        metadata = course / "reference/course.json"
        for path in (course, metadata.parent, metadata):
            if path.is_symlink():
                raise ValueError("Course metadata must not be symlinked")
        regular(metadata)
        data = json.loads(metadata.read_text())
        if data.get("slug") != course.name or not NAME.fullmatch(course.name):
            raise ValueError("Course metadata identity does not match its directory")
        if data.get("profile") in {"text-only", "reference-only"}:
            continue
        result[course.name] = course
    return root, result


def load_catalog():
    path = Path(__file__).with_name("catalog.json")
    regular(path)
    catalog = json.loads(path.read_text())
    if catalog.get("schema") != "course-runtime-catalog/v1":
        raise ValueError("Unsupported course runtime catalog")
    for section in ("components", "runtimes"):
        if not isinstance(catalog.get(section), dict):
            raise ValueError("Incomplete course runtime catalog")
        if any(not NAME.fullmatch(name) for name in catalog[section]):
            raise ValueError("Unsafe runtime/component identity")
    for name, spec in catalog["components"].items():
        for dependency in spec.get("depends", []):
            if dependency not in catalog["components"]:
                raise ValueError(f"Unknown dependency of {name}: {dependency}")
        if spec.get("kind") == "image" and not re.fullmatch(
            r"docker://[^\s@]+@sha256:[0-9a-f]{64}", spec.get("uri", "")
        ):
            raise ValueError(f"Image {name} requires an immutable identity")
        if spec.get("kind") == "git" and not re.fullmatch(
            r"[0-9a-f]{40}", spec.get("revision", "")
        ):
            raise ValueError(f"Source {name} requires an immutable revision")
    for name, spec in catalog["runtimes"].items():
        if not spec.get("components"):
            raise ValueError(f"Runtime {name} has no dependencies")
        for component in spec["components"]:
            if component not in catalog["components"]:
                raise ValueError(f"Unknown component of {name}: {component}")
    order(catalog, catalog["components"])
    return catalog


def order(catalog, selected):
    result, active, done = [], set(), set()

    def visit(name):
        if name in active:
            raise ValueError(f"Dependency cycle at {name}")
        if name in done:
            return
        active.add(name)
        for dependency in catalog["components"][name].get("depends", []):
            visit(dependency)
        active.remove(name)
        done.add(name)
        result.append(name)

    for name in selected:
        visit(name)
    return result


def bindings(course, catalog):
    path = course / "reference/runtime.json"
    regular(path)
    value = json.loads(path.read_text())
    if (
        value.get("schema") != "course-runtime-bindings/v1"
        or value.get("course") != course.name
    ):
        raise ValueError(f"Invalid runtime bindings for {course.name}")
    declared = value["launchers"]
    actual = {p.name for p in (course / "slurm").glob("*.sbatch")}
    if set(declared) != actual:
        raise ValueError(
            f"Every native launcher must have one runtime binding: {course.name}"
        )
    labs = {
        Path(row["path"]).stem
        for row in json.loads((course / "reference/course.json").read_text())["labs"]
    }
    if set(value["labs"]) != labs:
        raise ValueError(f"Every lab must have one runtime binding: {course.name}")
    for runtime in [*declared.values(), *value["labs"].values()]:
        if runtime not in catalog["runtimes"]:
            raise ValueError(f"Unknown runtime binding: {runtime}")
    return value


def source_path(root, courses, relative):
    value = Path(relative)
    if value.is_absolute() or ".." in value.parts:
        raise ValueError("Unsafe catalog source path")
    if value.parts[0] in courses:
        path = courses[value.parts[0]].joinpath(*value.parts[1:])
    else:
        path = root / value
    if not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError(f"Missing or unsafe catalog source: {relative}")
    regular(path)
    return path


def implementation(spec):
    """Hash executable recipe inputs, excluding unrelated branches and CLI/docs."""
    import ast

    folder = Path(__file__).parent
    tree = ast.parse((folder / "installer.py").read_text())
    common = {
        "__init__",
        "environment",
        "run",
        "values",
        "expand",
        "install",
        "check_installed",
    }
    common.add(spec["kind"] if spec["kind"] != "models" else "models")
    if spec["kind"] == "image":
        common.add("record_image")
    if spec.get("recipe") == "toolchain":
        common.add("archive")
    parts = [
        ast.dump(node, include_attributes=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name in common
    ]
    parts.append(file_digest(folder / "state.py"))
    if spec["kind"] == "models":
        parts.append(file_digest(folder / "models.py"))
    if spec["kind"] == "recipe":
        tree = ast.parse((folder / "recipes.py").read_text())
        function = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "install"
        )
        # All branches share only the preamble; hash the selected branch body.
        parts.extend(
            ast.dump(n, include_attributes=False)
            for n in function.body
            if not isinstance(n, ast.If)
        )
        branches = [
            n
            for n in ast.walk(function)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Compare)
            and ast.dump(n.test.left) == "Name(id='recipe', ctx=Load())"
        ]
        branch = next(
            (
                n
                for n in branches
                if isinstance(n.test.comparators[0], ast.Constant)
                and n.test.comparators[0].value == spec["recipe"]
            ),
            None,
        )
        if branch is None:
            raise ValueError(f"No recipe implementation for {spec['recipe']}")
        parts.extend(ast.dump(n, include_attributes=False) for n in branch.body)
    return digest(parts)


def component_inputs(catalog, root, courses, name):
    spec = catalog["components"][name]
    inputs = {"spec": spec, "root": str(root), "implementation": implementation(spec)}
    for key in ("requirements", "source"):
        if key in spec:
            inputs[key] = file_digest(source_path(root, courses, spec[key]))
    if spec.get("definition"):
        inputs["definition"] = file_digest(Path(__file__).parent / spec["definition"])
    if spec.get("recipe") == "fabric":
        inputs["helper"] = file_digest(
            next(iter(courses.values())) / "tools/install_fabric_tools.py"
        )
    if spec.get("source_tree"):
        source = courses[spec["source_tree"]]
        inputs["files"] = {
            relative: file_digest(source / relative)
            for relative in spec["source_files"]
        }
    return inputs


def fingerprint(catalog, root, courses, runtime):
    spec = catalog["runtimes"][runtime]
    return digest(
        {
            "runtime": spec,
            "components": {
                name: component_inputs(catalog, root, courses, name)
                for name in order(catalog, spec["components"])
            },
        }
    )
