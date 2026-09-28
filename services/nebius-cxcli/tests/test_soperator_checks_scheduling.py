"""Initial install waits for controller-created schedules without tolerating drift."""

import pytest

from test_soperator_checks_execution import Cluster, execution
from test_soperator_checks_execution import policy as policy


def test_initial_scheduling_waits_for_missing_cron_without_writes(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    pending = cluster.crons.pop("gpu-fryer")
    messages = []
    runner.emit = messages.append

    def reconcile(seconds):
        cluster.sleep(seconds)
        cluster.crons["gpu-fryer"] = pending

    runner.sleep = reconcile
    runner.wait_for_deferred_scheduling()
    assert cluster.now == 1
    assert any("CronJob/gpu-fryer" in message for message in messages)
    assert not cluster.writes
    runner.verify_deferred_diagnostics()


@pytest.mark.parametrize("field", ["suspend", "schedule", "timeZone", "owner"])
def test_pending_child_does_not_hide_drift_in_another_schedule(tmp_path, policy, field):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    cluster.crons.pop("gpu-fryer")
    name = next(iter(cluster.crons))
    if field == "owner":
        cluster.crons[name]["metadata"]["ownerReferences"][0]["uid"] = "foreign"
    else:
        cluster.crons[name]["spec"][field] = False if field == "suspend" else "changed"
    with pytest.raises(RuntimeError, match=f"CronJob/{name}.*{field}"):
        runner.wait_for_deferred_scheduling()
    assert cluster.now == 0 and not cluster.writes


def test_missing_schedule_times_out_with_resource_and_leaves_maintenance_closed(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    cluster.crons.pop("gpu-fryer")
    with pytest.raises(RuntimeError, match="CronJob/gpu-fryer.*recovery remains available"):
        runner.wait_for_deferred_scheduling()
    assert cluster.now == 2 and not cluster.writes
    with pytest.raises(RuntimeError, match="deferral changed.*CronJob/gpu-fryer"):
        runner.verify_deferred_diagnostics()


def test_initial_wait_stops_on_authority_loss(tmp_path, policy):
    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    cluster.crons.pop("gpu-fryer")

    def authority():
        if cluster.now:
            raise RuntimeError("authority lost")

    runner.authority = authority
    with pytest.raises(RuntimeError, match="authority lost"):
        runner.wait_for_deferred_scheduling()
    assert cluster.now == 1 and not cluster.writes


@pytest.mark.parametrize("spec", [None, {"schedule": "0 * * * *", "suspend": True}])
def test_missing_or_malformed_auxiliary_is_drift_not_controller_pending(tmp_path, policy, spec):
    from dataclasses import replace

    from nebius_cxcli.soperator_checks_scheduling import scheduling_inventory

    policy = replace(policy, auxiliary_pvc="active-jail", auxiliary_spec={"schedule": "0 * * * *"})
    cluster = Cluster(policy)
    if spec is not None:
        cluster.crons["run-extensive-check-on-reservations"] = {
            "metadata": {"uid": "aux", "name": "run-extensive-check-on-reservations"},
            "spec": spec,
        }
    runner = execution(tmp_path, policy, cluster)
    assert scheduling_inventory(runner, deferred=True) is None
    with pytest.raises(
        RuntimeError, match="deferral changed.*CronJob/run-extensive-check-on-reservations"
    ):
        runner.wait_for_deferred_scheduling()
    assert cluster.now == 0 and not cluster.writes


def test_install_readiness_callback_waits_before_strict_restoration_guard(tmp_path, policy):
    """Execute the production nested callback with only its external transports replaced."""
    import ast
    import inspect
    from types import SimpleNamespace

    from nebius_cxcli import cli
    from nebius_cxcli.soperator_strategy import SoperatorStrategy

    cluster = Cluster(policy)
    runner = execution(tmp_path, policy, cluster)
    pending = cluster.crons.pop("gpu-fryer")
    calls = []

    def reconcile(seconds):
        cluster.sleep(seconds)
        calls.append("controller-child")
        cluster.crons["gpu-fryer"] = pending

    runner.sleep = reconcile
    tree = ast.parse(inspect.getsource(cli))
    callback = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_wait_pre_restore_product"
    )
    environment = {
        "_set_phase": lambda _: None,
        "reconcile_strategy": SimpleNamespace(strategy=SoperatorStrategy.INSTALL),
        "SoperatorStrategy": SoperatorStrategy,
        "_main_workload_authority": lambda: None,
        "wait_for_soperator_release_graph": lambda *a, **kw: calls.append("graph") or {},
        "_verify_soperator_upgrade_slurm_worker_registration": lambda **kw: (
            calls.append("slurm") or {}
        ),
        "paths": None,
        "extra_env": {},
        "_update_progress_detail": lambda _: None,
        "soperator_values": {},
        "kube_context": "fixture",
        "checks_execution": runner,
        "parent_owns_checks": False,
        "fast_deploy": False,
        "_worker_registration": lambda: calls.append("slurm") or {},
    }
    exec(
        compile(
            ast.Module(body=[callback], type_ignores=[]), "production-install-readiness", "exec"
        ),
        environment,
    )
    environment["_wait_pre_restore_product"]()
    # This is the exact guard that failed immediately after product readiness.
    runner.verify_deferred_diagnostics()
    assert calls == ["graph", "slurm", "controller-child"]
    assert not cluster.writes
