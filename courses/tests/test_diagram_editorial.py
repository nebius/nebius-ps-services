"""Navigation and diagrams preserve explicit, accurate teaching relationships."""

import re
import xml.etree.ElementTree as ET

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder


@pytest.mark.parametrize("course", COURSES)
def test_no_redundant_return_or_diagram_disclosure_controls(course: str) -> None:
    document = (ROOT / course / "index.html").read_text()
    assert "Back to contents" not in document
    assert "Read the diagram" not in document
    assert 'class="diagram-reading"' not in document
    assert 'id="course-contents"' in document
    assert 'class="answer-key"' not in document
    assert 'class="practice-links"' in document
    assert 'class="lab-source"' in document


@pytest.mark.parametrize("course", COURSES)
def test_overview_layout_is_declared_and_accessible(course: str) -> None:
    builder = load_builder()
    rows = builder.parse_visuals(ROOT / course / "reference/visual-plan.md")
    for index, row in enumerate(rows, 1):
        assert row.layout in builder.DIAGRAM_LAYOUTS
        markup = builder.diagram(row, index)
        assert f'data-diagram-kind="{row.layout}"' in markup
        svg = ET.fromstring(re.search(r"<svg\b.*?</svg>", markup, re.S).group())
        ids = {element.get("id") for element in svg.iter()}
        assert set(svg.get("aria-labelledby").split()) <= ids
        assert svg.find("title").text
        assert svg.find("desc").text
        if row.layout == "comparison":
            assert not svg.findall(".//line")
            assert not svg.findall(".//polyline")


def test_invalid_layout_is_rejected(tmp_path) -> None:
    builder = load_builder()
    plan = tmp_path / "visual-plan.md"
    plan.write_text(
        "| Title | First stage | Second stage | Third stage | Explanation | Lesson | After | Layout | Home |\n"
        "| Example | A | B | C | Explanation | 1 | Mechanism | invented | lesson |\n"
    )
    with pytest.raises(ValueError, match="layout|placement"):
        builder.parse_visuals(plan)


def test_workload_matrix_has_two_axes_and_all_four_cases() -> None:
    builder = load_builder()
    rows = builder.parse_visuals(ROOT / "llm-inference/reference/visual-plan.md")
    row = next(row for row in rows if row.layout == "matrix")
    markup = builder.diagram(row, 1)
    svg = ET.fromstring(re.search(r"<svg\b.*?</svg>", markup, re.S).group())
    labels = [" ".join(node.itertext()) for node in svg.findall(".//text")]
    assert {"Short OSL", "Long OSL", "Short ISL", "Long ISL"} <= set(labels)
    assert row.third in labels
    assert len(svg.findall("rect")) == 4
    for prompt in ("Small", "Large"):
        for generation in ("short", "long"):
            assert f"{prompt} prompt, {generation} generation" in labels


def test_decoder_additions_have_transformed_and_residual_inputs() -> None:
    path = (
        ROOT
        / "llm-training/reference/diagrams/decoder-only-language-model-from-token-ids-to-logits.svg"
    )
    svg = ET.fromstring(path.read_text())
    labels = {node.text for node in svg.findall("text")}
    assert {"Add x", "Add y"} <= labels
    connectors = {node.get("d") for node in svg.findall("path")}
    # Each addition has one transformed input from its left and a separate
    # incoming-state bypass terminating on its top or bottom edge.
    assert {"M450 367 H490", "M90 367 V295 H542 V330"} <= connectors
    assert {"M940 367 H1000", "M620 367 V465 H1052 V405"} <= connectors


def test_paged_kv_reuse_preserves_physical_identity() -> None:
    path = (
        ROOT
        / "llm-inference/reference/diagrams/paged-kv-blocks-before-release-and-after-reuse.svg"
    )
    svg = ET.fromstring(path.read_text())
    rows = {}
    for node in svg.findall(".//text"):
        if node.get("y") in {"230", "460"}:
            rows.setdefault(node.get("y"), {})[node.get("x")] = node.text
    before, after = rows["230"], rows["460"]
    assert before.keys() == after.keys()
    changed = {x: (before[x], after[x]) for x in before if before[x] != after[x]}
    assert changed == {"490": ("B0", "D0"), "835": ("B1", "D1")}
    labels = [node.text for node in svg.findall(".//text")]
    for block in range(7):
        assert labels.count(f"physical P{block}") == 2


def test_latency_diagram_separates_last_token_and_protocol_completion() -> None:
    path = (
        ROOT / "llm-inference/reference/diagrams/llm-request-latency-decomposition.svg"
    )
    svg = ET.fromstring(path.read_text())
    description = svg.find("desc").text
    assert "TPOT=(tN-t1)/(N-1)" in description
    assert "response completion latency=tc-t0" in description
    connectors = {node.get("d") for node in svg.findall("path")}
    assert {"M70 85 H510", "M70 330 H900", "M70 395 H1120"} <= connectors
