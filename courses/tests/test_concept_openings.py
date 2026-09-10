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
        field = "How it works"
        introduction = lesson[field]
        assert len(introduction.split()) >= (350 if number == 1 else 180)
        assert list(lesson).index("Objective") < list(lesson).index(field)
        rendered = builder.lesson_markup(lesson, number)
        assert rendered.index("<strong>Objective</strong>") < rendered.index(
            f"<strong>{field}</strong>"
        )
        assert builder.block(introduction) in rendered
    # Publication links may be rewritten; bind complete visible prose to its own lesson.
    with load_lab("tools/validate_course_template.py") as module:
        parser = module.Parser()
        parser.feed(page)
        canonical = [
            (item["title"], {k: v for k, v in item.items() if k != "title"}, 0)
            for item in lessons
        ]
        module.validate_rendered_openings(parser, canonical)


@pytest.mark.parametrize("course", COURSES)
def test_standalone_validator_rejects_missing_short_or_late_openings(course):
    with load_lab(f"{course}/tools/validate_course.py") as module:
        valid = {
            "Objective": "Apply it",
            "How it works": "A definition. " * 100,
            "Practice labs": "Try it",
            "Mental model": "Remember the relationship",
        }
        module.validate_concept_opening("Example", valid)
        for invalid in (
            {"Objective": "Apply it"},
            {**valid, "How it works": "It makes things faster."},
            {
                "How it works": valid["How it works"],
                "Objective": "Apply it",
                "Practice labs": "Try it",
                "Mental model": "Remember",
            },
            {**valid, "What it is": "Old field"},
            {
                key: valid[key]
                for key in (
                    "Objective",
                    "How it works",
                    "Mental model",
                    "Practice labs",
                )
            },
        ):
            with pytest.raises(SystemExit, match="opening"):
                module.validate_concept_opening("Example", invalid)


def test_cuda_graph_definition_precedes_capture_restrictions():
    builder = load_builder()
    lessons = builder.parse_course(ROOT / "gpu-optimizations/COURSE.md")[2]
    introduction = lessons[4]["How it works"].lower()
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
            '<section class="lesson" id="example"><div class="how-it-works">Definition<figure id="detail-example"></figure></div></section>'
        )
        assert parser.figures["detail-example"] == ("lesson:1", "how-it-works")
        assert parser.lesson_first_fields == ["how-it-works"]
        late = module.Parser()
        late.feed(
            '<section class="lesson" id="late"><div class="lesson-outcome">Apply it</div><div class="how-it-works">Definition</div></section>'
        )
        assert late.lesson_first_fields == ["lesson-outcome"]


@pytest.mark.parametrize("gap", ["\n", "\n\n"])
def test_opening_table_parity_preserves_every_cell_and_literal_pipes(gap):
    builder = load_builder()
    introduction = (
        "A | B stays literal.\n\n"
        "| Resource | Count |\n| --- | --- |\n| SMs | 132 |\n| L2 | 50 MB |"
        f"{gap}"
        "```text\n| Code | Value |\n| --- | --- |\n| A | B |\n```"
    )
    rendered = builder.block(introduction)
    canonical = [("Architecture", {"How it works": introduction}, 0)]
    with load_lab("tools/validate_course_template.py") as module:

        def check(body):
            parser = module.Parser()
            parser.feed(
                '<section class="lesson"><div class="how-it-works">'
                f"<p><strong>How it works</strong></p>{body}</div></section>"
            )
            module.validate_rendered_openings(parser, canonical)

        check(rendered)
        for corrupt in (
            rendered.replace("<td>132</td>", "<td>114</td>"),
            rendered.replace("<td>50 MB</td>", ""),
            rendered.replace("A | B stays literal.", "A B stays literal."),
            rendered.replace("| A | B |", "A B"),
        ):
            with pytest.raises(SystemExit, match="opening.*narrative"):
                check(corrupt)


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
            re.finditer(r'<div class="how-it-works">(.*?)</div>', page, re.S)
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


