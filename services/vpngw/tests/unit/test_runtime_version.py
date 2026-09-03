from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace

from nebius_vpngw import runtime_version


def test_resolve_runtime_version_prefers_source_tree_version(monkeypatch) -> None:
    monkeypatch.setattr(runtime_version, "_version_from_source_tree", lambda: "0.5.4")
    monkeypatch.setattr(runtime_version, "_version_from_metadata", lambda: "9.9.9")
    monkeypatch.setattr(runtime_version, "_version_from_generated_file", lambda: "8.8.8")

    assert runtime_version.resolve_runtime_version() == "0.5.4"


def test_resolve_runtime_version_falls_back_to_metadata(monkeypatch) -> None:
    monkeypatch.setattr(runtime_version, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_metadata", lambda: "0.5.5.dev1")
    monkeypatch.setattr(runtime_version, "_version_from_generated_file", lambda: "8.8.8")

    assert runtime_version.resolve_runtime_version() == "0.5.5.dev1"


def test_resolve_runtime_version_falls_back_to_generated_file(monkeypatch) -> None:
    monkeypatch.setattr(runtime_version, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_metadata", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_generated_file", lambda: "0.5.4")

    assert runtime_version.resolve_runtime_version() == "0.5.4"


def test_resolve_runtime_version_uses_unknown_fallback(monkeypatch) -> None:
    monkeypatch.setattr(runtime_version, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_metadata", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_generated_file", lambda: None)

    assert runtime_version.resolve_runtime_version() == "0.0.0"


def test_parse_git_describe_version_returns_exact_tag_version() -> None:
    assert (
        runtime_version._parse_git_describe_version(
            "nebius-vpngw-v0.5.5-0-ga76ff05471111111111111111111111111111111"
        )
        == "0.5.5"
    )


def test_parse_git_describe_version_bumps_patch_for_dev_distance() -> None:
    assert (
        runtime_version._parse_git_describe_version(
            "nebius-vpngw-v0.5.4-16-ga76ff05471111111111111111111111111111111"
        )
        == "0.5.5.dev16"
    )


def test_version_from_setuptools_scm_uses_project_configuration(monkeypatch) -> None:
    captured: dict[str, object] = {}
    configuration = object()

    def fake_get_version(config, *, force_write_version_files):
        captured["config"] = config
        captured["force_write_version_files"] = force_write_version_files
        return "0.5.5.dev10"

    class FakeEnvironment:
        @classmethod
        def from_env(cls, *tool_names):
            captured["tool_names"] = tool_names
            return cls()

        def build_config(self, **kwargs):
            captured["config_kwargs"] = kwargs
            return configuration

    monkeypatch.setitem(
        sys.modules, "setuptools_scm", SimpleNamespace(_get_version=fake_get_version)
    )
    monkeypatch.setitem(
        sys.modules, "vcs_versioning", SimpleNamespace(VcsEnvironment=FakeEnvironment)
    )

    service_root = Path("/tmp/repo/services/vpngw")

    assert runtime_version._version_from_setuptools_scm(service_root) == "0.5.5.dev10"
    assert captured == {
        "tool_names": ("SETUPTOOLS_SCM",),
        "config_kwargs": {"name": service_root / "pyproject.toml"},
        "config": configuration,
        "force_write_version_files": False,
    }


def test_version_from_setuptools_scm_is_warning_free() -> None:
    service_root = Path(__file__).resolve().parents[2]

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        assert runtime_version._version_from_setuptools_scm(service_root) is not None


def test_version_from_source_tree_falls_back_to_git_describe(monkeypatch) -> None:
    service_root = Path("/tmp/repo/services/vpngw")

    monkeypatch.setattr(runtime_version, "_service_root", lambda: service_root)
    monkeypatch.setattr(runtime_version, "_version_from_setuptools_scm", lambda path: None)
    monkeypatch.setattr(runtime_version, "_version_from_git_describe", lambda path: "0.5.5")

    assert runtime_version._version_from_source_tree() == "0.5.5"


def test_version_from_git_describe_has_a_bounded_timeout(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def timeout_run(*args, **kwargs):
        captured.update(kwargs)
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    monkeypatch.setattr(runtime_version.subprocess, "run", timeout_run)

    assert runtime_version._version_from_git_describe(Path("/tmp/repo/services/vpngw")) is None
    assert captured["timeout"] == runtime_version._GIT_DESCRIBE_TIMEOUT_SECONDS
