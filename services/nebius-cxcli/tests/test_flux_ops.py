from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import nebius_cxcli.flux_ops as flux_ops
from nebius_cxcli.paths import ProjectPaths


def _fake_paths(tmp_path: Path) -> ProjectPaths:
    project_dir = tmp_path / "deployments" / "tenant-name-example" / "project-name-example"
    project_dir.mkdir(parents=True, exist_ok=True)
    return ProjectPaths(
        config_path=project_dir / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path / "deployments",
        project_dir=project_dir,
        generated_dir=project_dir / "generated",
        infra_dir=project_dir / "generated" / "infra",
        flux_dir=project_dir / "generated" / "flux",
        reports_dir=project_dir / "generated" / "reports",
        path_tenant_folder="tenant-name-example",
        path_project_folder="project-name-example",
    )


def _write_rendered_flux_bundle(flux_dir: Path, *, namespace: str = "flux-system") -> None:
    flux_dir.mkdir(parents=True, exist_ok=True)
    (flux_dir / "kustomization.yaml").write_text(
        "apiVersion: kustomize.config.k8s.io/v1beta1\n"
        "kind: Kustomization\n"
        "resources:\n"
        "  - release.yaml\n",
        encoding="utf-8",
    )
    (flux_dir / "release.yaml").write_text(
        "apiVersion: helm.toolkit.fluxcd.io/v2\n"
        "kind: HelmRelease\n"
        "metadata:\n"
        "  name: demo\n"
        f"  namespace: {namespace}\n",
        encoding="utf-8",
    )


def test_filtered_kubectl_apply_returns_summary_without_terminal_chatter(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "namespace/flux-system unchanged\n"
                "deployment.apps/source-controller configured\n"
                "customresourcedefinition/example created\n"
            ),
            stderr="",
        ),
    )

    summary = flux_ops._run_filtered_kubectl_apply(["kubectl", "apply", "-f", "manifest"])

    assert summary == flux_ops.FluxApplySummary(
        created=1,
        configured=1,
        unchanged=1,
        other=0,
    )
    assert capsys.readouterr() == ("", "")


def test_captured_flux_failure_is_bounded_and_redacts_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = ["request failed at https://private.example.invalid/token"] + [
        f"detail {index}" for index in range(20)
    ]
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="\n".join(lines),
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        flux_ops._run_captured(["flux", "migrate"])

    detail = str(excinfo.value)
    assert "<url>" in detail
    assert "private.example.invalid" not in detail
    assert "lines omitted" in detail
    assert len(detail.splitlines()) <= 11


def test_captured_flux_failure_redacts_credential_shaped_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr=(
                "Authorization: Bearer authorization-sensitive-value\n"
                "Bearer bearer-sensitive-value\n"
                "token=token-sensitive-value\n"
                "NEBIUS_IAM_TOKEN=iam-sensitive-value\n"
                "AWS_SECRET_ACCESS_KEY=aws-sensitive-value\n"
                '{"access_token":"json-sensitive-value","safe":"visible"}\n'
                "{'password': 'map-sensitive-value', 'safe': 'visible'}\n"
                '{"tokens": ["array-sensitive-one", "array-sensitive-two"]}\n'
                "token => arrow-sensitive-value\n"
                "-----BEGIN PRIVATE KEY-----\n"
                "pem-sensitive-value\n"
                "-----END PRIVATE KEY-----\n"
                "safe diagnostic\n"
            ),
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        flux_ops._run_captured(["flux", "migrate"])

    detail = str(excinfo.value)
    assert "authorization-sensitive-value" not in detail
    assert "bearer-sensitive-value" not in detail
    assert "token-sensitive-value" not in detail
    assert "iam-sensitive-value" not in detail
    assert "aws-sensitive-value" not in detail
    assert "json-sensitive-value" not in detail
    assert "map-sensitive-value" not in detail
    assert "array-sensitive-one" not in detail
    assert "array-sensitive-two" not in detail
    assert "arrow-sensitive-value" not in detail
    assert "pem-sensitive-value" not in detail
    assert "PRIVATE KEY" not in detail
    assert "<redacted>" in detail
    assert "sensitive credential material redacted" in detail
    assert "safe diagnostic" in detail


def test_filtered_kubectl_failure_surfaces_bounded_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="migration refused by the Kubernetes API",
        ),
    )

    with pytest.raises(RuntimeError, match="migration refused by the Kubernetes API"):
        flux_ops._run_filtered_kubectl_apply(["kubectl", "apply", "-f", "manifest"])


