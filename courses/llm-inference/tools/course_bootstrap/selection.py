"""Pure preparation selection; no installation or scheduler access."""

from __future__ import annotations

from . import catalog as definitions


def command(*, root=None, lab=None, launcher=None):
    import shlex

    result = 'python3.12 "$HOME/courses/tools/lab-setup.py"'
    if root is not None:
        result = "python3.12 " + shlex.quote(str(root / "tools" / "lab-setup.py"))
    if launcher:
        result += " --launcher " + shlex.quote(launcher)
    elif lab:
        result += " --lab " + shlex.quote(lab)
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


def resolve(
    courses,
    catalog,
    *,
    lab=None,
    launcher=None,
    experiment=None,
    profiler=None,
    device=None,
    sanitizer=None,
    exclude=None,
):
    from course_execution import capabilities, runtime_selections

    labs, launchers = inventory(courses, catalog)
    source = launchers if launcher else labs
    selector = launcher or lab
    if lab and launcher:
        raise ValueError("Select either --lab or --launcher")
    if exclude not in (None, "containers"):
        raise ValueError("Supported exclusion: containers")
    if not selector and any(
        value is not None for value in (experiment, profiler, device, sanitizer)
    ):
        raise ValueError("Variant flags require --lab or --launcher")
    experiment, profiler = experiment or "default", profiler or "none"
    if selector:
        matches = [
            key for key in source if key == selector or key.split("/", 1)[1] == selector
        ]
        if len(matches) != 1:
            candidates = ", ".join(sorted(matches)) or "no matching identity"
            raise ValueError(
                f"Select one unambiguous supported {'launcher' if launcher else 'lab'} using course/name: {selector} ({candidates})"
            )
        keys = matches
    else:
        keys = sorted(labs)
    runtimes, selected = set(), []
    by_course = {}
    if not selector:
        for slug, course in courses.items():
            names = {
                row["runtime"]
                for spec in capabilities(course).values()
                for row in spec["variants"]
            }
            names.update(
                value for key, value in launchers.items() if key.startswith(slug + "/")
            )
            names.add("shared-tools")
            by_course[slug] = sorted(names)
            runtimes.update(names)
    for key in keys:
        if not selector:
            selected.append(key)
            continue
        slug, name = key.split("/", 1)
        stem = name.removesuffix(".sbatch")
        if f"{slug}/{stem}" in labs:
            stages = runtime_selections(
                courses[slug],
                stem,
                experiment=experiment,
                profiler=profiler,
                device=device,
                sanitizer=sanitizer,
            )
            names = {row["runtime"] for row in stages}
        else:
            if experiment != "default" or profiler != "none" or device or sanitizer:
                raise ValueError(
                    "Auxiliary jobs do not accept experiment/profiler selections"
                )
            names = {launchers[key]}
        runtimes.update(names)
        by_course[slug] = sorted(names | {"shared-tools"})
        selected.append(key)
    if runtimes:
        runtimes.add("shared-tools")
    excluded = set()
    if exclude == "containers":
        excluded = {
            name
            for name in runtimes
            if any(
                catalog["components"][part]["kind"] == "image"
                for part in definitions.order(
                    catalog, catalog["runtimes"][name]["components"]
                )
            )
        }
        runtimes.difference_update(excluded)
        if selector and not runtimes - {"shared-tools"}:
            raise ValueError(
                "--exclude containers excludes every selected runtime; "
                "omit the exclusion to prepare this target"
            )
    components = definitions.order(
        catalog,
        sorted(
            {
                part
                for name in runtimes
                for part in catalog["runtimes"][name]["components"]
            }
        ),
    )
    return {
        "labs" if not launcher else "launchers": sorted(selected),
        "courses": by_course,
        "selection": {
            "experiment": experiment,
            "profiler": profiler,
            "device": device,
            "sanitizer": sanitizer,
            "exclude": [exclude] if exclude else [],
        },
        "runtimes": sorted(runtimes),
        "excluded_runtimes": sorted(excluded),
        "components": components,
    }
