"""Lab titles, teaching narrative, execution guidance and lesson links agree."""

import html
import json
import re
import subprocess
import ast
import importlib.util

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder
from test_course_review_fixes import load_lab


SECTIONS = (
    "Before you start",
    "Concepts and code path",
    "Practice",
    "Check your results",
    "Investigate the behavior",
    "If something goes wrong",
    "Takeaways and next step",
)


@pytest.mark.parametrize("course", COURSES)
def test_every_lab_has_an_authored_guide_and_explicit_lessons(course):
    builder = load_builder()
    root = ROOT / course
    lessons = builder.parse_course(root / "COURSE.md")[2]
    metadata = builder.course_metadata(root)
    guides = builder.lab_guides(root, metadata, len(lessons))
    assert len(guides) == len(builder.executable_sources(root))
    introductions = set()
    for guide in guides:
        assert tuple(guide["sections"]) == SECTIONS
        assert guide["title"].startswith(f"Lab {guide['number']}: ")
        assert guide["lessons"]
        introductions.add(guide["introduction"])
        assert "```bash" in guide["sections"]["Practice"]
    assert len(introductions) == len(guides)


@pytest.mark.parametrize("course", COURSES)
def test_lab_narrative_and_identity_survive_rendering(course):
    builder = load_builder()
    root = ROOT / course
    lessons = builder.parse_course(root / "COURSE.md")[2]
    metadata = builder.course_metadata(root)
    document = (root / "index.html").read_text()
    lesson_html = re.findall(r'<section class="lesson".*?</section>', document, re.S)
    references = {
        name: "#" + builder.guide_id(name)
        for name in builder.COMMON_GUIDES + builder.SUPPORTING_GUIDES.get(course, ())
    }
    references.update(
        {
            html.unescape(url): "#" + anchor
            for anchor, url in re.findall(
                r'<li id="(reference-\d+)"><a href="([^"]+)">', document
            )
        }
    )
    references.update(
        {
            f"reference/labs/{guide['source'].stem}.md": "#lab-"
            + builder.slug(guide["source"].stem)
            for guide in builder.lab_guides(root, metadata, len(lessons))
        }
    )
    for guide in builder.lab_guides(root, metadata, len(lessons)):
        target = "lab-" + builder.slug(guide["source"].stem)
        article = re.search(
            rf'<article class="lab" id="{target}".*?</article>', document, re.S
        ).group()
        assert f"<h3>{html.escape(guide['title'])}</h3>" in article
        assert builder.block(guide["introduction"]) in article
        for section in SECTIONS:
            assert f"<h4>{section}</h4>" in article
            links = builder.lab_guide_links(guide, references)
            assert (
                builder.block(
                    guide["sections"][section],
                    links,
                    prefix=f"{target}-{builder.slug(section)}-",
                )
                in article
            )
        for number in guide["lessons"]:
            assert (
                f'href="#{target}">{html.escape(guide["title"])}</a>'
                in lesson_html[number - 1]
            )
            assert f'href="#{builder.slug(lessons[number - 1]["title"])}"' in article
        assert html.escape(guide["source"].read_text()) in article
    assert (
        "State which resource or execution path should change before running."
        not in document
    )


def fixture_course(tmp_path):
    (tmp_path / "labs").mkdir()
    (tmp_path / "labs/01_example.py").write_text('"""Example."""\n')
    (tmp_path / "reference/labs").mkdir(parents=True)
    paragraph = " ".join(
        ["Specific explanation of inputs, execution, checks and limitations."] * 9
    )
    body = "# Lab 01: Example\n\n" + paragraph + "\n\n"
    for section in SECTIONS:
        body += f"## {section}\n\n{paragraph}\n\n"
        if section == "Practice":
            body += "```bash\npython labs/01_example.py --help\n```\n\n"
    path = tmp_path / "reference/labs/01_example.md"
    path.write_text(body)
    metadata = {
        "labs": [{"path": "labs/01_example.py", "optional": False, "lessons": [1]}]
    }
    return metadata, path


@pytest.mark.parametrize(
    "defect", ["missing", "extra", "number", "section", "lesson", "duplicate"]
)
def test_invalid_lab_guides_fail_fast(tmp_path, defect):
    builder = load_builder()
    metadata, path = fixture_course(tmp_path)
    if defect == "missing":
        path.unlink()
    elif defect == "extra":
        (path.parent / "02_orphan.md").write_text(path.read_text())
    elif defect == "number":
        path.write_text(path.read_text().replace("Lab 01:", "Lab 02:"))
    elif defect == "section":
        path.write_text(
            path.read_text().replace("## Check your results", "## Unspecified")
        )
    elif defect == "lesson":
        metadata["labs"][0]["lessons"] = [2]
    else:
        metadata["labs"].append(metadata["labs"][0].copy())
    with pytest.raises(ValueError, match="guide|lesson|lab"):
        builder.lab_guides(tmp_path, metadata, 1)


def test_valid_authored_guide(tmp_path):
    builder = load_builder()
    metadata, _path = fixture_course(tmp_path)
    guide = builder.lab_guides(tmp_path, metadata, 1)[0]
    assert guide["title"] == "Lab 01: Example"
    assert guide["number"] == "01"


def test_metadata_has_explicit_lesson_membership():
    for course in COURSES:
        metadata = json.loads((ROOT / course / "reference/course.json").read_text())
        assert all(
            set(item) == {"path", "optional", "lessons"} for item in metadata["labs"]
        )


