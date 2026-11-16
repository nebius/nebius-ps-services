"""Canonical health observations shared by both workflow coordinators.

Age and missing telemetry request diagnosis; they never prove task failure.
"""


def health_policy(profile="standard"):
    if profile not in {"standard", "integration"}:
        raise ValueError("unknown worker health profile")
    return {"schema": "workflow/worker-health-policy-v1", "mode": "progress-aware",
            "heartbeat_interval_seconds": 30, "startup_observation_seconds": 300,
            "heartbeat_stale_observation_seconds": 240,
            "read_only_observation_seconds": 420 if profile == "integration" else 300,
            "periodic_observation_seconds": 3600, "duration_expiry": "disabled"}


RETIRED_FIELDS = frozenset({"heartbeat_seconds", "start_seconds", "stall_seconds", "max_seconds",
                            "read_only_warning_seconds", "read_only_seconds"})
