from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from nebius_cxcli.soperator_acceptance import _CONTROL, AcceptanceControl, terminal_proof
from nebius_cxcli.soperator_checks_policy import CHECKS_POLICY_ENV
from test_soperator_checks_execution import Cluster as BaseCluster  # noqa: F401
from test_soperator_checks_execution import accept, execution
from test_soperator_checks_execution import policy as base_policy  # noqa: F401


@pytest.fixture
def policy(base_policy):  # noqa: F811 - imported pytest fixture
    smoke = replace(base_policy.rules[-1], name="cuda-samples")
    compiled = replace(base_policy, rules=(*base_policy.rules, smoke))
    specs = copy.deepcopy(base_policy.execution_specs)
    specs["cuda-samples"] = copy.deepcopy(specs["gpu-fryer"])
    specs["cuda-samples"]["name"] = "cuda-samples"
    for spec in specs.values():
        spec[spec["checkType"] + "Spec"]["jobContainer"]["env"] = [
            {"name": CHECKS_POLICY_ENV, "value": compiled.sha256}
        ]
    return replace(compiled, execution_specs=specs)


class Cluster(BaseCluster):
    def slurm(self, command):
        if command.startswith("head -c 4194305") and any(
            name in command
            and entry["metadata"]["annotations"]["cxcli.nebius.ai/check"] == "cuda-samples"
            for name, entry in self.jobs.items()
        ):
            from nebius_cxcli.soperator_checks_verdict import _CUDA_SAMPLES

            report = {
                "meta": {"run_id": "smoke", "timestamp": 1},
                "status": "PASS",
                "tests": [
                    {
                        "name": name,
                        "cmd": name,
                        "enable": True,
                        "state": {"code": 0, "error": ""},
                        "checks": None,
                    }
                    for name in _CUDA_SAMPLES
                ],
            }
            return (
                "Health checker output:\n" + json.dumps(report) + "\nHealth checker status: PASS\n"
            )
        return super().slurm(command)


def test_readiness_skip_retains_real_jobs_and_resume_choice(tmp_path, policy):
    control = AcceptanceControl(requested="readiness", explicit=True)
    token = _CONTROL.set(control)
    try:
        cluster = Cluster(policy)
        runner = execution(tmp_path, policy, cluster)
        result = accept(runner)
        assert result["validation"]["extended"] == "skipped"
        assert not any(job["check"] == "gpu-fryer" for job in runner.state["jobs"].values())
        resumed = execution(tmp_path, policy, cluster)
        assert resumed.verify_acceptance()["validation"]["profile"] == "readiness"
        control.requested = "full"
        with pytest.raises(RuntimeError, match="choice cannot change"):
            execution(tmp_path, policy, cluster)
    finally:
        _CONTROL.reset(token)


def test_full_default_and_exact_required_coverage(tmp_path, policy):
    runner = execution(tmp_path, policy, Cluster(policy))
    result = accept(runner)
    assert result["validation"]["profile"] == "full"
    assert result["validation"]["extended"] == "passed"
    del runner.state["jobs"][next(iter(runner.state["jobs"]))]
    with pytest.raises(RuntimeError, match="does not cover"):
        runner.verify_acceptance()


def test_finish_before_extended_submits_nothing_and_is_sealed(tmp_path, policy):
    control = AcceptanceControl(finish_requested=True)
    token = _CONTROL.set(control)
    try:
        cluster = Cluster(policy)
        runner = execution(tmp_path, policy, cluster)
        result = accept(runner)
        assert result["validation"]["extended"] == "cancelled"
        assert result["validation"]["finish"]["status"] == "quiescent"
        assert {entry["check"] for entry in runner.state["jobs"].values()} == {
            rule.name for rule in policy.readiness
        }
        terminal_proof(runner.state, policy)
        altered = copy.deepcopy(runner.state)
        altered["jobs"]["invented"] = {}
        with pytest.raises(RuntimeError, match="cleanup evidence changed"):
            terminal_proof(altered, policy)
    finally:
        _CONTROL.reset(token)


def test_prompt_occurs_after_mandatory_work_and_eof_does_not_skip(tmp_path, policy, monkeypatch):
    token = _CONTROL.set(AcceptanceControl(requested="ask"))
    cluster = Cluster(policy)
    try:
        runner = execution(tmp_path, policy, cluster)

        def eof(*args, **kwargs):
            assert runner.state["validation"]["readiness"] == "passed"
            assert runner.state["jobs"]
            raise EOFError

        monkeypatch.setattr("typer.confirm", eof)
        with pytest.raises(EOFError):
            accept(runner)
        assert runner.state["validation"]["profile"] is None
        assert runner.state["phase"] == "acceptance"
        assert cluster.reservation_present
    finally:
        _CONTROL.reset(token)


