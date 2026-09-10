"""Protect label spacing and connector routes without a graphics dependency.

These are layout-budget regressions, not browser or font-rendering proof.
"""

import re
import xml.etree.ElementTree as ET

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder


@pytest.mark.parametrize("course", COURSES)
def test_overviews_keep_readable_scale_in_a_narrow_article(course: str) -> None:
    builder = load_builder()
    for index, row in enumerate(
        builder.parse_visuals(ROOT / course / "reference/visual-plan.md"), 1
    ):
        markup = builder.diagram(row, index)
        svg = ET.fromstring(re.search(r"<svg\b.*?</svg>", markup, re.S).group())
        width = float(svg.get("viewBox").split()[2])
        # At a 320px figure width, 18-unit labels retain at least 12px type.
        # This budget complements rendered review; it is not font-fit proof.
        assert 18 * 320 / width >= 12


def test_pipeline_copy_completion_precedes_dependent_compute() -> None:
    builder = load_builder()
    rows = builder.parse_visuals(ROOT / "custom-cuda-kernels/reference/visual-plan.md")
    row = next(row for row in rows if row.layout == "pipeline")
    markup = builder.diagram(row, 1)
    svg = ET.fromstring(re.search(r"<svg\b.*?</svg>", markup, re.S).group())
    completion = next(
        node
        for node in svg.findall("text")
        if " ".join(node.itertext()).startswith("Copy ")
    )
    compute_boxes = sorted(svg.findall("rect"), key=lambda node: float(node.get("x")))[
        :2
    ]
    next_compute = max(float(node.get("y")) for node in compute_boxes)
    last_baseline = (
        float(completion.get("y")) + (len(completion.findall("tspan")) - 1) * 21.6
    )
    assert last_baseline < next_compute


def test_multiline_overview_label_is_centered_in_its_slot() -> None:
    builder = load_builder()
    label = ET.fromstring(
        builder.svg_label(
            "Correctness and scoped performance report", 480, 225, width=24
        )
    )
    spans = label.findall("tspan")
    first = float(label.get("y"))
    last = first + (len(spans) - 1) * 21.6
    # A 60-unit funnel band centered at225 needs padding above/below both lines.
    assert first - 18 >= 201
    assert last + 4 <= 249
    assert (first + last) / 2 == pytest.approx(231)


@pytest.mark.parametrize("value", ["word " * 30, "unbroken" * 20])
def test_overview_label_rejects_content_that_cannot_fit(value: str) -> None:
    with pytest.raises(ValueError, match="diagram label"):
        load_builder().svg_label(value, 165, 130)


def test_shallow_slots_reject_three_line_labels() -> None:
    with pytest.raises(ValueError, match="diagram label"):
        load_builder().svg_label("first second third", 480, 85, width=6, max_lines=2)


def test_copy_ready_labels_are_beside_dependency_arrows() -> None:
    path = (
        ROOT
        / "gpu-fundamentals/reference/diagrams/double-buffered-copy-and-compute-timeline.svg"
    )
    svg = ET.fromstring(path.read_text())
    labels = [
        node
        for node in svg.findall(".//text")
        if (node.text or "").startswith("ready ")
    ]
    assert [float(node.get("x")) for node in labels] == [450, 670, 890]


def test_kv_release_connectors_stop_at_the_annotation_edges() -> None:
    path = (
        ROOT
        / "llm-inference/reference/diagrams/paged-kv-blocks-before-release-and-after-reuse.svg"
    )
    svg = ET.fromstring(path.read_text())
    routes = {node.get("d") for node in svg.findall(".//path")}
    assert {
        "M490 270 V305",
        "M835 270 V305",
        "M490 370 V405",
        "M835 370 V405",
    } <= routes
    assert "M490 270 V335 H490 V405" not in routes


def test_grid_assignments_leave_from_box_edges_not_through_labels() -> None:
    path = (
        ROOT
        / "gpu-fundamentals/reference/diagrams/cuda-grid-blocks-sm-assignment-and-warps.svg"
    )
    svg = ET.fromstring(path.read_text())
    routes = {node.get("d") for node in svg.findall(".//path")}
    assert "M175 163 C520 155 500 145 625 155" not in routes
    # A fork in the gutter connects the grid to both SMs, never across a label.
    assert "M600 195 V235 H305 V275" in routes
    assert "M600 235 H895 V275" in routes
    caption = next(
        node
        for node in svg.findall("text")
        if (node.text or "").startswith("Whole blocks")
    )
    assert 255 <= float(caption.get("y")) <= 265


def test_long_topology_notes_are_wrapped_inside_their_cards() -> None:
    path = (
        ROOT
        / "gpu-fundamentals/reference/diagrams/discovered-two-node-communication-path.svg"
    )
    svg = ET.fromstring(path.read_text())
    notes = [
        node
        for node in svg.findall(".//text")
        if "anonymous node" in "".join(node.itertext())
    ]
    assert len(notes) == 2
    assert all(len(node.findall("tspan")) == 2 for node in notes)


def test_forward_graph_caption_is_clear_of_the_loss_arrow() -> None:
    path = (
        ROOT
        / "llm-training/reference/diagrams/forward-backward-optimizer-and-next-training-step.svg"
    )
    svg = ET.fromstring(path.read_text())
    caption = next(node for node in svg.findall("text") if node.text == "Forward graph")
    assert float(caption.get("x")) < 890
    assert float(caption.get("y")) >= 211
