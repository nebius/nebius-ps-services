from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from nebius_cxcli.terraform_ops import (
    _run,
    _stream_json_events,
    _terraform_failure_text_from_events,
    _translate_terraform_failure,
    terraform_apply,
    terraform_init,
    terraform_output_json,
    terraform_output_raw,
    terraform_plan,
    terraform_show_json,
    terraform_state_list,
    terraform_state_show,
    terraform_validate,
)


def test_translate_terraform_failure_summarizes_provider_module_name_violation() -> None:
    stderr = """
╷
│ Error: Invalid Attribute Value Match
│
│   with provider["terraform-provider.storage.eu-north1.nebius.cloud/nebius/nebius"],
│   on providers.tf line 2, in provider "nebius":
│    2:   module_name = var.nebius_provider_module_name
│
│ Attribute module_name must be a string of [a-zA-Z0-9_], not more than 16 characters, got: nebius-cxcli-sometest-project-u00k9ahgpr00nt1vh774v2
╵
""".strip()

    message = _translate_terraform_failure(
        cmd=["terraform", "apply", "-input=false"],
        cwd=Path("/tmp/demo"),
        stderr=stderr,
    )

    assert "Nebius provider `module_name` is invalid" in message
    assert "TF_VAR_nebius_provider_module_name" in message
    assert "Terraform diagnostics:" in message


def test_translate_terraform_failure_summarizes_coalesce_module_bug() -> None:
    stderr = """
╷
│ Error: Error in function call
│
│   on .terraform/modules/mk8s/platform-infra/modules/mk8s/locals.tf line 54, in locals:
│   54:   cpu_autoscaling        = coalesce(try(local.cpu_overrides.autoscaling, null), null)
│     ├────────────────
│     │ while calling coalesce(vals...)
│     │ local.cpu_overrides is empty map of dynamic
│
│ Call to function "coalesce" failed: no non-null, non-empty-string arguments.
╵
""".strip()

    message = _translate_terraform_failure(
        cmd=["terraform", "apply", "-input=false"],
        cwd=Path("/tmp/demo"),
        stderr=stderr,
    )

    assert "Terraform source module expression failed" in message
    assert ".terraform/modules/mk8s/platform-infra/modules/mk8s/locals.tf:54" in message
    assert "coalesce(..., null)" in message


def test_translate_terraform_failure_adds_generic_source_module_guidance() -> None:
    stderr = """
╷
│ Error: Invalid function argument
│
│   on .terraform/modules/custom-demo/main.tf line 12, in resource "example" "this":
│   12:   name = trimspace(var.name)
│
│ Invalid value for "str" parameter: argument must not be null.
╵
""".strip()

    message = _translate_terraform_failure(
        cmd=["terraform", "plan", "-input=false"],
        cwd=Path("/tmp/demo"),
        stderr=stderr,
    )

    assert "Terraform error originated inside a source module" in message
    assert "terraform validate" in message


def test_translate_terraform_failure_explains_missing_mk8s_cluster_output_contract() -> None:
    stderr = """
╷
│ Error: Unsupported attribute
│
│   on outputs.tf line 2, in output "mk8s_cluster_id":
│    2:   value       = module.mk8s.cluster_id
│     ├────────────────
│     │ module.mk8s is object with no attributes
│
│ This object does not have an attribute named "cluster_id".
╵
""".strip()

    message = _translate_terraform_failure(
        cmd=["terraform", "validate", "-no-color"],
        cwd=Path("/tmp/demo"),
        stderr=stderr,
    )

    assert "Rendered Terraform root expects child module output `cluster_id`" in message
    assert "component with built-in cluster handoff" in message


def test_translate_terraform_failure_explains_remote_state_lockfile_issue() -> None:
    stderr = """
╷
│ Error: Error acquiring the state lock
│
│ Error message: operation error S3: PutObject, https response error StatusCode: 412, RequestID: demo, HostID: , api error KeyAlreadyExists: Object already exists in the bucket, but If-None-Match header was sent
│ Lock Info:
│   ID:        677941a2-e814-9532-927f-6f6b9754f00a
│   Path:      tfstate-demo-project/terraform.tfstate
│   Operation: OperationTypeApply
│   Who:       rezab@example
│   Version:   1.14.1
│   Created:   2026-03-19 02:04:39.873262 +0000 UTC
│   Info:
│
│
│ Terraform acquires a state lock to protect the state from being written
│ by multiple users at the same time. Please resolve the issue above and try
│ again.
╵
""".strip()

    message = _translate_terraform_failure(
        cmd=["terraform", "apply", "-input=false"],
        cwd=Path("/tmp/demo"),
        stderr=stderr,
    )

    assert "did not create or change any resources" in message
    assert "stale lockfile" in message
    assert "bucket `tfstate-demo-project`, object `terraform.tfstate.tflock`" in message
    assert "owner `rezab@example`" in message
    assert "Terraform diagnostics:" in message


