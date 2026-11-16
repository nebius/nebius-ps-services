"""Explicit bounded static authentication. This module cannot issue or renew credentials."""

from __future__ import annotations

import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path

MODE = "NEBIUS_CXCLI_AUTH_MODE"
FILE = "NEBIUS_CXCLI_STATIC_AUTH_FILE"
CONFLICTS = (
    "NEBIUS_AUTH_CREDENTIALS_FILE",
    "NEBIUS_IAM_TOKEN",
    "NEBIUS_PROFILE",
    "NEBIUS_CONFIG",
    "NEBIUS_SA_ID",
    "NEBIUS_SERVICE_ACCOUNT_ID",
    "NEBIUS_AUTH_PUBLIC_KEY_ID",
    "NEBIUS_AUTH_PRIVATE_KEY_FILE",
    "NEBIUS_AUTH_PRIVATE_KEY_PEM",
    "CXCLI_NEBIUS_DELEGATE_ID",
)


def enabled() -> bool:
    mode = os.environ.get(MODE, "")
    if mode not in {"", "managed", "preconfigured-static"}:
        raise RuntimeError("Unsupported NEBIUS_CXCLI_AUTH_MODE")
    if not mode and os.environ.get(FILE):
        raise RuntimeError("Static authentication file requires explicit preconfigured-static mode")
    return mode == "preconfigured-static"


def reject_managed_operation(operation: str) -> None:
    if enabled():
        raise RuntimeError(f"Preconfigured static mode does not permit {operation}")


@dataclass(frozen=True)
class StaticAuth:
    token: str = field(repr=False)
    service_account_id: str
    project_id: str
    issued_at: int
    expires_at: int
    deadline: int

    def assert_current(self) -> None:
        now = int(time.time())
        if not self.issued_at <= now < self.deadline <= self.expires_at:
            raise RuntimeError("Preconfigured static authentication expired or not yet valid")
        if self.expires_at - self.issued_at > 43200 or self.deadline - self.issued_at > 10800:
            raise RuntimeError("Preconfigured static authentication exceeds its bounded lifetime")


def load(project_id: str | None = None) -> StaticAuth | None:
    if not enabled():
        return None
    if any(os.environ.get(name) for name in CONFLICTS):
        raise RuntimeError("Preconfigured static authentication rejects mixed credential sources")
    path = Path(os.environ.get(FILE, ""))
    if not path.is_absolute():
        raise RuntimeError("Preconfigured static authentication requires a protected absolute file")
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_mode & 0o077
            or before.st_nlink != 1
            or before.st_size > 32768
        ):
            raise ValueError
        raw = os.read(fd, 32769)
        after = os.fstat(fd)
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError
        value = json.loads(raw)
        if (
            set(value)
            != {
                "schema",
                "token",
                "service_account_id",
                "project_id",
                "issued_at",
                "expires_at",
                "deadline",
            }
            or value.pop("schema") != "preconfigured-static/v1"
            or any(type(value[k]) is not int for k in ("issued_at", "expires_at", "deadline"))
            or not isinstance(value["token"], str)
            or not 32 <= len(value["token"]) <= 16384
            or any(c.isspace() for c in value["token"])
            or not isinstance(value["project_id"], str)
            or not value["project_id"].startswith("project-")
            or not isinstance(value["service_account_id"], str)
            or not value["service_account_id"].startswith("serviceaccount-")
        ):
            raise ValueError
        auth = StaticAuth(**value)
    except (OSError, ValueError, TypeError, KeyError):
        raise RuntimeError(
            "Preconfigured static authentication file is invalid or unsafe"
        ) from None
    finally:
        if fd is not None:
            os.close(fd)
    if project_id is not None and auth.project_id != project_id:
        raise RuntimeError("Preconfigured static authentication project mismatch")
    auth.assert_current()
    return auth


def client(auth: StaticAuth, *, endpoint=None, user_agent_prefix="nebius-cxcli"):
    from nebius.sdk import SDK

    if endpoint not in {None, "api.nebius.cloud"} or os.environ.get("NEBIUS_ENDPOINT") not in {
        None,
        "",
        "api.nebius.cloud",
    }:
        raise RuntimeError("Preconfigured static authentication requires the official API endpoint")
    auth.assert_current()
    sdk = SDK(
        credentials=auth.token,
        parent_id=auth.project_id,
        domain="api.nebius.cloud",
        user_agent_prefix=user_agent_prefix,
    )
    try:
        response = sdk.whoami(timeout=20, auth_timeout=20, retries=1).wait()
        metadata = response.service_account_profile.info.metadata
        if metadata.id != auth.service_account_id or metadata.parent_id != auth.project_id:
            raise ValueError
    except Exception:
        sdk.sync_close(timeout=10)
        raise RuntimeError(
            "Preconfigured static authenticated identity mismatch or unavailable"
        ) from None
    return sdk


def admit_cpu_vm_config(config) -> None:
    from .component_instances import component_type_id
    from .runtime_config import to_plain_data

    value = to_plain_data(config)
    rows = value.get("infra", {}).get("components", [])
    active = [row for row in rows if row.get("enabled", False)]
    charts = value.get("apps", {}).get("charts", [])
    if not active or any(c.get("enabled", False) for c in charts):
        raise RuntimeError("Preconfigured static mode supports only bounded CPU VM infrastructure")
    project = value["client_info"]["nebius"]["project_id"]
    for row in active:
        inputs = row.get("inputs", {})
        if (
            component_type_id(row) != "vm"
            or inputs.get("platform") not in {"cpu-d3", "cpu-e2"}
            or inputs.get("parent_id", project) != project
            or inputs.get("service_account_id")
            or inputs.get("gpu_cluster_enabled")
            or inputs.get("gpu_cluster_infiniband_fabric")
            or row.get("access")
            or row.get("depends_on")
            or row.get("bindings")
        ):
            raise RuntimeError(
                "Preconfigured static mode requires CPU VMs without runtime identities or dependencies"
            )
