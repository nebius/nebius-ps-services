"""Guard specific explanatory bridges, not automated semantic completeness."""

import ast
import math

import pytest

from test_course_content_contract import lab_section, ROOT
from test_substantive_editorial import lesson


@pytest.mark.parametrize(
    ("path", "terms"),
    [
        (
            "gpu-fundamentals/reference/labs/08_operator_to_kernels.md",
            ("matrix multiplication", "GELU", "child events"),
        ),
        (
            "gpu-fundamentals/reference/labs/04_layout_and_coalescing.md",
            ("logical index", "strides", "storage offsets"),
        ),
        (
            "gpu-fundamentals/reference/labs/09_triton_launch_geometry.md",
            ("1,003", "four programs", "bounds mask"),
        ),
        (
            "gpu-fundamentals/reference/labs/01_cpu_gpu_crossover.md",
            ("atol + rtol", "reference"),
        ),
        (
            "gpu-fundamentals/reference/labs/02_tensor_core_precision.md",
            ("square root", "every element"),
        ),
        (
            "gpu-optimizations/reference/labs/02_sync_trap.md",
            ("one number", "one-element tensor", "Python number"),
        ),
        (
            "llm-inference/reference/labs/29_quantization.md",
            ("square root", "nonzero", "0.26", "0.30"),
        ),
        (
            "custom-cuda-kernels/reference/labs/12_capstone.md",
            ("tanh(1.25*x + 0.5)", "affine", "-1 and 1"),
        ),
    ],
)
def test_lab_defines_required_concepts_before_commands(path, terms):
    before_commands = (ROOT / path).read_text().split("## Practice")[0]
    for term in terms:
        assert term.lower() in before_commands.lower()


def test_lora_teaches_composition_before_counting_savings():
    primer = lesson("llm-training", "Perform SFT and LoRA with explicit savings")[
        "What it is"
    ]
    assert "B(Ax)" in primer
    assert "[3, 7]" in primer
    x = [3, 4]
    a_x = x[0]
    b_ax = [0, 2 * a_x]
    result = [base + 0.5 * update for base, update in zip(x, b_ax, strict=True)]
    assert result == [3, 7]


def test_numerical_gate_hand_calculations():
    guide = (
        ROOT / "gpu-fundamentals/reference/labs/02_tensor_core_precision.md"
    ).read_text()
    assert "[3, 4]" in guide and "[3, 4.1]" in guide
    assert math.sqrt(3**2 + 4**2) == 5
    assert math.sqrt((3 - 3) ** 2 + (4.1 - 4) ** 2) / 5 == pytest.approx(0.02)
    assert 1e-6 + 1e-5 * abs(2) == pytest.approx(0.000021)


def test_training_capstone_requires_evidence_from_its_own_workload():
    practice = lab_section("llm-training", 31, "Practice")
    assert "different workloads" in practice
    assert "Lab 31's own baseline and candidate" in practice
    guide = (ROOT / "llm-training/reference/labs/31_training_capstone.md").read_text()
    assert "matched profile" in guide


def test_crossover_scales_tolerance_by_the_cpu_reference():
    source = (ROOT / "gpu-fundamentals/labs/01_cpu_gpu_crossover.py").read_text()
    checks = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "allclose"
    ]
    assert len(checks) == 1
    assert [ast.unparse(arg) for arg in checks[0].args[:2]] == ["result", "reference"]
    # A boundary fixture demonstrates why argument order matters even though
    # absolute error is symmetric: the relative allowance is not symmetric.
    reference, observed = 2.0, 2.0000210001
    error = abs(observed - reference)
    assert error > 1e-6 + 1e-5 * abs(reference)
    assert error <= 1e-6 + 1e-5 * abs(observed)
