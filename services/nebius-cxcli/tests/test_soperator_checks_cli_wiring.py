from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli
from source_inspection import function_source


def production_nodes():
    return tuple(ast.walk(ast.parse(function_source(cli._apply_rendered_flux))))


def closure(name, environment):
    node = next(n for n in production_nodes() if isinstance(n, ast.FunctionDef) and n.name == name)
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, "<production-checks-wiring>", "exec"), environment)
    return environment[name]


@pytest.mark.parametrize("installing", [False, True])
def test_strategy_selects_complete_upgrade_preimages_or_fresh_install(installing):
    checks = SimpleNamespace(state={})
    transfer = (object(),)
    reads = []

    def preimages():
        reads.append("upgrade-preimage")
        if installing:
            raise KeyError("partitions")
        return transfer

    environment = {
        **vars(cli),
        "checks_execution": checks,
        "reconcile_strategy": SimpleNamespace(
            strategy=cli.SoperatorStrategy.INSTALL if installing else cli.SoperatorStrategy.IN_PLACE
        ),
        "checks_partition_preimages": preimages,
    }
    assignment = next(
        n
        for n in production_nodes()
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Attribute) and ast.unparse(t) == "checks_execution.lifecycle"
            for t in n.targets
        )
    )
    module = ast.fix_missing_locations(ast.Module(body=[assignment], type_ignores=[]))
    exec(compile(module, "<production-checks-binding>", "exec"), environment)
    assert checks.lifecycle.admission.preimages() == (() if installing else transfer)
    assert reads == ([] if installing else ["upgrade-preimage"])


@pytest.mark.parametrize("installing,install_owned", [(False, False), (True, False), (True, True)])
def test_reservation_handoff_uses_recorded_owner(installing, install_owned):
    calls = []
    checks = SimpleNamespace(
        operation_id="op",
        state={"installReservationIntent": True} if install_owned else {},
        release_install_reservation=lambda proof: calls.append(("install-release", proof)),
        verify_install_reservation_released=lambda proof: calls.append(("install-verify", proof)),
        require_lifecycle=lambda: SimpleNamespace(admission=object()),
    )
    environment = {
        **vars(cli),
        "checks_execution": checks,
        "parent_owns_checks": False,
        "reconcile_strategy": SimpleNamespace(
            strategy=cli.SoperatorStrategy.INSTALL if installing else cli.SoperatorStrategy.IN_PLACE
        ),
        "release_checks_reservation": lambda proof: calls.append(("upgrade-release", proof)),
        "verify_checks_reservation_released": lambda proof: calls.append(("upgrade-verify", proof)),
    }
    handoff = closure("_checks_handoff", environment)()
    handoff.release_owned({"operation": "op"})
    handoff.verify_owned({"operation": "op"})
    prefix = "install" if install_owned else "upgrade"
    assert calls == [
        (prefix + "-release", {"operation": "op"}),
        (prefix + "-verify", {"operation": "op"}),
    ]
    if not install_owned:
        environment["verify_checks_reservation_released"] = None
        with pytest.raises(RuntimeError, match="scheduling owner"):
            environment["_checks_handoff"]()
    else:
        environment["reconcile_strategy"].strategy = cli.SoperatorStrategy.IN_PLACE
        with pytest.raises(RuntimeError, match="ownership conflicts with strategy"):
            environment["_checks_handoff"]()


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("failure", [False, True])
def test_interrupted_release_preserves_classifier_and_live_verifier(held, failure):
    events = []

    def recovery():
        events.append("recover")
        return {"status": "cached-release"}

    def verify():
        events.append("verify-live")
        if failure:
            raise RuntimeError("live release not proven")

    lifecycle = SimpleNamespace(
        authorize_admission=lambda: events.append("authorize"),
        finish=lambda: events.append("finish"),
    )
    checks = SimpleNamespace(
        verify_acceptance=lambda: events.append("acceptance"),
        require_lifecycle=lambda: lifecycle,
    )
    environment = {
        **vars(cli),
        "_set_phase": lambda _: None,
        "checks_execution": checks,
        "parent_owns_checks": False,
        "_checks_handoff": lambda: SimpleNamespace(verify=lambda: events.append("handoff")),
        "recover_requeued_jobs": recovery,
        "recover_held_jobs": recovery,
        "verify_requeued_jobs": verify,
        "verify_held_jobs": verify,
        "release_requeued_jobs": lambda: pytest.fail("bypassed recovery classifier"),
        "release_held_jobs": lambda: pytest.fail("bypassed recovery classifier"),
    }
    callback = closure("_release_held_jobs" if held else "_release_requeued_jobs", environment)
    if failure:
        with pytest.raises(RuntimeError, match="live release not proven"):
            callback(recover=True)
    else:
        assert callback(recover=True) == {"status": "cached-release"}
    prefix = [] if held else ["acceptance", "handoff", "authorize"]
    assert events == prefix + ["recover", "verify-live"] + (
        ["finish"] if held and not failure else []
    )


