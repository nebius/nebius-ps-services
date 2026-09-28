import json

import pytest

from nebius_cxcli.deployment_timing import TimingRecorder


def test_timing_persists_nested_exclusive_duration_and_attempts(tmp_path):
    now = [0.0]
    recorder = TimingRecorder(tmp_path / "timing.json", clock=lambda: now[0])
    with recorder.phase("deploy", "orchestration"):
        now[0] = 1
        with recorder.phase("setup", "setup"):
            now[0] = 8
        now[0] = 10
        with recorder.phase("setup", "setup"):
            now[0] = 12
        now[0] = 15
    data = json.loads(recorder.path.read_text())
    outer, first, second = data["phases"]
    assert outer["durationSeconds"] == 15
    assert outer["exclusiveSeconds"] == 6
    assert first["parent"] == second["parent"] == outer["id"]
    assert (first["attempt"], second["attempt"]) == (1, 2)
    assert data["targetIsDeadline"] is False


def test_timing_preserves_failure_without_command_or_error_details(tmp_path):
    recorder = TimingRecorder(tmp_path / "timing.json")
    with pytest.raises(ValueError), recorder.phase("setup", "setup"):
        raise ValueError("private error")
    text = recorder.path.read_text()
    assert "private error" not in text
    assert json.loads(text)["phases"][0]["outcome"] == "failed"


def test_overrun_stays_in_report_without_console_warning_or_double_counting(tmp_path):
    from io import StringIO

    from rich.console import Console

    now = [0.0]
    recorder = TimingRecorder(tmp_path / "timing.json", clock=lambda: now[0])
    with recorder.phase("deploy", "orchestration"):
        with recorder.phase("bootstrap", "setup"):
            now[0] = 920
        now[0] = 930
    recorder.payload["status"] = "succeeded"
    output = StringIO()
    recorder.finish(console=Console(file=output, width=200))
    payload = json.loads(recorder.path.read_text())
    assert payload["status"] == "succeeded"
    assert payload["withinTargetDuration"] is False
    assert payload["exclusiveCategorySeconds"] == {"orchestration": 10, "setup": 920}
    assert output.getvalue().splitlines() == [
        "Deployment elapsed: 15.5 minutes (succeeded).",
        f"Deployment timing report: {recorder.path}",
    ]
    assert str(recorder.path) in output.getvalue()


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_publication_failure_preserves_operation_error(tmp_path, monkeypatch, error_type):
    from types import SimpleNamespace

    from nebius_cxcli.deployment_timing import _PARENT, _RECORDER, deployment_timing

    original = error_type("operation failed")

    def fail_save(self):
        raise OSError("publication failed")

    @deployment_timing
    def deploy(config, paths, *, options):
        monkeypatch.setattr(TimingRecorder, "save", fail_save)
        raise original

    with pytest.raises(error_type) as raised:
        deploy(None, SimpleNamespace(reports_dir=tmp_path), options=SimpleNamespace(dry_run=False))
    assert raised.value is original
    assert original.__notes__ == [
        "Deployment phase timing could not be published.",
        "Deployment timing summary could not be published.",
    ]
    assert _PARENT.get() is None
    assert _RECORDER.get() is None


@pytest.mark.parametrize("when", ["entry", "exit", "finish"])
def test_publication_failure_without_operation_error_is_visible(tmp_path, monkeypatch, when):
    from types import SimpleNamespace

    from nebius_cxcli.deployment_timing import _PARENT, _RECORDER, deployment_timing

    publication_error = OSError("publication failed")
    executed = []

    def fail_save(self, **kwargs):
        raise publication_error

    if when == "entry":
        monkeypatch.setattr(TimingRecorder, "save", fail_save)
    elif when == "finish":
        monkeypatch.setattr(TimingRecorder, "finish", fail_save)

    @deployment_timing
    def deploy(config, paths, *, options):
        executed.append(True)
        if when == "exit":
            monkeypatch.setattr(TimingRecorder, "save", fail_save)

    with pytest.raises(OSError) as raised:
        deploy(None, SimpleNamespace(reports_dir=tmp_path), options=SimpleNamespace(dry_run=False))
    assert raised.value is publication_error
    assert executed == ([] if when == "entry" else [True])
    assert _PARENT.get() is None
    assert _RECORDER.get() is None


def test_fast_timing_does_not_publish_recovery_checkpoints(tmp_path):
    from nebius_cxcli.deployment_recovery import execution_checkpoint
    from nebius_cxcli.soperator_receipt_io import write_owner_only_json

    checkpoints = []
    recorder = TimingRecorder(tmp_path / "timing.json")
    recorder.checkpoint_reports = False
    with (
        execution_checkpoint(lambda: checkpoints.append("recovery")),
        recorder.phase("kubernetes-subprocess", "orchestration"),
    ):
        assert checkpoints == []
        write_owner_only_json(tmp_path / "lifecycle-receipt.json", {"intent": True})
        assert checkpoints == ["recovery"]
    assert checkpoints == ["recovery"]
    assert recorder.path.stat().st_mode & 0o777 == 0o600


