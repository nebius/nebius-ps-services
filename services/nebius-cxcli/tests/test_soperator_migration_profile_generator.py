from __future__ import annotations

import importlib.util
import json
from io import StringIO
from pathlib import Path
from types import ModuleType


def _load_generator() -> ModuleType:
    script_path = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "generate_soperator_migration_profiles.py"
    )
    spec = importlib.util.spec_from_file_location("generate_soperator_migration_profiles", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _JsonResponse(StringIO):
    def __enter__(self) -> _JsonResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def test_generator_fetches_all_github_release_pages(monkeypatch) -> None:
    generator = _load_generator()
    monkeypatch.setattr(generator, "RELEASES_PER_PAGE", 2)
    calls: list[str] = []
    payloads = {
        1: [{"tag_name": "4.0.1"}, {"tag_name": "3.0.5"}],
        2: [{"tag_name": "2.0.0"}],
    }

    def _urlopen(url: str, *, timeout: int) -> _JsonResponse:
        assert timeout == 30
        calls.append(url)
        page = int(url.rsplit("page=", maxsplit=1)[1])
        return _JsonResponse(json.dumps(payloads.get(page, [])))

    monkeypatch.setattr(generator.urllib.request, "urlopen", _urlopen)

    releases = generator._fetch_releases()

    assert [release["tag_name"] for release in releases] == ["4.0.1", "3.0.5", "2.0.0"]
    assert "per_page=2&page=1" in calls[0]
    assert "per_page=2&page=2" in calls[1]
    assert len(calls) == 2


def test_generator_profile_payload_records_scope_and_compatibility_axes() -> None:
    generator = _load_generator()

    payload = generator._profile_payload(
        [
            {"tag_name": "4.0.1", "published_at": "2026-02-01T00:00:00Z"},
            {"tag_name": "3.0.5", "published_at": "2025-12-01T00:00:00Z"},
            {"tag_name": "v1.14.1", "published_at": "2024-09-23T00:00:00Z"},
        ]
    )

    assert payload["generator_scope"] == "release-metadata-and-compatibility-axes"
    assert (
        payload["future_generator_scope"]
        == "chart-tarball-crd-template-image-and-slurm-contract-fingerprints"
    )
    assert [release["version"] for release in payload["releases"]] == [
        "1.14.1",
        "3.0.5",
        "4.0.1",
    ]
    assert payload["releases"][0]["profile_id"] == "legacy-v1-to-target"
    assert payload["releases"][1]["profile_id"] == "v3-to-target"
    assert payload["profile_groups"]["v3-to-target"]["requires_aligned_sfs"] is True
    assert (
        payload["profile_groups"]["v3-to-target"]["compatibility_axes"]["compute_layout"]
        == "replace-and-roll"
    )
    assert (
        payload["profile_groups"]["v4-to-target"]["compatibility_axes"]["storage_layout"]
        == "adopt-existing-or-create-if-missing"
    )