def test_pending_choice_cannot_change_on_resume(tmp_path, policy):
    control = AcceptanceControl(requested="full", explicit=True)
    token = _CONTROL.set(control)
    try:
        cluster = Cluster(policy)
        execution(tmp_path, policy, cluster)._save()
        control.requested = "readiness"
        with pytest.raises(RuntimeError, match="choice cannot change"):
            execution(tmp_path, policy, cluster)
    finally:
        _CONTROL.reset(token)


def test_finish_does_not_prompt_later_target(monkeypatch):
    monkeypatch.setattr("typer.confirm", lambda *_a, **_k: pytest.fail("prompt after finish"))
    control = AcceptanceControl(requested="ask", finish_requested=True)
    assert control.choose() == "full"


def test_graceful_key_preserves_ctrl_c_and_restores_terminal(monkeypatch):
    import os
    import pty
    import sys
    import termios

    from nebius_cxcli.soperator_acceptance import graceful_keyboard

    master, slave = pty.openpty()
    with os.fdopen(slave, "r") as terminal:
        monkeypatch.setattr(sys, "stdin", terminal)
        original = termios.tcgetattr(terminal)
        control = AcceptanceControl()
        try:
            with pytest.raises(RuntimeError, match="stop"), graceful_keyboard(control):
                assert termios.tcgetattr(terminal)[3] & termios.ISIG
                assert not termios.tcgetattr(terminal)[3] & termios.ICANON
                os.write(master, b"\x07")
                import select

                assert select.select([terminal], [], [], 1)[0]
                assert control.poll()
                raise RuntimeError("stop")
            restored = termios.tcgetattr(terminal)
            # Darwin's kernel may set PENDIN while processing queued input.
            restored[3] &= ~termios.PENDIN
            original[3] &= ~termios.PENDIN
            assert restored == original
        finally:
            os.close(master)


def test_public_choice_defaults_and_positional_explicit(monkeypatch):
    import sys

    from nebius_cxcli.soperator_acceptance import acceptance_command, current_control

    @acceptance_command
    def command(acceptance=None, interactive=True, dry_run=True):
        return current_control().snapshot()

    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert command()["requested"] == "full"
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert command()["requested"] == "ask"
    assert command(interactive=False)["requested"] == "full"
    assert command("readiness")["requested"] == "readiness"


def test_cancelled_extra_worker_and_duplicate_proof_rejected(tmp_path, policy):
    from nebius_cxcli.soperator_checks_policy import checks_digest

    control = AcceptanceControl(finish_requested=True)
    token = _CONTROL.set(control)
    try:
        runner = execution(tmp_path, policy, Cluster(policy))
        accept(runner)
        entry = copy.deepcopy(next(iter(runner.state["jobs"].values())))
        runner.state["jobs"]["duplicate"] = entry
        runner.state["validation"]["finish"]["jobs_sha256"] = checks_digest(runner.state["jobs"])
        with pytest.raises(RuntimeError, match="does not cover"):
            runner.verify_acceptance()
        entry.update(check="gpu-fryer", worker="foreign-worker")
        runner.state["validation"]["finish"]["jobs_sha256"] = checks_digest(runner.state["jobs"])
        with pytest.raises(RuntimeError, match="does not cover"):
            runner.verify_acceptance()
    finally:
        _CONTROL.reset(token)


@pytest.mark.parametrize("outcome", ["skipped", "cancelled"])
def test_safe_finish_never_erases_current_catchup_failure(outcome):
    from types import SimpleNamespace

    from nebius_cxcli.soperator_checks_catchup import ChecksCatchupRecovery

    recovery = ChecksCatchupRecovery(
        SimpleNamespace(state={"validation": {"extended": outcome}}),
        read_log=lambda *a: pytest.fail("hidden diagnostic"),
        render_hook=lambda: pytest.fail("hidden hook"),
        apply_quiet=lambda *a: pytest.fail("hidden policy mutation"),
    )
    recovery._failures = lambda: [{"failed": True}]
    with pytest.raises(RuntimeError, match="failure prevents safe finish"):
        recovery.recover()