def test_terraform_failure_text_from_json_events_formats_location_and_detail() -> None:
    events = [
        {
            "type": "diagnostic",
            "diagnostic": {
                "severity": "error",
                "summary": "Error in function call",
                "detail": 'Call to function "coalesce" failed: no non-null, non-empty-string arguments.',
                "range": {"filename": "main.tf", "start": {"line": 5}},
                "snippet": {
                    "context": "locals",
                    "code": "  bad = coalesce(null, null)",
                },
            },
        }
    ]

    rendered = _terraform_failure_text_from_events(events)

    assert "Error: Error in function call" in rendered
    assert "on main.tf line 5, in locals:" in rendered
    assert 'Call to function "coalesce" failed' in rendered


def test_run_forwards_successful_stdout_and_stderr(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(stdout="plan ok\n", stderr="warning\n"),
    )

    _run(["terraform", "plan"], cwd=Path("/tmp/demo"), timeout=1)

    captured = capsys.readouterr()
    assert captured.out == "plan ok\n"
    assert captured.err == "warning\n"


def test_terraform_init_can_disable_backend(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[str, object]] = []
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops._run",
        lambda cmd, *, cwd, timeout, extra_env=None: calls.append(("run", tuple(cmd))),
    )

    terraform_init(infra_dir, backend=False)

    assert calls == [("run", ("terraform", "init", "-input=false", "-backend=false"))]


def test_terraform_validate_can_skip_init(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops.terraform_init",
        lambda infra_dir, *, extra_env=None: calls.append(("init", infra_dir)),
    )
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops._run",
        lambda cmd, *, cwd, timeout, extra_env=None: calls.append(("run", tuple(cmd))),
    )

    terraform_validate(Path("/tmp/demo"), initialize=False)

    assert calls == [("run", ("terraform", "validate", "-no-color"))]


def test_terraform_output_raw_initializes_backend_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir(parents=True, exist_ok=True)
    extra_env = {"TF_VAR_demo": "value"}

    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops.terraform_init",
        lambda cwd, *, extra_env=None, backend=True: calls.append(
            ("init", (cwd, extra_env, backend))
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops._run_capture",
        lambda cmd, *, cwd, timeout, extra_env=None: (
            calls.append(("run_capture", (tuple(cmd), cwd, timeout, extra_env)))
            or ("cluster-123", "")
        ),
    )

    assert terraform_output_raw(infra_dir, "mk8s_cluster_id", extra_env=extra_env) == "cluster-123"
    assert calls == [
        ("init", (infra_dir, extra_env, True)),
        (
            "run_capture",
            (("terraform", "output", "-raw", "mk8s_cluster_id"), infra_dir, 120, extra_env),
        ),
    ]


def test_terraform_output_json_can_skip_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops.terraform_init",
        lambda cwd, *, extra_env=None, backend=True: calls.append(
            ("init", (cwd, extra_env, backend))
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops._run_capture",
        lambda cmd, *, cwd, timeout, extra_env=None: (
            calls.append(("run_capture", (tuple(cmd), cwd, timeout, extra_env)))
            or ('{"mk8s_cluster_id":{"value":"cluster-123"}}', "")
        ),
    )

    assert terraform_output_json(infra_dir, initialize=False) == {
        "mk8s_cluster_id": {"value": "cluster-123"}
    }
    assert calls == [
        ("run_capture", (("terraform", "output", "-json"), infra_dir, 120, None)),
    ]


def test_terraform_show_json_can_skip_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    infra_dir = tmp_path / "infra"
    infra_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops.terraform_init",
        lambda cwd, *, extra_env=None, backend=True: calls.append(
            ("init", (cwd, extra_env, backend))
        ),
    )
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops._run_capture",
        lambda cmd, *, cwd, timeout, extra_env=None: (
            calls.append(("run_capture", (tuple(cmd), cwd, timeout, extra_env)))
            or ('{"values":{"root_module":{"resources":[]}}}', "")
        ),
    )

    assert terraform_show_json(infra_dir, initialize=False) == {
        "values": {"root_module": {"resources": []}}
    }
    assert calls == [
        ("run_capture", (("terraform", "show", "-json"), infra_dir, 120, None)),
    ]


def test_terraform_state_list_returns_empty_tuple_when_state_is_absent(monkeypatch) -> None:
    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")

    def _fake_run(*args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            ["terraform", "state", "list"],
            output="",
            stderr="No state file was found!\n",
        )

    import subprocess

    monkeypatch.setattr("nebius_cxcli.terraform_ops.subprocess.run", _fake_run)

    assert terraform_state_list(Path("/tmp/demo"), initialize=False) == ()


