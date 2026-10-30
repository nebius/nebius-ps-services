"""Validate a bounded result obtained from an independently pinned probe host."""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .nat_gateway import fingerprint

RESULT_LIMIT = 4096


def accepted_report(raw: bytes, probe: dict[str, Any]) -> dict[str, Any]:
    if not raw or len(raw) > RESULT_LIMIT:
        raise RuntimeError("NAT probe result is empty or oversized")
    try:
        report = json.loads(raw)
        valid = (
            report["schema"] == 1
            and report["run_id"] == probe["config"]["run_id"]
            and report["activation"] == probe["config"]["activation"]
            and report["config_sha256"] == fingerprint(probe["config"])
            and report["program_sha256"] == probe["config"]["program_sha256"]
            and report["endpoint_ip"] == probe["config"]["endpoint_ip"]
            and probe["started_at"] - 5
            <= report["boot_time"]
            <= report["finished_at"]
            <= time.time() + 5
            and re.fullmatch(r"[0-9a-f-]{36}", report["boot_id"]) is not None
            and isinstance(report["ok"], bool)
        )
        consistent = report["ok"] == (report["observed_ip"] == probe["config"]["expected_ip"])
    except (UnicodeError, ValueError, TypeError, KeyError):
        raise RuntimeError("Invalid NAT probe result") from None
    if not valid:
        raise RuntimeError("Stale or mismatched NAT probe result")
    if not consistent:
        raise RuntimeError("Inconsistent NAT probe result")
    return report
