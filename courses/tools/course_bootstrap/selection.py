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
    elif lab and group != "regular":
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


def resolve(group, courses, catalog, *, lab=None, launcher=None):
    labs, launchers = inventory(courses, catalog)
    source = launchers if launcher else labs
    selector = launcher or lab
    if selector:
        matches = [
            key for key in source if key == selector or key.split("/", 1)[1] == selector
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Select one unambiguous supported {'launcher' if launcher else 'lab'}: {selector}"
            )
        selected = {matches[0]: source[matches[0]]}
        if catalog["runtimes"][next(iter(selected.values()))]["group"] != group:
            raise ValueError(f"Selection does not belong to {group} preparation")
    else:
        selected = {
            key: value
            for key, value in labs.items()
            if catalog["runtimes"][value]["group"] == group
        }
    runtimes = set(selected.values())
    # Include native diagnostic variants for selected default labs, never opt-in containers.
    if not launcher:
        for key, runtime in launchers.items():
            slug, filename = key.split("/", 1)
            if f"{slug}/{filename.split('.')[0]}" in selected and not catalog[
                "runtimes"
            ][runtime].get("optional"):
                runtimes.add(runtime)
    if group == "regular" and selected:
        runtimes.add("shared-tools")
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
        "group": group,
        "labs" if not launcher else "launchers": sorted(selected),
        "runtimes": sorted(runtimes),
        "components": components,
    }
