from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import nebius_cxcli.flux_ops as flux_ops
from nebius_cxcli.paths import ProjectPaths


def _fake_paths(tmp_path: Path) -> ProjectPaths:
    project_dir = tmp_path / "deployments" / "tenant-123" / "project-456"
    project_dir.mkdir(parents=True, exist_ok=True)
    return ProjectPaths(
        config_path=project_dir / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path / "deployments",
        project_dir=project_dir,
        generated_dir=project_dir / "generated",
        infra_dir=project_dir / "generated" / "infra",
        flux_dir=project_dir / "generated" / "flux",
        inventory_dir=project_dir / "generated" / "inventory",
        path_tenant_id="tenant-123",
        path_project_id="project-456",
    )


def _write_rendered_flux_bundle(flux_dir: Path) -> None:
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
        "  namespace: flux-system\n",
        encoding="utf-8",
    )


def test_delete_rendered_flux_uses_kubectl_delete_kustomize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_paths = _fake_paths(tmp_path)
    _write_rendered_flux_bundle(fake_paths.flux_dir)
    calls: list[list[str]] = []

    monkeypatch.setattr(
        flux_ops.shutil, "which", lambda name: "/usr/bin/kubectl" if name == "kubectl" else None
    )

    def _fake_run(cmd: list[str], **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["kubectl", "cluster-info"]:
            return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")
        if "delete" in cmd:
            return SimpleNamespace(returncode=0, stdout="deleted\n", stderr="")
        raise AssertionError(f"Unexpected kubectl invocation: {cmd}")

    monkeypatch.setattr(flux_ops.subprocess, "run", _fake_run)

    flux_ops.delete_rendered_flux(fake_paths, extra_env={"KUBECONFIG": "/tmp/kubeconfig"})

    assert calls[0] == ["kubectl", "cluster-info"]
    delete_cmd = calls[1]
    assert delete_cmd[:4] == ["kubectl", "--cache-dir", delete_cmd[2], "delete"]
    assert "-k" in delete_cmd
    assert str(fake_paths.flux_dir) in delete_cmd
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
