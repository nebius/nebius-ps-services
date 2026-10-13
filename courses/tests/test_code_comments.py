"""Comment color must never change source text or mistake strings for comments."""

import copy
import re

import pytest

from course_builder import content, metadata, pages
from course_builder.code import render_code, source_language
from course_builder.config import COURSES, ROOT
from course_builder.markdown import block
from html_code import CodeBlocks


@pytest.mark.parametrize(
    "language,source,comments",
    [
        ("python", '# First\nx = "# string"  # Last\n', ["# First", "# Last"]),
        (
            "python",
            '"""# docstring\nnot a comment"""\nx = "escaped \\" # string"\n# Real',
            ["# Real"],
        ),
        (
            "python",
            "\t# Unicode: café <tag>&\n\nx = 1\t# end\n",
            ["# Unicode: café <tag>&", "# end"],
        ),
        (
            "bash",
            "#!/bin/bash\necho \"# quoted\" '# quoted' plain#word \\#escaped $# ${#value} ${value#x} # Real\n",
            ["#!/bin/bash", "# Real"],
        ),
        (
            "bash",
            'value="$(echo "# string" # inner\n)" # outer\n',
            ["# inner", "# outer"],
        ),
        ("bash", 'echo $((16#ff + 2)) "${value:-#literal}" # real\n', ["# real"]),
        ("bash", "echo foo\\\n#word\necho foo \\\n# comment\n", ["# comment"]),
        ("bash", "cat <<'EOF' # real\n# data\nEOF\n# after\n", ["# real", "# after"]),
        ("bash", "cat <<-EOF\n\t# data\n\tEOF\n# after", ["# after"]),
        (
            "cpp",
            '#include <stdio.h>\nconst char *s = "// string"; // real\n/* block\ncomment */ int x;',
            ["// real", "/* block\ncomment */"],
        ),
        (
            "cuda",
            'auto text = R"tag(// string\n/* string */)tag"; // real',
            ["// real"],
        ),
        ("cpp", "int n = 1'000; char c = '\\''; // real\n", ["// real"]),
        ("cpp", "/\\\n/ continued\\\ncomment\nint x;", ["/\\\n/ continued\\\ncomment"]),
        ("promql", 'metric{label="# literal",other=`# raw`} # real\n', ["# real"]),
        ("text", "# plain\n// plain\n", []),
        ("", "# plain\n", []),
    ],
)
def test_comments_and_source_text(language, source, comments):
    rendered = render_code(source, language)
    parsed = CodeBlocks(f"<pre><code>{rendered}</code></pre>").blocks[0]
    assert parsed["text"] == source
    assert parsed["comments"] == comments
    assert "<tag>" not in rendered


@pytest.mark.parametrize(
    "language,source",
    [
        ("ruby", "# unsupported"),
        ("python", 'x = "unterminated'),
        ("python", "x = [\n# incomplete"),
        ("bash", 'echo "unterminated'),
        ("bash", 'echo "$(echo missing'),
        ("bash", "cat <<EOF\n# no delimiter"),
        ("cpp", "/* missing end"),
        ("cpp", 'R"tag(missing end'),
        ("promql", 'metric{label="missing'),
    ],
)
def test_invalid_code_fails_with_source_context(language, source):
    with pytest.raises(ValueError, match="example.md:.*" + language):
        render_code(source, language, source="example.md")


def test_markdown_dispatch_and_failure_context():
    rendered = block("```python\nx = 1 # colored\n```", source="example.md")
    assert CodeBlocks(rendered).blocks[0]["comments"] == ["# colored"]
    assert (
        block("```text\n# plain\n```") == '<pre tabindex="0"><code># plain</code></pre>'
    )
    with pytest.raises(ValueError, match=r"example.md:.*ruby"):
        block("```ruby\n# unsupported\n```", source="example.md")


@pytest.mark.parametrize(
    "markdown,error",
    [
        ("```python\nx = 1", "unterminated Markdown code block"),
        (
            '<!-- comparison:start -->\n```python\nx = "unterminated\n```\n'
            "<!-- comparison:next -->\nSecond panel\n<!-- comparison:end -->",
            "python",
        ),
    ],
)
def test_markdown_fragment_context_survives_fence_and_comparison_errors(
    markdown, error
):
    with pytest.raises(ValueError) as caught:
        block(markdown, source="example.md", context="lesson 2 / How it works")
    message = str(caught.value)
    assert "example.md [lesson 2 / How it works]: fragment line 1" in message
    assert error in message


def test_source_extension_selection():
    assert source_language("labs/example.py") == "python"
    assert source_language("labs/example.cu") == "cpp"
    with pytest.raises(ValueError, match="unsupported code source extension"):
        source_language("labs/example.unknown")


