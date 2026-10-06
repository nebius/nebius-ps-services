"""Resolve catalog selections and reviewed argv recipes; never execute prose."""

from __future__ import annotations

import copy
import re
from pathlib import Path

from run_labs_common import canonical, digest, read

RECIPE_FILE = Path(__file__).resolve().parents[1] / "references/recipes.json"
VARIABLE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}|\$([A-Z][A-Z0-9_]*)")


def catalog(root):
    recipes = read(RECIPE_FILE)["labs"]
    actual = {}
    for path in sorted(root.glob("*/reference/course.json")):
        course = path.parents[1].name
        for row in read(path).get("labs", []):
            lab = Path(row["path"]).stem
            actual[f"{course}:{lab}"] = row["path"]
    if set(actual) != set(recipes):
        raise ValueError(
            "Catalog and reviewed recipes differ; maintain recipes before execution"
        )
    for key, source in actual.items():
        if recipes[key]["source"] != source:
            raise ValueError(f"Recipe source identity differs: {key}")
    return recipes


def select(recipes, courses, labs, all_courses):
    if all_courses and (courses or labs):
        raise ValueError("--all-courses cannot be combined with narrower selectors")
    if not (courses or labs or all_courses):
        raise ValueError("Select --lab, --course or --all-courses")
    selected = set(recipes) if all_courses else set()
    for course in courses:
        matches = {k for k, r in recipes.items() if r["course"] == course}
        if not matches:
            raise ValueError(f"Unknown or non-practical course: {course}")
        selected.update(matches)
    for value in labs:
        if ":" not in value:
            raise ValueError("--lab requires course:lab-number or course:source-stem")
        course, lab = value.split(":", 1)
        matches = {
            k
            for k, r in recipes.items()
            if r["course"] == course
            and (
                r["lab"] == lab
                or (lab.isdigit() and int(r["lab"].split("_")[0]) == int(lab))
            )
        }
        if len(matches) != 1:
            raise ValueError(f"Unknown or ambiguous lab: {value}")
        selected.update(matches)
    return sorted(selected)


def source_identity(root, courses):
    result = {}
    # The shared runtime catalog/loader also governs copied course activation.
    for p in sorted((root / "tools/course_bootstrap").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            if p.is_symlink():
                raise ValueError("Executable source must not use symlinks")
            result[str(p.relative_to(root))] = digest(p)
    for course in sorted(set(courses)):
        base = root / course
        for section in ("labs", "tools", "slurm", "env", "reference"):
            for p in sorted((base / section).rglob("*")):
                if not p.is_file() or any(
                    x in ("__pycache__", "lab-results") for x in p.parts
                ):
                    continue
                if p.is_symlink():
                    raise ValueError("Executable source must not use symlinks")
                if p.suffix in (".pyc", ".html", ".zip"):
                    continue
                result[str(p.relative_to(root))] = digest(p)
        for name in (
            "CMakeLists.txt",
            "requirements.txt",
            "requirements-serving.txt",
            "requirements-mechanics.txt",
        ):
            p = base / name
            if p.is_file():
                result[str(p.relative_to(root))] = digest(p)
    return result


def preparation(root, course, stages):
    """Resolve actual jobs, including optional recipe variants, without effects."""
    definitions = read(root / "tools/course_bootstrap/catalog.json")
    bindings = read(root / course / "reference/runtime.json")
    if bindings.get("course") != course or bindings.get("schema") != "course-runtime-bindings/v1":
        raise ValueError("Invalid course runtime bindings")
    result = {}
    for stage in stages:
        if stage["kind"] not in {"execute", "dependency", "profile"}:
            continue
        argv = stage["argv"]
        jobs = [value for value in argv if value.endswith(".sbatch")]
        if not argv or argv[0] != "sbatch" or len(jobs) != 1:
            raise ValueError("Preparation requires one native launcher per executable stage")
        launcher = Path(jobs[0]).name
        if jobs[0] != "slurm/" + launcher:
            raise ValueError("Preparation requires a course-owned native launcher")
        runtime = bindings["launchers"][launcher]
        spec = definitions["runtimes"][runtime]
        group = spec["group"]
        result[launcher] = {
            "launcher": launcher,
            "runtime": runtime,
            "group": group,
            "script": f"{group}-lab-setup.py",
            "arguments": [] if group == "regular" else ["--launcher", launcher],
            "optional": bool(spec.get("optional")),
        }
    if not result:
        raise ValueError("Campaign unit has no executable preparation selections")
    return [result[name] for name in sorted(result)]


def expand(argv, profile, variables, *, unresolved=False):
    def replace(match):
        name = match[1] or match[2]
        if name not in variables:
            if unresolved:
                return "${" + name + "}"
            raise ValueError(f"Missing prepared prerequisite: {name}")
        value = str(variables[name])
        if not value or "\x00" in value or "\n" in value:
            raise ValueError(f"Invalid prerequisite: {name}")
        return value

    result = [VARIABLE.sub(replace, s) for s in argv]
    if "--workload" in result:
        result[result.index("--workload") + 1] = profile
    return result


def actions(recipe, profile, variables):
    r = copy.deepcopy(recipe)
    variables = {**variables, **r["profiles"][profile]}
    stages = []
    for item in r["dependencies"]:
        stages.append({**item, "id": "dependency-" + item["id"], "kind": "dependency"})
    for repeat in range(r["repetitions"]):
        variants = r["variants"] if repeat % 2 == 0 else list(reversed(r["variants"]))
        for item in variants:
            stages.append(
                {
                    "id": f"{item['id']}-trial{repeat + 1}",
                    "kind": "execute",
                    "variant": item["id"],
                    "argv": item["argv"],
                    "environment": item.get("environment", {}),
                }
            )
    for capture in r["profiling_runs"]:
        stages.append({**capture, "kind": "profile"})
    for name in ("verify", "collect", "browser", "export"):
        stages.append({"id": name, "kind": name})
    for stage in stages:
        environment = stage.get("environment", {})
        if not isinstance(environment, dict) or any(
            name != "SBATCH_MEM_PER_NODE"
            or not isinstance(value, str)
            or not re.fullmatch(r"[1-9][0-9]*", value)
            for name, value in environment.items()
        ):
            raise ValueError(
                "Recipe stage environment accepts only positive decimal "
                "SBATCH_MEM_PER_NODE strings in MiB"
            )
        if "argv" in stage:
            stage["argv"] = expand(stage["argv"], profile, variables, unresolved=True)
        stage["environment"] = {
            **variables,
            **environment,
            "COURSE_WORKLOAD": profile,
        }
    ids = [s["id"] for s in stages]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate recipe stage IDs")
    return stages


def freeze(root, selected, recipes, profiles, variables, overrides=None):
    source = source_identity(root, [recipes[k]["course"] for k in selected])
    units = []
    for key in selected:
        for profile in profiles:
            recipe = recipes[key]
            config = overrides or {}
            effective = {
                **variables,
                **config.get("course_variables", {}).get(recipe["course"], {}),
                **config.get("lab_variables", {}).get(key, {}),
            }
            stages = actions(recipe, profile, effective)
            units.append(
                {
                    "key": key,
                    "course": recipe["course"],
                    "lab": recipe["lab"],
                    "profile": profile,
                    "recipe": recipe,
                    "stages": stages,
                    "preparation": preparation(root, recipe["course"], stages),
                }
            )
    return {
        "schema": "run-labs-plan/v1",
        "execution_contract": "native-jobs/v2",
        "source": source,
        "source_sha256": canonical(source),
        "recipes_sha256": digest(RECIPE_FILE),
        "units": units,
    }
