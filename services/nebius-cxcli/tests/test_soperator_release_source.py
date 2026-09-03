from __future__ import annotations

import hashlib
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

import nebius_cxcli.soperator_release as release_module
from nebius_cxcli.soperator_release import (
    SOPERATOR_UPSTREAM_CHART_ROLES,
    SoperatorGitTreeEntry,
    SoperatorReleaseMetadata,
    classify_soperator_release_capabilities,
    load_soperator_release_snapshot,
    normalize_soperator_release_selector,
    resolve_soperator_release,
    verify_soperator_source_git_tree,
    write_soperator_release_snapshot,
)
from soperator_fixtures import sample_snapshot


class _Response(io.BytesIO):
    def __init__(self, payload: Any, url: str) -> None:
        super().__init__(json.dumps(payload).encode())
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _Opener:
    def __init__(
        self,
        *,
        draft: bool = False,
        prerelease: bool = False,
        response_url: str | None = None,
    ) -> None:
        self.draft = draft
        self.prerelease = prerelease
        self.response_url = response_url
        self.urls: list[str] = []
        self.authorizations: list[str | None] = []

    def open(self, request: Any, timeout: float = 0) -> _Response:
        del timeout
        url = request.full_url
        self.urls.append(url)
        self.authorizations.append(request.get_header("Authorization"))
        if "/releases/" in url:
            payload = {
                "tag_name": "4.1.7",
                "draft": self.draft,
                "prerelease": self.prerelease,
                "published_at": "2026-01-01T00:00:00Z",
            }
        elif "/git/ref/tags/" in url:
            payload = {"object": {"type": "commit", "sha": "a" * 40}}
        elif "/git/commits/" in url:
            payload = {"tree": {"sha": "b" * 40}}
        elif "/git/trees/" in url:
            payload = {"truncated": False, "tree": []}
        else:  # pragma: no cover - fixture guard
            raise AssertionError(url)
        return _Response(payload, self.response_url or url)


class _TransientOpener(_Opener):
    def __init__(self) -> None:
        super().__init__()
        self.transient_failures = 1

    def open(self, request: Any, timeout: float = 0) -> _Response:
        if self.transient_failures:
            self.transient_failures -= 1
            self.urls.append(request.full_url)
            raise urllib.error.HTTPError(
                request.full_url,
                504,
                "Gateway Timeout",
                hdrs=None,
                fp=None,
            )
        return super().open(request, timeout)


class _TagEndpointUnavailableOpener(_Opener):
    def __init__(self, *, prerelease: bool = False) -> None:
        super().__init__()
        self.prerelease = prerelease

    def open(self, request: Any, timeout: float = 0) -> _Response:
        del timeout
        url = request.full_url
        if "/releases/tags/" in url:
            self.urls.append(url)
            raise urllib.error.HTTPError(
                url,
                504,
                "Gateway Timeout",
                hdrs=None,
                fp=None,
            )
        if "/releases?" in url:
            self.urls.append(url)
            return _Response(
                [
                    {
                        "tag_name": "1.22.3",
                        "draft": False,
                        "prerelease": self.prerelease,
                        "published_at": "2025-11-06T18:11:46Z",
                    }
                ],
                url,
            )
        return super().open(request)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "latest"), ("LATEST", "latest"), ("v4.1.7", "4.1.7")],
)
def test_release_selector_normalization(value: str | None, expected: str) -> None:
    assert normalize_soperator_release_selector(value) == expected


@pytest.mark.parametrize("value", ["4.1", "main", "4.1.7-rc.1", ""])
def test_release_selector_rejects_floating_or_prerelease_values(value: str) -> None:
    with pytest.raises(ValueError, match="latest.*X.Y.Z"):
        normalize_soperator_release_selector(value)


def test_latest_resolution_uses_official_release_and_git_identities() -> None:
    opener = _Opener()
    metadata = resolve_soperator_release("latest", opener=opener)
    assert metadata.release == "4.1.7"
    assert metadata.commit == "a" * 40
    assert metadata.tree == "b" * 40
    assert opener.urls[0].endswith("/releases/latest")


def test_default_release_resolution_uses_environment_token_only_in_request_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _Opener()
    monkeypatch.setenv("GH_TOKEN", "github-test-token")
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_handlers: opener)

    metadata = resolve_soperator_release("latest")

    assert metadata.release == "4.1.7"
    assert opener.authorizations == ["Bearer github-test-token"] * len(opener.urls)
    assert all("github-test-token" not in url for url in opener.urls)


