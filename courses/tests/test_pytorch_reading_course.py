"""Visual reading courses preserve source truth without gaining lab machinery."""

import ast
import copy
import html
import json
import re

import pytest

from course_builder import config, metadata, pages, visuals
from validate_text_course import validate, validate_document


SLUG = "pytorch-gpu-performance-engineering"
COURSE = config.ROOT / SLUG


def contract():
    return json.loads((COURSE / "reference/course.json").read_text())


def test_complete_reading_route_and_exact_source_publication():
    data = contract()
    title, preamble, lessons = metadata.parse_text_course(
        COURSE / "COURSE.md", "lessons-only"
    )
    assert title == "PyTorch for GPU Performance Engineering"
    assert len(lessons) == 18
    assert data["estimated_guided_hours"] == 3
    assert data["lessons"] == [
        {"id": number, "title": item["title"]} for number, item in enumerate(lessons, 1)
    ]
    document = pages.render_course(SLUG)
    assert document == (COURSE / "index.html").read_text()
    assert pages.block(preamble) in document
    assert 'class="practice-links"' not in document
    assert 'id="labs"' not in document and "-lab-results.zip" not in document
    figures = visuals.detailed_visuals(COURSE, len(lessons))
    assert len(figures) >= len(lessons)
    for number, lesson in enumerate(lessons, 1):
        assert tuple(lesson) == ("title", "Objective", "How it works", "Mental model")
        section = re.search(
            rf'<section class="lesson" id="lesson-{number}-.*?</section>',
            document,
            re.S,
        )[0]
        lesson_figures = [item for item in figures if item["lessons"] == [number]]
        assert lesson_figures
        for figure in lesson_figures:
            assert document.count(figure["svg"]) == 1
            assert figure["svg"] in section
        assert lesson["How it works"].count("**Performance connection:**") == 1
        snippets = re.findall(r"```python\n(.*?)\n```", lesson["How it works"], re.S)
        assert snippets, lesson["title"]
        for snippet in snippets:
            ast.parse(snippet)
            assert "#" in snippet
            assert html.escape(snippet) in section
        # Render each complete field, retaining every paragraph, table and example.
        for field in ("Objective", "Mental model"):
            assert pages.block(lesson[field]) in section
        rendered = pages.diagram_block(
            lesson["How it works"],
            [
                dict(
                    path=figure["path"],
                    title=figure["title"],
                    markup=visuals.detailed_diagram_markup(figure),
                )
                for figure in lesson_figures
            ],
            prefix=f"lesson-{number}-",
        )
        assert rendered in section
    validate(SLUG)


@pytest.mark.parametrize(
    "field,value",
    [
        ("labs", []),
        ("runtime", {}),
        ("estimated_guided_hours", True),
        ("estimated_guided_hours", float("nan")),
        ("estimated_guided_hours", 0),
        ("profile", "text-only"),
        ("slug", "gpu-fundamentals"),
        ("visual_manifest", "../other.json"),
        ("lessons", []),
        ("lessons", [{"id": True, "title": "Tensor"}]),
        ("lessons", [{"id": 2, "title": "Tensor"}]),
        ("lessons", [{"id": 1, "title": "Tensor"}, {"id": 2, "title": "Tensor"}]),
    ],
)
def test_invalid_or_executable_metadata_is_rejected(field, value):
    data = contract()
    data[field] = value
    with pytest.raises(ValueError):
        metadata.lessons_course_metadata(COURSE, data)


def test_fixed_reading_profiles_remain_fixed():
    with pytest.raises(ValueError):
        metadata.reference_course_metadata(contract())
    with pytest.raises(ValueError):
        metadata.text_course_metadata(contract())


@pytest.mark.parametrize(
    "change",
    [
        lambda text: text.replace(
            "### Mental model", "### Practice\n\nDo an exercise.\n\n### Mental model", 1
        ),
        lambda text: text.replace("### Objective", "### Unknown", 1),
        lambda text: text.replace("## 2.", "## 3.", 1),
    ],
)
def test_reading_structure_rejects_practice_unknown_fields_and_wrong_order(
    tmp_path, change
):
    source = tmp_path / "COURSE.md"
    source.write_text(change((COURSE / "COURSE.md").read_text()))
    with pytest.raises(ValueError):
        metadata.parse_text_course(source, "lessons-only")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "shared", "wrong-home"])
def test_every_lesson_has_its_own_contextual_figure(monkeypatch, mutation):
    entries = copy.deepcopy(
        visuals.detailed_visuals(COURSE, len(contract()["lessons"]))
    )
    if mutation == "missing":
        # Extra figures elsewhere must not mask a lesson without any figure.
        entries = [entry for entry in entries if entry["lessons"] != [1]]
    elif mutation == "duplicate":
        entries.append(entries[0])
    elif mutation == "shared":
        entries[0]["lessons"] = [1, 2]
    else:
        entries[0]["home"] = "lab:1_example"
    monkeypatch.setattr(pages, "detailed_visuals", lambda *_: entries)
    with pytest.raises(ValueError):
        pages.render_text_course(COURSE, contract())


def test_course_identity_mismatch_fails_before_rendering():
    data = contract()
    data["lessons"][1]["title"] = "A different title"
    with pytest.raises(ValueError, match="identities"):
        pages.render_text_course(COURSE, data)


def test_multiple_contextual_figures_keep_individual_homes_and_notation():
    document = pages.render_course(SLUG)
    figures = visuals.detailed_visuals(COURSE, len(contract()["lessons"]))
    for number in (1, 2):
        owned = [figure for figure in figures if figure["lessons"] == [number]]
        assert len(owned) >= 2
        section = re.search(
            rf'<section class="lesson" id="lesson-{number}-.*?</section>',
            document,
            re.S,
        )[0]
        for figure in owned:
            assert section.count(figure["svg"]) == 1
    for name in ("shape", "dtype", "device", "torch.rand", "torch.randn", "nn.Linear"):
        assert f"<strong><code>{name}</code></strong>" in document
    assert "<strong>Performance connection:</strong>" in document
    for meaning in ("batch", "position", "feature", "gradient"):
        # Glossary entry headings are separate from explanatory strong text.
        assert f"<strong>{meaning}</strong>" not in document
    assert (
        'Keep <a href="../gpu-performance-tools/index.html">GPU Performance Tools</a> nearby'
        not in document
    )


def test_body_links_do_not_count_as_navigation_and_unknown_local_links_fail():
    document = pages.render_course(SLUG)
    validate_document(document, SLUG)
    with pytest.raises(ValueError, match="unrecognized"):
        validate_document(
            document.replace(
                "</main>", '<a href="../missing/index.html">Unknown</a></main>'
            ),
            SLUG,
        )


def test_all_practical_entry_surfaces_offer_the_tensor_foundation():
    for name in config.COURSES:
        if name in {"soperator", "gpu-performance-tools", SLUG}:
            continue
        for filename in ("COURSE.md", "README.md", "SYLLABUS.md"):
            text = (config.ROOT / name / filename).read_text()
            assert f"../{SLUG}/index.html" in text
