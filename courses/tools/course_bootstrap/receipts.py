"""Revalidate committed components without running their installation recipes."""

import sys

from . import catalog as definitions
from .state import digest


def completed(installer, name):
    spec = installer.catalog["components"][name]
    inputs = definitions.component_inputs(
        installer.catalog, installer.root, installer.courses, name
    )
    inputs["python"] = list(sys.version_info[:2])
    inputs["dependencies"] = {
        key: installer.records[key] for key in spec.get("depends", [])
    }
    record = installer.state.completed(name, digest(inputs))
    if record and installer.check_installed(spec, record):
        return record
    return None