def _profile_config(profile="fast-dev-test", *, enabled=True, target="cluster"):
    values = {} if profile is None else {"deploymentProfile": profile}
    return {
        "apps": {
            "charts": [
                {"id": "soperator", "enabled": enabled, "target_ref": target, "values": values}
            ]
        }
    }


@pytest.mark.parametrize(
    ("profile", "enabled", "selected", "checkpoint_reports"),
    [
        ("fast-dev-test", True, ("cluster",), False),
        ("standard", True, ("cluster",), True),
        (None, True, ("cluster",), True),
        ("invalid", True, ("cluster",), True),
        ("fast-dev-test", False, ("cluster",), True),
        ("fast-dev-test", True, ("other",), True),
        ("fast-dev-test", True, ("cluster", "other"), True),
        ("fast-dev-test", True, (), True),
    ],
)
def test_timing_policy_requires_exclusive_explicit_fast_target(
    tmp_path, profile, enabled, selected, checkpoint_reports
):
    from nebius_cxcli.deployment_recovery import execution_checkpoint
    from nebius_cxcli.deployment_timing import _RECORDER, selected_deployment_timing

    recorder = TimingRecorder(tmp_path / "timing.json")
    calls = []
    token = _RECORDER.set(recorder)
    try:
        with execution_checkpoint(lambda: calls.append("checkpoint")):
            with selected_deployment_timing(_profile_config(profile, enabled=enabled), selected):
                assert recorder.checkpoint_reports is checkpoint_reports
                with recorder.phase("read-only-query", "orchestration"):
                    pass
            assert recorder.checkpoint_reports is True
        assert len(calls) == (2 if checkpoint_reports else 0)
    finally:
        _RECORDER.reset(token)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"apps": {"charts": []}},
        {
            "apps": {
                "charts": [
                    {
                        "id": "soperator",
                        "enabled": True,
                        "target_ref": ref,
                        "values": {"deploymentProfile": "fast-dev-test"},
                    }
                    for ref in ["a", "b"]
                ]
            }
        },
    ],
)
def test_ambiguous_or_absent_fast_scope_keeps_default(tmp_path, config):
    from nebius_cxcli.deployment_timing import _RECORDER, selected_deployment_timing

    recorder = TimingRecorder(tmp_path / "timing.json")
    token = _RECORDER.set(recorder)
    try:
        with selected_deployment_timing(config, ("a",)):
            assert recorder.checkpoint_reports is True
    finally:
        _RECORDER.reset(token)


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_nested_standard_policy_and_failed_fast_receipt_restore_context(tmp_path, error_type):
    from nebius_cxcli.deployment_recovery import execution_checkpoint
    from nebius_cxcli.deployment_timing import _RECORDER, selected_deployment_timing
    from nebius_cxcli.soperator_receipt_io import write_owner_only_json

    recorder = TimingRecorder(tmp_path / "timing.json")
    calls = []
    failure = error_type("checkpoint failed")

    def checkpoint():
        calls.append("checkpoint")
        raise failure

    token = _RECORDER.set(recorder)
    try:
        with (
            pytest.raises(error_type) as raised,
            selected_deployment_timing(_profile_config(), ("cluster",)),
        ):
            with selected_deployment_timing(_profile_config("standard"), ("cluster",)):
                assert recorder.checkpoint_reports is True
            assert recorder.checkpoint_reports is False
            with execution_checkpoint(checkpoint), recorder.phase("fast-span", "orchestration"):
                write_owner_only_json(tmp_path / "real-receipt.json", {"intent": True})
        assert raised.value is failure
        assert calls == ["checkpoint"]
        assert recorder.checkpoint_reports is True
        assert recorder.payload["phases"][0]["outcome"] == (
            "interrupted" if error_type is KeyboardInterrupt else "failed"
        )
    finally:
        _RECORDER.reset(token)


def test_fast_report_preserves_private_parent_protection(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    recorder = TimingRecorder(link / "timing.json")
    recorder.checkpoint_reports = False
    with pytest.raises(RuntimeError, match="directory is not safe"):
        recorder.save()
    assert not (target / "timing.json").exists()


@pytest.mark.parametrize(("fast", "expected"), [(True, 1), (False, 201)])
def test_counted_timing_workload_preserves_only_required_receipt_checkpoint(
    tmp_path, fast, expected
):
    from nebius_cxcli.deployment_recovery import execution_checkpoint
    from nebius_cxcli.soperator_receipt_io import write_owner_only_json

    recorder = TimingRecorder(tmp_path / "timing.json")
    recorder.checkpoint_reports = not fast
    calls = []
    with execution_checkpoint(lambda: calls.append("checkpoint")):
        for _ in range(100):
            with recorder.phase("read-only-query", "orchestration"):
                pass
        write_owner_only_json(tmp_path / "real-receipt.json", {"intent": True})
    assert len(calls) == expected