def test_terraform_state_show_renders_named_address(monkeypatch) -> None:
    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops._run_capture",
        lambda cmd, *, cwd, timeout, extra_env=None: ('name = "cluster-a"\n', ""),
    )

    rendered = terraform_state_show(
        Path("/tmp/demo"),
        "module.mk8s.nebius_mk8s_v1_cluster.this",
        initialize=False,
    )

    assert 'name = "cluster-a"' in rendered


def test_terraform_plan_and_apply_can_skip_init(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops.terraform_init",
        lambda infra_dir, *, extra_env=None: calls.append(("init", infra_dir)),
    )
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops._run",
        lambda cmd, *, cwd, timeout, extra_env=None: calls.append(("run", tuple(cmd))),
    )

    terraform_plan(Path("/tmp/demo"), initialize=False)
    terraform_apply(Path("/tmp/demo"), initialize=False)

    assert calls == [
        ("run", ("terraform", "plan", "-input=false", "-lock-timeout=5m")),
        ("run", ("terraform", "apply", "-input=false", "-auto-approve", "-lock-timeout=5m")),
    ]


def test_stream_json_events_aborts_early_when_abort_check_requests_it(
    monkeypatch, tmp_path: Path
) -> None:
    class _DelayedStream:
        def __init__(self, delay_seconds: float) -> None:
            self._delay_seconds = delay_seconds
            self._done = False

        def readline(self) -> str:
            if self._done:
                return ""
            time.sleep(self._delay_seconds)
            self._done = True
            return ""

        def close(self) -> None:
            return

    class _FakeProcess:
        def __init__(self) -> None:
            self.stdout = _DelayedStream(0.5)
            self.stderr = _DelayedStream(0.5)
            self.terminated = False
            self.killed = False

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

        def poll(self) -> int | None:
            return None if not (self.terminated or self.killed) else 1

        def wait(self, timeout=None) -> int:
            return 1

    process = _FakeProcess()

    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    with pytest.raises(RuntimeError, match="aborted early"):
        _stream_json_events(
            ["terraform", "apply", "-json"],
            cwd=tmp_path,
            timeout=10,
            abort_check=lambda: "Nebius API reported a terminal deployment error: demo",
        )

    assert process.terminated is True


def test_terraform_apply_passes_abort_check_to_streaming_runner(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def abort_check() -> None:
        return None

    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops.terraform_init",
        lambda infra_dir, *, extra_env=None: calls.update({"init": infra_dir}),
    )

    def _fake_stream_json_events(
        cmd,
        *,
        cwd,
        timeout,
        extra_env=None,
        event_callback=None,
        abort_check=None,
    ) -> None:
        calls.update(
            {
                "cmd": tuple(cmd),
                "cwd": cwd,
                "timeout": timeout,
                "event_callback": event_callback,
                "abort_check": abort_check,
            }
        )

    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops._stream_json_events",
        _fake_stream_json_events,
    )

    terraform_apply(
        Path("/tmp/demo"),
        event_callback=lambda event: None,
        abort_check=abort_check,
    )

    assert calls["cmd"] == (
        "terraform",
        "apply",
        "-json",
        "-input=false",
        "-auto-approve",
        "-lock-timeout=5m",
    )
    assert calls["abort_check"] is abort_check


def test_terraform_destroy_passes_abort_check_to_streaming_runner(monkeypatch) -> None:
    calls: dict[str, object] = {}

    def abort_check() -> None:
        return None

    monkeypatch.setattr("nebius_cxcli.terraform_ops._require_terraform", lambda: "terraform")
    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops.terraform_init",
        lambda infra_dir, *, extra_env=None: calls.update({"init": infra_dir}),
    )

    def _fake_stream_json_events(
        cmd,
        *,
        cwd,
        timeout,
        extra_env=None,
        event_callback=None,
        abort_check=None,
    ) -> None:
        calls.update(
            {
                "cmd": tuple(cmd),
                "cwd": cwd,
                "timeout": timeout,
                "event_callback": event_callback,
                "abort_check": abort_check,
            }
        )

    monkeypatch.setattr(
        "nebius_cxcli.terraform_ops._stream_json_events",
        _fake_stream_json_events,
    )

    from nebius_cxcli.terraform_ops import terraform_destroy

    terraform_destroy(
        Path("/tmp/demo"),
        event_callback=lambda event: None,
        abort_check=abort_check,
    )

    assert calls["cmd"] == (
        "terraform",
        "destroy",
        "-json",
        "-input=false",
        "-auto-approve",
        "-lock-timeout=5m",
    )
    assert calls["abort_check"] is abort_check