@pytest.mark.parametrize("course", COURSES)
def test_guide_commands_are_valid_shell_and_use_existing_sources_and_options(course):
    root = ROOT / course
    options_by_source: dict[str, set[str]] = {}
    for guide in (root / "reference/labs").glob("*.md"):
        for command in re.findall(r"```bash\n(.*?)\n```", guide.read_text(), re.S):
            result = subprocess.run(
                ["bash", "-n"], input=command, text=True, capture_output=True
            )
            assert result.returncode == 0, guide
            for line in command.splitlines():
                for source in re.findall(r"(?:labs|slurm)/[\w.-]+", line):
                    assert (root / source).is_file(), (guide, source)
                match = re.search(r"labs/([\w]+\.py) (.*)", line)
                if match:
                    if match[1] not in options_by_source:
                        code = (root / "labs" / match[1]).read_text()
                        tree = ast.parse(
                            code
                            + "\n"
                            + (root / "labs/common.py")
                            .read_text()
                            .replace("from __future__ import annotations", "")
                        )
                        options = {
                            arg.value
                            for node in ast.walk(tree)
                            if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "add_argument"
                            for arg in node.args
                            if isinstance(arg, ast.Constant)
                            and isinstance(arg.value, str)
                        }
                        options_by_source[match[1]] = options | {"--help"}
                    assert (
                        set(re.findall(r"(?<!\w)--[\w-]+", match[2]))
                        <= options_by_source[match[1]]
                    ), guide


def test_synthetic_workload_rates_have_operator_units_and_joined_boundary():
    with load_lab("llm-inference/labs/25_workload_metrics.py") as lab:
        result = lab.operator_rates(4, 32, 64.0, 80.0)
        assert result == {
            "mean_recurrent_iteration_ms": 2.0,
            "recurrent_row_updates_per_second": 2000.0,
            "joined_row_updates_per_second": 1600.0,
        }
        for duration in (0, -1, float("inf"), float("nan")):
            with pytest.raises(ValueError):
                lab.operator_rates(4, 32, duration, 80.0)
        for batch, steps in ((0, 32), (4, 0)):
            with pytest.raises(ValueError):
                lab.operator_rates(batch, steps, 64.0, 80.0)
    source = (ROOT / "llm-inference/labs/25_workload_metrics.py").read_text()
    for obsolete in (
        "device_ttft_proxy_ms",
        "device_tpot_itl_proxy_ms",
        "decode_output_tokens_per_second",
        "end_to_end_device_output_tokens_per_second",
    ):
        assert obsolete not in source
    assert "torch, joined_workload" in source


@pytest.mark.parametrize(
    ("path", "required"),
    [
        (
            "gpu-fundamentals/reference/labs/02_tensor_core_precision.md",
            "FP32 highest-policy reference",
        ),
        ("gpu-fundamentals/reference/labs/11_scheduler_tail.md", "optimistic estimate"),
        (
            "gpu-optimizations/reference/labs/15_tail_load_balance.md",
            "optimistic estimate",
        ),
        (
            "llm-training/reference/labs/02_gradient_accumulation.md",
            "Trade microbatch size for peak memory",
        ),
        ("llm-training/reference/labs/05_lora_sft.md", "masks padding only"),
        (
            "custom-cuda-kernels/reference/cluster-smoke-test.md",
            "COURSE_ENABLE_SM90A=ON",
        ),
    ],
)
def test_guides_match_reviewed_implementation_boundaries(path, required):
    assert required in (ROOT / path).read_text()


def test_recomputation_guide_names_forward_backward_only_boundary():
    document = (
        ROOT / "llm-training/reference/labs/14_activation_checkpointing.md"
    ).read_text()
    assert "excludes optimizer work and optimizer state" in document
    assert "optimizer-update equivalence is not tested" in document


def test_unknown_guide_link_is_not_silently_dropped(tmp_path):
    builder = load_builder()
    metadata, path = fixture_course(tmp_path)
    path.write_text(path.read_text() + "\n[Missing reference](../missing.md)\n")
    guide = builder.lab_guides(tmp_path, metadata, 1)[0]
    with pytest.raises(ValueError, match="unresolved link"):
        builder.lab_guide_links(guide, {})


def test_standalone_validator_rejects_stale_guide_narrative(tmp_path, monkeypatch):
    builder = load_builder()
    metadata, path = fixture_course(tmp_path)
    guides = builder.lab_guides(tmp_path, metadata, 1)
    lessons = [
        {
            "title": "Example lesson",
            "Practice labs": f"- [{guides[0]['title']}](reference/labs/{guides[0]['source'].stem}.md)",
        }
    ]
    document = builder.lesson_markup(
        lessons[0], 1, practice_labs=guides
    ) + builder.lab_markup(tmp_path, guides[0], lessons, {})
    spec = importlib.util.spec_from_file_location(
        "guide_validator", ROOT / "tools/validate_course_template.py"
    )
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    monkeypatch.setattr(validator, "ROOT", tmp_path)
    validator.validate_lab_guides(
        document,
        metadata,
        [("1. Example lesson", lessons[0], 0)],
        builder.executable_sources(tmp_path),
    )
    path.write_text(
        path.read_text().replace(
            "Specific explanation", "Substantively revised explanation", 1
        )
    )
    with pytest.raises(SystemExit, match="narrative differs"):
        validator.validate_lab_guides(
            document,
            metadata,
            [("1. Example lesson", lessons[0], 0)],
            builder.executable_sources(tmp_path),
        )
