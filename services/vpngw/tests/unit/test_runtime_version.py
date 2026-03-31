from __future__ import annotations

import sys
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


def test_version_from_source_tree_uses_nested_scm_describe_command(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_get_version(**kwargs):
        captured.update(kwargs)
        return "0.5.5.dev10"

    monkeypatch.setattr(runtime_version, "_service_root", lambda: Path("/tmp/repo/services/vpngw"))
    monkeypatch.setitem(sys.modules, "setuptools_scm", SimpleNamespace(get_version=fake_get_version))

    assert runtime_version._version_from_source_tree() == "0.5.5.dev10"
    assert captured["root"] == "/tmp/repo/services/vpngw"
    assert captured["search_parent_directories"] is True
    assert captured["scm"] == {"git": {"describe_command": runtime_version._GIT_DESCRIBE_COMMAND}}
    assert "git_describe_command" not in captured
