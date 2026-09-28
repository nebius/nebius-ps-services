"""Known passive-policy limits must not trigger a redundant cluster apply."""

from types import SimpleNamespace

import pytest

from nebius_cxcli.soperator_checks_lifecycle import ChecksLifecycle
from nebius_cxcli.soperator_checks_phase import ChecksPhase


@pytest.mark.parametrize(
    ("supported", "suppression_fails", "enabled_fails", "expected_fallbacks", "expected_pauses"),
    [
        (False, False, False, [True], [False]),
        (False, False, True, [True], [False]),
        (True, False, False, [False], [True]),
        (True, True, False, [False, True], [True, False]),
    ],
)
def test_maintenance_selects_known_passive_policy_before_apply(
    supported, suppression_fails, enabled_fails, expected_fallbacks, expected_pauses
):
    applied = []
    verified = []
    checks = SimpleNamespace(
        state={},
        operation_id="operation",
        policy=SimpleNamespace(passive={"supported": supported}),
        _save=lambda: None,
        _verify_isolation=lambda: None,
    )
    lifecycle = ChecksLifecycle(checks, applied.append, installing=True)
    lifecycle.admission.establish = lambda: None
    lifecycle.admission.verify = lambda: None
    native_verify = lifecycle.passive.verify

    def verify(*, paused):
        verified.append(paused)
        if not supported and paused:
            # Exercise the real native guard; it rejects before any transport.
            return native_verify(paused=True)
        if suppression_fails and paused:
            raise RuntimeError("existing passive execution has not quiesced")
        if enabled_fails and not paused:
            raise RuntimeError("effective passive policy has not converged")
        return {"status": "paused" if paused else "enabled-fallback"}

    lifecycle.passive.verify = verify
    if enabled_fails:
        with pytest.raises(RuntimeError, match="effective passive policy has not converged"):
            lifecycle.maintenance()
    else:
        lifecycle.maintenance()

    assert [context.phase for context in applied] == [ChecksPhase.MAINTENANCE] * len(
        expected_fallbacks
    )
    assert [context.passive_fallback for context in applied] == expected_fallbacks
    assert verified == expected_pauses
