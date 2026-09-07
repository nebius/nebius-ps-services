"""Theory stays before a single, explicitly linked practical learning route."""

import json
import re
from pathlib import Path

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder


THEORY_FIELDS = {
    "title",
    "Objective",
    "Prerequisite bridge",
    "Recall",
    "Why it matters",
    "Mental model",
    "Mechanism",
    "Practice labs",
}


@pytest.mark.parametrize("course", COURSES)
def test_every_lesson_retains_theory_and_exact_lab_links(course):
    builder = load_builder()
    root = ROOT / course
    lessons = builder.parse_course(root / "COURSE.md")[2]
    metadata = json.loads((root / "reference/course.json").read_text())
    for number, lesson in enumerate(lessons, 1):
        opening = "Start here" if number == 1 else "What it is"
        assert set(lesson) == THEORY_FIELDS | {opening}
        labs = [lab for lab in metadata["labs"] if number in lab["lessons"]]
        assert labs, f"Lesson {number} has no practical owner"
        expected = []
        for lab in labs:
            guide = Path("reference/labs") / (Path(lab["path"]).stem + ".md")
            title = (root / guide).read_text().splitlines()[0][2:]
            expected.append(f"- [{title}]({guide.as_posix()})")
        assert lesson["Practice labs"].strip() == "\n".join(expected)


@pytest.mark.parametrize("course", COURSES)
def test_lab_practice_contains_commands_without_parallel_example_sections(course):
    root = ROOT / course
    for path in (root / "reference/labs").glob("*.md"):
        text = path.read_text()
        sections = re.findall(r"^## (.+)$", text, re.M)
        assert sections == [
            "Before you start",
            "Concepts and code path",
            "Practice",
            "Check your results",
            "Investigate the behavior",
            "If something goes wrong",
            "Takeaways and next step",
        ]
        practice = text.split("\n## Practice\n", 1)[1].split("\n## ", 1)[0]
        assert "```bash\n" in practice
        assert not re.search(r"^#{2,6} Worked example\b", text, re.M)


def load_validator():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "practice_validator", ROOT / "tools/validate_course_template.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "field",
    [
        "H100 focus",
        "Worked example",
        "Trade-offs",
        "Practice",
        "Evidence",
        "Interpretation",
        "Common failure",
        "Answer",
        "Review",
    ],
)
def test_old_applied_lesson_fields_fail_fast(tmp_path, field):
    path = tmp_path / "COURSE.md"
    path.write_text(f"# Course\n\n## 1. Example\n\n**{field}** Content\n")
    with pytest.raises(ValueError, match="unsupported lesson fields"):
        load_builder().parse_course(path)


def test_lesson_practice_links_cannot_disagree_with_metadata():
    builder = load_builder()
    guide = {"source": Path("labs/01_example.py"), "title": "Lab 01: Example"}
    with pytest.raises(ValueError, match="Practice labs must match"):
        builder.lesson_markup(
            {"title": "Example", "Practice labs": "Unlinked prose"},
            1,
            practice_labs=[guide],
        )


def test_old_guide_execution_heading_is_rejected(tmp_path):
    from test_authored_lab_guides import fixture_course

    builder = load_builder()
    metadata, path = fixture_course(tmp_path)
    path.write_text(
        path.read_text().replace("## Practice\n", "## Run the experiment\n")
    )
    with pytest.raises(ValueError, match="ordered sections"):
        builder.lab_guides(tmp_path, metadata, 1)


@pytest.mark.parametrize(
    "home", [None, 3, "", "labs:07_profile_workload", "lab:../outside"]
)
def test_malformed_visual_homes_fail_fast(tmp_path, home):
    builder = load_builder()
    reference = tmp_path / "reference"
    reference.mkdir()
    entry = {
        "path": "reference/test.svg",
        "title": "Test",
        "lessons": [1],
        "after": "Practice",
        "home": home,
    }
    (reference / "visual-manifest.json").write_text(json.dumps({"diagrams": [entry]}))
    with pytest.raises(ValueError, match="placement"):
        builder.detailed_visuals(tmp_path, 1)


@pytest.mark.parametrize("home", ["lab:99_missing", "lab:04_cuda_graphs"])
def test_unknown_or_unrelated_lab_cannot_own_a_figure(monkeypatch, home):
    builder = load_builder()
    entries = builder.detailed_visuals(ROOT / "gpu-optimizations", 13)
    entries[0].update(home=home, after="Practice")
    monkeypatch.setattr(builder, "detailed_visuals", lambda *args: entries)
    with pytest.raises(ValueError, match="primary lesson"):
        builder.render_course("gpu-optimizations")


def test_missing_visual_home_is_rejected(tmp_path):
    reference = tmp_path / "reference"
    reference.mkdir()
    entry = {
        "path": "reference/test.svg",
        "title": "Test",
        "lessons": [1],
        "after": "Mechanism",
    }
    (reference / "visual-manifest.json").write_text(json.dumps({"diagrams": [entry]}))
    with pytest.raises(ValueError, match="placement"):
        load_builder().detailed_visuals(tmp_path, 1)


