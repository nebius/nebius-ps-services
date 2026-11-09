"""Package failures retain the first guarded step without leaking exception text."""

from types import SimpleNamespace

import pytest
from paramiko import AuthenticationException, SSHException

from nebius_vpngw import cli
from nebius_vpngw.deploy import (
    checkpoint_conversion,
    ordinary_apply,
    ordinary_handoff,
    vm_ha_package,
)
from nebius_vpngw.deploy.apply_diagnostics import first_failure
from nebius_vpngw.deploy.apply_result import ApplyReport
from nebius_vpngw.deploy.ssh_push import SSHPush

STEPS = (
    "inspect-predecessor",
    "checkpoint-admission",
    "stage-payload",
    "guarded-install",
    "checkpoint-publication",
)


@pytest.mark.parametrize("failed_step", STEPS)
@pytest.mark.parametrize("kind", [RuntimeError, ValueError, TimeoutError])
def test_real_package_path_preserves_first_step_and_stops_later_effects(
    monkeypatch, failed_step, kind
):
    observed = []
    instance = SimpleNamespace(instance_index=0, hostname="gateway")
    plan = SimpleNamespace(observation={"boot_id": "boot"}, manifest={})
    artifact = SimpleNamespace(sha256="a" * 64, capabilities=[])
    sensitive = "secret-token remote-output private.example"

    def step(name, result=None):
        observed.append(name)
        if name == failed_step:
            raise kind(sensitive)
        return result

    monkeypatch.setattr(
        ordinary_handoff, "observe_guarded", lambda *_a, **_k: {"state": "NOT_SUBMITTED"}
    )
    monkeypatch.setattr(vm_ha_package, "package_predecessor", lambda value: value)
    monkeypatch.setattr(
        ordinary_apply,
        "remote",
        lambda *_a, **_k: step("inspect-predecessor", {"observation": plan.observation}),
    )
    monkeypatch.setattr(
        checkpoint_conversion,
        "guard_package",
        lambda *_a, publish, **_k: step(
            "checkpoint-publication" if publish else "checkpoint-admission"
        ),
    )
    monkeypatch.setattr(vm_ha_package, "stage", lambda *_a, **_k: step("stage-payload"))
    monkeypatch.setattr(
        ordinary_handoff,
        "prepare_guarded",
        lambda *_a, **_k: step("guarded-install", {"capabilities": []}),
    )
    worker = SSHPush()
    monkeypatch.setattr(
        worker,
        "ensure_vm_ha_agent_package",
        lambda target, inst, local, **kw: worker._ensure_vm_ha_planned_package(
            target, inst, local, dependency_plan=plan, handoff=None, **kw
        ),
    )
    with pytest.raises(cli._VMHAAgentPackagePreparationFailed) as caught:
        cli._prepare_vm_ha_agent_package(worker, "target", instance, {}, artifact=artifact)
    assert observed == list(STEPS[: STEPS.index(failed_step) + 1])
    failure = first_failure(caught.value)
    assert failure is not None
    assert failure.phase == "prepare-packages"
    assert failure.predicate == failed_step + "-failed"
    assert failure.reasons == (
        {
            RuntimeError: "cause-runtime",
            ValueError: "cause-invalid-data",
            TimeoutError: "cause-timeout",
        }[kind],
    )
    report = ApplyReport(started=True)
    report.failed(caught.value)
    assert report.mapping()["failure"] == failure.mapping()
    assert failed_step in str(caught.value)
    assert sensitive not in str(caught.value) + str(report.mapping())


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (PermissionError("private"), "permission"),
        (AuthenticationException("private"), "authentication"),
        (SSHException("private"), "ssh-transport"),
        (EOFError("private"), "connection-closed"),
        (OSError("private"), "io"),
        (KeyError("private"), "unclassified"),
    ],
)
def test_nested_package_boundary_keeps_first_safe_failure(error, reason):
    with (
        pytest.raises(vm_ha_package.VMHAAgentPackageError) as caught,
        vm_ha_package.package_step("package-admission"),
        vm_ha_package.package_step("guarded-install"),
    ):
        raise error
    assert caught.value.__cause__ is error
    failure = first_failure(caught.value)
    assert failure is not None
    assert failure.predicate == "guarded-install-failed"
    assert failure.reasons == ("cause-" + reason,)
    assert "private" not in str(caught.value)


def test_handoff_package_error_retains_phase_and_safe_product_reason():
    handoff = SimpleNamespace(
        package_receipt=None,
        prepare_package=lambda _plan: (_ for _ in ()).throw(
            vm_ha_package.VMHAAgentPackageError("HA handoff package receipt is unverified")
        ),
    )
    with pytest.raises(vm_ha_package.VMHAAgentPackageError) as caught:
        SSHPush()._ensure_vm_ha_planned_package(
            "target",
            object(),
            {},
            artifact=SimpleNamespace(sha256="a" * 64),
            dependency_plan=object(),
            handoff=handoff,
        )
    assert first_failure(caught.value).predicate == "handoff-package-failed"
    assert "HA handoff package receipt is unverified" in str(caught.value)


def test_package_diagnostics_do_not_capture_process_cancellation():
    stop = KeyboardInterrupt()
    with (
        pytest.raises(KeyboardInterrupt) as caught,
        vm_ha_package.package_step("guarded-install"),
    ):
        raise stop
    assert caught.value is stop and first_failure(stop) is None


def test_package_diagnostic_steps_are_closed():
    with (
        pytest.raises(ValueError, match="invalid package diagnostic step"),
        vm_ha_package.package_step("private.example"),
    ):
        pytest.fail("invalid diagnostic step entered package work")
