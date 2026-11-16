import io
import json
import shlex
from types import SimpleNamespace

import pytest

from nebius_vpngw.deploy import repair_remote


@pytest.mark.parametrize(
    "payload,message,stage,code",
    [
        (
            {"action": "inspect"},
            "Signed Ubuntu repository metadata is unavailable",
            "inspect",
            "repair_repository_unavailable",
        ),
        (
            {"action": "guard"},
            "Effective repair service inhibition changed",
            "quiesce",
            "repair_service_inhibition",
        ),
        ({"action": "stage"}, "private-value-must-not-appear", "stage", "receipt"),
        ({"action": []}, "private-value-must-not-appear", "install", "receipt"),
        ([], "private-value-must-not-appear", "install", "receipt"),
        (
            {"action": "retire-idle-handoffs"},
            "Prior handoff is frozen by another operation",
            "predecessor", "repair_handoff_foreign_freeze",
        ),
        (
            {"action": "retire-idle-handoffs"},
            "Prior handoff reservation differs from original authority",
            "predecessor", "repair_handoff_authority",
        ),
        (
            {"action": "retire-idle-handoffs"},
            "private-value-must-not-appear",
            "predecessor", "repair_handoff_unverified",
        ),
    ],
)
def test_repair_diagnostic_is_action_specific_and_never_exposes_arbitrary_error(
    monkeypatch, capsys, payload, message, stage, code
):
    monkeypatch.setattr(
        repair_remote.sys, "stdin", SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))
    )
    monkeypatch.setattr(
        repair_remote, "control", lambda _: (_ for _ in ()).throw(RuntimeError(message))
    )
    with pytest.raises(SystemExit):
        repair_remote.control_main()
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "failed", "stage": stage, "error_code": code}
    assert "private-value" not in output.out + output.err


@pytest.mark.parametrize("command", ["apply"])
@pytest.mark.parametrize("code", [
    "repair_handoff_authority", "repair_handoff_foreign_freeze", "repair_handoff_unverified",
])
def test_handoff_blocker_keeps_shared_repair_context(tmp_path, command, code):
    from nebius_vpngw.deploy.recovery import GuestFailure, Invocation, advice

    config = tmp_path / "original single.yaml"
    authoritative = tmp_path / "generated HA's config.yaml"
    result = advice(Invocation(command, config, project="project-a", region="region-a",
                               repair_config=authoritative),
                    GuestFailure(code, "predecessor", "private-error-must-not-appear"))
    assert result.reason_code == "guest-" + code.replace("_", "-")
    assert result.next_action and result.repair_prerequisite
    assert shlex.split(result.repair_command) == [
        "nebius-vpngw", "apply", "--local-config-file", str(authoritative),
        "--project-id", "project-a", "--region", "region-a", "--repair",
    ]
    assert "private-error" not in result.text()


@pytest.mark.parametrize("command", ["apply"])
def test_repair_identity_blocker_has_safe_specific_guidance(tmp_path, command):
    from nebius_vpngw.deploy.recovery import Invocation, RepairBlocked, advice

    path = tmp_path / "generated HA's configuration.yaml"
    failure = RepairBlocked("repair-ssh-authority", ownership_established=True)
    failure.__cause__ = ValueError("private-lifecycle-content")
    result = advice(Invocation(command, path, project="project-a", region="region-a"), failure)
    assert result.reason_code == "repair-ssh-authority"
    assert "SSH identity" in result.reason
    assert result.next_action and result.repair_prerequisite
    assert shlex.split(result.repair_command) == [
        "nebius-vpngw", "apply", "--local-config-file", str(path),
        "--project-id", "project-a", "--region", "region-a", "--repair",
    ]
    assert result.destroy_command is None
    assert "private-lifecycle-content" not in result.text() + json.dumps(result.mapping())
