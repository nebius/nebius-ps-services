"""Reconcile all applicable installed runtimes; workload execution stays in labs."""

from __future__ import annotations

import os
import subprocess
import sys

from . import catalog as definitions
from . import mount_root
from .installer import Installer
from .state import State, atomic_json, digest, private_dir, setup_lock
from .system import applicability, capacity, install_packages, prerequisites, ubuntu


def setup(script, prepare, *, plan):
    previous_mask = os.umask(0o077)
    try:
        return reconcile(script, prepare, plan=plan)
    finally:
        os.umask(previous_mask)


def reconcile(script, prepare, *, plan):
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Run this command with python3.12")
    root, courses = definitions.discover(script)
    catalog = definitions.load_catalog()
    selected = plan["runtimes"]
    platform = ubuntu()
    nodes = capacity()
    decisions = {
        name: applicability(catalog["runtimes"][name].get("needs", {}), nodes)
        for name in selected
    }
    components = definitions.order(
        catalog,
        sorted(
            {
                component
                for name in selected
                if decisions[name][0] is not False
                for component in catalog["runtimes"][name]["components"]
            }
        ),
    )
    images = any(catalog["components"][name]["kind"] == "image" for name in components)
    # Existing directories are inspected before any installation or package changes.
    if (root / "reference/course.json").is_file():
        prepare(course_root=root)
    else:
        prepare(courses_root=root)
    runtime_root = private_dir(root / ".runtime")
    with setup_lock(runtime_root):
        state = State(root)
        installer = Installer(root, courses, catalog, state)
        if images:
            mount_root.prepare()
        install_packages(
            installer.run, packages=prerequisites(catalog, components), apptainer=images
        )
        failed = {}
        for name in definitions.order(catalog, components):
            dependencies = catalog["components"][name].get("depends", [])
            if any(dependency in failed for dependency in dependencies):
                failed[name] = "A required installation failed"
                continue
            try:
                installer.install(name)
            except (
                OSError,
                RuntimeError,
                ValueError,
                subprocess.SubprocessError,
            ) as exc:
                failed[name] = str(exc)
                print(f"FAILED {name}: {exc}", file=sys.stderr, flush=True)
        summary = []
        values = installer.values()
        for name in selected:
            enabled, reason = decisions[name]
            spec = catalog["runtimes"][name]
            needed = definitions.order(catalog, spec["components"])
            if enabled is False:
                status = "skipped"
                record = {"status": status, "reason": reason}
            elif any(key in failed for key in needed):
                status = "failed"
                record = {
                    "status": status,
                    "reason": "Required software installation failed",
                }
            else:
                status = "installed"
                environment = {
                    key: installer.expand(value, values)
                    for key, value in spec.get("environment", {}).items()
                }
                environment["COURSE_RUNTIME_ID"] = (
                    "course-runtime://"
                    + name
                    + "@sha256:"
                    + digest({key: installer.records[key] for key in needed})
                )
                record = {
                    "status": status,
                    "environment": environment,
                    "components": {key: installer.records[key] for key in needed},
                    "reason": reason,
                }
            record.update(
                schema="course-runtime/v1",
                id=name,
                root=str(root),
                fingerprint=definitions.fingerprint(catalog, root, courses, name),
                platform=platform,
            )
            atomic_json(state.runtimes / f"{name}.json", record)
            print(f"{status.upper()} {name}: {record['reason']}", flush=True)
            summary.append(
                {"runtime": name, "status": status, "reason": record["reason"]}
            )
        atomic_json(
            state.root / f"setup-{plan['group']}.json",
            {
                "schema": "course-setup/v1",
                "root": str(root),
                "selection": plan,
                "runtimes": summary,
            },
        )
        if failed:
            raise RuntimeError(
                "Some software could not be installed. Completed components were preserved; rerun the same command after resolving the reported errors"
            )
        print(
            "Setup complete: applicable software installed and configured. GPU, profiler and fabric qualification remain in the labs."
        )
