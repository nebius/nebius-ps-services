"""Exercise confirmation through the public command without live repair effects."""

import json
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from nebius_vpngw import cli
from nebius_vpngw.deploy import pair_phases, repair_apply, repair_confirmation


@pytest.fixture
def repair_command(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.touch()
    calls = []
    admission = SimpleNamespace(
        root=tmp_path / "repair",
        intent={
            "targets": [
                {
                    "name": "gateway-0",
                    "compute": "vm-0",
                    "disk": "disk-0",
                    "transport": "private-transport-sentinel",
                }
            ]
        },
    )
    monkeypatch.setattr(cli, "load_local_config", lambda _: {})
    monkeypatch.setattr(cli, "merge_with_peer_configs", lambda *_: SimpleNamespace())

    def apply(**kwargs):
        assert kwargs["repair"] is True
        calls.append("inspected")
        repair_apply.execute(admission, report=kwargs["result_report"])

    class Journal:
        def __init__(self, root):
            assert root == admission.root
            calls.append("journal")

        def load(self):
            return None

        def begin(self, intent, *, predecessor_settled):
            assert intent == admission.intent
            assert predecessor_settled is False
            calls.append("begin")
            return {"id": "new-repair"}

    class Coordinator:
        # The test observes entry into the existing effect owner without
        # performing package, cloud or guest operations.
        phase = "verified"

        def __init__(self, received, journal, *, resumed):
            assert received is admission
            assert resumed is False

        def finish(self):
            calls.append("repair")

    monkeypatch.setattr(cli, "_apply_impl", apply)
    monkeypatch.setattr(repair_apply.state, "Journal", Journal)
    monkeypatch.setattr(repair_apply, "Coordinator", Coordinator)
    return path, calls


@pytest.mark.parametrize("output_format", ["text", "json"])
@pytest.mark.parametrize(
    "answer,approved,aborted",
    [
        ("y\n", True, False),
        ("yes\n", True, False),
        ("n\n", False, False),
        ("\n", False, False),
        ("", False, True),
    ],
    ids=["yes-short", "yes", "no", "default-no", "eof"],
)
def test_public_confirmation_controls_effect_boundary(
    repair_command,
    output_format,
    answer,
    approved,
    aborted,
):
    path, calls = repair_command
    result = CliRunner().invoke(
        cli.app,
        ["apply", "-c", str(path), "--repair", "--output-format", output_format],
        input=answer,
    )
    assert (result.exit_code != 0) is aborted, (result.output, result.exception)
    assert calls == (["inspected", "journal", "begin", "repair"] if approved else ["inspected"])
    if output_format == "json":
        report = json.loads(result.stdout)
        assert report.get("cancelled", False) is (not approved and not aborted)
        if approved:
            assert report["outcome"] == "applied"
    if output_format == "text":
        assert "Continue with software repair? [y/N]:" in result.output
        assert result.output.count("Continue with software repair?") == 1
        assert result.output.index("Normal apply is recommended") < result.output.index("Targets:")
        assert result.output.index(
            "gateway-0: VM vm-0, retained disk disk-0"
        ) < result.output.index("Continue with software repair?")
        assert "Repair has no overall outage limit." in result.output
        assert "managed node certificates" in result.output
        assert "external\npeer credentials" in result.output
    assert "private-transport-sentinel" not in result.output


@pytest.mark.parametrize("output_format", ["text", "json"])
def test_interruption_grants_no_repair_authority(repair_command, monkeypatch, output_format):
    path, calls = repair_command

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(typer, "confirm", interrupt)
    result = CliRunner().invoke(
        cli.app,
        ["apply", "-c", str(path), "--repair", "--output-format", output_format],
    )
    assert result.exit_code != 0
    assert calls == ["inspected"]
    if output_format == "json":
        assert json.loads(result.stdout)["outcome"] == "blocked"


@pytest.mark.parametrize("changed", [False, True])
def test_regional_child_reuses_only_exact_parent_confirmation(monkeypatch, changed):
    intent = {"targets": [{"name": "gateway-0", "compute": "vm-0", "disk": "disk-0"}]}
    parent = SimpleNamespace(
        request={
            "repair": True,
            "repair_scope": {"targets": []} if changed else intent,
        }
    )
    monkeypatch.setattr(
        repair_confirmation, "confirm", lambda *_a, **_k: pytest.fail("duplicate confirmation")
    )
    token = pair_phases.CURRENT.set(parent)
    try:
        admission = SimpleNamespace(intent=intent)
        if changed:
            with pytest.raises(RuntimeError, match="targets changed"):
                repair_apply.confirm(admission)
        else:
            assert repair_apply.confirm(admission) is True
    finally:
        pair_phases.CURRENT.reset(token)
