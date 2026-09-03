from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from nebius_cxcli.soperator_release import (
    classify_soperator_release_capabilities,
    resolve_soperator_release,
    verify_soperator_source_git_tree,
)
from nebius_cxcli.soperator_release_source import (
    SoperatorArchiveLimits,
    _download_archive,
    extract_soperator_release_archive,
)

_REQUIRED_RELEASE_SELECTORS = ("latest", "1.22.0", "3.0.4", "4.0.5", "4.1.7")
_SUPPORTED_CAPABILITY_CONTRACTS = {"protected-data-plane-v1", "upstream-flux-v1"}
_EXPECTED_REQUIRED_CONTRACTS = {
    "latest": "upstream-flux-v1",
    "1.22.0": "protected-data-plane-v1",
    "1.22.3": "protected-data-plane-v1",
    "3.0.4": "upstream-flux-v1",
    "4.0.5": "upstream-flux-v1",
    "4.1.7": "upstream-flux-v1",
}


def _validated_release_selectors(raw: str) -> tuple[str, ...]:
    selectors = tuple(item.strip() for item in raw.split(",") if item.strip())
    if not selectors:
        raise ValueError("official Soperator capability sweep has no release selectors")
    missing = tuple(
        selector for selector in _REQUIRED_RELEASE_SELECTORS if selector not in selectors
    )
    if missing:
        raise ValueError(
            "official Soperator capability sweep is missing required selectors: "
            + ", ".join(missing)
        )
    return selectors


@pytest.mark.parametrize("raw", ("", "   ", ", ,"))
def test_release_selector_matrix_rejects_empty_input(raw: str) -> None:
    with pytest.raises(ValueError, match="has no release selectors"):
        _validated_release_selectors(raw)


@pytest.mark.parametrize(
    ("raw", "missing"),
    (
        ("1.22.0,3.0.4,4.0.5,4.1.7", "latest"),
        ("latest,3.0.4,4.0.5,4.1.7", "1.22.0"),
        ("latest,1.22.0,3.0.4,4.0.5", "4.1.7"),
    ),
)
def test_release_selector_matrix_rejects_incomplete_input(raw: str, missing: str) -> None:
    with pytest.raises(ValueError, match=rf"missing required selectors: .*{missing}"):
        _validated_release_selectors(raw)


def test_release_selector_matrix_accepts_complete_whitespace_and_any_order() -> None:
    assert _validated_release_selectors(" 4.1.7, latest , 4.0.5,1.22.0 , 3.0.4 ") == (
        "4.1.7",
        "latest",
        "4.0.5",
        "1.22.0",
        "3.0.4",
    )


@pytest.mark.integration
def test_selected_official_stable_releases_have_a_supported_structural_contract(
    tmp_path: Path,
) -> None:
    """Optional, non-mutating sweep against official release and Git identities."""

    raw = os.environ.get("NEBIUS_CXCLI_TEST_OFFICIAL_SOPERATOR_RELEASES", "").strip()
    if not raw:
        pytest.skip("official Soperator capability sweep was not requested")
    try:
        selectors = _validated_release_selectors(raw)
    except ValueError as exc:
        validation_error = str(exc)
    else:
        validation_error = ""
    if validation_error:
        pytest.fail(validation_error, pytrace=False)

    for index, selector in enumerate(selectors):
        metadata = resolve_soperator_release(selector)
        release_root = tmp_path / f"{index}-{metadata.release}"
        release_root.mkdir()
        archive = release_root / "release.tar.gz"
        _download_archive(
            metadata.archive_url,
            archive,
            limits=SoperatorArchiveLimits(),
        )
        archive_sha256 = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
        source = extract_soperator_release_archive(
            archive,
            release_root / "source",
            expected_root=metadata.archive_root,
            expected_archive_sha256=archive_sha256,
            expected_manifest_sha256=None,
        )
        verify_soperator_source_git_tree(source, metadata)
        contract, digest = classify_soperator_release_capabilities(source)

        assert contract in _SUPPORTED_CAPABILITY_CONTRACTS
        if selector in _EXPECTED_REQUIRED_CONTRACTS:
            assert contract == _EXPECTED_REQUIRED_CONTRACTS[selector]
        assert digest.startswith("sha256:")
