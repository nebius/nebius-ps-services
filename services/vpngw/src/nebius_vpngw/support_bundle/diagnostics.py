"""Closed acquisition diagnostics. Exception messages are never console output."""

from __future__ import annotations

import errno
from typing import Any

STAGES = frozenset(
    {
        "artifact",
        "discovery",
        "trust",
        "identity",
        "connect",
        "sudo",
        "startup",
        "collect",
        "status",
        "archive",
    }
)
GUIDANCE = {
    "artifact-unavailable": "Inspect installed artifact provenance or select the exact existing wheel with VPNGW_AGENT_WHEEL.",
    "target-unavailable": "Check gateway discovery and configured management addresses for the selected project.",
    "trust-format": "Select an already verified known-hosts file with VPNGW_SSH_KNOWN_HOSTS_FILE.",
    "trust-unavailable": "Check the existing exact gateway host pins and selected deployment scope.",
    "host-key-mismatch": "Verify the gateway identity against trusted host-key evidence before retrying.",
    "identity-unavailable": "Check the configured SSH key paths or select an existing key with --ssh-identity.",
    "authentication-failed": "Check the selected SSH username and existing authorized identity.",
    "connection-refused": "Check the target address, SSH service and network reachability.",
    "network-unreachable": "Check connectivity to the gateway management address.",
    "timeout": "Check the failed stage and target reachability; completed evidence is retained.",
    "sudo-denied": "Use an existing account permitted to run the diagnostic commands with noninteractive sudo.",
    "python-unavailable": "Check that /usr/bin/python3 is available on the gateway.",
    "probe-failed": "Inspect the redacted startup diagnostics and the collected evidence.",
    "invalid-output": "Inspect retained command diagnostics; the probe returned invalid structured output.",
    "source-limit": "The observation reached its byte or record limit; inspect retained coverage.",
    "configuration-unavailable": "Check the supplied configuration and access-field references.",
    "internal-error": "Inspect the collector version and failure stage; report the partial bundle.",
}


class CollectionFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def failure(error: Exception, stage: str, attempted: bool) -> dict[str, Any]:
    name = type(error).__name__
    code = "internal-error"
    if isinstance(error, CollectionFailure):
        code = error.code
    elif name == "BadHostKeyException":
        code = "host-key-mismatch"
    elif name in {"AuthenticationException", "UnableToAuthenticate", "PasswordRequiredException"}:
        code = "authentication-failed"
    elif isinstance(error, TimeoutError):
        code = "timeout"
    elif stage == "trust":
        code = "trust-format" if "schema" in str(error).lower() else "trust-unavailable"
    elif stage == "identity":
        code = "identity-unavailable"
    elif stage == "discovery":
        code = "configuration-unavailable"
    elif stage == "artifact":
        code = "artifact-unavailable"
    elif name == "NoValidConnectionsError":
        errors = getattr(error, "errors", {}).values()
        code = (
            "connection-refused"
            if any(getattr(e, "errno", None) == errno.ECONNREFUSED for e in errors)
            else "network-unreachable"
        )
    elif isinstance(error, ConnectionRefusedError):
        code = "connection-refused"
    elif isinstance(error, OSError):
        code = "network-unreachable" if stage == "connect" else "probe-failed"
    elif stage in {"sudo", "startup", "collect"}:
        code = "probe-failed"
    return clean_failure({"stage": stage, "code": code, "connection_attempted": attempted})


def clean_failure(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {
        "stage": value.get("stage") if value.get("stage") in STAGES else "collect",
        "code": value.get("code") if value.get("code") in GUIDANCE else "internal-error",
        "connection_attempted": value.get("connection_attempted")
        if type(value.get("connection_attempted")) is bool
        else None,
    }


def clean_progress(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("stage") not in STAGES:
        return None
    if value.get("state") not in {"started", "completed", "failed"}:
        return None
    result = {"kind": "progress", "stage": value["stage"], "state": value["state"]}
    if "failure" in value:
        result["failure"] = clean_failure(value["failure"])
    return result
