from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "tests/fixtures/docs/restored_document_contract.json"
DOC_PATHS = (PROJECT_ROOT / "README.md", PROJECT_ROOT / "docs/design.md")


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _markdown_headings(path: Path) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((len(match.group(1)), match.group(2)))
    return headings


def _slug(title: str) -> str:
    value = re.sub(r"[`*~]", "", title).strip().lower()
    value = re.sub(r"[^\w\- ]", "", value)
    return value.replace(" ", "-")


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _anchors(path: Path) -> set[str]:
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for _, title in _markdown_headings(path):
        base = _slug(title)
        ordinal = seen.get(base, 0)
        seen[base] = ordinal + 1
        anchors.add(base if ordinal == 0 else f"{base}-{ordinal}")
    return anchors


def test_restored_document_contract_is_complete() -> None:
    manifest = _manifest()
    assert manifest["schema"] == "nebius-cxcli-restored-document-contract/v1"
    documents = {row["source_document"]: row for row in manifest["documents"]}
    assert set(documents) == {"README.md", "docs/design.md"}
    assert all(document["destination_headings"] for document in documents.values())


@pytest.mark.parametrize("source_document", ("README.md", "docs/design.md"))
def test_every_required_heading_has_a_current_destination(source_document: str) -> None:
    document = next(
        row for row in _manifest()["documents"] if row["source_document"] == source_document
    )
    current = PROJECT_ROOT / source_document
    current_headings = [title for _, title in _markdown_headings(current)]
    for destination_heading in document["destination_headings"]:
        assert destination_heading in current_headings


def test_required_archived_body_contracts_are_restored() -> None:
    manifest = _manifest()
    for source_document, contracts in manifest["required_body_contracts"].items():
        active = _normalized((PROJECT_ROOT / source_document).read_text(encoding="utf-8"))
        for contract in contracts:
            assert _normalized(contract) in active, contract


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda path: str(path.relative_to(PROJECT_ROOT)))
def test_nested_lists_are_not_flattened_into_inline_list_markers(path: Path) -> None:
    assert re.search(r"\.[ \t]+-[ \t]+", path.read_text(encoding="utf-8")) is None


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda path: str(path.relative_to(PROJECT_ROOT)))
def test_markdown_links_and_toc_fragments_resolve(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"!?(?:\[[^\]]*\])\(([^)]+)\)", text):
        destination = match.group(1).strip().split(maxsplit=1)[0].strip("<>")
        if destination.startswith(("http://", "https://", "mailto:")):
            continue
        file_part, separator, fragment = destination.partition("#")
        target = path if not file_part else (path.parent / unquote(file_part)).resolve()
        assert target.exists(), destination
        if separator:
            assert fragment in _anchors(target), destination
