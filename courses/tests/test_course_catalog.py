"""Catalog publication, course routing and standalone distribution contracts."""

from course_builder import build as cb_build, markdown as cb_markdown, metadata as cb_metadata, pages as cb_pages, shell as cb_shell
import base64
import html
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser

import pytest
from test_course_content_contract import COURSES as GPU_COURSES
from test_course_content_contract import ROOT
from test_practice_integration import load_validator

COURSES = ("soperator", *GPU_COURSES, "advanced-gpu-communication")


class PageLinks(HTMLParser):
    def __init__(self, document):
        super().__init__()
        self.links = []
        self.ids = []
        self.resources = []
        self.feed(document)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        embedded_png = False
        if tag == "img" and attrs.get("alt") and attrs.get("src", "").startswith("data:image/png;base64,"):
            try:
                embedded_png = base64.b64decode(attrs["src"].split(",", 1)[1], validate=True).startswith(b"\x89PNG\r\n\x1a\n")
            except ValueError:
                pass
        if tag in {"script", "iframe", "object", "embed"} or ("src" in attrs and not embedded_png):
            self.resources.append((tag, attrs))
        if tag == "link" and attrs.get("href") != "data:,":
            self.resources.append((tag, attrs))


def test_catalog_matches_sources_and_metadata_updates_reach_readers(monkeypatch):
    document = (ROOT / "index.html").read_text()
    assert document == cb_pages.render_catalog()
    metadata = cb_metadata.catalog_metadata()
    cards = re.findall(
        r'<article class="course-card .*?</article>', document, re.DOTALL
    )
    assert len(cards) == 7
    for name, card in zip(COURSES, cards):
        assert f"<h3>{html.escape(metadata[name]['title'])}</h3>" in card
        assert f"{metadata[name]['estimated_guided_hours']} guided hours" in card
        assert f'href="{name}/index.html"' in card
    assert "The three specializations are independent" in document
    assert "Start here" in cards[0]
    assert "Fundamentals + Optimization" not in cards[0]
    assert "Text only · No labs" in cards[0]
    metadata["gpu-fundamentals"]["title"] = 'GPU <execution> & "memory"'
    metadata["gpu-fundamentals"]["estimated_guided_hours"] = 99
    monkeypatch.setattr(cb_pages, "catalog_metadata", lambda: metadata)
    assert "99 guided hours" in cb_pages.render_catalog()
    assert "GPU &lt;execution&gt; &amp; &quot;memory&quot;" in cb_pages.render_catalog()
    switcher = cb_shell.course_switcher("llm-training", metadata)
    assert "GPU &lt;execution&gt; &amp; &quot;memory&quot;" in switcher


@pytest.mark.parametrize(
    "relative",
    [
        "../index.html",
        "index.html",
        "lab-guide.html",
        *[f"{n}/index.html" for n in COURSES],
    ],
)
def test_all_site_links_resolve_without_external_assets(relative):
    path = ROOT / relative
    page = PageLinks(path.read_text())
    assert not page.resources
    assert len(page.ids) == len(set(page.ids))
    for link in page.links:
        if link.startswith(
            ("data:application/json;base64,", "data:application/zip;base64,")
        ):
            import base64

            assert base64.b64decode(link.split(",", 1)[1], validate=True)
            continue
        if link.startswith("https://"):
            continue
        if link.startswith("#"):
            assert link[1:] in page.ids
        else:
            target_path, _, fragment = link.partition("#")
            target = path.parent / target_path
            if fragment:
                assert fragment in PageLinks(target.read_text()).ids
            if target.is_dir():
                target /= "index.html"
            assert target.is_file(), (relative, link)


@pytest.mark.parametrize("course", GPU_COURSES)
def test_each_gpu_course_has_catalog_five_siblings_and_current_marker(course):
    validator = load_validator()
    parser = validator.Parser()
    parser.feed((ROOT / course / "index.html").read_text())
    assert not parser.errors
    validator.validate_course_navigation(parser, course)
    parser.course_navigation_links.append(parser.course_navigation_links[0])
    with pytest.raises(SystemExit, match="course navigation"):
        validator.validate_course_navigation(parser, course)
    parser.course_navigation_links.pop()
    parser.current_courses = [next(name for name in COURSES if name != course)]
    with pytest.raises(SystemExit, match="course navigation"):
        validator.validate_course_navigation(parser, course)


@pytest.mark.parametrize("in_navigation", [False, True])
@pytest.mark.parametrize(
    "destination",
    [
        "../arbitrary/index.html",
        "../llm-training/index.html?x=1",
        "//example.com/x",
        "https://example.com/x",
    ],
)
def test_course_navigation_exception_rejects_other_destinations(
    in_navigation, destination
):
    validator = load_validator()
    parser = validator.Parser()
    anchor = f'<a href="{destination}">Course</a>'
    if in_navigation:
        anchor = f'<nav id="course-contents"><div class="catalog-navigation">{anchor}</div></nav>'
    parser.feed(anchor)
    assert parser.errors


