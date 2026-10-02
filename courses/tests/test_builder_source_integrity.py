"""Malformed lesson structure must never silently remove canonical prose."""

import pytest

from course_builder import metadata


def lesson_document(body):
    return "# Example\n\nRetained orientation.\n\n## 1. Example lesson\n\n" + body


@pytest.mark.parametrize(
    "body, message",
    [
        (
            "**Objective** Explain.\n**How it works** First explanation.\n"
            "**How it works** Replacement explanation.\n",
            "duplicate lesson field",
        ),
        ("Lost introduction.\n**Objective** Explain.\n", "outside a lesson field"),
    ],
)
def test_ambiguous_lesson_content_fails_before_rendering(tmp_path, body, message):
    source = tmp_path / "COURSE.md"
    source.write_text(lesson_document(body))
    with pytest.raises(ValueError, match=message):
        metadata.parse_course(source)


@pytest.mark.parametrize("intro", ["Keep this example.\n\n", ""])
def test_fenced_lesson_syntax_remains_literal_source(tmp_path, intro):
    source = tmp_path / "COURSE.md"
    body = (
        f"**Objective** Explain.\n**How it works** {intro}"
        "```markdown\n## 2. Literal heading\n**How it works** Literal field.\n```\n\n"
        "Retain the conclusion.\n**Practice** Read.\n**Mental model** Connect.\n"
    )
    source.write_text(lesson_document(body))
    _, orientation, lessons = metadata.parse_course(source)
    assert orientation == "Retained orientation."
    assert len(lessons) == 1
    assert metadata.valid_lesson_fields(tuple(lessons[0])[1:])
    assert lessons[0]["How it works"] == (
        f"{intro}```markdown\n## 2. Literal heading\n"
        "**How it works** Literal field.\n```\n\nRetain the conclusion."
    )


@pytest.mark.parametrize("course", ["soperator", "gpu-performance-tools"])
@pytest.mark.parametrize(
    "payload",
    [
        '<style>@import "https://example.com/style.css";</style>',
        '<style>body{background:url(https://example.com/pixel.png)}</style>',
        '<div style="background:image-set(\'https://example.com/a.png\' 1x)">x</div>',
        '<svg><use href="https://example.com/a.svg#x"></use></svg>',
        '<svg><use xlink:href="https://example.com/a.svg#x"></use></svg>',
        '<svg><rect fill="url(https://example.com/a.svg#x)"></rect></svg>',
        '<meta http-equiv="refresh" content="0;url=https://example.com/">',
        '<a href="#main" ping="https://example.com/">Tracked</a>',
        '<a href="#main" attributionsrc>Tracked</a>',
        '<form action="https://example.com/"></form>',
        '<table background="https://example.com/pixel.png"><tr><td>x</td></tr></table>',
    ],
)
def test_reading_pages_reject_automatic_external_resources(course, payload):
    from validate_text_course import validate_document

    document = (metadata.ROOT / course / "index.html").read_text()
    with pytest.raises(ValueError, match="invalid text page resources"):
        validate_document(document.replace("</main>", payload + "</main>"), course)