def test_captured_command_output_is_bounded_by_character_count() -> None:
    detail = flux_ops.sanitized_bounded_command_output("x" * 5000)

    assert len(detail) <= 2048
    assert detail.endswith("... truncated ...")


def test_captured_flux_timeout_keeps_bounded_redacted_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _timeout(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise subprocess.TimeoutExpired(
            ["flux", "migrate"],
            600,
            stderr=b"Authorization: Bearer timeout-sensitive-value",
        )

    monkeypatch.setattr(flux_ops.subprocess, "run", _timeout)

    with pytest.raises(RuntimeError, match="flux timed out after 600 seconds") as excinfo:
        flux_ops._run_captured(["flux", "migrate"], timeout=600)

    assert "timeout-sensitive-value" not in str(excinfo.value)
    assert "<redacted>" in str(excinfo.value)


def test_flux_migration_summary_groups_resources_by_kind() -> None:
    summary = flux_ops._flux_migration_summary(
        "\n".join(
            (
                "✔ HelmRelease/flux-system/one migrated to version v2",
                "✔ HelmRelease/flux-system/two migrated to version v2",
                "✔ Kustomization/flux-system/main migrated to version v1",
                "custom resources migrated successfully",
            )
        )
    )

    assert summary.total == 3
    assert summary.kinds == (("HelmRelease", 2), ("Kustomization", 1))
    assert summary.detail() == ("3 custom resources migrated (HelmRelease 2, Kustomization 1)")


def test_flux_controller_install_emits_bounded_stage_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[flux_ops.SoperatorProgressEvent] = []
    monkeypatch.setattr(flux_ops, "_require_binary", lambda _name: None)
    monkeypatch.setattr(flux_ops, "wait_for_flux_namespace_ready", lambda **kwargs: None)
    monkeypatch.setattr(flux_ops, "wait_for_flux_crds_clear", lambda **kwargs: None)
    monkeypatch.setattr(flux_ops, "wait_for_flux_crds_ready", lambda **kwargs: None)
    monkeypatch.setattr(
        flux_ops,
        "_run_filtered_kubectl_apply",
        lambda *args, **kwargs: flux_ops.FluxApplySummary(
            created=1,
            configured=2,
            unchanged=3,
            other=0,
        ),
    )
    rollout_commands: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        flux_ops,
        "_run_captured",
        lambda command, **kwargs: (
            rollout_commands.append(tuple(command))
            or subprocess.CompletedProcess(command, 0, stdout="ready\n", stderr="")
        ),
    )

    summary = flux_ops._install_flux_controller_manifest(
        "https://example.invalid/install.yaml",
        progress=events.append,
        phase_prefix="flux-target",
        phase_label="Flux target controllers",
    )

    assert summary.total == 6
    assert len(rollout_commands) == len(flux_ops.FLUX_CORE_DEPLOYMENTS)
    assert [event.state for event in events] == [
        flux_ops.SoperatorProgressState.START,
        flux_ops.SoperatorProgressState.SUCCESS,
        flux_ops.SoperatorProgressState.START,
        flux_ops.SoperatorProgressState.SUCCESS,
        flux_ops.SoperatorProgressState.START,
        *(flux_ops.SoperatorProgressState.UPDATE for _ in flux_ops.FLUX_CORE_DEPLOYMENTS[1:]),
        flux_ops.SoperatorProgressState.SUCCESS,
    ]
    assert events[-1].description == "Flux target controllers ready"
    assert events[-1].current == events[-1].total == len(flux_ops.FLUX_CORE_DEPLOYMENTS)


def test_kustomization_resource_inventory_rejects_paths_outside_flux_dir(
    tmp_path: Path,
) -> None:
    flux_dir = tmp_path / "generated" / "flux"
    flux_dir.mkdir(parents=True)
    outside = tmp_path / "outside.yaml"
    outside.write_text("apiVersion: v1\nkind: Namespace\nmetadata:\n  name: unsafe\n")
    (flux_dir / "kustomization.yaml").write_text(
        "resources:\n  - ../../outside.yaml\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes the rendered Flux directory"):
        flux_ops._kustomization_resource_files(flux_dir)


