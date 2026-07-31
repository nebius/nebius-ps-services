"""Static, read-only package and dependency analysis."""

from __future__ import annotations

import configparser
import json
import math
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from scan_common import EXCLUDED_CODE_DIRS, format_int, iter_files, package_markers, rel

LOCKFILE_NAMES = {
    "Cargo.lock",
    "Pipfile.lock",
    "package-lock.json",
    "poetry.lock",
}
BENCHMARK_CATALOG = (
    Path(__file__).resolve().parent.parent / "references" / "famous-project-loc.json"
)


@dataclass(frozen=True)
class DependencyReport:
    package_roots: tuple[Path, ...]
    project_packages: frozenset[str]
    direct_runtime: frozenset[str]
    direct_development: frozenset[str]
    direct_optional: frozenset[str]
    resolved: frozenset[str] | None
    resolution_gaps: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def direct(self) -> frozenset[str]:
        return self.direct_runtime | self.direct_development | self.direct_optional

    @property
    def transitive(self) -> frozenset[str] | None:
        if self.resolved is None:
            return None
        return self.resolved - self.direct


def dependency_key(ecosystem: str, name: str) -> str:
    normalized = name.strip().lower()
    if ecosystem == "python":
        normalized = re.sub(r"[-_.]+", "-", normalized)
    return f"{ecosystem}:{normalized}"


def requirement_name(value: str) -> str | None:
    stripped = value.strip()
    if not stripped or stripped.startswith(("#", "-", "http://", "https://")):
        return None
    match = re.match(r"([A-Za-z0-9_.-]+)", stripped)
    return match.group(1) if match else None


def parse_pyproject_dependencies(
    path: Path,
) -> tuple[set[str], set[str], set[str], list[str]]:
    runtime: set[str] = set()
    development: set[str] = set()
    optional: set[str] = set()
    warnings: list[str] = []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return runtime, development, optional, [f"{path.name}: {type(exc).__name__}"]
    project = data.get("project", {})
    if isinstance(project, dict):
        dependencies = project.get("dependencies", [])
        if isinstance(dependencies, list):
            for raw in dependencies:
                if isinstance(raw, str) and (name := requirement_name(raw)):
                    runtime.add(dependency_key("python", name))
        groups = project.get("optional-dependencies", {})
        if isinstance(groups, dict):
            for values in groups.values():
                if not isinstance(values, list):
                    continue
                for raw in values:
                    if isinstance(raw, str) and (name := requirement_name(raw)):
                        optional.add(dependency_key("python", name))
    tool = data.get("tool")
    poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
    if isinstance(poetry, dict):
        dependencies = poetry.get("dependencies", {})
        if isinstance(dependencies, dict):
            runtime.update(
                dependency_key("python", name)
                for name in dependencies
                if name.lower() != "python"
            )
        groups = poetry.get("group", {})
        if isinstance(groups, dict):
            for group in groups.values():
                deps = group.get("dependencies", {}) if isinstance(group, dict) else {}
                if isinstance(deps, dict):
                    development.update(dependency_key("python", name) for name in deps)
    dependency_groups = data.get("dependency-groups", {})
    if isinstance(dependency_groups, dict):
        for values in dependency_groups.values():
            if not isinstance(values, list):
                continue
            for raw in values:
                if isinstance(raw, str) and (name := requirement_name(raw)):
                    development.add(dependency_key("python", name))
    return runtime, development, optional, warnings


