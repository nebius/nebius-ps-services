"""Load only a validated lab runtime; never install software from a batch job."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import sys

from . import catalog as definitions
from .selection import command
from .state import read_json, regular


VARIABLE = re.compile(
    r"(?:COURSE_[A-Z0-9_]+|[A-Z][A-Z0-9_]*(?:_IMAGE_DIGEST|_PREFIX)|HF_HOME|HF_HUB_CACHE|HF_HUB_OFFLINE|TRANSFORMERS_OFFLINE|CUDA_HOME|NVRTC_HOME|CUDA_IMAGE_DIGEST|CUTLASS_ROOT|MODEL_REPOSITORY|TRITON_[A-Z_]+|UCX_PREFIX|DYNAMO_UCX_PREFIX|NCCL_TESTS_HOME)\Z"
)


def context(course):
    course = Path(course).absolute()
    binding = course / ".course-runtime-source.json"
    if binding.exists() or binding.is_symlink():
        value = read_json(binding)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "root"}
            or value["schema"] != "course-runtime-source/v1"
            or not isinstance(value["root"], str)
        ):
            raise ValueError("Invalid prepared runtime source binding")
        prepared = Path(value["root"])
        if not prepared.is_absolute() or ".." in prepared.parts or "\x00" in value["root"]:
            raise ValueError("Prepared runtime root must be an absolute canonical path")
        root, courses = definitions.discover(prepared / "tools/regular-lab-setup.py")
        if root != prepared or course.name not in courses:
            raise ValueError("Prepared catalog does not own this course")
        # Validate the copy's identity, then use its relevant source inputs when
        # checking the original root-bound receipt. Never copy or rewrite receipts.
        _, copied = definitions.discover(course / "tools/regular-lab-setup.py")
        if copied.get(course.name) != course:
            raise ValueError("Invalid isolated course identity")
        courses[course.name] = course
        return root, courses, definitions.load_catalog()
    root, courses = definitions.discover(course / "tools/regular-lab-setup.py")
    # Course-local copies use their parent's catalog runtime when synchronized.
    if (course.parent / "tools/regular-lab-setup.py").is_file():
        root, courses = definitions.discover(
            course.parent / "tools/regular-lab-setup.py"
        )
    catalog = definitions.load_catalog()
    return root, courses, catalog


def load(course, selector, *, launcher=False):
    root, courses, catalog = context(course)
    course = Path(course).absolute()
    if course not in courses.values():
        raise ValueError("Run the native lab command from its course directory")
    bindings = definitions.bindings(course, catalog)
    key = Path(selector).name if launcher else selector
    runtime = bindings["launchers" if launcher else "labs"].get(key)
    if runtime is None:
        raise ValueError(f"No declared runtime for {key}")
    preparation = command(
        catalog["runtimes"][runtime]["group"],
        root=root,
        lab=selector if not launcher else None,
        launcher=selector
        if launcher and catalog["runtimes"][runtime].get("optional")
        else None,
    )
    if (
        launcher
        and not catalog["runtimes"][runtime].get("optional")
        and catalog["runtimes"][runtime]["group"] != "regular"
    ):
        preparation = command(
            catalog["runtimes"][runtime]["group"], root=root, launcher=key
        )
    try:
        record = read_json(root / ".runtime/runtimes" / f"{runtime}.json")
    except FileNotFoundError:
        raise ValueError(f"Prepared runtime is missing; run {preparation}") from None
    if (
        record.get("schema") != "course-runtime/v1"
        or record.get("id") != runtime
        or record.get("root") != str(root)
        or record.get("fingerprint")
        != definitions.fingerprint(catalog, root, courses, runtime)
    ):
        raise ValueError(f"Runtime configuration is stale; run {preparation}")
    if record.get("status") != "installed":
        raise ValueError(
            f"Runtime {runtime} is {record.get('status')}: {record.get('reason')}; run {preparation}"
        )
    for name, component in record["components"].items():
        if catalog["components"][name]["kind"] == "models":
            from .models import validate

            if not validate(root, component, hashes=False):
                raise ValueError(f"Model artifact is missing; run {preparation}")
        prefix = Path(component["prefix"])
        if (
            not prefix.is_relative_to(root / ".runtime/installations")
            or ".." in prefix.parts
            or not prefix.is_dir()
            or any(p.is_symlink() for p in (prefix, *prefix.parents))
        ):
            raise ValueError(
                "Runtime installation is missing or outside managed storage"
            )
        for relative in component["artifacts"]:
            path = Path(relative)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not (prefix / path).is_file()
            ):
                raise ValueError(f"Runtime artifact is missing; run {preparation}")
    for key, value in record["environment"].items():
        if not VARIABLE.fullmatch(key) or not isinstance(value, str) or "\x00" in value:
            raise ValueError("Invalid runtime environment record")
    # Source-owned adapters must execute the frozen copy. Installation/model
    # paths remain rooted at their original managed generations.
    source_prefix = "{course." + course.name + "}"
    environment = dict(record["environment"])
    for key, template in catalog["runtimes"][runtime].get("environment", {}).items():
        if template.startswith(source_prefix + "/"):
            relative = Path(template[len(source_prefix) + 1:])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Invalid course-owned runtime adapter")
            environment[key] = str(course / relative)
    record = {**record, "environment": environment}
    return root, record


def monitoring(root):
    path = root / ".course-environment.sh"
    if not path.exists() and not path.is_symlink():
        return {}
    regular(path, private=True)
    allowed = {"COURSE_WORKSPACE", "COURSE_METRICS_URL", "COURSE_PUSHGATEWAY"}
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = shlex.split(line)
        if len(fields) != 2 or fields[0] != "export" or "=" not in fields[1]:
            raise ValueError("Invalid worker monitoring environment")
        key, value = fields[1].split("=", 1)
        if key not in allowed or key in result:
            raise ValueError("Worker monitoring environment has an unexpected field")
        result[key] = value
    if result.keys() != allowed:
        raise ValueError("Worker monitoring environment is incomplete")
    return result


def shell(course, selector, launcher=False):
    root, record = load(course, selector, launcher=launcher)
    environment = {**record["environment"], **monitoring(root)}
    # Clear only runtime selectors, not job identity or command-local capture/workload controls.
    managed = {
        key
        for row in definitions.load_catalog()["runtimes"].values()
        for key in row.get("environment", {})
    }
    managed.update(
        {
            "COURSE_WORKSPACE",
            "COURSE_METRICS_URL",
            "COURSE_PUSHGATEWAY",
            "COURSE_RUNTIME_ID",
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
        }
    )
    for key in sorted(managed - environment.keys()):
        print(f"unset {key}")
    for key, value in sorted(environment.items()):
        print(f"export {key}={shlex.quote(value)}")
    print(f"export COURSE_RUNTIME_ROOT={shlex.quote(str(root))}")
    print(f"export COURSE_RUNTIME_NAME={shlex.quote(record['id'])}")
    prefixes = {root / ".runtime/installations"}
    previous_root = os.environ.get("COURSE_RUNTIME_ROOT")
    if previous_root:
        prefixes.add(Path(previous_root) / ".runtime/installations")

    def retained_paths(variable, default=""):
        return [
            path
            for path in os.environ.get(variable, default).split(os.pathsep)
            if path
            and not any(Path(path).is_relative_to(prefix) for prefix in prefixes)
        ]

    python = environment.get("COURSE_PYTHON")
    paths = retained_paths("PATH", os.defpath)
    if python:
        paths.insert(0, str(Path(python).parent))
    if environment.get("COURSE_EXECUTABLE_PATH"):
        paths.insert(0, environment["COURSE_EXECUTABLE_PATH"])
    print(f"export PATH={shlex.quote(os.pathsep.join(paths))}")
    libraries = environment.get("COURSE_LIBRARY_PATH", "")
    # Include the previous checkout when switching between independent kits.
    original = retained_paths("LD_LIBRARY_PATH")
    print(
        f"export LD_LIBRARY_PATH={shlex.quote(os.pathsep.join([p for p in [libraries, *original] if p]))}"
    )


def image(course, identity):
    root, courses, catalog = context(course)
    if not re.fullmatch(r"sif://[a-z0-9][a-z0-9_.-]*@sha256:[0-9a-f]{64}", identity):
        raise ValueError("Require an installed immutable SIF identity")
    name, checksum = identity[6:].split("@sha256:")
    candidates = definitions.bindings(Path(course).absolute(), catalog)["launchers"]
    owners = [
        (launcher, catalog["runtimes"][runtime])
        for launcher, runtime in candidates.items()
        if name
        in definitions.order(catalog, catalog["runtimes"][runtime]["components"])
    ]
    if not owners:
        raise ValueError("Container is not owned by this course")
    launcher, owner = owners[0]
    preparation = command(owner["group"], root=root, launcher=launcher)
    try:
        record = read_json(root / ".runtime/images" / f"{name}-{checksum}.json")
    except FileNotFoundError:
        raise ValueError(f"Container is missing; run {preparation}") from None
    path = Path(record["values"]["image"])
    if record["values"].get("identity") != identity or not path.is_relative_to(
        root / ".runtime/installations"
    ):
        raise ValueError("Container identity is not installed in this catalog")
    regular(path)
    info = path.stat()
    recorded = record.get("stats", {}).get("image.sif", {})
    if (
        path.is_symlink()
        or record["artifacts"].get("image.sif") != checksum
        or recorded != {"size": info.st_size, "mtime_ns": info.st_mtime_ns}
    ):
        raise ValueError(f"Cached container content changed; run {preparation}")
    print(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    activation = actions.add_parser("shell")
    activation.add_argument("--course-root", type=Path, required=True)
    selection = activation.add_mutually_exclusive_group(required=True)
    selection.add_argument("--launcher")
    selection.add_argument("--lab")
    container = actions.add_parser("image")
    container.add_argument("--course-root", type=Path, required=True)
    container.add_argument("identity")
    args = parser.parse_args()
    try:
        if args.action == "image":
            image(args.course_root, args.identity)
        else:
            shell(args.course_root, args.launcher or args.lab, bool(args.launcher))
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
