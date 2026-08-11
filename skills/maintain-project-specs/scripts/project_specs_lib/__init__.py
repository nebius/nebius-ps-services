"""Canonical project-spec contracts and lifecycle helpers."""

from .contracts import ProjectSpecError, inspect_project, validate_project

__all__ = ["ProjectSpecError", "inspect_project", "validate_project"]