def parse_package_json_dependencies(
    path: Path,
) -> tuple[set[str], set[str], set[str], list[str]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return set(), set(), set(), [f"{path.name}: {type(exc).__name__}"]

    def names(section: str) -> set[str]:
        values = data.get(section, {}) if isinstance(data, dict) else {}
        if not isinstance(values, dict):
            return set()
        return {dependency_key("node", name) for name in values}

    return (
        names("dependencies"),
        names("devDependencies"),
        names("optionalDependencies") | names("peerDependencies"),
        [],
    )


def parse_setup_cfg_dependencies(
    path: Path,
) -> tuple[set[str], set[str], set[str], list[str]]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (configparser.Error, OSError) as exc:
        return set(), set(), set(), [f"{path.name}: {type(exc).__name__}"]

    def parsed_lines(value: str) -> set[str]:
        result = set()
        for raw in value.splitlines():
            if name := requirement_name(raw):
                result.add(dependency_key("python", name))
        return result

    runtime = parsed_lines(parser.get("options", "install_requires", fallback=""))
    optional: set[str] = set()
    if parser.has_section("options.extras_require"):
        for _, value in parser.items("options.extras_require"):
            optional.update(parsed_lines(value))
    return runtime, set(), optional, []


def parse_cargo_dependencies(
    path: Path,
) -> tuple[set[str], set[str], set[str], list[str]]:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return set(), set(), set(), [f"{path.name}: {type(exc).__name__}"]

    def names(container: dict[str, object], section: str) -> set[str]:
        values = container.get(section, {})
        if not isinstance(values, dict):
            return set()
        found = set()
        for name, declaration in values.items():
            actual_name = (
                declaration.get("package")
                if isinstance(declaration, dict)
                and isinstance(declaration.get("package"), str)
                else name
            )
            found.add(dependency_key("rust", actual_name))
        return found

    target_runtime: set[str] = set()
    target_development: set[str] = set()
    targets = data.get("target")
    if isinstance(targets, dict):
        for target in targets.values():
            if not isinstance(target, dict):
                continue
            target_runtime.update(names(target, "dependencies"))
            target_development.update(names(target, "dev-dependencies"))
            target_development.update(names(target, "build-dependencies"))

    return (
        names(data, "dependencies") | target_runtime,
        names(data, "dev-dependencies")
        | names(data, "build-dependencies")
        | target_development,
        set(),
        [],
    )


def parse_go_mod_dependencies(path: Path) -> tuple[set[str], set[str]]:
    direct: set[str] = set()
    resolved: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return direct, resolved
    in_require = False
    for raw in lines:
        stripped = raw.strip()
        if stripped == "require (":
            in_require = True
            continue
        if in_require and stripped == ")":
            in_require = False
            continue
        if stripped.startswith("require "):
            stripped = stripped.removeprefix("require ").strip()
        elif not in_require:
            continue
        if not stripped or stripped.startswith("//"):
            continue
        name = stripped.split()[0]
        key = dependency_key("go", name)
        resolved.add(key)
        if "// indirect" not in raw:
            direct.add(key)
    return direct, resolved


def parse_requirements_dependencies(path: Path) -> set[str]:
    found: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return found
    for line in lines:
        if name := requirement_name(line):
            found.add(dependency_key("python", name))
    return found


def parse_lockfile(path: Path) -> set[str] | None:
    try:
        if path.name == "package-lock.json":
            data = json.loads(path.read_text(encoding="utf-8"))
            packages = data.get("packages", {}) if isinstance(data, dict) else {}
            if isinstance(packages, dict):
                names = set()
                for package_path in packages:
                    marker = "node_modules/"
                    if marker not in package_path:
                        continue
                    name = package_path.rsplit(marker, 1)[1]
                    if name:
                        names.add(dependency_key("node", name))
                if names:
                    return names
            dependencies = (
                data.get("dependencies", {}) if isinstance(data, dict) else {}
            )
            if isinstance(dependencies, dict):
                names = set()
                pending = [dependencies]
                while pending:
                    values = pending.pop()
                    for name, details in values.items():
                        names.add(dependency_key("node", name))
                        if isinstance(details, dict):
                            nested = details.get("dependencies")
                            if isinstance(nested, dict):
                                pending.append(nested)
                return names
        elif path.name in {"Cargo.lock", "poetry.lock"}:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            packages = data.get("package", []) if isinstance(data, dict) else []
            ecosystem = "rust" if path.name == "Cargo.lock" else "python"
            return {
                dependency_key(ecosystem, item["name"])
                for item in packages
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            }
        elif path.name == "Pipfile.lock":
            data = json.loads(path.read_text(encoding="utf-8"))
            names = set()
            if isinstance(data, dict):
                for section in ("default", "develop"):
                    values = data.get(section, {})
                    if isinstance(values, dict):
                        names.update(dependency_key("python", name) for name in values)
            return names
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, KeyError):
        return None
    return None