@pytest.mark.parametrize(
    "name,parser",
    [
        ("gpu-fundamentals", "parse_course"),
        ("soperator", "parse_text_course"),
        ("pytorch-gpu-performance-engineering", "parse_text_course"),
    ],
)
def test_lesson_lexical_errors_identify_course_and_section(monkeypatch, name, parser):
    original = getattr(pages, parser)

    def malformed_lesson(*args, **kwargs):
        title, preamble, lessons = original(*args, **kwargs)
        lessons[0]["How it works"] += '\n\n```python\nx = "unterminated\n```'
        return title, preamble, lessons

    monkeypatch.setattr(pages, parser, malformed_lesson)
    with pytest.raises(ValueError) as failure:
        pages.render_course(name)
    message = str(failure.value)
    assert f"{name}/COURSE.md" in message
    assert "lesson 1" in message
    assert "How it works" in message
    assert "fragment line" in message
    assert "python" in message


def test_lab_lexical_errors_identify_the_authored_guide():
    course = ROOT / "gpu-fundamentals"
    lessons = metadata.parse_course(course / "COURSE.md")[2]
    guide = copy.deepcopy(
        metadata.lab_guides(course, metadata.course_metadata(course), len(lessons))[0]
    )
    guide["sections"]["Run"] = '```bash\necho "unterminated\n```'
    with pytest.raises(ValueError) as failure:
        content.lab_markup(course, guide, lessons, {})
    message = str(failure.value)
    assert "gpu-fundamentals/reference/labs/01_cpu_gpu_crossover.md" in message
    assert "Run" in message
    assert "bash" in message


@pytest.mark.parametrize("section", ["Introduction", "More practice"])
def test_next_steps_lexical_errors_identify_the_guide(tmp_path, section):
    snippet = '```python\nx = "unterminated\n```'
    introduction = snippet if section == "Introduction" else "Read on."
    topic = snippet if section == "More practice" else "Try another example."
    (tmp_path / "NEXT-STEPS.md").write_text(
        f"# Where to Go Next\n\n{introduction}\n\n- **More practice**\n\n"
        + "\n".join("  " + line for line in topic.splitlines())
        + "\n"
    )
    with pytest.raises(ValueError) as failure:
        content.next_steps_markup(tmp_path, {})
    message = str(failure.value)
    assert f"{tmp_path.name}/NEXT-STEPS.md [{section}]" in message
    assert "python" in message


def test_python_unicode_line_separators_preserve_source():
    source = "# first\u2028continued\n# second\nx=1\n"
    rendered = render_code(source, "python")
    parsed = CodeBlocks(f"<pre><code>{rendered}</code></pre>").blocks[0]
    assert parsed["text"] == source
    assert parsed["comments"] == ["# first\u2028continued", "# second"]


@pytest.mark.parametrize(
    "source",
    [
        'echo "$(case x in x) echo "abc # literal";; esac)" # real\n',
        'echo "$(case x in (x) echo esac "# literal";; *) echo case;; esac)" # real\n',
        'echo "$(case x in x) case y in y) echo "# literal";; esac;; esac)" # real\n',
        'echo "$(if true; then case x in x) echo "abc # literal";; esac; fi)" # real\n',
    ],
)
def test_shell_case_patterns_do_not_close_substitutions(source):
    rendered = render_code(source, "bash")
    parsed = CodeBlocks(f"<pre><code>{rendered}</code></pre>").blocks[0]
    assert parsed["text"] == source
    assert parsed["comments"] == ["# real"]


@pytest.mark.parametrize("name", COURSES)
def test_every_published_source_listing_keeps_exact_text(name):
    course = ROOT / name
    parsed = CodeBlocks((course / "index.html").read_text())
    for item in parsed.blocks:
        source = item["attrs"].get("data-source")
        if not source:
            continue
        assert item["text"] == (course / source).read_text(), source
        if "# Read the experiment controls before allocating inputs." in item["text"]:
            assert (
                "# Read the experiment controls before allocating inputs."
                in item["comments"]
            )
    if name == "advanced-gpu-communication":
        assert any(
            item["attrs"].get("data-source") == "labs/common.py" and item["comments"]
            for item in parsed.blocks
        )


@pytest.mark.parametrize("name", (*COURSES, "lab-guide"))
def test_every_reader_page_colors_comments(name):
    path = ROOT / ("lab-guide.html" if name == "lab-guide" else f"{name}/index.html")
    document = path.read_text()
    parsed = CodeBlocks(document)
    assert any(item["comments"] for item in parsed.blocks), name
    assert "--code-comment: #236b36;" in document
    assert re.search(
        r"pre code \.code-comment\s*\{\s*color: var\(--code-comment\);", document
    )
