"""Allowlisted reports and CLI presentation. No cloud access on import."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sdk.runtime import error_code


@dataclass
class Report:
    scope: dict[str, Any]
    data: list[Any] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    mode: str | None = None

    def collect(self, component: str, function: Callable[[], list[Any]]) -> None:
        try:
            self.data.extend(function())
        except Exception as exc:  # noqa: BLE001 - Sanitize errors and retain reconciliation IDs.
            self.errors.append({"component": component, "code": error_code(exc)})

    def emit(self, *, as_json: bool) -> int:
        payload = {
            "scope": self.scope,
            "data": self.data,
            "complete": not self.errors,
            "errors": self.errors,
        }
        if self.mode is not None:
            payload["mode"] = self.mode
        if as_json:
            print(json.dumps(payload, indent=2))
        else:
            print(
                f"Inspection: {'incomplete' if self.errors else 'complete'}; "
                f"{len(self.data)} records"
                + (f"; mode={self.mode}" if self.mode else "")
            )
            for row in self.data[:20]:
                # Nested inventories are available in JSON; screen output remains bounded.
                short = {
                    k: (f"{len(v)} entries" if isinstance(v, (list, dict)) else v)
                    for k, v in row.items()
                }
                print(json.dumps(short, ensure_ascii=True)[:1000])
            if len(self.data) > 20:
                print(
                    "Showing 20 records; use --json for the complete collected inventory."
                )
            for error in self.errors:
                print(f"{error['component']}: {error['code']}")
        return 1 if self.errors else 0
