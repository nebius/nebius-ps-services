"""Beginner definitions precede advanced contracts without replacing depth."""

import json

import pytest

from test_course_content_contract import COURSES, ROOT, load_builder
from test_course_review_fixes import load_lab


@pytest.mark.parametrize("course", COURSES)
def test_first_lesson_has_substantive_beginner_entry(course):
    builder = load_builder()
    _, _, lessons = builder.parse_course(ROOT / course / "COURSE.md")
    entry = lessons[0]["How it works"]
    assert len(entry.split()) >= 350
    assert all(term in entry for term in ("### What", "### Why", "### How", "### Try"))
    rendered = builder.lesson_markup(lessons[0], 1)
    assert rendered.index("Objective") < rendered.index("How it works")
    assert builder.block(entry) in rendered
    manifest = json.loads(
        (ROOT / course / "reference/visual-manifest.json").read_text()
    )
    assert any(
        row["lessons"][0] == 1 and row["after"] == "How it works"
        for row in manifest["diagrams"]
    )


def test_training_intro_changes_weight_and_improves_held_out_predictions():
    torch = pytest.importorskip("torch")
    with load_lab("llm-training/labs/32_learning_basics.py") as module:
        result = module.run_experiment(torch, "cpu")
    assert result["initial_weight"] == 0.0
    assert result["final_weight"] == pytest.approx(2.0, abs=1e-4)
    assert result["loss_after"] < result["loss_before"]
    assert result["held_out_prediction"] == pytest.approx(6.0, abs=1e-4)
    assert result["first_gradient"] == pytest.approx(-10.0)
    assert result["weight_after_inference"] == result["final_weight"]


def test_validator_tracks_explanation_diagram_inside_its_section():
    with load_lab("tools/validate_course_template.py") as module:
        parser = module.Parser()
        parser.feed(
            '<section class="lesson" id="entry">'
            '<div class="how-it-works">Beginner explanation'
            '<figure id="detail-entry"></figure></div></section>'
        )
        assert parser.figures["detail-entry"] == ("lesson:1", "how-it-works")
        assert "how-it-works" in module.LESSON_CLASSES


def test_inference_intro_has_fixed_weights_and_explicit_stopping():
    torch = pytest.importorskip("torch")
    with load_lab("llm-inference/labs/35_inference_basics.py") as module:
        result = module.run_experiment(torch, "cpu", 5)
        short = module.run_experiment(torch, "cpu", 1)
        with pytest.raises(ValueError, match="positive"):
            module.run_experiment(torch, "cpu", 0)
    assert result["generated_tokens"] == ["like", "GPUs", "<eos>"]
    assert result["stop_reason"] == "eos"
    assert result["parameters_unchanged"]
    assert not result["gradients_created"]
    assert short["generated_tokens"] == ["like"]
    assert short["stop_reason"] == "length"


def test_h100_overview_separates_physical_and_logical_hierarchies():
    svg = (
        ROOT / "gpu-fundamentals/reference/diagrams/h100-beginner-overview.svg"
    ).read_text()
    for term in (
        "HBM",
        "L2",
        "L1",
        "shared memory",
        "subpartitions",
        "32 threads",
        "registers",
        "command",
        "global",
    ):
        assert term in svg
    assert "not a silicon floorplan" in svg


def test_reference_bridges_are_explained_not_just_named():
    requirements = {
        "gpu-fundamentals": ("memory-controller activity", "one-second"),
        "llm-training": ("stochastic rounding", "HFU", "recomputation"),
        "llm-inference": ("radix tree", "EAGLE", "Medusa", "GenAI-Perf"),
        "custom-cuda-kernels": (
            "host-facing application programming interface (API)",
            "source package",
            "consumer",
        ),
    }
    for course, terms in requirements.items():
        content = (ROOT / course / "COURSE.md").read_text()
        assert all(term.lower() in content.lower() for term in terms)
