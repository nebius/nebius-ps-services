"""Pure preparation selection; no installation or scheduler access."""

from __future__ import annotations

from . import catalog as definitions

GROUPS = ("regular", "cuda", "communication", "serving", "transformer-engine")


def command(group, *, root=None, lab=None, launcher=None):
    result = f'python3.12 "$HOME/courses/tools/{group}-lab-setup.py"'
    if root is not None:
        import shlex

        result = "python3.12 " + shlex.quote(
            str(root / "tools" / f"{group}-lab-setup.py")
        )
    if launcher:
        result += f" --launcher {launcher}"
    elif lab:
        result += f" --lab {lab}"
    return result


def inventory(courses, catalog):
    labs, launchers = {}, {}
    for slug, course in courses.items():
        row = definitions.bindings(course, catalog)
        labs.update({f"{slug}/{key}": value for key, value in row["labs"].items()})
        launchers.update(
            {f"{slug}/{key}": value for key, value in row["launchers"].items()}
        )
    return labs, launchers


def resolve(group, courses, catalog, *, lab=None, launcher=None, experiment="default", profiler="none", device=None, sanitizer=None):
    from course_execution import runtime_selections

    labs, launchers = inventory(courses, catalog)
    source = launchers if launcher else labs
    selector = launcher or lab
    if not selector and (experiment != "default" or device is not None or sanitizer is not None):
        raise ValueError("Select a lab or canonical launcher for optional experiments/devices")
    if selector:
        matches = [key for key in source if key == selector or key.split("/", 1)[1] == selector]
        if len(matches) != 1:
            raise ValueError(f"Select one unambiguous supported {'launcher' if launcher else 'lab'}: {selector}")
        keys = matches
    else:
        keys = [key for key, runtime in labs.items() if catalog["runtimes"][runtime]["group"] == group]
    runtimes, selected = set(), []
    for key in keys:
        slug, name = key.split("/", 1)
        stem = name.removesuffix(".sbatch")
        if f"{slug}/{stem}" in labs:
            stages = runtime_selections(courses[slug], stem, experiment=experiment, profiler=profiler, device=device, sanitizer=sanitizer)
            names = {row["runtime"] for row in stages}
        else:
            if experiment != "default" or profiler != "none" or device or sanitizer:
                raise ValueError("Auxiliary jobs do not accept experiment/profiler selections")
            names = {launchers[key]}
        if any(catalog["runtimes"][name]["group"] != group for name in names):
            raise ValueError(f"Selection does not belong to {group} preparation")
        runtimes.update(names)
        selected.append(key)
    if group == "regular" and runtimes:
        runtimes.add("shared-tools")
    components = definitions.order(catalog, sorted({part for name in runtimes for part in catalog["runtimes"][name]["components"]}))
    return {
        "group": group,
        "labs" if not launcher else "launchers": sorted(selected),
        "selection": {"experiment": experiment, "profiler": profiler, "device": device, "sanitizer": sanitizer},
        "runtimes": sorted(runtimes),
        "components": components,
    }
