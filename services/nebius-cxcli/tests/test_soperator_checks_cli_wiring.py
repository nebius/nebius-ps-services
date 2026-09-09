from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest

from nebius_cxcli import cli


def production_nodes():
    return tuple(ast.walk(ast.parse(inspect.getsource(cli._apply_rendered_flux))))


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


@pytest.mark.parametrize("installing", [False, True])
def test_reservation_handoff_uses_exact_install_or_upgrade_owner(installing):
    calls = []
    checks = SimpleNamespace(
        operation_id="op",
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
    prefix = "install" if installing else "upgrade"
    assert calls == [
        (prefix + "-release", {"operation": "op"}),
        (prefix + "-verify", {"operation": "op"}),
    ]
    if not installing:
        environment["verify_checks_reservation_released"] = None
        with pytest.raises(RuntimeError, match="scheduling owner"):
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