@pytest.mark.parametrize("failure", [False, True])
def test_install_restoration_callback_requires_current_passive_proof(failure):
    events = []

    def passive():
        events.append("passive")
        if failure:
            raise RuntimeError("passive restoration failed")

    environment = {
        **vars(cli),
        "checks_policy": SimpleNamespace(sha256="policy"),
        "parent_owns_checks": False,
        "checks_execution": SimpleNamespace(
            state={"phase": "restored"},
            readiness_exemptions=lambda: {},
            require_lifecycle=lambda: SimpleNamespace(
                passive=SimpleNamespace(verify_restored=passive)
            ),
        ),
        "SoperatorCampaignChecks": SimpleNamespace(_policy_restored=lambda _: True),
        "_checks_handoff": lambda: SimpleNamespace(verify=lambda: events.append("handoff")),
    }
    callback = closure("_verify_checks_restored", environment)
    if failure:
        with pytest.raises(RuntimeError, match="passive restoration failed"):
            callback()
    else:
        assert callback() == {"status": "restored", "policy": "policy"}
    assert events == ["handoff", "passive"]
    nodes = production_nodes()
    assert any(
        isinstance(n, ast.keyword)
        and n.arg == "verify_checks_restored"
        and ast.unparse(n.value) == "_verify_checks_restored"
        for n in nodes
    )
    assert any(
        isinstance(n, ast.Dict)
        and any(
            isinstance(k, ast.Constant)
            and k.value == "restore-steady-check-policy"
            and "_verify_checks_restored()" in ast.unparse(v)
            for k, v in zip(n.keys, n.values, strict=True)
        )
        for n in nodes
    )


@pytest.mark.parametrize("failure", [None, "passive", "active"])
def test_final_ready_apply_requires_active_and_passive_restoration(failure):
    events = []

    def passive():
        events.append("passive")
        if failure == "passive":
            raise RuntimeError("passive restoration failed")

    def active(_checks):
        events.append("active")
        return failure != "active"

    environment = {
        **vars(cli),
        "_set_phase": lambda _: None,
        "reconcile_strategy": SimpleNamespace(strategy=cli.SoperatorStrategy.INSTALL),
        "_main_workload_authority": lambda: None,
        "wait_for_soperator_release_graph": lambda *_args, **_kwargs: events.append("graph"),
        "_worker_registration": lambda: events.append("slurm"),
        "paths": None,
        "extra_env": {},
        "kube_context": None,
        "soperator_values": {},
        "_update_progress_detail": lambda _: None,
        "parent_owns_checks": False,
        "checks_policy": SimpleNamespace(sha256="policy"),
        "SoperatorCampaignChecks": SimpleNamespace(_policy_restored=active),
        "_checks_handoff": lambda: SimpleNamespace(verify=lambda: events.append("handoff")),
        "checks_execution": SimpleNamespace(
            state={"phase": "restored"},
            require_lifecycle=lambda: SimpleNamespace(
                passive=SimpleNamespace(verify_restored=passive)
            ),
            readiness_exemptions=lambda: {},
        ),
    }
    closure("_verify_checks_restored", environment)
    callback = closure("_wait_complete_product", environment)
    if failure:
        with pytest.raises(RuntimeError, match="restoration"):
            callback()
    else:
        callback()
    assert events == ["graph", "slurm", "active"] + (
        [] if failure == "active" else ["handoff", "passive"]
    )


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("_fast_smoke", {"verify_only": False}),
        ("_fast_smoke", {"verify_only": True}),
        ("_fast_admission", {}),
    ],
)
def test_fast_child_defers_smoke_to_parent_scheduling_owner(name, kwargs):
    def forbidden(*args, **kwargs):
        pytest.fail("child submitted or observed ordinary smoke before parent restored scheduling")

    environment = {
        **vars(cli),
        "parent_owns_checks": True,
        "operation_authority": lambda: None,
        "checks_policy": SimpleNamespace(sha256="policy"),
        "scheduling_evidence": {"mode": "parent-campaign", "campaignIntentSha256": "parent"},
        "_fast_admission": forbidden,
        "_fast_run": forbidden,
        "paths": SimpleNamespace(reports_dir=None),
        "operation_spec_sha256": "child",
    }
    assert closure(name, environment)(**kwargs) == {
        "status": "delegated",
        "owner": "parent-campaign",
        "generation": "parent",
    }


def test_fast_install_wires_exact_retirement_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "nebius_cxcli.soperator_fast_readiness.verify_fast_readiness",
        lambda **kwargs: calls.append(kwargs) or {"status": "passed"},
    )
    environment = {
        **vars(cli),
        "parent_owns_checks": False,
        "operation_authority": lambda: None,
        "checks_policy": object(),
        "_fast_admission": lambda: {"groups": []},
        "_fast_run": lambda *_: "",
        "paths": SimpleNamespace(reports_dir=None),
        "operation_spec_sha256": "generation",
    }
    assert closure("_fast_smoke", environment)()["status"] == "passed"
    assert calls[0]["confirm_retirement"] is cli._confirm_explicit_action


@pytest.mark.parametrize("state", ["IDLE", "IDLE+CLOUD+DRAIN+MAINTENANCE+RESERVED"])
def test_fast_registration_and_final_admission_are_separate_gates(state):
    from test_soperator_fast_readiness import VALUES

    nodes = f"NodeName=worker-0 State={state} SlurmdStartTime=2026-01-01"
    environment = {
        **vars(cli),
        "fast_deploy": True,
        "parent_owns_checks": False,
        "operation_authority": lambda: None,
        "soperator_values": VALUES,
        "_fast_workers": lambda: {"worker-0": True},
        "_fast_run": lambda cmd: (
            nodes if "nodes" in cmd else ("PartitionName=main State=UP Nodes=worker-0")
        ),
    }
    assert closure("_worker_registration", environment)() == {
        "status": "registered",
        "workers": ["worker-0"],
    }
    admission = closure("_fast_admission", environment)
    if "MAINTENANCE" in state:
        with pytest.raises(RuntimeError, match="schedulable"):
            admission()
    else:
        assert admission()["status"] == "ordinary-user-admission-ready"