def test_default_release_opener_rejects_cross_authority_redirect_before_following() -> None:
    request = urllib.request.Request(
        "https://api.github.com/repos/nebius/soperator/releases/latest",
        headers={"Authorization": "Bearer test-only-token"},
    )
    handler = release_module._OfficialGitHubRedirectHandler()

    with pytest.raises(ValueError, match="redirect must use the official GitHub API"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://example.invalid/capture",
        )


def test_custom_release_opener_isolated_from_environment_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opener = _Opener()
    monkeypatch.setenv("GH_TOKEN", "github-test-token")

    resolve_soperator_release("latest", opener=opener)

    assert opener.authorizations == [None] * len(opener.urls)


def test_release_resolution_rejects_malformed_environment_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GH_TOKEN", "invalid\ntoken")

    with pytest.raises(ValueError, match="GH_TOKEN.*valid GitHub API token"):
        resolve_soperator_release("latest")


def test_release_resolution_retries_one_transient_github_gateway_failure() -> None:
    opener = _TransientOpener()

    metadata = resolve_soperator_release("latest", opener=opener)

    assert metadata.release == "4.1.7"
    assert len(opener.urls) == 5


def test_exact_release_resolution_uses_official_release_list_after_persistent_gateway_failure() -> (
    None
):
    opener = _TagEndpointUnavailableOpener()

    metadata = resolve_soperator_release("1.22.3", opener=opener)

    assert metadata.release == "1.22.3"
    assert sum("/releases/tags/" in url for url in opener.urls) == 3
    assert sum("/releases?" in url for url in opener.urls) == 1


def test_exact_release_list_fallback_still_rejects_prereleases() -> None:
    with pytest.raises(ValueError, match="draft and prerelease"):
        resolve_soperator_release(
            "1.22.3",
            opener=_TagEndpointUnavailableOpener(prerelease=True),
        )


@pytest.mark.parametrize(
    "response_url",
    (
        "http://api.github.com/repos/nebius/soperator/releases/latest",
        "https://user@api.github.com/repos/nebius/soperator/releases/latest",
        "https://api.github.com:444/repos/nebius/soperator/releases/latest",
    ),
)
def test_release_resolution_rejects_redirects_outside_exact_github_api_authority(
    response_url: str,
) -> None:
    with pytest.raises(RuntimeError, match="left the official GitHub API"):
        resolve_soperator_release("latest", opener=_Opener(response_url=response_url))


@pytest.mark.parametrize("flag", ["draft", "prerelease"])
def test_resolution_rejects_nonstable_release(flag: str) -> None:
    opener = _Opener(**{flag: True})
    with pytest.raises(ValueError, match="draft and prerelease"):
        resolve_soperator_release("latest", opener=opener)


def test_resolution_rejects_downgrade_before_source_download() -> None:
    with pytest.raises(ValueError, match="downgrade"):
        resolve_soperator_release("4.1.7", current_release="4.2.0", opener=_Opener())


def test_source_tree_verification_uses_resolved_git_blobs(tmp_path: Path) -> None:
    content = b"official source\n"
    path = tmp_path / "Chart.yaml"
    path.write_bytes(content)
    blob = hashlib.sha1(  # noqa: S324 - intentional Git object identity.
        f"blob {len(content)}\0".encode() + content
    ).hexdigest()
    metadata = SoperatorReleaseMetadata(
        selector="4.1.7",
        release="4.1.7",
        repository="https://github.com/nebius/soperator",
        tag="4.1.7",
        commit="a" * 40,
        tree="b" * 40,
        archive_url="https://github.com/nebius/soperator/archive/refs/tags/4.1.7.tar.gz",
        archive_root="soperator-4.1.7",
        published_at="",
        tree_entries=(SoperatorGitTreeEntry("Chart.yaml", "100644", "blob", blob, len(content)),),
    )
    verify_soperator_source_git_tree(tmp_path, metadata)
    path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="differs from Git tree"):
        verify_soperator_source_git_tree(tmp_path, metadata)


