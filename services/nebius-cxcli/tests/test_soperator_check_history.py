"""Optional readiness exemptions retain source-bound native script authority."""

from __future__ import annotations

import copy

import pytest

from nebius_cxcli.soperator_check_history import terminal_native_check_pods
from test_soperator_checks_execution import (
    Cluster,
    policy,  # noqa: F401 - pytest fixture
)
from test_soperator_flux_readiness import _retained_check_history_responses


@pytest.mark.parametrize("script_state", ["frozen", "changed", "missing"])
def test_pending_slurm_check_requires_frozen_script_bytes(policy, script_state):  # noqa: F811
    native = Cluster(policy)
    responses = _retained_check_history_responses()
    check = responses["activechecks.slurm.nebius.ai"]["items"][0]
    check["metadata"]["name"] = "gpu-fryer"
    check["spec"] = copy.deepcopy(native.checks["gpu-fryer"]["spec"])
    check["status"] = {}
    cron = responses["cronjobs.batch"]["items"][0]
    cron["metadata"]["name"] = "gpu-fryer"
    cron["metadata"]["ownerReferences"][0]["name"] = "cluster"
    cron["spec"] = copy.deepcopy(native.crons["gpu-fryer"]["spec"])
    job = responses["jobs.batch"]["items"][0]
    job["metadata"]["ownerReferences"][0]["name"] = "gpu-fryer"
    job["spec"] = copy.deepcopy(cron["spec"]["jobTemplate"]["spec"])
    job["status"] = {"active": 1}
    pod = responses["pods"]["items"][-1]
    pod["spec"] = copy.deepcopy(job["spec"]["template"]["spec"])
    pod["status"] = {"phase": "Pending"}
    cluster = responses["slurmclusters.slurm.nebius.ai"]
    cluster["metadata"]["name"] = "cluster"
    script = copy.deepcopy(native.configmaps["sbatch-script-gpu-fryer"])
    script["metadata"].update(name="sbatch-script-gpu-fryer", namespace="soperator")
    if script_state == "changed":
        script["data"]["sbatch.sh"] = "exit 0\n"
    responses["configmaps"] = {"items": [] if script_state == "missing" else [script]}

    exempt = terminal_native_check_pods(
        [pod],
        cluster=cluster,
        releases=responses["helmreleases.helm.toolkit.fluxcd.io"]["items"],
        read=responses.get,
        pending_checks={"gpu-fryer": policy.execution_specs["gpu-fryer"]},
        pending_check_uids={"gpu-fryer": check["metadata"]["uid"]},
    )
    assert exempt == ({pod["metadata"]["uid"]} if script_state == "frozen" else set())