def project_package_identity(path: Path) -> str | None:
    try:
        if path.name == "pyproject.toml":
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            project = data.get("project")
            tool = data.get("tool")
            poetry = tool.get("poetry") if isinstance(tool, dict) else None
            name = project.get("name") if isinstance(project, dict) else None
            if not isinstance(name, str) and isinstance(poetry, dict):
                name = poetry.get("name")
            return dependency_key("python", name) if isinstance(name, str) else None
        if path.name == "package.json":
            data = json.loads(path.read_text(encoding="utf-8"))
            name = data.get("name") if isinstance(data, dict) else None
            return dependency_key("node", name) if isinstance(name, str) else None
        if path.name == "Cargo.toml":
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            package = data.get("package")
            name = package.get("name") if isinstance(package, dict) else None
            return dependency_key("rust", name) if isinstance(name, str) else None
        if path.name == "go.mod":
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("module "):
                    return dependency_key("go", line.strip().split(maxsplit=1)[1])
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return None
    return None


def lockfile_ecosystem(path: Path) -> str | None:
    return {
        "package-lock.json": "node",
        "Cargo.lock": "rust",
        "Pipfile.lock": "python",
        "poetry.lock": "python",
    }.get(path.name)


def workspace_lock_covers(ecosystem: str, lock_root: Path, package_root: Path) -> bool:
    if package_root == lock_root:
        return True
    try:
        relative = package_root.relative_to(lock_root).as_posix()
    except ValueError:
        return False
    patterns: list[str] = []
    excludes: list[str] = []
    try:
        if ecosystem == "node":
            manifest = lock_root / "package.json"
            if not manifest.is_file() or manifest.is_symlink():
                return False
            data = json.loads(manifest.read_text(encoding="utf-8"))
            workspaces = data.get("workspaces") if isinstance(data, dict) else None
            if isinstance(workspaces, list):
                patterns = [item for item in workspaces if isinstance(item, str)]
            elif isinstance(workspaces, dict):
                packages = workspaces.get("packages")
                if isinstance(packages, list):
                    patterns = [item for item in packages if isinstance(item, str)]
        elif ecosystem == "rust":
            manifest = lock_root / "Cargo.toml"
            if not manifest.is_file() or manifest.is_symlink():
                return False
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
            workspace = data.get("workspace")
            members = workspace.get("members") if isinstance(workspace, dict) else None
            if isinstance(members, list):
                patterns = [item for item in members if isinstance(item, str)]
            excluded = workspace.get("exclude") if isinstance(workspace, dict) else None
            if isinstance(excluded, list):
                excludes = [item for item in excluded if isinstance(item, str)]
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError):
        return False
    relative_path = PurePosixPath(relative)
    if any(relative_path.match(pattern.rstrip("/")) for pattern in excludes):
        return False
    return any(relative_path.match(pattern.rstrip("/")) for pattern in patterns)


