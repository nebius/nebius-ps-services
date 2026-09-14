"""Educational depth, substantive mechanisms, and canonical rendering."""

from __future__ import annotations

import importlib.util
import html as html_lib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COURSES = (
    "gpu-fundamentals",
    "gpu-optimizations",
    "llm-training",
    "llm-inference",
    "custom-cuda-kernels",
)
REQUIRED_FIELDS = ("Objective", "How it works", "Practice labs", "Mental model")
CORE_CONCEPTS = {
    "gpu-fundamentals": (
        "PTX",
        "SASS",
        "occupancy",
        "coalesc",
        "roofline",
        "NCCL",
        "MIG",
        "ECC",
    ),
    "gpu-optimizations": (
        "critical path",
        "NVTX",
        "Nsight",
        "fusion",
        "graphs",
        "allocator",
        "overlap",
        "tail",
    ),
    "llm-training": (
        "optimizer",
        "checkpoint",
        "resume",
        "LoRA",
        "GRPO",
        "FP8",
        "FSDP",
        "packing",
    ),
    "llm-inference": (
        "prefill",
        "decode",
        "KV",
        "TTFT",
        "ITL",
        "quantization",
        "speculative",
        "disaggregation",
    ),
    "custom-cuda-kernels": (
        "coalesc",
        "reduction",
        "stencil",
        "occupancy",
        "asynchronous",
        "CUTLASS",
        "RMSNorm",
    ),
}


