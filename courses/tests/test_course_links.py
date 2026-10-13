"""Cross-page navigation preserves the current course without affecting anchors."""

from html.parser import HTMLParser

import pytest

from course_builder import build, markdown, pages
from course_builder.config import COURSES, ROOT
from course_builder.links import link_attributes


class Anchors(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.anchors = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.anchors.append(dict(attrs))


@pytest.mark.parametrize("name", ["catalog", "lab-guide", *COURSES])
def test_every_published_link_preserves_reading_context(name):
    if name == "catalog":
        document = pages.render_catalog()
        path = ROOT / "index.html"
    elif name == "lab-guide":
        document = pages.render_shared_guide()
        path = ROOT / "lab-guide.html"
    else:
        document = pages.render_course(name)
        path = ROOT / name / "index.html"
    assert path.read_text() == document
    anchors = Anchors(document).anchors
    outgoing = [a for a in anchors if not a["href"].startswith("#") and "download" not in a]
    local = [a for a in anchors if a["href"].startswith("#") or "download" in a]
    assert outgoing and local
    for anchor in outgoing:
        assert anchor["target"] == "_blank", anchor
        assert set(anchor["rel"].split()) == {"noopener", "noreferrer"}, anchor
    for anchor in local:
        assert "target" not in anchor, anchor
        assert "rel" not in anchor, anchor


@pytest.mark.parametrize("target", ["https://example.com/doc?q=1&v=2#section", "../lab-guide.html#lab-preparation-scripts", "../soperator/index.html"])
def test_markdown_cross_page_destinations_and_escaping(target):
    # Link rewrites choose the final destination before navigation is classified.
    document = markdown.inline("[Read](guide.md)", {"guide.md": target})
    assert Anchors(document).anchors == [
        {"target": "_blank", "rel": "noopener noreferrer", "href": target}
    ]
    assert "&v=2" not in document


def test_fragment_rewrite_and_literal_html_remain_inert():
    assert markdown.inline("[Read](guide.md)", {"guide.md": "#setup"}) == '<a href="#setup">Read</a>'
    assert markdown.inline('`<a href="https://example.com">Read</a>`') == (
        '<code>&lt;a href=&quot;https://example.com&quot;&gt;Read&lt;/a&gt;</code>'
    )
    document = f'<a {link_attributes("page.html?x=\"&value=<tag>")}>Read</a>'
    assert Anchors(document).anchors[0]["href"] == 'page.html?x="&value=<tag>'
    assert "<tag>" not in document


@pytest.mark.parametrize("attrs", [
    'href="../lab-guide.html"',
    'target="_self" rel="noopener noreferrer" href="https://example.com"',
    'target="_blank" href="../soperator/index.html"',
    'target="_blank" rel="noopener" href="page.html#section"',
    'target="_blank" rel="noopener noreferrer opener" href="page.html"',
    'target="_blank" href="#lesson"',
    'target="_blank" download="results.zip" href="results.zip"',
])
def test_build_preflight_rejects_navigation_policy_regressions(attrs):
    with pytest.raises(ValueError, match="new tab|native navigation"):
        build.PageTargets(f"<a {attrs}>Read</a>".encode())


def test_build_preflight_accepts_new_tabs_fragments_and_downloads():
    document = (
        '<a target="_blank" rel="noopener noreferrer" href="page.html#section">Read</a>'
        '<a href="#lesson">Lesson</a>'
        '<a download="results.zip" href="results.zip">Results</a>'
    )
    assert build.PageTargets(document.encode()).links == ["page.html#section", "#lesson", "results.zip"]


@pytest.mark.parametrize("attribute", ['data-href="../lab-guide.html"', 'href="xx/lab-guide.html"'])
def test_setup_validator_requires_the_actual_shared_guide_destination(attribute):
    from validate_course_template import validate_shared_setup_link

    document = f'<div id="course-downloads"><a {attribute}>Lab setup guide</a></div>'
    with pytest.raises(SystemExit, match="must link to the shared setup guide"):
        validate_shared_setup_link(document, {})
