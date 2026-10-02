"""One source-complete course glossary across conceptual, text and lab profiles."""

from course_builder import config as cb_config, content as cb_content, metadata as cb_metadata
import re

import pytest
from test_course_content_contract import ROOT
from test_numerical_acceptance import load_lab


@pytest.mark.parametrize("course", cb_config.COURSES)
def test_exactly_one_course_glossary_preserves_every_definition(course):
    document = (ROOT / course / "index.html").read_text()
    source = (ROOT / course / "GLOSSARY.md").read_text()
    assert document.count("<h2>Glossary</h2>") == 1
    assert not re.search(
        r"<h[1-6]>Glossary</h[1-6]>", document.replace("<h2>Glossary</h2>", "")
    )
    assert (
        "lesson-glossary" not in document and "tools-glossary" not in document
    )
    assert (
        "lesson-next-steps" not in document
        and "tools-where-to-go-next" not in document
    )
    assert document.count("<h2>Where to Go Next</h2>") == 1
    assert cb_content.course_glossary_markup(ROOT / course) in document
    with load_lab("tools/validate_course_template.py") as validator:
        validator.validate_course_glossary(document, source)
    lessons = re.findall(
        r'<section class="lesson"[^>]*>(.*?)</section>', document, re.S
    )
    assert bool(lessons) == (course != "advanced-gpu-communication")
    for lesson in lessons:
        headings = re.findall(r'<div class="[^"]+"><h3>(.*?)</h3>', lesson)
        expected = ["Objective", "How it works", "Practice", "Mental model"]
        assert headings in (expected, [*expected, "References"])
    for path in (
        ROOT / course / "COURSE.md",
        ROOT / course / "reference/performance-tools.md",
    ):
        if path.exists():
            assert not re.search(
                r"(?m)^(?:#{1,6} |\*\*)(?:Glossary|Where to Go Next)",
                path.read_text(),
            )


@pytest.mark.parametrize(
    "content",
    [
        "",
        "- **Zebra** — Z.\n- **Alpha** — A.",
        "- **Term** — One.\n- **term** — Two.",
        "- **Term** — ",
        "Unstructured paragraph.",
    ],
)
def test_malformed_glossaries_fail_in_builder_and_standalone_validator(content):
    with pytest.raises(ValueError, match="Glossary"):
        cb_content.glossary_markup(content)
    with load_lab("tools/validate_course_template.py") as validator:
        with pytest.raises(SystemExit, match="Glossary"):
            validator.glossary_entries(content)


def test_glossary_escapes_content_and_preserves_code():
    rendered = cb_content.glossary_markup(
        "- **`buffer_id`** — Use `buffer_id < limit` and <script> literally."
    )
    assert "<dt><code>buffer_id</code></dt>" in rendered
    assert "<code>buffer_id &lt; limit</code>" in rendered
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered


@pytest.mark.parametrize("profile", ["gpu-fundamentals", "soperator"])
def test_optional_references_follow_mental_model_and_local_appendices_fail(
    profile, tmp_path, authored_figure_placeholders
):
    source = (ROOT / profile / "COURSE.md").read_text()
    heading = (
        (lambda name: f"### {name}")
        if profile == "soperator"
        else (lambda name: f"**{name}**")
    )
    boundary = re.search(r"^## 2\.", source, re.M).start()
    path = tmp_path / "COURSE.md"
    parse = (
        cb_metadata.parse_text_course
        if profile == "soperator"
        else cb_metadata.parse_course
    )
    path.write_text(
        source[:boundary].rstrip()
        + f"\n\n{heading('References')}\n\n1. A public source.\n\n"
        + source[boundary:]
    )
    lesson = parse(path)[2][0]
    rendered = cb_content.lesson_markup(lesson, 1, {destination: "#practice" for value in lesson.values() for destination in re.findall(r"\[[^]]+\]\(([^)]+)\)", value)}, figures=authored_figure_placeholders(lesson))
    assert cb_metadata.valid_lesson_fields(list(lesson)[1:])
    assert rendered.index('class="mental-model"') < rendered.index(
        'class="lesson-references"'
    )
    for field in ("References", "Glossary", "Where to Go Next"):
        marker = heading("Mental model")
        invalid = source.replace(
            marker, f"{heading(field)}\n\n- **Term** — Meaning.\n\n{marker}", 1
        )
        path.write_text(invalid)
        if profile == "soperator" or field in {"Glossary", "Where to Go Next"}:
            with pytest.raises(ValueError, match="ordered|unsupported"):
                parse(path)
        else:
            assert not cb_metadata.valid_lesson_fields(list(parse(path)[2][0])[1:])


@pytest.mark.parametrize("course", cb_config.COURSES)
def test_validator_rejects_missing_duplicate_local_or_changed_glossary(course):
    document = (ROOT / course / "index.html").read_text()
    source = (ROOT / course / "GLOSSARY.md").read_text()
    section = re.search(
        r'<section id="guide-glossary".*?</section>', document, re.S
    )[0]
    changed = re.sub(
        r"<dd>.*?</dd>",
        "<dd>Changed meaning.</dd>",
        section,
        count=1,
        flags=re.S,
    )
    malformed = section.replace("</dl>", "<dd>Orphan definition.</dd></dl>")
    local = '<div class="lesson-glossary"><h3>Glossary</h3><dl><dt>Term</dt><dd>Meaning.</dd></dl></div>'
    cases = [
        document.replace(section, ""),
        document.replace(section, section + section),
        document.replace(section, changed),
        document.replace(section, malformed),
        document.replace("</main>", local + "</main>"),
        document.replace("</main>", "<h3>Where to Go Next</h3></main>"),
        document.replace(section, "<div>" + section + "</div>"),
        document.replace(
            '<section id="next-steps">', '<div><section id="next-steps">'
        ).replace(section, "</div>" + section),
        document.replace("</main>", "<h4><em>Glossary</em></h4></main>"),
        document.replace('href="#guide-glossary"', 'href="#main"'),
        document.replace(section, "").replace(
            '<section id="course-overview">',
            section + '<section id="course-overview">',
        ),
    ]
    with load_lab("tools/validate_course_template.py") as validator:
        for invalid in cases:
            with pytest.raises(SystemExit, match="Glossary"):
                validator.validate_course_glossary(invalid, source)
