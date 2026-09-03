from __future__ import annotations

import os
from pathlib import Path

import pytest

from nebius_cxcli.paths import ProjectPaths
from nebius_cxcli.ssh_trust import resolve_ssh_known_hosts_file, ssh_trust_options


def _paths(tmp_path: Path) -> ProjectPaths:
    project_dir = tmp_path / "deployments" / "tenant" / "project"
    return ProjectPaths(
        config_path=project_dir / "config.yaml",
        repo_root=tmp_path,
        deployments_dir=tmp_path / "deployments",
        project_dir=project_dir,
        generated_dir=project_dir / "generated",
        infra_dir=project_dir / "generated" / "infra",
        flux_dir=project_dir / "generated" / "flux",
        reports_dir=project_dir / "generated" / "reports",
        path_tenant_folder="tenant",
        path_project_folder="project",
    )


def test_resolve_ssh_known_hosts_file_uses_project_generated_default(tmp_path: Path) -> None:
    paths = _paths(tmp_path)

    assert (
        resolve_ssh_known_hosts_file(paths, None)
        == (paths.generated_dir / "ssh_known_hosts").resolve()
    )


def test_resolve_ssh_known_hosts_file_expands_explicit_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert (
        resolve_ssh_known_hosts_file(paths, Path("~/trusted-hosts"))
        == (tmp_path / "trusted-hosts").resolve()
    )


def test_resolved_override_preserves_symlink_for_fail_closed_validation(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    target = tmp_path / "target-known-hosts"
    target.write_text("host ssh-ed25519 AAAATEST\n", encoding="utf-8")
    symlink = tmp_path / "selected-known-hosts"
    symlink.symlink_to(target)

    selected = resolve_ssh_known_hosts_file(paths, symlink)

    assert selected == symlink.absolute()
    with pytest.raises(RuntimeError, match="must be a regular file"):
        ssh_trust_options(selected)


def test_ssh_trust_options_are_strict_and_project_local(tmp_path: Path) -> None:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("host ssh-ed25519 AAAATEST\n", encoding="utf-8")

    options = ssh_trust_options(known_hosts)

    assert options == [
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts.resolve()}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
    ]
    assert "accept-new" not in " ".join(options)


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "hardlink"])
def test_ssh_trust_options_reject_unsafe_paths(tmp_path: Path, kind: str) -> None:
    known_hosts = tmp_path / "known_hosts"
    if kind == "directory":
        known_hosts.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text("host ssh-ed25519 AAAATEST\n", encoding="utf-8")
        known_hosts.symlink_to(target)
    elif kind == "hardlink":
        target = tmp_path / "target"
        target.write_text("host ssh-ed25519 AAAATEST\n", encoding="utf-8")
        os.link(target, known_hosts)

    with pytest.raises(RuntimeError, match="SSH known-hosts"):
        ssh_trust_options(known_hosts)


def test_ssh_trust_options_reject_unreadable_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("host ssh-ed25519 AAAATEST\n", encoding="utf-8")
    monkeypatch.setattr("nebius_cxcli.ssh_trust.os.access", lambda *_args: False)

    with pytest.raises(RuntimeError, match="not readable"):
        ssh_trust_options(known_hosts)
