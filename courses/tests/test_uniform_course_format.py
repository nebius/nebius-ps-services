"""Cross-profile presentation checks for the shared learner reading contract."""

from course_builder import config as cb_config, content as cb_content, markdown as cb_markdown, pages as cb_pages
import re

import pytest
from test_course_content_contract import ROOT


@pytest.mark.parametrize("course", cb_config.COURSES)
def test_every_profile_has_the_same_closing_sections_and_typography(course):
    document = (ROOT / course / "index.html").read_text()
    assert re.findall(r"<style>(.*?)</style>", document, re.S) == [
        (ROOT / "tools/course.css").read_text()
    ]
    assert not re.search(
        r"Syllabus|Course mission|guide-(?:syllabus|mission)", document
    )
    main = document.split('<main id="main">', 1)[1]
    glossary = re.search(
        r'<section id="guide-glossary" data-source="GLOSSARY.md"><h2>Glossary</h2><dl>.*?</dl></section>',
        main,
        re.S,
    )
    assert glossary
    assert main.index('id="next-steps"') < glossary.start()
    assert re.match(
        r'\s*<section id="official-references">', main[glossary.end() :]
    )
    references = re.search(
        r'<section id="official-references">.*?</section>', main, re.S
    )[0]
    assert "<ol><li" in references and "<ul>" not in references
    onward = re.search(r'<section id="next-steps">.*?</section>', main, re.S)[0]
    assert '<ul class="next-steps-list"><li><h3>' in onward
    for lesson in re.findall(
        r'<section class="lesson".*?</section>', main, re.S
    ):
        assert 'class="lesson-next-steps"' not in lesson
        assert 'class="lesson-glossary"' not in lesson
        assert "<h5" not in lesson and "<h6" not in lesson


@pytest.mark.parametrize(
    "body",
    [
        "No bullets",
        "- **Topic**\n\nUnindented body",
        "- **Topic**",
        "- **Topic**\n\n  ",
        "- ** **\n\n  Explanation.",
    ],
)
def test_next_step_renderer_rejects_unstructured_or_empty_topics(
    tmp_path, body
):
    (tmp_path / "NEXT-STEPS.md").write_text("# Where to Go Next\n\n" + body)
    with pytest.raises(ValueError, match="next-steps"):
        cb_content.next_steps_markup(tmp_path, {})


def test_lesson_anchors_preserve_punctuation_rules():
    assert (
        cb_markdown.markdown_heading_id("1. CPU–GPU cooperation")
        == "1-cpugpu-cooperation"
    )
    document = cb_pages.render_course("gpu-fundamentals")
    assert 'id="cpu-gpu-cooperation"' in document
    assert 'href="#cpu-gpu-cooperation"' in document