def test_delete_rendered_flux_uses_explicit_manifest_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    _write_rendered_flux_bundle(fake_paths.flux_dir)
    calls: list[tuple[list[str], str]] = []

    monkeypatch.setattr(
        flux_ops.shutil, "which", lambda name: "/usr/bin/kubectl" if name == "kubectl" else None
    )
    monkeypatch.setattr(flux_ops, "flux_crds_installed", lambda *, extra_env=None: True)

    def _fake_run(cmd: list[str], **kwargs):
        calls.append((cmd, str(kwargs.get("input") or "")))
        if cmd[:2] == ["kubectl", "cluster-info"]:
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        if "delete" in cmd:
            return SimpleNamespace(returncode=0, stdout="deleted\n", stderr="")
        raise AssertionError(f"Unexpected kubectl invocation: {cmd}")

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    flux_ops.delete_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert calls[0][0] == ["kubectl", "cluster-info"]
    delete_cmd, delete_manifest = calls[1]
    assert delete_cmd[:4] == ["kubectl", "--cache-dir", delete_cmd[2], "delete"]
    assert delete_cmd[4:6] == ["-f", "-"]
    assert "kind: HelmRelease" in delete_manifest
    assert "name: demo" in delete_manifest
    assert "--ignore-not-found=true" in delete_cmd
    assert "--wait=true" in delete_cmd
    assert "--timeout=15m" in delete_cmd


def test_delete_rendered_flux_fails_fast_when_cluster_is_unreachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    _write_rendered_flux_bundle(fake_paths.flux_dir)

    monkeypatch.setattr(
        flux_ops.shutil, "which", lambda name: "/usr/bin/kubectl" if name == "kubectl" else None
    )
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="connection refused\n"
        ),
    )

    with pytest.raises(
        RuntimeError,
        match="kubectl could not reach the target Kubernetes cluster for local destroy",
    ):
        flux_ops.delete_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})


def test_delete_rendered_flux_excludes_protected_and_shared_soperator_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.flux_dir / "kustomization.yaml").write_text(
        "resources:\n  - soperator.yaml\n",
        encoding="utf-8",
    )
    (fake_paths.flux_dir / "soperator.yaml").write_text(
        """apiVersion: v1
kind: PersistentVolume
metadata:
  name: jail-rootfs-slot-a-pv
  labels:
    soperator.nebius.ai/managed-by: nebius-cxcli-adapter
    soperator.nebius.ai/lifecycle: protected
---
apiVersion: v1
kind: Namespace
metadata:
  name: soperator
  labels:
    soperator.nebius.ai/lifecycle: shared-adopted
---
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: nebius-cxcli-soperator-jail-mount
  namespace: soperator
  labels:
    soperator.nebius.ai/managed-by: nebius-cxcli-adapter
    soperator.nebius.ai/lifecycle: recreatable
""",
        encoding="utf-8",
    )
    deleted: list[str] = []
    messages: list[str] = []

    monkeypatch.setattr(flux_ops.shutil, "which", lambda _name: "/usr/bin/kubectl")
    monkeypatch.setattr(flux_ops, "flux_crds_installed", lambda *, extra_env=None: True)

    def _fake_run(cmd: list[str], **kwargs):
        if cmd[:2] == ["kubectl", "cluster-info"]:
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        deleted.append(str(kwargs.get("input") or ""))
        return SimpleNamespace(returncode=0, stdout="deleted\n", stderr="")

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    flux_ops.delete_rendered_flux(fake_paths, emit=messages.append)

    assert len(deleted) == 1
    assert "kind: DaemonSet" in deleted[0]
    assert "kind: PersistentVolume" not in deleted[0]
    assert "kind: Namespace" not in deleted[0]
    assert messages and "PersistentVolume jail-rootfs-slot-a-pv" in messages[0]


