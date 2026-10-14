#!/usr/bin/env python3
"""Offline, closed Python candidate producer; bootstrap is a separate phase."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import keyword
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path, PurePosixPath

import tomllib

OWNER = "python-project"
PROFILE = "python-package"
ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]*\Z")
PACKAGE = re.compile(r"[a-z][a-z0-9_]*\Z")
PROFILES = {"cli", "api", "systemd"}
REQUEST_KEYS = {
    "schema_version",
    "candidate_set_id",
    "component_id",
    "materialization_unit_id",
    "component_root",
    "profile",
    "project_name",
    "package_name",
    "description",
    "project_kind",
    "python_requires",
    "profiles",
    "service_module",
    "dependencies",
    "assigned_paths",
    "excluded_paths",
}


class PythonProjectError(ValueError):
    """Invalid or unsafe producer input."""


def canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
        + b"\n"
    )


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def decode(payload: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PythonProjectError("duplicate JSON key")
            result[key] = value
        return result

    def invalid(_):
        raise PythonProjectError("non-finite JSON value")

    try:
        value = json.loads(payload, object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PythonProjectError("invalid JSON document") from error
    if not isinstance(value, dict):
        raise PythonProjectError("expected JSON object")
    return value


def path_name(value: object, *, root: bool = False) -> str:
    if root and value == ".":
        return "."
    if (
        not isinstance(value, str)
        or not value
        or value != unicodedata.normalize("NFC", value)
    ):
        raise PythonProjectError("invalid relative path")
    path = PurePosixPath(value)
    if (
        not path.parts
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {".", ".."} for part in path.parts)
        or "\\" in value
        or any(ord(c) < 32 for c in value)
    ):
        raise PythonProjectError("invalid relative path")
    if any(
        part.casefold() in {".git", ".hg", ".svn", "agents.md", "agents.override.md"}
        for part in path.parts
    ):
        raise PythonProjectError("reserved path")
    return value


def safe_path(path: Path) -> Path:
    """Reject symlinks in every existing ancestor without resolving them away."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink():
            raise PythonProjectError("symlinked path is not supported")
    return path