def load_builder():
    path = ROOT / "tools/build_course_html.py"
    spec = importlib.util.spec_from_file_location("course_builder", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def word_count(value: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", value))


@pytest.mark.parametrize("course", COURSES)
def test_every_lesson_is_long_form_and_structured(course: str) -> None:
    builder = load_builder()
    _title, _preamble, lessons = builder.parse_course(ROOT / course / "COURSE.md")
    assert len(lessons) >= 12
    for lesson in lessons:
        assert set(REQUIRED_FIELDS).issubset(lesson), lesson["title"]
        assert word_count(" ".join(lesson.values())) >= 300, lesson["title"]
        assert word_count(lesson["How it works"]) >= 180, lesson["title"]
        assert list(lesson) == ["title", *REQUIRED_FIELDS]
    metadata = builder.course_metadata(ROOT / course)
    guides = builder.lab_guides(ROOT / course, metadata, len(lessons))
    for number, lesson in enumerate(lessons, 1):
        assigned = [guide for guide in guides if number in guide["lessons"]]
        practice = "\n".join(guide["sections"]["Practice"] for guide in assigned)
        assert word_count(practice) >= 35, lesson["title"]
        assert all(
            cue in practice for cue in ("Given", "Change", "Expected observation")
        ), lesson["title"]
        assert any(
            word_count(guide["sections"]["Before you start"]) >= 18
            for guide in assigned
        )
        assert any(
            word_count(guide["sections"]["Investigate the behavior"]) >= 20
            for guide in assigned
        )


@pytest.mark.parametrize("course", COURSES)
def test_mechanism_explanations_are_not_repeated_boilerplate(course: str) -> None:
    builder = load_builder()
    _title, _preamble, lessons = builder.parse_course(ROOT / course / "COURSE.md")
    fingerprints = {
        " ".join(re.findall(r"[a-z0-9]+", lesson["How it works"].lower())[:18])
        for lesson in lessons
    }
    assert len(fingerprints) == len(lessons)


@pytest.mark.parametrize("course", COURSES)
def test_core_mechanisms_have_substantive_current_lessons(course: str) -> None:
    lessons = load_builder().parse_course(ROOT / course / "COURSE.md")[2]
    mechanisms = "\n".join(value for item in lessons for value in item.values())
    for concept in CORE_CONCEPTS[course]:
        assert concept.lower() in mechanisms.lower()


@pytest.mark.parametrize("course", COURSES)
def test_generated_html_exactly_matches_complete_canonical_sources(course: str) -> None:
    builder = load_builder()
    expected = builder.render_course(course)
    actual = (ROOT / course / "index.html").read_text(encoding="utf-8")
    assert actual == expected
    _title, preamble, _lessons = builder.parse_course(ROOT / course / "COURSE.md")
    assert builder.block(preamble) in actual
    for field in REQUIRED_FIELDS:
        assert f"<strong>{field}</strong>" in actual


def test_representative_mechanisms_and_tooling_are_published() -> None:
    optimizations = (ROOT / "gpu-optimizations/COURSE.md").read_text(encoding="utf-8")
    assert (
        "compute, memory/data movement, host/launch, communication, or input/storage"
        in optimizations
    )
    assert "disconfirming control" in optimizations

    tooling = (ROOT / "gpu-optimizations/reference/tooling-setup.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "gpu-optimizations/README.md").read_text(encoding="utf-8")
    generated = (ROOT / "gpu-optimizations/index.html").read_text(encoding="utf-8")
    assert "(reference/tooling-setup.md)" in readme
    for required_text in ("nsys status -e", "ncu --list-sets", "ERR_NVGPUCTRPERM"):
        assert required_text in tooling
        assert html_lib.escape(required_text) in generated

    representative_sources = {
        "llm-training/COURSE.md": (
            "sampler epoch and data cursor",
            "partial-accumulation",
        ),
        "llm-inference/COURSE.md": (
            "trust_remote_code",
            "loading places the weights in device memory",
        ),
        "custom-cuda-kernels/COURSE.md": (
            "indexing, masking, synchronization",
            "maintenance",
        ),
    }
    for relative, required_terms in representative_sources.items():
        destination = (ROOT / relative).read_text(encoding="utf-8")
        assert all(term in destination for term in required_terms)


@pytest.mark.parametrize("course", COURSES)
def test_every_visual_stage_label_is_complete_in_svg(course: str) -> None:
    builder = load_builder()
    visuals = builder.parse_visuals(ROOT / course / "reference/visual-plan.md")
    for visual in visuals:
        for stage in (visual.first, visual.second, visual.third):
            # Metadata includes full-width notes as well as narrow box labels.
            # Check lossless wrapping here; diagram rendering tests its own slots.
            rendered = builder.svg_label(stage, 100, 100, width=40)
            text = " ".join(
                html_lib.unescape(part)
                for part in re.findall(r"<tspan[^>]*>(.*?)</tspan>", rendered)
            )
            assert text == stage


def test_all_validator_copies_match_the_shared_contract() -> None:
    template = (ROOT / "tools/validate_course_template.py").read_bytes()
    for course in COURSES:
        assert (ROOT / course / "tools/validate_course.py").read_bytes() == template


def test_multiline_lesson_content_is_preserved_by_the_renderer(tmp_path: Path) -> None:
    builder = load_builder()
    source = tmp_path / "COURSE.md"
    source.write_text(
        "# Example\n\n"
        "## 1. Multiline\n\n"
        "**How it works** First paragraph.\n\n"
        "- first retained point\n"
        "- second retained point\n\n"
        "**Mental model** Explain both points.\n",
        encoding="utf-8",
    )
    _title, preamble, lessons = builder.parse_course(source)
    assert preamble == ""
    mechanism = lessons[0]["How it works"]
    assert "First paragraph." in mechanism
    assert "- first retained point\n- second retained point" in mechanism
    rendered = builder.block(mechanism)
    assert "<p>First paragraph.</p>" in rendered
    assert "<li>first retained point</li>" in rendered


def lab_section(course: str, number: int, section: str) -> str:
    path = next((ROOT / course / "reference/labs").glob(f"{number:02}_*.md"))
    pieces = re.split(r"^## (.+)\n", path.read_text(), flags=re.M)
    return dict(zip(pieces[1::2], pieces[2::2], strict=True))[section]
