"""The shared tools reference teaches passive, local, contextual diagrams."""

import ast
import html
import json
import re

import pytest
from course_builder import metadata, pages, visuals
from test_course_content_contract import ROOT

COURSE = ROOT / "gpu-performance-tools"
SVG = """<svg viewBox="0 0 100 100" role="img" aria-labelledby="tools-example-title tools-example-desc">
<title id="tools-example-title">Example</title><desc id="tools-example-desc">A local diagram.</desc>
<rect x="1" y="1" width="98" height="98" fill="#fff"/>
</svg>"""


def test_reference_course_has_five_reading_lessons_and_no_practical_inventory():
    contract = json.loads((COURSE / "reference/course.json").read_text())
    _, _, lessons = metadata.parse_text_course(COURSE / "COURSE.md", "reference-only")
    assert len(lessons) == 5
    assert "labs" not in contract and not (COURSE / "labs").exists()
    assert all("Practice" not in lesson for lesson in lessons)
    rendered = pages.render_text_course(COURSE, contract)
    assert rendered == (COURSE / "index.html").read_text()
    assert "Lab exercises" not in rendered
    for entry in visuals.detailed_visuals(COURSE, 5):
        assert rendered.count(entry["svg"]) == 1
        lesson_id = entry["lessons"][0]
        section = re.search(
            rf'<section class="lesson" id="lesson-{lesson_id}-.*?</section>',
            rendered,
            re.S,
        )[0]
        assert entry["svg"] in section


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "<foreignObject/>",
        '<image href="https://example.org/a.svg"/>',
        '<rect onclick="run()"/>',
        '<rect fill="url(https://example.org/a.svg)"/>',
        '<rect fill="URL(https://example.org/a.svg)"/>',
        '<rect fill="u/**/rl(https://example.org/a.svg)"/>',
        '<rect style="fill:red"/>',
        '<text id="foreign-id">Text</text>',
        '<rect fill="url(#missing)"/>',
    ],
)
def test_active_or_unscoped_svg_is_rejected(tmp_path, payload):
    source = tmp_path / "tools-example.svg"
    source.write_text(SVG.replace("</svg>", payload + "</svg>"))
    with pytest.raises(ValueError):
        visuals.passive_svg(tmp_path, source, id_prefix="tools-example-")


def test_symlinked_tool_figure_is_rejected(tmp_path):
    source = tmp_path / "real.svg"
    source.write_text(SVG)
    linked = tmp_path / "linked.svg"
    linked.symlink_to(source)
    with pytest.raises(ValueError, match="unsafe"):
        visuals.passive_svg(tmp_path, linked)


def test_nvtx_snippet_is_valid_python_and_published_verbatim():
    source = (COURSE / "COURSE.md").read_text()
    snippets = re.findall(r"```python\n(.*?)\n```", source, re.S)
    assert snippets
    document = (COURSE / "index.html").read_text()
    for code in snippets:
        ast.parse(code)
        assert html.escape(code) in document


def test_profiler_flags_and_monitoring_flow_are_explained_once():
    source = (COURSE / "COURSE.md").read_text()
    for flag in [
        "--cuda-trace-scope",
        "--sample",
        "--cpuctxsw",
        "--discard-environment",
        "--duration",
        "--kill",
        "--wait",
        "--launch-count",
        "--clock-control",
        "--capture-range",
    ]:
        assert flag in source
    for term in [
        "VictoriaMetrics",
        "Pushgateway",
        "Grafana",
        "@ now()",
        "NVTX",
        "torch.profiler",
    ]:
        assert term in source
    assert not list(ROOT.glob("*/reference/performance-tools.md"))
