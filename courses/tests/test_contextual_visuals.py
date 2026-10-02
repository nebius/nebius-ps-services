"""Figures belong to their explanations and fit the reading surface."""

from course_builder import (
    config as cb_config,
    content as cb_content,
    markdown as cb_markdown,
    metadata as cb_metadata,
    pages as cb_pages,
    visuals as cb_visuals,
)
import json
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET

import pytest
from test_course_content_contract import COURSES, ROOT


class FigurePredecessors(HTMLParser):
    """Record each figure's preceding sibling without using the course renderer."""

    def __init__(self):
        super().__init__()
        self.stack = [{"text": [], "last": None}]
        self.previous = {}

    def handle_starttag(self, tag, attrs):
        if tag == "figure":
            self.previous[dict(attrs).get("id")] = self.stack[-1]["last"]
        self.stack.append({"text": [], "last": None, "tag": tag})
        if tag in {"meta", "link", "img", "br", "hr", "input", "source", "wbr"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.stack.pop()
        assert node["tag"] == tag
        text = " ".join("".join(node["text"]).split())
        self.stack[-1]["text"].append(text + " ")
        self.stack[-1]["last"] = (tag, text)

    def handle_data(self, data):
        self.stack[-1]["text"].append(data)


@pytest.mark.parametrize("course", cb_config.COURSES)
def test_all_authored_figures_immediately_follow_their_explanation(course):
    """Compare canonical adjacent blocks with actual published predecessors."""
    root = ROOT / course
    actual = FigurePredecessors()
    actual.feed((root / "index.html").read_text())
    expected = {}
    sources = [
        root / "COURSE.md",
        *root.glob("reference/performance-tools.md"),
        *root.glob("reference/labs/*.md"),
    ]
    for source in sources:
        text = source.read_text()
        links = {
            destination: "#reference"
            for destination in re.findall(r"\[[^]]+\]\(([^)]+)\)", text)
        }

        def figure(path, title):
            if path.startswith("#"):
                target = path[1:]
            elif path.startswith("diagrams/tools-"):
                target = path.rsplit("/", 1)[-1][:-4]
            else:
                target = "detail-" + cb_markdown.slug(path.rsplit("/", 1)[-1][:-4])
            return f'<figure id="{target}"></figure>'

        parsed = FigurePredecessors()
        parsed.feed(cb_markdown.block(text, links, figure=figure))
        assert not expected.keys() & parsed.previous.keys()
        expected.update(parsed.previous)
    assert expected == actual.previous
    assert all(
        previous and previous[0] in {"p", "ul", "ol", "div", "pre"}
        for previous in expected.values()
    )


@pytest.mark.parametrize("course", COURSES)
def test_every_figure_is_inline_in_its_declared_home(course: str) -> None:
    root = ROOT / course
    document = (root / "index.html").read_text()
    assert 'id="visual-models"' not in document
    assert 'href="#visual-models"' not in document
    lessons = sorted(
        re.findall(r'<section class="lesson".*?</section>', document, re.DOTALL),
        key=lambda block: int(re.search(r'data-lesson-number="([0-9]+)"', block)[1]),
    )
    placements = []
    for index, visual in enumerate(
        cb_metadata.parse_visuals(root / "reference/visual-plan.md"), 1
    ):
        placements.append(
            (
                f"diagram-{index}-{cb_markdown.slug(visual.title)}",
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
                "detail-" + cb_markdown.slug(entry["path"].split("/")[-1][:-4]),
                entry["lessons"][0],
                entry["after"],
                entry["home"],
            )
        )
    primer = re.search(
        r'<section id="using-gpu-performance-tools".*?</section>',
        document,
        re.DOTALL,
    ).group()
    tool_figures = re.findall(
        r"(?m)^!\[[^]]+\]\(diagrams/(tools-[a-z0-9-]+)\.svg\)$",
        (root / "reference/performance-tools.md").read_text(),
    )
    assert primer.count("<figure ") == 1 + len(tool_figures)
    for target in tool_figures:
        assert primer.count(f'id="{target}"') == 1
    assert primer.count('id="tools-measurement-loop"') == 1
    explanation = primer.split('class="tools-field tools-how-it-works"')[1].split(
        'class="tools-field tools-practice-labs"'
    )[0]
    assert 'id="tools-measurement-loop"' in explanation
    assert document.count("<figure ") == len(placements) + 1 + len(tool_figures)
    labs = {
        match[1]: match[0]
        for match in re.finditer(
            r'<article class="lab" id="lab-([^"]+)".*?</article>', document, re.DOTALL
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
            else labs[cb_markdown.slug(home.removeprefix("lab:"))]
        )
        figure = re.search(rf'<figure\b[^>]*id="{target}"[^>]*>', owner).group()
        before = owner[: owner.index(figure)]
        if home == "lesson":
            stages = [
                name
                for name in re.findall(r'<div class="([^"]+)"', before)
                if name in cb_config.FIELD_CLASSES.values()
            ]
            assert stages[-1] == cb_config.FIELD_CLASSES[field]
        else:
            assert re.findall(r"<h4>(.*?)</h4>", before)[-1] == field


@pytest.mark.parametrize("course", COURSES)
def test_teaching_labels_have_no_trailing_period(course: str) -> None:
    root = ROOT / course
    source = (root / "COURSE.md").read_text()
    document = (root / "index.html").read_text()
    for field in cb_config.LESSON_FIELDS:
        assert f"**{field}.**" not in source
        assert f"**{field}**" in source
        assert f"<strong>{field}.</strong>" not in document
        assert f"<h3>{field}</h3>" in document
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
    path = tmp_path / "visual-plan.md"
    path.write_text(
        "| Title | First stage | Second stage | Third stage | Explanation | Lesson | After | Layout | Home |\n"
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
        "| Example | A | B | C | Explanation | 0 | Mechanism | flow | lesson |\n"
    )
    with pytest.raises(ValueError, match="placement"):
        cb_metadata.parse_visuals(path)


@pytest.mark.parametrize("course", COURSES)
def test_detailed_diagrams_preserve_native_accessible_descriptions(course: str) -> None:
    root = ROOT / course
    count = len(cb_metadata.parse_course(root / "COURSE.md")[2])
    for entry in cb_visuals.detailed_visuals(root, count):
        markup = cb_visuals.detailed_diagram_markup(entry)
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
        if (
            node.tag == "path"
            and "z" not in node.get("d", "").lower()
            and stroke != "none"
        ):
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
        cb_visuals.detailed_visuals(tmp_path, 2)


def test_duplicate_detailed_diagram_is_rejected(tmp_path) -> None:
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
        cb_visuals.detailed_visuals(tmp_path, 2)


def test_overview_placement_cannot_target_missing_lesson(monkeypatch) -> None:
    invalid = cb_metadata.OverviewDiagram(
        "Example", "A", "B", "C", "Explanation", 999, "Mechanism", "flow", "lesson"
    )
    monkeypatch.setattr(cb_pages, "parse_visuals", lambda path: [invalid])
    with pytest.raises(ValueError, match="missing lesson"):
        cb_pages.render_course("gpu-fundamentals")


def lesson_figure_example():
    entry = {
        "path": "reference/diagrams/example.svg",
        "title": "Example",
        "lessons": [1],
        "after": "How it works",
        "home": "lesson",
        "id": "detail-example",
        "svg": "<svg></svg>",
        "description": "A registered example",
    }
    marker = "![Example](reference/diagrams/example.svg)"
    return entry, marker, cb_visuals.detailed_diagram_markup(entry)


def test_authored_figure_consumed_once_at_exact_lesson_position() -> None:
    entry, marker, figure = lesson_figure_example()
    figures = {"How it works": [dict(entry, markup=figure)]}
    lesson = {"title": "Example", "How it works": f"Before.\n\n{marker}\n\nAfter."}
    rendered = cb_content.lesson_markup(lesson, 1, figures=figures)
    assert rendered.index("Before.") < rendered.index(figure) < rendered.index("After.")
    assert rendered.count(figure) == 1
    assert figures["How it works"][0]["markup"] == figure  # Inputs are unchanged.


@pytest.mark.parametrize(
    "case",
    ["unknown", "wrong-home", "wrong-title", "duplicate", "wrong-field", "missing"],
)
def test_authored_figure_rejects_invalid_placement(case: str) -> None:
    entry, marker, figure = lesson_figure_example()
    registry = [dict(entry, markup=figure)]
    if case == "unknown":
        marker = marker.replace("example.svg", "missing.svg")
    elif case == "wrong-home":
        registry = []
    elif case == "wrong-title":
        marker = marker.replace("[Example]", "[Other title]")
    elif case == "duplicate":
        marker += "\n\n" + marker
    elif case == "missing":
        marker = "Explanation without its registered diagram."
    field = "Objective" if case == "wrong-field" else "How it works"
    with pytest.raises(ValueError, match="figure"):
        cb_content.lesson_markup(
            {"title": "Example", field: marker},
            1,
            figures={"How it works": registry},
        )


@pytest.mark.parametrize(
    "explanation",
    [
        "Explanation.",
        "- First point\n- Second point",
        "| Input | Output |\n| --- | --- |\n| 1 | 2 |",
        "```text\nworked example\n```",
    ],
)
@pytest.mark.parametrize(
    "path",
    ["#diagram-1-example", "reference/diagrams/example.svg", "../diagrams/example.svg"],
)
def test_diagram_block_preserves_paragraph_adjacency_and_rejects_omissions(
    path, explanation
):
    figure = '<figure id="example"><svg></svg></figure>'
    entry = dict(path=path, title="Example", markup=figure)
    marker = f"![Example]({path})"
    source = f"{explanation}\n\n{marker}\n\n### Another topic\n\nLater text."
    rendered = cb_content.diagram_block(source, [entry])
    assert cb_markdown.block(explanation) + figure in rendered
    assert rendered.index(figure) < rendered.index("Another topic")
    with pytest.raises(ValueError, match="missing authored figure"):
        cb_content.diagram_block(source.replace(marker, ""), [entry])
    with pytest.raises(ValueError, match="figure"):
        cb_content.diagram_block(source + "\n\n" + marker, [entry])


def test_matrix_example_figure_is_inside_work_hierarchy() -> None:
    document = (ROOT / "gpu-fundamentals/index.html").read_text()
    start = document.index('id="work-hierarchy-grid-blocks-warps-threads"')
    figure = document.index('id="detail-matrix-multiplication-thread-mapping"')
    limits = document.index("Logical work or residency limit", start)
    end = document.index('id="execution-and-dependencies"', start)
    assert start < figure < limits < end
