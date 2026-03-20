"""Early native gRPC/absl log quieting for CLI subprocess-heavy workflows."""

from __future__ import annotations

import os

QUIET_NATIVE_LOG_ENV_DEFAULTS: dict[str, str] = {
    "GRPC_VERBOSITY": "NONE",
    "GLOG_minloglevel": "3",
    "ABSL_LOG_SEVERITY_LEVEL": "3",
    "GRPC_ENABLE_FORK_SUPPORT": "0",
}

for env_name, env_value in QUIET_NATIVE_LOG_ENV_DEFAULTS.items():
    os.environ.setdefault(env_name, env_value)
