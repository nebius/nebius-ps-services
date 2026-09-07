"""Figures belong to their explanations and fit the reading surface."""

import json
import re
import xml.etree.ElementTree as ET

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder


@pytest.mark.parametrize("course", COURSES)
def test_every_figure_is_inline_in_its_declared_home(course: str) -> None:
    builder = load_builder()
    root = ROOT / course
    document = (root / "index.html").read_text()
    assert 'id="visual-models"' not in document
    assert 'href="#visual-models"' not in document
    lessons = re.findall(r'<section class="lesson".*?</section>', document, re.S)
    placements = []
    for index, visual in enumerate(
        builder.parse_visuals(root / "reference/visual-plan.md"), 1
    ):
        placements.append(
            (
                f"diagram-{index}-{builder.slug(visual.title)}",
                visual.lesson,
                visual.after,
                visual.home,
            )
        )
    for entry in json.loads((root / "reference/visual-manifest.json").read_text())[
        "diagrams"
    ]:
        placements.append(
            (
                "detail-" + builder.slug(entry["path"].split("/")[-1][:-4]),
                entry["lessons"][0],
                entry["after"],
                entry["home"],
            )
        )
    assert document.count("<figure ") == len(placements)
    labs = {
        match[1]: match[0]
        for match in re.finditer(
            r'<article class="lab" id="lab-([^"]+)".*?</article>', document, re.S
        )
    }
    assert sum(item.count("<figure ") for item in [*lessons, *labs.values()]) == len(
        placements
    )
    for target, number, field, home in placements:
        assert document.count(f'id="{target}"') == 1
        owner = (
            lessons[number - 1]
            if home == "lesson"
            else labs[builder.slug(home.removeprefix("lab:"))]
        )
        figure = re.search(rf'<figure\b[^>]*id="{target}"[^>]*>', owner).group()
        before = owner[: owner.index(figure)]
        if home == "lesson":
            stages = [
                name
                for name in re.findall(r'<div class="([^"]+)"', before)
                if name in builder.FIELD_CLASSES.values()
            ]
            assert stages[-1] == builder.FIELD_CLASSES[field]
        else:
            assert re.findall(r"<h4>(.*?)</h4>", before)[-1] == field


@pytest.mark.parametrize("course", COURSES)
def test_teaching_labels_have_no_trailing_period(course: str) -> None:
    builder = load_builder()
    root = ROOT / course
    source = (root / "COURSE.md").read_text()
    document = (root / "index.html").read_text()
    for field in builder.LESSON_FIELDS:
        assert f"**{field}.**" not in source
        assert f"**{field}**" in source
        assert f"<strong>{field}.</strong>" not in document
        assert f"<strong>{field}</strong>" in document
    assert not re.search(r"<figcaption><strong>[^<]+\.</strong>", document)


def test_wide_layout_fits_diagrams_instead_of_forcing_pan() -> None:
    css = (ROOT / "tools/course.css").read_text()
    assert "max-width: 1800px" in css
    assert "--reading-width: 88ch" in css
    assert "min-width: 650px" not in css
    assert "min-width: 1200px" not in css
    assert "height: auto" in css
    assert ".diagram-scroll" not in css
    assert "overflow-x: auto" not in css


def test_invalid_overview_placement_is_rejected(tmp_path) -> None:
    builder = load_builder()
    path = tmp_path / "visual-plan.md"
    path.write_text(
        "| Title | First stage | Second stage | Third stage | Explanation | Lesson | After | Layout | Home |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| Example | A | B | C | Explanation | 0 | Mechanism | flow | lesson |\n"
    )
    with pytest.raises(ValueError, match="placement"):
        builder.parse_visuals(path)


@pytest.mark.parametrize("course", COURSES)
def test_detailed_diagrams_preserve_native_accessible_descriptions(course: str) -> None:
    builder = load_builder()
    root = ROOT / course
    count = len(builder.parse_course(root / "COURSE.md")[2])
    for entry in builder.detailed_visuals(root, count):
        markup = builder.detailed_diagram_markup(entry)
        svg = ET.fromstring(entry["svg"])
        ids = {node.get("id") for node in svg.iter()}
        assert set(svg.get("aria-labelledby").split()) <= ids
        assert svg.find("title").text
        assert svg.find("desc").text
        assert svg.findall(".//text")
        assert entry["svg"] in markup
        assert "diagram-reading" not in markup


@pytest.mark.parametrize("course", COURSES)
def test_open_diagram_connectors_do_not_fill_as_polygons(course: str) -> None:
    def visit(node, inherited_fill="black", inherited_stroke="none"):
        fill = node.get("fill", inherited_fill)
        stroke = node.get("stroke", inherited_stroke)
        if node.tag == "path" and "z" not in node.get("d", "").lower():
            if stroke != "none":
                assert fill == "none", f"open connector needs fill=none: {node.attrib}"
        for child in node:
            visit(child, fill, stroke)

    for path in (ROOT / course / "reference/diagrams").glob("*.svg"):
        visit(ET.fromstring(path.read_text()))


@pytest.mark.parametrize(
    "changes",
    [
        {"lessons": []},
        {"lessons": [0]},
        {"lessons": [3]},
        {"lessons": [True]},
        {"lessons": [1, 1]},
        {"after": "Unknown stage"},
    ],
)
def test_invalid_detailed_placements_fail_fast(tmp_path, changes) -> None:
    builder = load_builder()
    reference = tmp_path / "reference"
    reference.mkdir()
    entry = {
        "path": "reference/example.svg",
        "title": "Example",
        "lessons": [1],
        "after": "Mechanism",
        "home": "lesson",
    }
    entry.update(changes)
    (reference / "visual-manifest.json").write_text(json.dumps({"diagrams": [entry]}))
    with pytest.raises(ValueError, match="placement"):
        builder.detailed_visuals(tmp_path, 2)


def test_duplicate_detailed_diagram_is_rejected(tmp_path) -> None:
    builder = load_builder()
    reference = tmp_path / "reference"
    reference.mkdir()
    (reference / "example.svg").write_text("<svg><desc>Example</desc></svg>")
    entry = {
        "path": "reference/example.svg",
        "title": "Example",
        "lessons": [1],
        "after": "Mechanism",
        "home": "lesson",
    }
    (reference / "visual-manifest.json").write_text(
        json.dumps({"diagrams": [entry, entry]})
    )
    with pytest.raises(ValueError, match="duplicate"):
        builder.detailed_visuals(tmp_path, 2)


def test_overview_placement_cannot_target_missing_lesson(monkeypatch) -> None:
    builder = load_builder()
    invalid = builder.OverviewDiagram(
        "Example", "A", "B", "C", "Explanation", 999, "Mechanism", "flow", "lesson"
    )
    monkeypatch.setattr(builder, "parse_visuals", lambda path: [invalid])
    with pytest.raises(ValueError, match="missing lesson"):
        builder.render_course("gpu-fundamentals")
