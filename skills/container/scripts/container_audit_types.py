#!/usr/bin/env python3
"""Shared data types for the container audit helpers."""

from __future__ import annotations

from dataclasses import dataclass


class AuditError(ValueError):
    """Invalid or unsafe audit request."""


@dataclass(frozen=True)
class Finding:
    """A stable machine-readable audit finding."""

    code: str
    severity: str
    message: str
    path: str | None = None
    line: int | None = None