def read_bytes(path: Path, *, private: bool = False) -> bytes:
    path = safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (
            private and stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise PythonProjectError(
                "expected a regular private file"
                if private
                else "expected regular file"
            )
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            return stream.read()
    finally:
        os.close(descriptor)


def private_write(path: Path, payload: bytes) -> None:
    path = safe_path(path)
    missing = []
    parent = path.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise PythonProjectError("candidate directory must have mode 0700")
    if path.exists():
        if read_bytes(path, private=True) == payload:
            return
        raise PythonProjectError("candidate already exists with different bytes")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def repository_path(request: dict, relative: str) -> str:
    return (
        relative
        if request["component_root"] == "."
        else f"{request['component_root']}/{relative}"
    )


def expected_paths(request: dict) -> set[str]:
    package = request["package_name"]
    files = {
        "pyproject.toml",
        f"src/{package}/__init__.py",
        "tests/conftest.py",
        "tests/unit/test_package.py",
        "scripts/smoke-wheel.py",
    }
    if request["component_root"] != ".":
        files |= {"README.md", "Makefile"}
    if "cli" in request["profiles"]:
        files |= {
            f"src/{package}/__main__.py",
            f"src/{package}/cli.py",
            "tests/unit/test_cli.py",
        }
    if "api" in request["profiles"]:
        files |= {f"src/{package}/api.py", "tests/integration/test_api.py"}
    if "systemd" in request["profiles"]:
        module = request["service_module"].replace(".", "/")
        files |= {
            f"src/{module}.py",
            f"src/{package}/systemd/{request['project_name']}.service",
            "tests/unit/test_service.py",
        }
        parent = PurePosixPath(module).parent
        while parent.as_posix() != package:
            files.add(f"src/{parent}/__init__.py")
            parent = parent.parent
    return {repository_path(request, file) for file in files}


def normalize(request: dict) -> dict:
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise PythonProjectError(
            "unsupported Python request fields; regenerate with producer contract v1"
        )
    if (
        type(request["schema_version"]) is not int
        or request["schema_version"] != 1
        or request["profile"] != PROFILE
    ):
        raise PythonProjectError(
            "unsupported Python producer contract; regenerate candidates"
        )
    for field in (
        "candidate_set_id",
        "component_id",
        "materialization_unit_id",
        "project_name",
    ):
        if not isinstance(request[field], str) or not IDENTIFIER.fullmatch(
            request[field]
        ):
            raise PythonProjectError(f"invalid {field}")
    package = request["package_name"]
    if (
        not isinstance(package, str)
        or not PACKAGE.fullmatch(package)
        or keyword.iskeyword(package)
    ):
        raise PythonProjectError("invalid package_name")
    path_name(request["component_root"], root=True)
    description = request["description"]
    if (
        not isinstance(description, str)
        or any(ord(c) < 32 for c in description)
        or len(description) > 500
    ):
        raise PythonProjectError("description must be single-line text")
    if not isinstance(request["project_kind"], str) or request["project_kind"] not in {
        "application",
        "library",
    }:
        raise PythonProjectError("project_kind must be application or library")
    if not isinstance(request["python_requires"], str) or request[
        "python_requires"
    ] not in {
        ">=3.11,<3.14",
        ">=3.12,<3.14",
        ">=3.13,<3.14",
    }:
        raise PythonProjectError("unsupported Python range for core producer")
    profiles = request["profiles"]
    if (
        not isinstance(profiles, list)
        or any(not isinstance(p, str) for p in profiles)
        or len(profiles) != len(set(profiles))
        or not set(profiles) <= PROFILES
    ):
        raise PythonProjectError("unsupported or duplicate core profile")
    if request["project_kind"] == "library" and set(profiles) & {"api", "systemd"}:
        raise PythonProjectError("API and systemd require application project_kind")
    module = request["service_module"]
    if "systemd" in profiles:
        if (
            not isinstance(module, str)
            or not module.startswith(package + ".")
            or any(
                not PACKAGE.fullmatch(p) or keyword.iskeyword(p)
                for p in module.split(".")
            )
        ):
            raise PythonProjectError(
                "service_module must be an importable module inside the package"
            )
        if module.split(".")[1] in {
            "api",
            "cli",
            "__main__",
            "__init__",
            "systemd",
            "_version",
        }:
            raise PythonProjectError("service_module collides with a profile artifact")
    elif module is not None:
        raise PythonProjectError("service_module requires systemd profile")
    dependencies = request["dependencies"]
    if not isinstance(dependencies, dict):
        raise PythonProjectError(
            "dependencies must be a mapping of names to bounded specifiers"
        )
    for name, constraint in dependencies.items():
        if (
            not isinstance(name, str)
            or not re.fullmatch(r"[a-z][a-z0-9-]*", name)
            or not isinstance(constraint, str)
            or not re.fullmatch(r">=\d+(?:\.\d+){0,2},<\d+(?:\.\d+){0,2}", constraint)
        ):
            raise PythonProjectError(
                "dependencies require normalized names and bounded version ranges"
            )
    if len({name.replace("-", "_") for name in dependencies}) != len(dependencies):
        raise PythonProjectError("duplicate dependency identity")
    effective_dependencies = {}
    if "cli" in profiles:
        effective_dependencies.update(typer=">=0.12,<1", rich=">=13.7,<15")
    if "api" in profiles:
        effective_dependencies.update(fastapi=">=0.116,<1", uvicorn=">=0.30,<1")
    effective_dependencies.update(dependencies)
    result = {
        **request,
        "profiles": sorted(profiles),
        "dependencies": dict(sorted(effective_dependencies.items())),
    }
    for field in ("assigned_paths", "excluded_paths"):
        paths = request[field]
        if not isinstance(paths, list):
            raise PythonProjectError(f"{field} must be a list")
        normalized = [path_name(p) for p in paths]
        if len({p.casefold() for p in normalized}) != len(normalized):
            raise PythonProjectError("duplicate or case-folded paths")
        result[field] = sorted(normalized)
    if set(result["assigned_paths"]) != expected_paths(result):
        raise PythonProjectError(
            "assigned paths must equal the selected profile's exact artifact set"
        )
    if set(result["assigned_paths"]) & set(result["excluded_paths"]):
        raise PythonProjectError("assigned paths overlap exclusions")
    return result


def default_request(
    *,
    root: str = ".",
    profiles: tuple[str, ...] = (),
    kind: str = "application",
    name: str = "example-project",
    package: str = "example_project",
) -> dict:
    """Build an explicit request; callers can replace defaults before normalization."""
    request = dict(
        schema_version=1,
        candidate_set_id="python-main",
        component_id="main",
        materialization_unit_id="main",
        component_root=root,
        profile=PROFILE,
        project_name=name,
        package_name=package,
        description="Python project",
        project_kind=kind,
        python_requires=">=3.11,<3.14",
        profiles=list(profiles),
        service_module=package + ".service" if "systemd" in profiles else None,
        dependencies={},
        assigned_paths=[],
        excluded_paths=[],
    )
    request["assigned_paths"] = sorted(expected_paths(request))
    if root == ".":
        request["excluded_paths"] = ["README.md", "Makefile", ".gitignore"]
    return request


def template(name: str, values: dict[str, str]) -> str:
    content = (ROOT / "assets" / name).read_text()
    for key, value in values.items():
        content = content.replace("{{" + key + "}}", value)
    if re.search(r"\{\{[A-Za-z_][A-Za-z_0-9]*\}\}", content):
        raise PythonProjectError("unresolved template placeholder")
    return content


def render_files(request: dict) -> dict[str, bytes]:
    r = normalize(request)
    package, name = r["package_name"], r["project_name"]
    values = {
        "package_name": package,
        "project_slug": name,
        "short_description": "Python project",
        "author_name": "",
        "default_branch": "main",
    }
    pyproject = template("pyproject.toml.template", values)
    pyproject = pyproject.replace(
        'description = "Python project"',
        "description = " + json.dumps(r["description"], ensure_ascii=False),
    )
    pyproject = pyproject.replace('authors = [{ name = "" }]\n', "").replace(
        'license = "Apache-2.0"\nlicense-files = ["LICENSE"]\n', ""
    )
    pyproject = pyproject.replace(">=3.11,<3.14", r["python_requires"]).replace(
        'target-version = "py311"',
        f'target-version = "py{r["python_requires"][2:6].replace(".", "")}"',
    )
    dep_lines = "\n".join(
        "  " + json.dumps(key + value) + "," for key, value in r["dependencies"].items()
    )
    pyproject = pyproject.replace(
        "  # Add only direct runtime dependencies required by the selected profiles.",
        dep_lines,
    )
    if "api" in r["profiles"]:
        pyproject = pyproject.replace("dev = [", 'dev = [\n  "httpx>=0.27,<1",')
    if "cli" not in r["profiles"]:
        pyproject = pyproject.replace(
            f'[project.scripts]\n{name} = "{package}.__main__:main"\n', ""
        )
    if "systemd" in r["profiles"]:
        pyproject = pyproject.replace(
            f'# {package} = ["systemd/*"]', f'{package} = ["systemd/*"]'
        )
    tomllib.loads(pyproject)
    files = {
        "pyproject.toml": pyproject,
        f"src/{package}/__init__.py": '"""' + name + ' package."""\n',
        "tests/conftest.py": template("tests-conftest.py.template", values),
        "tests/unit/test_package.py": f"from importlib import metadata\n\n\ndef test_distribution():\n    assert metadata.version({name!r})\n",
        "scripts/smoke-wheel.py": template("smoke-wheel.py.template", values),
    }
    if r["component_root"] != ".":
        files["README.md"] = (
            f"# {name}\n\n{r['description']}\n\nRun `uv sync --locked` and `make check smoke-wheel`.\n"
        )
        files["Makefile"] = integration_files(r)["Makefile"].decode()
    if "cli" in r["profiles"]:
        files[f"src/{package}/cli.py"] = f'''"""Command line interface."""
from importlib.metadata import version
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True)


def show_version(value: bool) -> None:
    if value:
        typer.echo(version({name!r}))
        raise typer.Exit()


@app.callback()
def options(
    version_flag: Annotated[bool, typer.Option("--version", callback=show_version, is_eager=True)] = False,
) -> None:
    """{name} commands."""


@app.command()
def health() -> None:
    """Report local starter health."""
    typer.echo("ok")


def main() -> None:
    app()
'''
        files[f"src/{package}/__main__.py"] = (
            "from .cli import main\n\nif __name__ == '__main__':\n    main()\n"
        )
        files["tests/unit/test_cli.py"] = f"""from typer.testing import CliRunner

from {package}.cli import app


def test_cli():
    runner = CliRunner()
    assert runner.invoke(app, ["health"]).stdout.strip() == "ok"
    assert runner.invoke(app, ["--help"]).exit_code == 0
    assert runner.invoke(app, ["--version"]).exit_code == 0
"""
        files["scripts/smoke-wheel.py"] = files["scripts/smoke-wheel.py"].replace(
            'run([str(command), "--help"], cwd=temporary, env=env)',
            'run([str(command), "--help"], cwd=temporary, env=env)\n            run([str(command), "--version"], cwd=temporary, env=env)',
        )
    if "api" in r["profiles"]:
        files[f"src/{package}/api.py"] = template("api.py.template", values)
        files["tests/integration/test_api.py"] = f"""import asyncio

import httpx

from {package}.api import app


def test_health():
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for path, expected in [("/healthz", "ok"), ("/readyz", "ready")]:
                response = await client.get(path)
                assert response.status_code == 200
                assert response.json()["status"] == expected
    asyncio.run(check())
"""
        # Asyncio uses a local Unix socket pair; network sockets remain disabled.
        pyproject = files["pyproject.toml"].replace(
            '"--disable-socket",', '"--disable-socket", "--allow-unix-socket",'
        )
        files["pyproject.toml"] = pyproject
    if "systemd" in r["profiles"]:
        module = r["service_module"]
        files[f"src/{module.replace('.', '/')}.py"] = (
            '"""Service entrypoint; implement the selected workload here."""\n\ndef main() -> None:\n    """Run one service invocation."""\n\nif __name__ == "__main__":\n    main()\n'
        )
        resource = f"systemd/{name}.service"
        unit = template(
            "systemd.service.template",
            {
                **values,
                "service_module": module,
                "service_user": name,
                "service_group": name,
            },
        )
        files[f"src/{package}/{resource}"] = unit
        files["tests/unit/test_service.py"] = (
            f"import importlib\n\n\ndef test_service_module():\n    assert callable(importlib.import_module({module!r}).main)\n"
        )
        files["scripts/smoke-wheel.py"] = files["scripts/smoke-wheel.py"].replace(
            "RESOURCES: tuple[str, ...] = ()",
            f"RESOURCES: tuple[str, ...] = ({resource!r},)",
        )
    for path in expected_paths(r):
        relative = (
            path if r["component_root"] == "." else path[len(r["component_root"]) + 1 :]
        )
        if relative.endswith("/__init__.py"):
            files.setdefault(relative, '"""Package."""\n')
    output = {repository_path(r, key): value.encode() for key, value in files.items()}
    if set(output) != expected_paths(r):
        raise PythonProjectError("producer artifact set differs from profile contract")
    for key, value in output.items():
        if key.endswith(".py"):
            ast.parse(value)
        if re.search(rb"\{\{[A-Za-z_][A-Za-z_0-9]*\}\}", value):
            raise PythonProjectError("unresolved template placeholder")
    return output


def integration_files(request: dict) -> dict[str, bytes]:
    """Content recommendations consumed by the respective integration owners."""
    values = {
        "package_name": request["package_name"],
        "project_slug": request["project_name"],
    }
    make = template("Makefile.template", values)
    if "api" not in request["profiles"]:
        make = make.replace("test-integration ", "")
        make = re.sub(
            r"# Omit this target.*?\ntest-integration: sync\n\t.*?\n\n",
            "",
            make,
            flags=re.S,
        )
    ignore = ".venv/\n__pycache__/\n*.py[cod]\n.pytest_cache/\n.ruff_cache/\nbuild/\ndist/\n*.egg-info/\n.coverage*\nhtmlcov/\n"
    version_path = repository_path(
        request, f"src/{request['package_name']}/_version.py"
    )
    ignore += re.sub(r"([!#*?\[\] ])", r"\\\1", version_path) + "\n"
    return {
        "README.md": f"# {request['project_name']}\n\nPython {request['python_requires']} project.\n\nRun `uv sync --locked` and `make check smoke-wheel`.\n".encode(),
        "Makefile": make.encode(),
        ".gitignore": ignore.encode(),
    }


def validations(request: dict) -> list[dict]:
    common = dict(
        owner=OWNER,
        materialization_unit_id=request["materialization_unit_id"],
        candidate_set_id=request["candidate_set_id"],
    )
    return [
        {
            **common,
            "id": request["candidate_set_id"] + ":static",
            "phase": "candidate",
            "command": "python producer validate",
            "network_required": False,
            "status": "passed",
        },
        {
            **common,
            "id": request["candidate_set_id"] + ":readiness",
            "phase": "post-apply",
            "command": "python producer bootstrap",
            "network_required": True,
            "status": "pending",
        },
    ]


def manifest_data(request: dict) -> tuple[dict, dict[str, bytes]]:
    request = normalize(request)
    files = render_files(request)
    entries = []
    for path, payload in sorted(files.items()):
        relative = (
            path
            if request["component_root"] == "."
            else path[len(request["component_root"]) + 1 :]
        )
        entries.append(
            dict(
                path=path,
                candidate="files/" + relative,
                mode="0644",
                sha256=digest(payload),
            )
        )
    return dict(
        schema_version=1,
        candidate_set_id=request["candidate_set_id"],
        owner=OWNER,
        materialization_unit_id=request["materialization_unit_id"],
        profile=PROFILE,
        input_sha256=digest(canonical(request)),
        inputs=request,
        files=entries,
        validations=validations(request),
    ), files


def render_candidates(request_path: Path, output: Path) -> dict:
    request = decode(read_bytes(request_path))
    manifest, files = manifest_data(request)
    output = safe_path(output)
    # Like the coordinator, candidate bytes must never be stored in a Git worktree.
    if any((parent / ".git").exists() for parent in (output, *output.parents)):
        raise PythonProjectError("candidate output must be outside Git worktrees")
    for entry in manifest["files"]:
        private_write(output / entry["candidate"], files[entry["path"]])
    payload = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False).encode()
        + b"\n"
    )
    private_write(output / "manifest.json", payload)
    return dict(
        ok=True,
        manifest=str(output / "manifest.json"),
        manifest_sha256=digest(payload),
        input_sha256=manifest["input_sha256"],
        file_count=len(files),
    )