@pytest.mark.parametrize("fault", ["owner", "section", "missing", "duplicate"])
def test_validator_checks_actual_figure_home_and_preceding_section(monkeypatch, fault):
    validator = load_validator()
    root = ROOT / "gpu-optimizations"
    monkeypatch.setattr(validator, "ROOT", root)
    document = (root / "index.html").read_text()
    figure = re.search(
        r'<figure class="detail-diagram" id="detail-framework-to-kernel-attribution-with-nvtx".*?</figure>',
        document,
        re.S,
    )[0]
    if fault == "owner":
        document = document.replace(figure, "")
        document = document.replace(
            '<div class="practice-links">', figure + '<div class="practice-links">', 1
        )
    elif fault == "section":
        article = re.search(
            r'<article class="lab" id="lab-07-profile-workload".*?</article>',
            document,
            re.S,
        )[0]
        changed = article.replace("<h4>Practice</h4>", "<h4>Check your results</h4>")
        document = document.replace(article, changed)
    elif fault == "missing":
        document = document.replace(figure, "")
    else:
        document = document.replace(figure, figure + figure)
    parser = validator.Parser()
    parser.feed(document)
    if fault == "duplicate":
        assert any("duplicate id" in error for error in parser.errors)
    else:
        with pytest.raises(SystemExit, match="absent|owning lesson or lab"):
            validator.validate_figures(
                document,
                parser,
                validator.source_lessons(),
                json.loads((root / "reference/course.json").read_text()),
            )


def test_lab_section_links_resolve_to_unique_html_fragments():
    builder = load_builder()
    root = ROOT / "gpu-optimizations"
    guides = builder.lab_guides(root, builder.course_metadata(root), 13)
    guide = next(guide for guide in guides if guide["source"].stem.startswith("18_"))
    links = builder.lab_guide_links(
        guide,
        {
            f"reference/labs/{item['source'].stem}.md": "#lab-"
            + builder.slug(item["source"].stem)
            for item in guides
        },
    )
    assert (
        links["17_nccl_transport_sweep.md#practice"]
        == "#lab-17-nccl-transport-sweep-practice"
    )


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "header",
        "cell",
        "row",
        "table",
        "order",
        "prose-pipe",
        "code-pipe",
        "code-table-pipe",
    ],
)
def test_lab_tables_preserve_cells_and_non_table_pipes(tmp_path, monkeypatch, fault):
    from test_authored_lab_guides import fixture_course

    builder = load_builder()
    validator = load_validator()
    metadata, path = fixture_course(tmp_path)
    table = (
        "| Candidate | Observation |\n"
        "| --- | --- |\n"
        "| Baseline | First measurement |\n"
        "| Variant | Second measurement |\n"
    )
    practice = (
        "### Compare the observations\n\n"
        + table
        + "\nA literal | character remains meaningful outside a table.\n\n"
        + "```bash\nprintf 'left | right\\n' | cat\n"
        + "cat <<'TABLE'\n\n| Literal | Header |\n| --- | --- |\n| Value | Kept |\n\nTABLE\n```\n\n"
    )
    path.write_text(
        path.read_text().replace("## Practice\n\n", "## Practice\n\n" + practice)
    )
    guide = builder.lab_guides(tmp_path, metadata, 1)[0]
    fields = {"Practice labs": "- [Lab 01: Example](reference/labs/01_example.md)"}
    document = (
        '<section class="lesson"><div class="practice-links"><strong>Practice labs</strong>'
        '<ul><li><a href="#lab-01-example">Lab 01: Example</a></li></ul></div>\n</section>'
        + builder.lab_markup(tmp_path, guide, [{"title": "Example"}], {})
    )
    replacements = {
        "header": ('<th scope="col">Observation</th>', '<th scope="col">Changed</th>'),
        "cell": ("<td>First measurement</td>", "<td>Different measurement</td>"),
        "row": ("<tr><td>Variant</td><td>Second measurement</td></tr>", ""),
        "table": (builder.block(table), ""),
        "order": (
            "<td>Baseline</td><td>First measurement</td>",
            "<td>First measurement</td><td>Baseline</td>",
        ),
        "prose-pipe": ("A literal | character", "A literal character"),
        "code-pipe": (" | cat", " cat"),
        "code-table-pipe": ("| Value | Kept |", "Value | Kept |"),
    }
    if fault:
        original, changed = replacements[fault]
        assert document.count(original) == 1
        document = document.replace(original, changed)
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    args = (document, metadata, [("1. Example", fields, {})], [guide["source"]])
    if fault:
        with pytest.raises(SystemExit, match="narrative differs"):
            validator.validate_lab_guides(*args)
    else:
        validator.validate_lab_guides(*args)
