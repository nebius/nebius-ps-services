"""Reconcile all applicable software; workload qualification remains in labs."""

from __future__ import annotations

import os
from contextlib import nullcontext
import subprocess
import sys

from . import catalog as definitions
from . import mount_root
from . import receipts
from .orchestration import (
    ObservedInstaller,
    Packages,
    SUCCESS,
    partitions,
    phase,
    rehydrate,
)
from .progress import Progress
from .state import State, atomic_json, digest, private_dir, setup_lock
from .system import applicability, capacity, install_packages, ubuntu
from .worker import Cancellation, supervise


def setup(script, prepare, *, plan, progress=None, cancellation=None):
    previous_mask = os.umask(0o077)
    try:
        with (
            Cancellation()
            if cancellation is None
            else nullcontext(cancellation) as active
        ):
            active.check()
            if progress is None:
                with Progress() as renderer:
                    return reconcile(
                        script,
                        prepare,
                        plan=plan,
                        progress=renderer,
                        cancellation=active,
                    )
            return reconcile(
                script, prepare, plan=plan, progress=progress, cancellation=active
            )
    finally:
        os.umask(previous_mask)


def reconcile(script, prepare, *, plan, progress, cancellation):
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Run this command with python3.12")
    root, courses = definitions.discover(script)
    catalog = definitions.load_catalog()
    selected = plan["runtimes"]
    progress.update("checking platform and hardware declarations")
    platform = ubuntu()
    nodes = capacity()
    cancellation.check()
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
    fingerprints = {
        name: definitions.fingerprint(catalog, root, courses, name) for name in selected
    }
    runtime_root = private_dir(root / ".runtime")
    with setup_lock(runtime_root):
        # One writer owns directory preparation, installations and finalization.
        if (root / "reference/course.json").is_file():
            prepare(course_root=root)
        else:
            prepare(courses_root=root)
        from course_publication_state import authority, initialize, settings

        _, publication_binding = settings(root)
        publication_directory = runtime_root / "publications"
        publication_exists = (
            publication_directory.exists() or publication_directory.is_symlink()
        )
        if publication_exists:
            authority(root)
        state = State(root)
        installer = ObservedInstaller(root, courses, catalog, state)
        packages = Packages(install_packages)
        outcomes, errors = {}, []
        native, images = partitions(catalog, components)
        exit_status = None
        progress.message(
            f"Preparing {len(selected)} runtimes and {len(components)} components. Detailed logs: {installer.logs}"
        )
        try:
            phase(
                installer,
                native,
                outcomes,
                packages,
                cancellation,
                progress,
                total=len(components),
            )
            cancellation.check()
            if images:

                def image_work(child_cancel, channel):
                    channel.update(
                        "preparing container namespace",
                        completed=len(outcomes),
                        total=len(components),
                        log=installer.logs / "container-worker.log",
                    )
                    child_cancel.check()
                    mount_root.prepare()
                    child_cancel.check()
                    phase(
                        installer,
                        images,
                        outcomes,
                        packages,
                        child_cancel,
                        channel,
                        total=len(components),
                    )

                worker_outcomes, status, fatal = supervise(
                    image_work,
                    progress,
                    cancellation,
                    log=installer.logs / "container-worker.log",
                )
                outcomes.update(worker_outcomes)
                progress.update(
                    "verifying container receipts",
                    completed=len(outcomes),
                    total=len(components),
                    log=installer.logs,
                )
                rehydrate(installer, images, outcomes)
                if status:
                    errors.append(
                        fatal
                        or f"Container worker exited with status {status}; inspect .runtime/setup-logs/container-worker.log. Completed native work is preserved"
                    )
                cancellation.check()
        except SystemExit as exc:
            exit_status = exc.code if isinstance(exc.code, int) else 1
            errors.append("Preparation cancelled; committed components are preserved")
        except (
            OSError,
            ValueError,
            RuntimeError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
        ) as exc:
            errors.append(str(exc))
        # An interrupted rerun must not revoke previously prepared software.
        # Validate only unvisited receipts; attempted failures remain failures.
        try:
            for name in components:
                dependencies = catalog["components"][name].get("depends", [])
                if name in outcomes or any(
                    outcomes.get(key, {}).get("status") not in SUCCESS
                    for key in dependencies
                ):
                    continue
                progress.update(
                    "verifying committed receipts",
                    component=name,
                    completed=len(outcomes),
                    total=len(components),
                    log=installer.logs,
                )
                record = receipts.completed(installer, name)
                if record:
                    installer.records[name] = record
                    outcomes[name] = {
                        "status": "reused",
                        "reason": "Verified committed receipt after preparation stopped",
                    }
        except (
            OSError,
            ValueError,
            RuntimeError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
        ) as exc:
            errors.append(f"Committed receipt verification failed: {exc}")
        progress.update(
            "finalizing",
            completed=len(outcomes),
            total=len(components),
            log=installer.logs,
        )
        for name in components:
            outcomes.setdefault(
                name,
                {
                    "status": "dependency-blocked",
                    "reason": "Preparation stopped before this component",
                },
            )
        # Recheck source identity before publishing any new readiness records.
        if any(
            definitions.fingerprint(catalog, root, courses, name) != fingerprints[name]
            for name in selected
        ):
            raise ValueError(
                "Preparation sources changed during setup; rerun the same command"
            )
        summary = []
        values = installer.values()
        for name in selected:
            enabled, reason = decisions[name]
            spec = catalog["runtimes"][name]
            needed = definitions.order(catalog, spec["components"])
            if enabled is False:
                outcome = "hardware-skipped"
                record = {"status": "skipped", "reason": reason}
            elif any(outcomes[key]["status"] not in SUCCESS for key in needed):
                outcome = (
                    "failed"
                    if any(
                        outcomes[key]["status"] == "failed"
                        for key in spec["components"]
                    )
                    else "dependency-blocked"
                )
                record = {
                    "status": "failed",
                    "reason": "Required software unavailable; inspect .runtime/setup.json",
                }
            else:
                outcome = (
                    "reused"
                    if all(outcomes[key]["status"] == "reused" for key in needed)
                    else "installed"
                )
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
                    "status": "installed",
                    "environment": environment,
                    "components": {key: installer.records[key] for key in needed},
                    "reason": reason,
                }
            record.update(
                schema="course-runtime/v1",
                id=name,
                root=str(root),
                fingerprint=fingerprints[name],
                platform=platform,
            )
            atomic_json(state.runtimes / f"{name}.json", record)
            summary.append(
                {"runtime": name, "status": outcome, "reason": record["reason"]}
            )
        # Exclusion describes this invocation, never a change to saved readiness.
        summary.extend(
            {
                "runtime": name,
                "status": "excluded",
                "reason": "Container dependencies excluded by --exclude containers",
            }
            for name in plan.get("excluded_runtimes", [])
        )
        publication = "unconfigured"
        try:
            if settings(root)[1] != publication_binding:
                raise ValueError(
                    "Monitoring settings changed during preparation; rerun the same setup command"
                )
            successful = {row["runtime"] for row in summary if row["status"] in SUCCESS}
            publication_ready = any(
                catalog["runtimes"][name]
                .get("environment", {})
                .get("COURSE_PUBLISH_PYTHON")
                for name in successful
            )
            if publication_binding is None:
                progress.message(
                    "Automatic publication unconfigured: results remain local. Run normal sync from the laptop, then rerun the same setup command."
                )
            elif publication_ready:
                identity = initialize(root)
                publication = (
                    "already initialized" if publication_exists else "initialized"
                )
                progress.message(
                    f"Automatic publication {publication}: installation {identity['publisher_id']}."
                )
            else:
                publication = "dependency-blocked"
                errors.append(
                    "Automatic publication requires its prepared software; rerun after repairing dependencies"
                )
        except (OSError, ValueError, RuntimeError) as exc:
            publication = "failed"
            errors.append(str(exc))
        course_summary = {}
        for slug, runtimes in plan.get("courses", {}).items():
            counts = {
                key: 0
                for key in (
                    "installed",
                    "reused",
                    "hardware-skipped",
                    "excluded",
                    "failed",
                    "dependency-blocked",
                )
            }
            for row in summary:
                if row["runtime"] in runtimes:
                    counts[row["status"]] += 1
            course_summary[slug] = counts
            progress.message(
                slug
                + ": "
                + ", ".join(f"{count} {key}" for key, count in counts.items())
            )
        atomic_json(
            state.root / "setup.json",
            {
                "schema": "course-setup/v1",
                "root": str(root),
                "selection": plan,
                "components": outcomes,
                "runtimes": summary,
                "courses": course_summary,
                "publication": publication,
                "errors": errors,
            },
        )
        for error in errors:
            progress.message("ERROR: " + error)
        if exit_status is not None:
            raise SystemExit(exit_status)
        cancellation.check()
        if errors or any(row["status"] not in SUCCESS for row in outcomes.values()):
            raise RuntimeError(
                "Some software could not be prepared. Successful labs remain usable; inspect .runtime/setup.json and rerun the same command after resolving failures"
            )
        progress.message(
            "Setup complete: applicable software prepared. GPU, profiler and fabric qualification remain in the labs."
        )