def validate_candidate_manifest(path: Path) -> dict:
    manifest = decode(read_bytes(path, private=True))
    expected, files = manifest_data(manifest.get("inputs", {}))
    if manifest != expected:
        raise PythonProjectError(
            "Python candidate manifest differs from producer contract"
        )
    for entry in expected["files"]:
        if (
            read_bytes(path.parent / entry["candidate"], private=True)
            != files[entry["path"]]
        ):
            raise PythonProjectError(
                "Python candidate content differs from producer output"
            )
    return expected["inputs"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render", help="Write offline private candidates")
    render.add_argument("--request", type=Path, required=True)
    render.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate", help="Validate exact producer content")
    validate.add_argument("--manifest", type=Path, required=True)
    boot = commands.add_parser(
        "bootstrap", help="Prepare an applied component's project environment"
    )
    boot.add_argument("--target", type=Path, required=True)
    boot.add_argument("--bundle", type=Path, required=True)
    boot.add_argument("--candidate-set", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "render":
            result = render_candidates(args.request, args.output)
        elif args.command == "validate":
            result = dict(ok=True, inputs=validate_candidate_manifest(args.manifest))
        else:
            from python_bootstrap import bootstrap

            result = bootstrap(args.target, args.bundle, args.candidate_set)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (PythonProjectError, OSError, ValueError) as error:
        print(json.dumps(dict(ok=False, error=str(error))), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
