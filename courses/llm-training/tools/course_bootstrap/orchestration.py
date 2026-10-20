"""Failure isolation and receipt reconciliation outside fingerprinted recipes."""

from __future__ import annotations

from contextlib import redirect_stdout
import shutil
import subprocess

from . import receipts, system
from .installer import Installer
from .progress import Messages


SUCCESS = {"installed", "reused"}


class ObservedInstaller(Installer):
    """Report existing command labels without changing installation identities."""

    def run(self, argv, **kwargs):
        label = kwargs.get("label", "install")
        self.progress.update(
            "preparing", component=self.current, log=self.logs / (label + ".log")
        )
        return super().run(argv, **kwargs)


def partitions(catalog, components):
    image_dependent = set()
    for name in components:
        spec = catalog["components"][name]
        if spec["kind"] == "image" or any(
            d in image_dependent for d in spec.get("depends", [])
        ):
            image_dependent.add(name)
    return (
        [n for n in components if n not in image_dependent],
        [n for n in components if n in image_dependent],
    )


class Packages:
    def __init__(self, install):
        self.install = install
        self.failed = set()

    def ensure(self, installer, name):
        spec = installer.catalog["components"][name]
        packages = system.prerequisites(installer.catalog, [name])
        image = spec["kind"] == "image"
        unavailable = self.failed.intersection(packages)
        if unavailable:
            return "dependency-blocked", "Missing system prerequisites: " + ", ".join(
                sorted(unavailable)
            )
        try:
            self.install(installer.run, packages=packages, apptainer=image)
        except system.UnsafePackages:
            raise
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            system.package_health()
            missing = {p for p in packages if not system.installed(p)}
            self.failed.update(missing)
            if image and shutil.which("apptainer") is None:
                missing.add("apptainer")
            return (
                "failed",
                f"{exc}; missing prerequisites: {', '.join(sorted(missing)) or 'none'}",
            )
        return None


def phase(installer, names, outcomes, packages, cancellation, progress, *, total):
    installer.progress = progress
    for name in names:
        cancellation.check()
        installer.current = name
        progress.update(
            "checking",
            component=name,
            completed=len(outcomes),
            total=total,
            log=installer.logs / (name + ".log"),
        )
        dependencies = installer.catalog["components"][name].get("depends", [])
        if any(outcomes[d]["status"] not in SUCCESS for d in dependencies):
            result = {
                "status": "dependency-blocked",
                "reason": "A required component failed",
            }
        else:
            # Invalid state/source is fatal, not an ordinary installation failure.
            before = receipts.completed(installer, name)
            problem = packages.ensure(installer, name)
            if problem:
                result = dict(zip(("status", "reason"), problem))
            elif before:
                installer.records[name] = before
                if installer.catalog["components"][name]["kind"] == "image":
                    installer.record_image(name, before)
                result = {"status": "reused", "reason": "Verified component receipt"}
            else:
                try:
                    with redirect_stdout(Messages(progress)):
                        installer.install(name)
                except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    if isinstance(exc, system.UnsafePackages) or str(exc).startswith(
                        (
                            "Source revision mismatch:",
                            "Download checksum mismatch:",
                            "Pinned TensorRT-LLM source does not contain",
                            "Unexpected CUDA redistributable layout",
                        )
                    ):
                        raise ValueError(
                            f"Source or system verification failed: {exc}"
                        ) from exc
                    result = {"status": "failed", "reason": str(exc)}
                else:
                    result = {
                        "status": "reused"
                        if before == installer.records[name]
                        else "installed",
                        "reason": "Verified component receipt",
                    }
        outcomes[name] = result
        progress.message(f"{result['status'].upper()} {name}: {result['reason']}")
        if hasattr(progress, "outcome"):
            progress.outcome(name, result)
        progress.update(
            "prepared",
            component=name,
            completed=len(outcomes),
            total=total,
            log=installer.logs / (name + ".log"),
        )


def rehydrate(installer, names, outcomes):
    """Worker status never substitutes for current committed receipt integrity."""
    for name in names:
        dependencies = installer.catalog["components"][name].get("depends", [])
        if any(d not in installer.records for d in dependencies):
            outcomes[name] = {
                "status": "dependency-blocked",
                "reason": "Required component has no valid receipt",
            }
            continue
        record = receipts.completed(installer, name)
        if record:
            installer.records[name] = record
            outcomes.setdefault(
                name,
                {
                    "status": "reused",
                    "reason": "Verified committed receipt after worker exit",
                },
            )
        else:
            installer.records.pop(name, None)
            outcomes[name] = {
                "status": "failed",
                "reason": "No valid committed component receipt after worker exit",
            }