def test_snapshot_round_trip_rejects_content_drift(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    snapshot = sample_snapshot()
    write_soperator_release_snapshot(path, snapshot)
    assert load_soperator_release_snapshot(path) == snapshot
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["commit"] = "c" * 40
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        load_soperator_release_snapshot(path)


def test_snapshot_loader_rejects_non_mapping_graph_nodes(tmp_path: Path) -> None:
    path = tmp_path / "snapshot.json"
    write_soperator_release_snapshot(path, sample_snapshot())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["release_graph"].append("invalid")
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid node"):
        load_soperator_release_snapshot(path)


def test_capability_classifier_admits_known_flux_contract(tmp_path: Path) -> None:
    for chart_name, _role in SOPERATOR_UPSTREAM_CHART_ROLES:
        name = chart_name.removeprefix("helm-")
        chart = tmp_path / "helm" / name / "Chart.yaml"
        chart.parent.mkdir(parents=True)
        chart.write_text(f"apiVersion: v2\nname: helm-{name}\n", encoding="utf-8")
    required = {
        "helm/soperator-fluxcd/templates/soperator.yaml": (
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
        ),
        "helm/soperator-fluxcd/templates/slurm-cluster.yaml": (
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
        ),
        "helm/slurm-cluster/templates/slurm-cluster-cr.yaml": (
            "apiVersion: slurm.nebius.ai/v1\nkind: SlurmCluster\n"
        ),
        "helm/soperator/crds/slurmcluster-crd.yaml": (
            "name: slurmclusters.slurm.nebius.ai\ngroup: slurm.nebius.ai\n"
        ),
        "helm/soperator-fluxcd/values.yaml": (
            "observability: {}\nslurmCluster: {}\nsoperator: {}\n"
        ),
        "helm/slurm-cluster/values.yaml": (
            "images: {}\npopulateJail: {}\nslurmNodes: {}\nvolumeSources: []\n"
        ),
    }
    for relative, content in required.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    contract, digest = classify_soperator_release_capabilities(tmp_path)
    assert contract == "upstream-flux-v1"
    assert digest.startswith("sha256:")


def test_capability_classifier_routes_legacy_flux_umbrella_through_protected_data_plane(
    tmp_path: Path,
) -> None:
    for name in ("soperator-fluxcd", "soperator", "slurm-cluster"):
        chart = tmp_path / "helm" / name / "Chart.yaml"
        chart.parent.mkdir(parents=True)
        chart.write_text(f"apiVersion: v2\nname: helm-{name}\n", encoding="utf-8")
    required = {
        "helm/soperator-fluxcd/templates/soperator.yaml": (
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
        ),
        "helm/soperator-fluxcd/templates/slurm-cluster.yaml": (
            "apiVersion: helm.toolkit.fluxcd.io/v2\nkind: HelmRelease\n"
        ),
        "helm/slurm-cluster/templates/slurm-cluster-cr.yaml": (
            "apiVersion: slurm.nebius.ai/v1\nkind: SlurmCluster\n"
        ),
        "helm/soperator/crds/slurmcluster-crd.yaml": (
            "name: slurmclusters.slurm.nebius.ai\ngroup: slurm.nebius.ai\n"
        ),
        "helm/soperator-fluxcd/values.yaml": (
            "observability: {}\nslurmCluster: {}\nsoperator: {}\n"
        ),
        "helm/slurm-cluster/values.yaml": (
            "images: {}\npopulateJail: {}\nslurmNodes: {}\nvolumeSources: []\n"
        ),
    }
    for relative, content in required.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    contract, digest = classify_soperator_release_capabilities(tmp_path)

    assert contract == "protected-data-plane-v1"
    assert digest.startswith("sha256:")


def test_capability_classifier_rejects_an_incomplete_modern_flux_graph(
    tmp_path: Path,
) -> None:
    for name in (
        "soperator-fluxcd-bootstrap",
        "soperator-fluxcd",
        "soperator",
        "slurm-cluster",
    ):
        chart = tmp_path / "helm" / name / "Chart.yaml"
        chart.parent.mkdir(parents=True)
        chart.write_text(f"apiVersion: v2\nname: helm-{name}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="upstream Flux chart graph is incomplete"):
        classify_soperator_release_capabilities(tmp_path)


def test_capability_classifier_rejects_same_names_with_incompatible_structure(
    tmp_path: Path,
) -> None:
    for chart_name, _role in SOPERATOR_UPSTREAM_CHART_ROLES:
        name = chart_name.removeprefix("helm-")
        chart = tmp_path / "helm" / name / "Chart.yaml"
        chart.parent.mkdir(parents=True)
        chart.write_text(f"apiVersion: v2\nname: helm-{name}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="required upstream structure"):
        classify_soperator_release_capabilities(tmp_path)
