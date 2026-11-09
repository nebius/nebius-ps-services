#!/usr/bin/env python3
"""Static selected-project profile and suite discovery. Never import project code."""

import argparse
import json
from pathlib import Path
import sys

from tsc_support.contracts import (
    OPERATIONS,
    PROFILE,
    identifier,
    list_value,
    object_value,
    read_json,
    require,
    string,
)


def project_path(project, value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else project / path


def discover(project=None, profile=None):
    root = Path(project or Path.cwd()).expanduser().resolve(strict=True)
    require(root.is_dir(), "project must be a directory")
    selected = (
        project_path(root, profile)
        if profile is not None
        else root / "container-tests.json"
    )
    if not selected.exists() and not selected.is_symlink() and profile is None:
        return root, None
    document = read_json(
        selected
    )  # Invalid, unreadable or dangling profiles never fall back.
    object_value(document, "profile")
    require(document.get("schema") == PROFILE, "unsupported profile schema")
    validate_catalog(document)
    return root, document


def validate_catalog(profile):
    catalog = object_value(profile.get("suites", {}), "suites")
    for name, suite in catalog.items():
        identifier(name, "suite")
        require(name != "auto", "auto is a reserved selector")
        object_value(suite, "suite")
        identifier(suite.get("kind"), "suite category")
        string(suite.get("purpose"), "suite purpose")
        string(suite.get("origin"), "suite origin")
        operations = list_value(suite.get("operations"), "suite operations")
        require(
            bool(operations)
            and len(set(operations)) == len(operations)
            and set(operations) <= set(OPERATIONS),
            "invalid suite operations",
        )
        targets = list_value(suite.get("test_targets", []), "suite test targets")
        require(
            len(set(targets)) == len(targets)
            and set(targets) <= {"source", "installed"},
            "invalid suite test targets",
        )
        require(
            bool(targets) if "test" in operations else not targets,
            "test suite requires targets",
        )
    defaults = object_value(profile.get("default_suites", {}), "default suites")
    require(
        set(defaults) <= {"source", "installed", "lifecycle"},
        "unknown suite default target",
    )
    for target, names in defaults.items():
        list_value(names, "default suite names")
        require(len(set(names)) == len(names), "duplicate default suite")
        for name in names:
            require(name in catalog, "unknown default suite")
            suite = catalog[name]
            require(
                target in suite.get("test_targets", [])
                if target != "lifecycle"
                else bool(set(suite["operations"]) - {"test"}),
                "incompatible default suite",
            )
    return catalog


def select_suites(profile, selected, operations, target):
    catalog = validate_catalog(profile)
    names = ["auto"] if selected is None else selected
    list_value(names, "selected suites")
    require(bool(names), "empty suite selector; use auto")
    require(len(set(names)) == len(names), "duplicate suite selector")
    if "auto" in names:
        require(names == ["auto"], "auto cannot combine with named suites")
        defaults = profile.get("default_suites", {})
        groups = ([target] if "test" in operations else []) + (
            ["lifecycle"] if set(operations) - {"test"} else []
        )
        names = list(
            dict.fromkeys(n for group in groups for n in defaults.get(group, []))
        )
        require(
            target != "source" or bool(names),
            "source suite selection unresolved; inspect documented default or choose a suite",
        )
    for name in names:
        identifier(name, "suite")
        require(name in catalog, "unknown suite")
        suite = catalog[name]
        require(
            set(operations) <= set(suite["operations"]),
            "suite must apply to every selected operation",
        )
        require(
            "test" not in operations or target in suite["test_targets"],
            "suite target mismatch",
        )
    return names


def list_suites(profile, target=None):
    catalog = validate_catalog(profile)
    defaults = profile.get("default_suites", {})
    return [
        dict(
            name=name,
            kind=suite["kind"],
            purpose=suite["purpose"],
            operations=suite["operations"],
            test_targets=suite.get("test_targets", []),
            origin=suite["origin"],
            default_for=[k for k, v in defaults.items() if name in v],
        )
        for name, suite in catalog.items()
        if target is None or target in suite.get("test_targets", [])
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", nargs="?")
    parser.add_argument("--profile")
    parser.add_argument("--list-suites", action="store_true", required=True)
    parser.add_argument("--test-target", choices=("source", "installed"))
    args = parser.parse_args()
    try:
        _, profile = discover(args.project, args.profile)
        result = {
            "status": "PROFILE_FOUND" if profile else "NEEDS_PROJECT_INSPECTION",
            "suites": list_suites(profile, args.test_target) if profile else [],
        }
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(
            str(exc)
            if isinstance(exc, ValueError)
            else "Profile unavailable or malformed",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
