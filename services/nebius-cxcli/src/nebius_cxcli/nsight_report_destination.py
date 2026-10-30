"""Reconcile the course report contract through the accepted active login jail."""

import json
from pathlib import Path

from .cluster_config import require_soperator_row
from .nsight import soperator_report_binding
from .nsight_runtime import NsightKubernetes, prepare_nsight_viewers


def reconcile_report_destination(*, env, config, target_ref, generation, identity, receipt, fence):
    """Reobserve both viewers' storage and publish the accepted producer contract."""
    import base64

    from .deployment_jail_state import jail_values

    fence()
    bindings = prepare_nsight_viewers(config, extra_env=env, target_ref=target_ref)
    if len(bindings) != 2 or bindings[0] != bindings[1]:
        raise RuntimeError("Nsight viewers must share one independently verified report binding")
    settings = next(row["profiling"] for row in [require_soperator_row(config, target_ref)])
    binding = soperator_report_binding(
        jail_values(config, target_ref),
        namespace="soperator",
        reports_path=settings["reports_path"],
    )
    if bindings[0]["pvcName"] != binding.claim or bindings[0]["namespace"] != binding.namespace:
        raise RuntimeError("Nsight viewer claim differs from the accepted producer mount")
    request = {
        "verification": receipt["verification"],
        "installation": {
            "generation": generation,
            "target": target_ref,
            "kubernetes_uid": identity["kubernetes_uid"],
        },
        "storage": bindings[0],
        "producer_path": binding.jail_path,
        "mount_point": binding.jail_path[: -len("/" + binding.subpath)],
    }
    source = Path(__file__).with_name("nsight_reports.py").read_text()
    fence()
    output = NsightKubernetes(env).run(
        [
            "-n",
            "soperator",
            "exec",
            "login-0",
            "--",
            "chroot",
            "/mnt/jail",
            "/usr/bin/timeout",
            "--signal=TERM",
            "--kill-after=5s",
            "45s",
            "/usr/bin/python3",
            "-c",
            source,
            base64.b64encode(json.dumps(request).encode()).decode(),
        ],
        timeout=60,
    )
    fence()
    result = json.loads(output)
    if (
        result.get("schema") == "nebius-cxcli.nsight-reports/v1"
        and result.get("status") == "failed"
    ):
        raise RuntimeError(
            f"Nsight report destination {binding.jail_path}: {result.get('reason', 'verification failed')}. Rerun profiling install after operator remediation."
        )
    if result.get("status") != "ready" or result.get("schema") != "nebius-cxcli.nsight-reports/v1":
        raise RuntimeError(
            "Nsight report destination acknowledgement is unconfirmed; rerun profiling install"
        )
