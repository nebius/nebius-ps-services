"""Optional current-technology reading stays complete, safe and navigable."""

import re

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder
from test_course_review_fixes import load_lab


@pytest.mark.parametrize("course", COURSES)
def test_next_steps_are_complete_optional_closing_guides(course):
    builder = load_builder()
    source = ROOT / course / "NEXT-STEPS.md"
    assert source.is_file()
    markdown = source.read_text()
    assert markdown.startswith("# Where to Go Next\n")
    assert "Research reviewed" not in markdown
    assert "optional" in markdown
    topics = re.split(r"(?m)^## ", markdown)[1:]
    assert 5 <= len(topics) <= 6
    for topic in topics:
        assert "**Investigate**" in topic
        assert "**Scope**" in topic
        assert re.search(r"\[[^]]+\]\(https://", topic)
    page = (ROOT / course / "index.html").read_text()
    assert page == builder.render_course(course)
    assert page.count('id="next-steps"') == 1
    assert page.count('data-source="NEXT-STEPS.md"') == 1
    closing = re.search(r'<section id="next-steps">.*?</section>', page, re.S)[0]
    title = markdown.splitlines()[0][2:]
    assert re.search(r"<h2>(.*?)</h2>", closing)[1] == title
    toc = re.search(r'<nav id="course-contents".*?</nav>', page, re.S)[0]
    assert f'<a href="#next-steps">{title}</a>' in toc
    assert "Research reviewed" not in closing
    assert page.index('id="supporting-guides"') < page.index('id="next-steps"')
    assert page.index('id="next-steps"') < page.index('id="official-references"')
    with load_lab(f"{course}/tools/validate_course.py") as validator:
        assert "NEXT-STEPS.md" in validator.REQUIRED_FILES
        validator.validate_next_steps(page)


@pytest.mark.parametrize("course", COURSES)
def test_next_steps_validator_rejects_local_drift(course):
    page = (ROOT / course / "index.html").read_text()
    section = re.search(r'<section id="next-steps">.*?</section>', page, re.S)
    assert section
    markup = section[0]
    paragraphs = re.findall(r"<p>.*?</p>", markup, re.S)
    assert len(paragraphs) > 2
    reordered = markup.replace(paragraphs[0], "__FIRST__", 1)
    reordered = reordered.replace(paragraphs[1], paragraphs[0], 1)
    reordered = reordered.replace("__FIRST__", paragraphs[1], 1)
    removed = page.replace(markup, "", 1)
    invalid_pages = (
        removed,
        page.replace(markup, markup + markup, 1),
        page.replace(markup, markup.replace(paragraphs[0], "", 1), 1),
        page.replace(markup, reordered, 1),
        page.replace('href="#next-steps"', 'href="#supporting-guides"'),
        removed.replace(
            '<section id="supporting-guides">',
            markup + '<section id="supporting-guides">',
        ),
        removed.replace("</main>", markup + "</main>"),
        page.replace('data-source="NEXT-STEPS.md"', 'data-source="README.md"'),
        page.replace(
            '<section id="next-steps"><h2>Where to Go Next</h2>',
            '<section id="next-steps"><h2>Further study</h2>',
            1,
        ),
        page.replace(
            '<a href="#next-steps">Where to Go Next</a>',
            '<a href="#next-steps">Further study</a>',
            1,
        ),
    )
    with load_lab(f"{course}/tools/validate_course.py") as validator:
        for invalid in invalid_pages:
            with pytest.raises(SystemExit, match="next.steps"):
                validator.validate_next_steps(invalid)


def test_next_steps_new_reference_allowlist_stays_narrow():
    with load_lab("tools/validate_course_template.py") as validator:
        for url, accepted in (
            ("https://flashinfer.ai/releases/", True),
            ("https://github.com/flashinfer-ai/flashinfer", True),
            ("https://github.com/deepseek-ai/DeepEP", True),
            ("https://github.com/deepseek-ai/unreviewed-project", False),
            ("https://github.com/flashinfer-ai/unreviewed-project", False),
            ("https://flashinfer.ai.example.com/releases/", False),
        ):
            parser = validator.Parser()
            parser.feed(
                f'<section id="official-references"><a href="{url}">Read</a></section>'
            )
            assert (not parser.errors) == accepted


@pytest.mark.parametrize("course", COURSES)
def test_next_steps_validator_checks_reference_destinations(course):
    page = (ROOT / course / "index.html").read_text()
    section = re.search(r'<section id="next-steps">.*?</section>', page, re.S)[0]
    link = re.search(r'href="#(reference-\d+)"', section)
    assert link
    reference = re.search(rf'<li id="{link[1]}"><a href="([^"]+)">', page)
    assert reference
    wrong_anchor = page.replace(
        section, section.replace(link[0], 'href="#reference-1"', 1), 1
    )
    wrong_destination = (
        page[: reference.start(1)]
        + "https://docs.nvidia.com/"
        + page[reference.end(1) :]
    )
    with load_lab(f"{course}/tools/validate_course.py") as validator:
        for invalid in (wrong_anchor, wrong_destination):
            with pytest.raises(SystemExit, match="next.steps.*reference"):
                validator.validate_next_steps(invalid)