def test_known_course_links_are_rejected_outside_navigation():
    validator = load_validator()
    parser = validator.Parser()
    parser.feed('<main><a href="../llm-training/index.html">Course</a></main>')
    assert parser.errors
    parser = validator.Parser()
    parser.feed(
        '<nav id="course-contents"><div class="catalog-navigation"><link rel="stylesheet" href="../llm-training/index.html"></div></nav>'
    )
    assert parser.errors


@pytest.mark.parametrize("course", (*GPU_COURSES, "advanced-gpu-communication"))
def test_renamed_standalone_course_validates_without_repository_or_siblings(
    tmp_path, monkeypatch, course
):
    validator = load_validator()
    source = ROOT / course
    monkeypatch.setattr(validator, "ROOT", source)
    standalone = tmp_path / "my-learning-material"
    for path in validator.course_paths():
        target = standalone / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    result = subprocess.run(
        [sys.executable, "-B", str(standalone / "tools/validate_course.py")],
        cwd=standalone,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "relative", ["index.html", *[f"{n}/index.html" for n in COURSES]]
)
def test_saved_page_contains_the_complete_repository_license(relative):
    document = (ROOT / relative).read_text()
    license_text = re.search(
        r'<pre class="license-text" tabindex="0">(.*?)</pre>', document, re.DOTALL
    )
    assert license_text
    assert html.unescape(license_text[1]) == (ROOT.parent / "LICENSE").read_text()
    assert "© 2026 Nebius B.V." in document
    assert "Provided free of charge for learning and education" in document
    assert "Third-party materials retain their respective licenses" in document


@pytest.mark.parametrize("catalog_contents", [None, "stale catalog"])
def test_check_rejects_missing_or_stale_catalog(
    tmp_path, monkeypatch, catalog_contents
):
    monkeypatch.setattr(cb_build, "ROOT", tmp_path)
    monkeypatch.setattr(cb_build, "publication_preflight", lambda outputs: {})
    monkeypatch.setattr(cb_build, "course_metadata", lambda course: {"profile": "text-only"})
    monkeypatch.setattr(cb_build, "render_course", lambda course: f"current {course}")
    monkeypatch.setattr(cb_pages, "ROOT", tmp_path)
    monkeypatch.setattr(cb_markdown, "ROOT", tmp_path)
    monkeypatch.setattr(cb_metadata, "ROOT", tmp_path)
    monkeypatch.setattr(cb_build, "render_catalog", lambda: "current catalog")
    monkeypatch.setattr(cb_build, "render_shared_guide", lambda: "current guide")
    monkeypatch.setattr(
        sys, "argv", ["build_course_html.py", "gpu-fundamentals", "--check"]
    )
    (tmp_path / "lab-guide.html").write_text("current guide")
    if catalog_contents is not None:
        (tmp_path / "index.html").write_text(catalog_contents)
    with pytest.raises(SystemExit, match="stale or missing.*course catalog"):
        cb_build.main()


def test_selected_course_build_also_refreshes_catalog(tmp_path, monkeypatch):
    selected = tmp_path / "gpu-fundamentals"
    selected.mkdir()
    monkeypatch.setattr(cb_build, "ROOT", tmp_path)
    monkeypatch.setattr(cb_build, "publication_preflight", lambda outputs: {})
    monkeypatch.setattr(cb_build, "course_metadata", lambda course: {"profile": "text-only"})
    monkeypatch.setattr(cb_build, "render_course", lambda course: f"current {course}")
    monkeypatch.setattr(cb_pages, "ROOT", tmp_path)
    monkeypatch.setattr(cb_markdown, "ROOT", tmp_path)
    monkeypatch.setattr(cb_metadata, "ROOT", tmp_path)
    monkeypatch.setattr(cb_build, "render_catalog", lambda: "current catalog")
    monkeypatch.setattr(cb_build, "render_shared_guide", lambda: "current guide")
    monkeypatch.setattr(cb_build, "render_course", lambda course: f"current {course}")
    monkeypatch.setattr(sys, "argv", ["build_course_html.py", "gpu-fundamentals"])
    cb_build.main()
    assert (tmp_path / "index.html").read_text() == "current catalog"
    assert (selected / "index.html").read_text() == "current gpu-fundamentals"
    assert (tmp_path / "lab-guide.html").read_text() == "current guide"
    assert not (tmp_path / "llm-training").exists()


def test_metadata_slug_matches_canonical_catalog_directory():
    for name in COURSES:
        metadata = json.loads((ROOT / name / "reference/course.json").read_text())
        assert metadata["slug"] == name


@pytest.mark.parametrize("markup", [
    '<img alt="external" src="https://example.invalid/image.png">',
    '<img alt="invalid" src="data:image/png;base64,invalid">',
    '<img alt="wrong bytes" src="data:image/png;base64,dGV4dA==">',
])
def test_catalog_resource_check_still_rejects_non_embedded_pngs(markup):
    assert PageLinks(markup).resources