def test_delete_rendered_flux_rejects_unclassified_soperator_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    fake_paths.flux_dir.mkdir(parents=True, exist_ok=True)
    (fake_paths.flux_dir / "kustomization.yaml").write_text(
        "resources:\n  - soperator.yaml\n",
        encoding="utf-8",
    )
    (fake_paths.flux_dir / "soperator.yaml").write_text(
        """apiVersion: v1
kind: ConfigMap
metadata:
  name: unsafe
  namespace: soperator
  labels:
    soperator.nebius.ai/managed-by: nebius-cxcli-adapter
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(flux_ops.shutil, "which", lambda _name: "/usr/bin/kubectl")
    monkeypatch.setattr(flux_ops, "flux_crds_installed", lambda *, extra_env=None: True)
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(returncode=0, stdout="ok\n", stderr=""),
    )

    with pytest.raises(ValueError, match="no lifecycle class"):
        flux_ops.delete_rendered_flux(fake_paths)


def test_delete_rendered_flux_skips_when_flux_crds_are_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    _write_rendered_flux_bundle(fake_paths.flux_dir)
    calls: list[list[str]] = []
    messages: list[str] = []

    monkeypatch.setattr(
        flux_ops.shutil, "which", lambda name: "/usr/bin/kubectl" if name == "kubectl" else None
    )
    monkeypatch.setattr(flux_ops, "flux_crds_installed", lambda *, extra_env=None: False)

    def _fake_run(cmd: list[str], **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["kubectl", "cluster-info"]:
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        raise AssertionError(f"Unexpected kubectl invocation: {cmd}")

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    flux_ops.delete_rendered_flux(
        fake_paths,
        extra_env={"KUBECONFIG": "/tmp/kubeconfig"},
        emit=messages.append,
    )

    assert calls == [["kubectl", "cluster-info"]]
    assert messages == [
        "Flux resource APIs are not installed in the target cluster; "
        "skipping rendered Flux resource deletion."
    ]


def test_delete_rendered_flux_private_handoff_reports_network_guidance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    _write_rendered_flux_bundle(fake_paths.flux_dir)

    monkeypatch.setattr(
        flux_ops.shutil, "which", lambda name: "/usr/bin/kubectl" if name == "kubectl" else None
    )
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda cmd, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="i/o timeout\n"),
    )

    with pytest.raises(RuntimeError, match="private MK8s control-plane endpoint"):
        flux_ops.delete_rendered_flux(
            fake_paths,
            extra_env={
                "KUBECONFIG": "/tmp/kubeconfig",
                flux_ops.CLUSTER_HANDOFF_ACCESS_ENV: "internal",
            },
        )


def test_get_crd_payload_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            subprocess.TimeoutExpired(cmd=["kubectl", "get", "crd"], timeout=20)
        ),
    )

    assert flux_ops._get_crd_payload("kustomizations.kustomize.toolkit.fluxcd.io") is None


def test_wait_for_flux_resource_apis_retries_transient_kubectl_timeouts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    _write_rendered_flux_bundle(fake_paths.flux_dir)
    calls: list[list[str]] = []
    call_count = {"count": 0}

    monkeypatch.setattr(flux_ops, "wait_for_flux_crds_ready", lambda *, extra_env=None: None)
    monkeypatch.setattr(
        flux_ops,
        "_FLUX_REQUIRED_API_TYPES",
        {("helmreleases.helm.toolkit.fluxcd.io", flux_ops.FLUX_NAMESPACE)},
    )

    def _fake_run(cmd: list[str], **kwargs):
        calls.append(cmd)
        call_count["count"] += 1
        if call_count["count"] == 1:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=20)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)
    monkeypatch.setattr(flux_ops.time, "sleep", lambda _seconds: None)

    flux_ops.wait_for_flux_resource_apis(
        fake_paths,
        timeout_seconds=5,
        poll_interval_seconds=0.01,
    )

    assert call_count["count"] >= 2
    assert calls[0][:2] == ["kubectl", "get"]
    assert calls[0][2].endswith(".helm.toolkit.fluxcd.io")
    assert "-A" in calls[0]


def test_wait_for_flux_resource_apis_checks_resource_types_without_target_namespaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    _write_rendered_flux_bundle(fake_paths.flux_dir, namespace="nvidia-network-operator")
    calls: list[list[str]] = []

    monkeypatch.setattr(flux_ops, "wait_for_flux_crds_ready", lambda *, extra_env=None: None)
    monkeypatch.setattr(
        flux_ops,
        "_FLUX_REQUIRED_API_TYPES",
        {("helmreleases.helm.toolkit.fluxcd.io", flux_ops.FLUX_NAMESPACE)},
    )
    monkeypatch.setattr(
        flux_ops.subprocess,
        "run",
        lambda cmd, **kwargs: (
            calls.append(cmd) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    flux_ops.wait_for_flux_resource_apis(fake_paths, timeout_seconds=5, poll_interval_seconds=0.01)

    assert calls
    assert all("-A" in cmd for cmd in calls)
    assert all("-n" not in cmd for cmd in calls)