def collect_dependencies(root: Path) -> DependencyReport:
    runtime: set[str] = set()
    development: set[str] = set()
    optional: set[str] = set()
    resolved: set[str] = set()
    project_packages: set[str] = set()
    declared_roots: set[tuple[str, Path]] = set()
    lock_roots: set[tuple[str, Path]] = set()
    warnings: list[str] = []
    markers = package_markers(root)
    package_roots = tuple(sorted({path.parent for path in markers}))
    supported_markers = {
        "pyproject.toml",
        "setup.cfg",
        "package.json",
        "Cargo.toml",
        "go.mod",
    }
    for marker in markers:
        if identity := project_package_identity(marker):
            project_packages.add(identity)
        if marker.name == "pyproject.toml":
            values = parse_pyproject_dependencies(marker)
            runtime.update(values[0])
            development.update(values[1])
            optional.update(values[2])
            if values[0] or values[1] or values[2]:
                declared_roots.add(("python", marker.parent))
            warnings.extend(f"{rel(marker, root)}: {warning}" for warning in values[3])
        elif marker.name == "setup.cfg":
            values = parse_setup_cfg_dependencies(marker)
            runtime.update(values[0])
            development.update(values[1])
            optional.update(values[2])
            if values[0] or values[1] or values[2]:
                declared_roots.add(("python", marker.parent))
            warnings.extend(f"{rel(marker, root)}: {warning}" for warning in values[3])
        elif marker.name == "package.json":
            values = parse_package_json_dependencies(marker)
            runtime.update(values[0])
            development.update(values[1])
            optional.update(values[2])
            if values[0] or values[1] or values[2]:
                declared_roots.add(("node", marker.parent))
            warnings.extend(f"{rel(marker, root)}: {warning}" for warning in values[3])
        elif marker.name == "Cargo.toml":
            values = parse_cargo_dependencies(marker)
            runtime.update(values[0])
            development.update(values[1])
            optional.update(values[2])
            if values[0] or values[1] or values[2]:
                declared_roots.add(("rust", marker.parent))
            warnings.extend(f"{rel(marker, root)}: {warning}" for warning in values[3])
        elif marker.name == "go.mod":
            direct, go_resolved = parse_go_mod_dependencies(marker)
            runtime.update(direct)
            resolved.update(go_resolved)
            if direct or go_resolved:
                declared_roots.add(("go", marker.parent))
                lock_roots.add(("go", marker.parent))
        elif marker.name not in supported_markers:
            warnings.append(f"{rel(marker, root)}: dependency parser not supported")

    for path in iter_files(root, EXCLUDED_CODE_DIRS):
        lowered = path.name.lower()
        if lowered.startswith("requirements") and path.suffix == ".txt":
            target = (
                development
                if any(token in lowered for token in ("dev", "test", "lint"))
                else runtime
            )
            requirement_dependencies = parse_requirements_dependencies(path)
            target.update(requirement_dependencies)
            if requirement_dependencies:
                declared_roots.add(("python", path.parent))

    lockfiles = [
        path
        for path in iter_files(root, EXCLUDED_CODE_DIRS)
        if path.name in LOCKFILE_NAMES
    ]
    parsed_lock = False
    for lockfile in lockfiles:
        values = parse_lockfile(lockfile)
        if values is None:
            warnings.append(
                f"{rel(lockfile, root)}: lockfile parser not supported or malformed"
            )
            continue
        parsed_lock = True
        resolved.update(values)
        if ecosystem := lockfile_ecosystem(lockfile):
            lock_roots.add((ecosystem, lockfile.parent))
    missing_roots = []
    for ecosystem, package_root in sorted(
        declared_roots, key=lambda item: (item[0], item[1].as_posix())
    ):
        covered = any(
            lock_ecosystem == ecosystem
            and workspace_lock_covers(ecosystem, lock_root, package_root)
            for lock_ecosystem, lock_root in lock_roots
        )
        if not covered:
            missing_roots.append(f"{ecosystem}:{rel(package_root, root)}")
    resolution_gaps = tuple(missing_roots)
    for gap in resolution_gaps:
        warnings.append(
            f"{gap}: declared dependencies have no supported lockfile or module selection evidence; resolved counts are partial"
        )
    return DependencyReport(
        package_roots,
        frozenset(project_packages),
        frozenset(runtime),
        frozenset(development),
        frozenset(optional),
        frozenset(resolved - project_packages) if parsed_lock or resolved else None,
        resolution_gaps,
        tuple(warnings),
    )


def load_benchmarks(path: Path = BENCHMARK_CATALOG) -> list[dict[str, object]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    if data.get("schema_version") != 1 or data.get("method") != "code-info-code-loc-v1":
        return []
    benchmarks = data.get("benchmarks")
    if not isinstance(benchmarks, list):
        return []
    required = {
        "name",
        "version",
        "repository",
        "ref",
        "code_loc",
        "scope",
        "measured_at",
        "source_url",
    }
    return [
        item
        for item in benchmarks
        if isinstance(item, dict)
        and required <= item.keys()
        and isinstance(item.get("code_loc"), int)
        and item["code_loc"] > 0
    ]


def nearest_benchmarks(
    code_loc: int, benchmarks: list[dict[str, object]]
) -> list[dict[str, object]]:
    if code_loc <= 0:
        return []
    return sorted(
        benchmarks,
        key=lambda item: abs(math.log(code_loc / int(item["code_loc"]))),
    )[:2]


def resolved_dependency_value(report: DependencyReport) -> str:
    if report.resolved is None:
        return "Unavailable"
    value = format_int(len(report.resolved))
    if report.resolution_gaps:
        value += (
            f" (partial; missing {', '.join(report.resolution_gaps)} selection data)"
        )
    return value


def transitive_dependency_value(report: DependencyReport) -> str:
    if report.transitive is None:
        return "Unavailable"
    value = format_int(len(report.transitive))
    if report.resolution_gaps:
        value += " (partial)"
    return value