@pytest.mark.parametrize("course", COURSES)
def test_every_lesson_renders_a_core_diagram_inside_its_explanation(course):
    page = (ROOT / course / "index.html").read_text()
    with load_lab("tools/validate_course_template.py") as module:
        parser = module.Parser()
        parser.feed(page)
        assert not parser.errors
        module.validate_lesson_structure(parser)
        assert len(parser.lesson_core_diagrams) == len(
            load_builder().parse_course(ROOT / course / "COURSE.md")[2]
        )
        assert all(count >= 1 for count in parser.lesson_core_diagrams)


@pytest.mark.parametrize(
    "fault", ["missing", "outside", "no-svg", "order", "duplicate"]
)
def test_diagram_and_order_checks_are_local_to_each_lesson(fault):
    figure = (
        '<figure id="core"><svg role="img" aria-labelledby="t d">'
        '<title id="t">Core flow</title><desc id="d">Input becomes output.</desc>'
        "</svg></figure>"
    )
    objective = '<div class="lesson-outcome">Objective</div>'
    explanation = '<div class="how-it-works">Definition' + figure + "</div>"
    practice = '<div class="practice-links">Practice</div>'
    mental = '<div class="mental-model">Summary</div>'
    document = (
        '<section class="lesson">'
        + objective
        + explanation
        + practice
        + mental
        + "</section>"
    )
    with load_lab("tools/validate_course_template.py") as module:
        valid = module.Parser()
        valid.feed(document)
        module.validate_lesson_structure(valid)
        if fault == "missing":
            document = document.replace(figure, "")
        elif fault == "outside":
            document = document.replace(figure, "").replace(practice, figure + practice)
        elif fault == "no-svg":
            document = document.replace(
                figure, '<figure id="core">Caption only</figure>'
            )
        elif fault == "order":
            document = document.replace(practice + mental, mental + practice)
        else:
            document = document.replace(mental, mental + mental)
        # Extra figures in a different lesson cannot satisfy this lesson's requirement.
        extra = (
            '<section class="lesson">'
            + objective
            + explanation.replace('id="core"', 'id="other"')
            .replace('id="t"', 'id="t2"')
            .replace('id="d"', 'id="d2"')
            + practice
            + mental
            + "</section>"
        )
        parser = module.Parser()
        parser.feed(document + extra)
        with pytest.raises(SystemExit, match="Lesson 1"):
            module.validate_lesson_structure(parser)


def test_explanation_list_parity_keeps_fenced_list_literals():
    source = "Steps:\n\n- Read the input.\n- Compute the result.\n\n```text\n- literal marker\n```"
    builder = load_builder()
    with load_lab("tools/validate_course_template.py") as module:
        canonical = [("Example", {"How it works": source}, 0)]
        rendered = (
            '<section class="lesson"><div class="how-it-works"><strong>How it works</strong> '
            + builder.block(source)
            + "</div></section>"
        )
        parser = module.Parser()
        parser.feed(rendered)
        module.validate_rendered_openings(parser, canonical)
        for missing in ("Compute the result.", "- literal marker"):
            parser = module.Parser()
            parser.feed(rendered.replace(missing, "omitted"))
            with pytest.raises(SystemExit, match="narrative differs"):
                module.validate_rendered_openings(parser, canonical)


def test_cache_levels_and_vector_norms_have_distinct_definitions():
    for course in ("gpu-fundamentals", "llm-training"):
        text = (ROOT / course / "COURSE.md").read_text()
        assert "Euclidean (L2)" in text
        assert "level-two cache) norm" not in text
        assert "level-two cache) error" not in text
    foundation = (ROOT / "gpu-fundamentals/COURSE.md").read_text()
    assert "Parallel Thread Execution (PTX)" in foundation
    assert "DMA-BUF" in foundation and "(DMA)-BUF" not in foundation
