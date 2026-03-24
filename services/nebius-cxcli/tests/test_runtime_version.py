from __future__ import annotations

from nebius_cxcli import runtime_version


def test_resolve_runtime_version_prefers_source_tree_version(monkeypatch) -> None:
    monkeypatch.setattr(runtime_version, "_version_from_source_tree", lambda: "1.2.3")
    monkeypatch.setattr(runtime_version, "_version_from_metadata", lambda: "9.9.9")
    monkeypatch.setattr(runtime_version, "_version_from_generated_file", lambda: "8.8.8")

    assert runtime_version.resolve_runtime_version() == "1.2.3"


def test_resolve_runtime_version_falls_back_to_metadata(monkeypatch) -> None:
    monkeypatch.setattr(runtime_version, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_metadata", lambda: "2.3.4")
    monkeypatch.setattr(runtime_version, "_version_from_generated_file", lambda: "8.8.8")

    assert runtime_version.resolve_runtime_version() == "2.3.4"


def test_resolve_runtime_version_falls_back_to_generated_file(monkeypatch) -> None:
    monkeypatch.setattr(runtime_version, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_metadata", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_generated_file", lambda: "3.4.5")

    assert runtime_version.resolve_runtime_version() == "3.4.5"


def test_resolve_runtime_version_uses_unknown_fallback(monkeypatch) -> None:
    monkeypatch.setattr(runtime_version, "_version_from_source_tree", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_metadata", lambda: None)
    monkeypatch.setattr(runtime_version, "_version_from_generated_file", lambda: None)

    assert runtime_version.resolve_runtime_version() == "0+unknown"

