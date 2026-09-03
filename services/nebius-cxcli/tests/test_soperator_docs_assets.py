from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = PROJECT_ROOT / "docs"
EXPECTED_ASSETS = {
    "jail-rootfs-active-passive-storage.svg",
    "soperator-protected-upgrade-workflow.svg",
}


def test_soperator_docs_keep_exactly_two_accessible_svg_sources_of_truth() -> None:
    image_assets = {
        path.name
        for path in DOCS_ROOT.iterdir()
        if path.suffix.lower() in {".svg", ".png", ".jpg", ".jpeg", ".webp"}
    }
    assert image_assets == EXPECTED_ASSETS

    for name in EXPECTED_ASSETS:
        root = ElementTree.parse(DOCS_ROOT / name).getroot()
        assert root.attrib.get("role") == "img"
        assert root.attrib.get("aria-labelledby") == "title desc"
        ids = [element.attrib["id"] for element in root.iter() if "id" in element.attrib]
        assert len(ids) == len(set(ids))
        assert set(root.attrib["aria-labelledby"].split()) <= set(ids)
        view_box = tuple(float(value) for value in root.attrib["viewBox"].split())
        assert view_box[:2] == (0.0, 0.0)
        assert view_box[2:] == (
            float(root.attrib["width"]),
            float(root.attrib["height"]),
        )
        assert all(
            element.tag.rsplit("}", 1)[-1] not in {"script", "foreignObject"}
            and not any(attribute.rsplit("}", 1)[-1] == "href" for attribute in element.attrib)
            for element in root.iter()
        )
        children = {
            child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in root
        }
        assert children["title"]
        assert children["desc"]
        assert next(child for child in root if child.tag.endswith("title")).attrib.get("id") == (
            "title"
        )
        assert next(child for child in root if child.tag.endswith("desc")).attrib.get("id") == (
            "desc"
        )


def test_soperator_svg_assets_are_referenced_by_readme_and_design() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    design = (DOCS_ROOT / "design.md").read_text(encoding="utf-8")

    for name in EXPECTED_ASSETS:
        assert f"docs/{name}" in readme
        assert f"]({name})" in design


def test_soperator_svg_semantics_match_current_upgrade_and_rootfs_contracts() -> None:
    workflow = (DOCS_ROOT / "soperator-protected-upgrade-workflow.svg").read_text(
        encoding="utf-8"
    )
    jail = (DOCS_ROOT / "jail-rootfs-active-passive-storage.svg").read_text(
        encoding="utf-8"
    )

    retired_contracts = (
        "scratch PVC",
        "source + live + target manifests",
        "Nebius metrics + logs",
        "retry forever",
        "type + content",
        "parent receipt chooses one branch",
    )
    assert all(contract not in workflow for contract in retired_contracts)
    assert all(contract not in jail for contract in retired_contracts)

    for required in (
        "Full-stack Soperator upgrade campaign",
        "managed → Terraform",
        "onboarded → provider API",
        "target-wins",
        "Optional pre-hop catch-up",
        "node-templates:&lt;source&gt;",
        "→ runtime-readiness:&lt;source&gt;",
        "only when a group lags source CP",
        "each hop: mk8s-hop:&lt;minor&gt;",
        "→ runtime-readiness:&lt;minor&gt;",
        "zero-hop OS/GPU: node-templates:&lt;endpoint&gt;",
        "→ runtime-readiness:&lt;endpoint&gt;",
        "final-readiness",
        "soperator status --verify-observability",
    ):
        assert required in workflow

    for required in (
        "Target-wins admission",
        "no source/reference comparison",
        "/data · /home · /models · /scripts",
        "physical SFS (canonical)",
        "VM-NFS (optional)",
        "exact unconsumed inactive slot",
    ):
        assert required in jail
