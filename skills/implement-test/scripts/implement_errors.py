"""Shared lifecycle errors for imported helpers and the CLI entry point."""


class LifecycleError(RuntimeError):
    status = "FAIL"


class OwnershipBlockedError(LifecycleError):
    status = "OWNERSHIP_BLOCKED"


class CapabilityUnavailableError(LifecycleError):
    status = "PARTIAL"
