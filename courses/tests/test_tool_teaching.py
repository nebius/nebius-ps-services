"""Contextual tool figures stay passive, local and faithful to their source."""

from course_builder import content as cb_content, metadata as cb_metadata
import ast
import html
import json
import re

import pytest
from test_course_content_contract import ROOT
from test_practice_integration import load_validator

SVG = """<svg viewBox="0 0 100 100" role="img" aria-labelledby="tools-example-title tools-example-desc">
<title id="tools-example-title">Example</title><desc id="tools-example-desc">A local diagram.</desc>
<rect x="1" y="1" width="98" height="98" fill="#fff"/>
</svg>"""
METADATA = {
    "performance_tools": "reference/performance-tools.md",
    "observability": "reference/observability.json",
}


def primer(
    tmp_path,
    image="![Example](diagrams/tools-example.svg)",
    field="How it works",
):
    directory = tmp_path / "reference/diagrams"
    directory.mkdir(parents=True)
    (directory / "tools-example.svg").write_text(SVG)
    text = "# Using GPU performance tools\n\n" + "\n\n".join(
        f"## {name}\n\n" + (image if name == field else "Explanation.")
        for name in (
            "Objective",
            "How it works",
            "Practice",
            "Mental model",
        )
    )
    (tmp_path / "reference/performance-tools.md").write_text(text)
    return directory / "tools-example.svg"


def test_local_figure_is_embedded_at_its_authored_position(tmp_path):
    primer(
        tmp_path, "Before.\n\n![Example](diagrams/tools-example.svg)\n\nAfter."
    )
    result = cb_content.performance_tools_markup(tmp_path, METADATA)
    assert result.index("Before.") < result.index(SVG) < result.index("After.")
    assert (
        "<figcaption><strong>Example</strong><p>A local diagram.</p>" in result
    )


@pytest.mark.parametrize(
    "image",
    [
        "![Example](../outside.svg)",
        "![Example](https://example.org/a.svg)",
        "![](diagrams/tools-example.svg)",
        "![Example](diagrams/tools-missing.svg)",
        "![Example](diagrams/tools-example.svg)\n\n![Again](diagrams/tools-example.svg)",
    ],
)
def test_invalid_tool_figure_is_rejected(tmp_path, image):
    primer(tmp_path, image)
    with pytest.raises(ValueError):
        cb_content.performance_tools_markup(tmp_path, METADATA)


@pytest.mark.parametrize("field", ["Objective", "Practice", "Mental model"])
def test_tool_figures_are_only_in_how_it_works(tmp_path, field):
    primer(tmp_path, field=field)
    with pytest.raises(ValueError):
        cb_content.performance_tools_markup(tmp_path, METADATA)


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
    path = primer(tmp_path)
    path.write_text(SVG.replace("</svg>", payload + "</svg>"))
    with pytest.raises(ValueError):
        cb_content.performance_tools_markup(tmp_path, METADATA)


def test_symlinked_tool_figure_is_rejected(tmp_path):
    path = primer(tmp_path)
    real = tmp_path / "real.svg"
    path.rename(real)
    path.symlink_to(real)
    with pytest.raises(ValueError, match="unsafe"):
        cb_content.performance_tools_markup(tmp_path, METADATA)


def test_image_syntax_inside_fenced_code_is_literal(tmp_path):
    primer(
        tmp_path,
        '```python\nimage = "![Example](diagrams/tools-example.svg)"\n```',
    )
    result = cb_content.performance_tools_markup(tmp_path, METADATA)
    assert SVG not in result
    assert "![Example](diagrams/tools-example.svg)" in result


def test_independent_validator_rejects_modified_tool_svg(monkeypatch):
    course = ROOT / "gpu-fundamentals"
    validator = load_validator()
    monkeypatch.setattr(validator, "ROOT", course)
    document = (course / "index.html").read_text()
    metadata = json.loads((course / "reference/course.json").read_text())
    lessons = cb_metadata.parse_course(course / "COURSE.md")[2]
    lesson_fields = [(row["title"], row, {}) for row in lessons]
    parser = validator.Parser()
    parser.feed(document)
    validator.validate_figures(document, parser, lesson_fields, metadata)
    altered = document.replace(
        "Synthetic vector-add report", "Altered vector-add report", 1
    )
    with pytest.raises(SystemExit, match="tool figure differs"):
        validator.validate_figures(altered, parser, lesson_fields, metadata)


def test_nvtx_snippet_is_valid_python_and_published_verbatim(monkeypatch):
    course = ROOT / "gpu-fundamentals"
    source = (course / "reference/performance-tools.md").read_text()
    (code,) = re.findall(r"```python\n(.*?)\n```", source, re.DOTALL)
    ast.parse(code)
    document = (course / "index.html").read_text()
    assert html.escape(code) in document
    validator = load_validator()
    monkeypatch.setattr(validator, "ROOT", course)
    metadata = json.loads((course / "reference/course.json").read_text())
    validator.validate_observability_assets(document, metadata)
    altered = document.replace(
        "nvtx.mark(&quot;result_ready&quot;)",
        "nvtx.mark(&quot;wrong_result&quot;)",
        1,
    )
    with pytest.raises(SystemExit, match="performance tools code differs"):
        validator.validate_observability_assets(altered, metadata)
