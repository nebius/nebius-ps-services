"""Every lesson defines its concept before asking learners to apply it."""

import re

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder
from test_course_review_fixes import load_lab


@pytest.mark.parametrize("course", COURSES)
def test_every_lesson_has_a_complete_definition_first(course):
    builder = load_builder()
    lessons = builder.parse_course(ROOT / course / "COURSE.md")[2]
    page = (ROOT / course / "index.html").read_text()
    for number, lesson in enumerate(lessons, 1):
        field = "Start here" if number == 1 else "What it is"
        introduction = lesson[field]
        assert len(introduction.split()) >= (350 if number == 1 else 60)
        assert list(lesson).index(field) < list(lesson).index("Objective")
        rendered = builder.lesson_markup(lesson, number)
        assert rendered.index(f"<strong>{field}</strong>") < rendered.index(
            "<strong>Objective</strong>"
        )
        assert builder.block(introduction) in rendered
        assert builder.block(introduction) in page


@pytest.mark.parametrize("course", COURSES)
def test_standalone_validator_rejects_missing_short_or_late_openings(course):
    with load_lab(f"{course}/tools/validate_course.py") as module:
        valid = {"What it is": "A definition. " * 35, "Objective": "Apply it"}
        module.validate_concept_opening("Example", valid)
        for invalid in (
            {"Objective": "Apply it"},
            {"What it is": "It makes things faster.", "Objective": "Apply it"},
            {"Objective": "Apply it", "What it is": valid["What it is"]},
            {"Start here": "Too brief.", "Objective": "Apply it"},
            {"Start here": "A definition. " * 180, **valid},
        ):
            with pytest.raises(SystemExit, match="opening"):
                module.validate_concept_opening("Example", invalid)


def test_cuda_graph_definition_precedes_capture_restrictions():
    builder = load_builder()
    lessons = builder.parse_course(ROOT / "gpu-optimizations/COURSE.md")[2]
    introduction = lessons[4]["What it is"].lower()
    for term in (
        "operations",
        "dependencies",
        "capture",
        "replay",
        "fusion",
        "compilation",
    ):
        assert term in introduction
    assert "not" in introduction


def test_definition_field_can_own_a_contextual_diagram():
    with load_lab("tools/validate_course_template.py") as module:
        parser = module.Parser()
        parser.feed(
            '<section class="lesson" id="example"><div class="concept-introduction">Definition</div><figure id="detail-example"></figure></section>'
        )
        assert parser.figures["detail-example"] == ("lesson:1", "concept-introduction")
        assert parser.lesson_first_fields == ["concept-introduction"]
        late = module.Parser()
        late.feed(
            '<section class="lesson" id="late"><div class="lesson-outcome">Apply it</div><div class="concept-introduction">Definition</div></section>'
        )
        assert late.lesson_first_fields == ["lesson-outcome"]


@pytest.mark.parametrize("course", COURSES)
def test_standalone_opening_parity_is_bound_to_its_own_lesson(course):
    builder = load_builder()
    lessons = builder.parse_course(ROOT / course / "COURSE.md")[2]
    page = (ROOT / course / "index.html").read_text()
    with load_lab(f"{course}/tools/validate_course.py") as module:
        canonical = [
            (item["title"], {k: v for k, v in item.items() if k != "title"}, 0)
            for item in lessons
        ]
        parser = module.Parser()
        parser.feed(page)
        module.validate_rendered_openings(parser, canonical)
        openings = list(
            re.finditer(r'<div class="concept-introduction">(.*?)</div>', page, re.S)
        )
        first, second = openings[:2]
        swapped = (
            page[: first.start(1)]
            + second[1]
            + page[first.end(1) : second.start(1)]
            + first[1]
            + page[second.end(1) :]
        )
        truncated = page[: first.start(1)] + "Definition omitted" + page[first.end(1) :]
        for invalid in (swapped, truncated):
            parser = module.Parser()
            parser.feed(invalid)
            with pytest.raises(SystemExit, match="opening.*narrative"):
                module.validate_rendered_openings(parser, canonical)
